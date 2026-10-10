#!/usr/bin/env python3
"""
book_recorder.py (v2) - book + fair-value recorder, no credentials

Every second, for the live BTC 5m and 15m rounds, records Chainlink TWAP60 + spot (RTDS),
the v2 fair model (fair_p = P(Up) under the real settlement rule, TWAP60 end vs start) and
the CLOB top-of-book of the Up token (best bid/ask + sizes, top-5 ladder).

Output: book_data/YYYY-MM-DD.jsonl, one JSON object per second per round, replayable by
maker_sim.py --replay. Public endpoints only.

v2 fixes: market lookup via gamma `events?slug=` (the `markets?slug=` form times out at the
round boundary and lists only live rounds), failures are retried instead of cached for the
round, the fair model is the shared fair_model.py, the heartbeat task is cancelled on
reconnect, a heartbeat line is logged every 60 s.
"""
import asyncio, json, os, time, urllib.request
from datetime import datetime, timezone

import websockets
from fair_model import FairModel

RTDS = "wss://ws-live-data.polymarket.com"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "book_data")
SYMBOL = "btc/usd"
ROUNDS = [(300, "5m"), (900, "15m")]


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)


def http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class State:
    def __init__(self):
        self.model = FairModel()
        self.rounds = {}
        self.tokens = {}          # (label, start) -> (clobTokenIds, conditionId)
        self.token_fail_ts = {}   # (label, start) -> last failed lookup time
        self.last_feed = time.time()
        self.msgs = 0


state = State()
WS_HOLDER = [None]


def round_state(T, label, start):
    key = (T, label, start)
    if key not in state.rounds:
        state.rounds[key] = dict(o_twap=None, o_spot=None, book=None)
    return state.rounds[key]


def snap_open_refs(now):
    """TWAP60 at the round start = the open reference; accepted within 3 s of the boundary."""
    for (T, label) in ROUNDS:
        start = int(now // T) * T
        rs = round_state(T, label, start)
        if rs["o_twap"] is None and state.model.twap is not None and (now - start) <= 3:
            rs["o_twap"] = state.model.twap
            rs["o_spot"] = state.model.spot[-1][1] if state.model.spot else None
            log(f"{label} {start}: open refs snapped twap={rs['o_twap']:.2f}")


def fair_for(T, label, start, rs, now):
    o = rs["o_twap"]
    if o is None:
        return None
    t_rem = start + T - now
    p = state.model.p_up(o, t_rem, now)
    if p is None:
        return None
    tw = state.model.twap
    gap = (tw - o) / o * 1e4 if tw else None
    return dict(p=p, gap_bps=gap, r=t_rem, sigma1=state.model._sigma)


def get_tokens(label, start):
    """(clobTokenIds, conditionId) or None. Uses events?slug=; retries, never caches a failure."""
    key = (label, start)
    if key in state.tokens:
        return state.tokens[key]
    if time.time() - state.token_fail_ts.get(key, 0) < 2.0:
        return None
    for _ in range(2):
        try:
            ev = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")
            m = ev[0]["markets"][0]
            state.tokens[key] = (json.loads(m["clobTokenIds"]), m.get("conditionId"))
            return state.tokens[key]
        except Exception:
            time.sleep(0.3)
    state.token_fail_ts[key] = time.time()
    return None


def poll_book(rs, label, start):
    tok = get_tokens(label, start)
    if not tok:
        rs["book"] = None; return
    try:
        book = http_json(f"https://clob.polymarket.com/book?token_id={tok[0][0]}")
        bids = sorted(((float(b["price"]), float(b["size"])) for b in book["bids"]), reverse=True)[:5]
        asks = sorted(((float(a["price"]), float(a["size"])) for a in book["asks"]))[:5]
        rs["book"] = dict(bids=bids, asks=asks, bb=bids[0][0] if bids else None,
                          ba=asks[0][0] if asks else None, tick=book.get("tick_size"))
    except Exception:
        rs["book"] = None


def out_path():
    return os.path.join(DATA_DIR, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")


async def record_loop():
    os.makedirs(DATA_DIR, exist_ok=True)
    n = 0
    while True:
        now = time.time()
        try:
            snap_open_refs(now)
            with open(out_path(), "a") as f:
                for (T, label) in ROUNDS:
                    start = int(now // T) * T
                    rs = round_state(T, label, start)
                    if rs["o_twap"] is None:
                        continue
                    poll_book(rs, label, start)
                    md = fair_for(T, label, start, rs, now)
                    b = rs["book"] or {}
                    bb, ba = b.get("bb"), b.get("ba")
                    tok = state.tokens.get((label, start))
                    rec = dict(ts=round(now, 2), label=label, start=start,
                               t_el=round(now - start, 1), t_rem=round(start + T - now, 1),
                               twap=state.model.twap, spot=state.model.spot[-1][1] if state.model.spot else None,
                               o_twap=rs["o_twap"],
                               gap_bps=round(md["gap_bps"], 2) if md and md["gap_bps"] is not None else None,
                               fair_p=round(md["p"], 4) if md else None,
                               sigma1=round(md["sigma1"], 4) if md and md["sigma1"] else None,
                               bb=bb, ba=ba,
                               bb_sz=b["bids"][0][1] if b.get("bids") else None,
                               ba_sz=b["asks"][0][1] if b.get("asks") else None,
                               mid=round((bb + ba) / 2, 4) if (bb is not None and ba is not None) else None,
                               spread=round(ba - bb, 4) if (bb is not None and ba is not None) else None,
                               tick=b.get("tick"),
                               bids=b.get("bids"), asks=b.get("asks"),
                               condition_id=tok[1] if tok else None)
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            n += 1
            if n % 60 == 0:
                s = state.model.snapshot()
                log(f"HEARTBEAT {n}s twap={s['twap'] and round(s['twap'], 1)} spot={s['spot'] and round(s['spot'], 1)} "
                    f"sigma1={s['sigma1'] and round(s['sigma1'], 3)} feed_age={time.time() - state.last_feed:.0f}s msgs={state.msgs}")
        except Exception as e:
            log(f"WARN record tick failed: {e}")
        await asyncio.sleep(1.0)


async def rtds():
    backoff = 2
    while True:
        hbt = None
        try:
            async with websockets.connect(RTDS, max_size=None) as ws:
                WS_HOLDER[0] = ws
                log("RTDS connected")
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                    {"topic": "crypto_prices_twap_sixty", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))},
                    {"topic": "crypto_prices_chainlink", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))}]}))
                backoff = 2

                async def hb():
                    while True:
                        await asyncio.sleep(5)
                        await ws.send("PING")
                hbt = asyncio.create_task(hb())
                async for raw in ws:
                    state.last_feed = time.time()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("type") != "update":
                        continue
                    p = msg.get("payload", {})
                    if p.get("symbol") != SYMBOL:
                        continue
                    try:
                        val = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
                    except Exception:
                        continue
                    obs_ts = p.get("timestamp", time.time() * 1000) / 1000.0
                    topic = msg.get("topic", "")
                    state.msgs += 1
                    if topic == "crypto_prices_twap_sixty":
                        state.model.on_twap(obs_ts, val)
                        snap_open_refs(time.time())
                    elif topic == "crypto_prices_chainlink":
                        state.model.on_spot(obs_ts, val)
        except Exception as e:
            log(f"RTDS error: {e}; reconnect in {backoff}s")
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)
        finally:
            if hbt:
                hbt.cancel()
            WS_HOLDER[0] = None


async def watchdog():
    while True:
        await asyncio.sleep(15)
        staleness = time.time() - state.last_feed
        if staleness > 45 and WS_HOLDER[0] is not None:
            log(f"FEED-STALE {staleness:.0f}s - forcing RTDS reconnect")
            try:
                await WS_HOLDER[0].close()
            except Exception:
                pass
            state.last_feed = time.time()


def main():
    log(f"book_recorder v2 starting -> {DATA_DIR}/YYYY-MM-DD.jsonl")
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.create_task(rtds()); loop.create_task(record_loop()); loop.create_task(watchdog())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
