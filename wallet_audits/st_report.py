#!/usr/bin/env python3
"""st_report.py - aggregate signal_taker_replay day files: gross, fees, net, net + taker rebate, bootstrap CI, days positive.
  python3 st_report.py "st_out/day_*.json" --out st_out/SIGNAL-TAKER-RESULTS.md [--rebate 0.39]"""
import argparse, collections, glob, json, random

ap = argparse.ArgumentParser(); ap.add_argument("days", nargs="+"); ap.add_argument("--out", required=True); ap.add_argument("--rebate", type=float, default=0.39)
a = ap.parse_args()


def boot_ci(vals, n=2000, seed=7):
    if not vals: return (0.0, 0.0)
    rnd = random.Random(seed); m = len(vals); out = []
    for _ in range(n): out.append(sum(vals[rnd.randrange(m)] for _ in range(m)))
    out.sort(); return out[int(0.025 * n)], out[int(0.975 * n)]


files = sorted(set(sum([glob.glob(p) for p in a.days], [])))
agg = collections.defaultdict(lambda: dict(rows=[], days={}))
for fp in files:
    d = json.load(open(fp)); day = fp.split("day_")[-1][:8]
    for v, res in d["variants"].items():
        agg[v]["rows"] += res["rounds"]; agg[v]["days"][day] = sum(r["profit"] for r in res["rounds"])
L = [f"# Stale-ask taker replay - {len(files)} days ({files[0].split('day_')[-1][:8]} .. {files[-1].split('day_')[-1][:8]})", "",
     "Rule: lift a side's best ask whenever the model fair value (chainlink spot vs round open) exceeds it by theta; 20 shares, 2 s cooldown, "
     "200 shares cap per side, order lands 0.1 s later and fills only if the ask is still there. Hold to settlement. Fees 0.07*p*(1-p). "
     f"`lag` = seconds our chainlink feed trails the exchange (0 = as fast as the exchange's takers, 1.44 = our measured feed). Rebate column = {a.rebate:.0%} of fees (what norm1e69 got back).", "",
     "| variant | rounds | fills | money in | gross (before fees) | fees | net after fees | net 95% CI | net + rebate | days positive | worst day |", "|---|---|---|---|---|---|---|---|---|---|---|"]
for v in sorted(agg, key=lambda x: (float(x.split("lag")[1]), float(x.split("theta")[1].split()[0]))):
    R = agg[v]["rows"]; c = sum(r["cost"] for r in R); g = sum(r["gross"] for r in R); f = sum(r["fees"] for r in R); p = sum(r["profit"] for r in R)
    lo, hi = boot_ci([r["profit"] for r in R]); days = agg[v]["days"]; pos = sum(1 for x in days.values() if x > 0)
    L.append(f"| {v} | {len(R):,} | {sum(r['fills'] for r in R):,} | ${c:,.0f} | ${g:,.0f} ({100 * g / c if c else 0:+.2f}%) | ${f:,.0f} | ${p:,.0f} ({100 * p / c if c else 0:+.2f}%) | [{lo:,.0f}, {hi:,.0f}] | ${p + a.rebate * f:,.0f} ({100 * (p + a.rebate * f) / c if c else 0:+.2f}%) | {pos}/{len(days)} | ${min(days.values()) if days else 0:,.0f} |")
L += ["", "Gate: net after fees 95% CI low > 0. " + ("PASS for: " + ", ".join(v for v in agg if boot_ci([r['profit'] for r in agg[v]['rows']])[0] > 0) if any(boot_ci([r['profit'] for r in agg[v]['rows']])[0] > 0 for v in agg) else "No variant passes.")]
open(a.out, "w").write("\n".join(L)); print("\n".join(L))
