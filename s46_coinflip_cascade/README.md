# S46 — Coin-Flip Harvester (S6) + Liquidation Cascade Continuation (S4) — Paired

**Markets:** BTC Up/Down 5m + 15m (S6 both, S4 15m only)  
**Style:** maker at bell (S6) + taker mid-round (S4)  
**Edge class:** fee recycling + forced-flow momentum  
**Status:** DRY harness built, preflight ready, needs Moon Dev key for live triggers

## Why they pair naturally

| | S6 | S4 |
|---|---|---|
| window | t+0..45s | t+30s..80% (15m only) |
| regime | quiet tape, 30% flat rounds | vol / cascade regime |
| feed | book at 50/51 at bell | all_liq_10m 1s lag + spot confirm |
| P&L | +2c per pair (0.98→1.00) + rebate | continuation edge, book lags cascade |
| fails when | vol spike → adverse selection | dead tape → no triggers |

S6's pull trigger (LIQ >=$1.5M/10s) IS S4's entry trigger. S6 pull protects S6 from being picked off, S4 monetizes the same forced flow. Portfolio: Group A (fee farm) + Group C (fast flow) — uncorrelated, fail in different weathers.

## Moon Dev knowledge (verified 2026-09-07)

- `GET /api/all_liquidations/10m.json` — 270-278 rows, 30s update, **median 1s lag** → **ONLY** intra-round liq feed allowed
- `GET /api/binance_liquidations/10m.json` — median **163s lag** (half a 5m round) → **BANNED** intra-round, background only
- `GET /api/all_liquidations/totals.json` — 6 windows (5m..4h), 20s update, long vs short split → context for P_model
- `GET /api/imbalance/1h.json` — 125-130 coins, 90s cadence → background pull trigger (c)
- Feed-health M3/M4/M7: empty rows / stale `updated_at` / 401 key expired → flat immediately (S4/S9 rule)
- Data freshness doc: tick 500ms, liquidation real-time websocket, but REST polled 30s

## Mechanics encoded in `s46_harvester.py`

### S6 — Coin-Flip Harvester at Round Open
- BUY Up @ 0.49 and BUY Down @ 0.49, 20 shares each (~$10/side incubation)
- Window: t+0 to 45s (0.15 frac), band 0.45-0.55, live-band gate BEFORE any fill (Part-4)
- Inventory: single leg HELD to resolution (hold mode) — measures adverse selection
- Kill: asymmetry >8pp over 300 legs, or fill pattern not reproducible

### S4 — Liquidation Cascade Continuation (15m only)
- Trigger: all_liq_10m one-sided cascade >=$1.5M/10s AND spot confirms direction
  - SELL cascade = longs liquidated = forced sells = down → buy Down
  - BUY cascade = shorts liquidated = forced buys = up → buy Up
- Spot confirm: directional move >=2 bps in last 10s matching cascade side (magnitude pull is 6 bps, S4 needs direction)
- Entry: taker FOK BUY continuation if ask <= P_model - fee - 0.02
  - P_model prior: 1.5M→0.60, 2.5M→0.65, 4M→0.70, 8M→0.75 +0.03 if spot>=6bps +0.02 if >=10bps cap 0.80
  - To be refined by event study `s46_backtest.py` using harvested data
- Window: t+30s..80% of 15m (30s..720s), ask band 0.30-0.85, size 20 shares
- Exit: hold to resolution, S1 TWAP lock-in handoff if gap locks
- Kill: <55% post-fee win over 100 triggers, or regime flips mean-reverting

### S8 Router
- Quiet 00-12 UTC + 16-18 UTC + weekends all day → S6 enabled, S4 armed
- Active US 13:30-21:00 UTC → S6 flat (thin edge), S4 enabled
- Vol override sigma >1.5x 7bps baseline → S6 flat, S4 armed
- Macro blackout placeholder (8:30/10:00 ET data, 14:00 FOMC) → S4 blocked ±1 round

### Risk (global, encoded P5)
- Net cap $30 per 5m bucket across both S6+S4 and both series (same underlying)
- Daily stop $20 LIVE only → KILL + flat, DRY P&L never kills
- Feed stale >60s / empty / 401 → flat (cannot pull on what cannot see)
- DRY unless .env LIVE_TRADING=1 + keys present + every gate passes

## Files

- `s46_harvester.py` — combined harness, DRY by default
- `s46_backtest.py` — S4 event study (needs harvest data) + S6 sim placeholder
- `fill_asymmetry.py` — copy from S6, checks conditional win-rate after 300 legs
- `S46-PLAYBOOK.md` — operator playbook
- `S4-EVENT-STUDY.md` — how to calibrate P_model
- `deploy/s46-harvester.service` — systemd unit (twapvm)
- `.env.example` — env template

## Run

```bash
python3 s46_coinflip_cascade/s46_harvester.py --preflight
python3 s46_coinflip_cascade/s46_harvester.py --minutes 60

# event study (needs ~/Moondev API/data/ harvest)
python3 s46_coinflip_cascade/s46_backtest.py --event-study
python3 s46_coinflip_cascade/fill_asymmetry.py
```

Outputs (gitignored): `s46_harvester.log`, `s46_fills.csv`, `s46_s4_fills.csv`, `s46_pulls.csv`, `s46_cascades.csv`, `s46_stats.json`

## Deployment to twapvm (34.34.13.7)

```bash
# on twapvm
mkdir -p ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
# copy files, chmod 700
# create .env chmod 600 from Documents key file: MOONDEV_API_KEY, POLY_PRIVATE_KEY, POLY_FUNDER, LIVE_TRADING=0
python3 s46_coinflip_cascade/s46_harvester.py --preflight
# systemd
sudo cp deploy/s46-harvester.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now s46-harvester.service
journalctl -u s46-harvester -f
```

Telegram wiring: reuse `s1_harness/s1_monitor.py` — add S46 line with S6+S4 counts, urgent pings for loop DEAD, zero quotes in quiet hour, 401/429, FILL REJECTED, tick error, LIVE flips.

## Evidence needed before LIVE

- DRY 3 days: S6 quotes every quiet round, pulls fire once per cascade (not every poll), band gate rejects
- S4: at least 10 cascades detected in vol session, spot confirmation logged, P_model edge check working, ask repricing speed measured
- S6 asymmetry: 300 legs → win-rate within 8pp of 50%
- S4 event study: post-cascade continuation 60s/180s >55% post-fee over 100 triggers
