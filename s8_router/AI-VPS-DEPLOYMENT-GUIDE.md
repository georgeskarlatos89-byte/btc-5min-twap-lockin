# S8 Session Regime Router — VPS Handoff & Deployment Guide for Another AI

## Mission

Deploy the **S8 Session Regime Router** ("Asia fades, US momentum, macro blackout") to the Polymarket trading VPS in a safe, observable, and reproducible manner.

The router acts as the meta-regime gate layered across all active trading bots (S1, S2, S3, S4, S5, S6, S10). Its objective is to eliminate **time-blind trading execution** by:
1. Directing maker strategies (**S3, S6**) and mean-reverters (**S5**) to quiet sessions (Asia block 00:00–12:00 UTC and all-day weekends).
2. Directing momentum takers (**S1, S2, S4**) to trend sessions (US block 13:30–21:00 UTC).
3. Enforcing a **Macro Blackout** ($\pm1$ round around scheduled economic releases: 8:30/10:00 ET releases, 14:00 FOMC) that strictly forbids all directional taker entries and permits only **S10 Vol-Event Box Mode**.
4. Enforcing a **Realized-Vol Override** when trailing-1h 5m $\sigma > 1.5\times$ baseline ($10.5$ bps) to immediately switch to US mode regardless of clock or weekend.

---

## Package Contents

```
s8_router/
├── s8_router.py               # Core regime router engine, CLI, and continuous daemon
├── s8_backtest.py             # Per-session attribution backtest engine (B-step)
├── s8_backtest_report.md      # Markdown report showing backtest attribution findings
├── s8_backtest_rows.csv       # Round-by-round simulation rows (425 rounds)
├── s8_monitor.py              # Health check and regime observability monitor
├── s8_state.json              # Atomic state file for zero-latency (<1ms) querying
├── S8-PLAYBOOK.md             # Operator manual, session rules, commands, kill rules
├── S8-LIVING-RECORD.md        # Living record Part #1 (architecture, B-step, Saturday test)
├── README.md                  # Strategy overview and quickstart
├── run_router.sh              # Shell runner for daemon or preflight
├── run_backtest.sh            # Shell runner for attribution backtest
├── deploy/
│   └── s8-router.service      # systemd service template
└── AI-VPS-DEPLOYMENT-GUIDE.md # This guide for AI handoff
```

---

## Non-Negotiable Safety Rules (Moon Dev RBI & Part-4 Lesson)

1. **Never copy credentials into git, logs, chat, or reports.** Keys reside strictly in local `.env` files (mode `chmod 600`).
2. **DRY by default.** Never toggle `LIVE_TRADING=1` merely because preflight passes. Preflight verifies plumbing, not alpha.
3. **Emergency KILL switch:** `touch s8_router/KILL` immediately signals all downstream bots to go FLAT. Verify this mechanism before launching.
4. **Part-4 Live-Band Invariant:** Live price gates must execute before any fill is recorded. Fills outside $[0.45, 0.55]$ must fail loudly and never be recorded as simulated fills.
5. **Feed Health:** If the Moon Dev liquidation feed (`all_liq_10m`) is stale (>60s) or returns 401/403/empty, makers (S3/S6) must immediately pull quotes and go flat.
6. **No Directional Takes During Macro Releases:** The $\pm1$ round blackout around scheduled data releases is non-bypassable. Directional takes are the sucker trade during whipsaws.

---

## VPS Deployment Sequence

Adapt paths and the service username to the actual VPS environment (e.g. `/home/user/btc-5min-twap-lockin/` or `/home/ubuntu/...`).

### Step 1: Copy and Verify Package
```bash
# Navigate to the repo on the VPS
cd ~/btc-5min-twap-lockin

# Verify Python syntax across all scripts
python3 -m py_compile s8_router/s8_router.py s8_router/s8_backtest.py s8_router/s8_monitor.py
```

### Step 2: Run Preflight Check
Verify that the macro calendar, clock session, and strategy gate dispatch logic are functional:
```bash
python3 s8_router/s8_router.py --preflight
```
*Expected output:* Confirms loaded releases, reports current effective regime, and outputs `PREFLIGHT RESULT: ALL GATES & PLUMBING VERIFIED.`

### Step 3: Verify Current Status & Live Test Case
```bash
python3 s8_router/s8_router.py --status
```
*Note for Saturday:* If today is Saturday (e.g. 2026-09-26), the router must output `Effective regime: ASIA`, with S3 and S6 **ALLOWED**, and S1 and S4 **BLOCKED**.

### Step 4: Run Per-Session Attribution Backtest
Execute the B-step attribution backtest to re-verify gate lift on historical rounds:
```bash
python3 s8_router/s8_backtest.py --save-report --save-csv
```
Review `s8_router/s8_backtest_report.md` to confirm:
- S3 Maker lift is positive (savings on US trend adverse selection $\approx +\$140$).
- S1 Taker per-signal efficiency increases by $+22\%$.
- S6 Harvester avoids asymmetric US open sweeps.

### Step 5: Test Downstream Bot Integration
Verify that downstream strategy runners recognize S8:
```bash
# Verify S6 Harvester acknowledges S8
python3 s6_coinflip/s6_harvester.py --preflight

# Verify S3 Maker acknowledges S8
python3 s3_feefarm/s3_maker.py --preflight
```
Both should display `session gate: QUIET (quotes allowed) — [S8] ASIA: ...`.

### Step 6: Install and Enable systemd Service
```bash
# Update user/paths in s8-router.service if different from /home/user/
sudo cp s8_router/deploy/s8-router.service /etc/systemd/system/s8-router.service
sudo systemctl daemon-reload
sudo systemctl enable --now s8-router.service

# Check service status and logs
sudo systemctl status s8-router.service --no-pager
journalctl -u s8-router.service -f -n 50
```

---

## Operating and Monitoring

### Health Monitor Verification
Run `s8_monitor.py` to inspect state freshness and calendar status:
```bash
python3 s8_router/s8_monitor.py
```
Expected output: `ALL HEALTH CHECKS GREEN (0 findings)`.

To run continuous monitoring:
```bash
python3 s8_router/s8_monitor.py --loop --interval 15
```

### Checking Macro Calendar
To view upcoming high-impact economic releases:
```bash
python3 s8_router/s8_router.py --calendar
```

### Evaluating Custom Timestamps
To simulate regime decisions for specific historical dates or future events:
```bash
# Test US session
python3 s8_router/s8_router.py --evaluate "2026-09-23 15:00:00"

# Test NFP release (Macro Blackout)
python3 s8_router/s8_router.py --evaluate "2026-10-02 12:30:00"
```

---

## Data Collection & Incubation Review Checklist

Collect at least **3 weeks** of live production data:
1. **State Freshness:** Ensure `s8_state.json` updates continuously every 5 seconds.
2. **Transition Observability:** Confirm regime transitions in `s8_router.log` (e.g. ASIA $\to$ US at 13:30 UTC, US $\to$ OFF-HOURS at 21:00 UTC).
3. **Macro Blackout Triggers:** Confirm that at release times, `s8_state.json` switches to `MACRO_BLACKOUT` and directional takers are blocked.
4. **Vol Override Firing:** Confirm that sudden volatility spikes (>10.5 bps) flip the regime to `US_VOL_OVERRIDE` and pull maker quotes.
5. **PnL Attribution Comparison:** Measure the actual PnL of S3, S6, and S1 vs their ungated hypothetical baselines.

---

## Pre-Registered Kill Criterion

Per the Moon Dev RBI framework:
- If after **3 weeks** of live incubation, per-session PnL attribution shows **no statistically significant improvement vs ungated trading**, S8 must be killed.
- To execute a kill:
  ```bash
  sudo systemctl stop s8-router.service
  touch ~/btc-5min-twap-lockin/s8_router/KILL
  ```
