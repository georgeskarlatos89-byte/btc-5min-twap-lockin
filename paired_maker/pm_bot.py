#!/usr/bin/env python3
"""pm_bot.py - STEP 2: event-driven paired maker for BTC 5-minute rounds (DRY by default).

  python3 pm_bot.py --env ~/pm/.env

The tactic is the one the replay tests (pm_replay.py "model"/"join", pair cap, inventory limit, 60-240 s
window). What is new versus every earlier bot here:
  * no polling: the CLOB market websocket drives every decision (book + price_change), the RTDS
    chainlink feed drives the fair value, the CLOB user websocket reports our own fills;
  * one persistent HTTP/2 connection (the SDK's), orders signed just before posting (~15 ms);
  * cancel/replace is coalesced per side: if the book moves again while an order is in flight only the
    latest wanted price is sent; every leg is timestamped (server ts -> decided -> ack) and logged;
  * post-only GTC, never crosses, never chases a lagging leg past the pair cap, never cancel_all.
Safety: DRY_RUN=true by default (no client, no orders, decisions + latency only). LIVE needs
GATE_PASS_FILE (the step-3 replay report JSON with gate=true for the measured latency, < 7 days old),
KILL_FILE absent, DAILY_STOP_USD not reached. Secrets never logged.
Files: pm_bot.log, pm_decisions.jsonl (every decision with timings), pm_fills.jsonl, pm_rounds.jsonl.
"""
import argparse, asyncio, json, math, os, sys, time, urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 pm-bot"}
CLOB_HOST = "https://clob.polymarket.com"
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
WS_RTDS = "wss://ws-live-data.polymarket.com"
SIGMA = 6.8
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))


def env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


def now():
    return time.time()


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


class Cfg:
    def __init__(self):
        self.dry = env("DRY_RUN", "true").lower() != "false"
        self.mode = env("PM_MODE", "model")                      # model | join
        self.size = float(env("PM_SIZE", "5"))
        self.cap_shares = float(env("PM_CAP_SHARES", "10"))        # max shares per side per round
        self.inv = float(env("PM_INV", "10"))                      # stop adding when this many shares ahead of the other side
        self.pair_cap = float(env("PM_PAIR_CAP", "0.97"))
        self.edge = float(env("PM_EDGE", "0.05"))
        self.qs = int(env("PM_QS", "60")); self.qe = int(env("PM_QE", "240"))
        self.p_min = 0.02; self.p_max = 0.97
        self.daily_stop = float(env("DAILY_STOP_USD", "5"))
        self.kill_file = os.path.expanduser(env("KILL_FILE", os.path.join(HERE, "KILL")))
        self.gate_file = os.path.expanduser(env("GATE_PASS_FILE", os.path.join(HERE, "replay_out", "REPLAY-RESULTS.json")))
        self.gate_lat = float(env("PM_GATE_LAT", "0.1"))
        self.out = os.path.expanduser(env("PM_OUT", HERE))


class Log:
    def __init__(self, cfg):
        self.f = open(os.path.join(cfg.out, "pm_bot.log"), "a")
        self.dec = open(os.path.join(cfg.out, "pm_decisions.jsonl"), "a")
        self.fills = open(os.path.join(cfg.out, "pm_fills.jsonl"), "a")
        self.rounds = open(os.path.join(cfg.out, "pm_rounds.jsonl"), "a")

    def __call__(self, s):
        line = f"{ts()} {s}"; print(line, flush=True); self.f.write(line + "\n"); self.f.flush()

    def j(self, fh, d):
        fh.write(json.dumps(d, separators=(",", ":")) + "\n"); fh.flush()


def gate_check(cfg, log):
    """LIVE only with a fresh passing step-3 report at the configured latency."""
    if cfg.dry:
        return True
    try:
        d = json.load(open(cfg.gate_file))
    except Exception as e:
        log(f"LIVE REFUSED: no gate file ({e})"); return False
    age = now() - os.path.getmtime(cfg.gate_file)
    ok = [v for v, s in d.get("summary", {}).items() if s.get("gate") and abs(s["cfg"]["lat"] - cfg.gate_lat) < 1e-9 and s["cfg"]["mode"] == cfg.mode]
    if not ok:
        log(f"LIVE REFUSED: no passing variant for mode={cfg.mode} lat={cfg.gate_lat} in {cfg.gate_file}"); return False
    if age > 7 * 86400:
        log("LIVE REFUSED: gate file older than 7 days"); return False
    log(f"LIVE authorized by gate file: {ok} (age {age / 3600:.1f} h)")
    return True


def gamma_market(start):
    for q in ("", "&closed=true"):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{start}{q}", headers=UA)
        try:
            d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception:
            continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]; outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            win = outs[pr.index(1.0)] if (m.get("closed") and sorted(pr) == [0.0, 1.0]) else None
            return dict(start=start, condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")], winner=win)
    return None


class Side:
    def __init__(self, name, token):
        self.name = name; self.token = token
        self.filled = 0.0; self.cost = 0.0
        self.working_price = None; self.working_id = None; self.working_size = 0.0
        self.want = None; self.want_event_ts = None; self.want_decided = None
        self.ev = asyncio.Event(); self.busy = False


class Bot:
    def __init__(self, cfg, log):
        self.cfg = cfg; self.log = log
        self.client = None
        self.bb = {}; self.ba = {}; self.levels = {}
        self.spot = None; self.opens = {}
        self.round = None; self.sides = {}; self.next_round = None
        self.realized = 0.0; self.day = datetime.now(timezone.utc).date()
        self.orders_posted = 0; self.orders_cancelled = 0
        self.stopped = False; self.cancel_reason = None
        self.rtds_n = {}; self.mkt_n = 0; self.mkt_max_ms = 0.0; self.loop_drift_ms = 0.0

    # ---------- client
    def init_client(self):
        if self.cfg.dry:
            self.log("DRY_RUN: no client, no orders"); return
        from py_clob_client_v2 import ClobClient
        pk = env("POLY_PRIVATE_KEY"); funder = env("POLY_FUNDER") or env("POLYMARKET_PROXY_ADDRESS")
        sig = int(env("POLY_SIGNATURE_TYPE", "3"))
        if not pk or not funder:
            raise SystemExit("LIVE needs POLY_PRIVATE_KEY and POLY_FUNDER in the env file")
        creds = ClobClient(host=CLOB_HOST, key=pk, chain_id=137).create_or_derive_api_key()
        self.client = ClobClient(host=CLOB_HOST, key=pk, chain_id=137, creds=creds, signature_type=sig, funder=funder)
        self.creds = creds; self.funder = funder.lower()
        self.log(f"client ready sig_type={sig} funder=...{funder[-6:]}")

    def post(self, side, price):
        from py_clob_client_v2 import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY
        order = self.client.create_order(OrderArgs(token_id=side.token, price=round(price, 2), size=self.cfg.size, side=BUY))
        resp = self.client.post_order(order, order_type=OrderType.GTC, post_only=True)
        if isinstance(resp, dict) and resp.get("success") and resp.get("orderID"):
            return resp["orderID"], None
        return None, str(resp)[:160]

    def cancel(self, order_id):
        r = self.client.cancel_order(order_id) if hasattr(self.client, "cancel_order") else self.client.cancel(order_id)
        if isinstance(r, dict):
            if order_id in (r.get("canceled") or []): return True, None
            nc = r.get("not_canceled") or {}
            return False, nc.get(order_id, str(r)[:120])
        return False, str(r)[:120]

    # ---------- decision (same rules as pm_replay.py)
    def want_price(self, side, t_el):
        c = self.cfg; a = side.token
        b = self.bb.get(a); ask = self.ba.get(a)
        if t_el < c.qs or t_el > c.qe or b is None or side.filled >= c.cap_shares - 1e-9: return None
        other = self.sides["Down" if side.name == "Up" else "Up"]
        if c.mode == "join":
            want = b
        else:
            op = self.opens.get(self.round["start"])
            if self.spot is None or op is None: return None
            left = max(5.0, 300.0 - t_el)
            f = Phi(((self.spot - op) / op * 1e4) / (SIGMA * math.sqrt(left / 300.0)))
            fair = f if side.name == "Up" else 1 - f
            want = math.floor((fair - c.edge) * 100 + 1e-9) / 100.0
            if ask is not None and want >= ask - 1e-9: want = round(ask - 0.01, 2)
        if other.filled > side.filled + 1e-9:
            want = min(want, math.floor((c.pair_cap - other.cost / other.filled) * 100 + 1e-9) / 100.0)
        if side.filled - other.filled >= c.inv - 1e-9: return None
        want = round(want, 2)
        return want if c.p_min <= want <= c.p_max else None

    def decide(self, event_ts_ms):
        if self.round is None or self.stopped: return
        t_el = now() - self.round["start"]
        for side in self.sides.values():
            want = self.want_price(side, t_el)
            cur = side.want if side.busy else side.working_price
            changed = (want is None) != (cur is None) or (want is not None and cur is not None and abs(want - cur) >= 0.0099)
            if changed:
                side.want = want; side.want_event_ts = event_ts_ms; side.want_decided = now(); side.ev.set()

    # ---------- order worker per side (coalesces: only the latest want is sent)
    async def worker(self, side):
        loop = asyncio.get_event_loop()
        while True:
            await side.ev.wait(); side.ev.clear()
            side.busy = True
            want = side.want; ev_ts = side.want_event_ts; t_dec = side.want_decided
            rec = dict(t=round(t_dec, 3), round=self.round["start"] if self.round else None, side=side.name, want=want,
                       was=side.working_price, event_ts_ms=ev_ts, decide_ms=round((t_dec * 1000 - ev_ts), 1) if ev_ts else None,
                       dry=self.cfg.dry, cancel_ms=None, post_ms=None, ack_ms=None, err=None)
            if self.cfg.dry:
                side.working_price = want; side.working_id = "dry" if want is not None else None
                rec["ack_ms"] = rec["decide_ms"]
            else:
                try:
                    if side.working_id:
                        t0 = now(); ok, err = await loop.run_in_executor(None, self.cancel, side.working_id)
                        rec["cancel_ms"] = round((now() - t0) * 1000, 1)
                        if not ok:
                            rec["err"] = f"cancel: {err}"; self.log(f"cancel failed {side.name} {side.working_id[:10]}: {err}")
                            await loop.run_in_executor(None, self.reconcile_order, side)
                        else:
                            self.orders_cancelled += 1
                        side.working_id = None; side.working_price = None
                    if want is not None and not self.stopped:
                        t0 = now(); oid, err = await loop.run_in_executor(None, self.post, side, want)
                        rec["post_ms"] = round((now() - t0) * 1000, 1)
                        if oid:
                            side.working_id = oid; side.working_price = want; side.working_size = self.cfg.size; self.orders_posted += 1
                            rec["ack_ms"] = round(now() * 1000 - ev_ts, 1) if ev_ts else None
                        else:
                            rec["err"] = f"post: {err}"; self.log(f"post failed {side.name} @{want}: {err}")
                except Exception as e:
                    rec["err"] = f"{type(e).__name__}: {str(e)[:120]}"; self.log(f"worker error {side.name}: {rec['err']}")
            self.log.j(self.log.dec, rec)
            side.busy = False
            # a newer want may have arrived while we were busy
            if side.want != side.working_price and not ((side.want is None) and (side.working_price is None)):
                side.ev.set()

    def reconcile_order(self, side):
        """after a failed cancel: ask the exchange what happened to the order (filled?)."""
        try:
            o = self.client.get_order(side.working_id)
            matched = float(o.get("size_matched", 0) or 0)
            if matched > 0: self.on_fill(side.token, float(o.get("price")), matched, "reconcile", side.working_id)
        except Exception as e:
            self.log(f"reconcile failed: {e}")

    # ---------- fills (user channel "order" updates: size_matched grows)
    def on_fill(self, token, price, size, src, oid):
        side = next((s for s in self.sides.values() if s.token == token), None)
        if side is None: return
        key = (oid, round(size, 4))
        if key in self.seen_fills: return
        self.seen_fills.add(key)
        side.filled += size; side.cost += price * size
        self.log(f"FILL {side.name} {size:g} @ {price} ({src}) -> {side.name} {side.filled:g} sh cost {side.cost:.2f}")
        self.log.j(self.log.fills, dict(t=round(now(), 3), round=self.round["start"], side=side.name, price=price, size=size, src=src, order_id=oid))
        self.decide(None)

    # ---------- round handling
    async def round_loop(self):
        while not self.stopped:
            t = now(); start = int(t - t % 300); t_el = t - start
            if self.round is None or self.round["start"] != start:
                await self.new_round(start)
            if t_el >= self.cfg.qe + 1 and any(s.working_id for s in self.sides.values()):
                await self.cancel_all_ours("window closed")
            if t_el >= 250 and self.next_round is None:
                self.next_round = await asyncio.get_event_loop().run_in_executor(None, gamma_market, start + 300)
            if os.path.exists(self.cfg.kill_file) and not self.stopped:
                self.log("KILL file present: cancelling and stopping"); await self.cancel_all_ours("kill"); self.stopped = True
            await asyncio.sleep(0.5)

    async def new_round(self, start):
        if self.round is not None:
            await self.close_round()
        mk = self.next_round if (self.next_round and self.next_round["start"] == start) else await asyncio.get_event_loop().run_in_executor(None, gamma_market, start)
        self.next_round = None
        if not mk:
            self.log(f"round {start}: market not found yet"); await asyncio.sleep(3); return
        self.round = mk; self.sides = {"Up": Side("Up", mk["up"]), "Down": Side("Down", mk["down"])}
        self.seen_fills = set()
        for s in self.sides.values():
            asyncio.ensure_future(self.worker(s))
        self.sub_market.set(); self.sub_user.set()
        self.log(f"round {start} ({datetime.fromtimestamp(start, timezone.utc).strftime('%H:%M')}Z) cond ...{mk['condition_id'][-8:]} open={self.opens.get(start)}")

    async def close_round(self):
        await self.cancel_all_ours("round end")
        r = self.round; up = self.sides["Up"]; dn = self.sides["Down"]
        rec = dict(start=r["start"], up_sh=up.filled, dn_sh=dn.filled, up_cost=round(up.cost, 4), dn_cost=round(dn.cost, 4),
                   pairs=min(up.filled, dn.filled), posted=self.orders_posted, cancelled=self.orders_cancelled, winner=None, profit=None)
        self.orders_posted = 0; self.orders_cancelled = 0
        self.log.j(self.log.rounds, rec)
        if up.filled + dn.filled > 0:
            asyncio.ensure_future(self.settle_later(rec))

    async def settle_later(self, rec):
        for i in range(12):
            await asyncio.sleep(30)
            mk = await asyncio.get_event_loop().run_in_executor(None, gamma_market, rec["start"])
            if mk and mk["winner"]:
                pay = rec["up_sh"] if mk["winner"] == "Up" else rec["dn_sh"]
                rec["winner"] = mk["winner"]; rec["profit"] = round(pay - rec["up_cost"] - rec["dn_cost"], 4)
                self.realized += rec["profit"]; rec["settled"] = True
                self.log.j(self.log.rounds, rec)
                self.log(f"SETTLED round {rec['start']}: winner {mk['winner']} profit {rec['profit']:+.2f} (day {self.realized:+.2f})")
                if self.realized <= -abs(self.cfg.daily_stop) and not self.stopped:
                    self.log(f"DAILY STOP {self.realized:+.2f} <= -{self.cfg.daily_stop}: stopping"); await self.cancel_all_ours("daily stop"); self.stopped = True
                return
        self.log(f"round {rec['start']} unresolved after 6 min; left pending")

    async def cancel_all_ours(self, why):
        for s in self.sides.values():
            s.want = None
            if s.working_id and not self.cfg.dry:
                ok, err = await asyncio.get_event_loop().run_in_executor(None, self.cancel, s.working_id)
                self.log(f"cancel {s.name} {s.working_id[:10]} ({why}): {'ok' if ok else err}")
                if not ok: await asyncio.get_event_loop().run_in_executor(None, self.reconcile_order, s)
            s.working_id = None; s.working_price = None

    # ---------- feeds
    async def market_feed(self):
        import websockets
        while True:
            await self.sub_market.wait(); self.sub_market.clear()
            try:
                async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None) as ws:
                    toks = [s.token for s in self.sides.values()]
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                    self.log(f"market feed subscribed ({len(toks)} assets)")
                    async for raw in ws:
                        if self.sub_market.is_set(): break          # round changed: resubscribe
                        rx = now() * 1000
                        self.mkt_n += 1
                        msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
                        touched = False; sts = rx
                        for m in msgs:
                            et = m.get("event_type")
                            if et == "book":
                                a = m["asset_id"]; bids = m.get("bids") or []; asks = m.get("asks") or []
                                nb = max((float(x["price"]) for x in bids), default=None); na = min((float(x["price"]) for x in asks), default=None)
                                if nb is not None and nb != self.bb.get(a): self.bb[a] = nb; touched = True
                                if na is not None and na != self.ba.get(a): self.ba[a] = na; touched = True
                            elif et == "price_change":
                                for ch in m.get("price_changes") or []:
                                    a = ch["asset_id"]
                                    nb = ch.get("best_bid"); na = ch.get("best_ask")
                                    if nb:
                                        nb = float(nb)
                                        if nb != self.bb.get(a): self.bb[a] = nb; touched = True
                                    if na:
                                        na = float(na)
                                        if na != self.ba.get(a): self.ba[a] = na; touched = True
                            else:
                                continue
                            if touched:
                                sts = float(m.get("timestamp") or rx); self.feed_lag_ms = rx - sts
                        if touched: self.decide(sts)
                        h = now() * 1000 - rx
                        if h > self.mkt_max_ms: self.mkt_max_ms = h
            except Exception as e:
                self.log(f"market feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)"); self.sub_market.set(); await asyncio.sleep(1)

    async def rtds_feed(self):
        import websockets
        while True:
            try:
                async with websockets.connect(WS_RTDS, ping_interval=None, max_size=None) as ws:
                    await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                        {"topic": "crypto_prices_chainlink", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))},
                        {"topic": "crypto_prices_twap_sixty", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))}]}))
                    self.log("rtds feed subscribed")

                    async def ping():
                        while True:
                            await asyncio.sleep(5)
                            try: await ws.send("PING")
                            except Exception: return
                    pt = asyncio.ensure_future(ping())
                    try:
                        async for raw in ws:
                            try: m = json.loads(raw)
                            except Exception: continue
                            if not isinstance(m, dict) or m.get("type") != "update": continue
                            p = m.get("payload") or {}
                            if p.get("symbol") != "btc/usd": continue
                            val = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
                            tms = float(p.get("timestamp") or now() * 1000)
                            tsec = tms / 1000.0 if tms > 1e11 else tms
                            topic = m.get("topic")
                            self.rtds_n[topic] = self.rtds_n.get(topic, 0) + 1
                            if self.rtds_n[topic] == 1:
                                self.log(f"rtds first {topic}: keys={sorted(p.keys())} ts={p.get('timestamp')} val={val}")
                            if topic == "crypto_prices_chainlink":
                                self.spot = val; self.spot_lag_ms = now() * 1000 - tms if tms > 1e11 else None
                                if self.round: self.decide(now() * 1000)
                            elif topic == "crypto_prices_twap_sixty":
                                st = int(tsec // 300 * 300)
                                if st not in self.opens and tsec - st <= 3: self.opens[st] = val
                    finally:
                        pt.cancel()
            except Exception as e:
                self.log(f"rtds feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)"); await asyncio.sleep(2)

    async def user_feed(self):
        if self.cfg.dry: return
        import websockets
        while True:
            await self.sub_user.wait(); self.sub_user.clear()
            try:
                async with websockets.connect(WS_USER, ping_interval=None, max_size=None) as ws:
                    await ws.send(json.dumps({"auth": {"apiKey": self.creds.api_key, "secret": self.creds.api_secret, "passphrase": self.creds.api_passphrase},
                                             "markets": [self.round["condition_id"]], "type": "user"}))
                    self.log("user feed subscribed")
                    matched = {}
                    while not self.sub_user.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            continue
                        msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
                        for m in msgs:
                            if m.get("event_type") == "order":
                                oid = m.get("id"); sm = float(m.get("size_matched") or 0)
                                prev = matched.get(oid, 0.0)
                                if sm > prev + 1e-9:
                                    matched[oid] = sm
                                    self.on_fill(m.get("asset_id"), float(m.get("price")), sm - prev, "user-ws order", oid)
                            elif m.get("event_type") == "trade":
                                for mo in m.get("maker_orders") or []:
                                    if (mo.get("owner") or "").lower() == (self.creds.api_key or "").lower() or (mo.get("maker_address") or "").lower() == self.funder:
                                        self.on_fill(mo.get("asset_id"), float(mo.get("price")), float(mo.get("matched_amount")), "user-ws trade", mo.get("order_id"))
            except Exception as e:
                self.log(f"user feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)"); self.sub_user.set(); await asyncio.sleep(1)

    async def heartbeat(self):
        while True:
            t0 = now(); await asyncio.sleep(0.1)
            d = (now() - t0 - 0.1) * 1000
            if d > self.loop_drift_ms: self.loop_drift_ms = d

    async def status_loop(self):
        while True:
            await asyncio.sleep(30)
            if self.round:
                s = " ".join(f"{k}:{'-' if v.working_price is None else v.working_price}/{v.filled:g}sh" for k, v in self.sides.items())
                self.log(f"status round {self.round['start']} spot={self.spot} open={self.opens.get(self.round['start'])} {s} feed_lag={getattr(self, 'feed_lag_ms', None) and round(self.feed_lag_ms)}ms spot_lag={getattr(self, 'spot_lag_ms', None) and round(self.spot_lag_ms)}ms "
                         f"mkt_msgs={self.mkt_n} handler_max={self.mkt_max_ms:.1f}ms loop_drift_max={self.loop_drift_ms:.1f}ms rtds={self.rtds_n} day={self.realized:+.2f}")
                self.mkt_n = 0; self.mkt_max_ms = 0.0; self.loop_drift_ms = 0.0

    async def run(self):
        self.sub_market = asyncio.Event(); self.sub_user = asyncio.Event()
        await asyncio.gather(self.round_loop(), self.market_feed(), self.rtds_feed(), self.user_feed(), self.status_loop(), self.heartbeat())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--env", default=os.path.join(HERE, ".env")); a = ap.parse_args()
    load_dotenv(os.path.expanduser(a.env), override=True)
    cfg = Cfg(); os.makedirs(cfg.out, exist_ok=True); log = Log(cfg)
    log(f"pm_bot start mode={cfg.mode} size={cfg.size} cap={cfg.cap_shares} inv={cfg.inv} pair_cap={cfg.pair_cap} window={cfg.qs}-{cfg.qe}s dry={cfg.dry}")
    if not gate_check(cfg, log):
        raise SystemExit(2)
    if os.path.exists(cfg.kill_file):
        log("KILL file present at start: refusing to run"); raise SystemExit(3)
    bot = Bot(cfg, log); bot.init_client()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
