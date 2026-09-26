# S2 — Open-print displacement

**Status: research scaffold and read-only forward recorder; NOT a live-ready bot.**
No wallets, keys, signatures or order-submission code. No real money spent.

## MoonDev RBI implementation

**R — Research.** Hypothesis: stale resting orders near 50¢ fail to reflect an early
BTC displacement from the correct opening reference. Test who pays and whether the
quotes are actually still executable. The user's 0.50/0.51 observation is a hypothesis
for this capture, not a universal fact.

**B — Backtest.** Record actual timestamped ticks and books, apply every gate before
counting a candidate, join official resolved outcomes, and replay. Do not interpolate
a completed Kraken one-minute candle to estimate the ten-second price: that uses future
information and cannot establish an executable latency edge. Gamma outcomes alone do
not supply historical early-round books. No historical profitability result is claimed.

**I — Incubate.** Only after validation, implement and separately review live execution,
then use ~$10 total cost per qualifying trade for 2–4 weeks. This follows the notes'
real-money incubation principle: research recording is NOT called incubation.

## Important blockers discovered

1. The repo's S1 harness explicitly compares alternative opening/settlement definitions.
   Its TWAP documentation does not establish that a spot snapshot is the official open.
   A public Gamma response fetched in the smoke test for
   `btc-updown-5m-1790407500` says settlement is based on Chainlink TWAP and explicitly
   warns against other spot sources. Its exact description is retained in
   `data/markets.jsonl`. This does not validate the proposed spot-open model.
2. The collector preserves Chainlink spot and TWAP-60 observations. The normalized rows
   use **spot-at/before-bell as a candidate reference**, never a verified official open.
   Exchange-composite fallback is deliberately excluded from executable candidates:
   basis error must first be measured, and fallback cohorts must be separate.
3. 7.5 bps is an uncalibrated model assumption, not today's measured volatility. The
   illustrative terminal-diffusion fair probability is not a verified TWAP model.
4. Default fee assumption follows the workspace Fees.md:
   `fee/share = 0.07 * price * (1-price)`.
   At 60¢ this is 1.68¢/share, 2.8% of stake, giving 61.68% break-even before slippage.
   Production must fetch/validate per-market fee parameters and actual fill accounting.
5. Maker intents are not fills, do not earn assumed rebates, and are excluded from P&L.
   A maker simulator needs queue position, cancel latency and adverse-selection evidence.

## Files

- `engine.py`: shared pure gates; no dry-run bypass.
- `capture.py`: bounded public RTDS/Gamma/CLOB recorder, no credentials.
- `resolve.py`: conservative official-outcome join; disputed/pending outcomes stay unknown.
- `replay.py`: one hypothetical candidate per round, cost-inclusive P&L and descriptive CI.
- `test_s2.py`: regression suite.
- `requirements.txt`: collector dependency.

## Run from repository root

```bash
python3 -m pip install -r s2_openprint/requirements.txt
python3 -m unittest discover -s s2_openprint -v
python3 s2_openprint/capture.py --minutes 60
python3 s2_openprint/resolve.py s2_openprint/data/samples.jsonl s2_openprint/data/resolved.jsonl
python3 s2_openprint/replay.py s2_openprint/data/resolved.jsonl --output s2_openprint/data/report.json
```

Run the last two commands only once a samples file exists; repeat the resolution join
later for still-pending outcomes. A lack of samples is not evidence against the edge:
inspect `health.jsonl` and feed availability first. To stop capture early:
`touch s2_openprint/KILL`; remove that file to resume. For sustained VPS recording use a
process supervisor, disk rotation/retention and clock synchronization; none is deployed here.

## Encoded research rules

- 5m: 5 bps; 15m: 8 bps; configuration rejects thresholds above 10 bps.
- Engine accepts t=5–15s; collector samples once near t=10s (HTTP latency may cause a skip).
- Symmetric Up/Down selection, explicit outcome-name token mapping.
- Ask ceiling 60¢, plus conservative 0.5¢ slippage allowance **within** the ceiling.
- At least 2¢ illustrative model edge after assumed fee/slippage.
- Hypothetical $10 total cost, minimum order-size check, sufficient top-level ask size.
- Opening observation and receipt must be at/before the bell and at most 2s old.
- Spot and book timestamps at most 2s old, no future timestamps/receipts.
- Skip late joins, stale feeds, missing depth/metadata, malformed rows and noise-zone opens.
- Quote intent at min(model fair−1¢, ask−tick, 60¢), rounded down to tick.
- No automatic promotion; all decisions explicitly `live_eligible=false`.

Metadata is prefetched in the last minute before the next bell. Start at least 60s
before a round you want to observe. REST book snapshots are a research starting point,
not a low-latency execution adapter. The legacy RTDS topics are inherited from this
repo and need end-to-end verification; no incoming data means no candidate.

## Research review / promotion checklist

- Verify actual opening benchmark and TWAP settlement semantics for both durations.
- Verify feed schemas, full-round reception, timestamp monotonicity and source/receipt lag.
- Measure the displacement–outcome relationship with the correct reference.
- Freeze thresholds and model before chronological out-of-sample testing.
- Use thousands of available rounds where possible; report qualifying sample size,
  missing-data rate, fee/slippage sensitivity, hour/day/regime and separate 5m/15m results.
- Cluster uncertainty by time/day: overlapping 5m and 15m rounds are not independent.
  The replay Wilson interval is descriptive only and is not a promotion test.
- Require positive cost-adjusted holdout evidence (target ≥2¢/share), not in-sample tuning.
  If profitability requires >10 bps, kill S2 rather than quietly relaxing the cap.
- Before real orders: implement verified-reference adapter, current fees, wallet allowance
  checks, regional eligibility checks, idempotent order/restart reconciliation, post-only
  maker expiry/cancel handling, partial-fill accounting and shared S1/S3/S6 risk caps.
- Agree daily loss and simultaneous exposure limits, then explicitly authorize ~$10
  real-money incubation. Compare realized fills/costs to backtest for 2–4 weeks.

## Validation performed in this workspace

- 10 unit tests pass, including parameterized rejection cases; Python compilation passes.
- Installed websockets and ran a bounded 12-second public-network smoke test.
- Gamma returned next-round metadata. **No RTDS ticks or complete round samples arrived
  during that short test.** End-to-end stream/candidate capture is therefore unverified.
- No backtest dataset, positive result, trading deployment or sustained process is claimed.

Sources: `../uploads/MOONDEV 6+ KNOWLEDGE.MD`,
`../Entire Predictions Tab Polymarket/Fees.md`,
`../Entire Predictions Tab Polymarket/Chainlink TWAP Prices.md`, and the existing S1/BME code.
