# BME — BOOK-MOVEMENT ENGINE RUNBOOK
### `s1_harness/bme_capture.py` — the forward-only dataset nothing else on Polymarket serves

**Status (2026-09-21): built, live-tested 14 min end-to-end, 100% L2 reconstruction verified.**
No historical L2 exists anywhere (prices-history bottoms out at 1-minute buckets) — this
daemon records what cannot be backfilled: every order-book movement, per-cause hash,
trade print, and the Chainlink spot/TWAP ticks on the same millisecond timeline.

## What it records (per UTC day, in `s1_harness/s9_data/bme/`)

| file | contents |
|---|---|
| `events_YYYYMMDD.csv` (→ `.gz` at rotation) | every `price_change` delta (price, side, new aggregate size, best bid/ask, **cause hash**), every trade print (price, size, taker side, fee bps), tick-size changes, best-bid/ask, and fused `spot_tick` / `twap_tick` markers — each spot tick tagged with **`pretick_pulls_200ms=N`** (cancels in the 200 ms before the tick) |
| `books_YYYYMMDD.csv` | full L2 snapshots (on subscribe + every 10 s per token) |
| `bookstate_1s_YYYYMMDD.csv` | 1 s top-of-book + top-10 depth per token (imbalance studies) |
| `hashes_YYYYMMDD.csv` | per-cause-hash activity stats, refreshed every 5 min (flicker/spoof candidates) |

Measured on the 14-min test (2 series = 4 tokens): **978 price_change/s sustained**, 75.7k
distinct hashes, 2,471 prints, 702 spot + 700 twap ticks. **Disk (measured): ~105 MB/hr,
~2.5 GB/day compressed** (gzip 11.2×). A 7-day dataset ≈ 18 GB — plan disk accordingly, or
run `--series 5m` only (~half). Old days auto-gzip at UTC midnight.

## Deployment (VPS)

```bash
# same venv as the stack (needs only: websockets)
cd ~/s1-vps-stack && ./venv/bin/python s1_harness/bme_capture.py   # forever
# bounded test first: --minutes 15
# stop: touch s1_harness/KILL   (also stops the other services — or systemctl stop bme-capture)
```

A systemd unit ships in `systemd/bme-capture.service` (same pattern as the other three).
Watch: `tail -f s1_harness/s9_data/bme/bme.log` (stdout) — expect one line per round
rollover per series and quiet otherwise.

## Ops notes (from the live test)

- **Slow-consumer kicks:** at ~1,000 events/s peaks the server may drop us
  (`Close(1013, slow consumer)`); the daemon reconnects in 2–3 s and `initial_dump`
  rebuilds the book. Observed once in 14 min at a 15m informed window. Acceptable for
  capture; gaps are bounded by the 10 s snapshot cadence.
- **Hash semantics:** docs say "hash of the order", but live data shows single hashes
  touching ~13 price levels in one millisecond (batch re-quote/mass-cancel events).
  Treat the hash as a *cause identifier* — single orders AND batch operations — until
  the 7-day data clarifies. Batch-burst detection is itself a signal.
- **Round rollover:** auto-discovers each new round's tokens at every bell (retries if
  gamma lags), unsubscribes the ended round. The 07:15 double bell (5m+15m) handled
  cleanly in the test.

## Pre-registered scoring plan (before looking for any edge)

Run ≥ **7 full days**, then score exactly like everything else in this repo —
**incubate a signal only at ≥ +2¢/sh post-fee EV over ≥ 100 independent rounds**:

1. **S1 flicker/batch:** do repeated cause-hashes (or batch re-quote bursts) mark levels
   that act as fake support/resistance? (hashes_ file + events join)
2. **S2 pre-tick pulls:** does `pretick_pulls_200ms` predict the next tick's direction/size?
   First 12 min already showed a spike to 378 pulls immediately before an 11.8-pt drop —
   an anecdote, not evidence; the 7-day distribution decides.
3. **S3 imbalance:** does 1 s bid/ask depth asymmetry predict round outcome after
   controlling for mid? (bookstate file + resolutions)
4. **S4 double-tap reaction:** time from an informed 2nd taker BUY (wallet-joined
   post-hoc via v1 `/trades`, the pyramid data's proven-informed flow) to the opposite
   ask being pulled — the S9 follower window reborn, book-timed.
5. **S5 divergence:** early-round mid vs TWAP-fair value vs outcome (uses the fused
   twap_tick markers — the settlement feed itself is in the same timeline).

## Scoring — `s1_harness/bme_score.py` (run it on the VPS after the capture window)

One command scores whatever is recorded in `s9_data/bme/` (any number of days/parts;
reads `.csv` and `.csv.gz` transparently, header or headerless):

```bash
./venv/bin/python s1_harness/bme_score.py          # -> s9_data/bme/bme_score_report.json
```

Signals scored now: **S1** flicker/batch (cause-hash churn, same-ms batch bursts),
**S2** pre-tick pulls (quintiles of `pretick_pulls_200ms` vs next-tick move),
**S3** early-round depth imbalance vs official outcome (gamma join, closed=true
fallback). S4/S5 print as pending (need the 7-day set / wallet join).

**First score on the 20-minute pipeline test (1.25M events, n=1,053 tick pairs, 5
rounds — a single regime slice, NOT evidence):**
- **S2 shows the pre-registered signature**: top quintile of pre-tick cancel bursts
  (≥124 pulls) precedes Chainlink ticks averaging **1.92 pts** vs **0.63–0.73 pts**
  for the quiet book (~2.7×); top decile (≥184 pulls) **2.37 pts**. Consistent across
  both independent sub-runs (2.25 / 2.37). The cancel burst front-runs the tick.
- **S3 directionally consistent** on 5 rounds (mean imbalance +0.031 when Up won vs
  −0.069 when Down won, 80% sign accuracy) — meaningless n, recorded for the gate timer.
- **S1**: 2,268 batch bursts (≥5 levels in one ms, max 99 levels) — the re-quote-bots'
  footprint is dense and easily identifiable.

Gates unchanged and enforced by the scorer's output: nothing is tradeable until
**≥100 independent rounds at ≥ +2¢/sh post-fee EV** on the full dataset. Note for the
7-day analysis: S2's `p_up` gradient may confound with the session's drift — score
direction and magnitude separately, and split by day.

## Verification record (this session)

- 14-min bounded run: all events captured, rollovers at 07:05/07:10/07:15 auto-handled,
  clean KILL-free exit at `--minutes`.
- **Integrity: full L2 reconstructed from snapshot + deltas = 102/102 levels exact
  (100%)** over a 78 s window on the busiest token.
- RTDS fusion verified (spot+twap on the same ms timeline); the string-timestamp bug
  found and fixed live (ring comparison).
- Artifacts kept: `bme/bme_capture_test.log`, `books_/bookstate_/hashes_2026-09-21`
  samples + `events_*.gz`.
- Second run (6 min, patched build): steady **978 events/s again**, **gzip-on-exit
  verified** (raw file folded into the `.gz`), **CSV headers written on fresh files**
  (unit-verified; appended legacy files stay headerless by design — the scorer handles
  both), one more slow-consumer kick auto-recovered (≈1 per 6–14 min at peak load).
- **`bme_score.py` validated end-to-end** on the combined 1.25M-event dataset,
  including multi-member gzip concatenation and the gamma resolution join.
