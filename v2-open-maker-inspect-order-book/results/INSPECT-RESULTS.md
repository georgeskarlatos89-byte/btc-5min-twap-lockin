# v2-open-maker-inspect-order-book - RESULTS - 19 recorded days (20260926 .. 20261014), 5,432 rounds

Order latency 0.1 s (measured). Size 20 shares per order. Taker fee 0.07*p*(1-p) per share; maker rebate = 20% of that, credited per maker-filled share (0.35% of notional at 50c). Queue rules as in the paired-maker replay. Edge/share = profit at settlement per filled share.

## 2a Will a taker trade at my price? One 20-share bid, held to the end of the round

| cents below best bid | posted at (elapsed s) | orders | filled (any) | avg shares filled | median s to first fill | median queue ahead (sh) | edge/share | edge/share + rebate | profit per order |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 30 (270 s left) | 9,786 | 93.8% | 18.69 | 2.3 | 330 | -2.34c | -2.04c | $-0.438 |
| 0 | 60 (240 s left) | 9,822 | 92.4% | 18.41 | 2.2 | 339 | -2.48c | -2.20c | $-0.457 |
| 0 | 90 (210 s left) | 9,828 | 91.6% | 18.24 | 2.8 | 348 | -2.40c | -2.15c | $-0.437 |
| 0 | 120 (180 s left) | 9,837 | 89.8% | 17.88 | 2.3 | 361 | -2.43c | -2.20c | $-0.434 |
| 0 | 150 (150 s left) | 9,543 | 87.6% | 17.44 | 2.3 | 383 | -2.01c | -1.80c | $-0.350 |
| 0 | 180 (120 s left) | 9,264 | 82.2% | 16.33 | 1.6 | 383 | -2.33c | -2.15c | $-0.381 |
| 0 | 210 (90 s left) | 8,521 | 74.3% | 14.75 | 1.7 | 420 | -2.31c | -2.16c | $-0.341 |
| 0 | 240 (60 s left) | 7,489 | 57.2% | 11.28 | 0.7 | 439 | -2.41c | -2.29c | $-0.272 |
| 0 | 270 (30 s left) | 6,213 | 34.4% | 6.32 | 2.2 | 7,343 | -1.37c | -1.29c | $-0.086 |
| 0 | 293 (7 s left) | 5,746 | 10.3% | 1.50 | 2.2 | 42,698 | -1.16c | -1.13c | $-0.017 |
| 1 | 30 (270 s left) | 9,786 | 91.0% | 18.14 | 5.0 | 331 | -2.56c | -2.26c | $-0.465 |
| 1 | 60 (240 s left) | 9,813 | 89.4% | 17.83 | 4.5 | 337 | -2.69c | -2.41c | $-0.479 |
| 1 | 90 (210 s left) | 9,780 | 87.7% | 17.45 | 5.4 | 351 | -2.88c | -2.63c | $-0.503 |
| 1 | 120 (180 s left) | 9,712 | 85.3% | 16.98 | 4.4 | 362 | -2.88c | -2.65c | $-0.489 |
| 1 | 150 (150 s left) | 9,282 | 81.8% | 16.28 | 4.8 | 374 | -2.45c | -2.24c | $-0.399 |
| 1 | 180 (120 s left) | 8,915 | 75.1% | 14.93 | 3.3 | 392 | -2.87c | -2.68c | $-0.429 |
| 1 | 210 (90 s left) | 8,128 | 64.7% | 12.86 | 3.1 | 410 | -3.08c | -2.91c | $-0.396 |
| 1 | 240 (60 s left) | 7,121 | 44.4% | 8.80 | 1.2 | 436 | -3.10c | -2.94c | $-0.273 |
| 1 | 270 (30 s left) | 5,987 | 15.5% | 3.06 | 1.2 | 819 | -2.79c | -2.66c | $-0.085 |
| 1 | 293 (7 s left) | 5,638 | 1.6% | 0.32 | 0.5 | 1,108 | -5.44c | -5.29c | $-0.017 |
| 2 | 30 (270 s left) | 9,786 | 88.8% | 17.68 | 7.7 | 357 | -2.76c | -2.45c | $-0.487 |
| 2 | 60 (240 s left) | 9,798 | 86.8% | 17.29 | 7.2 | 363 | -2.87c | -2.59c | $-0.497 |
| 2 | 90 (210 s left) | 9,713 | 84.7% | 16.87 | 8.5 | 372 | -3.20c | -2.95c | $-0.540 |
| 2 | 120 (180 s left) | 9,576 | 81.8% | 16.29 | 7.0 | 386 | -3.16c | -2.93c | $-0.515 |
| 2 | 150 (150 s left) | 9,085 | 77.6% | 15.45 | 7.5 | 391 | -2.79c | -2.58c | $-0.432 |
| 2 | 180 (120 s left) | 8,661 | 70.7% | 14.05 | 5.1 | 397 | -3.10c | -2.90c | $-0.435 |
| 2 | 210 (90 s left) | 7,835 | 59.4% | 11.83 | 4.6 | 402 | -3.59c | -3.40c | $-0.425 |
| 2 | 240 (60 s left) | 6,852 | 39.7% | 7.87 | 1.9 | 385 | -3.56c | -3.39c | $-0.281 |
| 2 | 270 (30 s left) | 5,887 | 12.9% | 2.57 | 1.5 | 629 | -3.73c | -3.58c | $-0.096 |
| 2 | 293 (7 s left) | 5,609 | 1.3% | 0.26 | 0.7 | 681 | -6.66c | -6.49c | $-0.017 |

## 2b Posting both sides at a fixed time (the notebook times) - pair view

| cents below bb | posted at | rounds | both sides filled | pair completion | money in | profit | +rebate | return on cost | profit+rebate 95% CI | gate |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 30 (270 s left) | 4,893 | 87.5% | 93.0% | $89,323 | $-4,287 | $558 | -4.80% | [-4,152, -3,324] | FAIL |
| 0 | 60 (240 s left) | 4,911 | 84.9% | 91.4% | $87,113 | $-4,491 | $505 | -5.15% | [-4,425, -3,580] | FAIL |
| 0 | 90 (210 s left) | 4,900 | 83.4% | 90.4% | $85,248 | $-4,299 | $452 | -5.04% | [-4,251, -3,458] | FAIL |
| 0 | 120 (180 s left) | 4,859 | 80.2% | 88.5% | $81,481 | $-4,298 | $398 | -5.27% | [-4,318, -3,503] | FAIL |
| 0 | 150 (150 s left) | 4,587 | 77.5% | 86.8% | $73,925 | $-3,324 | $337 | -4.50% | [-3,331, -2,661] | FAIL |
| 0 | 180 (120 s left) | 4,218 | 72.6% | 83.5% | $64,126 | $-3,562 | $272 | -5.56% | [-3,649, -2,937] | FAIL |
| 0 | 210 (90 s left) | 3,433 | 69.1% | 81.1% | $49,582 | $-2,800 | $195 | -5.65% | [-2,908, -2,316] | FAIL |
| 0 | 240 (60 s left) | 2,308 | 57.1% | 73.0% | $28,093 | $-1,970 | $108 | -7.01% | [-2,102, -1,626] | FAIL |
| 0 | 270 (30 s left) | 870 | 46.7% | 73.6% | $9,017 | $-482 | $27 | -5.34% | [-588, -334] | FAIL |
| 0 | 293 (7 s left) | 344 | 9.9% | 59.6% | $1,183 | $-87 | $3 | -7.37% | [-151, -23] | FAIL |
| 1 | 30 (270 s left) | 4,893 | 82.0% | 89.8% | $84,274 | $-4,547 | $542 | -5.40% | [-4,491, -3,520] | FAIL |
| 1 | 60 (240 s left) | 4,902 | 78.9% | 87.9% | $81,543 | $-4,709 | $490 | -5.77% | [-4,699, -3,752] | FAIL |
| 1 | 90 (210 s left) | 4,852 | 76.1% | 85.8% | $77,920 | $-4,918 | $433 | -6.31% | [-4,952, -3,977] | FAIL |
| 1 | 120 (180 s left) | 4,734 | 72.9% | 83.9% | $73,218 | $-4,777 | $379 | -6.52% | [-4,870, -3,915] | FAIL |
| 1 | 150 (150 s left) | 4,326 | 70.6% | 82.2% | $64,367 | $-3,700 | $319 | -5.75% | [-3,809, -2,952] | FAIL |
| 1 | 180 (120 s left) | 3,869 | 65.5% | 78.4% | $53,962 | $-3,845 | $255 | -7.13% | [-4,017, -3,167] | FAIL |
| 1 | 210 (90 s left) | 3,040 | 61.7% | 75.7% | $40,181 | $-3,051 | $180 | -7.59% | [-3,224, -2,506] | FAIL |
| 1 | 240 (60 s left) | 1,940 | 53.0% | 69.1% | $22,111 | $-1,885 | $98 | -8.52% | [-2,053, -1,520] | FAIL |
| 1 | 270 (30 s left) | 644 | 44.3% | 71.4% | $5,994 | $-450 | $24 | -7.52% | [-569, -292] | FAIL |
| 1 | 293 (7 s left) | 236 | 11.9% | 72.2% | $630 | $-85 | $3 | -13.51% | [-152, -23] | FAIL |
| 2 | 30 (270 s left) | 4,893 | 77.6% | 86.9% | $80,022 | $-4,767 | $527 | -5.96% | [-4,798, -3,714] | FAIL |
| 2 | 60 (240 s left) | 4,887 | 73.9% | 84.6% | $76,570 | $-4,875 | $474 | -6.37% | [-4,942, -3,859] | FAIL |
| 2 | 90 (210 s left) | 4,785 | 70.6% | 82.3% | $72,317 | $-5,234 | $417 | -7.24% | [-5,341, -4,262] | FAIL |
| 2 | 120 (180 s left) | 4,598 | 67.7% | 80.2% | $66,650 | $-4,900 | $363 | -7.35% | [-5,048, -3,985] | FAIL |
| 2 | 150 (150 s left) | 4,129 | 65.2% | 78.3% | $57,428 | $-3,929 | $303 | -6.84% | [-4,109, -3,160] | FAIL |
| 2 | 180 (120 s left) | 3,615 | 60.9% | 75.1% | $47,535 | $-3,799 | $241 | -7.99% | [-3,996, -3,101] | FAIL |
| 2 | 210 (90 s left) | 2,747 | 56.7% | 71.9% | $34,125 | $-3,128 | $169 | -9.17% | [-3,356, -2,569] | FAIL |
| 2 | 240 (60 s left) | 1,671 | 50.8% | 67.3% | $18,649 | $-1,853 | $91 | -9.94% | [-2,060, -1,474] | FAIL |
| 2 | 270 (30 s left) | 544 | 41.2% | 68.8% | $4,903 | $-506 | $22 | -10.33% | [-654, -329] | FAIL |
| 2 | 293 (7 s left) | 207 | 9.7% | 61.7% | $479 | $-87 | $2 | -18.14% | [-152, -24] | FAIL |

## 2c Odd, even and round-number prices

Our bid price parity (the price we post, 0 or 1 cent below the best bid):

| cents below bb | price parity | orders | filled (any) | median queue ahead | edge/share | edge/share + rebate |
|---|---|---|---|---|---|---|
| 0 | even cent | 30,748 | 85.8% | 347 | -2.76c | -2.54c |
| 0 | odd cent | 38,815 | 64.9% | 682 | -2.11c | -1.88c |
| 0 | round (x0/x5) | 16,486 | 83.9% | 172 | -1.77c | -1.56c |
| 1 | even cent | 39,832 | 60.8% | 484 | -2.24c | -2.01c |
| 1 | odd cent | 30,614 | 74.1% | 383 | -3.43c | -3.19c |
| 1 | round (x0/x5) | 13,716 | 82.9% | 326 | -2.63c | -2.39c |

Crowding: average shares resting at the best bid and the next three cents below it, by the parity of the best-bid price:

| best bid parity | snapshots | at bb | bb-1c | bb-2c | bb-3c | at ask | ask+1c |
|---|---|---|---|---|---|---|---|
| even cent | 30,757 | 580 | 723 | 554 | 750 | 1,001 | 524 |
| odd cent | 54,178 | 6,175 | 496 | 604 | 443 | 7,417 | 641 |
| round (x0/x5) | 16,513 | 3,764 | 5,445 | 608 | 660 | 594 | 414 |

## 2d Read the flow, then place: continuous join-the-best-bid tactic with filters

base = quote both sides at the best bid all window (60-240 s). sweep = pull the quote for 5 s when >= 30 shares hit our side's bids within 1 s or the best bid drops 2 cents within 1 s. imb = quote only while the top-3 bid size >= top-3 ask size on that side. both = sweep + imb.

| variant | rounds | fills | money in | profit | +rebate | return on cost | profit+rebate 95% CI | pair completion | days +/- | worst day | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 5,430 | 113,206 | $534,458 | $-21,558 | $3,264 | -4.03% | [-20,437, -16,041] | 83.7% | 0/19 | $-1,676 | FAIL |
| sweep | 5,188 | 20,384 | $113,499 | $-2,861 | $561 | -2.52% | [-4,234, -341] | 30.4% | 6/19 | $-500 | FAIL |
| imb | 5,401 | 37,670 | $171,088 | $-2,092 | $1,015 | -1.22% | [-3,283, 1,164] | 56.4% | 8/19 | $-577 | FAIL |
| both | 1,942 | 3,122 | $17,328 | $-79 | $80 | -0.46% | [-779, 711] | 9.7% | 8/19 | $-122 | FAIL |

## 2e After a fill: sell at once, pair at once, or hold?

For every fill of the base tactic (above): A = sell the shares at once at the best bid as a taker (fee paid, assumes the best-bid size takes all of it), B = buy the other side at its ask as a taker if the pair then costs <= 0.97 (else not available), C = hold to settlement.

| exit | fills covered | shares | total profit | per share |
|---|---|---|---|---|
| A sell at once (taker) | 113,206 | 1,191,383 | $-36,505 | -3.06c |
| C hold, same fills as A | 113,206 | 1,191,383 | $-21,558 | -1.81c |
| B pair at once (taker), when available | 747 | 8,393 | $370 | 4.41c |
| C hold, same fills as B | 747 | 8,393 | $866 | 10.32c |
| C hold, all fills | 113,206 | 1,191,383 | $-21,558 | -1.81c |

## Verdict

Rules tested with a gate: 34; PASS: 0; FAIL: 34.
Gate = profit 95% CI low > 0 after rebate AND pair completion >= 95%, at the measured latency.
