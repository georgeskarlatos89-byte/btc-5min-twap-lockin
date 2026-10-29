#!/usr/bin/env python3
"""book_join_report.py - what the order book looked like when the wallet bought (fills_vs_book.py output) + his round P&L.
  python3 book_join_report.py norm1e69/activity.csv norm1e69/book/day_*.json --out norm1e69/book/BOOK-JOIN.md"""
import argparse, collections, csv, glob, json, statistics

ap = argparse.ArgumentParser(); ap.add_argument("activity"); ap.add_argument("days", nargs="+"); ap.add_argument("--out", required=True); a = ap.parse_args()
F = []; R = {}
for fp in sorted(set(sum([glob.glob(p) for p in a.days], []))):
    d = json.load(open(fp)); F += d["fills"]; R.update({int(k): v for k, v in d["rounds"].items()})
# his per-round cash P&L (after fees, feed amounts) for the rounds in R
P = collections.defaultdict(lambda: [0.0, 0.0])
for r in csv.DictReader(open(a.activity)):
    sl = r["slug"] or ""
    if not sl.startswith("btc-updown-5m"): continue
    st = int(sl.rsplit("-", 1)[1])
    if st not in R: continue
    u = float(r["usdcSize"] or 0)
    if r["type"] == "TRADE" and r["side"] == "BUY": P[st][0] += u
    elif r["type"] in ("TRADE", "REDEEM", "MERGE"): P[st][1] += u


def pct(xs, q): xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


buys = [f for f in F if f["side"] == "BUY" and f["ba"] is not None]
L = [f"# Wallet fills vs our recorded book - {len(F):,} BTC 5m fills on {len(R)} rounds ({len(buys):,} buys with a book state)", ""]
ta = sum(1 for f in buys if f["at_ask"]); tb = sum(1 for f in buys if f["at_bid"]); mid = len(buys) - ta - tb
L.append(f"- price at or above the best ask (taker lift): {100 * ta / len(buys):.1f}%; at or below the best bid (resting maker fill): {100 * tb / len(buys):.1f}%; inside the spread: {100 * mid / len(buys):.1f}%")
sa = [f["sum_asks"] for f in buys if f["sum_asks"]]
L.append(f"- sum of the two best asks at the moment he buys: 10th {pct(sa, .1):.3f}, median {pct(sa, .5):.3f}, 90th {pct(sa, .9):.3f}; below 1.00 in {100 * sum(1 for x in sa if x < 1) / len(sa):.1f}%, below 0.97 in {100 * sum(1 for x in sa if x < 0.97) / len(sa):.1f}%, below 0.965 in {100 * sum(1 for x in sa if x < 0.965) / len(sa):.1f}% of his buys")
sb = [f["sum_bids"] for f in buys if f["sum_bids"]]
L.append(f"- sum of the two best bids at his buys: median {pct(sb, .5):.3f} (the book's normal state: asks sum just above 1, bids just below)")
# does he buy the side that just got cheap?  spot move since open vs side bought
up = [f for f in buys if f["outcome"] == "Up" and f["spot_bps"] is not None]; dn = [f for f in buys if f["outcome"] == "Down" and f["spot_bps"] is not None]
if up and dn:
    L.append(f"- Up buys while bitcoin is BELOW the open (Up is the cheap side): {100 * sum(1 for f in up if f['spot_bps'] < 0) / len(up):.1f}% of {len(up):,}; Down buys while bitcoin is ABOVE the open: {100 * sum(1 for f in dn if f['spot_bps'] > 0) / len(dn):.1f}% of {len(dn):,}")
    L.append(f"- his buy price vs the side's own best ask: median gap {statistics.median([f['ba'] - f['price'] for f in buys]) * 100:.1f}c (0 = he lifts the ask)")
# the signal test: for taker lifts, did bitcoin just move toward the side he bought, and was the ask below the model fair value?
tk = [f for f in buys if f["at_ask"] and f["d2"] is not None and f["fair"] is not None]; mk = [f for f in buys if f["at_bid"] and f["d2"] is not None and f["fair"] is not None]
def toward(f): return (f["d2"] > 0) if f["outcome"] == "Up" else (f["d2"] < 0)
def val(f): return (1.0 if f["winner"] == f["outcome"] else 0.0) - f["price"]
for lab, xs in (("taker lifts", tk), ("maker fills at the bid", mk)):
    if not xs: continue
    L.append(f"- {lab} ({len(xs):,}): bitcoin moved TOWARD the side bought in the previous 2 s in {100 * sum(1 for f in xs if toward(f)) / len(xs):.1f}%, away in {100 * sum(1 for f in xs if ((f['d2'] < 0) if f['outcome'] == 'Up' else (f['d2'] > 0))) / len(xs):.1f}%; median |move| {statistics.median([abs(f['d2']) for f in xs]):.1f} bps; model fair minus his price: median {100 * statistics.median([f['fair'] - f['price'] for f in xs]):+.1f}c; value at settlement per share {100 * sum(val(f) * f['size'] for f in xs) / sum(f['size'] for f in xs):+.2f}c")
L.append(""); L.append("| taker lifts: model fair minus ask | fills | shares | value at settlement per share | taker fee per share |"); L.append("|---|---|---|---|---|")
for lo, hi, lab in ((-9, -0.05, "below -5c (he overpays)"), (-0.05, 0, "-5c..0"), (0, 0.02, "0..2c"), (0.02, 0.05, "2..5c"), (0.05, 9, "above 5c (ask is stale/cheap)")):
    xs = [f for f in tk if lo <= f["fair"] - f["price"] < hi]
    if xs:
        sh = sum(f["size"] for f in xs)
        L.append(f"| {lab} | {len(xs):,} | {sh:,.0f} | {100 * sum(val(f) * f['size'] for f in xs) / sh:+.2f}c | {100 * sum(0.07 * f['price'] * (1 - f['price']) * f['size'] for f in xs) / sh:.2f}c |")
L.append(""); L.append("| price he paid | buys | share at ask | median sum of asks then | median seconds into round |"); L.append("|---|---|---|---|---|")
for lo, hi in ((0, .2), (.2, .4), (.4, .5), (.5, .6), (.6, .8), (.8, 1.01)):
    xs = [f for f in buys if lo <= f["price"] < hi]
    if xs: L.append(f"| {lo:.2f}-{hi:.2f} | {len(xs):,} | {100 * sum(1 for f in xs if f['at_ask']) / len(xs):.0f}% | {pct([f['sum_asks'] for f in xs if f['sum_asks']], .5):.3f} | {pct([f['e'] for f in xs], .5):.0f} |")
L.append(""); L.append("## The pair-arbitrage opportunity in the book itself (every second of every round, his trading or not)"); L.append("")
secs = sum(v["secs"] for v in R.values())
for k, lab in (("sec_lt100", "below 1.00"), ("sec_lt97", "below 0.97"), ("sec_lt965", "below 0.965 (covers both taker fees at 50c)")):
    n = sum(v[k] for v in R.values()); L.append(f"- seconds with Up ask + Down ask {lab}: {n:,} of {secs:,} ({100 * n / secs:.2f}%), in {sum(1 for v in R.values() if v[k] > 0)} of {len(R)} rounds")
ms = [v["min_sum"] for v in R.values() if v["min_sum"] < 9]
L.append(f"- lowest sum of asks seen per round: median {pct(ms, .5):.3f}, 10th pct {pct(ms, .1):.3f}; depth at that moment (smaller of the two ask sizes): median {pct([v['depth_at_min'] for v in R.values() if v['min_sum'] < 9], .5):.0f} shares")
L.append(""); L.append("## His round results vs the arbitrage seconds in that round"); L.append("")
L.append("| seconds with ask sum < 1.00 | rounds | his money in | his profit (after fees) | return |"); L.append("|---|---|---|---|---|")
for lo, hi, lab in ((0, 1, "0"), (1, 10, "1-9"), (10, 60, "10-59"), (60, 10 ** 9, "60+")):
    xs = [st for st, v in R.items() if lo <= v["sec_lt100"] < hi and st in P]
    if xs:
        o = sum(P[st][0] for st in xs); i = sum(P[st][1] for st in xs)
        L.append(f"| {lab} | {len(xs)} | ${o:,.0f} | ${i - o:,.0f} | {100 * (i - o) / o if o else 0:+.2f}% |")
tot_o = sum(v[0] for v in P.values()); tot_i = sum(v[1] for v in P.values())
L.append(""); L.append(f"All {len(P)} rounds he traded on these days: money in ${tot_o:,.0f}, profit after fees ${tot_i - tot_o:,.0f} ({100 * (tot_i - tot_o) / tot_o if tot_o else 0:+.2f}%), rebates not included.")
open(a.out, "w").write("\n".join(L)); print("\n".join(L))
