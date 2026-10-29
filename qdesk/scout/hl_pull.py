#!/usr/bin/env python3
"""hl_pull.py - Hyperliquid public info API (no keys): BTC funding, open interest, mark price.

  python3 hl_pull.py [--date YYYYMMDD] [--dry-run]

Verified 2026-10-29: POST https://api.hyperliquid.xyz/info
  {"type":"metaAndAssetCtxs"} -> [ {"universe":[{"name":"BTC",..},..]}, [ {"funding","openInterest","markPx","oraclePx",..}, ..] ]
  {"type":"fundingHistory","coin":"BTC","startTime":<ms>[,"endTime":<ms>]} -> [{"coin","fundingRate","premium","time"(ms)}]
Writes data/ext/hl/funding_YYYYMMDD.csv.gz with header t,funding,oi_usd,mark (hourly funding rows have empty
oi_usd/mark; the snapshot row carries all three). Every function returns (ok, data, note) and never raises.
"""
import argparse
import csv
import gzip
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

INFO = "https://api.hyperliquid.xyz/info"


def meta_btc(dry_run=False):
    """(ok, {"t","funding","oi","oi_usd","mark"}, note) from metaAndAssetCtxs."""
    if dry_run:
        return False, None, "dry"
    ok, d, note = U.http_json(INFO, data={"type": "metaAndAssetCtxs"}, timeout=15)
    if not ok:
        return False, None, note
    try:
        uni = d[0]["universe"]; ctxs = d[1]
        i = next(i for i, u in enumerate(uni) if u.get("name") == "BTC")
        c = ctxs[i]
        mark = float(c["markPx"]); oi = float(c["openInterest"])
        return True, {"t": int(time.time()), "funding": float(c["funding"]), "oi": oi, "oi_usd": oi * mark, "mark": mark}, "ok"
    except Exception as e:
        return False, None, f"parse: {type(e).__name__}: {str(e)[:60]}"


def funding_history(start_ms, end_ms=None, dry_run=False):
    """(ok, [{"t": s, "funding": rate}], note) for BTC between start_ms and end_ms."""
    if dry_run:
        return False, None, "dry"
    body = {"type": "fundingHistory", "coin": "BTC", "startTime": int(start_ms)}
    if end_ms:
        body["endTime"] = int(end_ms)
    ok, d, note = U.http_json(INFO, data=body, timeout=20)
    if not ok:
        return False, None, note
    try:
        rows = [{"t": int(float(r["time"]) // 1000), "funding": float(r["fundingRate"])} for r in d if r.get("coin") == "BTC"]
        return True, rows, "ok"
    except Exception as e:
        return False, None, f"parse: {type(e).__name__}: {str(e)[:60]}"


def out_path(day):
    return U.data_path("ext", "hl", f"funding_{day}.csv.gz")


def read_day(day):
    p = out_path(day)
    rows = {}
    if os.path.exists(p):
        try:
            with gzip.open(p, "rt", newline="") as f:
                for r in csv.DictReader(f):
                    rows[int(r["t"])] = r
        except Exception:
            pass
    return rows


def write_day(day, rows):
    p = out_path(day); tmp = p + ".tmp"
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["t", "funding", "oi_usd", "mark"])
    for t in sorted(rows):
        r = rows[t]
        w.writerow([t, r.get("funding", ""), r.get("oi_usd", ""), r.get("mark", "")])
    with gzip.open(tmp, "wt", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, p)
    return p


def run(date=None, dry_run=False, log=U.log):
    """Backfill yesterday's hourly funding + today's snapshot. Returns (ok, summary, note)."""
    day = U.yesterday(date)
    if dry_run:
        log("hl_pull dry-run: no network")
        return False, {"rows": 0, "day": day}, "dry"
    t0 = U.parse_day(day); t1 = t0 + 86400
    rows = read_day(day)
    ok_h, hist, note_h = funding_history(t0 * 1000, t1 * 1000)
    if ok_h:
        for r in hist:
            rows.setdefault(r["t"], {})["funding"] = r["funding"]
    ok_m, snap, note_m = meta_btc()
    if ok_m:
        rows[snap["t"]] = {"funding": snap["funding"], "oi_usd": round(snap["oi_usd"], 2), "mark": snap["mark"]}
    if not rows:
        return False, {"rows": 0, "day": day}, f"history: {note_h}; meta: {note_m}"
    p = write_day(day, rows)
    summ = {"rows": len(rows), "day": day, "file": p, "history_ok": ok_h, "meta_ok": ok_m,
            "funding_1h": snap["funding"] if ok_m else None, "oi_usd": round(snap["oi_usd"], 2) if ok_m else None,
            "mark": snap["mark"] if ok_m else None}
    log(f"hl_pull {day}: {len(rows)} rows (history {note_h}, meta {note_m}) -> {p}")
    return (ok_h or ok_m), summ, f"history: {note_h}; meta: {note_m}"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(run(a.date, a.dry_run))


if __name__ == "__main__":
    main()
