# ASSESSMENT - the arena workspace's VARIANT-EXPERIMENT-PLAN for OPEN-BOOK-MAKER (2026-10-15)

Source: `copy trading btc-5-minute.zip` (arena workspace export), extracted to `C:\g\arena_ws_extracted\`.
Files read: `open_book_maker/VARIANT-EXPERIMENT-PLAN.md`, `HANDOFF-TO-AI.md`, `README.md`, `DECISION.md`,
`postmortem-2026-10-10/*`, `MASTER-RUNBOOK.md` section 9, `HANDOFF-PROMPT.md`, `USER-PREFERENCES.md`,
`maker_lab/OPEN-BOOK-MAKER.md`, plus a diff of the bundle's code against our v2 and the original v1 zip.

## 1. What the other AI is saying, in plain words

- Strategy #11 is closed. Nobody may run it live again from the old documents.
- The only way back is a laboratory test with no money: eight experiments (V0 to V7), each a single
  change to the v2 bot, each with a written prediction BEFORE it runs, judged on days the settings
  were never tuned on.
- A new, stricter pass mark (R1 to R10): at least 200 chain-verified rounds; edge per share positive
  with the whole confidence range above zero; fill win rate above the 48.5 % break-even with its
  confidence range; fills still good against fair 5 s later; worst day under 2 % of capital; return
  on filled money positive (beating the old -5.5 % benchmark is no longer enough).
- A lock in the code: the sim writes `gate_pass.json` (criteria, confidence ranges, code hash, data
  hash); the bot refuses to leave DRY_RUN unless that file exists, is under 7 days old, and the hashes
  match. The lock replaces discipline.
- A sunset clause: if no variant passes, the strategy is buried for good and the fair model moves to
  taker-side research (S1 / truth_lab).
- Three house rules added to the runbook: a failed gate is a verdict; no bundle deploys until its
  settlement path runs end to end on its own output; size by worst-round swing (worst round at most
  1-2 % of capital).

## 2. What it asks us to BUILD (code changes to open_book_maker_v2)

| # | item | where | note |
|---|---|---|---|
| B1 | new gate math: fill win rate + CI, edge per share + CI, per-side edge at fill+5 s, worst day as % of pilot capital, return CI low > 0 | `maker_sim.py report()` | fair_p per second is in the replay, so fill+5 s is computable |
| B2 | `gate_pass.json` writer: timestamp, variant id, code hash, corpus hash, every criterion | `maker_sim.py --report` | |
| B3 | mechanical lock: refuse `DRY_RUN=false` without a fresh matching `gate_pass.json` | `maker_bot.py` | also log the flip with timestamp (R9) |
| B4 | variant switches: one-sided threshold (V1), top-of-book imbalance gate (V2), half-spread 3c/4c (V3, parameter exists), quote window 180-240 s (V4), open-fade first 15 s then cancel (V5), no re-quote on a filled side (V6) | `make_strategy.decide_quotes`, `maker_sim.py` | one code path for sim and bot, as the plan demands |
| B5 | V7 autopsy: for every sim fill, was the best bid moving through our price in the prior 1-2 s, and what did fair/mid do in the next 5 s | new `fill_autopsy.py` over `sim_fills.jsonl` + replay | diagnostic only |
| B6 | fit/test split tooling: `--fit-days` / `--test-days`, outputs under `variant_runs/<id>/` | `maker_sim.py` | |

## 3. Problems found in the plan as shipped

P1. **The bundle ships the broken v1 code, not v2.** All five .py files in `open_book_maker/` are byte-identical
to the original zip (phantom fills, wrong settlement rule, dead daily stop, .env never loaded). The v2 files
`fair_model.py`, `bme_to_replay.py`, `calibrate.py`, `notify.py`, `test_v2.py` are absent. The plan's text
points at `open_book_maker_v2/` on the VM, but anyone who runs the bundle itself re-creates the v1 numbers.
The V0 "instrument check" would then fail or, worse, pass against the wrong baseline.
Fix: replace the bundle's code with v2 from the repo before any run.

P2. **The data split is thinner than the plan thinks, and it does not need to be.** Only 4 replay days exist
(2026-10-06 to 10-09). "Fit on the first 3, test on the rest" leaves one test day, about 280 rounds, barely
R1. But the VM already holds the raw inputs for far more:

| input | days on twapvm |
|---|---|
| BME order-book snapshots (`bookstate_1s_*.csv`) | 2026-09-21 to 2026-10-15 (25 days, 56 GB) |
| S2 Chainlink spot + TWAP ticks | 2026-09-26 to 2026-10-15, continuous hourly sessions |
| buildable replay days (both inputs present) | 2026-09-26 to 2026-10-14, about 19 days, about 5,400 rounds |

Recommendation: build all 19 days with `bme_to_replay.py` (about 28 MB and one minute per day), fit on
3 days, test on the other 16 (about 4,500 rounds). That is what makes the confidence ranges mean something.

P3. **V2 cannot be built as written.** The book data has one price level per side (best bid and best ask
with their sizes). "Depth imbalance" and "our side depth >= k x opposite" can only be top-of-book size
imbalance. Pre-register that limitation or drop V2.

P4. **R10's arithmetic understates the swing.** At 20 shares per side with a 40-share cap, the worst
single round is about -$21 (40 shares of one side at about 0.53 paying zero), not $10. At 1-2 % of
capital that means $1,000-2,000, not $500-1,000. The live run's three rounds swung $9.80 to $12.60 only
because the account ran out of cash before the cap was reached on both sides.

P5. **F8 ("rewards rates: null, probed 2026-10-14") is unverified by us.** Our regime sentinel has
seen `clob_rewards.rates` toggle before, so a single probe is a snapshot, not a fact. It does not affect
the simulation either way because rewards were never counted in the gate.

P6. **HANDOFF-TO-AI.md still carries the old Phase A/B text** (run `book_recorder.py` 24/7, Phase B $10
pilot) under the new banner, and says the bundle lives in `~/open_book_maker` while the fixed code is in
`~/obm`. A fresh AI could follow the stale part. Minor, but it is exactly the kind of stale paragraph the
README banner warns about.

P7. **One prediction in the plan is already known from our data.** V3 (wider spread) was effectively
tested by the Phase A breakdown "every fair band loses, -1.5 c to -5.4 c per share"; widening the spread
moves fills into the bands that lose more. It is still cheap to run, but expect FAIL.

## 4. Honest expectation and recommended order

Everything here is simulation on data already on the VM. Cost: a few hours of build time, zero dollars.
Expected result: V0 reproduces; V1, V3, V4, V5 fail; V2 fails or cannot be built; V6 cuts the loss per
filled round but stays negative; V7 produces the one genuinely new piece of evidence (how many fills are
"run-throughs" versus benign touches), which is also the answer to the caveat in the Phase A results.

Order if the owner says go:
1. Sync the bundle's code to v2 (P1). Rebuild the replay corpus for 19 days (P2).
2. Add the R-gate, `gate_pass.json`, and the bot lock (B1-B3) and extend `test_v2.py` to cover them.
3. Run V0 and V7 first (instrument check + autopsy). If V0 does not reproduce, stop and audit.
4. Run V1-V6 as flags, fit days then test days, one results table, winners and losers both.
5. Apply the sunset clause and record it in the living record.

Nothing in this plan asks for, or permits, a live order. Account 2 stays at $32.46 either way.
