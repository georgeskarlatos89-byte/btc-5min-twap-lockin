# arena-ai-btc-5-minute new strategies #4 liquidation cascade continuation (15m only) — living record

Every run or change to strategy #4 is logged here as a Part. Same rules as the other records:
plain language, no claims without data, no keys or addresses ever.

**What S4 is, in one breath:** when a lot of leveraged Bitcoin positions get force-closed at
once (a "liquidation cascade", at least $1.5M in 10 seconds, almost all on one side), those are
forced market orders that tend to keep pushing the price the same way for a minute or three.
S4 watches the Moon Dev multi-exchange liquidation feed (about 1 s behind reality), waits for
the spot price to confirm the direction by at least 2 bps, and then, on the 15-minute round
only, buys the continuation side as a taker if the ask is still cheaper than a model
probability minus fee minus 2¢. It holds to resolution. It is bundled with the S6 coin-flip
maker in one process ("S46") because the same cascade that makes S6 pull its quotes is S4's
entry signal.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder
`~/#4 liquidation cascade continuation (15m only)/s46_coinflip_cascade/` (symlink
`~/s46_coinflip_cascade`), systemd unit `s46-harvester.service`. The guide proposed the folder
name `#4 S4 S6 Paired`; the user's local folder name was used instead, matching the #1/#2/#6
pattern. Package source: this folder's `s46_coinflip_cascade/` (= the zip, byte-identical, 11
files). GitHub: `georgeskarlatos89-byte/btc-5min-twap-lockin` → `s46_coinflip_cascade/`.

---

## Part #1 — Package review, local pipeline verification, three fixes, DRY deploy, event-study data (2026-09-26)

### Context
The user handed over the S46 package with `AI_VPS_GUIDE.md` and `DEPLOYMENT.md`, asked to
follow the guide, verify the pipeline works BEFORE pushing to the VPS, and give it its own
folder. The Moon Dev key is the same one S3/S6 use (Documents file), piped into `.env` over
SSH, never displayed.

### Package check (before anything ran)
- Zip vs folder: 11 files, 0 mismatches. Compile OK (Python 3.13 locally, 3.12 on the VM).
- Security read of every file: hosts only `api.moondev.com`, `clob/gamma/data-api.polymarket.com`,
  `ws-live-data.polymarket.com`; no eval/exec/subprocess, no key-shaped strings. Clean.
- The harvester is the S6 code with S4 added on top (about 1,300 changed lines vs S6). It
  already carries the S3/S6 fixes from 09-24 (compact RTDS filters, one pull per cascade,
  crash guard + heartbeat, DRY daily stop not a gate, legs counted not shares).

### Local pipeline verification (scratch copy with the real key, 6 min across a bell)
| item | result |
|---|---|
| preflight | 5m + 15m tokens OK, books fetched, key present, live keys absent, KILL absent |
| RTDS | connected, chainlink + twap60 |
| Moon Dev liq feed | 278 rows parsed, shape logged |
| S6 | quoted the 15m round both sides, pulled 14 s later by `BAND_EXIT mid=0.56` |
| S4 | no cascade in the window (correct: none happened) |
| heartbeat / errors | written / 0 |

### Three bugs found and fixed (strategy constants untouched, verified byte-identical)
| # | where | bug | fix |
|---|---|---|---|
| 1 | `s46_harvester.py` tick loop | settlement lived inside the loop over the CURRENT round, where elapsed time is always shorter than the round, so `e > T+45` was never true: no round ever settled, no S6 leg or S4 outcome would ever be counted (S3 and S6 had the same bug, fixed by the other session the same morning) | settle FINISHED rounds in a separate pass, retry every 30 s until Gamma reports |
| 2 | `s46_harvester.py` settlement | the round P&L (S6 + S4 together) was added to the S6 ledger, so when both traded the same 15m round the S4 result leaked into the S6 kill-test P&L | separate `s6_pnl` |
| 3 | `s46_backtest.py` event study | (a) analysed only the first 2,000 of ~27,000 harvest files (about 1.5 of 20 days) and said nothing; (b) each file is a rolling 10-min window taken every minute, so one cascade was counted in ~10 files; (c) the parser did not read the harvest's `value` field, so every row was $0 and zero cascades would ever be found | all files, global dedupe (same side within 60 s = one event, biggest print kept), `value` parsed, per-day and per-hour tables, a "what this does NOT show" section |

Offline proofs: a finished 15m round with a lone S6 leg and an S4 fill now settles and counts
(S6 leg lost −$9.80, S4 won +$8.06, ledgers separate); 12 overlapping synthetic snapshots with
two cascades → 24 raw hits → exactly 2 events.

### VPS deploy (DRY) — run mechanism
1. Folder created (chmod 700), package copied, md5 = local, symlink for tooling.
2. `.env` (chmod 600): `MOONDEV_API_KEY` + `LIVE_TRADING=0`. No Polymarket keys → cannot order.
3. Preflight 09:37:05Z: tokens OK, books OK, session QUIET (weekend), key present, DRY.
4. Bounded 5-min run: liq feed 142 rows, RTDS connected, S6 quoted the 09:40 bell (5m), pulled
   19 s later by `BAND_EXIT mid=0.45`, heartbeat written, 0 errors, no cascade.
5. `s46-harvester.service` installed (systemd verify clean), active 09:42:07Z; restarted
   09:59:03Z on the settlement fix. Ninth strategy service on the box (with S5 from the other
   session, ten units).

### About the S6 duplicate
`s6-harvester.service` (the standalone S6 from 09-24) is still running. The guide says not to
run both S6 copies at once or to share the $30 net cap. Both are DRY, so there is no real
exposure, only duplicate S6 data. I did not stop the old one (its kill-test sample started
09-26 09:54Z after the other session's fix). Retiring one of the two is the user's call.

### Telegram wiring (shared `s1_monitor.py`)
S46 added to the generic maker checker with prefix normalisation (`S6 QUOTE` → `QUOTE`,
`SKIP S6` → `SKIP`, `PULL BOTH S6`) and S4 counters. Hourly line: S6 counts + kill-test sample,
then `S4: cascades seen, triggers, dry fills | all-time triggers/fills/settled/wins/pnl by size`.
Pings: one informational per `S4 TRIGGER`, plus the usual dead-loop heartbeat, 0 quotes in a
quiet hour, key 401/403, `FILL REJECTED`, tick error, LIVE/KILL changes, service inactive.
Offline: normal lines → 0 pings; S4 trigger → 1; gate violation + key death → 2.

Concurrency note: another session added S5 checks to the same local monitor file this
morning; the version deployed at 09:44Z had S46 only. The merged file (S46 + S5) was tested and
deployed once the VM was confirmed unchanged since 09:44Z.

### Event-study data (the S4 B-step input)
The guide expects the Moon Dev harvest at `~/Moondev API/data/all_liquidations/10m` on twapvm.
It is not on twapvm; it is on the fleet box under `~/Moondev API/data/all_liq_10m/<date>/`
(20 days 09-07..09-26, ~1,370 gz files per day, 283 MB, `moondev-harvest.service` active).
Copied read-only (tar over SSH, nothing written on the fleet box) to the same path on twapvm.

**Fourth bug, found by the first real run (this one matters for LIVE):** the first pass
reported a $2,024M BTC cascade in 10 s, which is more than exists. The raw rows behind it were
Binance COIN-margined contracts (`BTCUSD_PERP`, delivery `BTC_260925`): the feed gives
`quantity` in contracts (100 USD face each) but computes `value = price × contracts`, so
20,738 contracts ($2.07M real) showed as $1.72B. The live harvester parses the same field and
matches the same symbols, so a small inverse-contract liquidation would have fired a false
cascade pull and, when live, a false S4 taker entry. Fix in both parsers: symbols matching
`(USD_PERP|_dddddd)$` use `contracts × 100`. Checked the other exchanges: Binance/Bybit/
Hyperliquid `value` = price × qty exactly; OKX `value` is already correct USD. S3 and S6 pull
triggers parse the same field (noted in memory for the other session).

Event study, corrected (25,507 snapshots, 18 days with events, 1 min 44 s to run):

| item | value |
|---|---|
| raw 10-s window hits | 1,429 |
| distinct cascade events (same side within 60 s = one) | **154** (BUY/shorts 83, SELL/longs 71) |
| per day | mean 8.6, range 1 (Sun 09-13) to 27 (09-21) |
| ≥ $1.5M / ≥ $2.5M / ≥ $4M / ≥ $8M | 154 / 25 / 13 / 3 |
| by UTC hour | 12h–14h hold 61 of 154 (US open); 10h has 0; S6 quiet hours 00–12 hold 51 |
| largest | 09-21 08:38:55Z BUY $35.2M (538 prints), 09-16 18:08Z BUY $20.2M, 09-10 23:15Z SELL $10.4M |

What this means for expectations: S4 gets roughly 8–9 cascade opportunities a day, 84 % of
them in the smallest bucket where the model prior is only 0.60. With the 2 bps spot
confirmation, the 15m window (30 s–12 min), the 0.30–0.85 ask band and the "ask ≤ model −
fee − 2¢" rule still to pass, live DRY triggers will be a fraction of that. The prior itself
(0.60 → 0.75 by size) is still unmeasured: the study counts events, it does not yet measure
what spot did 60 s and 180 s after each one, and I deliberately did not join the 15m round
result because the round resolves against its open, not against the price at cascade time,
which would flatter S4. That continuation join is the next B-step task; the BME order-book
capture on this box (since 09-21) can supply the spot and book history for the overlap days.

Report + CSV: `~/s46_coinflip_cascade/s46_event_study_report.md`, `s46_event_study.csv`.

### Files touched in this Part
- Local: `s46_coinflip_cascade/{s46_harvester.py, s46_backtest.py, deploy/s46-harvester.service}`;
  `#1 …/s1_harness/s1_monitor.py`; this record.
- VM: `~/#4 liquidation cascade continuation (15m only)/s46_coinflip_cascade/*`,
  `/etc/systemd/system/s46-harvester.service`, `~/Moondev API/data/all_liq_10m/` (copy),
  `~/s1_harness/s1_monitor.py` + reference copy.
- Memory: `project_s46_cascade.md`, index line.
- GitHub: `s46_coinflip_cascade/` (no `.env`, logs, stats), monitor, this record.
