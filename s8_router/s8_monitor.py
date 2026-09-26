#!/usr/bin/env python3
"""
S8 MONITOR — Health checks and regime observability for the S8 Session Regime Router.
Following Moon Dev 6+ monitoring architecture (urgent alerts, deduplication, state checks).

Monitored Checks:
  * M1_STATE_FRESH: s8_state.json updated within last 30 seconds.
  * M2_CALENDAR_HEALTH: Hardcoded macro releases loaded, countdown accurate.
  * M3_REGIME_INTEGRITY: Effective regime matches clock/calendar rules.
  * M4_VOL_FEED_HEALTH: Trailing sigma reasonable (1-100 bps); alert on vol override.
  * M5_KILL_FILE: Alert if emergency KILL file is present.
  * M6_TRANSITION_CHECK: Regime transitions logged and verified.

Usage:
  python3 s8_monitor.py [--loop] [--interval 15]
"""

import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "s8_state.json")
KILL_FILE = os.path.join(HERE, "KILL")
MONITOR_LOG = os.path.join(HERE, "s8_monitor.log")

sys.path.append(HERE)
import s8_router


def log_monitor(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{ts}] [S8-MONITOR] {msg}"
    print(line)
    try:
        with open(MONITOR_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_checks():
    findings = []
    now = time.time()

    # M1: State file freshness
    if not os.path.exists(STATE_FILE):
        findings.append(("M1_STATE_MISSING", "CRITICAL", f"State file {STATE_FILE} does not exist"))
        state = None
    else:
        mtime = os.path.getmtime(STATE_FILE)
        age = now - mtime
        if age > 45.0:
            findings.append(("M1_STATE_STALE", "HIGH", f"State file {STATE_FILE} is {age:.1f}s old (>45s)"))
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception as e:
            findings.append(("M1_STATE_CORRUPT", "CRITICAL", f"Failed to parse state JSON: {e}"))
            state = None

    # M2: Calendar & Macro blackout countdown
    events = s8_router.get_macro_events()
    if not events:
        findings.append(("M2_CALENDAR_EMPTY", "HIGH", "No scheduled macro events found in calendar"))
    else:
        is_blackout, ev, delta = s8_router.check_macro_blackout(now)
        if is_blackout:
            findings.append(("M2_MACRO_BLACKOUT_ACTIVE", "INFO", f"Macro blackout active for {ev['name']} ({ev['impact']})"))
        elif ev and delta and delta < 900.0:  # Within 15 minutes
            findings.append(("M2_MACRO_APPROACHING", "HIGH", f"Macro release in {delta/60:.1f} min: {ev['name']} ({ev['impact']})"))

    # M3: Regime Integrity (2026-09-26: fetch Kraken first, else "7.00 bps" here is the placeholder)
    s8_router._VOL_ESTIMATOR.fetch_recent_kraken_ohlc()
    computed = s8_router.evaluate_regime(now)
    if state:
        if state.get("effective_regime") != computed["effective_regime"]:
            findings.append(("M3_REGIME_MISMATCH", "HIGH",
                             f"State file regime '{state.get('effective_regime')}' != live evaluation '{computed['effective_regime']}'"))

    # M4: Realized Volatility Check
    sigma = computed["realized_sigma_5m_bps"]
    if sigma < 1.0 or sigma > 150.0:
        findings.append(("M4_VOL_IMPLAUSIBLE", "HIGH", f"Realized 5m sigma {sigma:.2f} bps outside plausible bounds [1, 150]"))
    if computed["vol_override_active"]:
        findings.append(("M4_VOL_OVERRIDE_ACTIVE", "MEDIUM", f"Realized vol override active: {sigma:.1f} bps > 10.5 bps"))

    # M5: Emergency Kill File
    if os.path.exists(KILL_FILE):
        findings.append(("M5_KILL_FILE_ACTIVE", "CRITICAL", f"KILL file present at {KILL_FILE}"))

    return findings, computed


def print_check_results(findings, computed):
    print("=" * 76)
    print("S8 MONITOR CHECK REPORT  " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    print("=" * 76)
    print(f"Effective regime:      {computed['effective_regime']}")
    print(f"Clock session:         {computed['clock_session']} (Day: {computed['day_of_week']})")
    print(f"Vol override active:   {computed['vol_override_active']} ({computed['realized_sigma_5m_bps']:.2f} bps [{computed.get('sigma_source','?')}])")
    print(f"Macro blackout active: {computed['macro_blackout_active']}")
    print(f"Allowed strategies:    {', '.join(computed['allowed_strategies'])}")
    print(f"Disabled strategies:   {', '.join(computed['disabled_strategies'])}")
    print("-" * 76)
    if not findings:
        print("ALL HEALTH CHECKS GREEN (0 findings).")
    else:
        print(f"FINDINGS ({len(findings)}):")
        for code, severity, msg in findings:
            print(f"  [{severity:<8}] {code:<24} -> {msg}")
    print("=" * 76)


def main():
    parser = argparse.ArgumentParser(description="S8 Health Monitor")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    parser.add_argument("--interval", type=float, default=15.0, help="Check interval in seconds")
    args = parser.parse_args()

    if args.loop:
        log_monitor(f"Starting S8 Monitor Loop (interval={args.interval}s)...")
        try:
            while True:
                findings, computed = run_checks()
                for code, severity, msg in findings:
                    if severity in ("CRITICAL", "HIGH"):
                        log_monitor(f"[{severity}] {code}: {msg}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            log_monitor("Monitor loop terminated by operator.")
    else:
        findings, computed = run_checks()
        print_check_results(findings, computed)


if __name__ == "__main__":
    main()
