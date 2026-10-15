# AUTOPSY (V7) - V0_base: 12928 counted fills over 4580 rounds

Edge is measured FOR US (positive = the price moved our way after the fill). sweep = how far the touch had
moved through our price in the previous 1-2 s, in ticks (1 tick = 1 cent).

| group | n | sweep 1s | sweep 2s | edge mid +5s | edge fair +5s | edge mid +30s | edge fair +30s | edge mid end | edge settle |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 12928 | +5.51 | +6.07 | -3.18c | -2.04c | -3.20c | -3.24c | +0.90c | -3.88c |
| BUY | 6493 | +5.64 | +6.20 | -3.31c | -2.19c | -3.24c | -3.51c | +9.29c | -4.02c |
| SELL | 6435 | +5.38 | +5.93 | -3.06c | -1.90c | -3.16c | -2.98c | -6.88c | -3.73c |
| 000-060s | 4861 | +4.40 | +4.88 | -2.82c | -1.65c | -2.81c | -2.63c | +3.33c | -3.76c |
| 060-120s | 3663 | +4.99 | +5.54 | -2.69c | -1.73c | -3.04c | -3.02c | +10.36c | -3.06c |
| 120-180s | 2716 | +5.93 | +6.50 | -3.48c | -2.16c | -3.33c | -3.28c | +0.48c | -4.05c |
| 180-240s | 1490 | +8.07 | +8.80 | -4.47c | -3.31c | -4.01c | -4.45c | -14.46c | -4.83c |
| 240-300s | 198 | +17.26 | +18.37 | -7.52c | -6.44c | -16.26c | -13.53c | +6.43c | -12.08c |
| BUY@000-060s | 2384 | +4.51 | +5.00 | -2.98c | -1.87c | -2.82c | -2.89c | -0.54c | -4.49c |
| BUY@060-120s | 1863 | +5.11 | +5.65 | -2.80c | -1.74c | -2.77c | -2.88c | +14.56c | -2.34c |
| BUY@120-180s | 1374 | +6.09 | +6.71 | -3.49c | -2.29c | -4.12c | -4.06c | +23.81c | -5.52c |
| BUY@180-240s | 766 | +8.00 | +8.71 | -4.69c | -3.48c | -3.52c | -4.25c | +6.08c | -2.71c |
| BUY@240-300s | 106 | +17.41 | +18.31 | -7.58c | -6.52c | -16.74c | -17.19c | +5.50c | -12.84c |
| SELL@000-060s | 2477 | +4.29 | +4.77 | -2.67c | -1.44c | -2.81c | -2.38c | +6.93c | -3.07c |
| SELL@060-120s | 1800 | +4.87 | +5.42 | -2.57c | -1.72c | -3.32c | -3.16c | -0.83c | -3.81c |
| SELL@120-180s | 1342 | +5.77 | +6.28 | -3.47c | -2.03c | -2.53c | -2.49c | -13.88c | -2.55c |
| SELL@180-240s | 724 | +8.16 | +8.90 | -4.23c | -3.13c | -4.52c | -4.67c | -32.07c | -7.06c |
| SELL@240-300s | 92 | +17.10 | +18.44 | -7.44c | -6.34c | -15.83c | -9.55c | +7.12c | -11.20c |

## Exposure the snapshot fill model cannot see

- rounds: 4580
- counted_fills: 12928
- counted_fills_per_round: 2.823
- pnl_usd: -10021.2
- loss_per_round_usd: -2.188
- touch_ticks_per_round: 13.6
- improve_ticks_per_round: 12.8
- benign_touch_fills_per_round_needed_to_break_even: 7.29
- ratio_benign_needed_to_counted: 2.58
- note: a benign touch fill earns at most the half-spread (HALF_SPREAD x QUOTE_SIZE); the ratio says how many invisible good fills per counted bad fill the strategy would need
