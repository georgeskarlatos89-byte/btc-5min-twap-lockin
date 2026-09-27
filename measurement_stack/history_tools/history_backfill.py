#!/usr/bin/env python3
"""
history_backfill.py — pull the UNDOCUMENTED full-precision round history from
polymarket.com/api/past-results and turn it into calibration stats.

ENDPOINT SEMANTICS (probed 2026-09-26):
  GET /api/past-results?symbol=X&variant=fiveminute|fifteenminute&assetType=crypto
      &currentEventStartTime=<ISO aligned to the round grid>
  -> the 4 most recent SETTLED rounds ending at/before the given time,
     each with: startTime, endTime, openPrice, closePrice, outcome, percentChange
     (open/close at FULL precision).
  Depth: ~1 week works, 30 days returns empty. Page backwards by feeding the
  oldest returned startTime back in; dedupe on startTime.

FIELD SEMANTICS (corrected after the on-chain resolution audit 2026-09-27):
  openPrice  = Chainlink TWAP-60s stream value at window start (= previous
               round's closePrice; the chain is exact 99.65% of the time).
  closePrice = stream value at window END. NOT the settlement value.
  outcome    = DERIVED display field == sign(closePrice-openPrice). The actual
               settlement compares the WINDOW TWAP to the open print; on razor
               rounds the derived outcome is wrong 28-35% of the time (see
               history/HISTORY-FINDINGS.md §4). For authoritative outcomes use
               history_backfill_gamma.py + chain_verify.py.

OUTPUT (maker_lab/history/):
  {symbol}_{variant}.csv        one row per round (raw + derived)
  {symbol}_{variant}_stats.json calibration stats
"""
import argparse, csv, json, os, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
os.makedirs(HERE, exist_ok=True)
T_VARIANT = {"fiveminute": 300, "fifteen": 900}   # variant strings as the endpoint expects them
SLEEP = 0.12

def hj(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_page(symbol, variant, x):
    u = (f"https://polymarket.com/api/past-results?symbol={symbol}&variant={variant}"
         f"&assetType=crypto&currentEventStartTime={iso(x)}")
    return hj(u)["data"]["results"]

def backfill(symbol, variant, days):
    T = T_VARIANT[variant]
    now = int(time.time())
    horizon = now - days * 86400
    x = (now // T) * T
    seen = {}
    empty_streak = 0
    while x > horizon and empty_streak < 3:
        try:
            page = fetch_page(symbol, variant, x)
        except Exception as e:
            print(f"  warn {iso(x)}: {e}; retry once"); time.sleep(1)
            try: page = fetch_page(symbol, variant, x)
            except Exception: page = []
        new = [r for r in page if r["startTime"] not in seen]
        for r in page: seen[r["startTime"]] = r
        if not new: empty_streak += 1
        else: empty_streak = 0
        if page:
            oldest = min(r["startTime"] for r in page)
            x = int(datetime.fromisoformat(oldest.replace("Z", "+00:00")).timestamp())
        else:
            x -= 4 * T
        time.sleep(SLEEP)
    rows = sorted(seen.values(), key=lambda r: r["startTime"])
    rows = [r for r in rows if int(datetime.fromisoformat(r["startTime"].replace("Z", "+00:00")).timestamp()) >= horizon]
    return rows

def stats(rows):
    n = len(rows)
    chain = sum(1 for a, b in zip(rows, rows[1:]) if a["closePrice"] == b["openPrice"])
    tail_disagree = 0; razor1 = 0; razor05 = 0
    gaps = []
    for r in rows:
        g = (r["closePrice"] - r["openPrice"]) / r["openPrice"] * 1e4   # tail gap, bps
        gaps.append(abs(g))
        s = "up" if g >= 0 else "down"
        if s != r["outcome"]: tail_disagree += 1
        if abs(g) < 1.0: razor1 += 1
        if abs(g) < 0.5: razor05 += 1
    gaps.sort()
    def pct(p): return gaps[min(n - 1, int(p * n))] if n else 0.0
    return dict(rounds=n,
                chain_open_eq_prev_close=f"{chain}/{max(n-1,0)}",
                tail_vs_settlement_disagree=f"{tail_disagree}/{n}",  # NOTE: tautology vs past-results (see HISTORY-FINDINGS §4)
                tail_disagree_rate=round(tail_disagree / n, 4) if n else None,
                tail_gap_bps_p50=round(pct(0.5) or 0.0, 2), tail_gap_bps_p90=round(pct(0.9) or 0.0, 2),
                tail_gap_bps_max=round(pct(0.999) or 0.0, 2),
                razor_under_1bps=razor1, razor_under_05bps=razor05,
                first=rows[0]["startTime"] if rows else None,
                last=rows[-1]["startTime"] if rows else None)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--variant", default="fiveminute", choices=list(T_VARIANT))
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    print(f"backfilling {a.symbol} {a.variant} x {a.days}d ...")
    rows = backfill(a.symbol, a.variant, a.days)
    csv_p = os.path.join(HERE, f"{a.symbol}_{a.variant}.csv")
    with open(csv_p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_time", "end_time", "open_price_full", "close_price_full",
                    "outcome", "percent_change", "tail_gap_bps"])
        for r in rows:
            g = (r["closePrice"] - r["openPrice"]) / r["openPrice"] * 1e4
            w.writerow([r["startTime"], r["endTime"], repr(r["openPrice"]),
                        repr(r["closePrice"]), r["outcome"], r["percentChange"], round(g, 3)])
    st = stats(rows)
    json.dump(st, open(os.path.join(HERE, f"{a.symbol}_{a.variant}_stats.json"), "w"), indent=1)
    print(json.dumps(st, indent=1))
    print(f"saved {csv_p}")

if __name__ == "__main__":
    main()
