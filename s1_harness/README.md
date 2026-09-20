# S1 — TWAP Lock-In Harness (DRY RUN)

Flight simulator + live calibrator for strategy **S1 (TWAP Lock-In Convergence)** from
`BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD`. It **never places orders** — it observes,
reconstructs, and scores.

## Run

```bash
python3 -u s1_harness/twap_lockin_harness.py | tee -a s1_harness/harness.log
```

Requires: `pip install websockets`. No API keys, no wallet.

## What it is, in simple language

Polymarket's BTC 5-minute and 15-minute "Up or Down" markets don't settle on the
last price tick. They settle on an **average**: the Chainlink 60-second TWAP stream,
averaged over the whole round, compared with the price at the round's start.

An average fills up like a bathtub. With one minute left in a 5-minute round,
**80% of the water is already in the tub** — the final average can barely move.
But the order book is traded by humans/bots watching the *last* price tick, who
keep paying for "comeback" chances that the math no longer supports.

The harness:
1. drinks from the same tap the settlement uses (Polymarket RTDS relays the
   Chainlink TWAP **without credentials**),
2. tracks how full each round's tub is (`locked-TWAP gap` = how far the
   already-locked average sits from the open price),
3. computes the honest probability of the outcome (`model p(Up)`),
4. compares it with what the market is charging (`book Up bid/ask`) **after the
   official taker fee** `0.07·p·(1−p)`,
5. shouts `⚡ SIGNAL … (DRY RUN)` when buying the leading side in the last
   45s (5m) / 90s (15m) would have >2¢ post-fee edge,
6. after every round, fetches the official result and writes two verdicts to
   `rounds.csv`:
   - `semantics_match` — does our tub reconstruction agree with the real
     settlement? (live-proof we understand the rules)
   - calibration — was the model's probability right vs the book's price?

## Log vocabulary

| line | meaning |
|---|---|
| `locked-TWAP gap +X bps` | the already-accumulated round average sits X basis points above (+) or below (−) the open print |
| `model p(Up)=0.97` | honest probability of "Up" given what's locked + measured stream volatility (×1.5 safety) |
| `book Up 0.85/0.86` | live best bid/ask for the Up token |
| `⚡ SIGNAL … would BUY Up @ 0.86, post-fee edge 9.3%` | a dry-run trade the rules would have taken |
| `ROUND-RESULT … SEMANTICS MATCH ✔` | our reconstruction predicted the official settlement |

## Framework discipline (MOON DEV RBI)

- This file is the **B→I bridge**: no money moves until `semantics_match` is ~100%
  over many rounds AND the model is calibrated.
- `rounds.csv` rows are written **only with live book prices and the official fee
  formula** — the Part-4 lesson (paper fills that skip live gates are fiction).
- Kill criteria from the strategy doc: ≥200 signals with post-fee edge <0.5¢, or
  win-rate missing model by >2σ → strategy dies, cheaply.

## v2 addendum — the settlement-semantics arbiter

While incubating, the harness also live-tests WHICH settlement formula Polymarket
actually uses, because the docs wording admits two readings. At every round bell it
snapshots two candidate open references (Chainlink 60s-TWAP value and Chainlink
spot value), and after the bell it compares the official result against six
reconstructions: {twap-open, spot-open} × {full-round average, trailing-60s
average, end value}. `rounds.csv` records a yes/NO per hypothesis; whichever
column stays "yes" across many full-coverage rounds is the true rule. The S1
model currently trades the **full-round-average** reading (the literal reading
of the market description); the `p(end-value)` column shows the rival reading
live so disagreement is always visible before any money is ever at risk.
