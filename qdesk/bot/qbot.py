#!/usr/bin/env python3
"""qbot.py - the qdesk paper bot (INTERFACES section 9). v1 is PAPER ONLY: DRY_RUN must be "true".

  python3 bot/qbot.py [--env ~/qdesk/.env] [--data ~/qdesk/data] [--dry-run] [--date YYYYMMDD]

What it does (feeds/round loop/kill file/daily stop/heartbeat copied from paired_maker/pm_bot.py):
  * CLOB market websocket (book, price_change, last_trade_price) + RTDS chainlink/TWAP feed drive everything;
  * one FeatureState per live round (5m, and 15m when gamma has it) is fed from the ws messages; on every top-of-book
    change and spot tick the bot takes FeatureState.row_now for both sides and asks dsl.chosen_side / eval_when;
  * the active rule (data/bot/active_rule.json) is loaded ONLY when gate.check passes, else OBSERVE mode: the rule is
    still evaluated and every would-be trade is logged as killed_by "observe:<reason>" with its would_price;
  * paper fills by paper_fill.PaperBook (taker: ask still there after 100 ms; maker: queue vs the live tape);
  * settlement by the gamma winner (settle_later) -> data/bot/paper_trades.csv + decisions-YYYYMMDD.jsonl.gz
    (+ .settled sidecar with would_pnl) -> decay.check after every settlement;
  * limits: rule.limits (max_trades_per_round, cooldown_s, max_rounds_per_day), size <= $5, paper capital $100,
    paper daily stop $10 (LOG ONLY: trades after it are killed_by "daily_stop", the process keeps running);
  * KILL file ~/qdesk/KILL stops trading (resting paper orders cancelled); heartbeat data/state/hb_bot.json + status
    line every 30 s. Handler work per message is state updates + the decision only; file writes are batched.
No secrets are read here (the .env is only loaded for DRY_RUN / QD_* settings and never printed).
"""
import argparse, asyncio, gzip, json, os, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from qdesk.bot import _util, gate, decay
from qdesk.bot.paper_fill import PaperBook

UA = {"User-Agent": "Mozilla/5.0 qdesk-bot"}
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_RTDS = "wss://ws-live-data.polymarket.com"
PAPER_TRADES_HEADER = ["ts_utc", "rule_id", "market", "start", "side", "leg", "decided_t_el", "price_wanted", "price_filled",
                       "size", "fill_ms", "fill_status", "fee", "winner", "pnl", "R", "backtest_avg_R", "ctx_json"]
ROUND_T = {"5m": 300, "15m": 900}


# copied from C:\g\tlfix\paired_maker\pm_bot.py (env, now, ts)
def env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


def now():
    return time.time()


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def dry_run_guard():
    """INTERFACES section 9: DRY_RUN default "true"; anything else -> SystemExit (there is no live path in v1)."""
    if str(env("DRY_RUN", "true")).strip().lower() != "true":
        raise SystemExit("qdesk v1 is paper only")
    return True



def _blackout_fn():
    """macro blackout 0/1 for the live feature column (same source as the history build)."""
    try:
        from qdesk.scout import macro
        return macro.is_blackout
    except Exception:
        return None

class Cfg:
    def __init__(self, data=None):
        self.data = _util.data_dir(data)
        self.markets = [m.strip() for m in str(env("QD_MARKETS", "5m,15m")).split(",") if m.strip() in ROUND_T]
        self.capital = float(env("QD_PAPER_CAPITAL", "100"))
        self.max_trade_usd = min(5.0, float(env("QD_MAX_TRADE_USD", "5")))
        self.daily_stop = float(env("QD_PAPER_DAILY_STOP", "10"))
        self.kill_file = os.path.expanduser(env("KILL_FILE", os.path.join("~", "qdesk", "KILL")))
        self.dec_throttle = float(env("QD_DECISION_THROTTLE_S", "1"))     # 0 = log every non-firing evaluation
        self.spot_lag = float(env("QD_SPOT_LAG", "1.44"))


# copied from C:\g\tlfix\paired_maker\pm_bot.py (Log) - one bot log + json writers
class Log:
    def __init__(self, cfg):
        os.makedirs(os.path.join(cfg.data, "bot"), exist_ok=True)
        self.f = open(os.path.join(cfg.data, "bot", "qbot.log"), "a", encoding="utf-8")
        self.lines = []

    def __call__(self, s):
        line = f"{ts()} {s}"; print(line, flush=True); self.f.write(line + "\n"); self.f.flush()
        self.lines.append(line); del self.lines[:-200]

    def j(self, fh, d):
        fh.write(json.dumps(d, separators=(",", ":")) + "\n"); fh.flush()


# copied from C:\g\tlfix\paired_maker\pm_bot.py (gamma_market) - generalised to the 15m slug
def gamma_market(start, market="5m"):
    for q in ("", "&closed=true"):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{market}-{start}{q}", headers=UA)
        try:
            d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception:
            continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]; outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            win = outs[pr.index(1.0)] if (m.get("closed") and sorted(pr) == [0.0, 1.0]) else None
            return dict(start=start, market=market, condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")], winner=win)
    return None


def fmt_killed(rec):
    """the journal line for a trade the rule wanted but the bot did not take (GrokBot: journal of killed trades)."""
    return (f"KILLED rule={rec.get('rule_id')} {rec.get('market')} {rec.get('start')} {rec.get('side')} t_el={rec.get('t_el')} "
            f"would_price={rec.get('would_price')} killed_by={rec.get('killed_by')}")


class DecisionLog:
    """decisions-YYYYMMDD.jsonl.gz (every evaluation) + decisions-YYYYMMDD.settled.jsonl.gz (would_pnl after settlement).
    Records are buffered in the handler and flushed by a background task (gzip append = one member per flush, crash-safe)."""
    def __init__(self, ddir):
        self.dir = os.path.join(ddir, "bot"); os.makedirs(self.dir, exist_ok=True)
        self.buf = {}; self.n = 0

    def path(self, day, settled=False):
        return os.path.join(self.dir, f"decisions-{day}{'.settled' if settled else ''}.jsonl.gz")

    def write(self, rec, t, settled=False):
        self.buf.setdefault(self.path(_util.day_str(t), settled), []).append(json.dumps(rec, separators=(",", ":")))
        self.n += 1

    def flush(self):
        out = []
        for p, lines in list(self.buf.items()):
            if not lines: continue
            with gzip.open(p, "ab") as f:
                f.write(("\n".join(lines) + "\n").encode("utf-8"))
            out.append(p); self.buf[p] = []
        return out


class Round:
    def __init__(self, market, start, mk, fs):
        self.market = market; self.start = start; self.T = ROUND_T[market]
        self.up = mk["up"]; self.down = mk["down"]; self.condition_id = mk["condition_id"]
        self.fs = fs; self.orders = []; self.n_trades = 0; self.closed = False; self.winner = None
        self.pending_dec = []; self._dec_last = {}; self._killed_seen = set()

    @property
    def key(self):
        return (self.market, self.start)


class QBot:
    def __init__(self, cfg, log, rule=None, gate_reasons=None):
        self.cfg = cfg; self.log = log
        self.rule = rule; self.rule_id = (rule or {}).get("id")
        self.gate_reasons = list(gate_reasons or [])
        self.mode = "PAPER" if (rule and not self.gate_reasons) else "OBSERVE"
        self.book = PaperBook()
        self.rounds = {}; self.asset_round = {}; self.next_round = {}
        self.spot = None; self.opens = {}
        self.day = datetime.now(timezone.utc).date(); self.day_pnl = 0.0; self.day_rounds = set(); self.daily_stopped = False
        self.realized = 0.0; self.open_cost = 0.0; self.last_trade_t = -1e9
        self.stopped = False; self.rtds_n = {}; self.mkt_n = 0; self.mkt_max_ms = 0.0; self.loop_drift_ms = 0.0
        self.feed_lag_ms = None; self.spot_lag_ms = None; self.n_dec = 0; self.n_fired = 0; self.n_killed = 0; self.n_errors = 0
        self.dec = DecisionLog(cfg.data)
        self.sub_market = None; self.state_path = os.path.join(cfg.data, "bot", "strategy_state.json")
        self.backtest_avg_R = (_util.load_json(self.state_path, {}) or {}).get("backtest_avg_R")
        self._dsl = None

    # ---------- modules (agent A / C) resolved lazily with clear errors
    def dsl(self):
        if self._dsl is None:
            self._dsl = _util.common("dsl")
        return self._dsl

    def make_feature_state(self, market, start, up, down):
        ff = _util.features_mod()
        return ff.FeatureState(market, start, up, down, spot_lag=self.cfg.spot_lag, blackout_fn=_blackout_fn(), src="live")

    # ---------- rounds
    def add_round(self, market, start, mk, fs=None):
        fs = fs or self.make_feature_state(market, start, mk["up"], mk["down"])
        rc = Round(market, start, mk, fs)
        self.rounds[rc.key] = rc; self.asset_round[rc.up] = rc; self.asset_round[rc.down] = rc
        self.book.set_pair(rc.up, rc.down)
        op = self.opens.get(start)
        if op is not None:
            try: fs.on_twap(float(start), op)
            except Exception: pass
        if self.spot is not None:
            try: fs.on_spot(now() - self.cfg.spot_lag, self.spot)
            except Exception: pass
        return rc

    # ---------- ws message handlers (tiny: state + decision only)
    def on_market_msg(self, m, rx):
        et = m.get("event_type")
        if et == "book":
            a = m.get("asset_id"); rc = self.asset_round.get(a)
            if rc is None: return
            bids = [(float(x["price"]), float(x["size"])) for x in m.get("bids") or []]
            asks = [(float(x["price"]), float(x["size"])) for x in m.get("asks") or []]
            self.book.on_book(a, bids, asks, rx)
            rc.fs.on_book(a, bids, asks, rx)
            sts = m.get("timestamp")
            if sts: self.feed_lag_ms = rx * 1000 - float(sts)
            self.decide(rc, rx)
        elif et == "price_change":
            touched = {}
            for ch in m.get("price_changes") or []:
                a = ch.get("asset_id"); rc = self.asset_round.get(a)
                if rc is None: continue
                nb = ch.get("best_bid"); na = ch.get("best_ask")
                nb = float(nb) if nb else None; na = float(na) if na else None
                old = (self.book.bb.get(a), self.book.ba.get(a))
                p = float(ch["price"]); s = float(ch.get("size") or 0); side = ch.get("side")
                self.book.on_price_change(a, side, p, s, rx, nb, na)
                rc.fs.on_price_change(a, side, p, s, nb, na, rx)
                if (self.book.bb.get(a), self.book.ba.get(a)) != old: touched[rc.key] = rc
            sts = m.get("timestamp")
            if sts: self.feed_lag_ms = rx * 1000 - float(sts)
            for rc in touched.values(): self.decide(rc, rx)
        elif et == "last_trade_price":
            a = m.get("asset_id"); rc = self.asset_round.get(a)
            if rc is None: return
            p = float(m["price"]); s = float(m.get("size") or 0); side = m.get("side")
            fills = self.book.on_trade(a, side, p, s, m.get("timestamp"), rx)
            rc.fs.on_trade(a, side, p, s, rx)
            for f in fills: self.on_fill(f)

    def on_spot(self, t_source, value):
        self.spot = value
        for rc in self.rounds.values():
            if rc.closed: continue
            rc.fs.on_spot(t_source, value)
        t = now()
        for rc in list(self.rounds.values()):
            if not rc.closed: self.decide(rc, t)

    def on_twap(self, t, value):
        for T in (300, 900):
            st = int(t // T * T)
            if st not in self.opens and t - st <= 3: self.opens[st] = value
        for rc in self.rounds.values():
            if not rc.closed: rc.fs.on_twap(t, value)

    # ---------- the decision
    def decide(self, rc, t):
        if rc.closed or self.rule is None or self.rule.get("market", "5m") != rc.market: return
        try:
            row_up = rc.fs.row_now("Up", t); row_dn = rc.fs.row_now("Down", t)
            side = self.dsl().chosen_side(self.rule, row_up, row_dn)
        except Exception as e:
            self.n_errors += 1
            if self.n_errors <= 3: self.log(f"decide error: {type(e).__name__}: {str(e)[:120]}")
            return
        self.n_dec += 1
        t_el = round(t - rc.start, 3)
        if side is None:
            self._log_dec(rc, t, t_el, None, False, None, None, row_up, throttle=True); return
        leg = (self.rule.get("entry") or {}).get("leg") or {}
        submitted = False
        for s in (("Up", "Down") if side == "both" else (side,)):
            row = row_up if s == "Up" else row_dn
            price, killed = self.price_and_kill(rc, s, row, leg, t)
            if killed is None:
                self.submit(rc, s, row, leg, price, t, t_el); submitted = True
            self._log_dec(rc, t, t_el, s, True, killed, price, row, throttle=(killed or "").startswith(("limit:", "observe:", "daily_stop", "kill_file")))
        if submitted:
            rc.n_trades += 1; self.last_trade_t = t; self.day_rounds.add(rc.key)

    def price_and_kill(self, rc, s, row, leg, t):
        """-> (would_price, killed_by|None); the first reason in a fixed order wins."""
        lim = self.rule.get("limits") or {}
        if leg.get("type") == "maker":
            bb = _num(row.get("bb"))
            if bb is None: return None, "no_bid"
            price = round(bb - int(leg.get("offset_ticks") or 0) * 0.01, 2)
            if price < 0.01: return price, "price_floor"
        else:
            ask = _num(row.get("ba"))
            if ask is None: return None, "no_ask"
            price = ask
            mp = leg.get("max_price")
            if mp is not None and ask > float(mp) + 1e-9: return price, "max_price"
        if self.stopped: return price, "kill_file"
        if self.mode != "PAPER": return price, "observe:" + (self.gate_reasons[0] if self.gate_reasons else "no gate")
        if self.daily_stopped: return price, "daily_stop"
        if rc.n_trades >= int(lim.get("max_trades_per_round", 1)): return price, "limit:max_trades_per_round"
        if t - self.last_trade_t < float(lim.get("cooldown_s", 0)): return price, "limit:cooldown"
        if rc.key not in self.day_rounds and len(self.day_rounds) >= int(lim.get("max_rounds_per_day", 10 ** 6)): return price, "limit:max_rounds_per_day"
        size_usd = min(float(leg.get("size_usd", 5)), self.cfg.max_trade_usd)
        if self.open_cost + size_usd > self.cfg.capital + 1e-9: return price, "capital"
        return price, None

    def submit(self, rc, s, row, leg, price, t, t_el):
        size_usd = min(float(leg.get("size_usd", 5)), self.cfg.max_trade_usd)
        shares = round(size_usd / price, 2)
        order = dict(leg=leg.get("type", "taker"), asset=rc.up if s == "Up" else rc.down, side=s, size=shares, price=price,
                     offset_ticks=leg.get("offset_ticks"), ttl_s=leg.get("ttl_s"), rule_id=self.rule_id, market=rc.market,
                     start=rc.start, t_el=t_el, size_usd=size_usd, ctx=row, exit=self.rule.get("exit"), logged=False, closed=None)
        oid = self.book.submit(order, t)
        rc.orders.append(oid); self.open_cost += size_usd; self.n_fired += 1
        self.log(f"PAPER {order['leg'].upper()} {rc.market} {rc.start} {s} @{price} {shares}sh t_el={t_el} id={oid}")
        return oid

    def _log_dec(self, rc, t, t_el, side, fired, killed, price, row, throttle=False):
        if throttle and self.cfg.dec_throttle > 0:
            k = (side, killed); last = rc._dec_last.get(k)
            if last is not None and t - last < self.cfg.dec_throttle: return
            rc._dec_last[k] = t
        rec = dict(t=round(t, 3), start=rc.start, market=rc.market, side=side, rule_id=self.rule_id, fired=bool(fired), killed_by=killed,
                   would_price=price, t_el=t_el, mode=self.mode, ctx=row)
        self.dec.write(rec, t)
        if fired:
            rc.pending_dec.append({k: rec[k] for k in ("t", "start", "market", "side", "rule_id", "fired", "killed_by", "would_price", "t_el")})
            if killed:
                self.n_killed += 1
                if (side, killed) not in rc._killed_seen:
                    rc._killed_seen.add((side, killed)); self.log(fmt_killed(rec))

    # ---------- fills / exits
    def on_fill(self, f):
        o = self.book.orders.get(f["order_id"])
        self.log(f"FILL {o.get('market')} {o.get('start')} {f['side']} {f['leg']} {f['size']:g} @ {f['price']} fee {f['fee']} ({f['status']}, {f['fill_ms']} ms)")

    def tick(self, t):
        """every ~50 ms: resolve paper orders, log unfilled takers, run ttl/rule exits."""
        for f in self.book.on_tick(t): self.on_fill(f)
        for rc in list(self.rounds.values()):
            for oid in rc.orders:
                o = self.book.orders.get(oid)
                if o is None or o["logged"]: continue
                if o["status"] in ("none", "expired", "cancelled"):
                    self.open_cost -= o["size_usd"]; o["logged"] = True
                    self.write_trade(rc, o, winner="", pnl=None)
                elif o["status"] in ("full", "partial") and o["closed"] is None:
                    self.check_exit(rc, o, t)

    def check_exit(self, rc, o, t):
        ex = o.get("exit") or "hold_to_settle"
        mode = ex if isinstance(ex, str) else ex.get("mode")
        if mode == "ttl" and o["fills"] and t >= o["fills"][0]["ts"] + float(ex.get("ttl_s", 0)):
            self.close_leg(rc, o, t, "ttl")
        elif mode == "rule":
            try:
                row = rc.fs.row_now(o["side"], t); other = rc.fs.row_now("Down" if o["side"] == "Up" else "Up", t)
                if self.dsl().eval_when(ex.get("when") or [], row, other): self.close_leg(rc, o, t, "rule")
            except Exception:
                pass

    def close_leg(self, rc, o, t, why):
        """exit before settlement: sell at the bid as a taker (paper)."""
        bb = self.book.bb.get(o["asset"])
        if bb is None: return
        fee_out = self.book.fee_fn(bb, o["filled"])
        pnl = round((bb - o["cost"] / o["filled"]) * o["filled"] - o["fee"] - fee_out, 5)
        o["closed"] = dict(t=t, price=bb, why=why, pnl=pnl); o["logged"] = True
        self.book_pnl(rc, o, pnl, winner=f"exit:{why}")

    # ---------- settlement
    async def settle_later(self, rc):
        # copied from C:\g\tlfix\paired_maker\pm_bot.py (settle_later): poll gamma for the winner, up to 6 min
        for i in range(12):
            await asyncio.sleep(30)
            mk = await asyncio.get_event_loop().run_in_executor(None, gamma_market, rc.start, rc.market)
            if mk and mk["winner"]:
                self.settle_round(rc, mk["winner"]); return
        self.log(f"round {rc.market} {rc.start} unresolved after 6 min; left pending")
        self.forget_round(rc)

    def settle_round(self, rc, winner):
        rc.winner = winner; n = 0
        for oid in rc.orders:
            o = self.book.orders.get(oid)
            if o is None or o["logged"] or o["filled"] <= 0: continue
            pay = o["filled"] if winner == o["side"] else 0.0
            pnl = round(pay - o["cost"] - o["fee"], 5); o["logged"] = True; n += 1
            self.book_pnl(rc, o, pnl, winner)
        for d in rc.pending_dec:
            p = d.get("would_price")
            if p is None: continue
            fee = self.book.fee_fn(p, 1.0) if (self.rule.get("entry") or {}).get("leg", {}).get("type") != "maker" else 0.0
            d2 = dict(d); d2.update(winner=winner, would_pnl=round((1.0 if winner == d["side"] else 0.0) - p - fee, 5))
            self.dec.write(d2, d["t"], settled=True)
        rc.pending_dec = []
        self.log(f"SETTLED {rc.market} {rc.start}: winner {winner} legs {n} day {self.day_pnl:+.2f} total {self.realized:+.2f}")
        if n:
            try: self.log(f"decay: {json.dumps(decay.check(self.cfg.data), default=str)[:300]}")
            except Exception as e: self.log(f"decay check failed: {type(e).__name__}: {str(e)[:100]}")
        self.forget_round(rc)

    def book_pnl(self, rc, o, pnl, winner):
        self.open_cost -= o["size_usd"]; self.day_pnl += pnl; self.realized += pnl
        self.write_trade(rc, o, winner, pnl)
        if self.day_pnl <= -abs(self.cfg.daily_stop) and not self.daily_stopped:
            self.daily_stopped = True
            self.log(f"PAPER DAILY STOP {self.day_pnl:+.2f} <= -{self.cfg.daily_stop:g}: no new paper trades today (log only)")

    def write_trade(self, rc, o, winner, pnl):
        f0 = o["fills"][0] if o["fills"] else None
        price_filled = round(o["cost"] / o["filled"], 4) if o["filled"] > 0 else ""
        R = round(pnl / o["size_usd"], 5) if (pnl is not None and o["size_usd"]) else ""
        row = [_util.iso(), self.rule_id, rc.market, rc.start, o["side"], o["leg"], o["t_el"], o["price"] if o["leg"] == "maker" else o.get("limit", o["price"]),
               price_filled, round(o["filled"], 4) if o["filled"] > 0 else round(o["size"], 4), f0["fill_ms"] if f0 else "", o["status"], round(o["fee"], 5),
               winner or "", "" if pnl is None else round(pnl, 5), R, "" if self.backtest_avg_R is None else self.backtest_avg_R,
               json.dumps(o.get("ctx") or {}, separators=(",", ":"), default=str)]
        _util.append_csv(os.path.join(self.cfg.data, "bot", "paper_trades.csv"), PAPER_TRADES_HEADER, row)

    def forget_round(self, rc):
        self.book.forget(rc.orders); self.rounds.pop(rc.key, None)

    # ---------- round handling (copied from pm_bot.py round_loop/new_round/close_round, generalised to 2 markets)
    async def round_loop(self):
        while True:
            t = now()
            d = datetime.now(timezone.utc).date()
            if d != self.day:
                self.log(f"UTC day {self.day} closed: paper day P&L {self.day_pnl:+.2f}, rounds traded {len(self.day_rounds)}")
                self.day = d; self.day_pnl = 0.0; self.day_rounds = set(); self.daily_stopped = False
            for market in self.cfg.markets:
                T = ROUND_T[market]; start = int(t - t % T); t_el = t - start
                if (market, start) not in self.rounds and not self.stopped:
                    await self.new_round(market, start)
                if t_el >= T - 50 and (market, start + T) not in self.next_round:
                    self.next_round[(market, start + T)] = None
                    asyncio.ensure_future(self.prefetch(market, start + T))
            for rc in list(self.rounds.values()):
                if not rc.closed and t >= rc.start + rc.T:
                    await self.close_round(rc)
            if os.path.exists(self.cfg.kill_file) and not self.stopped:
                self.log("KILL file present: cancelling paper orders and stopping new trades")
                self.book.cancel_all(t, "kill"); self.stopped = True
            elif self.stopped and not os.path.exists(self.cfg.kill_file):
                self.log("KILL file removed: trading resumes"); self.stopped = False
            await asyncio.sleep(0.5)

    async def prefetch(self, market, start):
        self.next_round[(market, start)] = await asyncio.get_event_loop().run_in_executor(None, gamma_market, start, market)

    async def new_round(self, market, start):
        mk = self.next_round.pop((market, start), None)
        if mk is None:
            mk = await asyncio.get_event_loop().run_in_executor(None, gamma_market, start, market)
        if not mk:
            if market == "15m": self.rounds[(market, start)] = Round15Missing(market, start)
            else: self.log(f"round {market} {start}: market not found yet"); await asyncio.sleep(3)
            return
        try:
            rc = self.add_round(market, start, mk)
        except ImportError as e:
            self.log(f"FeatureState unavailable: {e}"); await asyncio.sleep(10); return
        if self.sub_market: self.sub_market.set()
        self.log(f"round {market} {start} ({datetime.fromtimestamp(start, timezone.utc).strftime('%H:%M')}Z) cond ...{mk['condition_id'][-8:]} open={self.opens.get(start)} mode={self.mode}")

    async def close_round(self, rc):
        rc.closed = True
        if isinstance(rc, Round15Missing):
            self.rounds.pop(rc.key, None); return
        t = now()
        for oid in rc.orders: self.book.cancel(oid, t, "round end")
        self.tick(t)
        self.asset_round.pop(rc.up, None); self.asset_round.pop(rc.down, None)
        if self.sub_market: self.sub_market.set()
        if any(self.book.orders.get(o, {}).get("filled", 0) > 0 and not self.book.orders[o]["logged"] for o in rc.orders) or rc.pending_dec:
            asyncio.ensure_future(self.settle_later(rc))
        else:
            self.forget_round(rc)

    # ---------- feeds (copied from pm_bot.py market_feed / rtds_feed; the market handler now dispatches to on_market_msg)
    async def market_feed(self):
        import websockets
        while True:
            await self.sub_market.wait(); self.sub_market.clear()
            toks = list(self.asset_round.keys())
            if not toks:
                await asyncio.sleep(0.5); self.sub_market.set(); continue
            try:
                async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None) as ws:
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                    self.log(f"market feed subscribed ({len(toks)} assets)")
                    async for raw in ws:
                        if self.sub_market.is_set(): break          # round changed: resubscribe
                        rx = now(); self.mkt_n += 1
                        msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
                        for m in msgs:
                            if isinstance(m, dict): self.on_market_msg(m, rx)
                        h = (now() - rx) * 1000
                        if h > self.mkt_max_ms: self.mkt_max_ms = h
            except websockets.ConnectionClosed as e:
                code = getattr(e, "code", None) or getattr(getattr(e, "rcvd", None), "code", None)
                if code == 1013:
                    self.log("market feed closed 1013 (slow consumer / try again later): reconnect in 1 s")
                else:
                    self.log(f"market feed closed {code}: {str(e)[:100]} (reconnect)")
                self.sub_market.set(); await asyncio.sleep(1)
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
                                self.spot_lag_ms = now() * 1000 - tms if tms > 1e11 else None
                                self.on_spot(tsec, val)
                            elif topic == "crypto_prices_twap_sixty":
                                self.on_twap(tsec, val)
                    finally:
                        pt.cancel()
            except Exception as e:
                self.log(f"rtds feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)"); await asyncio.sleep(2)

    # ---------- housekeeping
    async def tick_loop(self):
        while True:
            try: self.tick(now())
            except Exception as e:
                self.n_errors += 1
                if self.n_errors <= 3: self.log(f"tick error: {type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(0.05)

    async def flush_loop(self):
        while True:
            await asyncio.sleep(2)
            try: self.dec.flush()
            except Exception as e: self.log(f"decision log flush failed: {type(e).__name__}: {str(e)[:100]}")

    async def heartbeat(self):
        # copied from C:\g\tlfix\paired_maker\pm_bot.py (heartbeat): event-loop drift probe
        while True:
            t0 = now(); await asyncio.sleep(0.1)
            d = (now() - t0 - 0.1) * 1000
            if d > self.loop_drift_ms: self.loop_drift_ms = d

    def hb_note(self):
        return f"mode={self.mode} rule={self.rule_id} rounds={len(self.rounds)} dec={self.n_dec} fired={self.n_fired} killed={self.n_killed} day={self.day_pnl:+.2f} stopped={self.stopped}"

    async def hb_loop(self):
        while True:
            _util.write_heartbeat(self.cfg.data, "bot", True, self.hb_note())
            await asyncio.sleep(30)

    async def status_loop(self):
        # copied from C:\g\tlfix\paired_maker\pm_bot.py (status_loop), fields adapted
        while True:
            await asyncio.sleep(30)
            live = [f"{k[0]}:{k[1]}" for k in self.rounds]
            self.log(f"status {self.hb_note()} live={live} spot={self.spot} orders_live={len(self.book.live_orders())} "
                     f"feed_lag={self.feed_lag_ms and round(self.feed_lag_ms)}ms spot_lag={self.spot_lag_ms and round(self.spot_lag_ms)}ms "
                     f"mkt_msgs={self.mkt_n} handler_max={self.mkt_max_ms:.1f}ms loop_drift_max={self.loop_drift_ms:.1f}ms rtds={self.rtds_n} "
                     f"trades={self.book.n_trades} dupes={self.book.n_dupes} errors={self.n_errors}")
            self.mkt_n = 0; self.mkt_max_ms = 0.0; self.loop_drift_ms = 0.0

    async def run(self):
        self.sub_market = asyncio.Event()
        await asyncio.gather(self.round_loop(), self.market_feed(), self.rtds_feed(), self.tick_loop(), self.flush_loop(),
                             self.hb_loop(), self.status_loop(), self.heartbeat())


class Round15Missing:
    """placeholder so a 15m start without a gamma market is asked once per 15 min, not every 0.5 s."""
    def __init__(self, market, start):
        self.market = market; self.start = start; self.T = ROUND_T[market]; self.closed = False; self.orders = []
        self.pending_dec = []; self.up = None; self.down = None

    @property
    def key(self):
        return (self.market, self.start)


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def load_rule(cfg, log):
    """active_rule.json + gate.check -> (rule|None, reasons). The rule is kept in OBSERVE mode so killed trades are journaled."""
    rule = _util.load_json(os.path.join(cfg.data, "bot", "active_rule.json"))
    if not isinstance(rule, dict):
        log("no active_rule.json: OBSERVE mode (nothing to evaluate)"); return None, ["no active rule"]
    ok, reasons = gate.check(rule, cfg.data)
    log(gate.describe(ok, reasons) + f" rule={rule.get('id')}")
    return rule, ([] if ok else reasons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.path.join(os.path.expanduser("~"), "qdesk", ".env"))
    ap.add_argument("--data", default=None); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--date", default=None)
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        if os.path.exists(os.path.expanduser(a.env)): load_dotenv(os.path.expanduser(a.env), override=False)
    except ImportError:
        pass
    dry_run_guard()
    cfg = Cfg(a.data); os.makedirs(os.path.join(cfg.data, "state"), exist_ok=True); log = Log(cfg)
    log(f"qbot start PAPER ONLY markets={cfg.markets} capital={cfg.capital} max_trade={cfg.max_trade_usd} daily_stop={cfg.daily_stop} data={cfg.data}")
    if os.path.exists(cfg.kill_file):
        log("KILL file present at start: refusing to run"); _util.write_heartbeat(cfg.data, "bot", False, "KILL file present"); raise SystemExit(3)
    rule, reasons = load_rule(cfg, log)
    bot = QBot(cfg, log, rule, reasons)
    log(f"mode={bot.mode} rule={bot.rule_id}" + (f" reasons={reasons}" if reasons else ""))
    _util.write_heartbeat(cfg.data, "bot", True, bot.hb_note())
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
    finally:
        bot.dec.flush()


if __name__ == "__main__":
    main()
