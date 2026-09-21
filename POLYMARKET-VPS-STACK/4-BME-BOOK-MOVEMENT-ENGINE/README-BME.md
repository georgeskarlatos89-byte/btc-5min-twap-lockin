# BME — BOOK-MOVEMENT ENGINE
### The forward-only order-book movement dataset + signal scorer — the one unopened data source

**STATUS: ACTIVE — this is the live thread. Capture ≥7 days on the VPS, then score.
No strategy yet; pre-registered gates decide.**

---

## What this is

Two programs:
- **`bme_capture.py`** — a keyless recorder that subscribes to Polymarket's public
  **market channel** WebSocket on every btc-updown 5m+15m round and writes every
  order-book movement to disk: every `price_change` delta (price, side, new size,
  best bid/ask, **cause hash**), every trade print, tick-size changes, plus the
  **Chainlink spot + TWAP-60 ticks fused onto the same millisecond timeline** (via
  RTDS). Full L2 snapshots every 10s, 1-second book-state/depth, per-hash stats.
- **`bme_score.py`** — one command that scores whatever is recorded against the
  pre-registered signals S1–S5 and writes `s9_data/bme/bme_score_report.json`.

**Why forward-only:** no historical L2 exists anywhere — the venue's price-history
endpoints stop at 1-minute buckets. Whoever records the book's movements, owns the
only copy. (Full doc sweep with ✅/❌ per endpoint: `ORDER-BOOK-EDGE-CHECKLIST.md`.)

## The theory

The maker bots obviously watch this stream — but *nobody outside them records it and
scores the movements against outcomes*. Five pre-registered signal families:
1. **S1 flicker/batch** — do repeated cause-hashes (re-quote bursts) mark fake
   support/resistance levels?
2. **S2 pre-tick pulls** — does a cancel burst in the 200ms *before* a Chainlink tick
   predict the tick? (Makers front-running the feed.)
3. **S3 imbalance** — does early-round bid/ask depth asymmetry predict the outcome
   after controlling for mid?
4. **S4 double-tap reaction** — how fast do asks get pulled after a proven-informed
   2nd taker buy? (The S9 follower window, reborn book-timed. Needs wallet join.)
5. **S5 divergence** — early-round mid vs TWAP-fair value vs outcome.

## Confirmed mechanics (verified live in the sandbox, 20 min total capture)

- Channel `wss://ws-subscriptions-clob.polymarket.com/ws/market` works exactly as
  documented; sustained **~978 price_change/s** across 4 tokens (peaks >2,000/s in the
  15m informed window); PING every 10s; `initial_dump` rebuilds the book on reconnect.
- **100% L2 reconstruction fidelity** — 102/102 levels, exact sizes, rebuilt from
  snapshot + deltas over a 78s window. The book is recoverable at any millisecond.
- RTDS fusion works (spot+twap on the same ms timeline); one string-timestamp bug was
  found and fixed live.
- Ops: occasional `slow consumer` kicks at peak load — auto-reconnect ~3s, gaps bounded
  by the 10s snapshot cadence. **Disk: ~105MB/hr, ~2.5GB/day compressed** (gzip 11.2×);
  days auto-gzip at UTC midnight; `--series 5m` halves it if disk is tight.
- First score on the 20-min test (1.25M events, n=1,053 tick pairs): **S2 shows the
  pre-registered signature — top-quintile cancel bursts (≥124 pulls) precede ticks
  averaging 1.92 pts vs 0.63–0.73 pts for the quiet book (~2.7×, top decile 2.37 pts)**,
  consistent across both independent sub-runs. Single 20-min regime slice — a *hint*,
  not evidence. The 7-day dataset decides.

## The gates (same discipline as everything in this stack)

A signal graduates only at **≥ +2¢/share post-fee EV over ≥100 independent rounds** on
the full dataset. The scorer prints this reminder on every run. Analysis rules for the
7-day score: split S2 by day and score direction/magnitude separately (p_up may
confound with session drift).

## Run on the VPS (the 7-day plan)

```bash
cd ~/polymarket-stack
./venv/bin/python -u 4-BME-BOOK-MOVEMENT-ENGINE/bme_capture.py        # forever (or systemd: bme-capture.service)
# after >= 7 full days:
./venv/bin/python -u 4-BME-BOOK-MOVEMENT-ENGINE/bme_score.py          # -> s9_data/bme/bme_score_report.json
```
Capture outputs land in `4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/`
(`events_YYYYMMDD.csv[.gz]`, `books_…`, `bookstate_1s_…`, `hashes_…`).
Kill: `touch 4-BME-BOOK-MOVEMENT-ENGINE/KILL` (gzips the raw events file on exit).
Disk plan: ~18GB/week — prune with
`find 4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme -name '*.gz' -mtime +7 -delete` after scoring.

Shipped in `s9_data/bme/`: the full 20-min validation dataset (`events_20260921.csv.gz`
= 1.25M events, plus books/bookstate/hashes, the capture log, and the first
`bme_score_report.json`) — so the scorer can be re-run on the VPS immediately as a
smoke test before the 7-day wait.

Full deployment + scoring detail: **`BME-RUNBOOK.md`**. Endpoint-by-endpoint doc
checklist: **`ORDER-BOOK-EDGE-CHECKLIST.md`**. Key source docs (Market Channel spec,
order book REST, realtime, session keys, rate limits): **`../source-docs/`**.
