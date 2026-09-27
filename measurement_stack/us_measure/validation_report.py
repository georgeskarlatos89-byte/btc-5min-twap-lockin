#!/usr/bin/env python3
"""
validation_report.py — BRTI-proxy vs venue settlement (US BTC Up/Down rounds).

Reads us_rounds/rounds.csv. A round counts as a COMPARISON only when the recorder saw it
from its own start boundary, both boundary TWAPs have >= 45 one-second samples and the venue
published a settlement. The proxy is not trusted as an arbiter before 200 comparisons.

Writes us_rounds/VALIDATION-REPORT.md + validation_by_gap.csv, prints a JSON summary.
"""
import csv, json, math, os, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "us_rounds")
TARGET = 200
BUCKETS = [("<1bp", 0, 1), ("1-2bp", 1, 2), ("2-4bp", 2, 4), ("4-8bp", 4, 8), (">8bp", 8, 1e9)]

def wilson(k, n, z=1.96):
    if not n: return None, None
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * (c - h), 1), round(100 * (c + h), 1)

def main():
    p = os.path.join(OUT, "rounds.csv")
    rows = list(csv.DictReader(open(p))) if os.path.exists(p) else []
    status = Counter(r["match"] for r in rows)
    comp = [r for r in rows if r["match"] in ("OK", "DIVERGENCE")]
    by = defaultdict(lambda: [0, 0]); byh = defaultdict(lambda: [0, 0])
    for r in comp:
        g = abs(float(r["proxy_gap_bps"])); b = next(n for n, lo, hi in BUCKETS if lo <= g < hi)
        by[b][0] += 1; by[b][1] += r["match"] == "OK"; byh[r["horizon"]][0] += 1; byh[r["horizon"]][1] += r["match"] == "OK"
    tab = []
    for n, _, _ in BUCKETS:
        k = by[n]
        if k[0]: lo, hi = wilson(k[1], k[0]); tab.append(dict(gap_bucket=n, rounds=k[0], agree=k[1], agree_pct=round(100 * k[1] / k[0], 2), ci95_low=lo, ci95_high=hi))
    with open(os.path.join(OUT, "validation_by_gap.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["gap_bucket", "rounds", "agree", "agree_pct", "ci95_low", "ci95_high"]); w.writeheader(); w.writerows(tab)
    ws = [r for r in rows if r.get("settlement_src") == "ws" and r.get("settlement_delay_s") not in ("", None)]
    delays = sorted(float(r["settlement_delay_s"]) for r in ws)
    both = [r for r in rows if r.get("settlement_px") not in ("", None) and r.get("http_settlement") not in ("", None)]
    wit_dis = [r for r in both if (float(r["settlement_px"]) >= 0.5) != (float(r["http_settlement"]) >= 0.5)]
    ups = [r for r in rows if r.get("venue_up") in ("0", "1")]
    flat = [r for r in comp if abs(float(r["proxy_gap_bps"])) < 0.05]
    n, ok = len(comp), sum(r["match"] == "OK" for r in comp)
    ready = n >= TARGET
    big = [r for r in comp if abs(float(r["proxy_gap_bps"])) >= 2]
    big_ok = sum(r["match"] == "OK" for r in big)
    verdict = ("NOT READY: fewer than 200 comparisons" if not ready else
               "USABLE as a direction arbiter outside the razor zone" if big and big_ok / len(big) >= 0.99 else
               "NOT TRUSTWORTHY: disagrees with the venue even on clear rounds")
    summ = dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), rounds_recorded=len(rows), status=dict(status),
                comparisons=n, target=TARGET, agree=ok, agree_pct=round(100 * ok / n, 2) if n else None,
                clear_rounds_2bp=len(big), clear_rounds_agree=big_ok, by_horizon={h: dict(rounds=v[0], agree=v[1]) for h, v in byh.items()},
                settlement_delay_median_s=(delays[len(delays) // 2] if delays else None), settlement_delay_max_s=(delays[-1] if delays else None),
                two_witness_checked=len(both), two_witness_disagree=len(wit_dis),
                venue_up_rate_pct=round(100 * sum(r["venue_up"] == "1" for r in ups) / len(ups), 2) if ups else None,
                dead_flat_rounds=len(flat), dead_flat_settled_up=sum(r["venue_up"] == "1" for r in flat), verdict=verdict, ready=ready)
    L = ["# US BTC Up/Down — BRTI proxy validation\n", f"Generated {summ['generated']}. Measure-only. Proxy = mid of 4 constituent top-of-books, TWAP60 at each boundary, 2 dp, ties Up.\n",
         f"**Verdict: {verdict}.**\n", "| item | value |", "|---|---|",
         f"| rounds recorded | {len(rows)} |", f"| valid comparisons | {n} of {TARGET} needed |",
         f"| proxy agrees with venue | {ok} of {n}" + (f" ({summ['agree_pct']} %)" if n else "") + " |",
         f"| clear rounds (gap at least 2 bps) agree | {big_ok} of {len(big)} |",
         f"| excluded rows | " + ", ".join(f"{k} {v}" for k, v in status.items() if k not in ("OK", "DIVERGENCE")) + " |",
         f"| settlement arrives over the websocket after | median {summ['settlement_delay_median_s']} s, max {summ['settlement_delay_max_s']} s |",
         f"| websocket vs HTTP settlement | {len(both)} checked, {len(wit_dis)} disagree |",
         f"| venue Up rate | {summ['venue_up_rate_pct']} % of {len(ups)} |",
         f"| dead-flat rounds (proxy gap under 0.05 bps) | {len(flat)}, of which {summ['dead_flat_settled_up']} settled Up |", "",
         "## Agreement by proxy gap\n", "| gap | rounds | agree | agree % | 95 % interval |", "|---|---|---|---|---|"]
    L += [f"| {t['gap_bucket']} | {t['rounds']} | {t['agree']} | {t['agree_pct']} | {t['ci95_low']} to {t['ci95_high']} |" for t in tab]
    L += ["", "## Divergences\n", "| round | proxy gap bps | proxy open | proxy close | settlement | source |", "|---|---|---|---|---|---|"]
    L += [f"| {r['round_slug']} | {r['proxy_gap_bps']} | {r['proxy_twap60_open']} | {r['proxy_twap60_close']} | {r['settlement_px']} | {r['settlement_src']} |"
          for r in comp if r["match"] == "DIVERGENCE"][-40:]
    L += ["", "Limits: the venue publishes the contract settlement (about 0.01 or 0.99), not the BRTI value, so only the DIRECTION can be compared. "
          "The proxy uses 4 of BRTI's constituents and top-of-book mids instead of the full depth-weighted curve, so disagreement on razor rounds is expected."]
    open(os.path.join(OUT, "VALIDATION-REPORT.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(summ, open(os.path.join(OUT, "validation_summary.json"), "w"), indent=1)
    print(json.dumps(summ, indent=1))

if __name__ == "__main__":
    main()
