#!/usr/bin/env python3
"""
mispricing.py — mispricing monitors computed from recorder data. NO trading, no network
except one read of data already on this box. Run by the daily report, or by hand.

  1  ONE-SIDED BOOK statistic: share of seconds with a missing bid or ask, per
     minute-to-resolution, per venue/horizon
         US rounds  <- us_measure/us_rounds/bookstate-*.csv
         .com       <- BME bookstate_1s_*.csv(.gz) (token_idx 0 = Up)
  2  MARKET vs ORACLE divergence (US rounds): market-implied p(Up) against a simple
     oracle-walk probability from the BRTI proxy gap and its own measured volatility
  3  CROSS-VENUE: the 15m .com round and the 15m US round over the SAME window —
     price gap per minute-to-resolution, and whether the two venues resolved alike

Outputs (monitors/out/): CSVs with named columns + summary.json.
"""
import argparse, csv, glob, gzip, json, math, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)
HOME = os.path.expanduser("~")
US_DIR = os.path.join(STACK, "us_measure", "us_rounds")
BME_DIR = os.path.join(HOME, "POLYMARKET-VPS-STACK", "polymarket-stack", "4-BME-BOOK-MOVEMENT-ENGINE", "s9_data", "bme")
BT_CACHE = os.path.join(STACK, "backtest7d", "cache")

def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def fl(x):
    try: return float(x)
    except Exception: return None
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def opener(p): return gzip.open(p, "rt", encoding="utf-8", errors="replace") if p.endswith(".gz") else open(p, encoding="utf-8", errors="replace")
def wcsv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})

def us_rows(days):
    cut = time.time() - days * 86400
    for p in sorted(glob.glob(os.path.join(US_DIR, "bookstate-*.csv*"))):
        if os.path.getmtime(p) < cut - 86400: continue
        with opener(p) as f:
            for r in csv.DictReader(f):
                ts = fl(r.get("ts_unix"))
                if ts and ts >= cut: yield r

def us_round_results():
    out = {}
    p = os.path.join(US_DIR, "rounds.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)): out[r["round_slug"]] = r
    return out

# ------------------------------------------------------------------ 1. one-sided books
def one_sided(days, bme_days):
    agg = defaultdict(lambda: [0, 0, 0, 0])          # (venue, horizon, minute) -> [secs, one_sided, no_ask, no_bid]
    for r in us_rows(days):
        s = fl(r["secs_to_end"])
        if s is None or s < 0 or (fl(r.get("frame_age_s")) or 0) > 30: continue
        k = ("polymarket.us", r["horizon"], int(s // 60))
        a = agg[k]; a[0] += 1
        nb, na = r["best_bid"] == "", r["best_ask"] == ""
        a[1] += nb or na; a[2] += na; a[3] += nb
    # .com: the BME recorder writes a book-state row ONLY when both sides exist (bme_capture.py
    # write_bookstate skips empty sides). A one-sided second is therefore a MISSING second, and
    # the statistic is 1 - observed / expected. Recorder gaps count too, so read it as an upper bound.
    files = sorted(glob.glob(os.path.join(BME_DIR, "bookstate_1s_*.csv*")))[-bme_days:]
    seen = defaultdict(set)                      # (series, round_start) -> seconds-to-end observed
    for p in files:
        with opener(p) as f:
            for r in csv.DictReader(f):
                if r.get("token_idx") != "0": continue
                T = 300 if r["series"] == "5m" else 900
                ts = fl(r["ts_ms"])
                if ts is None: continue
                ts /= 1000.0
                seen[(r["series"], int(ts // T) * T)].add(int(T - (ts % T)))
    for (series, start), secs in seen.items():
        T = 300 if series == "5m" else 900
        if len(secs) < 0.5 * T: continue          # partial round at a file edge or a recorder gap
        for mnt in range(T // 60):
            got = sum(1 for s in secs if mnt * 60 < s <= (mnt + 1) * 60)
            a = agg[("polymarket.com", series, mnt)]
            a[0] += 60; a[1] += 60 - got; a[2] += 0; a[3] += 0
    rows = [dict(venue=k[0], horizon=k[1], minutes_to_resolution=k[2], seconds_observed=v[0], one_sided_seconds=v[1],
                 one_sided_pct=round(100 * v[1] / v[0], 3), no_ask_pct=round(100 * v[2] / v[0], 3), no_bid_pct=round(100 * v[3] / v[0], 3))
            for k, v in sorted(agg.items()) if v[0]]
    wcsv(os.path.join(OUT, "one sided book by minute to resolution.csv"), rows,
         ["venue", "horizon", "minutes_to_resolution", "seconds_observed", "one_sided_seconds", "one_sided_pct", "no_ask_pct", "no_bid_pct"])
    return rows

# ------------------------------------------------------------------ 2. market vs oracle (US)
def divergence(days):
    by = defaultdict(list)
    for r in us_rows(days):
        by[r["round_slug"]].append(r)
    res = us_round_results()
    rows, per_round = [], []
    for slug, R in by.items():
        R.sort(key=lambda r: fl(r["ts_unix"]))
        px = [fl(r["proxy_px"]) for r in R if fl(r["proxy_px"])]
        if len(px) < 120: continue
        # volatility from 30-second changes: 1-second changes of a polled mid-of-mids are artificially
        # smooth (first run: 0.97 $/sqrt(s), about a quarter of BTC's real short-horizon volatility)
        LAG = 30
        d = [b - a for a, b in zip(px, px[LAG:])]
        if len(d) < 60: continue
        m = sum(d) / len(d); s1 = math.sqrt(max(sum((x - m) ** 2 for x in d) / len(d), 1e-9) / LAG)  # $ per sqrt(second)
        worst = None; n = 0; sq = 0.0
        for r in R:
            gap, s, up = fl(r["proxy_gap_bps_vs_open"]), fl(r["secs_to_end"]), fl(r["up_px"])
            o, p = fl(r["proxy_open_twap60"]), fl(r["proxy_px"])
            if None in (gap, s, up, o, p) or s <= 0: continue
            # the settlement value is a 60 s average: remaining uncertainty shrinks inside the last minute
            eff = max(s - 30, 0) + (min(s, 60) ** 3) / (3 * 60 ** 2) if s < 60 else s - 30
            sigma = s1 * math.sqrt(max(eff, 0.5))
            model = ncdf((p - o) / sigma)
            dv = up - model; n += 1; sq += dv * dv
            row = dict(round_slug=slug, horizon=r["horizon"], ts_utc=iso(fl(r["ts_unix"])), secs_to_end=int(s), market_up_px=up,
                       best_bid=r["best_bid"], best_ask=r["best_ask"], proxy_gap_bps_vs_open=gap, oracle_walk_p_up=round(model, 4),
                       market_minus_model=round(dv, 4))
            if worst is None or abs(dv) > abs(worst["market_minus_model"]): worst = row
            if int(s) % 30 == 0: rows.append(row)
        if n:
            rr = res.get(slug, {})
            per_round.append(dict(round_slug=slug, horizon=R[0]["horizon"], seconds_compared=n, rms_divergence=round(math.sqrt(sq / n), 4),
                                  worst_divergence=worst["market_minus_model"], worst_at_secs_to_end=worst["secs_to_end"],
                                  worst_market_px=worst["market_up_px"], worst_model_p=worst["oracle_walk_p_up"],
                                  proxy_sigma_usd_per_sqrt_s=round(s1, 3), venue_up=rr.get("venue_up", ""), proxy_up=rr.get("proxy_up", ""),
                                  match=rr.get("match", "")))
    wcsv(os.path.join(OUT, "US market vs oracle divergence - 30s samples.csv"), rows,
         ["round_slug", "horizon", "ts_utc", "secs_to_end", "market_up_px", "best_bid", "best_ask", "proxy_gap_bps_vs_open", "oracle_walk_p_up", "market_minus_model"])
    wcsv(os.path.join(OUT, "US market vs oracle divergence - per round.csv"), per_round,
         ["round_slug", "horizon", "seconds_compared", "rms_divergence", "worst_divergence", "worst_at_secs_to_end", "worst_market_px", "worst_model_p",
          "proxy_sigma_usd_per_sqrt_s", "venue_up", "proxy_up", "match"])
    return per_round

# ------------------------------------------------------------------ 3. cross venue 15m
def cross_venue(days, bme_days):
    us = defaultdict(dict)                              # round_start -> {sec_into_round: up_px}
    for r in us_rows(days):
        if r["horizon"] != "15m": continue
        ts, s, up = fl(r["ts_unix"]), fl(r["secs_to_end"]), fl(r["up_px"])
        if None in (ts, s, up): continue
        start = int(round((ts + s - 900) / 900.0)) * 900          # snap to the 15-minute grid (ts and secs are rounded)
        if not 0 <= int(ts) - start < 900: continue
        us[start][int(ts) - start] = (up, fl(r["best_bid"]), fl(r["best_ask"]))
    if not us:
        wcsv(os.path.join(OUT, "cross venue 15m - per round.csv"), [], ["round_start_utc"]); return []
    com = defaultdict(dict)
    lo, hi = min(us) - 5, max(us) + 905
    for p in sorted(glob.glob(os.path.join(BME_DIR, "bookstate_1s_*.csv*")))[-bme_days:]:
        with opener(p) as f:
            for r in csv.DictReader(f):
                if r.get("series") != "15m" or r.get("token_idx") != "0": continue
                ts = fl(r["ts_ms"])
                if ts is None: continue
                ts /= 1000.0
                if not lo <= ts <= hi: continue
                start = int(ts // 900) * 900
                if start in us: com[start][int(ts) - start] = (fl(r["mid"]), fl(r["best_bid"]), fl(r["best_ask"]))
    chain = {}
    try:
        g = {}
        for ln in open(os.path.join(BT_CACHE, "gamma_15m.jsonl")):
            j = json.loads(ln); g[j["start"]] = j
        c = {}
        for ln in open(os.path.join(BT_CACHE, "chain.jsonl")):
            j = json.loads(ln); c[j["condition_id"]] = j["chain_outcome"]
        chain = {s: c.get(v.get("condition_id")) for s, v in g.items()}
    except Exception:
        pass
    res = {int(r["start_unix"]): r for r in us_round_results().values() if r.get("horizon") == "15m" and r.get("start_unix")}
    per_round, by_min = [], defaultdict(list)
    for start in sorted(us):
        common = sorted(set(us[start]) & set(com.get(start, {})))
        diffs = []
        for s in common:
            a, b = us[start][s][0], com[start][s][0]
            if a is None or b is None: continue
            diffs.append((s, a - b)); by_min[int((900 - s) // 60)].append(abs(a - b))
        if not diffs: continue
        w = max(diffs, key=lambda x: abs(x[1])); rr = res.get(start, {})
        us_up = {"1": "up", "0": "down"}.get(str(rr.get("venue_up", "")), "")
        per_round.append(dict(round_start_utc=iso(start), seconds_compared=len(diffs),
                              mean_abs_price_gap=round(sum(abs(d) for _, d in diffs) / len(diffs), 4),
                              max_abs_price_gap=round(abs(w[1]), 4), max_gap_at_secs_into_round=w[0], us_minus_com_at_max=round(w[1], 4),
                              us_outcome=us_up, com_chain_outcome=chain.get(start) or "",
                              venues_resolved_alike="" if not (us_up and chain.get(start)) else int(us_up == chain.get(start))))
    wcsv(os.path.join(OUT, "cross venue 15m - per round.csv"), per_round,
         ["round_start_utc", "seconds_compared", "mean_abs_price_gap", "max_abs_price_gap", "max_gap_at_secs_into_round", "us_minus_com_at_max",
          "us_outcome", "com_chain_outcome", "venues_resolved_alike"])
    mins = [dict(minutes_to_resolution=m, seconds_compared=len(v), mean_abs_price_gap=round(sum(v) / len(v), 4),
                 p90_abs_price_gap=round(sorted(v)[int(0.9 * (len(v) - 1))], 4)) for m, v in sorted(by_min.items())]
    wcsv(os.path.join(OUT, "cross venue 15m - by minute to resolution.csv"), mins,
         ["minutes_to_resolution", "seconds_compared", "mean_abs_price_gap", "p90_abs_price_gap"])
    return per_round

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=float, default=1.0); ap.add_argument("--bme-days", type=int, default=2)
    a = ap.parse_args()
    t0 = time.time()
    os_rows = one_sided(a.days, a.bme_days)
    dv = divergence(a.days)
    cv = cross_venue(a.days, a.bme_days)
    last = lambda v, h: next((r for r in os_rows if r["venue"] == v and r["horizon"] == h and r["minutes_to_resolution"] == 0), None)
    alike = [r for r in cv if r["venues_resolved_alike"] != ""]
    summ = dict(generated=iso(time.time()), window_days=a.days,
                one_sided_last_minute={f"{v} {h}": (last(v, h) or {}).get("one_sided_pct") for v, h in
                                       (("polymarket.us", "15m"), ("polymarket.us", "1h"), ("polymarket.com", "5m"), ("polymarket.com", "15m"))},
                one_sided_seconds_observed={f"{v} {h}": sum(r["seconds_observed"] for r in os_rows if r["venue"] == v and r["horizon"] == h)
                                            for v, h in (("polymarket.us", "15m"), ("polymarket.us", "1h"), ("polymarket.com", "5m"), ("polymarket.com", "15m"))},
                divergence_rounds=len(dv), divergence_median_rms=(sorted(r["rms_divergence"] for r in dv)[len(dv) // 2] if dv else None),
                divergence_worst=max(dv, key=lambda r: abs(r["worst_divergence"])) if dv else None,
                cross_venue_rounds=len(cv), cross_venue_mean_gap=(round(sum(r["mean_abs_price_gap"] for r in cv) / len(cv), 4) if cv else None),
                cross_venue_resolved_compared=len(alike), cross_venue_resolved_differently=sum(1 for r in alike if r["venues_resolved_alike"] == 0),
                seconds=round(time.time() - t0, 1))
    json.dump(summ, open(os.path.join(OUT, "summary.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))

if __name__ == "__main__":
    main()
