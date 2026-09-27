# Measurement stack — daily report 2026-09-27

Generated 2026-09-27T12:42:32Z on the research box. Read-only research: no keys, no orders, public data only.

## 1. Health

| service | state | liveness |
|---|---|---|
| ms-us-recorder | active | heartbeat -3 s ago |
| ms-nws-watch | active | heartbeat -6 s ago |
| ms-regime-watch | active | heartbeat 177 s ago |


Disk free 169 GB. Telegram alerts sent in the last 24 h: 0. All sub-reports ran.

## 2. 7-day backtest: chain labels vs public labels

Window: backtest from 2026-09-27 to 2026-09-20. Rounds 2688, chain-verified 2688, public labels that disagree with the chain on 254 rounds, our signals mis-scored by a public label: 33.

| strategy | label_source | trades_taken | wins | win_rate_pct | breakeven_win_rate_pct | net_usd_fee_adjusted | days_stopped_by_20usd_rule |
|---|---|---|---|---|---|---|---|
| S1 trader (as deployed, DRY) | (a) chain-verified | 78 | 59 | 75.64 | 82.58 | -56.57 | 4 |
| S1 trader (as deployed, DRY) | (b1) public past-results outcome | 46 | 25 | 54.35 | 82.58 | -165.51 | 6 |
| S1 trader (as deployed, DRY) | (b2) public gamma outcome | 78 | 59 | 75.64 | 82.58 | -56.57 | 4 |
| S1 trader (as deployed, DRY) | (c) label the bot itself used | 78 | 59 | 75.64 | 82.58 | -56.57 | 4 |


Full report and CSVs: `backtest7d/out/backtest from 2026-09-27 to 2026-09-20/`.

## 3. US rounds and the BRTI proxy

Rounds closed in the last 24 h: 2 (NO-OPEN-REF 1, OK 1).

Validation so far: **1 of 200** comparisons, proxy agrees on 1 (100.0 %); clear rounds (gap at least 2 bps) 1 of 1. Settlement over the websocket after median 0.19 s. Two-witness check: 1 rounds, 0 disagree. Verdict: **NOT READY: fewer than 200 comparisons**.

## 4. Mispricing monitors

One-sided book in the final minute (share of seconds with a missing bid or ask):

| venue and horizon | one-sided % in last minute | seconds observed (all minutes) |
|---|---|---|
| polymarket.us 15m | 100.0 | 1724 |
| polymarket.us 1h |  | 1723 |
| polymarket.com 5m | 73.855 | 131400 |
| polymarket.com 15m | 78.152 | 132300 |


Market vs oracle (US): 2 rounds, median RMS divergence 0.0523. Largest: cpc-btc-updown-15m-2026-09-27-1230z at 429 s to the end, market 0.675 vs model 0.828.

Cross-venue 15m (.com vs US, same window): 3 rounds, mean price gap 0.0134; outcomes compared on 1, resolved differently on 0.

## 5. Weather markets

NWS forecast updates detected in the last 24 h: 5 (high changed on 0). Reaction-lag rows: 0 (all time 0).

Executable ask-sums per bucket set (1.00 = fair, below 0.985 = buying every bucket would be riskless before fees):

| city | market_day | scans | ask_sum_min | ask_sum_median | ask_sum_max | overround_pct_median | buy_arb_scans |
|---|---|---|---|---|---|---|---|
| LAX | 2026-09-27 | 8 | 1.1 | 1.13 | 1.15 | 13.0 | 0 |
| MDW | 2026-09-27 | 8 | 1.05 | 1.08 | 1.1 | 8.0 | 0 |
| MIA | 2026-09-27 | 8 | 1.06 | 1.13 | 1.14 | 13.0 | 0 |
| NYC | 2026-09-27 | 8 | 1.07 | 1.12 | 1.17 | 12.0 | 0 |
| SFO | 2026-09-27 | 8 | 1.11 | 1.13 | 1.13 | 13.0 | 0 |


## 6. Rule changes (regime watch)

No rule change in the last 24 h. Sources healthy: 12 of 12.


## 7. Standing constraints

Measure, never attack. No authenticated surface, no promo or bonus interaction, no endpoint hammering. No real orders until the user re-approves after seeing backtest and validation results. S1 stays hard-blocked.

