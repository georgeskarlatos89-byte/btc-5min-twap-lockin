#!/usr/bin/env python3
"""labcommon.py — shared plumbing for the measurement stack (no secrets in this file).

  alert()      rate-limited Telegram sender. Reads the token/chat/thread from the fleet's
               existing ~/s1_harness/telegram.env (chmod 600, never copied, never printed).
               Rules (from the 2026-09-21 alert storm): dedup per key, a GLOBAL hourly cap,
               one "muted" notice when the cap is hit, everything is also written to
               state/alerts.log so nothing is lost while muted.
  heartbeat()  every daemon writes state/hb_<name>.json; "service active" is not "alive".
  GzDaily      append-only gzip stream that rolls at UTC midnight.
"""
import fcntl, gzip, json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(STACK, "state")
os.makedirs(STATE, exist_ok=True)
TG_ENV = os.path.expanduser("~/s1_harness/telegram.env")
ALERT_STATE = os.path.join(STATE, "alerts.json")
ALERT_LOG = os.path.join(STATE, "alerts.log")
PREFIX = "🧪 Measurement stack (read-only research, no orders)"
DEDUP_S = 6 * 3600          # 'warn' class: same key at most once per 6 h
HOUR_CAP = 6                # all classes together: at most 6 Telegram messages per hour
MUTE_S = 3600

def utc(): return datetime.now(timezone.utc)
def iso(ts=None): return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def day(ts=None): return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y%m%d")

def read_env(path):
    env = {}
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); env[k.strip()] = v.strip()
    return env

def _log(line):
    try:
        with open(ALERT_LOG, "a", encoding="utf-8") as f: f.write(f"[{iso()}] {line}\n")
    except Exception:
        pass

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

def alert(key, text, cls="warn", source="stack"):
    """cls: 'warn' = dedup 6 h per key; 'event' = every time (still under the hourly cap);
    'once' = once ever; 'report' = scheduled report, bypasses the cap (bounded by schedule).
    Returns True if a Telegram message was actually sent."""
    now = time.time()
    fd = os.open(ALERT_STATE + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
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
            tg_send(f"{PREFIX}\n🔇 {HOUR_CAP} alerts in the last hour. Muting for 60 min; everything is still written to state/alerts.log. Last: {flat[:200]}")
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
    finally:
        try: fcntl.flock(fd, fcntl.LOCK_UN)
        finally: os.close(fd)

def heartbeat(name, **fields):
    p = os.path.join(STATE, f"hb_{name}.json"); tmp = p + ".tmp"
    fields.update(ts=int(time.time()), utc=iso())
    try:
        json.dump(fields, open(tmp, "w")); os.replace(tmp, p)
    except Exception:
        pass

def read_heartbeat(name):
    try: return json.load(open(os.path.join(STATE, f"hb_{name}.json")))
    except Exception: return None

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

def append_csv(path, cols, row):
    import csv
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in cols})

def disk_free_gb(path=STACK):
    s = os.statvfs(path); return s.f_bavail * s.f_frsize / 2 ** 30
