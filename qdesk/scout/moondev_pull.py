#!/usr/bin/env python3
"""moondev_pull.py - Moon Dev API client (urllib, X-API-Key from ~/qdesk/.env) + raw JSON archive.

  python3 moondev_pull.py [--date YYYYMMDD] [--dry-run] [--only totals,liq_10m,...]

Env (~/qdesk/.env, read with python-dotenv, never printed): MOONDEV_API_KEY, MOONDEV_BASE_URL (default
https://api.moondev.com). Only this module and llm/client.py may read that file (INTERFACES §11).
api_get(path) mirrors moondev_24h_harvest.py's api_get (browser UA required: Cloudflare 1010 blocks python UAs)
but returns (ok, data, note) and never raises. 429 -> one slow retry, then give up (the key is shared with the
live bots that poll every 2 s; a retry storm only makes it worse).
Endpoints (names used by the strategy bots):
  totals             /api/all_liquidations/totals.json      (windows -> long/short USD, polled by the recorder every 30 s)
  liq_10m            /api/all_liquidations/10m.json
  liq_30d            /api/all_liquidations/30d.json
  imbalance_1h       /api/imbalance/1h.json
  hip3_24h           /api/hip3_liquidations/24h.json
  profitable_traders /api/poly/profitable-traders           (wallet seeds; {"traders":[{"wallet","pnl_7d",..}]} seen 2026-06-14)
  top_traders        /api/poly/whales/top-traders
Raw JSON goes to data/ext/moondev/<name>_YYYYMMDD.json.gz. --dry-run: no network, every call returns (False, None, "dry").
"""
import argparse
import gzip
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

ENV_PATH = os.path.expanduser("~/qdesk/.env")
ENDPOINTS = {
    "totals": "/api/all_liquidations/totals.json",
    "liq_10m": "/api/all_liquidations/10m.json",
    "liq_30d": "/api/all_liquidations/30d.json",
    "imbalance_1h": "/api/imbalance/1h.json",
    "hip3_24h": "/api/hip3_liquidations/24h.json",
    "profitable_traders": "/api/poly/profitable-traders",
    "top_traders": "/api/poly/whales/top-traders",
}
# copied from moondev_24h_harvest.py (HEADERS): the browser UA is required by Moon Dev's Cloudflare
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_cfg = {}


def config():
    """{"key", "base"} from ~/qdesk/.env (cached). Missing file/key -> key None (callers report 'no key')."""
    if _cfg:
        return _cfg
    key = None; base = "https://api.moondev.com"
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(ENV_PATH) if os.path.exists(ENV_PATH) else {}
    except Exception:
        vals = {}
        if os.path.exists(ENV_PATH):
            for line in open(ENV_PATH, encoding="utf-8", errors="ignore"):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1); vals[k.strip()] = v.strip().strip('"').strip("'")
    key = (vals.get("MOONDEV_API_KEY") or os.environ.get("MOONDEV_API_KEY") or "").strip() or None
    base = (vals.get("MOONDEV_BASE_URL") or os.environ.get("MOONDEV_BASE_URL") or base).rstrip("/")
    _cfg.update(key=key, base=base)
    return _cfg


# adapted from moondev_24h_harvest.py api_get (requests -> urllib; returns a tuple instead of raising)
def api_get(path, timeout=30, dry_run=False):
    """GET <base><path> with X-API-Key. Returns (ok, data, note); never raises; no key -> (False, None, 'no key')."""
    if dry_run:
        return False, None, "dry"
    c = config()
    if not c["key"]:
        return False, None, "no key (MOONDEV_API_KEY missing in ~/qdesk/.env)"
    h = {"User-Agent": BROWSER_UA, "X-API-Key": c["key"], "Accept": "application/json"}
    ok, d, note = U.http_json(c["base"] + path, headers=h, tries=2, timeout=timeout, pace=1.0)
    return ok, d, note


def save_raw(name, day, data):
    p = U.data_path("ext", "moondev", f"{name}_{day}.json.gz"); tmp = p + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, p)
    return p


def load_raw(name, day):
    p = U.data_path("ext", "moondev", f"{name}_{day}.json.gz", mkdir=False)
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _count(d):
    if isinstance(d, list):
        return len(d)
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list):
                return len(v)
        return len(d)
    return 0


def pull(name, date=None, dry_run=False):
    """Fetch one endpoint and archive it. Returns (ok, data, note)."""
    day = date or U.today()
    path = ENDPOINTS.get(name)
    if not path:
        return False, None, f"unknown endpoint {name}"
    ok, d, note = api_get(path, dry_run=dry_run)
    if ok:
        try:
            save_raw(name, day, d)
        except Exception as e:
            note = f"saved failed: {type(e).__name__}"
    return ok, d, note


def pull_all(date=None, dry_run=False, only=None, log=U.log):
    """Every endpoint in turn (1 s apart). Returns {name: (ok, rows, note)}; data stays on disk."""
    out = {}
    for name in ENDPOINTS:
        if only and name not in only:
            continue
        ok, d, note = pull(name, date, dry_run)
        out[name] = (ok, _count(d) if ok else 0, note)
        log(f"moondev {name}: {'ok' if ok else 'FAIL'} rows={out[name][1]} {note if not ok else ''}".rstrip())
        if ok and (ok is True) and not dry_run and name != list(ENDPOINTS)[-1]:
            time.sleep(1.0)
    return out


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def liq_totals_5m(data):
    """(long_usd, short_usd) over the last 5 min from totals.json, tolerant of the field names seen in the bots
    (windows -> {"5m": {...}}; side keys long/short, longs/shorts, sell/buy, liquidated_long/_short). (None, None) if unknown."""
    if not isinstance(data, dict):
        return None, None
    wins = data.get("windows") if isinstance(data.get("windows"), dict) else data
    w = None
    for k in ("5m", "5min", "300", "300s", "five_minutes"):
        if isinstance(wins.get(k), dict):
            w = wins[k]; break
    if w is None:
        return None, None
    long_keys = ("long_usd", "longs_usd", "long", "longs", "sell", "sells", "liquidated_long", "long_liquidations_usd")
    short_keys = ("short_usd", "shorts_usd", "short", "shorts", "buy", "buys", "liquidated_short", "short_liquidations_usd")
    src = w
    for sub in ("BTC", "btc", "total", "all"):
        if isinstance(w.get(sub), dict):
            src = w[sub]; break
    lo = next((_num(src[k]) for k in long_keys if k in src and _num(src[k]) is not None), None)
    sh = next((_num(src[k]) for k in short_keys if k in src and _num(src[k]) is not None), None)
    return lo, sh


def live_totals(dry_run=False):
    """For the recorder: (ok, (long_usd, short_usd), note). No archive write."""
    ok, d, note = api_get(ENDPOINTS["totals"], timeout=10, dry_run=dry_run)
    if not ok:
        return False, (None, None), note
    lo, sh = liq_totals_5m(d)
    return (lo is not None or sh is not None), (lo, sh), "ok" if lo is not None else "parse: no 5m window"


_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")


def wallet_seeds(data, limit=100):
    """Wallet addresses found in a profitable-traders / top-traders document (any nesting), lower-cased, deduped."""
    out = []; seen = set()

    def walk(x):
        if len(out) >= limit:
            return
        if isinstance(x, dict):
            for k in ("wallet", "proxyWallet", "address", "proxy_wallet", "user"):
                v = x.get(k)
                if isinstance(v, str) and _ADDR.fullmatch(v.strip()):
                    a = v.strip().lower()
                    if a not in seen:
                        seen.add(a); out.append(a)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            for a in _ADDR.findall(x):
                a = a.lower()
                if a not in seen:
                    seen.add(a); out.append(a)
    walk(data)
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="comma list of endpoint names")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    res = pull_all(a.date, a.dry_run, only)
    print(json.dumps({k: {"ok": v[0], "rows": v[1], "note": v[2]} for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
