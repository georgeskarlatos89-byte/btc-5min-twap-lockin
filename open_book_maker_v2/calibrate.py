#!/usr/bin/env python3
"""
calibrate.py - is the v2 fair model honest? Compare fair_p (and the market mid) with the
chain-verified outcome at fixed times into the round.

  python calibrate.py --replay replay/*.jsonl --labels <corpus.csv> [--at 60,120,180,240,270]
Prints, per time point: rounds, Brier score of the model vs the market mid, and a
calibration table (predicted bucket -> realized Up rate). A model that says 0.9 should
settle Up about 90% of the time; a Brier score below the market's means it is sharper
than the crowd, above means the crowd knows more.
"""
import argparse, csv, json, sys
from collections import defaultdict
from datetime import datetime, timezone


def load_labels(path):
    out = {}
    for r in csv.DictReader(open(path, newline="")):
        lab = (r.get("chain_outcome") or "").strip().lower()
        if lab not in ("up", "down"):
            continue
        try:
            st = int(datetime.strptime(r["start_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        out[st] = 1 if lab == "up" else 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", nargs="+", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--at", default="30,60,120,180,240,270")
    a = ap.parse_args()
    labels = load_labels(a.labels)
    ats = [int(x) for x in a.at.split(",")]
    rows = defaultdict(dict)   # (start) -> {t_el: rec}
    for fp in a.replay:
        for ln in open(fp):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("label") != "5m" or r.get("fair_p") is None:
                continue
            t = int(round(r["t_el"]))
            if t in ats:
                rows[r["start"]][t] = r
    print(f"rounds in replay: {len(rows)}; with chain label: {sum(1 for s in rows if s in labels)}")
    for t in ats:
        pts = [(rows[s][t]["fair_p"], rows[s][t].get("mid"), labels[s]) for s in rows if s in labels and t in rows[s]]
        if not pts:
            continue
        n = len(pts)
        brier_m = sum((p - y) ** 2 for p, _, y in pts) / n
        with_mid = [(p, m, y) for p, m, y in pts if m is not None]
        brier_mkt = sum((m - y) ** 2 for _, m, y in with_mid) / len(with_mid) if with_mid else None
        acc_m = sum((p >= 0.5) == (y == 1) for p, _, y in pts) / n
        acc_mkt = sum((m >= 0.5) == (y == 1) for _, m, y in with_mid) / len(with_mid) if with_mid else None
        # mean absolute gap model vs market
        gap = sum(abs(p - m) for p, m, _ in with_mid) / len(with_mid) if with_mid else None
        print(f"\n== t_el = {t:3d} s | rounds {n} | Brier model {brier_m:.4f} vs market {brier_mkt if brier_mkt is None else round(brier_mkt, 4)} "
              f"| direction right: model {acc_m:.3f} market {acc_mkt if acc_mkt is None else round(acc_mkt, 3)} | mean |model-market| {gap if gap is None else round(gap, 3)}")
        buckets = defaultdict(lambda: [0, 0])
        for p, _, y in pts:
            b = min(int(p * 10), 9)
            buckets[b][0] += 1; buckets[b][1] += y
        print("   predicted   rounds   realized Up rate")
        for b in sorted(buckets):
            c, u = buckets[b]
            print(f"   {b / 10:.1f}-{(b + 1) / 10:.1f}      {c:5d}      {u / c:.3f}")


if __name__ == "__main__":
    main()
