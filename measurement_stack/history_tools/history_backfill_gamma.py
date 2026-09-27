#!/usr/bin/env python3
"""
history_backfill_gamma.py — DEEP round-history backfill via the Gamma series
endpoint. Complements history_backfill.py (past-results, ~7-day retention):

  past-results : full-precision open/close + outcome, last ~7 days ONLY
  gamma series : outcome (+closedTime) ONLY, but the ENTIRE series history
                 (BTC 5m series 10684 started 2025-12-18 — ~9 months deep)

How it works: each 5m/15m round is a gamma EVENT in a recurring series
(slug btc-updown-5m-<unixStartTs>). We page the series with daily
start_date windows (offset paging 422s past ~2000) and read the winner from
the nested market's outcomePrices ("1"/"0" over ["Up","Down"]).

NOTE: gamma keeps NO open/close reference prices for old rounds and the old
markets' CLOB price-history / trade tape is purged (probed: 0 points at 60d).
For reference prices, only the 7-day past-results window exists.

RELIABILITY WARNING (audited 2026-09-27): gamma's outcomePrices for these
auto-resolved markets is INTERMITTENTLY stale (observed serving the wrong side
for settled rounds, self-correcting within minutes). Ground truth is the
on-chain CTF payout vector: payoutNumerators(conditionId, 0|1) on
0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 (Polygon) — index 0 = "Up".
Use chain_verify.py to re-label a corpus against the chain.

Output: history/{SYMBOL}_{VARIANT}_long.csv
        (start_time,end_time,outcome,closed_time,condition_id)
"""
import argparse, csv, json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
os.makedirs(HERE, exist_ok=True)
GAMMA = "https://gamma-api.polymarket.com"
SERIES = {("BTC", "fiveminute"): "10684", ("BTC", "fifteen"): None}  # 15m resolved at runtime
SERIES_SLUG = {"fiveminute": "btc-up-or-down-5m", "fifteen": "btc-up-or-down-15m"}
T_VARIANT = {"fiveminute": 300, "fifteen": 900}
SLEEP = 0.15

def hj(u, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(1 + i)

def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def outcome_of(ev):
    try:
        m = ev["markets"][0]
        op = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
        oc = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
        if "1" in op: return oc[op.index("1")]
    except Exception:
        pass
    return ""

def market_of(ev):
    try: return ev["markets"][0]
    except Exception: return {}

def resolve_series_id(symbol, variant):
    sid = SERIES.get((symbol, variant))
    if sid: return sid
    # find any event of the series and read its series id
    now = int(time.time()); T = T_VARIANT[variant]
    ts = (now // T) * T - 2 * T
    sym = symbol.lower()
    tag = "5m" if variant == "fiveminute" else "15m"
    evs = hj(f"{GAMMA}/events?slug={sym}-updown-{tag}-{ts}")
    if not evs:
        sys.exit(f"could not find a live event to resolve series id for {symbol} {variant}")
    sid = str(evs[0]["series"][0]["id"])
    SERIES[(symbol, variant)] = sid
    return sid

def backfill(symbol, variant, start_date, end_date):
    sid = resolve_series_id(symbol, variant)
    T = T_VARIANT[variant]
    rows, seen = [], set()
    d = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = time.time()
    while d <= end:
        nxt = d + timedelta(days=1)
        day_n = 0
        off = 0
        while True:                                           # a day holds 288 (5m) / 96 (15m) rounds
            q = urllib.parse.urlencode(dict(series_id=sid, limit=100, offset=off, order="id", ascending="true",
                                            start_date_min=d.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                            start_date_max=nxt.strftime("%Y-%m-%dT%H:%M:%SZ")))
            res = hj(f"{GAMMA}/events?{q}")
            for ev in res:
                slug = ev.get("slug", "")
                try: ts = int(slug.rsplit("-", 1)[1])
                except Exception: continue
                if ts in seen or ts + T > now: continue      # skip dupes + not-yet-settled
                seen.add(ts)
                m = market_of(ev)
                rows.append(dict(start_time=iso(ts), end_time=iso(ts + T),
                                 outcome=outcome_of(ev).lower(), closed_time=ev.get("closedTime", ""),
                                 condition_id=m.get("conditionId", "")))
            day_n += len(res)
            if len(res) < 100 or off > 1000: break
            off += 100
            time.sleep(SLEEP)
        print(f"  {d:%Y-%m-%d}: {day_n} events (total {len(rows)})")
        d = nxt
        time.sleep(SLEEP)
    rows.sort(key=lambda r: r["start_time"])
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--variant", default="fiveminute", choices=["fiveminute", "fifteen"])
    ap.add_argument("--start", default="2025-12-18", help="first day (series start for BTC 5m)")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--incremental", action="store_true",
                    help="merge into the existing long CSV instead of rewriting from --start "
                         "(refetches from newest stored round minus 1 day; used by the daily cron)")
    a = ap.parse_args()
    out = os.path.join(HERE, f"{a.symbol}_{a.variant}_long.csv")
    existing = []
    if a.incremental and os.path.exists(out):
        existing = list(csv.DictReader(open(out)))
        if existing:
            newest = max(r["start_time"] for r in existing)
            a.start = (datetime.fromisoformat(newest.replace("Z", "+00:00")) - timedelta(days=1)).strftime("%Y-%m-%d")
            print(f"incremental: extending from {a.start} (existing {len(existing)} rows)")
    print(f"deep backfill {a.symbol} {a.variant} {a.start}..{a.end} via gamma series ...")
    rows = backfill(a.symbol, a.variant, a.start, a.end)
    if existing:
        merged = {r["start_time"]: dict(start_time=r["start_time"], end_time=r["end_time"],
                                         outcome=r["outcome"], closed_time=r.get("closed_time", ""),
                                         condition_id=r.get("condition_id", "")) for r in existing}
        for r in rows: merged[r["start_time"]] = r    # fresh fetch wins
        rows = sorted(merged.values(), key=lambda r: r["start_time"])
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_time", "end_time", "outcome", "closed_time", "condition_id"])
        for r in rows: w.writerow([r["start_time"], r["end_time"], r["outcome"],
                                   r.get("closed_time", ""), r.get("condition_id", "")])
    ups = sum(1 for r in rows if r["outcome"] == "up")
    downs = sum(1 for r in rows if r["outcome"] == "down")
    unknown = len(rows) - ups - downs
    # gaps: rounds present in the series grid but missing from results
    ts = sorted(int(datetime.fromisoformat(r["start_time"].replace("Z", "+00:00")).timestamp()) for r in rows)
    T = T_VARIANT[a.variant]
    missing = sum(1 for x, y in zip(ts, ts[1:]) if y - x > T)
    st = dict(rounds=len(rows), up=ups, down=downs, unknown=unknown,
              up_rate=round(ups / max(ups + downs, 1), 4),
              grid_gaps=missing, first=rows[0]["start_time"] if rows else None,
              last=rows[-1]["start_time"] if rows else None)
    json.dump(st, open(out.replace(".csv", "_stats.json"), "w"), indent=1)
    print(json.dumps(st, indent=1))
    print(f"saved {out}")

if __name__ == "__main__":
    main()
