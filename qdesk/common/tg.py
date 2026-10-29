#!/usr/bin/env python3
"""tg.py - Telegram, alert rate limiting, heartbeats, CSV append, daily gzip rotation (no secrets here).

Copied from measurement_stack/common/labcommon.py (path in the per-block comments) with these changes:
  STATE      = $QD_DATA/state (default ~/qdesk/data/state), created lazily, not at import
  env file   = ~/s1_harness/telegram.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID=600)
  LAB_NO_TELEGRAM=1 -> nothing is sent, every message goes to state/alerts.log
  heartbeat(stage, ok, note) -> state/hb_<stage>.json {"ts","stage","ok","note"} (section 4 format)
  file lock  = fcntl on Linux, a no-op on Windows (the desk only runs multi-process on the VM)
  disk_free_gb uses shutil.disk_usage (works on both OSes)
Alert rules (from the 2026-09-21 alert storm): dedup per key, a GLOBAL hourly cap, one "muted"
notice when the cap is hit, everything is also written to state/alerts.log so nothing is lost.
"""
import gzip
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

SRC = r"C:\Users\Administrator\polymarket-bot\Speed+VPS,weather bot+btc-5-minute bot\Copy trade btc-5 minute gabagool22 Cinese programmer\arena-ai-copy btc-5-minute workspace\measurement_stack\common\labcommon.py"


def data_dir():
    return os.path.expanduser(os.environ.get("QD_DATA") or "~/qdesk/data")


def state_dir():
    return os.path.join(data_dir(), "state")


STATE = state_dir()
TG_ENV = os.path.expanduser("~/s1_harness/telegram.env")
PREFIX = "qdesk (paper-only research desk, no orders)"
DEDUP_S = 6 * 3600          # 'warn' class: same key at most once per 6 h
HOUR_CAP = 6                # all classes together: at most 6 Telegram messages per hour
MUTE_S = 3600


def _state():
    """Current state dir (re-reads QD_DATA so tests can redirect it); creates it."""
    global STATE
    STATE = state_dir()
    os.makedirs(STATE, exist_ok=True)
    return STATE


def _alert_state():
    return os.path.join(_state(), "alerts.json")


def _alert_log():
    return os.path.join(_state(), "alerts.log")


# copied from <SRC> labcommon.py: utc / iso / day
def utc(): return datetime.now(timezone.utc)
def iso(ts=None): return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def day(ts=None): return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y%m%d")


# copied from <SRC> labcommon.py: read_env
def read_env(path):
    env = {}
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); env[k.strip()] = v.strip()
    return env


# copied from <SRC> labcommon.py: _log (path made lazy)
def _log(line):
    try:
        with open(_alert_log(), "a", encoding="utf-8") as f: f.write(f"[{iso()}] {line}\n")
    except Exception:
        pass


# copied from <SRC> labcommon.py: tg_send
def tg_send(text):
    if os.environ.get("LAB_NO_TELEGRAM"):                 # test runs: write to the log, send nothing
        _log("TELEGRAM suppressed (LAB_NO_TELEGRAM) >> " + text.replace("\n", " | ")[:300]); return False
    cfg = read_env(TG_ENV)
    token, chat, thread = cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID"), cfg.get("TELEGRAM_THREAD_ID")
    if not token or not chat:
        _log("TELEGRAM not configured >> " + text.replace("\n", " | ")[:300]); return False
    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}
    if thread: body["message_thread_id"] = thread
    data = urllib.parse.urlencode(body).encode()
    for _ in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data), timeout=10)
            if r.status == 200: return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try: wait = int(json.load(e).get("parameters", {}).get("retry_after", 5))
                except Exception: wait = 5
                time.sleep(min(wait, 30)); continue
            _log(f"telegram HTTP {e.code}"); return False
        except Exception as e:
            _log(f"telegram error {e.__class__.__name__}"); time.sleep(3)
    return False


class _Lock:
    """fcntl.flock on Linux; no-op on Windows (same call shape so alert() reads identically)."""
    def __init__(self, path):
        self.path = path; self.fd = None
    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None: fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self
    def __exit__(self, *a):
        try:
            if fcntl is not None: fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


# copied from <SRC> labcommon.py: alert (lock wrapped in _Lock, source default 'qdesk', emoji removed)
def alert(key, text, cls="warn", source="qdesk"):
    """cls: 'warn' = dedup 6 h per key; 'event' = every time (still under the hourly cap);
    'once' = once ever; 'report' = scheduled report, bypasses the cap (bounded by schedule).
    Returns True if a Telegram message was actually sent."""
    now = time.time()
    ALERT_STATE = _alert_state()
    with _Lock(ALERT_STATE + ".lock"):
        try: st = json.load(open(ALERT_STATE))
        except Exception: st = {}
        st.setdefault("sent", {}); st.setdefault("once", []); st.setdefault("ts", [])
        flat = text.replace("\n", " | ")[:400]
        if cls == "once" and key in st["once"]: return False
        if cls == "warn" and now - st["sent"].get(key, 0) < DEDUP_S:
            _log(f"DEDUP[{source}:{key}] {flat}"); return False
        st["ts"] = [t for t in st["ts"] if t > now - 3600]
        sent = False
        if cls == "report":
            sent = tg_send(f"{PREFIX}\n{text}")
        elif now < st.get("mute_until", 0):
            st["muted_n"] = st.get("muted_n", 0) + 1; _log(f"MUTED[{source}:{key}] {flat}")
        elif len(st["ts"]) >= HOUR_CAP:
            st["mute_until"] = now + MUTE_S; st["muted_n"] = 1
            tg_send(f"{PREFIX}\n[muted] {HOUR_CAP} alerts in the last hour. Muting for 60 min; everything is still written to state/alerts.log. Last: {flat[:200]}")
            _log(f"MUTE ENGAGED; dropped [{source}:{key}] {flat}")
        else:
            if st.get("muted_n") and now >= st.get("mute_until", 0):
                text += f"\n(note: {st['muted_n']} alerts were muted in the previous hour, see state/alerts.log)"; st["muted_n"] = 0
            sent = tg_send(f"{PREFIX}\n{text}")
            if sent: st["ts"].append(now)
        if sent or cls != "report":
            st["sent"][key] = now
            if cls == "once" and sent: st["once"].append(key)
        if sent: _log(f"SENT[{cls}:{source}:{key}] {flat}")
        if len(st["sent"]) > 400: st["sent"] = dict(sorted(st["sent"].items(), key=lambda kv: -kv[1])[:300])
        tmp = ALERT_STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, ALERT_STATE)
        return sent


def heartbeat(stage, ok, note=""):
    """Write state/hb_<stage>.json = {"ts","utc","stage","ok","note"} (tmp + os.replace).
    Every stage calls this at its end; qd-health reads the files. Returns the path or None."""
    p = os.path.join(_state(), f"hb_{stage}.json"); tmp = p + ".tmp"
    doc = {"ts": int(time.time()), "utc": iso(), "stage": str(stage), "ok": bool(ok), "note": str(note or "")[:500]}
    try:
        with open(tmp, "w", encoding="utf-8") as f: json.dump(doc, f)
        os.replace(tmp, p)
        return p
    except Exception:
        return None


# copied from <SRC> labcommon.py: read_heartbeat (name -> stage)
def read_heartbeat(stage):
    try: return json.load(open(os.path.join(_state(), f"hb_{stage}.json")))
    except Exception: return None


# copied from <SRC> labcommon.py: GzDaily
class GzDaily:
    """append text lines to <dir>/<prefix>-YYYYMMDD<suffix>.gz, rolling at UTC midnight.
    Each (re)open adds a gzip member, so a crash can only damage the tail of the last member."""
    def __init__(self, directory, prefix, suffix=".jsonl", flush_s=5):
        self.dir, self.prefix, self.suffix, self.flush_s = directory, prefix, suffix, flush_s
        os.makedirs(directory, exist_ok=True)
        self.f, self.day, self.last_flush, self.n = None, None, 0, 0
    def path(self, d=None): return os.path.join(self.dir, f"{self.prefix}-{d or day()}{self.suffix}.gz")
    def write(self, line):
        d = day()
        if d != self.day or self.f is None:
            self.close(); self.f = gzip.open(self.path(d), "at", encoding="utf-8", compresslevel=6); self.day = d
        self.f.write(line if line.endswith("\n") else line + "\n"); self.n += 1
        if time.time() - self.last_flush >= self.flush_s:
            self.f.flush(); self.last_flush = time.time()
    def close(self):
        if self.f is not None:
            try: self.f.close()
            except Exception: pass
            self.f = None


# copied from <SRC> labcommon.py: append_csv (row may be a list in column order or a dict)
def append_csv(path, header, row):
    import csv
    cols = list(header)
    if not isinstance(row, dict):
        row = dict(zip(cols, list(row)))
    d = os.path.dirname(path)
    if d: os.makedirs(d, exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in cols})


def disk_free_gb(path=None):
    """Free gigabytes on the volume holding path (default: the data dir, or its first existing parent)."""
    p = path or data_dir()
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p: break
        p = parent
    return shutil.disk_usage(p or ".").free / 2 ** 30
