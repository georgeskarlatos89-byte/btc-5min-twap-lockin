#!/usr/bin/env python3
"""disk_guard.py — qd-health (every 15 min). stdlib only; no keys; no orders.

  * data dir du > 1.8 GB  or  disk free < 3 GB  ->  bash deploy/rotate.sh --aggressive,
    write data/state/SKIP_TIER2 (quant/night.py skips tier 2 tonight), alert ONCE (cls warn,
    re-armed only after the disk is healthy again). Healthy -> SKIP_TIER2 removed.
  * heartbeats: data/state/hb_recorder.json and hb_bot.json must be < 120 s old
    ("service active" is not "alive" — feedback_alert_storm_rules).
  * once per UTC day: qdesk.llm.client.auth_probe() -> alert on failure (claude login expired).
  * writes its own heartbeat hb_health.json. --dry-run: no rotate, no probe, no Telegram.

Never touches anything outside QD_DATA. Exit 0 always (health must never crash the timer).
"""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                       # ~/qdesk
ROOT = os.environ.get("QD_ROOT") or os.path.dirname(PKG)
sys.path.insert(0, ROOT)
DATA = os.path.abspath(os.path.expanduser(os.environ.get("QD_DATA") or os.path.join(os.path.expanduser("~"), "qdesk", "data")))
STATE = os.path.join(DATA, "state")
DU_LIMIT_GB, FREE_LIMIT_GB, HB_MAX_AGE_S = 1.8, 3.0, 120


def iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{iso()}] health: {msg}", flush=True)


def tg():
    try:
        from qdesk.common import tg as _tg
        return _tg
    except Exception as e:
        log(f"qdesk.common.tg unavailable ({e.__class__.__name__}); alerts go to stdout only")
        return None


def alert(mod, key, text, dry):
    if dry or mod is None:
        log(f"ALERT[{key}] {text}"); return False
    try:
        return mod.alert(key, text, cls="warn", source="health")
    except Exception as e:
        log(f"alert failed {e.__class__.__name__}: {e}"); return False


def heartbeat(mod, ok, note):
    try:
        if mod is not None:
            mod.heartbeat("health", ok, note); return
    except Exception:
        pass
    os.makedirs(STATE, exist_ok=True)
    p = os.path.join(STATE, "hb_health.json"); tmp = p + ".tmp"
    json.dump({"ts": int(time.time()), "stage": "health", "ok": ok, "note": note}, open(tmp, "w"), indent=1); os.replace(tmp, p)


def du_gb(path):
    total = 0
    for dp, _dn, fns in os.walk(path):
        for fn in fns:
            try: total += os.lstat(os.path.join(dp, fn)).st_size
            except OSError: pass
    return total / 2 ** 30


def free_gb(path):
    try: return shutil.disk_usage(path).free / 2 ** 30
    except OSError: return float("nan")


def hb_age(name):
    """seconds since hb_<name>.json was written; None when missing/unreadable. Accepts ts as epoch or ISO."""
    p = os.path.join(STATE, f"hb_{name}.json")
    try:
        d = json.load(open(p))
    except Exception:
        return None
    ts = d.get("ts")
    if isinstance(ts, (int, float)):
        return time.time() - float(ts)
    if isinstance(ts, str):
        try:
            return time.time() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try: return time.time() - os.path.getmtime(p)
    except OSError: return None


def check_disk(mod, dry):
    du, free = du_gb(DATA), free_gb(DATA)
    flag = os.path.join(STATE, "SKIP_TIER2"); armed = os.path.join(STATE, "disk_guard_alerted")
    bad = du > DU_LIMIT_GB or (free == free and free < FREE_LIMIT_GB)
    log(f"disk du={du:.2f}GB free={free:.1f}GB bad={bad}")
    if bad:
        if not dry:
            try:
                r = subprocess.run(["bash", os.path.join(HERE, "rotate.sh"), "--aggressive"], capture_output=True, text=True, timeout=600,
                                   env={**os.environ, "QD_DATA": DATA})
                log("rotate --aggressive rc=%d %s" % (r.returncode, (r.stdout or "").strip().splitlines()[-1:] or ""))
            except Exception as e:
                log(f"rotate failed {e.__class__.__name__}")
            with open(flag, "w") as f: f.write(json.dumps({"ts": int(time.time()), "du_gb": round(du, 2), "free_gb": round(free, 1)}) + "\n")
        if not os.path.exists(armed):
            alert(mod, "disk_guard", f"disk guard tripped: data {du:.2f} GB (limit {DU_LIMIT_GB}), free {free:.1f} GB (min {FREE_LIMIT_GB}). "
                                     f"Ran rotate --aggressive; tier 2 skipped tonight (state/SKIP_TIER2).", dry)
            if not dry:
                open(armed, "w").write(iso() + "\n")
    else:
        for p in (flag, armed):
            if os.path.exists(p) and not dry:
                os.remove(p); log(f"cleared {os.path.basename(p)}")
    return not bad, du, free


def check_heartbeats(mod, dry):
    ok = True; notes = []
    for name in ("recorder", "bot"):
        age = hb_age(name)
        if age is None:
            notes.append(f"hb_{name} missing"); ok = False
            alert(mod, f"hb_{name}_missing", f"no heartbeat file for {name} (state/hb_{name}.json). Is qd-{name}.service enabled?", dry)
        elif age > HB_MAX_AGE_S:
            notes.append(f"hb_{name} stale {age:.0f}s"); ok = False
            alert(mod, f"hb_{name}_stale", f"{name} heartbeat is {age:.0f} s old (> {HB_MAX_AGE_S}). The service may be active but not alive.", dry)
        else:
            notes.append(f"hb_{name} {age:.0f}s")
    log("heartbeats " + ", ".join(notes))
    return ok, notes


def check_auth(mod, dry, date):
    """claude CLI login probe at most once per UTC day; result cached in state/auth_probe_<date>.json."""
    p = os.path.join(STATE, f"auth_probe_{date}.json")
    if os.path.exists(p):
        try: return bool(json.load(open(p)).get("ok")), "auth probe cached"
        except Exception: pass
    if dry:
        return True, "auth probe skipped (dry-run)"
    ok, note = False, ""
    try:
        from qdesk.llm import client
        ok = bool(client.auth_probe()); note = "auth probe ok" if ok else "auth probe FAILED"
    except ImportError as e:
        ok, note = True, f"auth probe skipped (qdesk.llm.client not importable: {e.__class__.__name__})"
    except Exception as e:
        ok, note = False, f"auth probe error {e.__class__.__name__}"
    tmp = p + ".tmp"
    json.dump({"ts": int(time.time()), "ok": ok, "note": note}, open(tmp, "w")); os.replace(tmp, p)
    for old in os.listdir(STATE):                                   # keep only today's cache file
        if old.startswith("auth_probe_") and old != os.path.basename(p):
            try: os.remove(os.path.join(STATE, old))
            except OSError: pass
    if not ok:
        alert(mod, "claude_auth", f"Claude CLI auth probe failed on twapvm ({note}). Run `claude login` again (owner step); the night falls back to quant/library.py.", dry)
    return ok, note


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true", help="no rotate, no auth probe, no Telegram")
    a = ap.parse_args()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    os.makedirs(STATE, exist_ok=True)
    mod = tg()
    disk_ok, du, free = check_disk(mod, a.dry_run)
    hb_ok, hb_notes = check_heartbeats(mod, a.dry_run)
    auth_ok, auth_note = check_auth(mod, a.dry_run, a.date)
    note = f"du={du:.2f}GB free={free:.1f}GB; {'; '.join(hb_notes)}; {auth_note}"
    heartbeat(mod, disk_ok and hb_ok and auth_ok, note)
    log("done ok=%s %s" % (disk_ok and hb_ok and auth_ok, note))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # the health timer must never crash
        log(f"UNHANDLED {e.__class__.__name__}: {e}"); sys.exit(0)
