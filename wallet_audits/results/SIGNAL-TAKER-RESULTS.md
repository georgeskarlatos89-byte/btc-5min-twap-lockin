# Stale-ask taker replay - 19 days (20260926 .. 20261014)

Rule: lift a side's best ask whenever the model fair value (chainlink spot vs round open) exceeds it by theta; 20 shares, 2 s cooldown, 200 shares cap per side, order lands 0.1 s later and fills only if the ask is still there. Hold to settlement. Fees 0.07*p*(1-p). `lag` = seconds our chainlink feed trails the exchange (0 = as fast as the exchange's takers, 1.44 = our measured feed). Rebate column = 39% of fees (what norm1e69 got back).

| variant | rounds | fills | money in | gross (before fees) | fees | net after fees | net 95% CI | net + rebate | days positive | worst day |
|---|---|---|---|---|---|---|---|---|---|---|
| theta0.02 lag0 | 4,900 | 87,108 | $774,363 | $-22,963 (-2.97%) | $24,794 | $-47,756 (-6.17%) | [-53,901, -41,801] | $-38,087 (-4.92%) | 0/19 | $-4,978 |
| theta0.03 lag0 | 4,899 | 84,730 | $740,471 | $-23,015 (-3.11%) | $23,899 | $-46,915 (-6.34%) | [-53,512, -40,155] | $-37,594 (-5.08%) | 0/19 | $-5,161 |
| theta0.05 lag0 | 4,892 | 78,956 | $662,699 | $-23,351 (-3.52%) | $21,847 | $-45,198 (-6.82%) | [-52,400, -38,116] | $-36,677 (-5.53%) | 0/19 | $-5,082 |
| theta0.08 lag0 | 4,864 | 69,050 | $534,162 | $-25,397 (-4.75%) | $18,278 | $-43,675 (-8.18%) | [-51,381, -35,758] | $-36,547 (-6.84%) | 0/19 | $-5,039 |
| theta0.02 lag1.44 | 4,900 | 87,191 | $775,408 | $-22,934 (-2.96%) | $24,821 | $-47,755 (-6.16%) | [-53,792, -41,810] | $-38,074 (-4.91%) | 0/19 | $-4,974 |
| theta0.03 lag1.44 | 4,899 | 84,822 | $741,831 | $-22,920 (-3.09%) | $23,936 | $-46,856 (-6.32%) | [-53,371, -40,077] | $-37,521 (-5.06%) | 0/19 | $-5,140 |
| theta0.05 lag1.44 | 4,894 | 79,123 | $664,365 | $-23,061 (-3.47%) | $21,906 | $-44,968 (-6.77%) | [-51,925, -37,792] | $-36,424 (-5.48%) | 0/19 | $-5,064 |
| theta0.08 lag1.44 | 4,866 | 69,355 | $537,217 | $-25,440 (-4.74%) | $18,379 | $-43,819 (-8.16%) | [-51,257, -36,464] | $-36,651 (-6.82%) | 0/19 | $-5,025 |

Gate: net after fees 95% CI low > 0. No variant passes.