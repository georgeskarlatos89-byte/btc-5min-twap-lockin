#!/usr/bin/env python3
"""feat_recorder.py - always-on per-second feature recorder for the BTC 5m and 15m rounds (no keys, no orders).

  python3 feat_recorder.py                 live service (qd-recorder.service)
  python3 feat_recorder.py --dry-run       replay tests/fixtures/bme_sample_20260926_first_hour.csv.gz through the SAME
                                           handler path (BME rows -> ws-shaped messages) and print row counts (parity hook)

Feeds (copied from C:\\g\\tlfix\\paired_maker\\pm_bot.py: gamma_market, market_feed, rtds_feed):
  CLOB market ws  wss://ws-subscriptions-clob.polymarket.com/ws/market  {"assets_ids":[..],"type":"market"}
                  book / price_change (price_changes[] with best_bid/best_ask) / last_trade_price  (shapes verified 2026-10-29)
  RTDS            wss://ws-live-data.polymarket.com  topics crypto_prices_chainlink + crypto_prices_twap_sixty, compact-JSON
                  filters, "PING" every 5 s
One FeatureState (qdesk.features.features) per round (5m and 15m); the ws handler ONLY routes messages to FeatureState
methods (kept < 1 ms); flush(now) every second -> rows appended to data/features/live_YYYYMMDD.csv, gzipped to
day_YYYYMMDD.csv.gz at the UTC day rotate. Moon Dev totals every 30 s and Hyperliquid metaAndAssetCtxs every 60 s
feed on_ext (forward-filled, never raise). Heartbeat data/state/hb_recorder.json every 30 s with msgs/s and reconnect
counts. ws close 1013 "slow consumer" -> immediate reconnect (counted). KILL file ~/qdesk/KILL -> idle with ok=false.
"""
import argparse
import asyncio
import csv
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO_ROOT)

try:
    from qdesk.features.features import FeatureState, FEATURE_COLUMNS
except ImportError:
    FeatureState = None
    FEATURE_COLUMNS = None

UA = {"User-Agent": "Mozilla/5.0 qdesk-recorder (read-only)"}
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_RTDS = "wss://ws-live-data.polymarket.com"
STEP = {"5m": 300, "15m": 900}
GRACE_S = 10.0                 # keep a round alive this long after its end for the look-ahead flush
PREFETCH_S = 40.0              # look up the next round's tokens this long before the rollover
KILL_FILE = os.path.expanduser("~/qdesk/KILL")
FIXTURE = os.path.join(REPO_ROOT, "qdesk", "tests", "fixtures", "bme_sample_20260926_first_hour.csv.gz")


def data_dir():
    return os.path.expanduser(os.environ.get("QD_DATA") or "~/qdesk/data")


def now():
    return time.time()


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def log(s):
    print(f"{ts()} {s}", flush=True)


def day_of(t):
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y%m%d")


# copied from C:\g\tlfix\paired_maker\pm_bot.py (gamma_market), generalised to the variant
def gamma_market(start, variant="5m"):
    for q in ("", "&closed=true"):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{variant}-{start}{q}", headers=UA)
        try:
            d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception:
            continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]; outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            win = outs[pr.index(1.0)] if (m.get("closed") and sorted(pr) == [0.0, 1.0]) else None
            return dict(start=start, condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")], winner=win, market=variant)
    return None


# ---------- row writer
def fmt(v):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(v)



def _blackout_fn():
    """macro blackout 0/1 for the live feature column (same source as the history build)."""
    try:
        from qdesk.scout import macro
        return macro.is_blackout
    except Exception:
        return None

class DayWriter:
    """Appends feature rows to <dir>/<prefix>_YYYYMMDD.csv (header once); at a day change the closed day is gzipped to
    day_YYYYMMDD.csv.gz (appended to an existing gz, header kept once) and the live file removed."""

    def __init__(self, dirpath, columns=None, prefix="live"):
        self.dir = dirpath; os.makedirs(dirpath, exist_ok=True)
        self.columns = list(columns) if columns else None
        self.prefix = prefix; self.day = None; self.fh = None; self.rows = 0; self.rows_day = 0

    def path(self, day):
        return os.path.join(self.dir, f"{self.prefix}_{day}.csv")

    def _open(self, day):
        p = self.path(day); new = not os.path.exists(p) or os.path.getsize(p) == 0
        self.fh = open(p, "a", newline=""); self.day = day; self.rows_day = 0
        if new:
            self.fh.write(",".join(self.columns) + "\n")

    def write(self, row):
        if self.columns is None:
            self.columns = list(row.keys())
        d = day_of(float(row.get("start", 0)) + float(row.get("t_el", 0)))
        if d != self.day:
            self.rotate(d)
        self.fh.write(",".join(fmt(row.get(c)) for c in self.columns) + "\n")
        self.rows += 1; self.rows_day += 1

    def flush(self):
        if self.fh:
            self.fh.flush()

    def rotate(self, new_day):
        old = self.day
        if self.fh:
            self.fh.close(); self.fh = None
        if old:
            self.gzip_day(old)
        if new_day:
            self._open(new_day)

    def gzip_day(self, day):
        src = self.path(day); dst = os.path.join(self.dir, f"day_{day}.csv.gz"); tmp = dst + ".tmp"
        if not os.path.exists(src):
            return
        try:
            with gzip.open(tmp, "wt", newline="") as out:
                if os.path.exists(dst):
                    with gzip.open(dst, "rt", newline="") as prev:
                        for line in prev:
                            out.write(line)
                    skip_header = True
                else:
                    skip_header = False
                with open(src, newline="") as f:
                    for i, line in enumerate(f):
                        if i == 0 and skip_header:
                            continue
                        out.write(line)
            os.replace(tmp, dst); os.remove(src)
            log(f"rotated {src} -> {dst}")
        except Exception as e:
            log(f"rotate failed for {day}: {type(e).__name__}: {e}")

    def close(self):
        if self.fh:
            self.fh.close(); self.fh = None


# ---------- rounds
class Round:
    def __init__(self, market, start, up, down, cond=None, spot_lag=1.44):
        self.market = market; self.start = int(start); self.T = STEP[market]; self.up = up; self.down = down; self.cond = cond
        self.fs = FeatureState(market, self.start, up, down, spot_lag=spot_lag, blackout_fn=_blackout_fn(), src="live") if FeatureState else None
        self.rows = 0; self.msgs = 0

    @property
    def end(self):
        return self.start + self.T


class Recorder:
    def __init__(self, writer, dry=False):
        self.writer = writer; self.dry = dry
        self.rounds = {}            # (market, start) -> Round
        self.asset_map = {}         # asset -> Round
        self.pending = {}           # (market, start) -> gamma dict (prefetched)
        self.sub_market = None
        self.n_msgs = 0; self.n_unrouted = 0; self.handler_max_ms = 0.0; self.rows_written = 0
        self.reconnects = {"market": 0, "rtds": 0, "slow_consumer": 0}
        self.rtds_n = {}; self.spot = None; self.spot_t = None
        self.ext = {"liq_long": None, "liq_short": None, "funding": None, "oi": None}
        self.ext_ok = {"moondev": None, "hl": None}
        self.stopped = False; self.last_event_t = None

    # ----- round bookkeeping
    def add_round(self, market, start, up, down, cond=None):
        key = (market, int(start))
        if key in self.rounds:
            return self.rounds[key]
        r = Round(market, start, up, down, cond)
        self.rounds[key] = r; self.asset_map[up] = r; self.asset_map[down] = r
        if self.sub_market is not None:
            self.sub_market.set()
        return r

    def drop_round(self, key, t_final):
        r = self.rounds.pop(key, None)
        if r is None:
            return
        self.emit(r, t_final)
        for a in (r.up, r.down):
            if self.asset_map.get(a) is r:
                self.asset_map.pop(a, None)
        log(f"round {r.market} {r.start} closed: {r.rows} rows, {r.msgs} msgs")

    def emit(self, r, t_now):
        if r.fs is None:
            return 0
        try:
            rows = r.fs.flush(t_now)
        except Exception as e:
            log(f"flush error {r.market} {r.start}: {type(e).__name__}: {str(e)[:80]}"); return 0
        for row in rows:
            self.writer.write(row)
        r.rows += len(rows); self.rows_written += len(rows)
        return len(rows)

    def flush_all(self, t_now):
        n = 0
        for key in list(self.rounds):
            r = self.rounds[key]
            if t_now >= r.end + GRACE_S:
                self.drop_round(key, t_now)
            else:
                n += self.emit(r, t_now)
        self.writer.flush()
        return n

    # ----- HOT PATH: only FeatureState calls, no logging
    def on_market_msg(self, m, t_recv):
        et = m.get("event_type")
        if et == "price_change":
            tsm = m.get("timestamp"); t = float(tsm) / 1000.0 if tsm else t_recv
            for ch in m.get("price_changes") or ():
                r = self.asset_map.get(ch.get("asset_id"))
                if r is None or r.fs is None:
                    self.n_unrouted += 1; continue
                bb = ch.get("best_bid"); ba = ch.get("best_ask")
                r.msgs += 1
                r.fs.on_price_change(ch["asset_id"], ch.get("side"), float(ch["price"]), float(ch["size"]),
                                     float(bb) if bb not in (None, "") else None, float(ba) if ba not in (None, "") else None, t)
        elif et == "last_trade_price":
            r = self.asset_map.get(m.get("asset_id"))
            if r is None or r.fs is None:
                self.n_unrouted += 1; return
            tsm = m.get("timestamp"); t = float(tsm) / 1000.0 if tsm else t_recv
            r.msgs += 1
            r.fs.on_trade(m["asset_id"], m.get("side"), float(m["price"]), float(m["size"]), t)
        elif et == "book":
            r = self.asset_map.get(m.get("asset_id"))
            if r is None or r.fs is None:
                self.n_unrouted += 1; return
            tsm = m.get("timestamp"); t = float(tsm) / 1000.0 if tsm else t_recv
            r.msgs += 1
            r.fs.on_book(m["asset_id"], [(float(x["price"]), float(x["size"])) for x in m.get("bids") or ()],
                         [(float(x["price"]), float(x["size"])) for x in m.get("asks") or ()], t)
        elif et == "bba":                       # synthetic (BME replay only): best bid/ask without levels
            r = self.asset_map.get(m.get("asset_id"))
            if r is None or r.fs is None:
                self.n_unrouted += 1; return
            r.msgs += 1
            r.fs.on_bba(m["asset_id"], m.get("bb"), m.get("ba"), float(m["timestamp"]) / 1000.0)

    def on_rtds_msg(self, m, t_recv):
        if not isinstance(m, dict) or m.get("type") != "update":
            return
        p = m.get("payload") or {}
        if p.get("symbol") != "btc/usd":
            return
        try:
            val = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
        except Exception:
            return
        tms = float(p.get("timestamp") or t_recv * 1000)
        tsec = tms / 1000.0 if tms > 1e11 else tms
        topic = m.get("topic")
        self.rtds_n[topic] = self.rtds_n.get(topic, 0) + 1
        if topic == "crypto_prices_chainlink":
            self.spot = val; self.spot_t = tsec
            for r in self.rounds.values():
                if r.fs is not None:
                    r.fs.on_spot(tsec, val)
        elif topic == "crypto_prices_twap_sixty":
            for r in self.rounds.values():
                if r.fs is not None:
                    r.fs.on_twap(tsec, val)

    def push_ext(self, t):
        for r in self.rounds.values():
            if r.fs is not None:
                try:
                    r.fs.on_ext(t, liq_long=self.ext["liq_long"], liq_short=self.ext["liq_short"], funding=self.ext["funding"], oi=self.ext["oi"])
                except Exception:
                    pass

    # ----- live loops
    async def round_loop(self):
        loop = asyncio.get_event_loop()
        while not self.stopped:
            t = now()
            for market, T in STEP.items():
                start = int(t - t % T); key = (market, start)
                if key not in self.rounds:
                    mk = self.pending.pop(key, None)
                    if mk is None:
                        mk = await loop.run_in_executor(None, gamma_market, start, market)
                    if mk:
                        self.add_round(market, start, mk["up"], mk["down"], mk["condition_id"])
                        log(f"round {market} {start} ({datetime.fromtimestamp(start, timezone.utc).strftime('%H:%M')}Z) cond ...{mk['condition_id'][-8:]}")
                    else:
                        self.rounds.setdefault(key, None)      # mark as looked-up-and-missing; retried below
                nxt = (market, start + T)
                if t - start >= T - PREFETCH_S and nxt not in self.pending and nxt not in self.rounds:
                    mk = await loop.run_in_executor(None, gamma_market, start + T, market)
                    if mk:
                        self.pending[nxt] = mk
            # retry missing rounds every 5 s
            for key, r in list(self.rounds.items()):
                if r is None:
                    if int(t) % 5 == 0:
                        self.rounds.pop(key, None)
                    elif t >= key[1] + STEP[key[0]]:
                        self.rounds.pop(key, None)
            if os.path.exists(KILL_FILE):
                if not self.stopped:
                    log("KILL file present: feeds stop, recorder idles"); self.stopped = True
            await asyncio.sleep(0.5)

    async def flush_loop(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                self.flush_all(now())
            except Exception as e:
                log(f"flush loop error: {type(e).__name__}: {str(e)[:80]}")

    async def market_feed(self):
        import websockets
        while True:
            await self.sub_market.wait(); self.sub_market.clear()
            toks = sorted({a for a, r in self.asset_map.items() if r is not None})
            if not toks or self.stopped:
                await asyncio.sleep(1); self.sub_market.set() if not self.stopped else None
                if self.stopped:
                    await asyncio.sleep(5); self.sub_market.set()
                continue
            try:
                async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None, open_timeout=10) as ws:
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                    log(f"market feed subscribed ({len(toks)} assets)")
                    async for raw in ws:
                        if self.sub_market.is_set():
                            break                                  # asset set changed: resubscribe
                        rx = now()
                        try:
                            msgs = json.loads(raw)
                        except Exception:
                            continue
                        msgs = msgs if isinstance(msgs, list) else [msgs]
                        for m in msgs:
                            if isinstance(m, dict):
                                self.n_msgs += 1
                                self.on_market_msg(m, rx)
                        h = (now() - rx) * 1000
                        if h > self.handler_max_ms:
                            self.handler_max_ms = h
                        self.last_event_t = rx
            except Exception as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                if code == 1013:
                    self.reconnects["slow_consumer"] += 1; log("market feed closed 1013 (slow consumer): reconnecting now")
                else:
                    self.reconnects["market"] += 1; log(f"market feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)")
                    await asyncio.sleep(1)
                self.sub_market.set()

    # copied from C:\g\tlfix\paired_maker\pm_bot.py (rtds_feed): exact subscribe payload (compact-JSON filters) + PING 5 s
    async def rtds_feed(self):
        import websockets
        while True:
            if self.stopped:
                await asyncio.sleep(5); continue
            try:
                async with websockets.connect(WS_RTDS, ping_interval=None, max_size=None, open_timeout=10) as ws:
                    await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                        {"topic": "crypto_prices_chainlink", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))},
                        {"topic": "crypto_prices_twap_sixty", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))}]}))
                    log("rtds feed subscribed")

                    async def ping():
                        while True:
                            await asyncio.sleep(5)
                            try:
                                await ws.send("PING")
                            except Exception:
                                return
                    pt = asyncio.ensure_future(ping())
                    try:
                        async for raw in ws:
                            if self.stopped:
                                break
                            try:
                                m = json.loads(raw)
                            except Exception:
                                continue
                            self.on_rtds_msg(m, now())
                    finally:
                        pt.cancel()
            except Exception as e:
                self.reconnects["rtds"] += 1
                log(f"rtds feed error: {type(e).__name__}: {str(e)[:100]} (reconnect)"); await asyncio.sleep(2)

    async def ext_loop(self):
        loop = asyncio.get_event_loop(); last_md = 0.0; last_hl = 0.0
        try:
            from qdesk.scout import moondev_pull as MD
        except Exception:
            MD = None
        try:
            from qdesk.scout import hl_pull as HL
        except Exception:
            HL = None
        while True:
            t = now()
            if MD and t - last_md >= 30:
                last_md = t
                try:
                    ok, (lo, sh), note = await loop.run_in_executor(None, MD.live_totals)
                    if ok:
                        self.ext["liq_long"], self.ext["liq_short"] = lo, sh
                    self.ext_ok["moondev"] = ok if ok else note[:60]
                except Exception as e:
                    self.ext_ok["moondev"] = f"{type(e).__name__}"
                self.push_ext(now())
            if HL and t - last_hl >= 60:
                last_hl = t
                try:
                    ok, d, note = await loop.run_in_executor(None, HL.meta_btc)
                    if ok:
                        self.ext["funding"], self.ext["oi"] = d["funding"], d["oi_usd"]
                    self.ext_ok["hl"] = ok if ok else note[:60]
                except Exception as e:
                    self.ext_ok["hl"] = f"{type(e).__name__}"
                self.push_ext(now())
            await asyncio.sleep(1)

    async def heartbeat_loop(self):
        p = os.path.join(data_dir(), "state", "hb_recorder.json"); os.makedirs(os.path.dirname(p), exist_ok=True)
        last_n = 0; last_t = now()
        while True:
            await asyncio.sleep(30)
            t = now(); rate = (self.n_msgs - last_n) / max(1e-9, t - last_t); last_n = self.n_msgs; last_t = t
            active = {f"{k[0]}:{k[1]}": r.rows for k, r in self.rounds.items() if r is not None}
            fresh = self.last_event_t is not None and t - self.last_event_t < 60
            ok = (not self.stopped) and fresh and FeatureState is not None
            note = "KILL file" if self.stopped else ("no market events for 60 s" if not fresh else ("FeatureState missing" if FeatureState is None else "ok"))
            doc = {"ts": int(t), "utc": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": "recorder", "ok": ok, "note": note,
                   "msgs_per_s": round(rate, 2), "msgs_total": self.n_msgs, "unrouted": self.n_unrouted, "handler_max_ms": round(self.handler_max_ms, 2),
                   "reconnects": dict(self.reconnects), "rows_written": self.rows_written, "rounds_active": active,
                   "spot": self.spot, "spot_age_s": round(t - self.spot_t, 1) if self.spot_t else None, "rtds": self.rtds_n,
                   "ext": self.ext, "ext_ok": self.ext_ok, "writer_day": self.writer.day}
            try:
                tmp = p + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(doc, f)
                os.replace(tmp, p)
            except Exception as e:
                log(f"heartbeat write failed: {type(e).__name__}")
            log(f"hb msgs/s={rate:.1f} handler_max={self.handler_max_ms:.2f}ms rows={self.rows_written} rounds={list(active)} "
                f"reconnects={self.reconnects} spot={self.spot} ext_ok={self.ext_ok}")
            self.handler_max_ms = 0.0

    async def run(self):
        self.sub_market = asyncio.Event()
        await asyncio.gather(self.round_loop(), self.flush_loop(), self.market_feed(), self.rtds_feed(), self.ext_loop(), self.heartbeat_loop())


# ---------- dry-run replay of the BME fixture through the same handler
def load_tokens_for_replay():
    """asset -> {start, outcome, other} from the first map found (fixture, QD_TOKENS, data/tokens, ~/pm)."""
    cands = [os.path.join(REPO_ROOT, "qdesk", "tests", "fixtures", "tokens_btc5m_range.json"), os.environ.get("QD_TOKENS") or "",
             os.path.join(data_dir(), "tokens", "tokens_btc5m_range.json"), os.path.join(data_dir(), "tokens", "tokens_btc5m_live.json"),
             os.path.expanduser("~/pm/tokens_btc5m_range.json")]
    for p in cands:
        if p and os.path.exists(p):
            try:
                with open(p) as f:
                    return json.load(f), p
            except Exception:
                continue
    return {}, None


def replay_fixture(path, rec, max_rows=None):
    """BME rows -> ws-shaped messages -> rec.on_market_msg / rec.on_rtds_msg; flush each event-second. Returns stats."""
    tokens, tok_src = load_tokens_for_replay()
    inferred = {}          # condition_id -> [assets] when no token map is available
    stats = {"rows": 0, "price_change": 0, "trade": 0, "book": 0, "bba": 0, "spot": 0, "twap": 0, "skipped": 0, "rounds": 0,
             "tokens_src": tok_src, "handler_max_ms": 0.0, "fixture_error": None}
    last_sec = None; t_max = 0.0; hmax = 0.0
    with gzip.open(path, "rt", newline="") as f:
        rd = csv.reader(f); hdr = next(rd)
        ix = {k: i for i, k in enumerate(hdr)}
        while True:
            try:
                r = next(rd)
            except StopIteration:
                break
            except (EOFError, OSError, csv.Error) as e:          # truncated / still-being-written gz: keep what we have
                stats["fixture_error"] = f"{type(e).__name__}: {str(e)[:80]}"; break
            if max_rows and stats["rows"] >= max_rows:
                break
            stats["rows"] += 1
            try:
                tms = float(r[ix["ts_ms"]])
            except (ValueError, IndexError):
                stats["skipped"] += 1; continue
            et = r[ix["event_type"]]; asset = r[ix["asset"]]; cond = r[ix["market"]]
            t = tms / 1000.0; h0 = time.perf_counter()
            if et in ("spot_tick", "twap_tick"):
                topic = "crypto_prices_chainlink" if et == "spot_tick" else "crypto_prices_twap_sixty"
                rec.on_rtds_msg({"type": "update", "topic": topic, "payload": {"symbol": "btc/usd", "value": r[ix["price"]], "timestamp": tms}}, t)
                stats["spot" if et == "spot_tick" else "twap"] += 1
            else:
                if asset not in rec.asset_map and asset:
                    info = tokens.get(asset)
                    if info:
                        up, dn = (asset, info["other"]) if info["outcome"] == "Up" else (info["other"], asset)
                        if ("5m", int(info["start"])) not in rec.rounds:
                            rec.add_round("5m", int(info["start"]), up, dn, cond); stats["rounds"] += 1
                    else:
                        lst = inferred.setdefault(cond, [])
                        if asset not in lst:
                            lst.append(asset)
                        if len(lst) == 2:
                            start = int(t - t % 300)
                            rec.add_round("5m", start, lst[0], lst[1], cond); stats["rounds"] += 1
                if et == "price_change":
                    bb = r[ix["best_bid"]]; ba = r[ix["best_ask"]]
                    rec.on_market_msg({"event_type": "price_change", "timestamp": tms, "market": cond, "price_changes": [
                        {"asset_id": asset, "side": r[ix["side"]], "price": r[ix["price"]], "size": r[ix["size"]],
                         "best_bid": bb if bb and float(bb) > 0.005 else None, "best_ask": ba if ba and float(ba) > 0.005 else None}]}, t)
                    stats["price_change"] += 1
                elif et == "trade":
                    rec.on_market_msg({"event_type": "last_trade_price", "timestamp": tms, "market": cond, "asset_id": asset,
                                       "side": r[ix["side"]], "price": r[ix["price"]], "size": r[ix["size"]]}, t)
                    stats["trade"] += 1
                elif et in ("bba", "book"):
                    bb = r[ix["best_bid"]]; ba = r[ix["best_ask"]]
                    rec.on_market_msg({"event_type": "bba", "timestamp": tms, "asset_id": asset,
                                       "bb": float(bb) if bb not in (None, "") else None, "ba": float(ba) if ba not in (None, "") else None}, t)
                    stats[et] += 1
                else:
                    stats["skipped"] += 1
            h = (time.perf_counter() - h0) * 1000
            if h > hmax:
                hmax = h
            if et != "spot_tick":                      # spot rows carry the SOURCE time, 1.44 s before arrival
                t_max = max(t_max, t)
            sec = int(t_max)
            if last_sec is None or sec > last_sec:
                last_sec = sec; rec.flush_all(t_max)
    rec.flush_all(t_max + GRACE_S + 1)
    for key in list(rec.rounds):
        rec.drop_round(key, t_max + GRACE_S + 1)
    stats["handler_max_ms"] = round(hmax, 3)
    if inferred and not tokens:
        stats["note"] = "no token map found: sides assigned by first appearance (Up = first asset seen) - parity needs tests/fixtures/tokens_btc5m_range.json"
    return stats


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--date", default=None)
    ap.add_argument("--fixture", default=FIXTURE); ap.add_argument("--max-rows", type=int, default=None)
    a = ap.parse_args()
    feat_dir = os.path.join(data_dir(), "features")
    if a.dry_run:
        if not os.path.exists(a.fixture):
            print(f"dry-run: fixture not found at {a.fixture}; nothing to replay (exit 0)"); return
        if FeatureState is None:
            print("dry-run: qdesk.features.features.FeatureState not importable yet; counting fixture rows only")
        writer = DayWriter(feat_dir, FEATURE_COLUMNS, prefix="dry")
        rec = Recorder(writer, dry=True)
        t0 = time.time(); st = replay_fixture(a.fixture, rec, a.max_rows); writer.close()
        st.update(rows_out=writer.rows, seconds=round(time.time() - t0, 1), unrouted=rec.n_unrouted, out=writer.path(writer.day) if writer.day else None)
        print(json.dumps(st, indent=1))
        return
    if FeatureState is None:
        log("FATAL: qdesk.features.features.FeatureState not importable; the recorder cannot run"); sys.exit(2)
    writer = DayWriter(feat_dir, FEATURE_COLUMNS, prefix="live")
    rec = Recorder(writer)
    log(f"feat_recorder start data={data_dir()} columns={len(FEATURE_COLUMNS)}")
    try:
        asyncio.run(rec.run())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            rec.flush_all(now()); writer.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
