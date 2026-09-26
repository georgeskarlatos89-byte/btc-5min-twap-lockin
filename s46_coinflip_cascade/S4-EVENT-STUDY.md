# S4 — Liquidation Cascade Continuation — Event Study Design

**Goal:** Measure P(continuation | cascade size, spot confirm, vol regime) to calibrate P_model for taker entry: `ask <= P_model - fee - 0.02`

**Why this is needed:** S4 doc says "P_model from measured post-cascade drift in your own event study (don't assume)". The Moon Dev knowledge base gives us the feeds but not the P_model — we must measure.

## Data Sources (from Moon Dev docs)

- **Primary trigger:** `GET /api/all_liquidations/10m.json` — 270-278 rows, 30s update, median 1s lag (the only intra-round feed). Structure: `liquidations: [{symbol, side, quantity, price, value_usd, timestamp}]`, side SELL=long liquidated, BUY=short liquidated.
- **Context:** `GET /api/all_liquidations/totals.json` — 6 windows 5m..4h, 20s update, long vs short split + by_exchange (binance, bybit, okx, hyperliquid). Use for regime filter.
- **Spot:** RTDS `crypto_prices_chainlink` btc/usd — 1s ticks, stall watchdog 60s. Need to log for 60s/180s continuation.
- **TWAP:** RTDS `crypto_prices_twap_sixty` — settlement feed, for S1 handoff detection
- **Polymarket:** Gamma `events?slug=btc-updown-15m-{start}` outcomePrices, CLOB `prices-history` for book repricing speed, `book?token_id=` for live ask
- **BANNED intra-round:** `binance_liquidations/10m.json` median 163s stale — half a 5m round, cannot inform live decision

## Harvest Since 2026-09-07

The VPS harvester (`moondev-harvest.service`) pulls 21 endpoints including:
- `all_liq_10m` 60s cadence, 270-278 rows
- `binance_liq_10m` 60s, 180-194 rows (for post-hoc, not live)
- `hl_liq_10m` 60s, 48-60 rows, no upstream timestamp (unproven)

Location: `~/Moondev API/data/all_liquidations/10m/<date>/<epoch>.json.gz` + `manifest_<date>.csv`

**Measured freshness (knowledge base 5b):**
```
all_liq_10m      lag min 0s | MEDIAN 1s | max 3s  <- usable
binance_liq_10m  lag min 39s | MEDIAN 163s | max 225s <- BANNED
hl_liq_10m       no upstream stamp -> unmeasurable
```

## Event Definition

- Cascade: one-sided liquidations >=$1.5M in any 10s window on BTC (SELL=longs, BUY=shorts), with opposite side <1/3 of dominant (to avoid two-sided chop)
- Dedupe: one event per 60s (latest crossing wins, so new cascade behind old one still fires — S6 record Part #1 fix)
- Spot confirm: spot move direction matches cascade, |move| >=2 bps in last 10s (S4 needs directional, pull trigger needs magnitude only 6 bps)

## Metrics to Measure

For each cascade event at ts0:
1. **Spot continuation:** spot price at ts0+60s and ts0+180s vs spot at ts0 — does it continue in cascade direction?
2. **Polymarket resolution:** 15m round containing ts0 — does it resolve in cascade direction?
3. **Book repricing speed:** CLOB ask for continuation side at ts0, ts0+5s, ts0+15s, ts0+60s — how fast does Polymarket book reprice? If ask jumps >5c in 5s, our entry window is tiny.
4. **TWAP lock-in:** does TWAP gap lock in after cascade? (S1 handoff)
5. **Size dependency:** hit-rate by cascade USD bucket 1.5M,2.5M,4M,8M
6. **Exchange breakdown:** from totals.json by_exchange — does Binance-driven cascade behave different from Hyperliquid?
7. **Funding/OI regime:** from Moon Dev prices endpoint funding + OI — at extremes cascades mean-revert

## P_model Prior (conservative, before measurement)

```
1.5M -> 0.60
2.5M -> 0.65
4M   -> 0.70
8M   -> 0.75
+0.03 if |spot| >=6bps
+0.02 if |spot| >=10bps
cap 0.80
```

Rationale: forced flow self-propagates 1-3 min as margin calls chain (S4 doc). Larger cascade = more forced flow = higher continuation prob. Spot confirmation adds confidence.

Entry: `ask <= P_model - fee(ask) - 0.02`

Example: ask 0.60, fee 1.68%, P_model 0.65 → 0.65-0.0168-0.02=0.6132, ask 0.60 <=0.6132 → take (1.32c edge). If ask already 0.70, 0.70 <=0.6132? no → skip (book repriced).

## Implementation in s46_backtest.py

- `--event-study`: loads harvested 10m snapshots, scans cascades, joins with spot if available, writes `s46_cascades.csv` and `s46_event_study_report.md`
- Without harvest data: synthetic demo showing report shape
- Next: join with Kraken 1s spot logs and Gamma resolutions to compute actual continuation

## Kill Criteria (from S4 doc)

- <55% post-fee win rate over 100 triggers
- Funding/OI regime shows cascades mean-reverting (they can at extremes)
- If book repricing speed < our latency (we are structurally last)

## How S4 Pairs with S6

- S6 pull trigger logs cascade to `s46_pulls.csv` with reason LIQ
- S4 entry logs to `s46_s4_fills.csv` with p_model, edge, spot_bps, cascade_usd
- S6 protects from adverse selection, S4 monetizes same flow
- Net exposure cap $30/5m shared — pair hedged nets zero, naked legs count
- Session: S6 quiet-only, S4 vol-only, but cascade override arms S4 even in quiet

## What We Learned from S3/S6 Fixes

- Pull must fire once per cascade timestamp (not every poll) — otherwise same cascade re-pulls every 60s lock expiry (S6 record: 10 LIQ pulls same event)
- Main tick loop needs crash guard + heartbeat tick_ts every 60s — otherwise loop dies but systemd shows active (S6 zombie 6h, 0 quotes, 52 KeyError)
- Daily stop LIVE only — DRY P&L simulated, must not KILL (S6 fix)
- Band gate BEFORE fill — Part-4 bug faked +5.71pp (72.4% fills above live ceiling)
- Stats: count legs not shares for kill test (20 shares/leg, 15 legs looked like 300)
