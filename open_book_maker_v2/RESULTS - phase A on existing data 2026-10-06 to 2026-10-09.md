# RESULTS - OPEN-BOOK-MAKER v2, Phase A on existing data (2026-10-06 to 2026-10-09)

Run 2026-10-10 15:19 UTC on twapvm in `~/#11 open-book maker/` (no service, one-shot, nice 19).
Inputs: BME book state (per-second best bid/ask of the Up token) + S2 Chainlink spot/TWAP ticks,
both already on the VM (Step 0 reuse). Labels: chain-verified corpus. No new recorder was started.
Raw outputs in `results/` (sim_rounds.csv, sim_rounds.chain.csv, maker_stats.json, calibration.txt,
phase_a_2026-10-06_to_2026-10-09.txt).

## Pipeline checks (all passed)
- 28 unit tests pass locally and on the VM (`test_v2.py`).
- 1,141 rounds replayed (4 days; 11 rounds skipped for no TWAP tick within 3 s of open).
- Chain audit of 60 random rounds: 60 match, 0 differ, 0 house flags.
- Re-replaying a day a second time adds 0 rounds (dedup works).
- Fair model calibration vs chain outcomes (what it says vs what happens): honest at every
  time point. At 60 s in, the "0.9-1.0" bucket settled Up 91.9%, the "0.0-0.1" bucket 2.1%.
  Brier score equals the market's early in the round (0.195 vs 0.195 at 60 s) and beats it
  late (0.055 vs 0.111 at 240 s). The v1 model is gone; this one can be trusted as a fair.

## The gate (DECISION.md) - FAIL
| criterion | needed | got |
|---|---|---|
| settled rounds | >= 200 | 1,141 |
| net PnL | > 0 | **-$2,825** (3,580 fills of 20 shares, $35,550 filled notional) |
| return vs the -5.5% passive-MM benchmark | 95% CI above -5.5% | **-7.95%**, CI -10.2% to -5.6% |
| adverse-selection edge per filled share | >= 0 | **-6.25 cents** |
| worst day | > -$20 | **-$977** (every day negative: -544, -977, -790, -514) |

## Why it loses (from sim_fills.jsonl)
- Fills land on the side that eventually wins only 43.8% of the time. A resting bid gets hit
  when the price is moving through it, i.e. exactly when the model is about to be wrong.
- Both sides lose: buys -3.6c per share, sells -4.3c.
- Every time window loses: worst in the first 60 s (-6.1c, -4.9c) and the last 60 s before the
  blackout (-8.4c, -16.7c); the best window (60-90 s) is still -0.5c.
- Every fair band loses (-1.5c to -5.4c per share).
So there is no "ITERATE" window per DECISION.md: no time-of-round or fair band where quoting
is profitable.

## Caveat, stated plainly
The fill model counts a fill only when the displayed best bid falls THROUGH our price. Those
are, by construction, the moments the market moves against us. Fills from a seller hitting
us while we sit at the touch leave no trace in a top-of-book snapshot and are not counted; some
of those would be benign. For the strategy to break even, the uncounted benign fills would
have to earn the full 1.5c half-spread AND outnumber the counted ones about four to one. The
public -5.5% benchmark came from a real account; our number is in the same place.

## Recommendation
Do not fund Phase B. The idea (earn the half-spread as a maker and cancel fast) does not
survive the only fair model that is calibrated, on 1,141 real rounds. Keep the v2 code: the
fair model and the chain-verified pipeline are reusable, the quoting strategy is not.
