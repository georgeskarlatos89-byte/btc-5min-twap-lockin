# S5 — Wick Fade / Tape-Lag Reversion

**Status: research setup implemented; live trading deliberately unavailable.**

Isolated from S1, S3, S6 and both VPS fleets. No wallets, private keys, Moon Dev API
keys, signed orders, invented fills, or changes to existing bots. No VPS deployment
has been performed. This follows `../uploads/MOONDEV 6+ KNOWLEDGE.MD`: causal
research → honest backtest → $10 real-money incubation **after** validation. This
harness is research infrastructure, not that incubation stage.

## Implemented

- Public Kraken BTC/USD individual trade recording (not interpolated candles).
- Exact current `btc-updown-5m-{timestamp}` discovery, outcome-label token mapping,
  automatic round rollover, and public Polymarket snapshot/delta recording.
- UTC receive timestamps, monotonic receive clock, source timestamps inside raw
  payloads, session IDs, connection/error records and daily JSONL files.
- Symmetric ≥8 bp wick within ≤5 seconds, ≥60% retracement within ≤10 seconds
  of peak; duplicate/old ticks ignored; spot gaps >2 seconds reset the detector.
- Entries only for `0 <= elapsed < 180 seconds`. Entire wick detection is within
  this window. An entry is rechecked after the assumed submission delay.
- Buy opposite side only when `fair − ask >= $0.02 + entry fee + slippage`.
- Fail-closed freshness, valid probability, side/round identity, displayed depth,
  $10 total-entry-cash sizing, KILL file and three shadow candidates per UTC day.
- Receive-order replay; no source-time sorting, forward-filled candles or future
  fair-value lookups. Snapshot required before book deltas are accepted.
- Subsequent bid markouts at 0.5/1/2/5/10 seconds, actual delay, freshness flag,
  bid-depth check and explicitly assumed round-trip transaction costs.
- A quote-recovery **proxy** based on the pre-wick midpoint (diagnostic only,
  never used as fair value for entry).

## Quick start — Python 3.11+

```bash
cd /home/user/btc-5min-twap-lockin/s5_wickfade
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v

# Foreground, finite one-hour collection, public endpoints only:
.venv/bin/python collect.py --minutes 60 --output data

# Files must be in chronological receive order, from ONE collector/session chain.
.venv/bin/python replay.py data/*.jsonl --output reports/first
```

`reports/first/events.jsonl` contains wick detections, gate decisions and markouts;
`summary.json` contains counts and explicit assumptions. No fair input means
**all entry candidates are blocked**, but wick and quote research still runs.
`touch KILL` blocks shadow entry decisions; collection continues. Remove it only
once its cause is understood. Replay gates inspect this file at replay time; it
is not a reconstruction of a historical operator kill state.

### Data operations

Collect uninterrupted data for **7–14 days** on a supervised host before assessing
this hypothesis. Run only one collector per output directory. Synchronize host
clock with NTP; log and investigate negative source-to-receive ages. Public endpoint
outages are retried, recorded and reset pending signals. Session changes reset the
model state. Polymarket reconnects require fresh snapshots.

Books are high-volume: the smoke test received thousands of messages in 15 seconds.
Daily filename rotation is **not a storage quota**. Provision disk, monitor capacity,
archive/compress completed days and stop before disk exhaustion. Do not attempt a
multi-week raw recording in Arena's capped workspace snapshot. No long-running
collector was left running here. Ignored data/reports remain local and aren't Git
tracked; archive valuable data separately.

A user-systemd service template is supplied in `deploy/`. Edit installation paths,
create the venv on the target host and verify disk/NTP before installing it. It is
**not enabled**, and neither existing VPS has been accessed.

## Fair-value contract — deliberately unresolved

Kraken spot is a wick detector, **not** the Polymarket settlement oracle. The repo's
Chainlink TWAP docs and S1 model must not be assumed interchangeable with a spot
probability model. Confirm the exact selected market's resolution rules and oracle,
collect synchronized Chainlink/reference data, then calibrate an early-round
probability model with walk-forward training. This collector preserves Gamma market
metadata but does not yet record the settlement-oracle stream.

An independently validated fair model can provide `--fair-events fair.jsonl`:

```json
{"recv_ts": 1800000014.1, "t": 1800000014.0, "round_start": 1800000000, "side": "Down", "value": 0.52, "validated": true}
```

Fields are seconds since epoch. `recv_ts` is when the model output became available,
`t` is the oldest relevant input/as-of timestamp (never refresh it merely by rerunning
a stale model). File order must be causal availability order. `validated` is an
operator assertion, **not a statistical validation performed by this program**.
Do not populate it with fabricated prices or retrospectively fitted probabilities.
Only values already available by replay decision time are consumed.

Defaults of 2¢ entry fee/share, 0.5¢ slippage/share and 500ms latency are labeled
research placeholders, not current fee rules or measurements. Replace with token-
specific venue fees and measured latency before economic evaluation. An eligible
shadow candidate consumes one daily slot, **not a real fill**. Caps reset for each
independent replay run; persistent execution accounting is not implemented.

## What is NOT a backtest result

A bid markout is the observed later bid minus the initial ask. It is NOT a fill,
realized profit, or guarantee of simultaneous executable depth. Late observations
are flagged invalid. Missing horizons are censored/missing, not zero returns.
Studies with no quote recovery are right-censored; never discard them when
estimating correction-window distributions. Book source timestamps and local
receive timestamps have different clock errors. Fast sweeps, queue position,
partial fills and feed outages can make fills worse than displayed quotes.

Do not trust a strategy backtest from 1m bars, even interpolated. No return, Sharpe,
win rate or positive edge is claimed. No specific morning's market regime is assumed.

## Promotion plan and exit specification

1. **Research:** quality-audit 7–14 days of joint spot/books/oracle coverage. Measure
   wick counts, simultaneous quote responses and gaps. Pre-register hypotheses;
   compare up/down wicks and hold out complete later days to avoid fitting noise.
2. **Backtest:** validate fair probabilities and settlement semantics. Use actual
   token fee rules, recorded asks/bids/depth, delayed entry, adverse slippage and
   conservative unfilled/partial-fill handling. Report sensitivity and censored
   data, not just mean winning markouts.
3. **Execution adapter (not yet implemented):** persistent transaction ledger,
   single position/in-flight order, idempotent retries, reconciliation after restart,
   shared S1 inventory coordination, minimum order/tick checks and loss accounting.
   Call the same `core.gate` before submitting any entry.
4. **Exit policy to pre-register and test:** taker-only initial version; close when
   executable net bid reaches entry cost + 1¢/share, at a 3¢/share adverse move,
   or after 10 seconds, whichever first; force an exit attempt by round t=180s.
   Never suppress risk-reducing exits with the entry time/daily/KILL gate. Exit
   attempts are not guaranteed fills; stale books/disconnects require alerts and
   reconciliation. Maker exits require their own queue/cancel model and are deferred.
5. **Incubation after approval:** $10 total entry budget, max three entries/UTC day,
   one position, proposed $10 daily realized-loss stop, persistent risk counters.
   Run real money for 2–4 weeks and compare actual costs to the approved backtest.
   This is the Moon Dev I-stage; shadow markouts cannot substitute for it.

### Corrected structural latency kill

Measure the remaining **quote-mispricing lifetime from detection**, separately from
spot peak-to-retracement time and actual entry detection-to-fill latency. Kill if
opportunities disappear before achievable entry fills plus safety margin. Full
fill-to-fill latency includes intentional holding and is not the correct entry
race metric. `latency_verdict()` conservatively compares p25 quote window with p95
entry latency + 100ms, requiring at least 30 observations of each. That is a
provisional diagnostic threshold, not statistical proof. Unknown latency blocks
live promotion. Current proxy markouts are not automatically treated as true
mispricing lifetimes; calibrated fair data is required.

## Verification recorded in this workspace

- 17 unit tests passed: symmetric detection, consumed wick, gap/duplicate handling,
  small moves, time boundary, round reset, price inequality, data/model validity,
  market identity, depth, caps/KILL, latency direction, book deltas and replay.
- A finite 15-second public-network smoke test recorded 3,251 Polymarket payloads,
  22 Kraken payloads, one market metadata record and three status records.
- Replay completed without orders or synthetic fills. Zero wicks were detected in
  that tiny interval; it says nothing about strategy quality or daily opportunity.

## Files

| File | Purpose |
|---|---|
| `collect.py` | Public live recorder; no trading capability |
| `core.py` | Wick detector, shared risk gate, latency diagnostic |
| `replay.py` | Causal replay, blocked/eligible decisions, quote markouts |
| `tests/test_s5.py` | Deterministic tests |
| `deploy/s5-collector.service` | Uninstalled recording-only service template |

Local references: `../uploads/MOONDEV 6+ KNOWLEDGE.MD`,
`../Entire polymarket API Reference docs/Market Channel.md`,
`../Entire Predictions Tab Polymarket/Chainlink TWAP Prices.md`,
`../BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD`.
