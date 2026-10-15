# OPEN-BOOK-MAKER (strategy #11) - variant experiment results, 2026-10-15

Simulation only. No live orders were placed, no credentials were read, account 2 is untouched ($32.46).
Sweep ran on twapvm 10:01-11:13 UTC (72 minutes, 12 runs + 1 instrument check + 2 autopsies).

## The one-line result

**Every variant failed the revival gate. Not one of the 12 runs passed even one of R2-R6.** No
`gate_pass.json` was written anywhere, so the bot's code lock (R8) keeps LIVE refused. Under the plan's
own sunset clause, strategy #11 is **archived permanently** and the fair model goes to taker-side
research only. This matches what you already decided on 2026-10-10 ("Stop it for good").

## I'm explaining this in simple language a 15 year old can understand:

The idea of #11 was to sit in the order book with a buy offer a little below the fair price and a sell
offer a little above it, and collect the gap when other people trade against us. The live test on
2026-10-10 lost $32.60 in 25 minutes. The arena workspace AI then wrote a plan with 7 ways to change the
strategy (V1-V6 plus a diagnostic V7) and a strict pass/fail gate, and said: test all of them on recorded
market data, and if none passes, close the strategy for good.

We did exactly that, on 19 recorded days (2026-09-26 to 2026-10-14, 5,313 five-minute rounds). The
first 3 days were used only to pick settings; the verdict comes from the other 16 days (4,580 rounds).
Every variant lost money on the test days. The best one still lost about 2.4 cents on every share it
traded. The baseline lost on all 16 days, the damage limiter V6 lost on all 16 days, and the best variant
had exactly one winning day out of 16.

Why does it keep losing? The autopsy (V7) shows it plainly. Our resting offer only gets hit when the
price is already running through it: in the one second before each fill, the best price had moved about
5 to 6 cents in the direction of our order. Five seconds after the fill, the price is on average 3 cents
against us, and it is still 3 cents against us 30 seconds later. The people hitting our offers know
something we don't, every time, in every minute of the round. Changing the spread, the side, the timing,
or adding filters did not change that. That is the whole story.

## The 12 runs on the 16 test days (20 shares per side, cap 40 per round, 4,580 rounds each)

| run | fills | edge per share [95% CI] | fills on the winning side | return on money spent | worst day | total P&L | verdict |
|---|---|---|---|---|---|---|---|
| V0 baseline (reproduction) | 12,928 | -3.88c [-4.44, -3.33] | 44.2% | -8.06% | -$977 | -$10,021 | FAIL |
| V1 one-sided, theta 0.03 (selected) | 11,357 | -2.54c [-3.39, -1.70] | 34.2% | -6.92% | -$1,157 | -$5,774 | FAIL |
| V1 one-sided, theta 0.05 | 8,045 | -2.38c [-3.38, -1.34] | 30.5% | -7.23% | -$1,062 | -$3,828 | FAIL |
| V2 toxicity gate q0.2 k2 | 5,908 | -2.85c [-3.86, -1.86] | 41.3% | -6.46% | -$629 | -$3,372 | FAIL |
| V2 toxicity gate q0.2 k3 | 5,373 | -3.00c [-4.10, -1.90] | 42.3% | -6.61% | -$581 | -$3,220 | FAIL |
| V2 toxicity gate q0.4 k2 | 10,191 | -3.45c [-4.17, -2.78] | 42.7% | -7.48% | -$699 | -$7,041 | FAIL |
| V2 toxicity gate q0.4 k3 (selected) | 9,830 | -3.38c [-4.09, -2.69] | 43.3% | -7.23% | -$711 | -$6,636 | FAIL |
| V3 half-spread 0.03 | 12,879 | -3.52c [-4.11, -2.93] | 43.4% | -7.49% | -$791 | -$9,060 | FAIL |
| V3 half-spread 0.04 (selected) | 12,290 | -3.42c [-4.07, -2.78] | 42.6% | -7.43% | -$852 | -$8,406 | FAIL |
| V4 late window 180-240 s | 1,694 | -5.73c [-7.56, -3.85] | 41.5% | -12.13% | -$327 | -$1,941 | FAIL |
| V5 open-auction fade, first 15 s | 1,469 | -3.81c [-6.17, -1.34] | 44.0% | -7.96% | -$289 | -$1,120 | FAIL |
| V6 no re-quote after a fill | 6,330 | -3.58c [-4.11, -3.06] | 45.8% | -7.25% | -$424 | -$4,534 | FAIL |

How to read it: "edge per share" is what one share earned or lost at settlement, after the price we paid.
The 95% CI is from a 2,000-sample bootstrap over rounds; every upper bound is below zero. "Fills on the
winning side" needs to be above about 48% to break even at these prices; none got there. The break-even
share price (plan: 0.485; measured 0.33-0.49 depending on the variant) is in `variant_runs/VARIANT-RESULTS.md`.

Gate flags (R1 rounds >= 200, R2 edge CI low > 0, R3 win rate >= 50% and CI low > break-even, R4
5-second adverse move >= 0 on both sides, R5 worst day > -2% of the $1,000 pilot, R6 return-on-cost CI low > 0):
- R1 passed for all 12 (enough rounds). R2, R3, R5, R6 failed for all 12.
- R4 passed only for the two V1 runs, and that is a measurement artefact: V1 buys below the mid-price by
  design, so "5 seconds later vs mid" looks positive (+4.9c), yet the same fills still lost 2.4-2.5c per
  share at settlement. Settlement is the truth; the mid-relative number is not.
- R5 is hopeless at any size: the worst single day was -$289 to -$1,157 at 20 shares per side. To keep
  the worst day inside -$20 (2% of $1,000) the size would have to be under 1.4 shares per side.

## Predictions written before the run vs what happened

| variant | predicted | got |
|---|---|---|
| V0 | reproduces -6.25c/share on the Phase-A days | yes: rounds 1,141, fills 3,580, P&L -$2,825.0, adverse edge -6.25c, exactly the 2026-10-10 numbers |
| V1 | still negative, loss per round shrinks | negative (-2.54c); loss per filled round $1.40 vs $2.62 baseline |
| V2 | FAIL, no filter finds dumb flow | FAIL; fewer fills (5,373-10,191) but the same -2.9 to -3.5c per share |
| V3 | rate unchanged or worse | -3.42c / -3.52c, same as baseline within the CI; the wider spread did not help |
| V4 | negative, every window loses | the worst of all: -5.73c, -12.1% on cost. Late fills come from 8-17 cent sweeps |
| V5 | negative | -3.81c; fewest fills (1,469) and smallest worst day (-$289), still a loser |
| V6 | loss per filled round drops a lot, rate still negative | loss per filled round $1.19 vs $2.62 (down 55%); per share -3.58c, still negative, 0 of 16 days positive |
| V7 | diagnostic memo | written: `variant_runs/V0_base/AUTOPSY.md` and `variant_runs/V6_no_requote_after_fillTrue/AUTOPSY.md` |

Every prediction held. Nothing surprised us, which is itself a result: the 2026-10-10 verdict was not a
bad-luck day, it is how this book behaves.

## The autopsy (V7) in numbers, baseline V0 on the 16 test days, 12,928 fills

| when in the round | fills | sweep through our price in the 1 s before (ticks) | price vs us 5 s later | 30 s later | at settlement |
|---|---|---|---|---|---|
| 0-60 s | 4,861 | 4.4 | -2.8c | -2.8c | -3.8c |
| 60-120 s | 3,663 | 5.0 | -2.7c | -3.0c | -3.1c |
| 120-180 s | 2,716 | 5.9 | -3.5c | -3.3c | -4.1c |
| 180-240 s | 1,490 | 8.1 | -4.5c | -4.0c | -4.8c |
| 240-300 s | 198 | 17.3 | -7.5c | -16.3c | -12.1c |
| all | 12,928 | 5.5 | -3.2c | -3.2c | -3.9c |

Buy fills and sell fills look the same (-4.0c and -3.7c at settlement). The later in the round, the bigger
the sweep that hits us and the bigger the loss. The "edge vs mid at the end of the round" column in the
raw file jumps around because the end-of-round mid sits near 0 or 1; ignore it, settlement is the
measure that counts.

Exposure the simulation cannot see (its known limit: it only counts fills where the price ran through
our quote, never a quiet fill at the touch):
- baseline: our quote sat at the displayed best price 13.6 seconds per round and improved it 12.8
  seconds per round; loss $2.19 per round; it would need 7.3 benign touch fills per round, each earning
  the full 2-cent half-spread on 20 shares, to break even. That is 2.6 invisible good fills for every
  counted bad fill.
- V6: 6.9 seconds at the touch, 5.9 improving; loss $0.99 per round; 3.3 benign fills per round needed,
  2.4 per counted bad fill.
The live test on 2026-10-10 already measured the real mix: 6 fills, 6 on the losing side, 0 benign.

## Checks that make this trustworthy

- Instrument check: V0 on the Phase-A days alone reproduced the 2026-10-10 Phase-A numbers to the cent
  (rounds 1,141, fills 3,580, P&L -$2,825.0, adverse edge -6.25c). Same code path as the bot.
- Chain audit: for each of the 12 runs, 40 sampled rounds were re-checked against the on-chain outcome:
  40 of 40 matched, 0 differed, 0 house flags, in every run.
- Labels: chain corpus `BTC_fiveminute_long.relabeled.csv`, 288 rounds per day through 10-14.
- Grid points were chosen on the 3 fit days only (09-26..28); verdicts use the 16 test days. The
  selection changed nothing: every grid point of every variant fails on its own.
- Code: `run_variants.py` / `variants.py` were not edited during the sweep. After the sweep one cosmetic
  fix went in (the chain-audit column printed "?/?" because the parser read only the last output line of
  `chain_verify.py`; the raw audits were complete and are re-rendered without re-simulating).
- `test_v2.py`: 48 checks, ALL PASS on the PC and on the VM after that fix.

## What the plan says to do now, and what was done

1. Sunset clause (VARIANT-EXPERIMENT-PLAN.md): no variant passed R1-R6 -> strategy #11 is archived
   permanently; no further maker-at-fair variants on this book; the calibrated `fair_model.py` stays
   available for taker-side research only.
2. The bot keeps the R8 code lock: LIVE is refused unless a fresh `gate_pass.json` with a matching code
   hash exists, and none exists. Service `obm-maker` stays disabled, KILL file present, `.env`
   DRY_RUN=true (not touched by this work).
3. Nothing in the fleet on polyvps was touched. No strategy was altered.

## Where the files are

- This file: `C:\g\open_book_maker - VARIANT RESULTS all 7 variants failed - 2026-10-15.md` (copies in
  the working folder `...\arena-ai-btc-5-minute new strategies open maker\`, the Desktop folder
  `open_book_maker results`, repo `open_book_maker_v2/`, VM `~/obm/`).
- Full table with CIs, break-evens and gate flags: `open_book_maker_v2/variant_runs/VARIANT-RESULTS.md`;
  machine-readable: `variant_runs/variant_results.json`; per run `gate_result.json`, `run_meta.json`
  (cfg + code hash + corpus hash), `maker_stats.json`.
- Autopsies: `variant_runs/V0_base/AUTOPSY.md`, `variant_runs/V6_no_requote_after_fillTrue/AUTOPSY.md`
  (+ `autopsy.json`).
- Big raw files stay on the VM only (`~/obm/variant_runs/<run>/sim_rounds.csv`, `sim_fills.jsonl`,
  `sim_rounds.chain.csv`, 37 MB total).
- Repo: https://github.com/georgeskarlatos89-byte/btc-5min-twap-lockin (folder `open_book_maker_v2/`).
