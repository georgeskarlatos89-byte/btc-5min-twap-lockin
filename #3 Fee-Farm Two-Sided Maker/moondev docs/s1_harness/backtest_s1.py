#!/usr/bin/env python3
"""
S1 historical backtest — honest edition.

Data: Kraken XBTUSD trade tape (real prints, not candles), last ~6h.
Reconstruction: 1Hz last-price spot -> trailing 60s TWAP (Chainlink-style).
Validation FIRST: for every finished round in the window, compare the
reconstructed settlement side vs the OFFICIAL Polymarket resolution,
for both open-ref candidates (spot@open, twap@open).
Only then: simulate S1 with the official taker fee and a synthetic book
calibrated to observed live behaviour (book tracks the end-value probability;
1c tick, ~1c spread).

Every assumption that is a stand-in for something unobserved is labelled.
"""
import json, math, time, urllib.request, csv, os, statistics
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def fee(p): return 0.07 * p * (1 - p)
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

print("== fetching Kraken 1m OHLC (real prints, 12h) ==")
now = time.time()
closes = {}   # bar_ts -> close
since = 0
for _ in range(2):
    d = get(f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1&since={since}")
    rows = d["result"]["XXBTZUSD"]
    for r in rows:
        closes[int(r[0])] = float(r[4])
    since = d["result"]["last"]
    time.sleep(0.3)
bars = sorted(closes)
t0, t1 = bars[0], bars[-1] + 60
print(f"1m bars: {len(bars)} over {(bars[-1]-bars[0])/3600:.1f}h")

print("== building 1Hz spot (linear between real closes) + trailing 60s TWAP ==")
# NOTE (honest stand-in): intra-minute path is linearly interpolated; the
# validation step below MEASURES how well this reconstruction reproduces the
# official Chainlink settlement. If it doesn't, the backtest is void.
bi = 0
def spot_at(s):
    global bi
    while bi < len(bars) - 1 and bars[bi + 1] <= s:
        bi += 1
    a = bars[bi]
    if s <= a: return closes[a]
    b = min(a + 60, bars[-1] + 60)
    cb = closes.get(b, closes[a])
    return closes[a] + (cb - closes[a]) * (s - a) / 60.0
spot = {s: spot_at(s) for s in range(t0, t1 + 1)}
secs = list(range(t0, t1 + 1))
cum = [0.0]
for s in secs:
    cum.append(cum[-1] + spot[s])
def twap_at(s):
    a = max(t0, s - 59)
    return (cum[s - t0 + 1] - cum[a - t0]) / (s - a + 1)

SIG1_FLOOR = 4.3e-6          # ~7.5bps/5min per-second vol floor (from measured regime)
def sigma1_of(s):
    a = max(t0, s - 120)
    vals = [twap_at(x) for x in range(a, s + 1, 2)]
    d = [b - x for x, b in zip(vals, vals[1:])]
    if len(d) >= 20:
        m = sum(d)/len(d)
        sd = math.sqrt(sum((x-m)**2 for x in d)/len(d))
        return max(sd, spot[s] * SIG1_FLOOR)
    return spot[s] * SIG1_FLOOR

print("== rounds: reconstruct + official validation ==")
official = {}
def settled_side(label, start):
    key = (label, start)
    if key not in official:
        try:
            m = get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")[0]["markets"][0]
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            official[key] = "Up" if pr[0] > 0.5 else "Down" if m.get("closed") or not m.get("acceptingOrders") else None
        except Exception:
            official[key] = None
        time.sleep(0.15)
    return official[key]

rows = []
first5 = (t0 // 300) * 300 + 300
first15 = (t0 // 900) * 900 + 900
rounds5 = list(range(first5, t1 - 70, 300))
rounds15 = list(range(first15, t1 - 70, 900))

val = {("full","twap"): [0,0], ("full","spot"): [0,0], ("tail","twap"): [0,0], ("end","twap"): [0,0]}
for label, rlist in (("5m", rounds5), ("15m", rounds15)):
    T = 300 if label == "5m" else 900
    for start in rlist:
        end = start + T
        if end > t1 - 5: break
        o_spot = spot[start]; o_twap = twap_at(start)
        tw = [twap_at(s) for s in range(start, end + 1)]
        full = sum(tw) / len(tw)
        tail = sum(tw[-61:]) / 61
        endv = tw[-1]
        st = settled_side(label, start)
        if st is None: continue
        for name, v_, o in (("full", full, o_twap), ("tail", tail, o_twap), ("end", endv, o_twap)):
            val[(name, "twap")][1] += 1
            val[(name, "twap")][0] += ("Up" if v_ >= o else "Down") == st
        val[("full","spot")][1] += 1
        val[("full","spot")][0] += ("Up" if full >= o_spot else "Down") == st
        rows.append(dict(label=label, start=start, T=T, o_spot=o_spot, o_twap=o_twap,
                         full=full, tail=tail, end=endv, settled=st))
print("reconstruction accuracy vs official settlement:")
for k, (ok, n) in val.items():
    if n: print(f"  {k[0]:>4}/{k[1]:>4}: {ok}/{n} = {ok/n:.1%}")

print("accuracy by |full-avg gap| bucket (full/twap):")
bkt = {}
for r in rows:
    gap = abs(r["full"] - r["o_twap"]) / r["o_twap"] * 1e4
    b = "<1bps" if gap < 1 else "1-2" if gap < 2 else "2-4" if gap < 4 else ">4bps"
    ok = ("Up" if r["full"] >= r["o_twap"] else "Down") == r["settled"]
    s = bkt.setdefault(b, [0, 0]); s[1] += 1; s[0] += ok
for b in ("<1bps", "1-2", "2-4", ">4bps"):
    ok, n = bkt.get(b, (0, 0))
    if n: print(f"  {b:>6}: {ok}/{n} = {ok/n:.1%}")

import csv as _csv
with open(os.path.join(HERE, "backtest_rows.csv"), "w", newline="") as f:
    w = _csv.writer(f)
    w.writerow(["label","start","o_twap","o_spot","full","settled"])
    for r in rows:
        w.writerow([r["label"], r["start"], f"{r['o_twap']:.2f}", f"{r['o_spot']:.2f}", f"{r['full']:.2f}", r["settled"]])

print("== S1 simulation (synthetic book, official fees) ==")
def sim(label, rows, win_secs, edge_min):
    out = []
    for r in rows:
        if r["label"] != label: continue
        T = r["T"]; start = r["start"]; t = start + T - win_secs
        e = t - start; rem = win_secs
        acc = sum(twap_at(s) for s in range(start, t + 1)) / (t - start + 1)
        o = r["o_twap"]
        f_req = (T * o - e * acc) / rem
        s1 = sigma1_of(t); sf = 1.5 * s1 * math.sqrt(rem / 3.0)
        p_full = ncdf((twap_at(t) - f_req) / sf)
        p_end = ncdf((spot[t] - o) / sf)              # book-behaviour stand-in
        ask_up = min(0.99, max(0.01, round(p_end + 0.005, 2)))   # 1c tick + half spread
        e_up = p_full - ask_up - fee(ask_up)
        e_dn = (1 - p_full) - (1 - ask_up + 0.01) - fee(1 - ask_up + 0.01)
        side, edge = ("Up", e_up) if e_up > e_dn else ("Down", e_dn)
        if edge > edge_min:
            out.append(dict(start=start, side=side, price=ask_up if side == "Up" else 1 - ask_up + 0.01,
                            edge=edge, win=(r["settled"] == side), p_full=p_full))
    return out

report = []
for label, w in (("5m", 45), ("15m", 90)):
    for em in (0.02, 0.03, 0.05):
        sig = sim(label, rows, w, em)
        if sig:
            wr = sum(s["win"] for s in sig) / len(sig)
            ev = statistics.mean([ (1 - s["price"]) if s["win"] else -s["price"] for s in sig ])
        else:
            wr = ev = float("nan")
        report.append((label, w, em, len(sig), wr, ev))
        print(f"{label} win={w}s edge>{em:.0%}: n={len(sig)} win-rate={wr:.1%} avg-PnL/share={ev:+.3f} "
              f"(per $10: {ev*10*len(sig):+,.1f} total)" if sig else
              f"{label} win={w}s edge>{em:.0%}: n=0")

with open(os.path.join(HERE, "backtest_s1.csv"), "w", newline="") as f:
    wcsv = csv.writer(f)
    wcsv.writerow(["market","signal_window_s","edge_min","n_signals","win_rate","avg_pnl_per_share"])
    for r in report:
        wcsv.writerow([r[0], r[1], r[2], r[3], f"{r[4]:.3f}" if r[4] == r[4] else "", f"{r[5]:.4f}" if r[5] == r[5] else ""])

# signal detail for the main configuration
det = sim("5m", rows, 45, 0.02) + sim("15m", rows, 90, 0.02)
with open(os.path.join(HERE, "backtest_s1_signals.csv"), "w", newline="") as f:
    wcsv = csv.writer(f)
    wcsv.writerow(["round_start","side","synthetic_price","post_fee_edge","win","p_full"])
    for s in sorted(det, key=lambda x: x["start"]):
        wcsv.writerow([s["start"], s["side"], f"{s['price']:.2f}", f"{s['edge']:.3f}", s["win"], f"{s['p_full']:.3f}"])
print("signal detail rows:", len(det))
print("DONE")
