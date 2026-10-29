# norm1e69 wallet audit: the real numbers behind the X post (2026-10-27)

I'm explaining this in simple language a 15 year old can understand.

Wallet: polymarket.com/@norm1e69 = proxy 0x41e2e1ccf1e4940029af02259a31c6b89b9fa354 (public). Sources: Polymarket's
own profit API (data-api v2/user-pnl, 81 daily points), the public activity feed (1,335,054 rows, every fill and
redeem since 2026-08-07, pulled in 1-hour windows because the feed caps paging at 5,000), the taker-only trade feed
(808,180 rows), and our own recorded order book for 3 October days. Nothing here needed a login or a key.

## 1. The post's claims against the official profit series

| claim in the post | official API (2026-10-27) | verdict |
|---|---|---|
| total earnings +$315,510 | gross trade profit $319,750 | true, but GROSS |
| | fees paid -$271,189 | never mentioned |
| | after fees $48,564 | |
| | rebates received: taker $106,217 + maker $13,541 | |
| | **net $168,322** | about half the headline |
| 73 of 74 days green | 79 of 81 days gross positive, 77 net, 59 after fees before rebates | true on gross |
| only loss day -$55 | worst gross day -$55 (Aug 14); net -$329 | true on gross |
| +$30,833 last week | Oct 16-22 gross $31,138, net $11,776 | true on gross |
| 56,694 markets, 27 orders each | 58,799 markets, 1,223,839 fills = 20.8 per market | roughly true |
| "limit orders at 48c both sides" | 80% of its dollars are TAKER fills (the taker fee is inside the amount paid); only 5% of buys are at 46-50c | false |
| "robot playing Bitcoin" | by dollars: BTC 5m 77%, ETH 5m 12%, SOL 5m 7%, BTC 15m 4%, esports <1% | mostly |

Volume $13.77 million. Net $168k is 1.2 cents per dollar traded: gross edge 2.3c, fees -2.0c, rebates +0.9c.
The wallet stopped on 2026-10-23; since then only small hand trades on League of Legends markets (your second
screenshot).

## 2. What it actually does (all 58,218 crypto 5-minute rounds)

- Trades 91.6% of all BTC 5m rounds, every hour of the day (84-94% per hour), every weekday (Fri 78%, others 89-97%).
  No session filter, no "wait for a setup": the median untraded gap is one round.
- Enters a median 21 s after the round opens (90% within 75 s), first buy at a median 46c, and the side it buys
  first wins 47.4% of the time. It has no view on direction.
- Buys BOTH sides in 90.8% of rounds, median 12 fills and $88 per round (90th pct $714, max $4,179), largest single
  fill $95.47. Buys continue all round (10% by 35 s, median 147 s, 90% by 246 s). It almost never sells (0.2% of fills).
- Buy prices are spread over the whole range: 10th pct 11c, median 47c, 90th pct 87c. The most common prices are
  4-11c (the dying side, bought cheap) and 90c (the winning side, bought dear).
- Pair cost (average Up price + average Down price in a round): median 0.991; below 1.00 in 54% of rounds. Rounds
  with pair cost under 0.96 earned +8.5%; rounds with pair cost over 1.02 lost -4.3%. That is the whole game: it
  wins when the round swings so both sides were bought cheap at some point, it loses when the round trends.
- Fills: 66% taker by count, 80% by dollars (BTC 51% / 75%; ETH and SOL 100% taker). Pure-taker rounds returned
  +0.55% after fees; mixed rounds +0.03%.
- Round outcomes: 47% of rounds profitable, average win $16.67, average loss -$13.37, mean +$0.79 per round. The
  top 10 rounds are only 6% of the crypto profit: the money is a thin grind over tens of thousands of rounds.

## 3. The screenshot rows are winning legs, not round profits

September 28, 8:00-8:05 PM ET (the top row, "4,044.1 Up at 39.3c, +$2,453"): it bought 4,044 Up at 0.393 for
$1,591 AND 4,114 Down at 0.609 for $2,505, 444 fills, zero sells. Up won, it redeemed $4,044. Cash out $4,096,
cash in $4,044, net -$52. The page shows the winning leg's gain and hides the losing leg. Every row on that
"profitable" list is the same kind of number. The best true rounds, from the full feed, are +$570 (Oct 17 6:25PM),
+$325 (Oct 10 5:05PM), +$265 (Oct 3 6:00AM); the worst -$328 (Oct 17 11:05AM).

## 4. October after fees: losing, saved by rebates

Daily cash profit after fees (feed amounts include the taker fee): Oct 12 -$1,292, Oct 13 -$3,473, Oct 14 -$755,
Oct 15 -$1,311, Oct 16 -$799, Oct 17 -$476, Oct 18 -$966, Oct 19 -$345, Oct 20 -$1,596, Oct 21 -$3,228. The
official series still shows those days net positive because the taker rebate (about $2,000-3,000 a day on
$200-370k of daily volume) exceeded the loss. In its last two weeks the wallet was a rebate farm, not an edge.

## 5. Reconciliation (so the feed numbers can be trusted)

- Feed cash profit for markets closed before Sep 30: $49,108; official after-fee figure at Sep 29: $47,671.
- Feed rebate rows to Sep 30: $40,310; official: $39,174.
- Whole life: feed $48,399 vs official after-fee $48,564.
- 80.1% of its BTC 5m buys carry exactly the taker fee 0.07*p*(1-p) inside the amount paid; 19.9% carry none
  (maker fills).

## 6. The other AI's MAKER-INTEL file, corrected

Right: the address, the $95 cap, the market mix, "holds winners and redeems pairs", the netted-screenshot point.
Wrong: "27 fills is too low" (the mean is 21, its 492 was 444 and was a top-size round); "no daily PnL endpoint"
(there is one, see section 1); "re-quotes and sells mid-round" (0.2% sells); "ladders early" (buys all round);
"loss mode = one-sided rounds" (one-sided rounds lost $5k in total; both-sided rounds bought above $1 lost $124k);
and above all "it is a maker": it pays $271k in fees. Its M1 (a 46/48/50c resting ladder) is the family we
closed on 19 recorded days.

## 7. Where the files are

VM twapvm `~/wallets/norm1e69/` (activity.csv, trades_taker.csv, pnl_all_1d.json, AUDIT.md, book/BOOK-JOIN.md);
repo `wallet_audits/` (wallet_pull.py, wallet_analyze.py, fills_vs_book.py, book_join_report.py, results/).
Companion file: `norm1e69 - CONDITIONS and replication RULES - 2026-10-27.md` (same folder).
