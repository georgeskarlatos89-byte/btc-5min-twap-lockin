# S46 — Paired Operator Playbook (S6 + S4)

**Status:** DRY harness built 2026-09-26, preflight ready. Needs Moon Dev key + 3-day DRY before any LIVE.

## The Pair in One Sentence

S6 farms the entertainment flow at the bell (both sides 0.49, +2c per pair, hold single leg to measure if you're being picked off). S4 harvests the forced flow mid-round (all_liq_10m 1s lag cascade + spot confirm → taker continuation on 15m only). Same trigger, opposite regimes — they pull each other.

## Entry — All Must Hold (coded)

### S6 — Coin-Flip Maker
1. t in [0,45s] of round, 5m or 15m
2. Session quiet: 00-12 UTC, 16-18 UTC, weekends all day (S8 router) AND sigma5m <=1.5x 7bps base
3. Mid in [0.45,0.55] at placement AND at fill (Part-4 live gate, fails loudly)
4. No KILL, feed fresh (<10s RTDS, <60s liq), not pulled, daily stop not hit (LIVE only), net cap $30/5m
5. BUY Up @0.49 20sh + BUY Down @0.49 20sh, GTC

### S4 — Liquidation Cascade Taker (15m ONLY)
1. t in [30s,80%] of 15m round (30s..720s)
2. all_liq_10m prints one-sided cascade >=$1.5M/10s (SELL=longs rekt→Down, BUY=shorts rekt→Up)
3. Spot confirms direction: spot move >=+2bps for BUY, <=-2bps for SELL in last 10s (directional, not just magnitude)
4. Book: continuation side ask in [0.30,0.85], mid in same, fee =0.07*p*(1-p)
5. P_model (prior, to be refined): 1.5M→0.60,2.5M→0.65,4M→0.70,8M→0.75 +0.03 if spot>=6bps +0.02 if >=10bps cap 0.80
6. Edge: ask <= P_model - fee -0.02 (2c post-fee margin)
7. No existing S4 in this round, net cap $30/5m, feed health OK, not in macro blackout
8. FOK BUY 20sh at ask, hold to resolution (S1 handoff if TWAP locks)

## Pull Both S6 Quotes Instantly

| # | trigger | source | lag | note |
|---|---|---|---|---|
| a | cascade >=$1.5M/10s | all_liq_10m | median 1s | ONLY intra-round liq feed, fire once per cascade timestamp |
| b | spot >=6bps/10s | RTDS chainlink | ~1s | magnitude-only, protects both |
| c | imbalance extreme >=0.85 / <=0.15 | Moon Dev imbalance/1h | 90s cadence | background only |
| d | mid leaves band | CLOB book | — | band-exit pull |

After any pull: no S6 re-quotes for 60s. S4 attempts entry on same cascade.

## Inventory / Risk

- S6 single leg: HELD to resolution (hold mode) — this IS the experiment. No cut20, no offer49.
- S6 pair: cost 0.98 → hold to resolution → +0.02/sh gross, plus rebate pool + liquidity rewards (≥50sh min, but we use 20sh for $10 incubation)
- S4: hold to resolution, track TWAP gap for S1 handoff logging
- Net cap $30 per 5m bucket across S6+S4 both series — pair is hedged (Up+Down=1.00) nets zero
- Daily stop $20 LIVE only → KILL file + flat. DRY P&L simulated, never kills (S6 record Part #1 lesson)
- Feed-health flat-switch M3/M4/M7: liq stale >60s / empty rows / 401 key expired → flat, S4 disabled
- BANNED: binance_liquidations/10m.json intra-round (163s median lag, half a 5m round)

## Evidence & Kill Criteria

### S6
- DRY 3 days: quotes every quiet round, SKIP reasons logged once per round, pulls fire once per cascade, band gate rejects outside, heartbeat tick_ts every 60s
- Incubate $10/side 2 weeks, 300 legs: conditional win-rate by side must stay within 50% ±8pp. If Up-fill rounds resolve Up 58%+ → killed (someone picking you off)
- Kill: asymmetry >8pp over 300 fills, or live fill pattern not reproducible from tape

### S4
- Backtest: event study on harvested all_liq_10m since 2026-09-07 → average continuation 60s/180s after cascade, hit-rate by size
- Incubate $10/trade, triggers rare on baseline funding tape — correct behavior (P3 waiting is edge)
- Kill: <55% post-fee win over 100 triggers, or funding/OI regime shows cascades mean-reverting

### Paired
- Kill paired if S6 asymmetry fails OR S4 win-rate fails OR combined adverse > gross 2 weeks
- Portfolio cap: $30 net per 5m window shared, never double-size S6+S3+S4 overlap

## Preflight Checklist

```
--preflight checks:
- 5m and 15m tokens OK
- book fetch OK (bid/ask)
- session gate quiet/active + vol override
- band gates S6 [0.45,0.55] S4 [0.30,0.85]
- moondev key present? (if no → flat via stale-switch, expected before feeds connect)
- live keys present? (if no → DRY only)
- KILL absent
- hard gates met?
- RTDS topics chainlink + twap_sixty
- LIQ feed all_liq_10m (1s lag) — ONLY
- BANNED list: binance_liq_10m
```

## What to Expect DRY

- No orders exist. Fills are simulated from public tape (queue-optimistic) — proves plumbing, never edge (Part-4 lesson: +5.71pp fake headline from bypassed gates)
- S6: mid often outside 0.45-0.55 at bell → many SKIP mid outside band (first S6 round was skipped for that reason)
- S4: triggers rare on Saturday baseline funding (OKX funding +0.010%/8h) — zero S4 on quiet day is correct
- Leg rate unknown: if 5 legs/hour quiet, 300 legs ~ week; if 1/hour, month. Stats line shows pace
- Moon Dev key lifetime ~1 week (moongroup_* not moonstream_* rotating 24h). M7 alarm (401) tells you

## Live Wiring (after DRY)

- .env chmod 600: MOONDEV_API_KEY, POLY_PRIVATE_KEY, POLY_FUNDER, LIVE_TRADING=0→1, POLY_SIGNATURE_TYPE=3
- Telegram: hourly status S46 line — S6 quotes/fills/pairs/legs held (won/lost)/kill sample / S4 triggers/fills/wins/by_size / pulls / skips / errors / heartbeat age
- Urgent pings dedup 6h: loop DEAD (heartbeat >5m), 0 quotes in quiet hour, 401/403 key expired, 429/5xx, empty feed, FILL REJECTED (band), tick error, LIVE order, DAILY STOP, KILL appears, LIVE_TRADING flipped, service inactive

## Files Touched

- VM: ~/#4 S4 S6 Paired/s46_coinflip_cascade/* new, ~/s1_harness/s1_monitor.py (add S46 checks)
- Local: s46_coinflip_cascade/{s46_harvester.py, s46_backtest.py, README, PLAYBOOK, EVENT-STUDY, deploy, .env.example}
- GitHub: s46_coinflip_cascade/ (no .env, logs, stats)
