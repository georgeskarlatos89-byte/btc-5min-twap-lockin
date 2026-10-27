# Paired maker, step 3 result: the 19-day replay with real order latency - FAIL, the project stops here (2026-10-27)

I'm explaining this in simple language a 15 year old can understand.

## What was tested

The gabigol-style tactic: rest a 20-share bid on BOTH sides of every BTC 5-minute round between second 60 and
second 240, keep the two sides paired so a pair never costs more than $0.97, hold at most 40 unpaired shares.
Two ways to pick the price: `join` = sit at the best bid; `model` = our fair value minus 5 cents.
Replayed on the real recorded order book of 19 days (26 Sep - 14 Oct 2026, 5,430 rounds) with the queue rules
(we fill only after the shares that were already resting at our price) and a real delay between "we decide" and
"our order is live": 0 s (impossible, the ceiling), 0.05 s (best case), 0.1 s (what we measured from the VM on
2026-10-15), 0.2 s (bad tail), 1.0 s (the old bot). The chainlink price feed was applied with its measured
1.44 s lag. Gate agreed in the plan: at 0.1 s the profit range (95% CI) must be above zero AND at least 95% of
filled shares must be paired (gabigol: 97%).

## The result: every setting loses, even the impossible instant one

| setting | money in | profit | return on cost | profit 95% CI | pair completion | days positive | gate |
|---|---|---|---|---|---|---|---|
| join, instant (0 s) | $605,427 | -$1,223 | -0.20% | [-3,514, +1,082] | 87.7% | 9/19 | FAIL |
| join, 0.05 s | $541,396 | -$21,416 | -3.96% | [-23,506, -19,223] | 84.1% | 0/19 | FAIL |
| **join, 0.1 s (measured)** | $536,929 | **-$24,036** | **-4.48%** | [-26,154, -21,879] | 83.8% | 0/19 | **FAIL** |
| join, 0.2 s | $533,045 | -$25,559 | -4.79% | [-27,633, -23,398] | 83.6% | 0/19 | FAIL |
| join, 1.0 s (old bot) | $482,978 | -$27,510 | -5.70% | [-29,572, -25,394] | 81.5% | 0/19 | FAIL |
| model, instant | $269,066 | -$9,848 | -3.66% | [-12,161, -7,497] | 68.4% | 2/19 | FAIL |
| **model, 0.1 s (measured)** | $261,900 | **-$16,679** | **-6.37%** | [-18,949, -14,347] | 66.7% | 0/19 | **FAIL** |
| model, 1.0 s | $220,045 | -$17,303 | -7.86% | [-19,490, -15,251] | 63.2% | 0/19 | FAIL |

(0.05 s and 0.2 s for `model` are in the full table: -5.95% and -6.60%, both FAIL.)

What this says in words:
- At our real speed the join tactic loses 4.5 cents of every dollar it puts in, on all 19 of 19 days, and only
  84% of its fills ever get a partner. The fair-value tactic is worse (-6.4%, 67% paired).
- Even with zero delay, which no computer can have, the join tactic is only break-even (-0.2%, the range
  straddles zero) and still pairs only 88%. So being faster is not enough on its own: the ceiling itself fails
  the gate.
- The maker rebate (20% of the taker fee, about 0.6% of money in) does not change the picture: -4.5% + 0.6% is
  still -3.9%.
- Almost all of the loss between "instant" and "0.05 s" comes from orders that were already filled or run through
  in the 50 ms before our cancel landed. That is the same adverse-selection mechanism the #11 autopsy found: the
  takers who hit a resting bid are the ones who just saw the price move.

## The decision (stop rule from the plan)

Step 3 FAILED at the measured latency, so by the rule we set before running: the project stops. No step 4 live
queue test (no 5-share clips, account 2 stays at $32.41), no step 5 co-location application (it would remove
~45 ms of network time, and the table shows even 0 ms does not pass). The paired-maker bot stays DRY-locked: its
LIVE mode is code-gated on a passing replay report, and none exists.

A 3-day sensitivity run with the refined cancel-clock model (v2: the cancel lands once, not restarted on every
replace) was started on 2026-10-27 at 03:45 UTC for the record; it can only soften the 0.05-0.1 s numbers, it
cannot lift the instant ceiling, so it cannot change the verdict.

## Where the numbers are

- Full table and per-day profit: repo `paired_maker/results/REPLAY-RESULTS.md` (+ .json), VM `~/pm/replay_out/`.
- Per-day JSON (every round, every fill): VM `~/pm/replay_out/day_YYYYMMDD.json`.
- Measured latency behind the 0.1 s bucket: `C:\g\polymarket maker - REAL ORDER LOOP measured with tiny live orders - 2026-10-15.md`.
- The run died twice before finishing (10-15 and 10-24) because the VM killed user jobs when my login ended
  (`loginctl Linger=no`); fixed on 10-26, the last 3 days were rerun with the same v1 model, so all 19 days are
  one consistent model.
