#!/usr/bin/env python3
"""prices_archive.py - archive the 1-minute CLOB price history of every round closed in the last 24 h.

  python3 prices_archive.py [--date YYYYMMDD] [--dry-run] [--max-rounds N]

Polymarket purges prices-history after ~21 days; this keeps it forever (~0.5 MB/day gz).
Verified 2026-10-29: GET https://clob.polymarket.com/prices-history?market=<token_id>&fidelity=1&startTs=<s>&endTs=<e>
  -> {"history":[{"t": epoch_s, "p": price}, ...]}   (parameter names market / fidelity / startTs / endTs;
  adding interval=max together with startTs returns an empty history, so interval is never sent).
Rounds come from data/tokens/tokens_btc{5m,15m}_live.json (gamma_rounds.py) for the day before --date.
Writes data/prices1m/day_YYYYMMDD.csv.gz, header asset,t,p; assets already present are skipped (idempotent).
run() returns (ok, {"days_archived", "rounds_with_history", "assets_new", "requests"}, note).
"""
import argparse
import csv
import glob
import gzip
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

CLOB = "https://clob.polymarket.com"
STEP = {"5m": 300, "15m": 900}


def prices_history(token, start_ts, end_ts, fidelity=1):
    """(ok, [(t, p), ...], note)"""
    ok, d, note = U.http_json(f"{CLOB}/prices-history?market={token}&fidelity={fidelity}&startTs={int(start_ts)}&endTs={int(end_ts)}")
    if not ok:
        return False, [], note
    try:
        return True, [(int(h["t"]), float(h["p"])) for h in d.get("history", [])], "ok"
    except Exception as e:
        return False, [], f"parse: {type(e).__name__}"


def day_file(day):
    return U.data_path("prices1m", f"day_{day}.csv.gz")


def read_day(day):
    """{asset: [(t,p)...]} already archived for the day."""
    p = day_file(day); out = {}
    if os.path.exists(p):
        try:
            with gzip.open(p, "rt", newline="") as f:
                for r in csv.DictReader(f):
                    out.setdefault(r["asset"], []).append((int(r["t"]), float(r["p"])))
        except Exception:
            pass
    return out


def write_day(day, data):
    p = day_file(day); tmp = p + ".tmp"
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["asset", "t", "p"])
    for a in sorted(data):
        for t, pr in sorted(set(data[a])):
            w.writerow([a, t, pr])
    with gzip.open(tmp, "wt", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, p)
    return p


def days_archived():
    return len(glob.glob(os.path.join(U.data_dir(), "prices1m", "day_*.csv.gz")))


def rounds_for_day(day):
    """[(start, market, up_asset, down_asset)] of the day from the live token maps."""
    t0 = U.parse_day(day); t1 = t0 + 86400; out = {}
    for variant in ("5m", "15m"):
        tok = U.load_json(U.data_path("tokens", f"tokens_btc{variant}_live.json", mkdir=False), {}) or {}
        for a, r in tok.items():
            s = int(r.get("start", 0))
            if t0 <= s < t1 and r.get("outcome") == "Up":
                out[(variant, s)] = (s, variant, a, r.get("other"))
    return sorted(out.values())


def run(date=None, dry_run=False, max_rounds=None, log=U.log):
    day = U.yesterday(date)
    rounds = rounds_for_day(day)
    if max_rounds:
        rounds = rounds[:max_rounds]
    summ = {"day": day, "days_archived": days_archived(), "rounds": len(rounds), "rounds_with_history": 0, "assets_new": 0, "requests": 0, "failed": 0}
    if dry_run:
        log(f"prices_archive dry-run: {len(rounds)} rounds listed for {day}, no network")
        return False, summ, "dry"
    if not rounds:
        return False, summ, "no rounds in the live token maps for " + day
    data = read_day(day)
    changed = False
    for s, variant, up, dn in rounds:
        T = STEP[variant]; got = False
        for a in (up, dn):
            if not a:
                continue
            if a in data:
                got = got or bool(data[a]); continue
            ok, hist, note = prices_history(a, s - 60, s + T + 60)
            summ["requests"] += 1
            if not ok:
                summ["failed"] += 1; continue
            data[a] = hist; changed = True; summ["assets_new"] += 1
            got = got or bool(hist)
        if got:
            summ["rounds_with_history"] += 1
    if changed:
        write_day(day, data)
    summ["days_archived"] = days_archived()
    log(f"prices_archive {day}: rounds {len(rounds)}, with history {summ['rounds_with_history']}, new assets {summ['assets_new']}, "
        f"requests {summ['requests']}, failed {summ['failed']}, days archived {summ['days_archived']}")
    return summ["failed"] < max(1, len(rounds)), summ, "ok" if summ["failed"] == 0 else f"{summ['failed']} requests failed"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=None)
    a = ap.parse_args()
    print(run(a.date, a.dry_run, a.max_rounds))


if __name__ == "__main__":
    main()
