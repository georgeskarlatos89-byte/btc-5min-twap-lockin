#!/usr/bin/env python3
"""macro.py - hardcoded 2026 US macro calendar + blackout check (copied from the S8 session regime router).

  python3 macro.py [--date YYYYMMDD] [--dry-run]     prints blackout status and the next event

check_macro_blackout(now_ts, round_type) -> (is_blackout, event|None, seconds_to_event|None)  (names kept)
next_event(now_ts) -> {"name","utc","impact"} | None
The calendar ends 2026-11-12: the owner extends it monthly (plan: "extend the macro calendar monthly").
Times are UTC; EDT until Nov 1 2026 (08:30 ET = 12:30 UTC), EST after (08:30 ET = 13:30 UTC).
"""
import argparse
import datetime
import time

SRC = (r"C:\Users\Administrator\polymarket-bot\Speed+VPS,weather bot+btc-5-minute bot\Copy trade btc-5 minute gabagool22 Cinese programmer"
       r"\arena-ai-btc-5-minute new strategies #8 session regime router (asia fades, us momentum, macro blackout)\s8_router\s8_router.py")

# copied from s8_router.py (SRC above): HARDCODED_MACRO_EVENTS_2026
HARDCODED_MACRO_EVENTS_2026 = [
    # September 2026
    {"name": "US Non-Farm Payrolls (NFP)", "dt": "2026-09-04 12:30:00", "impact": "HIGH"},
    {"name": "Initial Jobless Claims", "dt": "2026-09-10 12:30:00", "impact": "MEDIUM"},
    {"name": "US Consumer Price Index (CPI)", "dt": "2026-09-11 12:30:00", "impact": "CRITICAL"},
    {"name": "US Producer Price Index (PPI)", "dt": "2026-09-12 12:30:00", "impact": "HIGH"},
    {"name": "FOMC Rate Decision", "dt": "2026-09-16 18:00:00", "impact": "CRITICAL"},
    {"name": "FOMC Press Conference", "dt": "2026-09-16 18:30:00", "impact": "CRITICAL"},
    {"name": "Initial Jobless Claims", "dt": "2026-09-17 12:30:00", "impact": "MEDIUM"},
    {"name": "Initial Jobless Claims & GDP Q2 Final", "dt": "2026-09-24 12:30:00", "impact": "HIGH"},
    {"name": "Core PCE Price Index", "dt": "2026-09-25 12:30:00", "impact": "CRITICAL"},

    # October 2026
    {"name": "Initial Jobless Claims", "dt": "2026-10-01 12:30:00", "impact": "MEDIUM"},
    {"name": "ISM Manufacturing PMI", "dt": "2026-10-01 14:00:00", "impact": "HIGH"},
    {"name": "US Non-Farm Payrolls (NFP)", "dt": "2026-10-02 12:30:00", "impact": "CRITICAL"},
    {"name": "ISM Services PMI", "dt": "2026-10-05 14:00:00", "impact": "HIGH"},
    {"name": "Initial Jobless Claims", "dt": "2026-10-08 12:30:00", "impact": "MEDIUM"},
    {"name": "US Consumer Price Index (CPI)", "dt": "2026-10-14 12:30:00", "impact": "CRITICAL"},
    {"name": "US Producer Price Index (PPI)", "dt": "2026-10-15 12:30:00", "impact": "HIGH"},
    {"name": "Initial Jobless Claims", "dt": "2026-10-15 12:30:00", "impact": "MEDIUM"},
    {"name": "Initial Jobless Claims", "dt": "2026-10-22 12:30:00", "impact": "MEDIUM"},
    {"name": "Advance GDP Q3", "dt": "2026-10-29 12:30:00", "impact": "CRITICAL"},
    {"name": "Initial Jobless Claims", "dt": "2026-10-29 12:30:00", "impact": "MEDIUM"},
    {"name": "Core PCE Price Index", "dt": "2026-10-30 12:30:00", "impact": "CRITICAL"},

    # November 2026 (DST ends Nov 1; 8:30 ET -> 13:30 UTC, 14:00 ET -> 19:00 UTC)
    {"name": "ISM Manufacturing PMI", "dt": "2026-11-02 15:00:00", "impact": "HIGH"},
    {"name": "FOMC Rate Decision", "dt": "2026-11-04 19:00:00", "impact": "CRITICAL"},
    {"name": "FOMC Press Conference", "dt": "2026-11-04 19:30:00", "impact": "CRITICAL"},
    {"name": "Initial Jobless Claims", "dt": "2026-11-05 13:30:00", "impact": "MEDIUM"},
    {"name": "US Non-Farm Payrolls (NFP)", "dt": "2026-11-06 13:30:00", "impact": "CRITICAL"},
    {"name": "US Consumer Price Index (CPI)", "dt": "2026-11-12 13:30:00", "impact": "CRITICAL"},
]

_EVENTS_CACHE = None


# copied from s8_router.py (get_macro_events), cached because the recorder calls it every second
def get_macro_events():
    """Returns sorted list of macro events with epoch timestamp."""
    global _EVENTS_CACHE
    if _EVENTS_CACHE is not None:
        return _EVENTS_CACHE
    events = []
    for ev in HARDCODED_MACRO_EVENTS_2026:
        dt = datetime.datetime.strptime(ev["dt"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        events.append({
            "name": ev["name"],
            "impact": ev["impact"],
            "utc_str": ev["dt"],
            "epoch": dt.timestamp(),
        })
    events.sort(key=lambda x: x["epoch"])
    _EVENTS_CACHE = events
    return events


# copied from s8_router.py (check_macro_blackout)
def check_macro_blackout(now_ts, round_type="5m"):
    """
    Rule: ±1 round around each scheduled release = no directional taker entries.
    5m round: ±300s window around release.
    15m round: ±900s window around release.
    Returns: (is_blackout: bool, active_event: dict or None, seconds_to_event: float or None)
    """
    buffer_s = 300.0 if round_type == "5m" else 900.0
    events = get_macro_events()

    # Check active blackout
    for ev in events:
        delta = now_ts - ev["epoch"]
        if -buffer_s <= delta <= buffer_s:
            return True, ev, -delta

    # Find next upcoming event
    next_ev = None
    min_dist = None
    for ev in events:
        dist = ev["epoch"] - now_ts
        if dist > 0:
            if min_dist is None or dist < min_dist:
                min_dist = dist
                next_ev = ev

    return False, next_ev, min_dist


def is_blackout(now_ts, round_type="5m"):
    """0/1 for the macro_blackout feature column."""
    return 1 if check_macro_blackout(now_ts, round_type)[0] else 0


def next_event(now_ts=None):
    """Next scheduled event at or after now_ts -> {"name","utc","impact"} (evidence.regime.macro_next) or None."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    for ev in get_macro_events():
        if ev["epoch"] >= now_ts:
            return {"name": ev["name"], "utc": ev["utc_str"].replace(" ", "T") + "Z", "impact": ev["impact"]}
    return None


def calendar_end():
    ev = get_macro_events()
    return ev[-1]["utc_str"] if ev else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ts = time.time()
    if a.date:
        ts = datetime.datetime.strptime(a.date, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    for rt in ("5m", "15m"):
        b, ev, s = check_macro_blackout(ts, rt)
        print(f"{rt}: blackout={b} event={ev['name'] if ev else None} seconds_to_event={None if s is None else round(s)}")
    print("next:", next_event(ts), "calendar ends", calendar_end(), f"({len(HARDCODED_MACRO_EVENTS_2026)} events)")


if __name__ == "__main__":
    main()
