# S6 — Coin-Flip Harvester at Round Open

This is the **DRY-by-default incubation package** for S6 from the strategy memo. It is
paired operationally with `s3_feefarm/`: both are maker strategies, but S3 waits for a
quiet mid-round pair while S6 tests the round-open flow.

## Research edge

Entertainment takers repeatedly cross the 1¢ spread and pay the maximum crypto taker fee
near 50¢ for a fair coin. S6 quotes both outcomes at 0.49 from the bell through the first
45 seconds. If both legs fill, cost is 0.98 and the pair pays 1.00; if only one fills, it
is deliberately held to resolution so we can measure whether the fill is informed rather
than hiding the result with an untested exit rule. Ties count as Up.

## RBI status

- **R:** causal story documented above and in the attached strategy memo.
- **B:** not promoted by this setup. The critical B question is conditional fill asymmetry:
  does an Up fill resolve Up materially more often than 50%, and vice versa?
- **I:** dry/read-only first. Real orders require an explicit `.env` with keys,
  `LIVE_TRADING=1`, and a human review. This package never creates credentials.

## Safety gates encoded

- Quotes only during the first 45 seconds of each 5m/15m round.
- Live-band gate runs before a fill is recorded; rejected fills are loud.
- Moon Dev `all_liquidations` is the only intra-round liquidation feed; stale/empty/401
  switches flat. Binance liquidation data is not used.
- Quiet-session, volatility, KILL-file, daily-stop, and shared $30/5m cap are retained.
- DRY is the default and does not place orders.

## Run

```bash
python3 s6_coinflip/s6_harvester.py --preflight
python3 s6_coinflip/s6_harvester.py --minutes 60
```

Outputs are local and gitignored: `s6_harvester.log`, `s6_fills.csv`, `s6_pulls.csv`,
and `s6_stats.json`. Review `fill_asymmetry.py` after enough fills before any live step.
