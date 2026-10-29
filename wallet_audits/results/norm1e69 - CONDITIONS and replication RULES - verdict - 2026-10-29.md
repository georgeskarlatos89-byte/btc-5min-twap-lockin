# norm1e69: his conditions, the replication rules, and the verdict (2026-10-29)

I'm explaining this in simple language a 15 year old can understand.

Companion to `norm1e69 wallet audit - the real numbers behind the X post - 2026-10-27.md`. Data: his 1,335,054
activity rows and 808,180 taker-only fills (public feeds), the official profit API, and our own recorded order
book + chainlink feed for Sep 26 - Oct 14 (19 days, 5,358 of his rounds overlap). Nothing live was touched.

## 1. Does he wait for a time or a condition before entering? No.

- He trades 91.6% of all BTC 5-minute rounds, 84-94% in every hour of the day, 89-97% on every weekday (Fri 78%).
  Median gap between traded rounds: one round. One 26-hour pause; stopped for good on Oct 23.
- First fill a median 21 s after the open (90% within 75 s), at a median 46c. The side he buys first wins 47.4%
  of the time. Buys continue evenly through all five minutes. So there is no entry timing and no direction call.

## 2. What his fills actually are (exact maker/taker split from the taker-only feed, 19 recorded days)

| class | fills | shares | money in | worth at settlement per share | taker fee per share | net per share |
|---|---|---|---|---|---|---|
| maker (resting bid got hit) | 313,850 | 4.31 M | $2.09 M | +0.14c | 0 | +0.14c |
| taker (he lifted the ask) | 94,167 | 4.83 M | $2.45 M | +1.28c | -1.19c | +0.09c |

Net on those 19 days: about $10,500 on $4.55 million = 0.23 cents per dollar. The taker fills have a real gross
edge of 1.28c a share, and the fee takes 1.19c of it. The maker fills are break-even. What is left, 0.1-0.2c a
share, is noise-sized; his profit is the taker-rebate programme paying back 39% of his fees (0.46c a share on the
taker fills), which is more than his whole trading edge. (A first 3-day pass that classified maker/taker from the
book state showed +3.5c maker / -3.4c taker; that was an artefact of second-resolution book snapshots and is
superseded by the exact split above.)

## 3. Is his taker buying timed to bitcoin? No.

At his 25,123 ask-lifts on the three matched days, bitcoin had moved TOWARD the side he bought in the previous
2 seconds in 43% of cases and away in 50%, median move 0.1 bps. Our model fair value was above his price by
2.5c in the median, but the fills in the "ask looks 5c too cheap" bucket still settled 3.2c below the price
paid. The sum of the two best asks at his buys was 1.010 in the median and never below 1.00; the book offered a
pair below $1 in 0.05% of seconds with zero depth. He is not arbitraging and not front-running the chainlink feed.

## 4. The replication rule we tested, and the result

Rule (from the only mechanism left that could explain a taker edge): lift a side's ask whenever the chainlink-based
fair value exceeds it by theta (2, 3, 5, 8 cents), 20 shares, 2 s cooldown, 200-share cap per side, order lands
0.1 s later and fills only if the ask is still there, hold to settlement, real fees. Two feed speeds: zero lag
(as fast as the exchange's own takers) and our measured 1.44 s.

| variant | rounds | fills | money in | gross before fees | net after fees | net 95% CI | net + 39% rebate | days positive |
|---|---|---|---|---|---|---|---|---|
| theta 2c, lag 0 | 4,900 | 87,108 | $774,363 | -2.97% | -6.17% | [-53,901, -41,801] | -4.92% | 0/19 |
| theta 5c, lag 0 | 4,892 | 78,956 | $662,699 | -3.52% | -6.82% | [-52,400, -38,116] | -5.53% | 0/19 |
| theta 8c, lag 0 | 4,864 | 69,050 | $534,162 | -4.75% | -8.18% | [-51,381, -35,758] | -6.84% | 0/19 |
| theta 2c, lag 1.44 s | 4,900 | 87,191 | $775,408 | -2.96% | -6.16% | [-53,792, -41,810] | -4.91% | 0/19 |

All 8 variants lose before fees, on all 19 days, and the feed speed makes no difference: the model is not a
signal on this book. The market's ask already knows more than our fair-value formula.

## 5. The rules, written out, and why they do not make money for us

1. Every round, no time filter. 2. Start within the first 20 s. 3. Buy both sides all round, mostly small fills
($5 median, $95 max), never sell. 4. About half the fills rest at the bid, half lift the ask. 5. Hold to settlement,
redeem. 6. Scale with volume (he went from $29 to $1,000 a round).

Copying rules 1-5 gives, by his own fills, +0.14c a share on the maker half and +0.09c a share on the taker half
after fees. That is 0.2% of money in, before any slippage or mistake, on a tactic that needs $200-370k of volume
a day to matter. What made his line green was the rebate: 39% of taker fees returned, which only exists at his
volume tier (the public fee page documents only the 20% maker rebate; his taker rebate rows are a separate
programme we cannot assume). Without it, October after fees was a loss every day from the 12th to the 21st.

## 5b. The rebate programme he lives on (docs.polymarket.com/trading/taker-rebates, launched 2026-05-28)

Tier by 30-day weighted volume wV = trade size x (1 - entry price) x category weight (crypto 2.3): Bronze $2k 3%,
Silver $20k 8%, Gold $200k 18%, Platinum $1M 32%, Diamond $4M 44%, Obsidian $10M 50% of taker fees back, paid
daily; only fee-paying taker trades count, maker fills do not. His 39% blended rate matches Platinum-to-Diamond
on roughly $5 million of crypto taker volume a month. A small account at Bronze gets 3%: on his taker fills that
is 0.04c a share on top of 0.09c net, so copying him at our size earns nothing, and reaching Diamond needs about
$1.2 million of fee-paying taker volume a month, which at his 0.09c net edge is not a business either.

## 6. Verdict

- The post is marketing: gross profit shown, fees hidden, rebate hidden, screenshot rows are winning legs only.
- His entry has no conditions. His edge after fees is about zero. His profit is a volume rebate.
- The replication rule tested on 19 recorded days: FAIL, 0 of 8 variants, 0 of 19 days.
- Together with the paired maker (19 days, FAIL at every latency) and the 34 order-book rules (FAIL): on the
  BTC 5-minute book, resting bids lose to informed takers, and naive taking loses to fees. The only wallets that
  look profitable at the top line are either pure speed makers at scale (gabigol) or rebate farms (this one).
- Nothing to build. Account 2 untouched.

## Files
VM `~/wallets/norm1e69/` (activity.csv, trades_taker.csv, pnl_all_1d.json, AUDIT.md, book/BOOK-JOIN.md),
`~/wallets/st_out/SIGNAL-TAKER-RESULTS.md`; repo `wallet_audits/` (code) and `wallet_audits/results/`.
