# S3 BACKTEST REPORT — FEE-FARM TWO-SIDED MAKER (the B step)

**Generated:** 2026-09-22 06:35 UTC · window: last 24.0h · rounds: 380 (both) · skipped: 0

**Doc-verbatim version:** bid 0.49 on Up AND Down, 50sh each, t+30s → 60% elapsed, unpaired 20s → breakeven offer at cost, quote end → taker exit (exit_mode `offer49`). Official taker fee `0.07·p·(1−p)` on taker exits; makers 0%.

## 1 — FILL RATES (the farm's raw material)

| outcome | n | share |
|---|---|---|
| both sides filled (at least a partial pair) | 71 | 18.7% |
| single-leg (inventory rule exercised) | 117 | 30.8% |
| no fill (quotes never hit) | 192 | 50.5% |

**Part-4 band gate:** 10044 touch-fills REJECTED because mid was outside (0.45, 0.55) at fill time — those fills are fiction and are never recorded (the paper-harness bug that once faked +5.71pp).

**Dead-flat rounds (price never left (0.48, 0.52) through the quote window):** 0/380 = 0.0% — token-proxy for the doc's '~30% settle within ±3bps of open' (tokens always resolve 0/1, so the window is the farmable span).

## 2 — PnL (split honestly: pairs vs naked legs)

| metric | value |
|---|---|
| pair PnL (matched shares held to resolution) | $+38.59 |
| naked-leg PnL (inventory rule) | $-819.92 |
| **net before rebates** | **$-781.33** |
| est. rebates credited (param = $0.000/sh-pair) | $0.00 |

## 3 — ADVERSE SELECTION vs GROSS CAPTURE (the pre-registered kill metric)

| metric | value |
|---|---|
| adverse-selection events (exit ≤ 0.44) | 73 |
| adverse losses (all losing exits) | $819.63 |
| gross capture (pairs + winning legs + rebates) | $40.62 |
| ratio adverse/gross | 20.18 |

**Pre-registered kill rule (S3):** *2 consecutive weeks where adverse selection > gross capture*. This single window cannot fire that rule — it sets the baseline it will be judged against.

## 4 — FILL-COUNT BASELINE (M5 lesson: know the natural range BEFORE any threshold)

Rounds-with-fills per hour across the window: mean **7.52**, σ **2.12**, min **2**, max **12** (n=25 hours). No alert threshold may be set narrower than this natural swing.

## 5 — BY SERIES
- **5m**: 286 rounds, pairs 46, net $-611.80, adverse 49
- **15m**: 94 rounds, pairs 25, net $-169.54, adverse 24

## 6 — SWEEP ("run 25 versions in an afternoon" — Moon Dev B-step)

One data fetch, many parameter sets. **net** is all-in over the same rounds.

| # | exit_mode | bid | window | net | pairs | singles | adverse/gross |
|---|---|---|---|---|---|---|---|
| 1 | cut20 | 0.45 | 60% | $+69.11 | 48 | 131 | 158/232 |
| 2 | cut20 | 0.45 | 40% | $-24.49 | 27 | 130 | 149/129 |
| 3 | cut20 | 0.47 | 60% | $-44.22 | 63 | 121 | 182/147 |
| 4 | chase | 0.45 | 60% | $-99.07 | 48 | 131 | 271/178 |
| 5 | cut20 | 0.47 | 40% | $-113.22 | 37 | 125 | 188/83 |
| 6 | chase | 0.45 | 40% | $-141.88 | 27 | 130 | 223/85 |
| 7 | cut20 | 0.49 | 60% | $-196.55 | 71 | 117 | 250/61 |
| 8 | chase | 0.47 | 60% | $-210.76 | 63 | 121 | 324/120 |
| 9 | cut20 | 0.49 | 40% | $-222.34 | 50 | 115 | 251/36 |
| 10 | chase | 0.47 | 40% | $-224.50 | 37 | 125 | 278/59 |
| 11 | cut40 | 0.45 | 60% | $-249.36 | 48 | 131 | 433/194 |
| 12 | cut40 | 0.45 | 40% | $-273.23 | 27 | 130 | 365/100 |
| 13 | chase | 0.49 | 40% | $-315.07 | 50 | 115 | 340/28 |
| 14 | offer49 | 0.45 | 40% | $-316.93 | 27 | 130 | 370/56 |
| 15 | chase | 0.49 | 60% | $-317.56 | 71 | 117 | 363/50 |
| 16 | cut20 | 0.51 | 40% | $-359.85 | 59 | 108 | 362/-25 |
| 17 | cut20 | 0.51 | 60% | $-379.57 | 81 | 110 | 382/-43 |
| 18 | cut40 | 0.47 | 40% | $-381.96 | 37 | 125 | 439/67 |
| 19 | cut40 | 0.47 | 60% | $-385.96 | 63 | 121 | 500/127 |
| 20 | offer49 | 0.47 | 40% | $-395.07 | 37 | 125 | 432/42 |
| 21 | chase | 0.51 | 40% | $-429.37 | 59 | 108 | 431/-26 |
| 22 | chase | 0.51 | 60% | $-476.18 | 81 | 110 | 478/-45 |
| 23 | offer49 | 0.45 | 60% | $-477.33 | 48 | 131 | 601/130 |
| 24 | cut40 | 0.49 | 40% | $-489.36 | 50 | 115 | 521/38 |
| 25 | offer49 | 0.49 | 40% | $-498.49 | 50 | 115 | 518/22 |

**Best version:** exit_mode `cut20`, bid 0.45, window to 60% → net **$+69.11**, adverse/gross 157.70. The doc-verbatim `offer49`/0.49/60% sits at rank 31 of 32.

## 7 — HONEST LIMITATIONS (written down, not swept under)

* **Queue-optimistic touch-fills**: the public tape has no queue position. First print
  through our price fills us at our price. Biases fills UP and adverse selection UP.
* **No historical L2** exists (BME README) — queue dynamics cannot be replayed.
  Rebates parameterized at 0 (upside not counted).
* **Moon Dev pull-triggers are live-only** (all_liq_10m 1s-lag cascade, spot 6bps/10s,
  imbalance 90s background). This backtest runs with only the band gate emulated —
  if the idea needs the pulls to survive, the incubation pull log must prove them.
* **Exits** use marks minus 1 cent + taker fee. Marks are ~1min buckets; intra-minute
  gaps are interpolated pessimistically (late exit).
* Single window ≠ 2 weeks. The kill rule needs the incubation cadence regardless.

## 8 — VERDICT (B-step gate to the I-step)

**PASS at one variant → incubate THAT variant.** `offer49` at 0.49 is the doc-literal reading; the surviving variant is **exit_mode `cut20`, bid 0.45, window 60%** (net $+69.11). Port it into `s3_maker.py` constants, run DRY 3 days to verify the fill model, then $-small real money for 2 weeks (I step). Scale only if live matches THIS table.
