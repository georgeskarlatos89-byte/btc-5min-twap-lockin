# v2-open-maker-inspect-order-book - RESULTS: the notebook ideas measured on 19 recorded days, all 34 rules FAIL (2026-10-27)

I'm explaining this in simple language a 15 year old can understand.

## What was built (repo `v2-open-maker-inspect-order-book/`, VM twapvm `~/v2-open-maker-inspect-order-book/`)

- `book_reader.py`: the "inspect" as a program. `--snapshot` prints the current round's full order book once,
  `--live` prints one line per side per second (best bid, best ask, spread, top-5 bid sizes, bid-vs-ask imbalance,
  taker buys/sells in the last 10 s, bitcoin price, price to beat, Up chance), `--replay START` prints the same
  for any recorded round. Read-only, no keys.
- `inspect_lab.py`: every notebook question turned into a measurement on the recorded book of 19 days
  (26 Sep - 14 Oct 2026, 5,432 rounds), with our measured 0.1 s order delay and the same queue rules as the
  paired-maker replay (we fill only after the shares already resting at our price; our pretend orders do not
  change the recorded book). `inspect_report.py` builds the tables; VM run took 11 h.
- Fees, from the Polymarket fees page (read 2026-10-26): takers in crypto markets pay 0.07 x price x (1 - price)
  per share (1.75 cents at 50c), makers pay nothing, and 20% of taker fees is paid back daily to makers. That is
  about 0.35 cents per share at 50c, which matches the 0.37% of turnover gabigol actually received. Every table
  below shows the result with and without that rebate.

Gate for every rule, agreed before running: profit 95% CI low above zero after the rebate AND at least 95% of the
filled shares paired.

## 2a "Will a taker trade at my price?" - one 20-share bid, held to the end of the round

| bid placed | at 270 s left | 180 s left | 60 s left | 30 s left | 7 s left (your 4:53) |
|---|---|---|---|---|---|
| at the best bid: filled / worth per share | 94% / -2.3c | 90% / -2.4c | 57% / -2.4c | 34% / -1.4c | 10% / -1.2c |
| 1 cent below: filled / worth per share | 91% / -2.6c | 85% / -2.9c | 44% / -3.1c | 16% / -2.8c | 1.6% / -5.4c |
| 2 cents below: filled / worth per share | 89% / -2.8c | 82% / -3.2c | 40% / -3.6c | 13% / -3.7c | 1.3% / -6.7c |

"Worth per share" = what the filled share was worth at settlement minus what we paid, before the rebate (the
rebate adds +0.15 to +0.3c). Takers do trade at our price: a bid at the best bid fills 9 times out of 10 early
in the round, usually within 2-3 seconds. But every row is negative: the shares we get are the ones someone
wanted to dump because the price had just moved against us. Placing one cent lower does not help, it fills less
often and the fills are worse, because the taker only reaches us after eating the better level first.

## 2b The notebook times (1:00, 2:00, 3:00, 4:00, 4:30, 4:53 left) - both sides at once

| posted with ... left | 270 s | 240 s | 180 s | 120 s | 60 s | 30 s | 7 s |
|---|---|---|---|---|---|---|---|
| rounds | 4,893 | 4,911 | 4,859 | 4,218 | 2,308 | 870 | 344 |
| both sides filled | 88% | 85% | 80% | 73% | 57% | 47% | 10% |
| pair completion | 93% | 91% | 89% | 84% | 73% | 74% | 60% |
| return on cost (before rebate) | -4.8% | -5.2% | -5.3% | -5.6% | -7.0% | -5.3% | -7.4% |
| profit + rebate 95% CI | [-4,152, -3,324] | [-4,425, -3,580] | [-4,318, -3,503] | [-3,649, -2,937] | [-2,102, -1,626] | [-588, -334] | [-151, -23] |

All 30 time/offset combinations FAIL. Later is worse, not better: with 60 s left the pair rarely completes, so we
are left holding one side, and that side is the loser more often than not. The 4:53 idea (7 s left) fills in only
1 round in 10 and loses when it does. A note on why late rounds have so few rounds in the table: by then one side
is often already at 1-2 cents, and we never bid below 2 cents.

## 2c "Numbers people don't think of": odd, even and round prices

| our bid price | orders | filled | median shares ahead of us | worth per share |
|---|---|---|---|---|
| at best bid, even cent | 30,748 | 86% | 347 | -2.8c |
| at best bid, odd cent | 38,815 | 65% | 682 | -2.1c |
| at best bid, round (x0/x5) | 16,486 | 84% | 172 | -1.8c |
| 1 below, even cent | 39,832 | 61% | 484 | -2.2c |
| 1 below, odd cent | 30,614 | 74% | 383 | -3.4c |
| 1 below, round (x0/x5) | 13,716 | 83% | 326 | -2.6c |

Crowding (average shares resting) when the best bid is at an even cent: 580 at the best bid; at an odd cent:
6,175; at a round number: 3,764 with 5,445 one cent below. The odd-cent crowd is almost entirely the 1-cent and
99-cent walls on a side that is already decided, so "odd numbers" is not a trick people miss, it is where the
biggest walls live. No parity choice turns the edge positive; the best is round numbers at -1.8c per share.
The price grid is 1 cent in the middle of the book, so there is no "in-between" price to hide at.

## 2d "Read the flow, then place" - continuous both-sides quoting with filters

| variant | rounds | fills | money in | profit | +rebate | return | profit+rebate 95% CI | pair completion | days + | gate |
|---|---|---|---|---|---|---|---|---|---|---|
| base (quote always) | 5,430 | 113,206 | $534,458 | -$21,558 | +$3,264 | -4.0% | [-20,437, -16,041] | 84% | 0/19 | FAIL |
| sweep-pull (stop 5 s after a 30-share hit or a 2c drop) | 5,188 | 20,384 | $113,499 | -$2,861 | +$561 | -2.5% | [-4,234, -341] | 30% | 6/19 | FAIL |
| imbalance (quote only when bids outweigh asks) | 5,401 | 37,670 | $171,088 | -$2,092 | +$1,015 | -1.2% | [-3,283, +1,164] | 56% | 8/19 | FAIL |
| both filters | 1,942 | 3,122 | $17,328 | -$79 | +$80 | -0.5% | [-779, +711] | 10% | 8/19 | FAIL |

Reading the flow helps in the sense that the filters avoid the worst fills: the loss per dollar shrinks from 4%
to about 1%. But it does so by refusing to trade most of the time (volume drops 3x to 30x), the result is still
not above zero (the two best ranges straddle zero = no detectable edge), and the pairing rate collapses because
the filter is often "on" for one side and "off" for the other, which leaves naked legs. Pulling the quote at our
measured speed is also too late: the hit that triggers the filter has usually already filled us.

## 2e After a fill: sell at once, pair at once, or hold?

| exit | fills | shares | total | per share |
|---|---|---|---|---|
| A: sell at once at the best bid (taker, fee paid) | 113,206 | 1.19 M | -$36,505 | -3.1c |
| C: hold the same fills to settlement | 113,206 | 1.19 M | -$21,558 | -1.8c |
| B: pair at once by buying the other side at its ask (only possible when the pair costs <= 0.97) | 747 | 8,393 | +$370 | +4.4c |
| C: hold those same 747 fills | 747 | 8,393 | +$866 | +10.3c |

Selling immediately with a limit order to "remove the position" is the worst choice: you pay the spread and the
taker fee and lose 3.1c per share instead of 1.8c. Pairing immediately as a taker is almost never available
(747 of 113,206 fills), and in those rare cases holding would have earned more, because a cheap other side
means our side was the one winning.

## 2f Rebates

The rebate is real but small: about 0.6% of money put in (the fee is biggest at 50c and shrinks toward 1c/99c).
Against losses of 4-7% it cannot flip any row, and it is not paid on the BTC 5-minute "liquidity rewards"
programme either (those markets are not in it), only on fills that happen.

## Verdict

34 rules tested, 0 PASS, 34 FAIL. The notebook questions now have measured answers:
- the "inspect" view exists as a tool and shows exactly what the page sees;
- takers do trade at our price, but the ones who do are the ones who know the price just moved;
- fixed posting times, odd/even prices and flow filters change how much we lose, not the sign;
- selling at once to exit is worse than holding; pairing at once is rarely possible;
- the maker rebate is about a third of a cent per share, far below the 2-3 cents each fill loses.
Together with the paired-maker replay (same day, 19 days, every latency setting negative, even instant), this
closes the "resting bid on BTC 5-minute" family on this order book. Nothing live was touched; account 2 unchanged.

Files: repo `v2-open-maker-inspect-order-book/results/INSPECT-RESULTS.md` (full tables), VM
`~/v2-open-maker-inspect-order-book/inspect_out/` (per-day JSON with every pretend order and fill), plan
`C:\g\BTC 5-minute - ORDER BOOK READER plan from the notebook idea - 2026-10-26.md`.
