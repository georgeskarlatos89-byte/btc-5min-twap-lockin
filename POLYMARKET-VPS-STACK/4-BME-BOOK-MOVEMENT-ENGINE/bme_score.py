#!/usr/bin/env python3
"""
bme_score.py — score recorded book-movement data against the pre-registered signals.

Reads everything in s1_harness/s9_data/bme/ (any number of days/parts) and scores:

  S1 flicker/batch  : cause-hash churn distribution + batch re-quote bursts
                      (single hash touching many levels in one ms).
  S2 pre-tick pulls : does pretick_pulls_200ms=N predict the Chainlink tick's move?
                      Quintiles of N vs mean |Δ| and P(up-tick), top-decile spotlight.
  S3 imbalance      : does early-round signed depth (top-10) predict the round outcome?
                      Joins official resolutions via gamma (closed=true fallback).
  S4/S5             : require the >=7-day dataset / wallet join — printed as pending.

Gates (same as everywhere in this repo): a signal graduates only at >= +2c/sh
post-fee EV over >= 100 independent rounds. This scorer REPORTS; it never
recommends trading on n < gates. Outputs bme_score_report.json + console.

Usage: python3 s1_harness/bme_score.py [--min-rounds 100]
"""
import argparse, csv, glob, gzip, io, json, os, time, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BME = os.path.join(HERE, "s9_data", "bme")
GAMMA = "https://gamma-api.polymarket.com"
_last = [0.0]

def get(url, retries=4):
    for i in range(retries):
        w = 0.12 - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == retries - 1: raise
            time.sleep(1.5 * (i + 1))

def read_csvs(pattern):
    """yield rows from all matching .csv and .csv.gz files (any header state)."""
    for p in sorted(glob.glob(os.path.join(BME, pattern))):
        op = gzip.open(p, "rt") if p.endswith(".gz") else open(p, "r")
        with op as f:
            first = True
            try:
                for r in csv.reader(f):
                    if not r: continue
                    if first:
                        first = False
                        if r[0] in ("ts_ms", "hash", "timestamp"): continue  # header
                    # 2026-09-21: streamed multi-member gzip files may carry a header row at
                    # each (re)start member - skip any header seen anywhere, not just row 1.
                    if r[0] in ("ts_ms", "hash", "timestamp"): continue
                    yield r
            except (EOFError, OSError) as e:
                # 2026-09-21: a crash mid-stream leaves the last gzip member without a trailer;
                # keep everything read so far instead of aborting the whole score.
                print(f"[read_csvs] {os.path.basename(p)}: truncated tail ignored ({e.__class__.__name__})")

def round_outcome(series, start):
    """official result via gamma events (live rounds: plain; settled: closed=true)."""
    for q in ("", "&closed=true"):
        try:
            ev = get(f"{GAMMA}/events?slug=btc-updown-{series}-{start}{q}")[0]["markets"][0]
            pr = [float(x) for x in json.loads(ev["outcomePrices"])]
            if ev.get("closed") or not ev.get("acceptingOrders") or pr[0] >= 0.9995 or pr[0] <= 0.0005:
                return pr[0] > 0.5
        except Exception:
            pass
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rounds", type=int, default=100)
    args = ap.parse_args()
    R = {"generated_utc": datetime.now(timezone.utc).isoformat(), "signals": {}}

    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(BME, "*")))
    days = sorted(set(f.split("_")[-1].split(".")[0] for f in files if f.startswith(("events_", "bookstate_"))))
    span_note = f"data files: {len(files)}, days touched: {days}"

    # ---------------- S1: hash churn + batch bursts
    hashes = [(r[0], int(r[1])) for r in read_csvs("hashes_*.csv")]
    bursts = Counter()   # (ts,hash) -> n events in same ms
    n_pc = 0
    for r in read_csvs("events_*.csv*"):
        if r[1] != "price_change": continue
        n_pc += 1
        bursts[(r[0], r[9])] += 1
    batch = [v for v in bursts.values() if v >= 5]
    hh = [n for _, n in hashes]
    S1 = {
        "price_changes_read": n_pc,
        "distinct_causes": len(hashes),
        "causes_ge5_events": sum(1 for x in hh if x >= 5),
        "causes_ge20_events": sum(1 for x in hh if x >= 20),
        "batch_bursts_ge5_same_ms": len(batch),
        "batch_burst_max_levels": max(batch) if batch else 0,
        "note": "churn tail + batch bursts identify the re-quote bots; S1 proper (do "
                "their levels act as fake support/resistance?) needs level-tagged "
                "outcomes on the 7-day set.",
    }
    R["signals"]["S1_flicker"] = S1
    print(f"[S1 flicker] {json.dumps(S1, indent=1)}")

    # ---------------- S2: pre-tick pulls vs tick move
    ticks = []   # (ts, price, pretick_pulls)
    for r in read_csvs("events_*.csv*"):
        if r[1] != "spot_tick": continue
        try:
            n = int((r[10].split("=")[-1]) if "=" in r[10] else 0)
        except Exception:
            n = 0
        ticks.append((int(r[0]), float(r[5]), n))
    ticks.sort()
    pairs = []
    for (t0, p0, n0), (t1, p1, _) in zip(ticks, ticks[1:]):
        dt = (t1 - t0) / 1000.0
        if 0.5 <= dt <= 5.0:
            pairs.append((n0, p1 - p0))
    def qreport(pairs):
        if not pairs: return {"n": 0}
        pairs_sorted = sorted(pairs, key=lambda x: x[0])
        k = len(pairs_sorted)
        out = {"n": k}
        for i, (lo, hi) in enumerate(((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.0))):
            seg = pairs_sorted[int(lo*k):int(hi*k)]
            if not seg: continue
            ups = sum(1 for _, d in seg if d > 0)
            out[f"Q{i+1}"] = {"pulls_range": f"{seg[0][0]}-{seg[-1][0]}",
                              "mean_abs_move": round(sum(abs(d) for _, d in seg)/len(seg), 3),
                              "p_up": round(ups/len(seg), 3)}
        top = pairs_sorted[int(0.9*k):]
        if top:
            out["top_decile"] = {"n": len(top),
                                 "min_pulls": top[0][0],
                                 "mean_abs_move": round(sum(abs(d) for _, d in top)/len(top), 3),
                                 "p_up": round(sum(1 for _, d in top if d > 0)/len(top), 3)}
        return out
    S2 = {"ticks": len(ticks), "usable_pairs": len(pairs), "by_quintile_of_pretick_pulls": qreport(pairs),
          "note": "if top quintile's mean|Δ| clearly exceeds Q1's, the cancel burst "
                  "front-runs the tick (S2 confirmed at scale only over >=7 days)."}
    R["signals"]["S2_pretick_pulls"] = S2
    print(f"[S2 pre-tick pulls] {json.dumps(S2, indent=1)}")

    # ---------------- S3: early-round depth imbalance vs outcome
    per_round = defaultdict(list)
    for r in read_csvs("bookstate_1s_*.csv*"):
        try:
            ts, series, idx = int(r[0]), r[1], int(r[2])
            if series not in ("5m", "15m") or idx != 0: continue   # Up token only
            bd, ad = float(r[7]), float(r[8])
            if bd + ad <= 0: continue
            T = 300 if series == "5m" else 900
            start = ts // 1000 // T * T
            frac = (ts / 1000.0 - start) / T
            if frac > 0.20: continue                               # early window only
            per_round[(series, start)].append((bd - ad) / (bd + ad))
        except Exception:
            continue
    rows = []
    for (series, start), imbs in sorted(per_round.items()):
        won_up = round_outcome(series, start)
        rows.append({"series": series, "start": start, "n_1s": len(imbs),
                     "mean_imb": round(sum(imbs)/len(imbs), 4), "won_up": won_up})
    scored = [r for r in rows if r["won_up"] is not None]
    if scored:
        up_imb = [r["mean_imb"] for r in scored if r["won_up"]]
        dn_imb = [r["mean_imb"] for r in scored if not r["won_up"]]
        acc = sum(1 for r in scored if (r["mean_imb"] > 0) == r["won_up"]) / len(scored)
        s3 = {"rounds_scored": len(scored),
              "mean_imb_when_up_won": round(sum(up_imb)/len(up_imb), 4) if up_imb else None,
              "mean_imb_when_down_won": round(sum(dn_imb)/len(dn_imb), 4) if dn_imb else None,
              "sign_accuracy": round(acc, 3)}
    else:
        s3 = {"rounds_scored": 0}
    s3["gate"] = f"needs >= {args.min_rounds} scored rounds (have {len(scored)})"
    R["signals"]["S3_imbalance"] = s3
    R["rounds"] = rows
    print(f"[S3 imbalance] {json.dumps(s3, indent=1)}")

    R["signals"]["S4_double_tap_reaction"] = {"status": "pending 7-day dataset + wallet join (v1 /trades)"}
    R["signals"]["S5_divergence"] = {"status": "pending 7-day dataset (fused twap markers recorded)"}
    R["span_note"] = span_note

    out = os.path.join(BME, "bme_score_report.json")
    json.dump(R, open(out, "w"), indent=1)
    print(f"\nreport -> {out}\n{span_note}")
    print("REMINDER: nothing here is tradeable until the pre-registered gates "
          "(>=100 rounds, >= +2c/sh post-fee EV) are met on the full dataset.")

if __name__ == "__main__":
    main()
