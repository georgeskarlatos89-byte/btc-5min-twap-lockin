# OPEN-BOOK-MAKER — maker bot bundle for Polymarket BTC 5-minute rounds

Built 2026-10-07 from the proven `maker_lab` modules + new live quoting bot.
This folder is SELF-CONTAINED: copy it to a VPS and follow `instructions.md`.

## Files

| File | Role |
|---|---|
| `maker_bot.py` | **The live maker bot** (DRY RUN by default). Posts two-sided BUY bids around model fair value, cancels fast, stands down in the final seconds, kill switch + daily stop included |
| `make_strategy.py` | Quoting constants + fill model + accounting (pure, unit-testable, shared by sim and bot) |
| `maker_sim.py` | Backtest/replay engine — the PHASE-A GATE runs through this |
| `book_recorder.py` | Data capture: Chainlink TWAP+spot (RTDS websocket), fair model, CLOB book — 1 record/sec/round. No credentials needed |
| `chain_verify.py` | Settles round verdicts against Polygon CTF ground truth (not Polymarket's feed) |
| `instructions.md` | VPS setup, phase plan, decision rules |
| `HANDOFF-TO-AI.md` | Exact prompt to hand to a setup AI |
| `DECISION.md` | Timeframes + go/no-go criteria |
| `.env.example` | Config template (copy to `.env`, chmod 600) |

## The strategy in one paragraph

Every 5 minutes a new BTC up/down market opens. The bot computes fair value
from the settlement feed itself (Chainlink 60s-TWAP vs the round's open
reference, scaled by live volatility), then bids for the UP token below fair
and the DOWN token below (1−fair). People selling into those bids pay us the
half-spread; fast cancels protect us when fair moves; the final-seconds
blackout protects us from informed flow. Zero maker fee; rebates/rewards are
upside, never counted in the gate.

## Why it's different from the −5.5% public maker test

marv's passive market-making result (−5.5%) used: hold everything to
resolution, no cancels, no rebates. This bot is the opposite: cancel-and-requote
on every fair drift, stand down before settlement gamma, tiny capped inventory.
The sim gate must show this design beats his −5.5% before any real money moves.

## Safety rails (all enforced in code)

1. DRY RUN by default — nothing trades until `.env` says so.
2. Post-only by construction — any quote that would cross the touch is skipped.
3. No quoting in the last `NO_QUOTE_SECS` (20s) of a round; cancel-all at end.
4. Inventory cap (`MAX_NET_SHARES`) and quote size (`LIVE_QUOTE_SIZE`).
5. Daily stop (`DAILY_STOP_USD=20`) → cancel all, halt until next UTC day.
6. Kill file → cancel all within ~1s, standby.
7. Every verdict settles against the blockchain, not the venue's own feed.
