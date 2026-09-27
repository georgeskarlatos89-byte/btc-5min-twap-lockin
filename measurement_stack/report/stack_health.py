#!/usr/bin/env python3
"""
stack_health.py — run every 5 minutes by a systemd timer. Urgent alerts only.

"Service active" is not "alive": every daemon writes a heartbeat file and must keep producing
DATA. This check reads heartbeats + data freshness and pings Telegram only when something is
actually broken. All alerts pass through labcommon.alert (dedup 6 h, global hourly cap).
"""
import glob, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(STACK, "common"))
import labcommon
from labcommon import alert, read_heartbeat

SERVICES = {"ms-us-recorder": "us_recorder", "ms-nws-watch": "nws_watch", "ms-regime-watch": "regime_watch"}
HB_MAX_AGE = {"us_recorder": 180, "nws_watch": 180, "regime_watch": 1500}

def svc(name, prop="ActiveState"):
    try: return subprocess.run(["systemctl", "show", name + ".service", "-p", prop, "--value"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception: return "?"

def main():
    now = time.time(); problems = []
    for unit, hbname in SERVICES.items():
        state = svc(unit)
        if state == "inactive" and svc(unit, "UnitFileState") in ("disabled", ""): continue      # not installed yet
        if state != "active":
            problems.append(unit); alert(f"svc:{unit}", f"🚨 {unit} is {state}. Fix: sudo systemctl restart {unit}.service", "warn", "health"); continue
        hb = read_heartbeat(hbname)
        age = None if not hb else now - hb.get("ts", 0)
        up = float(svc(unit, "ActiveEnterTimestampMonotonic") or 0)
        if age is None or age > HB_MAX_AGE[hbname]:
            problems.append(unit)
            alert(f"hb:{unit}", f"🚨 {unit} is 'active' but its heartbeat is {'missing' if age is None else str(int(age / 60)) + ' min old'} "
                  f"(the main loop is not running). Fix: sudo systemctl restart {unit}.service", "warn", "health"); continue
        if hbname == "us_recorder":
            if hb.get("uptime_s", 0) > 300 and (hb.get("last_frame_age_s") is None or hb["last_frame_age_s"] > 300):
                alert("us:noframes", f"🚨 US recorder: no venue frame for {hb.get('last_frame_age_s')} s while connected. "
                      f"The anonymous feed may have changed or blocked this IP. Rounds are not being recorded.", "warn", "health")
            if hb.get("uptime_s", 0) > 300 and len(hb.get("books") or []) < 3:
                alert("us:books", f"⚠ US recorder: only {len(hb.get('books') or [])} of 4 constituent order books are fresh "
                      f"({', '.join(hb.get('books') or []) or 'none'}). The BRTI proxy is degraded; those rounds are marked THIN-PROXY.", "warn", "health")
        if hbname == "nws_watch":
            if hb.get("uptime_s", 0) > 900 and (hb.get("quoted") or 0) < 20:
                alert("wx:quotes", f"⚠ Weather watcher: only {hb.get('quoted')} of {hb.get('buckets')} buckets have a quote. Bucket discovery or the feed is failing.", "warn", "health")
            if (hb.get("nws_errors") or 0) > 200:
                alert("wx:nws", f"⚠ Weather watcher: {hb.get('nws_errors')} failed api.weather.gov calls since start. Forecast updates may be missed.", "warn", "health")
        if hbname == "regime_watch" and hb.get("blind"):
            alert("rw:blind", f"⚠ Regime watcher is blind on: {', '.join(hb['blind'])}. No-change results from these sources cannot be trusted.", "warn", "health")
    free = labcommon.disk_free_gb()
    if free < 20: alert("disk", f"🚨 Disk: {free:.1f} GB free on the research box. Recorders stop writing when it fills; compress or archive now.", "warn", "health")
    labcommon.heartbeat("stack_health", problems=problems, disk_free_gb=round(free, 1))
    print(json.dumps(dict(problems=problems, disk_free_gb=round(free, 1))))

if __name__ == "__main__":
    main()
