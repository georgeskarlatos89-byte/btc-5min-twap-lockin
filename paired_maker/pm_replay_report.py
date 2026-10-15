#!/usr/bin/env python3
"""pm_replay_report.py - aggregate pm_replay day files into one table with bootstrap CIs and the step-3 gate.

  python3 pm_replay_report.py --days replay_out/day_*.json --out replay_out/REPLAY-RESULTS.md [--gate-lat 0.1]

Per variant: rounds, fills, money in, profit, return on cost, 95% CI (2,000 bootstraps over rounds),
pairs made (locked-in $), one-sided result, pair completion (share of filled shares that were paired),
rounds won/lost, days positive, worst day. Gate (plan step 3): at the measured latency the profit CI
low must be > 0 AND pair completion >= 95%.
"""
import argparse, collections, glob, json, random, statistics


def boot_ci(vals, n=2000, seed=7):
    if not vals: return (None, None)
    rnd = random.Random(seed); m = len(vals); out = []
    for _ in range(n):
        out.append(sum(vals[rnd.randrange(m)] for _ in range(m)))
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--gate-lat", type=float, default=None)
    a = ap.parse_args()
    files = sorted(set(sum([glob.glob(p) for p in a.days], [])))
    agg = collections.defaultdict(lambda: dict(rounds=[], days={}))
    cfgs = {}
    for fp in files:
        d = json.load(open(fp)); day = fp.split("day_")[-1][:8]
        for v, res in d["variants"].items():
            cfgs[v] = res["cfg"]
            agg[v]["rounds"].extend(res["rounds"])
            agg[v]["days"][day] = sum(r["profit"] for r in res["rounds"])
    L = [f"# Paired-maker replay with order latency - {len(files)} days ({files[0].split('day_')[-1][:8]} .. {files[-1].split('day_')[-1][:8]})", "",
         "Tactic: both sides, quote window 60-240 s, pair cap 0.97, inventory 40, size 20 per order. `model` = fair value minus 5c; "
         "`join` = join the best bid. `lat` = seconds for our order/cancel to land at the exchange (0 = instant ceiling).", "",
         "| variant | rounds | fills | money in | profit | return on cost | profit 95% CI | pairs locked $ | one-sided $ | pair completion | rounds won/lost | days +/- | worst day | gate |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    summary = {}
    for v in sorted(agg, key=lambda x: (cfgs[x]["mode"], cfgs[x]["lat"])):
        rows = agg[v]["rounds"]; days = agg[v]["days"]
        c = sum(r["cost"] for r in rows); p = sum(r["profit"] for r in rows)
        both = [r for r in rows if r["up_avg"] is not None and r["dn_avg"] is not None]
        pr = sum(r["pairs"] * (1 - r["up_avg"] - r["dn_avg"]) for r in both)
        sh = sum(r["up_sh"] + r["dn_sh"] for r in rows); paired = sum(2 * r["pairs"] for r in rows)
        comp = paired / sh if sh else 0.0
        lo, hi = boot_ci([r["profit"] for r in rows])
        won = sum(1 for r in rows if r["profit"] > 0); lost = sum(1 for r in rows if r["profit"] < 0)
        dpos = sum(1 for x in days.values() if x > 0); dneg = sum(1 for x in days.values() if x < 0)
        worst = min(days.values()) if days else 0.0
        gate = (lo is not None and lo > 0 and comp >= 0.95)
        summary[v] = dict(cfg=cfgs[v], rounds=len(rows), fills=sum(r["fills"] for r in rows), cost=round(c, 2), profit=round(p, 2),
                          return_on_cost=(p / c if c else 0.0), profit_ci95=[lo, hi], pairs_locked=round(pr, 2), one_sided=round(p - pr, 2),
                          pair_completion=comp, rounds_won=won, rounds_lost=lost, days_pos=dpos, days_neg=dneg, worst_day=round(worst, 2),
                          per_day=days, gate=gate)
        L.append(f"| {v} | {len(rows)} | {sum(r['fills'] for r in rows)} | ${c:,.0f} | ${p:,.2f} | {100 * p / c if c else 0:+.2f}% | "
                 f"[{lo:,.0f}, {hi:,.0f}] | {pr:,.2f} | {p - pr:,.2f} | {100 * comp:.1f}% | {won}/{lost} | {dpos}/{dneg} | ${worst:,.0f} | {'PASS' if gate else 'FAIL'} |")
    L += ["", "Gate per variant = profit CI low > 0 AND pair completion >= 95% (gabigol: 97%)."]
    if a.gate_lat is not None:
        hits = [v for v in summary if abs(summary[v]['cfg']['lat'] - a.gate_lat) < 1e-9]
        L += ["", f"## Step-3 verdict at the measured latency {a.gate_lat:g} s", ""]
        for v in hits:
            s = summary[v]
            L.append(f"- {v}: profit ${s['profit']:,.2f} ({100 * s['return_on_cost']:+.2f}%), CI [{s['profit_ci95'][0]:,.0f}, {s['profit_ci95'][1]:,.0f}], "
                     f"pair completion {100 * s['pair_completion']:.1f}% -> {'PASS' if s['gate'] else 'FAIL'}")
    L += ["", "## Per-day profit", "", "| day | " + " | ".join(sorted(summary)) + " |", "|---|" + "---|" * len(summary)]
    alldays = sorted(set(sum([list(s["per_day"]) for s in summary.values()], [])))
    for day in alldays:
        L.append(f"| {day} | " + " | ".join(f"{summary[v]['per_day'].get(day, 0):+.0f}" for v in sorted(summary)) + " |")
    open(a.out, "w").write("\n".join(L) + "\n")
    json.dump(dict(files=files, summary=summary), open(a.out.replace(".md", ".json"), "w"), indent=1)
    print("\n".join(L[:len(summary) + 8]))


if __name__ == "__main__":
    main()
