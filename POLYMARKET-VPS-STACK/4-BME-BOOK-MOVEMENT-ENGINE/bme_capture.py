#!/usr/bin/env python3
"""
bme_capture.py — BOOK-MOVEMENT ENGINE capture daemon (measure-only, never trades).

Records everything the public market channel (wss://ws-subscriptions-clob.polymarket.com/ws/market)
does on btc-updown 5m + 15m rounds, plus the Chainlink spot + TWAP-60 stream (RTDS) fused
into the same timeline — because no historical L2 exists anywhere (prices-history stops at
1-minute buckets), the only way to have this data is to record it forward.

Writes (s1_harness/s9_data/bme/), one set per UTC day:
  events_YYYYMMDD.csv      every price_change (per-order hash!), trade print, tick-size
                           change, best_bid_ask, spot/twap tick markers, pre-tick pull counts
  books_YYYYMMDD.csv       full L2 snapshot rows (on subscribe + every 10s per token)
  bookstate_1s_YYYYMMDD.csv  1s top-of-book + depth (imbalance studies)
  hashes_YYYYMMDD.csv      per-order-hash activity stats (flicker/spoof candidates)
Previous day is gzipped at rotation. Kill: touch s1_harness/KILL.

Pre-registered scoring (after >= 7 full days, gates like everything else in this repo:
incubate a signal only at >= +2c/sh post-fee EV over >= 100 independent rounds):
  S1 flicker: do top-flicker hashes' levels act as fake support/resistance?
  S2 pre-tick pulls: does a cancel burst in the 200ms before a Chainlink tick predict the tick?
  S3 imbalance: does 1s bid/ask depth asymmetry predict round outcome after controlling for mid?
  S4 double-tap reaction: time from an informed 2nd taker BUY to opposite-ask pull (the S9
     follower window reborn with a book-timed trigger); needs post-hoc wallet join via v1 /trades.
  S5 divergence: early-round mid vs TWAP-fair value vs outcome.

Usage:
  python3 s1_harness/bme_capture.py                # forever (VPS)
  python3 s1_harness/bme_capture.py --minutes 8    # bounded run (tests)
Requires: websockets.
"""
import argparse, asyncio, csv, gzip, glob, json, os, time, urllib.request
from collections import deque
from datetime import datetime, timezone

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "s9_data", "bme")
os.makedirs(OUT, exist_ok=True)
KILL = os.path.join(HERE, "KILL")
WS_CLOB = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_RTDS = "wss://ws-live-data.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
SERIES = {"5m": 300, "15m": 900}
SNAPSHOT_EVERY_S = 10
HASH_CAP = 200_000

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

class Recorder:
    def __init__(self):
        self.day = ""
        self.fh = {}
        self.buf = []                      # buffered event rows
        self.tokens = {}                   # series -> [up, down]
        self.prev_tokens = {}              # series -> previous round's tokens
        self.l2 = {}                       # token -> {"bids":{}, "asks":{}}
        self.last_snap = {}                # token -> ts
        self.hash_stats = {}               # hash -> dict
        self.ring = deque()                # (ts_ms, is_cancel, token) for pre-tick window
        self.counts = {"price_change": 0, "last_trade_price": 0, "book": 0,
                       "best_bid_ask": 0, "tick_size_change": 0, "spot_tick": 0,
                       "twap_tick": 0}
        self.t0 = time.time()

    # ---------- files
    HEADERS = {
        "ev": ["ts_ms", "event_type", "market", "asset", "side", "price", "size",
               "best_bid", "best_ask", "hash16", "extra"],
        "bk": ["ts_ms", "market", "asset_id", "side", "price", "size"],
        "st": ["ts_ms", "series", "token_idx", "token16", "best_bid", "best_ask",
               "mid", "bid_depth_top10", "ask_depth_top10", "n_bid_levels", "n_ask_levels"],
    }

    def _open(self):
        self.day = datetime.now(timezone.utc).strftime("%Y%m%d")
        # 2026-09-21 (twapvm deploy, living record Part #17): events + books are written as
        # STREAMING GZIP instead of raw CSV gzipped at midnight. Measured live: 801 MB/hour raw
        # = ~19 GB per UTC day before the old design's rotation ever ran, on a 19 GB disk.
        # Streaming keeps the peak raw footprint near zero (gzip 11.6x on this data). Each
        # (re)start appends a new gzip member; bme_score.read_csvs() reads multi-member files.
        # bookstate (small) and hashes (rewritten every 5 min) stay raw as before.
        new_ev = not os.path.exists(os.path.join(OUT, f"events_{self.day}.csv.gz"))
        new_bk = not os.path.exists(os.path.join(OUT, f"books_{self.day}.csv.gz"))
        self.fh["ev"] = gzip.open(os.path.join(OUT, f"events_{self.day}.csv.gz"), "at", newline="", compresslevel=6)
        self.fh["bk"] = gzip.open(os.path.join(OUT, f"books_{self.day}.csv.gz"), "at", newline="", compresslevel=6)
        self.fh["st"] = open(os.path.join(OUT, f"bookstate_1s_{self.day}.csv"), "a", newline="")
        self.w_ev = csv.writer(self.fh["ev"])
        self.w_bk = csv.writer(self.fh["bk"])
        self.w_st = csv.writer(self.fh["st"])
        if new_ev: self.w_ev.writerow(self.HEADERS["ev"])
        if new_bk: self.w_bk.writerow(self.HEADERS["bk"])
        if self.fh["st"].tell() == 0:
            self.w_st.writerow(self.HEADERS["st"])

    def rotate_if_needed(self):
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if today != self.day:
            old = self.day
            for k in self.fh: self.fh[k].close()
            self._open()
            log(f"day rollover -> {self.day}; gzipping {old}")
            for pat in (f"events_{old}.csv", f"books_{old}.csv",
                        f"bookstate_1s_{old}.csv", f"hashes_{old}.csv"):
                p = os.path.join(OUT, pat)
                if os.path.exists(p):
                    try:
                        # "ab": if a streamed .gz for that day already exists (events/books),
                        # a leftover raw file is APPENDED as a member, never overwriting it.
                        with open(p, "rb") as fi, gzip.open(p + ".gz", "ab") as fo:
                            fo.writelines(fi)
                        os.remove(p)
                    except Exception as e:
                        log(f"gzip {pat} failed: {e!r}")

    def _gzip_events(self):
        """gzip the current raw events file (day-rollover does this too)."""
        import gzip as _gz, shutil as _sh
        try:
            for k in self.fh: self.fh[k].close()
            p = os.path.join(OUT, f"events_{self.day}.csv")
            if os.path.exists(p) and os.path.getsize(p) > 0:
                with open(p, "rb") as fi, _gz.open(p + ".gz", "ab", compresslevel=6) as fo:
                    _sh.copyfileobj(fi, fo)
                os.remove(p)
                log(f"events_{self.day}.csv -> .gz")
        except Exception as e:
            log(f"gzip-on-exit failed: {e!r}")

    def event(self, *row):
        self.buf.append(row)

    def flush(self):
        self.rotate_if_needed()
        if self.buf:
            self.w_ev.writerows(self.buf)
            self.buf.clear()
        for fh in self.fh.values():
            fh.flush()

    # ---------- round discovery
    def discover(self, series):
        start = int(time.time() // SERIES[series]) * SERIES[series]
        for attempt in range(30):
            try:
                m = http_json(f"{GAMMA}/markets?slug=btc-updown-{series}-{start}")
                if m:
                    return start, json.loads(m[0]["clobTokenIds"])
            except Exception:
                pass
            time.sleep(2)
        return start, None

    # ---------- market-channel events
    def on_clob_msg(self, msg):
        et = msg.get("event_type")
        if et == "price_change":
            for pc in msg.get("price_changes") or []:
                self.counts["price_change"] += 1
                h = pc.get("hash") or ""
                d = self.hash_stats.get(h)
                if d is None:
                    if len(self.hash_stats) > HASH_CAP:
                        self.hash_stats.clear()
                    d = self.hash_stats[h] = {"n": 0, "first": None, "last": None,
                                              "lo": 9.0, "hi": 0.0}
                ts = msg.get("timestamp")
                d["n"] += 1
                d["first"] = d["first"] or ts
                d["last"] = ts
                p = float(pc.get("price", 0))
                d["lo"], d["hi"] = min(d["lo"], p), max(d["hi"], p)
                # maintain L2 + cancel ring
                tok = pc.get("asset_id")
                if tok in self.l2:
                    side = "bids" if pc.get("side") == "BUY" else "asks"
                    book = self.l2[tok][side]
                    old = book.get(p, 0.0)
                    new = float(pc.get("size", 0))
                    book.pop(p, None) if new <= 0 else book.__setitem__(p, new)
                    if new < old:
                        self.ring.append((int(ts), True, tok))
                self.event(ts, "price_change", msg.get("market", ""), tok,
                           pc.get("side"), pc.get("price"), pc.get("size"),
                           pc.get("best_bid", ""), pc.get("best_ask", ""), h[:16], "")
        elif et == "last_trade_price":
            self.counts["last_trade_price"] += 1
            self.event(msg.get("timestamp"), "trade", msg.get("market", ""),
                       msg.get("asset_id"), msg.get("side"), msg.get("price"),
                       msg.get("size"), "", "", "",
                       f"{msg.get('fee_rate_bps','')}|{str(msg.get('transaction_hash',''))[:14]}")
        elif et == "book":
            self.counts["book"] += 1
            tok = msg.get("asset_id")
            if tok in self.l2:
                self.l2[tok] = {"bids": {float(x["price"]): float(x["size"]) for x in msg.get("bids") or []},
                                "asks": {float(x["price"]): float(x["size"]) for x in msg.get("asks") or []}}
                now = time.time()
                if now - self.last_snap.get(tok, 0) >= SNAPSHOT_EVERY_S:
                    self.last_snap[tok] = now
                    ts = msg.get("timestamp")
                    for side, levels in (("BID", msg.get("bids") or []), ("ASK", msg.get("asks") or [])):
                        for x in levels:
                            self.w_bk.writerow([ts, msg.get("market", ""), tok, side,
                                                x.get("price"), x.get("size")])
        elif et == "best_bid_ask":
            self.counts["best_bid_ask"] += 1
            self.event(msg.get("timestamp"), "bba", msg.get("market", ""),
                       msg.get("asset_id"), "", msg.get("best_bid"), "",
                       msg.get("best_bid"), msg.get("best_ask"), "", msg.get("spread", ""))
        elif et == "tick_size_change":
            self.counts["tick_size_change"] += 1
            self.event(msg.get("timestamp"), "tick_size", msg.get("market", ""),
                       msg.get("asset_id"), "", "", "", "", "", "",
                       f"{msg.get('old_tick_size')}->{msg.get('new_tick_size')}")

    # ---------- RTDS ticks (spot + twap fused into the same timeline)
    def on_rtds_tick(self, topic, ts_ms, value):
        if topic == "crypto_prices_chainlink":
            self.counts["spot_tick"] += 1
            # pre-tick pull count: cancels in the 200ms before this tick
            n = 0
            ts_ms = int(ts_ms)
            while self.ring and ts_ms - self.ring[0][0] > 5000:
                self.ring.popleft()
            for r_ts, is_c, _ in self.ring:
                if is_c and 0 <= ts_ms - r_ts <= 200:
                    n += 1
            self.event(ts_ms, "spot_tick", "", "btc/usd", "", f"{value:.2f}", "", "", "", "",
                       f"pretick_pulls_200ms={n}")
        else:
            self.counts["twap_tick"] += 1
            self.event(ts_ms, "twap_tick", "", "btc/usd", "", f"{value:.2f}", "", "", "", "", "")

    # ---------- periodic 1s book state
    def write_bookstate(self):
        now_ms = int(time.time() * 1000)
        for series, toks in self.tokens.items():
            if not toks: continue
            for i, tok in enumerate(toks):
                b = self.l2.get(tok)
                if not b or not b["bids"] or not b["asks"]: continue
                bb, ba = max(b["bids"]), min(b["asks"])
                bids = sorted(b["bids"].items(), reverse=True)[:10]
                asks = sorted(b["asks"].items())[:10]
                self.w_st.writerow([now_ms, series, i, tok[:16], bb, ba,
                                    round((bb + ba) / 2, 4),
                                    round(sum(s for _, s in bids), 2),
                                    round(sum(s for _, s in asks), 2),
                                    len(b["bids"]), len(b["asks"])])

    def dump_hashes(self):
        p = os.path.join(OUT, f"hashes_{self.day}.csv")
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hash", "n_events", "first_ts", "last_ts", "life_s",
                        "lo_price", "hi_price"])
            rows = sorted(self.hash_stats.items(), key=lambda kv: -kv[1]["n"])[:50000]
            for h, d in rows:
                try:
                    life = (int(d["last"]) - int(d["first"])) / 1000.0
                except Exception:
                    life = ""
                w.writerow([h, d["n"], d["first"], d["last"], life, d["lo"], d["hi"]])

async def clob_loop(rec, sub_q, unsub_q):
    backoff = 2
    while True:
        try:
            async with websockets.connect(WS_CLOB, max_size=None) as ws:
                log("market channel connected")
                toks = [t for v in rec.tokens.values() for t in v if t]
                if toks:
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market",
                                              "initial_dump": True, "level": 2,
                                              "custom_feature_enabled": True}))
                async def ping():
                    while True:
                        await asyncio.sleep(10)
                        try: await ws.send("PING")
                        except Exception: return
                pt = asyncio.create_task(ping())
                try:
                    while True:
                        while sub_q:
                            await ws.send(json.dumps({"operation": "subscribe",
                                                      "assets_ids": [sub_q.popleft()]}))
                        while unsub_q:
                            await ws.send(json.dumps({"operation": "unsubscribe",
                                                      "assets_ids": [unsub_q.popleft()]}))
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        if raw == "PONG": continue
                        try:
                            msgs = json.loads(raw) if raw.startswith("[") else [json.loads(raw)]
                        except Exception:
                            continue
                        for m in msgs: rec.on_clob_msg(m)
                finally:
                    pt.cancel()
        except Exception as ex:
            log(f"WS problem {ex!r} — reconnect in {backoff}s")
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)

async def rtds_loop(rec):
    backoff = 2
    while True:
        try:
            async with websockets.connect(WS_RTDS, max_size=None) as ws:
                log("RTDS connected (spot + twap60)")
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                    {"topic": "crypto_prices_chainlink", "type": "update",
                     "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))},
                    {"topic": "crypto_prices_twap_sixty", "type": "update",
                     "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))}]}))
                async def ping():
                    while True:
                        await asyncio.sleep(5)
                        try: await ws.send("PING")
                        except Exception: return
                pt = asyncio.create_task(ping())
                try:
                    async for raw in ws:
                        try: msg = json.loads(raw)
                        except Exception: continue
                        if msg.get("type") != "update": continue
                        p = msg.get("payload", {})
                        if p.get("symbol") != "btc/usd": continue
                        val = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
                        rec.on_rtds_tick(msg.get("topic", ""), p["timestamp"], val)
                finally:
                    pt.cancel()
        except Exception as ex:
            log(f"RTDS problem {ex!r} — reconnect in {backoff}s")
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)

async def scheduler(rec, sub_q, unsub_q, minutes):
    t_end = time.time() + minutes * 60 if minutes else None
    t_state = t_flush = t_hash = 0.0
    last_roll = {}
    while True:
        now = time.time()
        for series, T in SERIES.items():
            start = int(now // T) * T
            if last_roll.get(series) == start: continue
            last_roll[series] = start
            s, toks = rec.discover(series)
            if toks:
                if series in rec.tokens:
                    for t in rec.tokens[series]:
                        if t: unsub_q.append(t)
                rec.tokens[series] = toks
                for t in toks:
                    rec.l2[t] = {"bids": {}, "asks": {}}
                    sub_q.append(t)
                log(f"{series} round {s}: subscribed {toks[0][:14]}…/{toks[1][:14]}…")
        if time.time() - t_state >= 1.0:
            rec.write_bookstate(); t_state = time.time()
        if time.time() - t_flush >= 1.0:
            rec.flush(); t_flush = time.time()
        if time.time() - t_hash >= 300:
            rec.dump_hashes(); t_hash = time.time()
        if t_end and now > t_end:
            rec.flush(); rec.dump_hashes()
            dt = now - rec.t0
            log(f"--minutes elapsed. captured in {dt/60:.1f} min: {rec.counts} "
                f"({rec.counts['price_change']/dt:.0f} price_change/s, "
                f"{len(rec.hash_stats)} distinct order hashes)")
            rec._gzip_events()
            os._exit(0)
        if os.path.exists(KILL) or STOP[0]:
            rec.flush(); rec.dump_hashes()
            log("KILL present — bme_capture exiting" if os.path.exists(KILL) else "SIGTERM — bme_capture exiting cleanly")
            rec._gzip_events()          # closes the gzip streams -> valid trailer on every member
            os._exit(0)
        await asyncio.sleep(1)

# 2026-09-21: orderly shutdown on `systemctl stop` (SIGTERM). Without this the process died
# mid-stream and the current gzip member had no trailer. The scheduler loop sees STOP within 1 s,
# flushes, closes the files and exits 0 (systemd's default stop timeout is 90 s).
STOP = [False]

def _on_sigterm(signum, frame):
    STOP[0] = True

async def main():
    import signal
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0)
    args = ap.parse_args()
    rec = Recorder()
    rec._open()
    sub_q, unsub_q = deque(), deque()
    log(f"bme_capture start — series {list(SERIES)}, snapshots every {SNAPSHOT_EVERY_S}s "
        f"per token, spot+twap fused, 1s bookstate | bounded={args.minutes or 'forever'}")
    await asyncio.gather(clob_loop(rec, sub_q, unsub_q), rtds_loop(rec),
                         scheduler(rec, sub_q, unsub_q, args.minutes))

if __name__ == "__main__":
    asyncio.run(main())
