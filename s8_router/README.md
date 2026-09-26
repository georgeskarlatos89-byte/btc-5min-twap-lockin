# Strategy S8 — SESSION REGIME ROUTER

> *"Regime change is the edge, not the enemy." (Jim Simons / Moon Dev Principle P4)*  
> *"Discipline is not a strategy... Hand the trigger to a bot." (Moon Dev Principle P5)*

## Overview

**S8 Session Regime Router** is a meta-strategy and execution gate layered across all Polymarket BTC 5m and 15m trading bots (S1, S2, S3, S4, S5, S6, S10). It partitions market time into structured regimes based on participant flow, macroeconomic event risk, and trailing realized volatility.

## Quick Start

```bash
# Check current regime and strategy dispatch permissions
python3 s8_router.py --status

# Run dry preflight check
python3 s8_router.py --preflight

# Run per-session attribution backtest
python3 s8_backtest.py

# Run health monitor
python3 s8_monitor.py

# View scheduled macro calendar
python3 s8_router.py --calendar
```

## Strategy Matrix

| Session / Regime | Active Strategies | Gated / Blocked Strategies | Maker Rules |
|---|---|---|---|
| **Asia Block (00:00–12:00 UTC)** | **S3, S5, S6** | S1, S2, S4 | S3 normal bid (0.45); S6 open (0.49) |
| **US Block (13:30–21:00 UTC)** | **S1, S2, S4, S5** | S6 (and S3 default) | S3 quotes pulled to avoid adverse selection |
| **Transition / Off-Hours** | **S3, S5** | S1, S2, S4, S6 | S3 conservative quoting |
| **Weekends (Sat & Sun)** | **S3, S5, S6** | S1, S2, S4 | Asia-mode all day (no US macro data) |
| **Macro Blackout ($\pm1$ Round)** | **ONLY S10 (Box)** | **S1, S2, S3, S4, S5, S6** | All directional takers strictly forbidden |

## Files in this Package

- `s8_router.py`: Core routing engine, CLI, and continuous daemon.
- `s8_backtest.py`: Per-session attribution backtest engine.
- `s8_monitor.py`: Health check and regime observability monitor.
- `s8_state.json`: Atomic state file for zero-latency cross-process querying.
- `S8-PLAYBOOK.md`: Operator operational rules, calendar, and pairing guide.
- `S8-LIVING-RECORD.md`: Living record documenting Part #1 and test verification.
- `run_router.sh`: Daemon launcher script.
- `run_backtest.sh`: Backtest runner script.
- `deploy/s8-router.service`: systemd service configuration.
