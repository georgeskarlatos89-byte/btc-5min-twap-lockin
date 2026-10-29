# Paired-maker replay with order latency - 3 days (20260928 .. 20261009)

Tactic: both sides, quote window 60-240 s, pair cap 0.97, inventory 40, size 20 per order. `model` = fair value minus 5c; `join` = join the best bid. `lat` = seconds for our order/cancel to land at the exchange (0 = instant ceiling).

| variant | rounds | fills | money in | profit | return on cost | profit 95% CI | pairs locked $ | one-sided $ | pair completion | rounds won/lost | days +/- | worst day | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| join lat0 | 842 | 22927 | $92,119 | $-185.78 | -0.20% | [-1,037, 702] | 6,907.31 | -7,093.09 | 87.6% | 428/414 | 1/2 | $-339 | FAIL |
| join lat0.02 | 842 | 16997 | $73,688 | $-1,218.51 | -1.65% | [-2,163, -244] | 6,491.85 | -7,710.35 | 81.2% | 327/515 | 0/3 | $-699 | FAIL |
| join lat0.05 | 842 | 16304 | $78,005 | $-3,184.84 | -4.08% | [-4,059, -2,311] | 5,444.27 | -8,629.11 | 82.4% | 300/542 | 0/3 | $-1,350 | FAIL |
| join lat0.1 | 842 | 15861 | $77,019 | $-3,790.26 | -4.92% | [-4,646, -2,931] | 4,945.98 | -8,736.24 | 81.9% | 278/564 | 0/3 | $-1,464 | FAIL |
| model lat0 | 756 | 10865 | $40,739 | $-1,618.41 | -3.97% | [-2,543, -706] | 5,463.70 | -7,082.11 | 69.3% | 290/466 | 1/2 | $-889 | FAIL |
| model lat0.02 | 749 | 9035 | $37,167 | $-1,748.91 | -4.71% | [-2,628, -823] | 4,509.77 | -6,258.67 | 65.1% | 287/462 | 1/2 | $-1,026 | FAIL |
| model lat0.05 | 749 | 8841 | $38,076 | $-2,340.08 | -6.15% | [-3,254, -1,425] | 4,399.04 | -6,739.12 | 65.3% | 273/476 | 0/3 | $-1,128 | FAIL |
| model lat0.1 | 749 | 8266 | $36,409 | $-2,308.42 | -6.34% | [-3,192, -1,392] | 4,252.97 | -6,561.39 | 64.0% | 257/492 | 0/3 | $-1,085 | FAIL |

Gate per variant = profit CI low > 0 AND pair completion >= 95% (gabigol: 97%).

## Step-3 verdict at the measured latency 0.1 s

- join lat0.1: profit $-3,790.26 (-4.92%), CI [-4,646, -2,931], pair completion 81.9% -> FAIL
- model lat0.1: profit $-2,308.42 (-6.34%), CI [-3,192, -1,392], pair completion 64.0% -> FAIL

## Per-day profit

| day | join lat0 | join lat0.02 | join lat0.05 | join lat0.1 | model lat0 | model lat0.02 | model lat0.05 | model lat0.1 |
|---|---|---|---|---|---|---|---|---|
| 20260928 | -292 | -42 | -629 | -951 | +47 | +214 | -201 | -222 |
| 20261003 | -339 | -477 | -1206 | -1375 | -889 | -937 | -1012 | -1085 |
| 20261009 | +445 | -699 | -1350 | -1464 | -777 | -1026 | -1128 | -1001 |
