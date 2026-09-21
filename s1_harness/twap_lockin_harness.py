#!/usr/bin/env python3
"""
S1 — TWAP LOCK-IN HARNESS v2 (DRY RUN, never places orders)

Streams (no credentials, via Polymarket RTDS):
  * crypto_prices_twap_sixty   btc/usd  -> the Chainlink 60s TWAP (settlement feed)
  * crypto_prices_twap_thirty  btc/usd  -> kept alive for feed health
  * crypto_prices_chainlink    btc/usd  -> Chainlink SPOT (candidate open reference)

Per round it reconstructs the settlement value and, after the bell, compares
SIX hypotheses against the official resolution:
    open ref in {twap-at-open, spot-at-open}  x  settle in {full-round avg,
    trailing-60s avg, end value}
Rounds where the harness was not alive at the bell are skipped (open ref unknown).
It also runs the live S1 model (full-round-avg semantics) against the CLOB book
with the official taker fee, and logs DRY-RUN signals in the last 45s/90s.
"""
import asyncio, json, math, time, urllib.request, csv, os
from collections import deque
from datetime import datetime, timezone

import websockets

RTDS = "wss://ws-live-data.polymarket.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "rounds.csv")
S2_PATH = os.path.join(HERE, "s2_stats.json")
SYMBOL = "btc/usd"
ROUNDS = [(300, "5m", 45), (900, "15m", 90)]
EDGE_MIN = 0.02
GAP_MIN_BPS = 4.0   # backtest-calibrated: |gap|>4bps = 70/70 vs official; below = proxy noise
SIGMA_SAFETY = 1.5

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}Z] {msg}", flush=True)

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def fee(p): return 0.07 * p * (1 - p)
def norm_cdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

class State:
    def __init__(self):
        self.holding = None; self.holding_since = None      # 60s TWAP
        self.spot = None; self.spot_since = None            # chainlink spot
        self.samples = deque(maxlen=1500)
        self.sigma1 = None
        self.rounds = {}
        self.pending_res = []
        self.n_upd = 0

state = State()
_new_csv = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
csv_f = open(CSV_PATH, "a", newline="")
csv_w = csv.writer(csv_f)
if _new_csv:
    csv_w.writerow(["market","round_start","open_ref_twap","open_ref_spot","full_round_avg",
                    "tail60_avg","end_value","settled","fa_twap","t60_twap","ev_twap",
                    "fa_spot","t60_spot","ev_spot","coverage","model_p","book_ask",
                    "signal_side","signal_edge"])

def round_state(T, label, start):
    key = (T, label, start)
    if key not in state.rounds:
        state.rounds[key] = dict(o_twap=None, o_spot=None, acc=0.0, elapsed=0.0,
                                 last_ts=None, book=None, last_model=None, sig=None,
                                 done=False, d10=None, d15=None)
    return state.rounds[key]

def snapshot_open_refs(T, label, start, now):
    """call exactly when a round's bell rings (or within 3s of it)"""
    rs = round_state(T, label, start)
    if rs["o_twap"] is None and state.holding is not None and (now - start) <= 3:
        rs["o_twap"] = state.holding
        rs["o_spot"] = state.spot
        log(f"{label} round {start}: open refs snapped  twap={rs['o_twap']:.2f} spot={rs['o_spot']:.2f}")

def on_twap_update(obs_ts, value):
    prev_val, prev_ts = state.holding, state.holding_since
    state.holding, state.holding_since = value, obs_ts
    state.samples.append((obs_ts, value))
    now = time.time()
    for (T, label, win) in ROUNDS:
        start = int(now // T) * T
        rs = round_state(T, label, start)
        # 2026-09-21 fix (plumbing, same semantics): snapshot the open HERE, on the feed
        # event, so it cannot be missed while the ticker is blocked in settle_check()'s
        # up-to-5-min resolution polling or a slow book HTTP call (that cost 1 round in 7:
        # "no open-ref snapshot (late join) - row skipped"). snapshot_open_refs() in the
        # ticker stays as a fallback and is a no-op once o_twap is set.
        snapshot_open_refs(T, label, start, now)
        if rs["last_ts"] is None:
            rs["last_ts"] = max(prev_ts or obs_ts, start)
        t0 = max(rs["last_ts"], start); t1 = min(obs_ts, start + T)
        if t1 > t0 and prev_val is not None:
            rs["acc"] += prev_val * (t1 - t0); rs["elapsed"] += (t1 - t0)
        rs["last_ts"] = obs_ts

def stream_sigma():
    vals = [v for _, v in list(state.samples)[-120:]]
    if len(vals) >= 30:
        d = [b - a for a, b in zip(vals, vals[1:])]
        m = sum(d)/len(d); var = sum((x-m)**2 for x in d)/len(d)
        state.sigma1 = math.sqrt(max(var, 1e-12))
    if state.sigma1 is None: state.sigma1 = (state.holding or 80000) * 4.3e-6  # ~7.5bps/5min floor
    return state.sigma1

def model_for(T, rs):
    o = rs["o_twap"] if rs["o_twap"] is not None else rs["o_spot"]
    if o is None or rs["elapsed"] <= 0: return None
    e = min(rs["elapsed"], T); r = T - e
    acc_avg = rs["acc"] / e
    gap_bps = (acc_avg - o) / o * 1e4
    if r <= 1:
        return dict(p=1.0 if acc_avg >= o else 0.0, p_end=1.0 if acc_avg >= o else 0.0,
                    acc_avg=acc_avg, gap_bps=gap_bps, r=0)
    f_req = (T * o - e * acc_avg) / r
    sigma_f = SIGMA_SAFETY * stream_sigma() * math.sqrt(r / 3.0)
    p = norm_cdf((state.holding - f_req) / sigma_f)
    p_end = norm_cdf((state.holding - o) / sigma_f)
    return dict(p=p, p_end=p_end, acc_avg=acc_avg, gap_bps=gap_bps, r=r)

async def poll_book(label, start, rs):
    try:
        if "tokens" not in rs:
            ev = http_json(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-{label}-{start}")
            rs["tokens"] = json.loads(ev[0]["clobTokenIds"])
        book = http_json(f"https://clob.polymarket.com/book?token_id={rs['tokens'][0]}")
        bb = max(float(x["price"]) for x in book["bids"]) if book["bids"] else 0.0
        ba = min(float(x["price"]) for x in book["asks"]) if book["asks"] else 1.0
        rs["book"] = (bb, ba)
    except Exception:
        rs["book"] = None

async def settle_check(T, label, start):
    rs = state.rounds.get((T, label, start))
    if not rs or rs["o_twap"] is None:
        log(f"{label} {start}: no open-ref snapshot (late join) — row skipped")
        return
    settled = None
    for _ in range(10):
        try:
            m = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")[0]["markets"][0]
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            if m.get("closed") or not m.get("acceptingOrders"):
                settled = "Up" if pr[0] > 0.5 else "Down"; break
            if pr[0] >= 0.9995 or pr[0] <= 0.0005:
                settled = "Up" if pr[0] >= 0.9995 else "Down"; break
        except Exception:
            pass
        await asyncio.sleep(30)
    if settled is None:
        log(f"{label} {start}: resolution not final yet, skipping row"); return
    # S2 (Open-Print Displacement) live evidence: does the t+10/15s displacement sign predict outcome?
    d10, d15 = rs.get("d10"), rs.get("d15")
    if d10 is not None or d15 is not None:
        s2 = json.load(open(S2_PATH)) if os.path.exists(S2_PATH) else {}
        parts = []
        for th, d in ((10, d10), (15, d15)):
            if d is None: continue
            hit = ("Up" if d >= 0 else "Down") == settled
            s2[f"n{th}"] = s2.get(f"n{th}", 0) + 1
            s2[f"h{th}"] = s2.get(f"h{th}", 0) + (1 if hit else 0)
            parts.append(f"d{th}={d:+.1f}bps hit={'yes' if hit else 'NO'}")
        json.dump(s2, open(S2_PATH, "w"))
        log(f"S2-EVAL {label} {start}: {' '.join(parts)} | settled={settled} | "
            f"tally10={s2.get('h10',0)}/{s2.get('n10',0)} tally15={s2.get('h15',0)}/{s2.get('n15',0)}")
    end = start + T
    o_t, o_s = rs["o_twap"], rs["o_spot"]
    acc_avg = rs["acc"] / rs["elapsed"] if rs["elapsed"] else float("nan")
    tail = [v for ts, v in state.samples if end - 60 <= ts <= end + 2]
    t60 = sum(tail)/len(tail) if tail else float("nan")
    evl = [v for ts, v in state.samples if ts <= end + 2]
    ev = evl[-1] if evl else float("nan")
    cov = rs["elapsed"] / T
    def s(x, o): return "Up" if x >= o else "Down"
    def yn(x, o): return "yes" if s(x, o) == settled else "NO"
    row = [label, start, f"{o_t:.2f}", f"{o_s:.2f}" if o_s else "", f"{acc_avg:.2f}",
           f"{t60:.2f}", f"{ev:.2f}", settled,
           yn(acc_avg, o_t) if cov >= 0.8 else f"partial({cov:.0%})", yn(t60, o_t), yn(ev, o_t),
           yn(acc_avg, o_s) if (cov >= 0.8 and o_s) else "", yn(t60, o_s) if o_s else "", yn(ev, o_s) if o_s else "",
           f"{cov:.0%}", f"{(rs['last_model'] or {}).get('p','')}", (rs['book'] or [None,None])[1],
           (rs['sig'] or {}).get('side',''), (rs['sig'] or {}).get('edge','')]
    csv_w.writerow(row); csv_f.flush()
    log(f"ROUND-RESULT {label} {start}: settled={settled} cov={cov:.0%} | vs twap-open: "
        f"full={s(acc_avg,o_t)}/{yn(acc_avg,o_t)} tail60={s(t60,o_t)}/{yn(t60,o_t)} end={s(ev,o_t)}/{yn(ev,o_t)}"
        + (f" | vs spot-open: full={s(acc_avg,o_s)}/{yn(acc_avg,o_s)} tail60={s(t60,o_s)}/{yn(t60,o_s)} end={s(ev,o_s)}/{yn(ev,o_s)}" if o_s else ""))

async def rtds():
    backoff = 2
    while True:
        try:
            async with websockets.connect(RTDS, max_size=None) as ws:
                log("RTDS connected; subscribing twap30+twap60+chainlink-spot")
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                    {"topic": "crypto_prices_twap_sixty", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))},
                    {"topic": "crypto_prices_twap_thirty", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))},
                    {"topic": "crypto_prices_chainlink", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))}]}))
                backoff = 2
                async def heartbeat():
                    while True:
                        await asyncio.sleep(5); await ws.send("PING")
                hb = asyncio.create_task(heartbeat())
                seen_nonjson = False
                try:
                    async for raw in ws:
                        try: msg = json.loads(raw)
                        except Exception:
                            if not seen_nonjson: log(f"note: skipping non-JSON frame {str(raw)[:40]!r}"); seen_nonjson = True
                            continue
                        if msg.get("type") != "update": continue
                        p = msg.get("payload", {})
                        if p.get("symbol") != SYMBOL: continue
                        val = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
                        topic = msg.get("topic", "")
                        if topic == "crypto_prices_chainlink":
                            state.spot, state.spot_since = val, p["timestamp"]/1000.0
                        elif topic == "crypto_prices_twap_sixty":
                            on_twap_update(p["timestamp"]/1000.0, val)
                            state.n_upd += 1
                            if state.n_upd <= 3: log(f"stream update #{state.n_upd}: twap60={val:.2f}")
                finally:
                    hb.cancel()
        except Exception as ex:
            log(f"RTDS problem: {ex!r} — reconnect in {backoff}s"); await asyncio.sleep(backoff); backoff = min(backoff*2, 30)

async def ticker():
    last_roll = {}
    while True:
        now = time.time()
        for (T, label, win) in ROUNDS:
            start = int(now // T) * T
            rs = round_state(T, label, start)
            if last_roll.get((T, label)) is None:
                last_roll[(T, label)] = start
                snapshot_open_refs(T, label, start, now)   # only binds if within 3s of bell
            elif last_roll[(T, label)] != start:
                prev = last_roll[(T, label)]; last_roll[(T, label)] = start
                prs = state.rounds.get((T, label, prev))
                if prs and not prs["done"]:
                    prs["done"] = True; state.pending_res.append((now + 45, T, label, prev))
                snapshot_open_refs(T, label, start, now)
            # S2 live evidence: spot displacement at t+10s / t+15s vs open ref
            if rs["o_twap"] is not None and state.spot is not None:
                age = now - start
                for th in (10, 15):
                    if rs[f"d{th}"] is None and th <= age < th + 2:
                        rs[f"d{th}"] = (state.spot - rs["o_twap"]) / rs["o_twap"] * 1e4
            m = model_for(T, rs)
            if m:
                rs["last_model"] = dict(p=m["p"])
                await poll_book(label, start, rs)
                if rs["book"]:
                    bb, ba = rs["book"]
                    e_up = m["p"] - ba - fee(ba)
                    e_dn = (1 - m["p"]) - (1 - bb) - fee(1 - bb)
                    if m["r"] <= win and max(e_up, e_dn) > EDGE_MIN and abs(m["gap_bps"]) >= GAP_MIN_BPS:
                        side = "Up" if e_up > e_dn else "Down"
                        rs["sig"] = dict(side=side, edge=f"{max(e_up,e_dn):.3f}")
                        log(f"⚡ SIGNAL {label} t-{int(m['r'])}s: would BUY {side} @ "
                            f"{ba if side=='Up' else 1-bb:.2f} | model p(Up)={m['p']:.3f} | "
                            f"post-fee edge {max(e_up,e_dn):.1%}  (gap {m['gap_bps']:+.1f}bps, DRY RUN)")
                if int(m["r"]) % 15 == 0 and m["r"] <= 120:
                    b = f"book Up {rs['book'][0]:.2f}/{rs['book'][1]:.2f}" if rs["book"] else "book n/a"
                    log(f"{label} t-{int(m['r'])}s: gap {m['gap_bps']:+.1f}bps | "
                        f"p(full-avg)={m['p']:.3f} p(end-value)={m['p_end']:.3f} | {b}")
        due = [x for x in state.pending_res if x[0] <= now]
        state.pending_res = [x for x in state.pending_res if x[0] > now]
        for _, T, label, start in due:
            await settle_check(T, label, start)
        await asyncio.sleep(1)

async def main():
    log("S1 TWAP lock-in harness v2 starting (DRY RUN — observation only)")
    await asyncio.gather(rtds(), ticker())

if __name__ == "__main__":
    asyncio.run(main())
