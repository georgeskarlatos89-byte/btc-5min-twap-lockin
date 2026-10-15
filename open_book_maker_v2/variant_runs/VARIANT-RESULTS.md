# VARIANT-RESULTS - OPEN-BOOK-MAKER sim sweep (2026-10-15 11:13 UTC)

Replay corpus: 19 day files (2026-09-26.jsonl .. 2026-10-14.jsonl). Fit days: 2026-09-26, 2026-09-27, 2026-09-28. Test days: 16 days 2026-09-29 .. 2026-10-14. Pilot capital for R5: $1000. Size 20 sh/side, cap 40. Runtime ? s.

Verdict = all of R1..R6 on TEST days. Every grid point is listed; `sel` marks the point chosen on FIT days.

| run | sel | fit edge/sh | fit win | TEST rounds | fills | edge/sh [CI] | win rate [CI] | be(meas) | adv+5s buy/sell | ret on cost [CI] | worst day | R1 R2 R3 R4 R5 R6 | verdict | chain audit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V0_base | * | -3.66c | +44.62% | 4580 | 12928 | -3.88c [-4.44c, -3.33c] | +44.22% [+43.59%, +44.83%] | +48.10% | -2.19c / -1.90c | -8.06% [-9.24%, -6.93%] | $-977 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V1_one_sided_theta0.03 | * | -4.48c | +28.97% | 4580 | 11357 | -2.54c [-3.39c, -1.70c] | +34.22% [+33.36%, +35.09%] | +36.76% | +4.87c / +4.99c | -6.92% [-9.19%, -4.65%] | $-1157 | Y n n Y n n | FAIL | 40/40 match, differ 0 |
| V1_one_sided_theta0.05 |  | -4.64c | +25.24% | 4580 | 8045 | -2.38c [-3.38c, -1.34c] | +30.53% [+29.47%, +31.57%] | +32.91% | +7.07c / +7.02c | -7.23% [-10.23%, -4.09%] | $-1062 | Y n n Y n n | FAIL | 40/40 match, differ 0 |
| V2_imb_q0.2_support_k2.0 |  | -5.58c | +37.32% | 4580 | 5908 | -2.85c [-3.86c, -1.86c] | +41.33% [+40.22%, +42.38%] | +44.19% | -1.85c / -1.66c | -6.46% [-8.72%, -4.22%] | $-629 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V2_imb_q0.2_support_k3.0 |  | -5.47c | +37.99% | 4580 | 5373 | -3.00c [-4.10c, -1.90c] | +42.34% [+41.16%, +43.51%] | +45.34% | -1.99c / -1.61c | -6.61% [-9.02%, -4.21%] | $-581 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V2_imb_q0.4_support_k2.0 |  | -4.68c | +41.54% | 4580 | 10191 | -3.45c [-4.17c, -2.78c] | +42.73% [+41.97%, +43.51%] | +46.19% | -1.90c / -1.83c | -7.48% [-9.03%, -6.03%] | $-699 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V2_imb_q0.4_support_k3.0 | * | -4.23c | +41.97% | 4580 | 9830 | -3.38c [-4.09c, -2.69c] | +43.29% [+42.52%, +44.08%] | +46.66% | -1.90c / -1.80c | -7.23% [-8.75%, -5.77%] | $-711 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V3_half_spread0.03 |  | -3.89c | +42.42% | 4580 | 12879 | -3.52c [-4.11c, -2.93c] | +43.42% [+42.81%, +44.04%] | +46.94% | -1.31c / -1.06c | -7.49% [-8.75%, -6.23%] | $-791 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V3_half_spread0.04 | * | -3.20c | +41.97% | 4580 | 12290 | -3.42c [-4.07c, -2.78c] | +42.60% [+41.89%, +43.29%] | +46.02% | -0.83c / -0.66c | -7.43% [-8.83%, -6.04%] | $-852 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V4_window180-240 | * | -6.86c | +40.00% | 4580 | 1694 | -5.73c [-7.56c, -3.85c] | +41.50% [+39.33%, +43.63%] | +47.23% | -3.26c / -3.15c | -12.13% [-15.93%, -8.15%] | $-327 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V5_open_fade_secs15 | * | -6.40c | +40.00% | 4580 | 1469 | -3.81c [-6.17c, -1.34c] | +44.04% [+41.58%, +46.60%] | +47.86% | -1.65c / -1.15c | -7.96% [-12.96%, -2.82%] | $-289 | Y n n n n n | FAIL | 40/40 match, differ 0 |
| V6_no_requote_after_fillTrue | * | -4.95c | +44.46% | 4580 | 6330 | -3.58c [-4.11c, -3.06c] | +45.81% [+45.25%, +46.36%] | +49.39% | -2.10c / -1.90c | -7.25% [-8.31%, -6.19%] | $-424 | Y n n n n n | FAIL | 40/40 match, differ 0 |

## Pre-registered predictions vs outcome

- **V0** (baseline reproduction (instrument check)): predicted "reproduces -6.25c/share +- CI on the Phase-A days". Got: edge -3.88c/share, win +44.22%, loss per filled round $-2.62, verdict FAIL.
- **V1** (one-sided conviction: quote only the side cheap by >= theta vs book mid): predicted "still negative (selection remains); loss per round shrinks". Got: edge -2.54c/share, win +34.22%, loss per filled round $-1.40, verdict FAIL.
- **V2** (flow-toxicity gate: top-of-book quiet (|imb| < q) OR our side >= k x opposite): predicted "FAIL; no filter has identified dumb flow here". Got: edge -3.38c/share, win +43.29%, loss per filled round $-1.90, verdict FAIL.
- **V3** (wider half-spread (both reported)): predicted "rate unchanged or worse (wider spread selects bigger moves through us)". Got: edge -3.42c/share, win +42.60%, loss per filled round $-2.16, verdict FAIL.
- **V4** (late-window maker: quote only t_el in [180, 240] s): predicted "negative (every window loses) - cleanest test of monetizing the fair-model edge passively". Got: edge -5.73c/share, win +41.50%, loss per filled round $-1.78, verdict FAIL.
- **V5** (open-auction fade: quote only the first 15 s, then cancel, no re-quote): predicted "negative". Got: edge -3.81c/share, win +44.04%, loss per filled round $-0.89, verdict FAIL.
- **V6** (no re-quote into a move: a filled side stays off for the rest of the round): predicted "loss per filled round drops materially; rate still negative (damage limiter, not revival)". Got: edge -3.58c/share, win +45.81%, loss per filled round $-1.19, verdict FAIL.

## V0 instrument check (Phase-A days only): rounds 1141 fills 3580 pnl $-2825.0 adverse edge -0.0625 vs expected {'rounds': 1141, 'fills': 3580, 'pnl_usd': -2825.0, 'return_on_notional': -0.0795, 'mean_adverse_edge': -0.0625} -> reproduced = True
