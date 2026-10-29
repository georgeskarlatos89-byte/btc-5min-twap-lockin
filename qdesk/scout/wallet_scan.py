#!/usr/bin/env python3
"""wallet_scan.py - nightly scan of <= 20 top wallets: who makes money in BTC 5m and how (maker/taker, rebates).

  python3 wallet_scan.py [--date YYYYMMDD] [--dry-run] [--max-wallets 20] [--hours 24]

Seeds = Moon Dev profitable-traders / top-traders (today's archive from moondev_pull.py when present)
        U data-api leaderboard. Verified 2026-10-29: /leaderboard?window=7d&limit=50 -> 404;
        /v1/leaderboard?timePeriod=WEEK&limit=50&orderBy=PNL -> [{"rank","proxyWallet","userName","vol","pnl"}].
Per wallet: last --hours of /activity (1-hour windows, offset cap 5,000 per window - copied get/pull logic from
C:\\g\\tlfix\\wallet_audits\\wallet_pull.py) + the taker-only /trades feed (cap 10,000), classified with kind() and the
taker-join of C:\\g\\tlfix\\wallet_audits\\wallet_analyze.py, plus the official pnl
/v2/user-pnl?user=<addr>&interval=all&fidelity=1d (cumulative daily points: trade_pnl, fees_paid, taker_rebate,
maker_rebate, settled_pnl -> 7-day deltas = last point minus the point 7 days earlier).
Output data/ext/wallets/scan_YYYYMMDD.json: {"date","scanned","budget":{...},"wallets":[{wallet, pnl_7d, fees_7d,
rebates_7d, settled_7d, btc5m_share, maker_share, rounds_per_day, fills, class}]}.
Class: maker_share>0.9 & rounds_per_day>100 -> speed_maker; rebates_7d > max(0, settled_7d - rebates_7d) -> rebate_farm;
btc5m_share<0.5 or fills<50 -> unknown; else directional.  Budget: <= 15 min, <= 2,000 requests (then stop, partial file).
"""
import argparse
import collections
import datetime
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

BASE = "https://data-api.polymarket.com"
COLS = ["timestamp", "type", "side", "outcome", "size", "price", "usdcSize", "title", "slug", "conditionId", "asset", "transactionHash"]
BUDGET_REQ = 2000
BUDGET_S = 15 * 60
_budget = {"req0": 0, "t0": 0.0}


def budget_left():
    return (U.REQUESTS["n"] - _budget["req0"]) < BUDGET_REQ and (time.time() - _budget["t0"]) < BUDGET_S


# copied from C:\g\tlfix\wallet_audits\wallet_pull.py (get) - tuple return instead of raising
def get(url, tries=6):
    ok, d, note = U.http_json(url, tries=min(tries, 5), timeout=30)
    return d if ok else None


# copied from C:\g\tlfix\wallet_audits\wallet_pull.py (pull) - rows returned in memory instead of a CSV file
def pull(endpoint, w, t0, t1, log, win=3600, cap=5000):
    rows = []; wins = 0; capped = 0
    s = t0
    while s < t1 and budget_left():
        e = min(s + win, t1); off = 0
        while True:
            q = urllib.parse.urlencode(dict(user=w, limit=500, offset=off, start=s, end=e, sortBy="TIMESTAMP", sortDirection="ASC"))
            d = get(f"{BASE}/{endpoint}?{q}")
            if not isinstance(d, list):
                break
            rows.extend({c: r.get(c) for c in COLS} for r in d)
            off += len(d)
            if len(d) < 500:
                break
            if off >= cap:
                capped += 1; break
            if not budget_left():
                break
        wins += 1; s = e
    if capped:
        log(f"{endpoint} {w[:10]}: {capped} windows hit the {cap} offset cap, rows lost")
    return rows


# copied from C:\g\tlfix\wallet_audits\wallet_analyze.py (kind)
def kind(title):
    t = title or ""
    for name, key in (("BTC 5m", "Bitcoin Up or Down"), ("ETH 5m", "Ethereum Up or Down"), ("SOL 5m", "Solana Up or Down"), ("XRP 5m", "XRP Up or Down")):
        if key in t: return name
    if "Up or Down" in t: return "other Up/Down"
    if t.startswith("LoL") or "esports" in t.lower(): return "esports"
    return "other"


# copied from C:\g\tlfix\wallet_audits\wallet_analyze.py (tkey)
def tkey(ts, asset, side, size, price):
    return (int(float(ts)), asset, side, round(float(size or 0), 4), round(float(price or 0), 4))


slug_t = re.compile(r"-(\d{10})$")


def analyze(rows, taker_rows, hours):
    """Maker share / BTC-5m share / rounds per day from the activity rows (wallet_analyze.py taker-join logic)."""
    takers = set(tkey(r["timestamp"], r["asset"], r["side"], r["size"], r["price"]) for r in taker_rows if r.get("timestamp"))
    fills = 0; taker = 0; btc = 0; btc_rounds = set()
    for r in rows:
        if r.get("type") != "TRADE":
            continue
        fills += 1
        if tkey(r["timestamp"], r["asset"], r["side"], r["size"], r["price"]) in takers:
            taker += 1
        k = kind(r.get("title"))
        if k == "BTC 5m":
            btc += 1
            g = slug_t.search(r.get("slug") or "")
            if g and "updown" in (r.get("slug") or ""):
                btc_rounds.add(int(g.group(1)))
    return {"fills": fills, "taker_fills": taker, "maker_share": round(1 - taker / fills, 4) if fills else None,
            "btc5m_share": round(btc / fills, 4) if fills else None,
            "rounds_per_day": round(len(btc_rounds) * 24.0 / max(1, hours), 2)}


def user_pnl_7d(w, now_ts):
    """7-day deltas from the official cumulative daily pnl points -> dict or None."""
    d = get(f"{BASE}/v2/user-pnl?user={w}&interval=all&fidelity=1d")
    try:
        pts = d["data"]["points"]
    except Exception:
        return None
    if not pts:
        return None
    pts = sorted(pts, key=lambda p: p["timestamp"])
    last = pts[-1]; cutoff = now_ts - 7 * 86400
    base = None
    for p in pts:
        if p["timestamp"] <= cutoff:
            base = p
    def delta(k):
        try:
            return round(float(last.get(k) or 0) - float((base or {}).get(k) or 0), 2)
        except Exception:
            return None
    reb = (delta("taker_rebate") or 0) + (delta("maker_rebate") or 0)
    return {"pnl_7d": delta("trade_pnl"), "fees_7d": delta("fees_paid"), "rebates_7d": round(reb, 2),
            "settled_7d": delta("settled_pnl"), "points": len(pts), "base_point": bool(base)}


def classify(a, p):
    ms = a.get("maker_share"); rpd = a.get("rounds_per_day") or 0; fills = a.get("fills") or 0; b5 = a.get("btc5m_share")
    if ms is not None and ms > 0.9 and rpd > 100:
        return "speed_maker"
    if p and p.get("rebates_7d") is not None and p.get("settled_7d") is not None and p["rebates_7d"] > max(0.0, p["settled_7d"] - p["rebates_7d"]):
        return "rebate_farm"
    if b5 is None or b5 < 0.5 or fills < 50:
        return "unknown"
    return "directional"


def seeds(date, log, dry_run=False):
    """Moon Dev archives (today) U leaderboard. Returns ordered unique wallet list."""
    out = []; seen = set()

    def add(ws):
        for w in ws:
            w = (w or "").lower()
            if w.startswith("0x") and len(w) == 42 and w not in seen:
                seen.add(w); out.append(w)
    try:
        from qdesk.scout import moondev_pull as MD
        for name in ("profitable_traders", "top_traders"):
            d = MD.load_raw(name, date) or MD.load_raw(name, U.yesterday(date))
            if d:
                ws = MD.wallet_seeds(d, 50); add(ws); log(f"seeds moondev {name}: {len(ws)}")
    except Exception as e:
        log(f"seeds moondev unavailable: {type(e).__name__}")
    if dry_run:
        return out
    d = get(f"{BASE}/v1/leaderboard?timePeriod=WEEK&limit=50&orderBy=PNL")
    if not isinstance(d, list):
        d = get(f"{BASE}/leaderboard?window=7d&limit=50")
    if isinstance(d, list):
        ws = [x.get("proxyWallet") or x.get("wallet") or x.get("address") for x in d if isinstance(x, dict)]
        add(ws); log(f"seeds leaderboard: {len(ws)}")
    else:
        log("seeds leaderboard: unavailable")
    return out


def run(date=None, dry_run=False, max_wallets=20, hours=24, log=U.log):
    date = date or U.today()
    _budget["req0"] = U.REQUESTS["n"]; _budget["t0"] = time.time()
    out_path = U.data_path("ext", "wallets", f"scan_{date}.json")
    doc = {"date": date, "generated": U.iso(), "scanned": 0, "seeds": 0, "budget": {}, "wallets": [], "note": ""}
    if dry_run:
        doc["note"] = "dry"; log("wallet_scan dry-run: no network")
        return False, doc, "dry"
    ws = seeds(date, log)[:max_wallets]
    doc["seeds"] = len(ws)
    now_ts = int(time.time()); t0 = now_ts - hours * 3600
    for w in ws:
        if not budget_left():
            doc["note"] = "budget exhausted"; break
        try:
            rows = pull("activity", w, t0, now_ts, log, cap=5000)
            trows = pull("trades", w, t0, now_ts, log, cap=10000) if budget_left() else []
            a = analyze(rows, trows, hours)
            p = user_pnl_7d(w, now_ts) if budget_left() else None
            rec = {"wallet": w, "pnl_7d": (p or {}).get("pnl_7d"), "fees_7d": (p or {}).get("fees_7d"),
                   "rebates_7d": (p or {}).get("rebates_7d"), "settled_7d": (p or {}).get("settled_7d"),
                   "btc5m_share": a["btc5m_share"], "maker_share": a["maker_share"], "rounds_per_day": a["rounds_per_day"],
                   "fills": a["fills"], "activity_rows": len(rows), "class": classify(a, p), "pnl_label": "MEASURED" if p else "NA"}
            doc["wallets"].append(rec); doc["scanned"] += 1
            log(f"wallet {w[:10]}: fills {a['fills']} maker {a['maker_share']} btc5m {a['btc5m_share']} rpd {a['rounds_per_day']} pnl7d {rec['pnl_7d']} -> {rec['class']}")
        except Exception as e:
            log(f"wallet {w[:10]} failed: {type(e).__name__}: {str(e)[:80]}")
    doc["budget"] = {"requests": U.REQUESTS["n"] - _budget["req0"], "seconds": round(time.time() - _budget["t0"], 1)}
    doc["wallets"].sort(key=lambda r: -(r.get("pnl_7d") or 0))
    U.save_json(out_path, doc)
    log(f"wallet_scan {date}: scanned {doc['scanned']} of {len(ws)} seeds, {doc['budget']} -> {out_path}")
    return doc["scanned"] > 0, doc, doc["note"] or "ok"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-wallets", type=int, default=20); ap.add_argument("--hours", type=int, default=24)
    a = ap.parse_args()
    ok, doc, note = run(a.date, a.dry_run, a.max_wallets, a.hours)
    print(ok, note, doc["scanned"], [(r["wallet"][:10], r["class"]) for r in doc["wallets"]])


if __name__ == "__main__":
    main()
