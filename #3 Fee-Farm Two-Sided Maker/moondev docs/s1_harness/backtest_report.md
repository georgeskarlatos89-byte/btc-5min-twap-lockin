# S1 BACKTEST REPORT — 2026-09-19 (the B step, honest edition)

**Window:** 12h of Kraken XBTUSD 1-minute OHLC (real prints), 190 finished rounds
(142×5m + 48×15m), every reconstruction checked against the **official Polymarket
resolution** (Gamma) before any PnL was computed. Fees: official `0.07·p·(1−p)`.
1Hz path = linear interpolation between real closes — a labelled stand-in; the
validation step exists precisely to measure how much that stand-in is worth.

## 1 — Settlement-semantics validation (the question that matters first)

Reconstructed full-round TWAP average vs official settlement, by |gap| bucket:

| reconstructed gap vs open | matches official | reading |
|---|---|---|
| < 1 bps | 20/40 = 50.0% | coin-flip zone, exactly as theory predicts — untradeable |
| 1–2 bps | 32/39 = 82.1% | proxy noise dominates |
| 2–4 bps | 35/41 = 85.4% | proxy noise dominates |
| **> 4 bps** | **70/70 = 100%** | **semantics confirmed in the tradeable zone** |

The full-round-average reading of the resolution rule is right **always** once the
gap exceeds 4 bps, and no better than a coin where the gap is under 1 bps. The
live true-feed arbiter (harness) independently agrees 2/2 on full-coverage rounds.

## 2 — Simulated S1 performance (synthetic book, official fees)

The synthetic book is calibrated to *observed live behaviour* (the book tracks the
end-value probability; 1¢ tick, ~1¢ spread). It is a stand-in — labelled as such.

| config | n | win-rate | note |
|---|---|---|---|
| 5m, 45s window, edge > 2¢ | 32 | 56.2% | headline +0.435/share is an ARTIFACT |
| 15m, 90s window, edge > 2¢ | 13 | 38.5% | same artifact |

Decomposition that kills the naive reading:

| signal class | n | win-rate | meaning |
|---|---|---|---|
| proxy-confident vs converged book (divergence) | 30 | **40%** | the book beats the proxy 60% of the time — divergence is proxy error, not market error |
| book lagging at mid prices | 15 | 73% | the only fragment with a real edge; below proof bar on proxy data |

A "40% edge" that wins 56% of the time is not an edge. **The backtest did its job:
it killed the money-printer reading of S1.** This is the Part-4 lesson again —
a model that trusts its own proxy produces a headline, not a strategy.

## 3 — What survives, and what was changed because of it

S1 survives **only** in this form:
1. Trade only the measured 100% zone: locked-TWAP gap **≥ 4 bps** (`GAP_MIN_BPS=4.0`,
   now hardcoded in the live harness signal gate).
2. Take the *lagging-book* entry (book behind our true-feed reconstruction), never
   the *divergence* entry (book converged opposite us — that is our error, not theirs).
3. The proxy backtest cannot certify win-rate; the **live true-feed harness** can.
   Promotion gate to $10 incubation: ≥50 full-coverage arbiter rounds with the
   full-average column still "yes", and ≥20 live ≥4bps lagging-book signals whose
   dry-run win-rate clears 90%.

## 4 — Standing limitations (written down, not swept under the rug)

- Single-exchange (Kraken) interpolation ≠ Chainlink multi-exchange benchmark;
  measured cost: 15–18% disagreement in the 1–4 bps zone.
- Synthetic book replaces real historical books (none available at this fidelity).
- Resolution latency (~5 min) means arbiter rows land late; partial-coverage rows
  are tagged and excluded from the semantics tally.

Files: `backtest_s1.csv` (summary), `backtest_s1_signals.csv` (per-signal),
`backtest_rows.csv` (per-round reconstruction vs official).
