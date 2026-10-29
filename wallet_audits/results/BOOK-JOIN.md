# Wallet fills vs our recorded book - 65,444 BTC 5m fills on 867 rounds (65,437 buys with a book state)

- price at or above the best ask (taker lift): 43.1%; at or below the best bid (resting maker fill): 51.4%; inside the spread: 5.5%
- sum of the two best asks at the moment he buys: 10th 1.010, median 1.010, 90th 1.010; below 1.00 in 0.0%, below 0.97 in 0.0%, below 0.965 in 0.0% of his buys
- sum of the two best bids at his buys: median 0.990 (the book's normal state: asks sum just above 1, bids just below)
- Up buys while bitcoin is BELOW the open (Up is the cheap side): 50.8% of 28,221; Down buys while bitcoin is ABOVE the open: 60.6% of 30,202
- his buy price vs the side's own best ask: median gap 1.0c (0 = he lifts the ask)
- taker lifts (25,123): bitcoin moved TOWARD the side bought in the previous 2 s in 43.3%, away in 50.1%; median |move| 0.1 bps; model fair minus his price: median +2.5c; value at settlement per share -3.41c
- maker fills at the bid (29,489): bitcoin moved TOWARD the side bought in the previous 2 s in 44.9%, away in 48.6%; median |move| 0.1 bps; model fair minus his price: median +0.5c; value at settlement per share +3.47c

| taker lifts: model fair minus ask | fills | shares | value at settlement per share | taker fee per share |
|---|---|---|---|---|
| below -5c (he overpays) | 6,620 | 165,810 | -4.09c | 1.19c |
| -5c..0 | 3,847 | 86,228 | -4.44c | 1.29c |
| 0..2c | 1,685 | 36,010 | -3.90c | 1.33c |
| 2..5c | 2,294 | 50,505 | +0.20c | 1.28c |
| above 5c (ask is stale/cheap) | 10,677 | 212,996 | -3.24c | 1.11c |

| price he paid | buys | share at ask | median sum of asks then | median seconds into round |
|---|---|---|---|---|
| 0.00-0.20 | 15,030 | 49% | 1.010 | 174 |
| 0.20-0.40 | 15,147 | 46% | 1.010 | 99 |
| 0.40-0.50 | 6,843 | 46% | 1.010 | 76 |
| 0.50-0.60 | 6,144 | 43% | 1.010 | 89 |
| 0.60-0.80 | 11,167 | 40% | 1.010 | 116 |
| 0.80-1.01 | 11,106 | 32% | 1.010 | 174 |

## The pair-arbitrage opportunity in the book itself (every second of every round, his trading or not)

- seconds with Up ask + Down ask below 1.00: 110 of 240,756 (0.05%), in 102 of 867 rounds
- seconds with Up ask + Down ask below 0.97: 91 of 240,756 (0.04%), in 83 of 867 rounds
- seconds with Up ask + Down ask below 0.965 (covers both taker fees at 50c): 91 of 240,756 (0.04%), in 83 of 867 rounds
- lowest sum of asks seen per round: median 1.010, 10th pct 0.970; depth at that moment (smaller of the two ask sizes): median 0 shares

## His round results vs the arbitrage seconds in that round

| seconds with ask sum < 1.00 | rounds | his money in | his profit (after fees) | return |
|---|---|---|---|---|
| 0 | 687 | $626,523 | $-835 | -0.13% |
| 1-9 | 93 | $96,518 | $127 | +0.13% |

All 780 rounds he traded on these days: money in $723,041, profit after fees $-708 (-0.10%), rebates not included.