#!/usr/bin/env python3
"""notify.py - Telegram alerts for OPEN-BOOK-MAKER (rate-limited, never prints secrets).

Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_THREAD_ID from the file named by
TELEGRAM_ENV (default ~/s1_harness/telegram.env). Limits: 12 messages per rolling hour,
identical text suppressed for 10 minutes. Set OBM_NO_TELEGRAM=1 to silence (tests).
"""
import json, os, time, urllib.parse, urllib.request
from collections import deque

STRATEGY = "#11 OPEN-BOOK-MAKER"
_sent = deque()
_last = {}
HOUR_CAP = 12
DEDUP_S = 600


def _creds():
    path = os.environ.get("TELEGRAM_ENV", os.path.expanduser("~/s1_harness/telegram.env"))
    env = {}
    try:
        for ln in open(path):
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.strip().split("=", 1); env[k] = v.strip().strip('"').strip("'")
    except Exception:
        return None
    if not env.get("TELEGRAM_BOT_TOKEN") or not env.get("TELEGRAM_CHAT_ID"):
        return None
    return env


def send(text, force=False):
    """-> True if sent. Prefixes the strategy name. Rate-limited unless force."""
    if os.environ.get("OBM_NO_TELEGRAM") == "1":
        return False
    now = time.time()
    while _sent and now - _sent[0] > 3600:
        _sent.popleft()
    if not force:
        if len(_sent) >= HOUR_CAP:
            return False
        if now - _last.get(text, 0) < DEDUP_S:
            return False
    env = _creds()
    if not env:
        return False
    body = {"chat_id": env["TELEGRAM_CHAT_ID"], "text": f"{STRATEGY}\n{text}"[:4000], "disable_web_page_preview": True}
    if env.get("TELEGRAM_THREAD_ID"):
        body["message_thread_id"] = int(env["TELEGRAM_THREAD_ID"])
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage",
                                     data=urllib.parse.urlencode(body).encode(), headers={"User-Agent": "obm"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    if ok:
        _sent.append(now); _last[text] = now
    return ok


if __name__ == "__main__":
    print("sent:", send("notify.py self-test (no orders, no keys)", force=True))
