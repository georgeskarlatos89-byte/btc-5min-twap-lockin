#!/usr/bin/env python3
"""fill_autopsy.py - V7 (diagnostic, no quotes): why are the fills toxic?

  python fill_autopsy.py --run variant_runs/V0_base --replay replay/*.jsonl [--test-days ...]

For every simulated fill (sim_fills.jsonl of a run) it looks at the replay around the fill:
  - the sweep that produced it: best-bid (or best-ask) move over the previous 1-2 s, in ticks
    (by construction of the v2 fill model EVERY counted fill is a run-through; the size is new)
  - what the book mid and the model fair did 5 s, 30 s and at the end after the fill, measured
    as edge for us: BUY -> mid(t+h) - price; SELL -> price - mid(t+h)
  - bucketed by time-of-round and by side
Plus the exposure the snapshot model cannot see: ticks per round our resting quote sat AT the
displayed touch (touch_*) or IMPROVED it (improve_*), from sim_rounds.csv, and the number of
benign touch fills per round that would be needed to break even at the half-spread.
Writes AUTOPSY.md + autopsy.json into the run folder.
"""
import argparse, csv, glob, json, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_strategy import HALF_SPREAD, QUOTE_SIZE

BUCKETS = [(0, 60), (60, 120), (120, 180), (180, 240), (240, 300)]


def bucket(t_el):
    for lo, hi in BUCKETS:
        if lo <= t_el < hi:
            return f"{lo:03d}-{hi:03d}s"
    return "other"


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--replay", nargs="+", required=True)
    ap.add_argument("--test-days", nargs="*", default=None)
    a = ap.parse_args()
    files = sorted(set(sum([glob.glob(p) for p in a.replay], [])))
    fills = [json.loads(l) for l in open(os.path.join(a.run, "sim_fills.jsonl"))]
    rows = list(csv.DictReader(open(os.path.join(a.run, "sim_rounds.csv"), newline="")))
    if a.test_days:
        td = set(a.test_days)
        rows = [r for r in rows if r["day"] in td]
        starts = {int(r["round_start"]) for r in rows}
        fills = [f for f in fills if f["start"] in starts]
    by_start = defaultdict(list)
    for f in fills:
        by_start[f["start"]].append(f)
    # walk the replay once; keep only rounds with fills
    series = {}
    for fp in files:
        with open(fp) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r["start"] in by_start:
                    series.setdefault(r["start"], []).append((r["ts"], r.get("bb"), r.get("ba"), r.get("mid"), r.get("fair_p")))
    out = []
    for start, fl in by_start.items():
        s = sorted(series.get(start, []))
        if not s:
            continue
        idx = {int(ts): i for i, (ts, *_r) in enumerate(s)}

        def at(t):
            i = idx.get(int(t))
            return s[i] if i is not None else None

        last = s[-1]
        for f in fl:
            t = f["t"]; side = f["side"]; p = f["price"]
            cur = at(t); m1 = at(t - 1); m2 = at(t - 2)
            payoff = 1.0 if f["outcome"] == "up" else 0.0
            def edge(mid):
                if mid is None: return None
                return (mid - p) if side == "BUY" else (p - mid)
            rec = dict(start=start, t_el=int(t - start), side=side, price=p, bucket=bucket(t - start),
                       sweep_ticks_1s=None, sweep_ticks_2s=None,
                       edge_mid_5=None, edge_mid_30=None, edge_fair_5=None, edge_fair_30=None,
                       edge_mid_end=edge(last[3]) if last[3] is not None else None,
                       edge_settle=(payoff - p) if side == "BUY" else (p - payoff))
            if cur and m1:
                a_, b_ = (cur[1], m1[1]) if side == "BUY" else (cur[2], m1[2])
                if a_ is not None and b_ is not None:
                    rec["sweep_ticks_1s"] = round((b_ - a_) * 100 if side == "BUY" else (a_ - b_) * 100, 1)
            if cur and m2:
                a_, b_ = (cur[1], m2[1]) if side == "BUY" else (cur[2], m2[2])
                if a_ is not None and b_ is not None:
                    rec["sweep_ticks_2s"] = round((b_ - a_) * 100 if side == "BUY" else (a_ - b_) * 100, 1)
            for h, k in ((5, "5"), (30, "30")):
                r2 = at(t + h)
                if r2:
                    rec[f"edge_mid_{k}"] = edge(r2[3]); rec[f"edge_fair_{k}"] = edge(r2[4])
            out.append(rec)
    # aggregate
    agg = defaultdict(lambda: defaultdict(list))
    for r in out:
        for key in ("ALL", r["bucket"], r["side"], f"{r['side']}@{r['bucket']}"):
            for k in ("sweep_ticks_1s", "sweep_ticks_2s", "edge_mid_5", "edge_mid_30", "edge_fair_5", "edge_fair_30", "edge_mid_end", "edge_settle"):
                if r[k] is not None:
                    agg[key][k].append(r[k])
            agg[key]["n"].append(1)
    summary = {key: {k: (len(v) if k == "n" else round(mean(v), 4)) for k, v in d.items()} for key, d in agg.items()}
    # exposure the snapshot model cannot see
    n_rounds = len(rows)
    touch = sum(int(r.get("touch_bid") or 0) + int(r.get("touch_ask") or 0) for r in rows)
    improve = sum(int(r.get("improve_bid") or 0) + int(r.get("improve_ask") or 0) for r in rows)
    pnl = sum(float(r["pnl_usd"]) for r in rows)
    loss_per_round = pnl / n_rounds if n_rounds else 0.0
    benign_needed = (-loss_per_round / (HALF_SPREAD * QUOTE_SIZE)) if loss_per_round < 0 else 0.0
    counted_fills_per_round = len(out) / n_rounds if n_rounds else 0.0
    exposure = dict(rounds=n_rounds, counted_fills=len(out), counted_fills_per_round=round(counted_fills_per_round, 3),
                    pnl_usd=round(pnl, 2), loss_per_round_usd=round(loss_per_round, 4),
                    touch_ticks_per_round=round(touch / n_rounds, 1) if n_rounds else None,
                    improve_ticks_per_round=round(improve / n_rounds, 1) if n_rounds else None,
                    benign_touch_fills_per_round_needed_to_break_even=round(benign_needed, 2),
                    ratio_benign_needed_to_counted=round(benign_needed / counted_fills_per_round, 2) if counted_fills_per_round else None,
                    note="a benign touch fill earns at most the half-spread (HALF_SPREAD x QUOTE_SIZE); the ratio says how many "
                         "invisible good fills per counted bad fill the strategy would need")
    res = dict(run=os.path.basename(a.run.rstrip("/\\")), fills=len(out), summary=summary, exposure=exposure)
    json.dump(res, open(os.path.join(a.run, "autopsy.json"), "w"), indent=1)
    L = [f"# AUTOPSY (V7) - {res['run']}: {len(out)} counted fills over {n_rounds} rounds", "",
         "Edge is measured FOR US (positive = the price moved our way after the fill). sweep = how far the touch had",
         "moved through our price in the previous 1-2 s, in ticks (1 tick = 1 cent).", "",
         "| group | n | sweep 1s | sweep 2s | edge mid +5s | edge fair +5s | edge mid +30s | edge fair +30s | edge mid end | edge settle |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    order = ["ALL", "BUY", "SELL"] + [f"{lo:03d}-{hi:03d}s" for lo, hi in BUCKETS] + \
            [f"{s}@{lo:03d}-{hi:03d}s" for s in ("BUY", "SELL") for lo, hi in BUCKETS]
    for key in order:
        d = summary.get(key)
        if not d: continue
        g = lambda k: ("-" if d.get(k) is None else (f"{d[k]:+.2f}" if k.startswith("sweep") else f"{100 * d[k]:+.2f}c"))
        L.append(f"| {key} | {d['n']} | {g('sweep_ticks_1s')} | {g('sweep_ticks_2s')} | {g('edge_mid_5')} | {g('edge_fair_5')} | {g('edge_mid_30')} | {g('edge_fair_30')} | {g('edge_mid_end')} | {g('edge_settle')} |")
    L += ["", "## Exposure the snapshot fill model cannot see", ""]
    for k, v in exposure.items():
        L.append(f"- {k}: {v}")
    open(os.path.join(a.run, "AUTOPSY.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
