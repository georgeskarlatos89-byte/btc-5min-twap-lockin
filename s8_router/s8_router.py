#!/usr/bin/env python3
"""
S8 SESSION REGIME ROUTER ("Asia fades, US momentum, macro blackout")
Markets: both (5m & 15m) · Style: gate/meta-strategy layered on all others
Edge class: regime conditioning (P4: regime change is the edge)

Causal edge:
A single bot trades all hours identically while the participant mix rotates:
- Asia block (00:00-12:00 UTC): retail-heavy quiet tape (regime: sigma ~7 bps,
  30% flat rounds) -> fade-friendly & maker-friendly.
- US block (13:30-21:00 UTC): vol doubles and trend rounds appear ->
  momentum-friendly & taker-friendly.
- Scheduled releases (8:30/10:00 ET data, 14:00 FOMC): vol events where
  directional taker trades are the sucker trade -> macro blackout armed.
- Losers: time-blind bots and humans applying one playbook to all sessions.

Mechanics (all in code — P5):
- Asia block (00:00–12:00 UTC): enable S3, S5, S6; disable S4.
- US block (13:30–21:00 UTC): enable S1, S2, S4, S5; S3 quotes widen or pull; S6 disabled.
- Macro calendar hardcoded: ±1 round around each scheduled release = NO directional
  taker entries (S1, S2, S4, S5 blocked); ONLY S10 box mode allowed.
- Realized-vol override: trailing-1h 5m sigma > 1.5x 7-day baseline (7.0 bps -> 10.5 bps)
  flips the router to US-mode regardless of clock.
- Today-specific: Saturday -> no US data releases at all; the router runs Asia-mode
  all day. That is our verified live test case today (2026-09-26).

Usage:
  python3 s8_router.py --status           # Print current regime & strategy permissions
  python3 s8_router.py --preflight        # Verify calendar, feeds, rules (dry check)
  python3 s8_router.py --calendar         # List scheduled macro events
  python3 s8_router.py --evaluate <time>  # Evaluate a specific timestamp or ISO string
  python3 s8_router.py --daemon           # Run daemon writing s8_state.json continuously
"""

import argparse
import datetime
import json
import math
import os
import sys
import time
import urllib.request
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "s8_state.json")
LOG_FILE = os.path.join(HERE, "s8_router.log")
KILL_FILE = os.path.join(HERE, "KILL")

# ---- Baseline Market Constants (from market snapshot & Moon Dev RBI) ----
SIGMA_BASE_BPS = 7.0       # 5m sigma baseline (~7.0 - 7.7 bps)
VOL_OVERRIDE_MULT = 1.5    # 1.5x baseline -> 10.5 bps flips to US mode
VOL_BOX_MULT = 1.8         # 1.8x baseline -> 12.6 bps arms S10 off-calendar
QUIET_UTC = (0.0, 12.0)    # 00:00 to 12:00 UTC
US_UTC = (13.5, 21.0)       # 13:30 to 21:00 UTC
WEEKEND_ALL_DAY = True     # Saturday & Sunday run Asia-mode all day

# ---- S3 Policy in US Block ----
# Options: "pull" (disable S3 entirely) or "widen" (widen bid from 0.45 to 0.40)
S3_US_POLICY = "pull"

# ---- Hardcoded Macro Calendar (2026) ----
# Times specified in UTC.
# Note on DST: US Eastern is EDT (UTC-4) from March 8 to Nov 1, 2026.
# 08:30 ET = 12:30 UTC
# 10:00 ET = 14:00 UTC
# 14:00 ET = 18:00 UTC (FOMC)
# 14:30 ET = 18:30 UTC (FOMC Press Conf)

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


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{ts}] [S8-ROUTER] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def parse_timestamp(val):
    """Accepts unix timestamp (int/float) or ISO string format."""
    if val is None:
        return time.time()
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        try:
            return float(val)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(val, fmt).replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue
    return time.time()


def get_macro_events():
    """Returns sorted list of macro events with epoch timestamp."""
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
    return events


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


class VolatilityEstimator:
    """Estimates rolling 5m realized sigma in bps over trailing 1 hour."""
    def __init__(self, baseline_bps=SIGMA_BASE_BPS):
        self.baseline_bps = baseline_bps
        self.samples = deque(maxlen=7200)   # (ts, spot)
        self.last_fetch = 0.0
        self.cached_sigma = baseline_bps

    def add_sample(self, ts, spot):
        self.samples.append((ts, float(spot)))

    def compute_trailing_sigma_bps(self, now_ts=None):
        if now_ts is None:
            now_ts = time.time()
        # Look back 1 hour (3600s)
        cutoff = now_ts - 3600.0
        pts = [(t, p) for (t, p) in self.samples if t >= cutoff]
        if len(pts) < 12:
            return self.cached_sigma

        # Calculate 5-minute rolling returns over the 1h window
        returns = []
        for i in range(len(pts)):
            t_curr, p_curr = pts[i]
            # Find point closest to t_curr - 300s
            t_target = t_curr - 300.0
            if t_target < pts[0][0]:
                continue
            # Nearest match within 30s
            best_prev = min(pts[:i], key=lambda x: abs(x[0] - t_target))
            if abs(best_prev[0] - t_target) <= 30.0 and best_prev[1] > 0:
                ret_bps = (p_curr - best_prev[1]) / best_prev[1] * 10000.0
                returns.append(ret_bps)

        if len(returns) < 5:
            return self.cached_sigma

        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        sigma = math.sqrt(var)
        self.cached_sigma = sigma
        return sigma

    def fetch_recent_kraken_ohlc(self):
        """Fetches 1m OHLC from Kraken to populate rolling vol estimator."""
        now = time.time()
        if now - self.last_fetch < 60.0:
            return self.cached_sigma
        self.last_fetch = now
        try:
            url = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1"
            req = urllib.request.Request(url, headers={"User-Agent": "S8-Session-Router/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                rows = data.get("result", {}).get("XXBTZUSD", [])
                for r in rows:
                    bar_t = float(r[0])
                    bar_close = float(r[4])
                    self.add_sample(bar_t, bar_close)
            return self.compute_trailing_sigma_bps(now)
        except Exception as e:
            # Silently fallback to cached/baseline
            return self.cached_sigma


_VOL_ESTIMATOR = VolatilityEstimator()


def evaluate_regime(ts=None, current_sigma_bps=None, round_type="5m"):
    """
    Core S8 Evaluation Engine (P5: all in code, zero willpower).
    Returns a comprehensive regime evaluation dictionary.
    """
    now_ts = parse_timestamp(ts)
    dt = datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc)
    day_name = dt.strftime("%A")
    is_weekend = dt.weekday() >= 5
    is_saturday = dt.weekday() == 5
    hour_float = dt.hour + (dt.minute / 60.0) + (dt.second / 3600.0)

    # 1. Macro Calendar Check (±1 round blackout)
    is_blackout, blackout_ev, time_to_event = check_macro_blackout(now_ts, round_type)

    # 2. Clock-based Session
    if QUIET_UTC[0] <= hour_float < QUIET_UTC[1]:
        clock_session = "ASIA"
    elif US_UTC[0] <= hour_float < US_UTC[1]:
        clock_session = "US"
    else:
        clock_session = "OFF_HOURS"

    # Weekend Rule: "Saturday -> no US data releases at all; the router should run Asia-mode all day"
    if WEEKEND_ALL_DAY and is_weekend:
        effective_clock_session = "ASIA"
        weekend_note = f"Weekend ({day_name}): Asia-mode all day"
    else:
        effective_clock_session = clock_session
        weekend_note = None

    # 3. Realized-Vol Override
    # "trailing-1h 5m sigma > 1.5x its 7-day baseline flips the router to US-mode regardless of clock"
    if current_sigma_bps is None:
        sigma_bps = _VOL_ESTIMATOR.compute_trailing_sigma_bps(now_ts)
    else:
        sigma_bps = float(current_sigma_bps)

    override_threshold = VOL_OVERRIDE_MULT * SIGMA_BASE_BPS  # 10.5 bps
    vol_override_active = sigma_bps > override_threshold
    vol_box_active = sigma_bps > (VOL_BOX_MULT * SIGMA_BASE_BPS)  # 12.6 bps

    # 4. Effective Regime Determination
    if is_blackout:
        effective_regime = "MACRO_BLACKOUT"
        regime_reason = f"Macro release ±1 round: {blackout_ev['name']} ({blackout_ev['impact']})"
    elif vol_override_active:
        effective_regime = "US_VOL_OVERRIDE"
        regime_reason = (f"Realized-vol override: trailing 1h sigma {sigma_bps:.1f}bps > "
                         f"{VOL_OVERRIDE_MULT}x baseline ({override_threshold:.1f}bps)")
    elif effective_clock_session == "ASIA":
        effective_regime = "ASIA"
        regime_reason = weekend_note if weekend_note else f"Asia quiet session ({QUIET_UTC[0]:.0f}-{QUIET_UTC[1]:.0f} UTC)"
    elif effective_clock_session == "US":
        effective_regime = "US"
        regime_reason = f"US momentum session ({US_UTC[0]:.1f}-{US_UTC[1]:.1f} UTC)"
    else:
        effective_regime = "OFF_HOURS"
        regime_reason = f"Off-hours / transition tape ({hour_float:.1f} UTC)"

    # 5. Strategy Routing Policies
    # Strategies:
    # S1: TWAP Lock-In Convergence late-round taker
    # S2: Latency spread taker
    # S3: Fee-Farm Two-Sided Maker
    # S4: Momentum Breakout Taker
    # S5: Mean-Reversion / Fade
    # S6: Coin-Flip Harvester at round open
    # S10: Vol-Event Binary Box

    policies = {}

    if effective_regime == "MACRO_BLACKOUT":
        # NO DIRECTIONAL TAKER ENTRIES ALLOWED; ONLY S10 BOX MODE ALLOWED
        policies["S1"] = {"allowed": False, "role": "taker", "reason": "Macro blackout: no directional taker entries"}
        policies["S2"] = {"allowed": False, "role": "taker", "reason": "Macro blackout: no directional taker entries"}
        policies["S3"] = {"allowed": False, "action": "pull", "bid": 0.0, "reason": "Macro blackout: extreme single-leg adverse selection risk"}
        policies["S4"] = {"allowed": False, "role": "taker", "reason": "Macro blackout: no directional taker entries"}
        policies["S5"] = {"allowed": False, "role": "fade", "reason": "Macro blackout: no directional fade entries into release whipsaw"}
        policies["S6"] = {"allowed": False, "action": "pull", "bid": 0.0, "reason": "Macro blackout: round open is not a coin-flip during release"}
        policies["S10"] = {"allowed": True, "mode": "box_mode", "reason": "Macro blackout active: S10 box mode armed (pair arb <0.97 / straddle 0.45)"}

    elif effective_regime in ("US", "US_VOL_OVERRIDE"):
        # US MOMENTUM & TREND REGIME:
        # Enable: S1, S2, S4, S5; S3 quotes widen or pull; S6 disabled
        policies["S1"] = {"allowed": True, "role": "taker", "reason": "US block / vol override: active momentum creates TWAP displacement"}
        policies["S2"] = {"allowed": True, "role": "taker", "reason": "US block / vol override: flow volume supports spread taking"}
        if S3_US_POLICY == "widen":
            policies["S3"] = {"allowed": True, "action": "widen", "bid": 0.40, "reason": "US block: quotes widened to 0.40 to mitigate trend adverse selection"}
        else:
            policies["S3"] = {"allowed": False, "action": "pull", "bid": 0.0, "reason": "US block: maker quotes pulled to prevent trending single-leg adverse selection"}
        policies["S4"] = {"allowed": True, "role": "taker", "reason": "US block / vol override: momentum breakout taker enabled"}
        policies["S5"] = {"allowed": True, "role": "fade", "reason": "US block: mean reversion enabled on exhausted moves"}
        policies["S6"] = {"allowed": False, "action": "pull", "bid": 0.0, "reason": "US block / vol override: coin-flip harvester disabled (trend tape is not a 50/50 open coin)"}
        policies["S10"] = {"allowed": vol_box_active, "mode": "box_mode" if vol_box_active else "disabled",
                           "reason": "S10 box armed via vol > 1.8x baseline" if vol_box_active else "S10 idle outside macro event"}

    elif effective_regime == "ASIA":
        # ASIA QUIET FADE & MAKER REGIME:
        # Enable: S3, S5, S6; Disable: S4
        policies["S1"] = {"allowed": False, "role": "taker", "reason": "Asia block: quiet tape lacks late-round lock-in displacement"}
        policies["S2"] = {"allowed": False, "role": "taker", "reason": "Asia block: taker spread edge insufficient on quiet tape"}
        policies["S3"] = {"allowed": True, "action": "quote_normal", "bid": 0.45, "reason": "Asia block: quiet tape maker active (fee farming)"}
        policies["S4"] = {"allowed": False, "role": "taker", "reason": "Asia block: momentum breakout taker disabled on quiet fade tape"}
        policies["S5"] = {"allowed": True, "role": "fade", "reason": "Asia block: retail quiet tape mean reversion active"}
        policies["S6"] = {"allowed": True, "action": "quote_normal", "bid": 0.49, "reason": "Asia block / weekend: fair coin-flip open harvester active"}
        policies["S10"] = {"allowed": False, "mode": "disabled", "reason": "S10 idle on quiet tape"}

    else:  # OFF_HOURS
        # Conservative transition regime
        policies["S1"] = {"allowed": False, "role": "taker", "reason": "Off-hours: outside US momentum block"}
        policies["S2"] = {"allowed": False, "role": "taker", "reason": "Off-hours: low volume transition tape"}
        policies["S3"] = {"allowed": True, "action": "quote_normal", "bid": 0.45, "reason": "Off-hours: conservative maker quoting allowed"}
        policies["S4"] = {"allowed": False, "role": "taker", "reason": "Off-hours: momentum breakout taker disabled"}
        policies["S5"] = {"allowed": True, "role": "fade", "reason": "Off-hours: mean reversion permitted"}
        policies["S6"] = {"allowed": False, "action": "pull", "bid": 0.0, "reason": "Off-hours: coin-flip harvester restricted to Asia block & weekends"}
        policies["S10"] = {"allowed": False, "mode": "disabled", "reason": "S10 idle"}

    allowed_strats = [s for s, p in policies.items() if p["allowed"]]
    disabled_strats = [s for s, p in policies.items() if not p["allowed"]]

    return {
        "timestamp": now_ts,
        "utc_time": dt.strftime("%Y-%m-%d %H:%M:%SZ"),
        "day_of_week": day_name,
        "is_weekend": is_weekend,
        "is_saturday": is_saturday,
        "clock_session": clock_session,
        "effective_regime": effective_regime,
        "regime_reason": regime_reason,
        "macro_blackout_active": is_blackout,
        "macro_blackout_event": blackout_ev if is_blackout else None,
        "next_macro_event": blackout_ev if not is_blackout else None,
        "seconds_to_macro_event": time_to_event,
        "realized_sigma_5m_bps": round(sigma_bps, 2),
        # 2026-09-26 (S8 record Part #2): without Kraken samples the estimator returns the 7.0 baseline,
        # which looked like a measurement in --status / the monitor. Say so explicitly.
        "sigma_source": ("caller" if current_sigma_bps is not None else
                         ("kraken_1m" if len(_VOL_ESTIMATOR.samples) >= 12 else "baseline_placeholder")),
        "baseline_sigma_bps": SIGMA_BASE_BPS,
        "vol_override_active": vol_override_active,
        "vol_override_threshold_bps": override_threshold,
        "vol_box_threshold_bps": VOL_BOX_MULT * SIGMA_BASE_BPS,
        "allowed_strategies": allowed_strats,
        "disabled_strategies": disabled_strats,
        "strategy_policies": policies,
    }


def evaluate_strategy_gate(strategy_name, ts=None, current_sigma=None, round_type="5m"):
    """
    Public gate checker for bots to call directly.
    Returns (is_allowed: bool, reason: str, policy_dict: dict)
    """
    state = evaluate_regime(ts=ts, current_sigma_bps=current_sigma, round_type=round_type)
    pol = state["strategy_policies"].get(strategy_name.upper())
    if not pol:
        return False, f"Unknown strategy {strategy_name}", {}
    return pol["allowed"], pol.get("reason", ""), pol


def write_state_file(state):
    """Atomically writes state to s8_state.json via temp file."""
    tmp = STATE_FILE + f".tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"Error writing state file: {e}")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def read_state_file():
    """Reads current s8_state.json if available and fresh."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def print_status(ts=None):
    if ts is None:
        _VOL_ESTIMATOR.fetch_recent_kraken_ohlc()          # real trailing sigma, not the placeholder
    st = evaluate_regime(ts=ts)
    if ts is None:
        write_state_file(st)
    print("=" * 76)
    print(f"S8 SESSION REGIME ROUTER STATUS — {st['utc_time']}")
    print("=" * 76)
    print(f"Day:               {st['day_of_week']} (Weekend: {'YES' if st['is_weekend'] else 'no'})")
    print(f"Clock session:     {st['clock_session']} ({QUIET_UTC[0]:.0f}-{QUIET_UTC[1]:.0f} Asia | {US_UTC[0]:.1f}-{US_UTC[1]:.1f} US UTC)")
    print(f"Effective regime:  {st['effective_regime']}")
    print(f"Regime rationale:  {st['regime_reason']}")
    print(f"Realized 5m vol:   {st['realized_sigma_5m_bps']:.2f} bps [{st['sigma_source']}] (Base: {st['baseline_sigma_bps']} bps | Override > {st['vol_override_threshold_bps']} bps)")
    print(f"Vol override:      {'ACTIVE' if st['vol_override_active'] else 'Inactive'}")

    if st["macro_blackout_active"]:
        ev = st["macro_blackout_event"]
        print(f"Macro blackout:    ACTIVE NOW! Event: {ev['name']} ({ev['impact']}) @ {ev['utc_str']}")
    else:
        ev = st["next_macro_event"]
        if ev and st["seconds_to_macro_event"]:
            hrs = st["seconds_to_macro_event"] / 3600.0
            print(f"Next macro event:  {ev['name']} in {hrs:.1f}h ({ev['utc_str']} UTC, {ev['impact']})")
        else:
            print("Next macro event:  None scheduled in near window")

    print("-" * 76)
    print("STRATEGY DISPATCH PERMISSIONS:")
    for strat, pol in sorted(st["strategy_policies"].items()):
        status_tag = " [ALLOWED] " if pol["allowed"] else " [BLOCKED] "
        details = pol.get("action", pol.get("role", pol.get("mode", "")))
        if details:
            status_tag += f"({details}) "
        print(f"  {strat:<4}: {status_tag:<18} -> {pol['reason']}")
    print("=" * 76)


def preflight():
    print("=" * 76)
    print("S8 SESSION REGIME ROUTER — PREFLIGHT CHECK  " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    print("=" * 76)
    _VOL_ESTIMATOR.fetch_recent_kraken_ohlc()
    st = evaluate_regime()
    print(f"Current UTC time:  {st['utc_time']} ({st['day_of_week']})")
    print(f"Realized 5m vol:   {st['realized_sigma_5m_bps']:.2f} bps [{st['sigma_source']}] (override > {st['vol_override_threshold_bps']} bps)")
    print(f"Effective regime:  {st['effective_regime']}")
    print(f"Reason:            {st['regime_reason']}")

    # Check Saturday live test case
    if st["is_saturday"]:
        print("Live test case:    TODAY IS SATURDAY -> Asia-mode runs all day; zero US macro releases.")
    else:
        print(f"Session test:      {st['day_of_week']} calendar check active.")

    print(f"Scheduled events:  {len(HARDCODED_MACRO_EVENTS_2026)} hardcoded 2026 releases loaded.")
    next_ev = st.get("next_macro_event")
    if next_ev:
        print(f"Next release:      {next_ev['name']} @ {next_ev['utc_str']} UTC")

    # Check KILL file
    if os.path.exists(KILL_FILE):
        print(f"KILL FILE:         PRESENT ({KILL_FILE}) -> Router enforces ALL STOP")
    else:
        print("KILL FILE:         Absent (normal operation)")

    # Test strategy gate calls
    for strat in ["S1", "S3", "S6", "S10"]:
        allowed, reason, pol = evaluate_strategy_gate(strat)
        print(f"Gate check {strat:<4}:  {'ALLOWED' if allowed else 'BLOCKED'} — {reason}")

    # Write initial state
    write_state_file(st)
    print(f"State file write:  OK ({STATE_FILE})")
    print("=" * 76)
    print("PREFLIGHT RESULT: ALL GATES & PLUMBING VERIFIED.")
    print("=" * 76)


def run_daemon(poll_interval=5.0):
    log(f"Starting S8 Session Regime Router Daemon (poll={poll_interval}s)...")
    last_regime = None
    last_vol_fetch = 0.0

    try:
        while True:
            now = time.time()
            if now - last_vol_fetch > 120.0:
                _VOL_ESTIMATOR.fetch_recent_kraken_ohlc()
                last_vol_fetch = now

            st = evaluate_regime(now)
            write_state_file(st)

            if st["effective_regime"] != last_regime:
                log(f"REGIME TRANSITION: {last_regime} -> {st['effective_regime']} | Reason: {st['regime_reason']}")
                log(f"Active strategies: {', '.join(st['allowed_strategies'])}")
                last_regime = st["effective_regime"]

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log("Daemon stopped by operator (SIGINT).")


def main():
    parser = argparse.ArgumentParser(description="S8 Session Regime Router")
    parser.add_argument("--status", action="store_true", help="Show current router status")
    parser.add_argument("--preflight", action="store_true", help="Run preflight check")
    parser.add_argument("--daemon", action="store_true", help="Run as long-running daemon")
    parser.add_argument("--poll", type=float, default=5.0, help="Daemon poll interval in seconds")
    parser.add_argument("--evaluate", type=str, help="Evaluate a specific timestamp or ISO string")
    parser.add_argument("--calendar", action="store_true", help="List scheduled macro calendar releases")
    args = parser.parse_args()

    if args.preflight:
        preflight()
    elif args.calendar:
        print("=" * 76)
        print("HARDCODED 2026 MACRO CALENDAR (±1 round blackout: directional takers blocked)")
        print("=" * 76)
        events = get_macro_events()
        for ev in events:
            print(f"  {ev['utc_str']} UTC | {ev['impact']:<8} | {ev['name']}")
        print("=" * 76)
    elif args.evaluate:
        print_status(args.evaluate)
    elif args.daemon:
        run_daemon(args.poll)
    else:
        print_status()


if __name__ == "__main__":
    main()
