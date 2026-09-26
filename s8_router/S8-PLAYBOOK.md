# S8 SESSION REGIME ROUTER — OPERATOR PLAYBOOK

**Strategy:** S8 — SESSION REGIME ROUTER ("Asia fades, US momentum, macro blackout")  
**Role:** Meta-strategy / gate layered on all other strategies (S1, S2, S3, S4, S5, S6, S10)  
**Edge class:** Regime conditioning (P4: regime change is the edge)  
**Methodology:** Moon Dev 6+ RBI (Research -> Backtest -> Incubate)

---

## 1 — CAUSAL EDGE (Why it exists)

A single bot trades all hours identically while the participant mix and microstructure rotate:
- **Asia Block (00:00–12:00 UTC):** Retail-dominated, quiet tape (realized 5m $\sigma \approx 7.0$ bps, ~30% flat rounds settling within $\pm3$ bps). This environment is fade-friendly and maker-friendly.
- **US Block (13:30–21:00 UTC):** Institutional volume, realized volatility doubles, and trend rounds appear. This environment is momentum-friendly and taker-friendly. Resting bids suffer toxic single-leg adverse selection.
- **Scheduled Macro Releases (08:30/10:00 ET, 14:00 FOMC):** Volatility shock events where directional taker trades are the *sucker trade* due to aggressive fee drag and spread widening.
- **Losers:** Time-blind bots and human traders applying one rigid playbook to all sessions.

---

## 2 — OPERATING MECHANICS (All in Code — P5)

| Regime / Session | Hours (UTC) | Allowed Strategies | Blocked Strategies | S3 Maker Policy | S6 Harvester Policy | S10 Box Policy |
|---|---|---|---|---|---|---|
| **ASIA BLOCK** | 00:00–12:00 UTC | **S3, S5, S6** | S1, S2, S4 | Quote normal (bid 0.45) | Quote normal (bid 0.49) | Disabled |
| **US BLOCK** | 13:30–21:00 UTC | **S1, S2, S4, S5** | S6 (and S3 default) | Pulled (or widened to 0.40) | Pulled / Disabled | Armed if vol > 12.6 bps |
| **OFF-HOURS / TRANSITION** | 12:00–13:30 & 21:00–24:00 | S3, S5 | S1, S2, S4, S6 | Conservative quote | Pulled | Disabled |
| **WEEKEND (Sat & Sun)** | All day (00:00–24:00) | **S3, S5, S6** | S1, S2, S4 | Quote normal | Quote normal | Disabled |
| **MACRO BLACKOUT** | $\pm1$ round around event | **ONLY S10** | **S1, S2, S3, S4, S5, S6** | Pulled | Pulled | **ARMED (Box Mode)** |

### Dynamic Overrides
1. **Realized-Vol Override:** If trailing-1h 5m $\sigma > 1.5\times$ baseline ($7.0 \times 1.5 = 10.5$ bps), the router immediately flips to **US-mode** regardless of the clock or weekend.
2. **Vol-Event Box Mode:** If trailing-1h 5m $\sigma > 1.8\times$ baseline ($12.6$ bps), S10 box mode is armed even off-calendar.
3. **Macro Blackout Window:** $\pm1$ round ($\pm300$s for 5m, $\pm900$s for 15m) around hardcoded scheduled releases. All directional taker entries are strictly blocked.

---

## 3 — LIVE TEST CASE: SATURDAY (TODAY)

- Today is **Saturday (2026-09-26)**.
- Per S8 rules: **Saturday runs Asia-mode all day** because zero US macro data releases occur on weekends.
- Expected status:
  - Effective regime: `ASIA`
  - S6 (Coin-Flip Harvester): **ALLOWED**
  - S3 (Fee-Farm Maker): **ALLOWED**
  - S1 & S4: **BLOCKED**

---

## 4 — OPERATOR COMMANDS

### Preflight Check (Read-only validation)
```bash
python3 s8_router.py --preflight
```

### Current Status
```bash
python3 s8_router.py --status
```

### Scheduled Macro Calendar
```bash
python3 s8_router.py --calendar
```

### Evaluate Any Timestamp (ISO or Unix)
```bash
python3 s8_router.py --evaluate "2026-10-02 12:30:00"
```

### Run Backtest & Attribution
```bash
python3 s8_backtest.py
```

### Health Monitor
```bash
python3 s8_monitor.py
```

### Start Router Daemon
```bash
python3 s8_router.py --daemon --poll 5.0
```

---

## 5 — PAIRING WITH S6 AND S3

- **S6 Harvester Integration:** `s6_harvester.py` imports `s8_router.evaluate_strategy_gate("S6")`. In US hours or macro blackout, S6 refuses to quote at open, saving adverse selection.
- **S3 Maker Integration:** `s3_maker.py` imports `s8_router.evaluate_strategy_gate("S3")`. In US hours, quotes are pulled or widened.
- **Exposure Cap:** S3 and S6 share the global portfolio exposure cap ($30 max net directional per 5-minute bucket).

---

## 6 — EMERGENCY CONTROLS & KILL RULES

- **Emergency Stop:**
  ```bash
  touch s8_router/KILL
  ```
  The router detects `KILL` immediately and blocks all strategy dispatch.
- **Pre-Registered Kill Criterion (Moon Dev I Step):**
  - Run S8 layered on top of live/incubating strategies for **3 weeks**.
  - If per-session PnL attribution shows **no statistically significant improvement vs ungated**, KILL the strategy and decommission the router.
