# Wallet audit from the public activity feed - 1,335,054 activity rows, 58,799 markets, 2026-08-07 .. 2026-10-27

Row types: TRADE 1,225,260, REDEEM 108,824, MERGE 854, TAKER_REBATE 83, MAKER_REBATE 33
Rebate rows in the feed: taker $106,217, maker $13,541
Taker-only feed rows: 808,180

## By market type (cash flow = sells + redeems + merges - buys - splits; before fees, before rebates)

| type | markets | fills | fills/market | money in (buys) | profit | return on buys | taker share of fills | taker share of $ | markets won |
|---|---|---|---|---|---|---|---|---|---|
| BTC 5m | 25,258 | 842,955 | 33.4 | $11,349,584 | $34,696 | +0.31% | 51.0% | 75.2% | 50.1% |
| ETH 5m | 17,191 | 230,771 | 13.4 | $1,608,733 | $6,547 | +0.41% | 100.0% | 100.0% | 46.1% |
| SOL 5m | 15,769 | 147,518 | 9.4 | $1,025,010 | $4,701 | +0.46% | 100.0% | 100.0% | 43.5% |
| esports | 581 | 4,016 | 6.9 | $48,720 | $2,455 | +5.04% | 99.6% | 99.8% | 57.5% |

## The crypto 5-minute tactic (58,218 rounds with a parsable start time)

- rounds where it bought BOTH Up and Down: 52,877 (90.8%)
- buy price per share: median 0.47, 10th pct 0.11, 90th pct 0.87 (1,221,244 buy fills)
- most common buy prices: 0.06 (1.4%), 0.05 (1.4%), 0.04 (1.4%), 0.08 (1.3%), 0.07 (1.2%), 0.11 (1.2%), 0.09 (1.2%), 0.10 (1.2%)
- when in the round it buys (seconds after the round start): median 147 s, 10th pct 35 s, 90th pct 246 s; share of buys in the last 60 s: 12.5%; after the round ended: 5.8%
- money per round (buys): median $88, 90th pct $714, max $4,179
- fills per round: median 12, mean 21.0, max 570
- taker fills: 808,503 of 1,221,244 = 66.2% of fills, 79.9% of dollars
- in both-sides rounds: median pair cost (avg Up + avg Down buy price, net shares) 0.991, 90th pct 1.113; rounds with pair cost < 1.00: 54.4%; median unpaired share of the round's shares 11.6%
- rounds won 27,441 (47.1%), lost 30,777; average win $16.67, average loss $-13.37
- profit per round: mean $0.79, median $-0.60

| round's taker share of fills | rounds | money in | profit | return |
|---|---|---|---|---|
| 0% (pure maker) | 19 | $3,174 | $-66 | -2.09% |
| 1-50% | 6,102 | $5,847,011 | $1,470 | +0.03% |
| 51-99% | 1,910 | $1,011,302 | $5,581 | +0.55% |
| 100% (pure taker) | 50,187 | $7,121,840 | $38,958 | +0.55% |

## Top 12 crypto rounds by profit (to compare with the screenshot)

| round | buys $ | profit | Up shares bought | Down shares bought | avg Up | avg Down | fills | taker fills |
|---|---|---|---|---|---|---|---|---|
| Bitcoin Up or Down - October 17, 6:25PM-6:30PM ET | $1,755 | $570 | 2,325 | 1,592 | 0.51 | 0.35 | 216 | 22 |
| Bitcoin Up or Down - October 10, 5:05PM-5:10PM ET | $3,224 | $325 | 3,454 | 3,550 | 0.43 | 0.49 | 570 | 15 |
| Bitcoin Up or Down - October 3, 6:00AM-6:05AM ET | $1,860 | $265 | 2,124 | 2,051 | 0.34 | 0.55 | 212 | 28 |
| Bitcoin Up or Down - October 3, 9:30PM-9:35PM ET | $1,064 | $264 | 1,328 | 1,166 | 0.34 | 0.53 | 80 | 34 |
| Bitcoin Up or Down - October 17, 9:15AM-9:20AM ET | $1,630 | $246 | 1,876 | 1,834 | 0.39 | 0.49 | 129 | 38 |
| Bitcoin Up or Down - October 20, 1:20PM-1:25PM ET | $1,858 | $241 | 1,883 | 2,099 | 0.72 | 0.24 | 147 | 31 |
| Bitcoin Up or Down - September 19, 12:25AM-12:30AM ET | $1,126 | $238 | 1,234 | 1,364 | 0.30 | 0.55 | 38 | 38 |
| Bitcoin Up or Down - September 15, 1:30AM-1:35AM ET | $1,473 | $227 | 1,606 | 1,701 | 0.35 | 0.53 | 56 | 56 |
| Bitcoin Up or Down - September 22, 1:00AM-1:05AM ET | $503 | $223 | 559 | 726 | 0.26 | 0.49 | 26 | 18 |
| Bitcoin Up or Down - October 6, 4:35PM-4:40PM ET | $1,116 | $211 | 1,327 | 1,234 | 0.31 | 0.57 | 100 | 25 |
| Bitcoin Up or Down - October 2, 9:35PM-9:40PM ET | $1,551 | $210 | 1,636 | 1,762 | 0.40 | 0.51 | 164 | 29 |
| Bitcoin Up or Down - October 14, 5:35PM-5:40PM ET | $184 | $210 | 200 | 394 | 0.47 | 0.23 | 19 | 7 |

## Worst 6 crypto rounds

- Bitcoin Up or Down - October 17, 11:05AM-11:10AM ET: buys $2,509, profit $-328, Up 2,531 sh, Down 2,181 sh, fills 156, taker 65
- Bitcoin Up or Down - October 8, 8:35AM-8:40AM ET: buys $2,262, profit $-324, Up 1,839 sh, Down 1,938 sh, fills 133, taker 29
- Bitcoin Up or Down - September 29, 6:20AM-6:25AM ET: buys $2,221, profit $-322, Up 2,050 sh, Down 1,899 sh, fills 143, taker 45
- Bitcoin Up or Down - October 12, 2:20PM-2:25PM ET: buys $2,451, profit $-310, Up 2,140 sh, Down 2,208 sh, fills 144, taker 60
- Bitcoin Up or Down - October 4, 8:55AM-9:00AM ET: buys $2,248, profit $-309, Up 1,938 sh, Down 2,118 sh, fills 174, taker 40
- Bitcoin Up or Down - September 20, 4:45PM-4:50PM ET: buys $1,088, profit $-290, Up 991 sh, Down 798 sh, fills 38, taker 26

- top 10 rounds = $2,809 of $45,944 total crypto profit (6.1%)
- top 100 rounds = $17,572 of $45,944 total crypto profit (38.2%)
- top 1000 rounds = $102,421 of $45,944 total crypto profit (222.9%)

## Daily profit from the feed (before fees and rebates), last 15 days

- 2026-10-11: $1,774
- 2026-10-12: $-1,292
- 2026-10-13: $-3,473
- 2026-10-14: $-755
- 2026-10-15: $-1,311
- 2026-10-16: $-799
- 2026-10-17: $-476
- 2026-10-18: $-966
- 2026-10-19: $-345
- 2026-10-20: $-1,596
- 2026-10-21: $-3,228
- 2026-10-23: $95
- 2026-10-24: $41
- 2026-10-25: $106
- 2026-10-26: $191

Total from the feed: $48,399 (compare with the official trade_pnl)