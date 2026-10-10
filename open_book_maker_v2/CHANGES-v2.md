# CHANGES-v2.md - what was fixed vs the zip (2026-10-10)

Every item maps to the review file `REVIEW - open_book_maker - 2026-10-10.md` (E1-E12).
The original extracted files are untouched in `open_book_maker_extracted/`.

| # | Problem in the zip | Fix in v2 | Where |
|---|---|---|---|
| E1 | Fair model sigma ~10x too small (std of 1-s TWAP steps) | sigma = std of 10-s SPOT changes over 30 min / sqrt(10), floor 0.3 bps/s, cached 15 s | `fair_model.py` |
| E2 | Fair = full-round-average rule | Fair = P(TWAP60 at end >= TWAP60 at start); for the last 60 s the known part of the window is used | `fair_model.py` |
| E3 | Fill counted whenever our bid was above the best bid, even with an unchanged book | fill only when the displayed best bid falls THROUGH our price (prev >= ours > new), 50% queue probability | `make_strategy.detect_fills` |
| E4 | Sim had no post-only check | `decide_quotes` drops a side that would cross the touch; same code in sim and bot | `make_strategy.decide_quotes` |
| E5 | A filled quote kept filling every tick | `Resting` tracks price + age; a fill consumes the side; a fresh quote cannot fill on its first tick | `make_strategy.Resting` |
| E6 | `chain_verify.py --csv sim_rounds.csv` -> KeyError 'outcome' | sim/bot CSVs carry `condition_id` + `outcome`; verifier also accepts `settled`, skips rows without an id | `maker_sim.py`, `chain_verify.py` |
| E7 | `.env` never loaded | `load_dotenv` in the bot and in make_strategy (sizes) | `maker_bot.py`, `make_strategy.py` |
| E8 | `get_trades(maker_only=True)` raised every second (swallowed) | `get_trades(TradeParams(maker_address=...))`, MAKER side, filtered to this round's tokens, idempotent by trade id, warning logged on failure | `maker_bot.poll_fills` |
| E9 | `Ledger.realized` never updated -> daily stop dead | round result booked into `realized` at round end (model end-value = settlement rule); stop checked every loop | `maker_bot.Ledger` |
| E10 | First failed market lookup cached for the whole round; `markets?slug=` times out at the boundary | `events?slug=` with 2 retries, failures retried after 2 s, never cached | `book_recorder.get_tokens` |
| E11 | Sim 3-decimal prices / 200 shares vs bot 2-decimal / 20 shares | one `round_tick` (0.01) and one `QUOTE_SIZE` / `MAX_NET_SHARES` from `.env` for both | `make_strategy.py` |
| E12 | Daily re-replay double-counted rounds | rounds already in `sim_rounds.csv` are skipped; stats recomputed from the CSV; `--report` prints the gate with a bootstrap CI | `maker_sim.py` |
| - | web3 / requests installed but unused | removed from requirements | `requirements.txt` |
| - | heartbeat task leaked on every reconnect | cancelled in `finally` | `book_recorder.py` |
| - | `cancel_all()` on every round end cancels other bots on the wallet | cancels our order ids; `cancel_all` only for the kill file / daily stop | `maker_bot.py` |
| - | Step 0 (reuse existing data) had no tool | `bme_to_replay.py` builds replay days from BME bookstate + S2 ticks already on twapvm | new |

## Still true, by design
- Fill model is CONSERVATIVE: when our bid improves on the displayed book a seller hitting us
  leaves no trace in a top-of-book snapshot, so that fill is not counted. The sim under-counts
  fills rather than inventing them.
- Rebates / rewards are not counted. Maker fee 0.
- `DRY_RUN=true` until the gate in DECISION.md passes AND the owner says go.

## How to run (unchanged commands, now working)
```
python test_v2.py                                   # unit tests, no network
python bme_to_replay.py --day 2026-10-09            # on twapvm: build replay/2026-10-09.jsonl
python maker_sim.py --replay replay/*.jsonl --labels ~/mstack/history_tools/history/BTC_fiveminute_long.relabeled.csv
python chain_verify.py --csv sim_rounds.csv --sample 100
python maker_sim.py --report
```
