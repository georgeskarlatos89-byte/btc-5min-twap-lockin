# OPEN-BOOK-MAKER (#11) - live test and backtest summary

Strategy: two-sided maker on Polymarket BTC 5-minute Up/Down rounds. It rests a BUY on both the Up
and the Down token a little below its own "fair" price, hoping to earn the 1.5-cent half-spread
and cancel before the price runs through it.
Account: account 2 (proxy ...B6A3Ed) on twapvm. Size: 20 shares per side (about $10), cap 40 shares
net per side, daily stop $20. Status: STOPPED FOR GOOD on 2026-10-10 17:07 UTC. Do not revive.

Sources (all on twapvm, `~/obm/`): maker_bot.log, bot_logs/orders.jsonl, bot_logs/bot_rounds.csv,
CLOB trade history for the account (maker_orders entries with our funder), chain-verified outcome
corpus, Gamma. Every number below comes from those files, not from memory.

---------------------------------------------------------------------------------------------------
## 1. The live test, minute by minute (2026-10-10, UTC)

| time  | what happened |
|-------|---------------|
| 15:59 | bot started in DRY RUN to check plumbing (no orders). |
| 16:01 | switched to LIVE. CLOB balance **$65.07**. |
| 16:05 | round 16:05-16:10 opens. Bid for 20 Up @0.51 filled within 7 s. A second Up bid @0.12 filled at 16:06 (the price had crashed, the model's fair fell with it, the new bid was hit on the way down). 40 Up shares held, cost $12.60. |
| 16:06 | paused with the KILL file: the fill parser had read the other side's trade record (showed 140 Down shares that were never ours). Code fixed to read only our maker entries. No orders were affected. |
| 16:08 | restarted LIVE. Balance **$52.47** (65.07 minus the $12.60 spent). |
| 16:10 | 16:05 round settled **Down**. Our 40 Up shares paid $0. Round 1: **-$12.60**. |
| 16:10 | round 16:10-16:15: 54 Up bids placed and cancelled as the price ran up (0.55 to 0.90), none filled. $0. |
| 16:15 | round 16:15-16:20: Down bid 20 @0.49 filled at 16:15:14 (15 s in). Then 67 Up bids 0.48 to 0.90, none filled. |
| 16:20 | 16:15 round settled **Up**. 20 Down shares paid $0. Round 2: **-$9.80**. |
| 16:20 | round 16:20-16:25: Up 20 @0.53 filled at 16:20:18; Down 20 @0.45 filled 16:20:28-30; Up 20 more @0.53 filled 16:21:26 (cap of 40 Up reached). 60 shares held, cost $30.20. Cash down to **$12.48**. |
| 16:21-16:24 | 64 further Down bids rejected by the CLOB: "not enough balance" (cash below the $13-18 each would cost). |
| 16:24 | ledger reached -$20.00 for the day -> **DAILY STOP**. All orders cancelled, bot idle. |
| 16:25 | 16:20 round settled **Down**. 40 Up paid $0, 20 Down paid $20. Round 3: **-$10.20** net. Cash back up to **$32.46**. |
| 16:25-17:07 | idle (stop active). Service disabled, KILL file set, DRY_RUN=true, cancel_all confirmed at 17:07. |

Earlier fills on this account (2026-09-17, 5-share fills on 15-minute rounds) belong to an older
S1/S3 test, not to this strategy, and are excluded.

---------------------------------------------------------------------------------------------------
## 2. Every fill, from the CLOB trade history (our maker entries only)

| round (UTC) | filled | side | price | shares | paid | round result | payout | P&L |
|---|---|---|---|---|---|---|---|---|
| 16:05-16:10 | 16:05:07 | Up | 0.51 | 20 | $10.20 | Down | $0 | -$10.20 |
| 16:05-16:10 | 16:06:13 | Up | 0.12 | 20 | $2.40 | Down | $0 | -$2.40 |
| 16:15-16:20 | 16:15:14 | Down | 0.49 | 20 | $9.80 | Up | $0 | -$9.80 |
| 16:20-16:25 | 16:20:18 | Up | 0.53 | 20 | $10.60 | Down | $0 | -$10.60 |
| 16:20-16:25 | 16:20:28-30 | Down | 0.45 | 20 | $9.00 | Down | $20.00 | +$11.00 |
| 16:20-16:25 | 16:21:26 | Up | 0.53 | 20 | $10.60 | Down | $0 | -$10.60 |
| **total** | | | | **120** | **$52.60** | | **$20.00** | **-$32.60** |

Outcomes checked two ways: chain-verified corpus (condition ids 0x8ac8b6a1..., 0x0b8c3627...,
0x6e33bf7b...) and Gamma outcomePrices. Both agree: Down, Up, Down.

Per-share summary: 120 shares bought, 20 won, 100 lost. Win rate by shares 17%. The backtest's
win rate was 44%. Average loss per share live: 27 cents. Backtest: 6.25 cents.

---------------------------------------------------------------------------------------------------
## 3. How the balance moved

| time | CLOB cash | change | why |
|---|---|---|---|
| 16:01 | $65.07 | | start |
| 16:08 | $52.47 | -$12.60 | bought 40 Up (16:05 round) |
| 16:10 | $52.47 | $0 | 16:05 round lost: shares expire worthless, nothing paid back |
| 16:15 | $42.67 | -$9.80 | bought 20 Down (16:15 round) |
| 16:20 | $42.67 | $0 | 16:15 round lost |
| 16:21 | $12.48 | -$30.20 | bought 40 Up + 20 Down (16:20 round) (CLOB error text shows 12.4758) |
| 16:25 | $32.46 | +$20.00 | 16:20 round Down: the 20 Down shares pay $1 each |
| 17:07 | $32.46 | | final, after cancel_all; nothing open, nothing pending |

Start $65.07, end $32.46, lost **$32.61** (one cent of rounding on fractional fills). No fees were
charged (Polymarket charges makers no fee on these markets). Nothing was lost to bugs: the fill-parser
bug at 16:05 only affected the Telegram messages and the bot's own ledger, not any order.

Why a round costs $10, not $1: a share pays $1 if its side wins and $0 if it loses. Buying 20 shares at
about 0.50 is a $10 bet that either becomes $20 or $0. The 1.5-cent "edge" only shows up as an average
over many rounds. In any single round the swing is the full $10 per 20 shares, and the bot can hold
up to 40 per side, so a round can swing $20 to $30.

---------------------------------------------------------------------------------------------------
## 4. The backtest (Phase A, run 2026-10-10 15:19 UTC, before going live)

Data: 4 days of recorded Polymarket order books (2026-10-06 to 10-09, one snapshot per second) plus
Chainlink spot and TWAP ticks, replayed through the same quoting code. Outcomes from the chain
(60 random rounds audited: 60 match, 0 differ). Same size: 20 shares per side, cap 40.

| | backtest | needed to pass |
|---|---|---|
| rounds replayed | 1,141 | >= 200 |
| rounds with at least one fill | 1,038 (91%) | |
| fills | 3,580 (of 20 shares) | |
| money that went through fills | $35,550 | |
| net P&L | **-$2,825** | > 0 |
| return on filled money | **-7.95%** (95% range -10.2% to -5.6%) | better than -5.5% |
| edge per filled share | **-6.25 cents** | >= 0 |
| worst day | **-$977** | > -$20 |
| day by day | 10-06 -$544, 10-07 -$977, 10-08 -$790, 10-09 -$514 | every day positive |

Gate result: **FAIL on 4 of 5 criteria.** Only the round count passed.

What the backtest said about why it loses:
- fills land on the winning side only 43.8% of the time: a resting bid gets hit when the price is
  moving through it, i.e. exactly when the model is about to be wrong (adverse selection).
- both sides lose: Up buys -3.6 cents per share, Down buys -4.3 cents.
- every time window of the round loses; worst are the first 60 s and the last 60 s before blackout.
- every fair-price band loses. There was no corner of the strategy to keep.

The one thing in the package that worked: the fair-price model. Its predictions were honest at every
time point (Brier 0.195 vs market 0.195 at 60 s; 0.055 vs market 0.111 at 240 s). Knowing the fair
price is not enough when your orders only get filled by people who already know where the price is
going.

---------------------------------------------------------------------------------------------------
## 5. Backtest vs live, side by side

| | backtest (1,141 rounds) | live (3 filled rounds) |
|---|---|---|
| fills that won | 44% | 17% (1 of 6 fills, 20 of 120 shares) |
| loss per filled share | 6.25 cents | 27 cents |
| loss per filled round | about $2.70 | $10.87 |
| return on money through fills | -7.95% | -62% ($32.60 of $52.60) |

Three rounds is far too few to say the live result is "worse" than the backtest in a statistical
sense. What it does show: the live market behaved the way the backtest warned (every filled round was
on the losing side), and nothing suggested the backtest was too pessimistic. The reason for the live
test, "I don't trust this data", was a fair check. The check came back agreeing with the data.

---------------------------------------------------------------------------------------------------
## 6. Where things stand

- Strategy #11 is closed. Service disabled, KILL file present, DRY_RUN=true, no open orders.
- Account 2 holds $32.46 in cash, no positions.
- Reusable pieces kept in `open_book_maker_v2/`: fair_model.py (calibrated), bme_to_replay.py
  (book + tick replay builder), chain_verify.py, the account-2 live plumbing (proxy signing, correct
  maker-fill parsing).
- Full review of the original zip (12 errors found and fixed) is in
  `REVIEW - open_book_maker - 2026-10-10.md` in the same folder.
