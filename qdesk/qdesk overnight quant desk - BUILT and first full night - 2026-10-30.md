# qdesk overnight quant desk: built, deployed, first full night (2026-10-30)

I'm explaining this in simple language a 15 year old can understand.

## What it is
A desk that runs on the research VM (twapvm) every night with no PC needed: Scout 01:00 UTC collects rounds,
1-minute prices, Hyperliquid funding, top wallets and the macro calendar; Quant 03:00 turns hypotheses into rules,
writes them down with a fingerprint BEFORE testing, tests them on our recorded order book with real fees and the
100 ms fill rule, and scores them 1-5 on five measures (18/25 to pass); Builder 05:30 locks a winner for the paper
bot; Brief 07:30 to the BTC-5-minute Telegram thread; Sunday review. Paper only: the bot has no live-money path.
Code: repo `C:\g\tlfix\qdesk` = VM `~/qdesk`. Built by 6 agents in parallel against one contract (INTERFACES.md).

## Proof
- 48/48 tests (hand-computed trade matches the engine; fast screen and exact replay agree on all 9 test trades;
  recorder and history build produce identical rows; forced "nothing passed" night writes no lock file).
- Real CLI call: the model read a planted "ignore everything" line as data.
- Bugs found and fixed during integration: an emptied book side (best bid 0) was kept as a stale price (~380
  locked-book seconds per round); a vanished ask within 100 ms now counts as not fillable; the brief silently
  mixed made-up sample files into a real night (now samples only with QD_USE_SAMPLES=1 and a banner).

## First full night (all 19 recorded days, 14 library rules, exact replay on the 10 holdout days)
Verdict NOTHING PASSED. Best rules on the holdout:

| rule | idea | holdout trades | net per dollar | score |
|---|---|---|---|---|
| LIB-07 | bid-heavy book + momentum agreement, taker | 450 | -7.98% (CI -17.9% .. +2.8%) | 9/25 |
| LIB-08 | follow taker flow + momentum, taker | 659 | -19.81% | 9/25 |
| LIB-01 | momentum after a 4 bps 5-s move, taker | 90 | -41.36% | 7/25 |

Every rule with trades lost on the holdout. Runtime: feature build 1 h 40 min for 17 days (P=3), quant 2 h 18 min
(tier-2 replay of 3 rules on 10 days).

## Known limits
- With 9 fit days there is only ONE walk-forward window, so "stability" can score at most 1 of 5 until live
  recorder days accumulate (the recorder adds one never-seen day per night once installed).
- Moon Dev fields are NA until a working key is in `~/qdesk/.env`.
- Macro calendar ends 2026-11-12.

## What the owner must do (never through chat)
1. On twapvm: install Node + the Claude CLI and run `claude login` once (until then hypotheses come from the fixed library).
2. Put MOONDEV_API_KEY in `~/qdesk/.env` (chmod 600), copied file to file.
3. Run the sudo lines printed by `bash ~/qdesk/deploy/install.sh` (timers + recorder + paper bot services).
4. Approve the scoring thresholds in INTERFACES.md section 4.
