# REVIEW - open_book_maker bundle (read every file, tested locally) - 2026-10-10

Bundle: `open_book_maker.zip` (11 files, 1,380 lines), extracted to `open_book_maker_extracted/open_book_maker/`.
Tested in a scratch copy on this PC against the live feeds (14:19-14:26 UTC) and against the
existing book data on twapvm. Nothing was deployed. The original extracted files are untouched.

## Verdict in one line
The plumbing works (feed, gamma, CLOB book, chain verifier) but the two parts that decide
whether the strategy makes money - the fair-value model and the fill model - are both wrong,
and the documented daily pipeline crashes. Do not start Phase A with this code as-is.

## What works (verified)
- All 5 Python files byte-compile on Python 3.13.
- `book_recorder.py`: connects to RTDS with the correct compact filters, snaps the open
  reference within 1 s of the round boundary, writes one JSON line per second.
- `chain_verify.py`: works on a CSV that has `condition_id` + `outcome` columns (1/1 match,
  7 s), and the Data API v2 resolutions "house" cross-check endpoint exists and answers.
- `maker_bot.py` starts in DRY mode, logs round opens, connects the feed.
- CLOB `/book` endpoint answers from this PC and from twapvm (tick 0.01, min order 5).

## Errors found (E1-E12, each with evidence)

**E1 - The fair-value model is wrong by an order of magnitude (fatal for a maker).**
`model_for()` scales volatility as `1.5 * std(1-second TWAP changes) * sqrt(r/3)`. A 60-s TWAP
moves in tiny, highly correlated 1-s steps, so this sigma is about 10x too small and the
model becomes certain after a few dollars of drift. Same round (14:20 UTC), model vs market:

| t (s) | model fair_p | real Up market (BME bookstate) |
|---|---|---|
| 37 | 0.80 | 0.52 / 0.53 |
| 57 | 0.73 | 0.31 / 0.32 |
| 98 | 0.017 | 0.40 / 0.41 |
| 138 | 0.0003 | 0.37 / 0.38 |
| 178 | 0.0000 | 0.28 / 0.29 |

TWAP was only $6 (0.7 bps) below open with 200 s left and the model said 0.3% Up. A maker
anchored to this fair either does not quote (out of the 0.08-0.92 band) or rests on the wrong
side of the market and gets picked off. This is the same model the audit scored as
break-even for S1 as a late-round taker, where the remaining time is short and the error
matters less.

**E2 - The model uses the wrong settlement rule.** `fair_p` (`md["p"]`) is the probability
that the full-round AVERAGE ends above open. Settlement is TWAP60 at the end vs TWAP60 at the
start (proven 381/381 on 1-Hz ticks; the average rule is right only 84.8%). The correct
quantity, `fair_p_end`, is recorded but never used by the strategy.

**E3 - The fill model counts fills when nothing happened.** In `detect_fills` the condition
`nb < qb <= max(pb, qb)` is always true on the right side, so it reduces to "new best bid is
below our bid". Whenever our bid improves on the book (bid above best bid), every second is a
50% fill. Test: 1,000 ticks with an UNCHANGED book (bid 0.40 / ask 0.44), our bid 0.42 ->
473 fills counted. Correct answer: 0. Same for asks. A synthetic replay of one round with a
frozen book produced 2 fills, 400 shares net and PnL -$182 on a round where nothing traded.

**E4 - The simulator has no post-only guard.** In the same synthetic round the sim rested a
bid at 0.455 above the real ask 0.44 (it would have been a taker) and counted it as a maker
fill. The bot has the guard; the sim does not. Sim and bot therefore trade differently.

**E5 - Fills repeat forever.** A filled quote is never consumed; the same resting quote can
fill again on every tick. Only the 400-share inventory cap stops it.

**E6 - The documented daily step crashes.** `python chain_verify.py --csv sim_rounds.csv`
-> `KeyError: 'outcome'` (sim_rounds.csv has no `condition_id` and no `outcome` column; the
verifier needs both). Same for `bot_rounds.csv`. So nothing in this bundle ever settles a
sim round against the chain. Reproduced.

**E7 - `.env` is never loaded.** `maker_bot.py` contains no `load_dotenv`; it reads
`os.environ` only, and the systemd unit in instructions.md has no `EnvironmentFile`. Test:
`.env` with `DRY_RUN=false` -> bot starts in "mode=DRY RUN". Fail-safe today, but the
documented path to live mode does not work, and KILL_FILE / stops in `.env` are ignored.

**E8 - Live fills are never reconciled.** `client.get_trades(maker_only=True)` is not a valid
call in py-clob-client 0.34.6 (signature `get_trades(params: TradeParams=None, next_cursor)`).
It raises TypeError every second, which is swallowed -> ledger stays at zero -> the inventory
cap and the daily stop never see a single fill.

**E9 - The daily stop can never trip.** `Ledger.realized` is never updated; `new_round()`
wipes the round's position. The stop only sees the current round's mark, which cannot reach
$20 at 20 shares per side. Combined with E8 the rail is decorative.

**E10 - Round open loses the book for the whole round.** `get_tokens` caches `None` forever
on the first failure. In the live test the gamma `markets?slug=` lookup at 14:20:01 (1 s after
the boundary) returned nothing, so all 170 rows of the round have `bb = ba = null`; the direct
call 20 s later worked. Also `markets?slug=` returns 0 rows once a round has closed
(`events?slug=` always works).

**E11 - Sim and bot quote different prices and sizes.** Sim: 3-decimal prices (0.472) and
200 shares; venue tick is 0.01 (a 0.472 order is rejected). Bot: 2-decimal prices and 20
shares. The gate numbers from the sim do not describe what the bot would do.

**E12 - Replaying "all files" every day double-counts.** `sim_rounds.csv` and
`maker_stats.json` are append-only with no dedup; the documented daily command re-replays
every file.

Smaller: `requirements.txt` installs web3 and requests, neither is imported; the rewards
constants in the comments (3.5c max spread, 200 min size) do not match the live sentinel
(4.5c, 50); the heartbeat task leaks on every reconnect; `cancel_all()` cancels every order
of the wallet, including other bots on it.

## Step 0 (existing data) - what twapvm already has
- BME `bookstate_1s_YYYYMMDD.csv` (since 2026-09-21): best bid/ask/mid/depth per second for
  both tokens of every 5m and 15m round. This is a better book than the bundle's 1-Hz REST poll.
- S2 `ticks.jsonl`: Chainlink spot + TWAP60 ticks per second.
- S1 `rounds.csv` (7,502 rounds) and the chain-verified corpora in `mstack/history_tools/history/`
  with condition ids and chain outcomes.
The sim could be fed from these without running a new recorder, once E1-E5 are fixed.

## Other facts from the same session
- BME 7-day score finished 2026-10-07 11:51Z: S3 depth imbalance picks the winner 45.8% of
  the time, EV -0.0255 per share after fees. Verdict FAIL. S2 pulls predict move size, not
  direction.
- twapvm disk: 89 GB free (54% used), about 6.3 GB per day.

## Live test log (this PC, 14:19-14:30 UTC, scratch copy, nothing deployed)
- Recorder ran 2 rounds. Round 14:20: 280 rows, book null on every row (E10). Round 14:25:
  63 rows, book present on 62 -> the null book is a first-call failure cached for the round.
- Boundary probe at 14:25:00: gamma `events?slug=` listed the new round 0.8 s BEFORE the
  boundary; `markets?slug=` (the form the recorder uses) timed out at -0.8 s and had an SSL
  handshake error at +16.8 s. The recorder should use `events?slug=` and retry.
- The recorder's websocket died once with "1011 keepalive ping timeout" and reconnected on
  its own (backoff works).
- Real replay of the recording: round 14:20 -> 0 fills (no book). Round 14:25 (63 s of data)
  -> 2 SELL fills of 200 shares at 0.533 / 0.524, net -400 shares, PnL -$190.40 on one minute
  of quoting, settled Up via gamma. That is E3 + E5 on real data: the book barely moved and
  the sim sold 400 Up shares that nobody bought.
- Synthetic frozen-book round: 2 fills, -$182 (E3, E4, E5).
- `chain_verify.py --csv sim_rounds.csv` -> KeyError 'outcome' (E6), reproduced on the real
  sim output too.
- Local recorder stopped after the test (0 processes left). Test files live only in the
  session scratch folder.
