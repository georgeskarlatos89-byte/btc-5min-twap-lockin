# backtest from 2026-09-27 to 2026-09-20 — S1 and label accuracy

Generated 2026-09-27T12:42:02Z on `arena-ai-bots`. Window 2026-09-20T12:15:00Z → 2026-09-27T12:15:00Z (UTC), BTC 5m + 15m on polymarket.com.
Read-only: no keys, no orders. Ground truth = Polygon CTF `payoutNumerators` (index 0 = Up).

## 1. Coverage

| item | count |
|---|---|
| rounds on the grid | 2688 |
| chain-verified (clean 1/0 payout) | 2688 |
| chain pending / no condition id | 0 / 0 |
| gamma outcome available | 2688 |
| past-results outcome available | 2518 |
| observed by our own S1 harness | 2511 |


## 2. How wrong is each label source? (vs chain, both markets)

Gap = distance between the round's boundary values in basis points. Small gap = razor round.

**public: past-results outcome**

| gap_bucket | rounds | wrong_vs_chain | wrong_pct |
|---|---|---|---|
| <1bp | 293 | 108 | 36.86 |
| 1-2bp | 255 | 67 | 26.27 |
| 2-4bp | 405 | 53 | 13.09 |
| 4-8bp | 602 | 22 | 3.65 |
| >8bp | 963 | 4 | 0.42 |
| ALL | 2518 | 254 | 10.09 |


**public: gamma outcomePrices (fetched now)**

| gap_bucket | rounds | wrong_vs_chain | wrong_pct |
|---|---|---|---|
| <1bp | 293 | 0 | 0.0 |
| 1-2bp | 255 | 0 | 0.0 |
| 2-4bp | 405 | 0 | 0.0 |
| 4-8bp | 602 | 0 | 0.0 |
| >8bp | 963 | 0 | 0.0 |
| ALL | 2688 | 0 | 0.0 |


**production: gamma label seen at settle time**

| gap_bucket | rounds | wrong_vs_chain | wrong_pct |
|---|---|---|---|
| <1bp | 280 | 0 | 0.0 |
| 1-2bp | 240 | 0 | 0.0 |
| 2-4bp | 425 | 0 | 0.0 |
| 4-8bp | 595 | 0 | 0.0 |
| >8bp | 948 | 0 | 0.0 |
| ALL | 2488 | 0 | 0.0 |


## 3. Which settlement rule reproduces the chain? (our own recorded values, full-coverage rounds)

**rule: TWAP60 end value >= open TWAP (our ticks)**

| gap_bucket | rounds | wrong_vs_chain | accuracy_pct |
|---|---|---|---|
| <1bp | 280 | 17 | 93.93 |
| 1-2bp | 240 | 0 | 100.0 |
| 2-4bp | 425 | 0 | 100.0 |
| 4-8bp | 595 | 0 | 100.0 |
| >8bp | 948 | 0 | 100.0 |
| ALL | 2488 | 17 | 99.32 |


**rule: full-round TWAP average >= open TWAP (our ticks)**

| gap_bucket | rounds | wrong_vs_chain | accuracy_pct |
|---|---|---|---|
| <1bp | 480 | 192 | 60.0 |
| 1-2bp | 414 | 78 | 81.16 |
| 2-4bp | 522 | 66 | 87.36 |
| 4-8bp | 575 | 34 | 94.09 |
| >8bp | 497 | 7 | 98.59 |
| ALL | 2488 | 377 | 84.85 |


**rule: last-60s average >= open TWAP (our ticks)**

| gap_bucket | rounds | wrong_vs_chain | accuracy_pct |
|---|---|---|---|
| <1bp | 280 | 67 | 76.07 |
| 1-2bp | 240 | 31 | 87.08 |
| 2-4bp | 425 | 22 | 94.82 |
| 4-8bp | 595 | 4 | 99.33 |
| >8bp | 948 | 0 | 100.0 |
| ALL | 2488 | 124 | 95.02 |


**rule: TWAP60 end value >= open SPOT (our ticks)**

| gap_bucket | rounds | wrong_vs_chain | accuracy_pct |
|---|---|---|---|
| <1bp | 280 | 80 | 71.43 |
| 1-2bp | 240 | 42 | 82.5 |
| 2-4bp | 425 | 35 | 91.76 |
| 4-8bp | 595 | 14 | 97.65 |
| >8bp | 948 | 3 | 99.68 |
| ALL | 2488 | 174 | 93.01 |


## 4. Boundary-tick study (1 Hz ticks recorded by the S2 collector)

Rounds with complete ticks at both boundaries: **389**.

Which of our ticks equals past-results' `openPrice` / `closePrice` (within half a cent):

| our tick | matches openPrice | matches closePrice |
|---|---|---|
| no tick within $0.005 | 0 | 1 |
| spot@+0s | 372 | 371 |


Accuracy vs chain of `TWAP60(end + b) >= TWAP60(start + a)` — best 8 offset pairs per market (all gaps):

**5m** (exact boundary ticks 0/0: 287/287 = 100.0 %)

| open_tick_offset_s | end_tick_offset_s | rounds | correct_vs_chain | accuracy_pct |
|---|---|---|---|---|
| 0 | -2 | 287 | 287 | 100.0 |
| 0 | -1 | 287 | 287 | 100.0 |
| 0 | 0 | 287 | 287 | 100.0 |
| 0 | 1 | 287 | 287 | 100.0 |
| 1 | -2 | 287 | 287 | 100.0 |
| 1 | -1 | 287 | 287 | 100.0 |
| 1 | 0 | 287 | 287 | 100.0 |
| 1 | 1 | 287 | 287 | 100.0 |


**15m** (exact boundary ticks 0/0: 102/102 = 100.0 %)

| open_tick_offset_s | end_tick_offset_s | rounds | correct_vs_chain | accuracy_pct |
|---|---|---|---|---|
| 0 | -3 | 102 | 102 | 100.0 |
| 0 | -2 | 102 | 102 | 100.0 |
| 0 | -1 | 102 | 102 | 100.0 |
| 0 | 0 | 102 | 102 | 100.0 |
| 0 | 1 | 102 | 102 | 100.0 |
| 0 | 2 | 102 | 102 | 100.0 |
| 1 | -3 | 102 | 102 | 100.0 |
| 1 | -2 | 102 | 102 | 100.0 |


## 5. Strategy results under each label source

Rules applied: $10 per trade at the recorded ask, one trade per round, at most 3 per hour, $20 daily stop per UTC day, taker fee 0.07·p·(1−p) per share. Entries are the asks the bot actually saw.

| strategy | label_source | signals | trades_taken | wins | win_rate_pct | breakeven_win_rate_pct | fees_usd | net_usd_fee_adjusted | net_per_trade_usd | days_stopped_by_20usd_rule |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 trader (as deployed, DRY) | (a) chain-verified | 133 | 78 | 59 | 75.64 | 82.58 | 11.07 | -56.57 | -0.7252 | 4 |
| S1 trader (as deployed, DRY) | (b1) public past-results outcome | 133 | 46 | 25 | 54.35 | 82.58 | 6.32 | -165.51 | -3.598 | 6 |
| S1 trader (as deployed, DRY) | (b2) public gamma outcome | 133 | 78 | 59 | 75.64 | 82.58 | 11.07 | -56.57 | -0.7252 | 4 |
| S1 trader (as deployed, DRY) | (c) label the bot itself used | 133 | 78 | 59 | 75.64 | 82.58 | 11.07 | -56.57 | -0.7252 | 4 |


Same signals with NO risk caps (every signal taken, fees included):

| strategy | label_source | all_signals_no_caps | all_signals_wins | all_signals_win_rate_pct | all_signals_net_usd |
|---|---|---|---|---|---|
| S1 trader (as deployed, DRY) | (a) chain-verified | 133 | 111 | 83.46 | 15.14 |
| S1 trader (as deployed, DRY) | (b1) public past-results outcome | 127 | 76 | 59.84 | -369.78 |
| S1 trader (as deployed, DRY) | (b2) public gamma outcome | 133 | 111 | 83.46 | 15.14 |
| S1 trader (as deployed, DRY) | (c) label the bot itself used | 133 | 111 | 83.46 | 15.14 |


### Accuracy delta (a) chain vs (b) public

**S1 trader (as deployed, DRY)**

| public source | win-rate error (pp) | net P&L error (USD) | signals mis-labeled |
|---|---|---|---|
| past-results | -21.29 | -108.94 | 33 |
| gamma | 0.0 | 0.0 | 0 |
| bot's own label | 0.0 | 0.0 | 0 |


### Specific boundary-round cases where a label disagreed with the chain on one of our signals

| strategy | market | round_start_utc | side | entry_price | chain_outcome | wrong_source | wrong_label | pr_gap_bps | net_usd_true | net_usd_if_public_label |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 trader (as deployed, DRY) | 5m | 2026-09-20T22:50:00Z | up | 0.97 | up | past-results | down | -0.656010366722339 | 0.29 | -10.02 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-20T23:15:00Z | up | 0.74 | down | past-results | up | 0.8193389711534386 | -10.18 | 3.33 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-20T23:40:00Z | down | 0.64 | down | past-results | up | 2.8449989251090795 | 5.37 | -10.25 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-21T00:45:00Z | up | 0.62 | up | past-results | down | -7.857121861579802 | 5.86 | -10.27 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-21T09:40:00Z | down | 0.69 | down | past-results | up | 14.64451441386019 | 4.28 | -10.22 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-21T12:10:00Z | down | 0.7 | down | past-results | up | 4.877444370206226 | 4.08 | -10.21 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-21T16:30:00Z | down | 0.58 | down | past-results | up | 1.640544273271923 | 6.95 | -10.29 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-21T16:55:00Z | up | 0.78 | up | past-results | down | -5.835489972916348 | 2.67 | -10.15 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-21T18:05:00Z | up | 0.67 | up | past-results | down | -2.7768806891676503 | 4.69 | -10.23 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-22T06:10:00Z | down | 0.95 | down | past-results | up | 2.806761179876266 | 0.49 | -10.04 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-22T06:15:00Z | down | 0.66 | down | past-results | up | 1.7393621490807307 | 4.91 | -10.24 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-22T15:15:00Z | down | 0.59 | up | past-results | down | -2.0012546628160286 | -10.29 | 6.66 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-22T18:50:00Z | down | 0.87 | down | past-results | up | 2.9179885145804447 | 1.4 | -10.09 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-22T21:00:00Z | up | 0.73 | up | past-results | down | -0.9188411268805244 | 3.51 | -10.19 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-22T23:45:00Z | up | 0.71 | up | past-results | down | -0.31889071961997406 | 3.88 | -10.2 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-23T14:35:00Z | up | 0.63 | up | past-results | down | -1.3332387434281792 | 5.61 | -10.26 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-23T14:45:00Z | down | 0.96 | down | past-results | up | 1.5120234006793911 | 0.39 | -10.03 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-23T23:00:00Z | down | 0.74 | down | past-results | up | 0.22540529601760495 | 3.33 | -10.18 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-24T03:55:00Z | down | 0.87 | down | past-results | up | 2.4080991975486463 | 1.4 | -10.09 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-24T08:35:00Z | down | 0.92 | down | past-results | up | 5.415197987538195 | 0.81 | -10.06 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-24T10:15:00Z | up | 0.65 | up | past-results | down | -4.79003375319899 | 5.14 | -10.24 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-24T17:10:00Z | down | 0.96 | down | past-results | up | 0.3844404823749607 | 0.39 | -10.03 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-24T17:30:00Z | up | 0.93 | up | past-results | down | -3.3302282225684126 | 0.7 | -10.05 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-24T20:05:00Z | down | 0.92 | down | past-results | up | 1.7048059138752514 | 0.81 | -10.06 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-24T20:15:00Z | down | 0.66 | down | past-results | up | 2.7083659053565468 | 4.91 | -10.24 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-25T00:40:00Z | up | 0.94 | up | past-results | down | -1.1328793314010652 | 0.6 | -10.04 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-25T01:50:00Z | up | 0.76 | up | past-results | down | -1.071431547733973 | 2.99 | -10.17 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-25T03:45:00Z | down | 0.76 | down | past-results | up | 2.749575731538643 | 2.99 | -10.17 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-25T07:10:00Z | up | 0.94 | up | past-results | down | -3.2512797218589653 | 0.6 | -10.04 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-25T13:00:00Z | down | 0.96 | down | past-results | up | 1.157484919543802 | 0.39 | -10.03 |
| S1 trader (as deployed, DRY) | 5m | 2026-09-25T15:30:00Z | up | 0.82 | up | past-results | down | -3.262802942113594 | 2.07 | -10.13 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-25T16:15:00Z | up | 0.61 | up | past-results | down | -4.409550920796326 | 6.12 | -10.27 |
| S1 trader (as deployed, DRY) | 15m | 2026-09-27T11:30:00Z | down | 0.84 | down | past-results | up | 1.6718028752071652 | 1.79 | -10.11 |


(33 rows in total, full list in the CSV.)

## 6. Per-session split (chain labels)

| strategy | session | trades | wins | win_rate_pct | net_usd_fee_adjusted |
|---|---|---|---|---|---|
| S1 trader (as deployed, DRY) | ASIA 00:00-12:00 | 40 | 30 | 75.0 | -32.43 |
| S1 trader (as deployed, DRY) | OFF-HOURS | 11 | 8 | 72.73 | -5.28 |
| S1 trader (as deployed, DRY) | US 13:30-21:00 | 18 | 16 | 88.89 | 17.99 |
| S1 trader (as deployed, DRY) | WEEKEND | 9 | 5 | 55.56 | -36.86 |


Sessions: ASIA 00:00–12:00 UTC, US 13:30–21:00 UTC, OFF-HOURS the rest of weekdays, WEEKEND Saturday and Sunday (same blocks the S8 router uses).

## 7. Other repo strategies

| strategy | ledger | positions_held_to_resolution | label_dependent |
|---|---|---|---|
| S1 harness rule (observer rows) | {"rows_with_a_signal": 162} |  | not scored: the stored ask is the Up token ask at round end, not an entry price |
| S3 fee-farm maker (DRY) | {"n": 427, "quotes": 1132, "fills": 3, "pairs": 0, "pnl": -1.8412856400000002} | 0 | no trades held to resolution, so no label can change its result |
| S46 cascade + coin-flip (DRY) | {"s6_quotes": 540, "s6_fills": 0, "s6_pairs": 0, "s4_triggers": 0, "s4_fills": 0} | 0 | no trades held to resolution, so no label can change its result |
| S10 vol-event box (DRY) | {"n": 412, "maker_quotes": 66, "maker_fills": 0, "taker_pairs": 0, "pairs_completed": 0, "pnl": 0.0} | 0 | no trades held to resolution, so no label can change its result |
| S2 open-print displacement (recorder) | {"decisions": 417, "candidates": 0} | 0 | no candidates produced |


## 8. Files

- `backtest from 2026-09-27 to 2026-09-20 - boundary tick study - accuracy by tick offset.csv`
- `backtest from 2026-09-27 to 2026-09-20 - boundary tick study - rounds.csv`
- `backtest from 2026-09-27 to 2026-09-20 - label and rule accuracy vs chain by gap.csv`
- `backtest from 2026-09-27 to 2026-09-20 - label disagreement cases (public vs chain).csv`
- `backtest from 2026-09-27 to 2026-09-20 - labels per round.csv`
- `backtest from 2026-09-27 to 2026-09-20 - other strategies - trade counts.csv`
- `backtest from 2026-09-27 to 2026-09-20 - session split.csv`
- `backtest from 2026-09-27 to 2026-09-20 - signals mis-scored by a public label.csv`
- `backtest from 2026-09-27 to 2026-09-20 - signals scored.csv`
- `backtest from 2026-09-27 to 2026-09-20 - summary by strategy and label source.csv`
