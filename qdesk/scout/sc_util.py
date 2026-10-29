#!/usr/bin/env python3
"""sc_util.py - small helpers shared by the scout modules (no secrets, no network side effects at import).

  data_dir()/data_path(...)   QD_DATA (default ~/qdesk/data) + subfolder creation
  http_json(url, ...)         urllib GET/POST with the UA header, 5 retries with backoff, 0.2 s pacing
                              -> (ok, obj, note); never raises
  save_json(path, obj)        tmp + os.replace (uses qdesk.common.schemas.save_json when importable)
  load_labels(path)           copied from C:\\g\\tlfix\\paired_maker\\pm_tokens.py
  day helpers                 parse_day('20261030') -> epoch, day_of(ts) -> 'YYYYMMDD', yesterday(date)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

UA = {"User-Agent": "Mozilla/5.0 qdesk-scout (read-only research desk)", "Accept": "application/json"}
PACE_S = 0.2
_last_call = [0.0]
REQUESTS = {"n": 0}          # global request counter (wallet_scan budget, evidence sources)


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}", flush=True)


# ---------- data dir
def data_dir():
    return os.path.expanduser(os.environ.get("QD_DATA") or "~/qdesk/data")


def data_path(*parts, mkdir=True):
    p = os.path.join(data_dir(), *parts)
    if mkdir:
        os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


# ---------- days
def parse_day(s):
    """'20261030' or '2026-10-30' -> epoch of 00:00 UTC."""
    s = str(s).replace("-", "")
    return int(datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())


def day_of(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y%m%d")


def today():
    return day_of(time.time())


def yesterday(date=None):
    """The day before `date` (YYYYMMDD; default today UTC)."""
    base = parse_day(date) if date else parse_day(today())
    return day_of(base - 86400)


def iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- http
def http_json(url, headers=None, data=None, tries=5, timeout=20, pace=PACE_S, budget=None):
    """GET (or POST when data is a dict) JSON. Returns (ok, obj, note). Never raises.
    429 -> longer sleep; 401/403/404 -> no retry (the answer will not change)."""
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    note = ""
    for i in range(tries):
        if budget is not None and REQUESTS["n"] >= budget:
            return False, None, "request budget exhausted"
        wait = pace - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time(); REQUESTS["n"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h, data=body), timeout=timeout) as r:
                raw = r.read()
            try:
                return True, json.loads(raw.decode()), "ok"
            except Exception:
                return False, None, "not json: " + raw[:80].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            note = f"HTTP {e.code}"
            if e.code in (401, 403, 404):
                return False, None, note
            time.sleep((5 + 5 * i) if e.code == 429 else (1 + i))
        except Exception as e:
            note = f"{type(e).__name__}: {str(e)[:80]}"
            time.sleep(1 + i)
    return False, None, note or "gave up"


# ---------- json files
def save_json(path, obj):
    try:
        from qdesk.common.schemas import save_json as _sj
        return _sj(path, obj)
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)
    return path


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# copied from C:\g\tlfix\paired_maker\pm_tokens.py (load_labels)
def load_labels(path):
    import csv
    lab = {}
    if not path or not os.path.exists(os.path.expanduser(path)):
        return lab
    with open(os.path.expanduser(path), newline="") as fh:
        for r in csv.DictReader(fh):
            st = r.get("start_time") or r.get("start") or r.get("round_start") or r.get("start_ts") or r.get("epoch")
            oc = (r.get("chain_outcome") or r.get("outcome") or r.get("label") or "").strip().lower()
            if not st or oc not in ("up", "down"):
                continue
            try:
                if "T" in st:
                    st = int(datetime.strptime(st.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                else:
                    st = int(float(st))
                lab[st] = oc.capitalize()
            except ValueError:
                pass
    return lab


def default_labels_path(variant):
    """VM chain-label files (INTERFACES §0); env override QD_LABELS_5M / QD_LABELS_15M."""
    env = os.environ.get("QD_LABELS_5M" if variant == "5m" else "QD_LABELS_15M")
    if env:
        return os.path.expanduser(env)
    base = os.path.expanduser("~/mstack/history_tools/history")
    return os.path.join(base, "BTC_fiveminute_long.relabeled.csv" if variant == "5m" else "BTC_fifteen_long.csv")


def heartbeat(stage, ok, note=""):
    """qdesk.common.tg.heartbeat when importable, else the same file format written here."""
    try:
        from qdesk.common.tg import heartbeat as hb
        return hb(stage, ok, note)
    except Exception:
        pass
    p = data_path("state", f"hb_{stage}.json")
    try:
        save_json(p, {"ts": int(time.time()), "utc": iso(), "stage": stage, "ok": bool(ok), "note": str(note)[:500]})
        return p
    except Exception:
        return None
