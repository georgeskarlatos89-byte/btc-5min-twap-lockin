# arena-ai-btc-5-minute new strategies #5 wick fade and tape-lag reversion — living record

Every run or change to strategy #5 is logged here as a Part. Same rules as the other records:
plain language, no claims without data, no keys or addresses ever.

**What S5 is, in one breath:** sometimes Bitcoin's price spikes sharply for a few seconds and
snaps right back (a "wick"). The idea is that the Polymarket Up/Down quotes for the current
5-minute round over-react to the spike and are slow to come back, so buying the opposite side
during that lag could be cheap. S5 does NOT trade. It is a read-only recorder: every Kraken
BTC/USD trade and every order-book change on the current round's two tokens, with receive
timestamps, so a later replay can find wicks, check the strategy's gates and measure how the
quotes moved afterwards. There is no fair-value model yet, so by the package's own design
every entry decision is blocked; the point of the next 7–14 days is data, not trades.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder
`~/#5 wick fade and tape-lag reversion/s5_wickfade/` (symlink `~/s5_wickfade`), own Python
venv, systemd unit `s5-collector.service` plus two timers (`s5-archive`, `s5-diskguard`).
Package source: this folder's `s5_wickfade/` (= `S5 WICK FADE and TAPE-LAG REVERSION.zip`,
byte-identical, 13 files). GitHub: `georgeskarlatos89-byte/btc-5min-twap-lockin` → `s5_wickfade/`.

---

## Part #1 — Package review, local + VPS verification, the storage problem, deploy (2026-09-26)

### Context
The user handed over the S5 research package with `AI-VPS-HANDOFF.md` and asked to follow the
guide, verify the pipeline before pushing to the VPS, and use its own folder. The guide's own
state: 17 tests pass, a 15-second smoke test got both feeds, no VPS deploy, and an explicit
warning that raw book traffic "can be substantial" with no quota or retention in the code.

### Package check
- `sha256sum -c SHA256SUMS`: 12 of 12 OK; zip vs folder: 13 files, 0 mismatches.
- Security read of every file: hosts are only `ws.kraken.com`, `gamma-api.polymarket.com`,
  `ws-subscriptions-clob.polymarket.com`; no keys, signing, eval/exec/subprocess. Clean.
- Unit tests locally (Python 3.13): 17 of 17 OK; compile OK. Needs Python 3.11+
  (`asyncio.timeout`); the VM has 3.12.

### Local verification (before any VPS work)
6-minute plain collection across one 5-minute boundary, then the guide's inspection script
and a replay:

| item | result |
|---|---|
| Kraken trades | 188 trade rows in 129 trade messages (+357 heartbeats), lag median 0.22 s |
| Polymarket | 149,010 messages: 2,248 book snapshots, 145,643 price changes, 1,122 last-trade prices |
| round rollover | 3 market records, 2 rounds (`…5300` → `…5600`), Up/Down token mapping explicit |
| replay | 149,505 rows, `MISSING_ENTRIES_BLOCKED` (expected: no fair model), 0 wicks in 6 min |
| feed errors | 1: Polymarket closed the socket with `1013 slow consumer: send buffer full` (this PC could not write fast enough); the collector reconnected and re-discovered the round |
| **data volume** | **126 MB in 6 minutes ≈ 29 GB per day** |

That last line is the real finding: the VM has 29 GB free, so raw collection would fill the
disk in about a day, and 14 days would need roughly 400 GB.

### Changes (plumbing only; the detector, gates and replay are untouched)
| # | change | why |
|---|---|---|
| 1 | `collect.py --gzip`: day files stream through gzip (`data/<day>.jsonl.gz`), raw payloads unchanged, sync-flush every 5 s so a killed session stays readable | 9.8× smaller (measured); replay needs plain JSONL, so `gunzip -k` first (documented in the code and here) |
| 2 | status errors log the exception class when the message is empty | `str(TimeoutError())` is `""`, the same blind spot found in S2 |
| 3 | `deploy/s5-collector.service`: system unit as the fleet user from the strategy-named folder, own venv, 24-h sessions, `--gzip`, hardening kept | the template was a per-user unit under a path that does not exist here |
| 4 | `deploy/s5-archive.{sh,service,timer}`: daily 00:40 UTC, gzips CLOSED plain day files only (not today's, not written for 30 min); never deletes | the guide's retention requirement |
| 5 | `deploy/s5-diskguard.{sh,service,timer}`: every 5 min, stops ONLY `s5-collector` below 2 GiB free; never deletes | the guide's "stop before disk exhaustion" |
| 6 | `SHA256SUMS` regenerated (18 files) | |

Local re-check with `--gzip`: 2 minutes → 40,447 rows, 3.48 MB compressed (34 MB plain),
decompressed copy replays identically.

### VPS install
Folder created (chmod 700), package copied, checksums 18/18 on the VM, venv Python 3.12.3 with
websockets 15.0.1, 17 tests OK, compile OK, all five unit files pass `systemd-analyze verify`.
Guide preflight (1 min plain): 9,448 rows, 19 live Kraken trade rows, 2 market records, 9,367
Polymarket payloads, statuses start/connected/stop, replay `MISSING_ENTRIES_BLOCKED`.

### VPS measurement (10 min with `--gzip`, 09:50:24Z–10:00:24Z, across two round boundaries)
| item | result |
|---|---|
| rows | 217,685 (216,813 Polymarket, 866 Kraken, 3 market, 3 status) |
| Kraken trades | 354 individual trades, source→receive lag median 14 ms, p90 21 ms |
| Polymarket books | 5,274 snapshots, 208,908 price changes, 2,634 last-trade prices; lag median 10 ms, p90 19 ms, p99 98 ms, max 3.0 s |
| rounds | 3 discovered (`…6200`, `…6500`, `…6800`), rollover clean, no `No active exact market` errors |
| feed errors | 0 (the VM keeps up; the PC's `1013 slow consumer` did not recur) |
| replay | 217,685 rows, 3 markets seen, 1 feed reset (the connect record), 0 wicks in 10 min, `MISSING_ENTRIES_BLOCKED` |
| **compressed rate** | **110 MB/h → 2.6 GB/day → 36 GB for 14 days**; disk has 29 GB free and BME already writes ~2.2 GB/day |

**Verdict in the guide's words: COLLECTING** (both feeds live, rollover verified, no errors),
with the storage question open (below).

### Sustained collection (10:00Z)
`s5-collector.service`, `s5-archive.timer` (next 00:40 UTC) and `s5-diskguard.timer` (every
5 min) enabled and active; first service session `data/2026-09-26.jsonl.gz` growing at the
measured rate. The service log under the folder is root-owned (systemd creates it), read it
with sudo. Note: the service started at 09:57Z while the detached measurement still had 3
minutes to run; they wrote to different directories, so no file was shared.

### The storage decision (yours)
At 2.6 GB/day compressed, the requested 7–14 days needs 18–36 GB. Free space is 29 GB and
falling ~2.2 GB/day from BME on the same disk. Without a change, the disk guard will stop S5
(and BME's own guard will stop BME) around a week in. Options: enlarge the disk once more
(the earlier 20→50 GB procedure is in memory and the S1 record; roughly 150 GB would cover
both recorders for a month), or accept a 7-day window, or stop BME when its 7-day gate is
met (it is already past day 5). Nothing is deleted automatically in any case.

### Related fixes found on the way (S3 and S6, shared maker code)
While checking the fleet before deploying I read the S3/S6 stats: after two days both showed
`n = 0` rounds settled (S3: 604 quotes, 1 simulated fill; S6: 658 quotes, 0 fills). Cause: the
tick loop only ever evaluated settlement on the CURRENT round, where "the round ended 45 s
ago" can never be true, so `settle_round` was unreachable. No ROUND-RESULT was ever logged and
the S6 kill-test counters could never move. Fixed in both: finished rounds are settled from
the round table, retried every 30 s until Gamma reports the outcome. Second fix: a one-sided
book (no bids or no asks) made `mid_for` add `None + float` every 2 s (159 `tick error` lines
in S3's log; the crash guard held but it stormed alerts). Both makers restarted 09:54Z on the
fixed code, strategy constants byte-identical. The other session's S46 copy was checked and
does not contain either pattern. Details in the S3 record (Part #3) and S6 record (Part #2).

### Telegram wiring (shared `s1_monitor.py`)
Hourly status gains an S5 line with the plain-English description and: Kraken trade messages
and book messages in the hour with last-seen ages, rounds discovered, feed errors, session
start/stops, today's file size, total data, archived days, measured MB/h and the GB/day
projection, disk line, KILL. Urgent pings: Kraken trades or Polymarket books silent for
3 min while the service is active (the guide's "retrying/offline process is not a healthy
collector"), feed-error storm (> 20/h), round discovery failing 3× in an hour, 14-day
projection above half the free disk, KILL appears/disappears, service inactive.
Note: the monitor file is shared with another session that added its S46 strategy today;
their entries were kept and the VM copy was verified identical to my baseline before deploy.
Offline tests: normal hour → 0 pings; stale Kraken → 1; disk projection → 1; discovery
failure → 1.

Two refinements after the first deploy: (1) the collector writes `<day>.jsonl.gz`, so the
monitor got an incremental gzip reader (keeps a decompressor across its 30 s loop, handles the
new gzip member each restarted session appends and the writer's 5 s sync-flush tail; first
sight of a file skips history) — tested on a real two-member file with a partial last line;
(2) S2 (60-min sessions) and S5 (24-h sessions) are restarted by systemd by design, so their
restarts now alert only above 3/h and 2/h respectively instead of on every rollover (the
10:01Z "s2-collect restarted" ping was that false alarm).

### Files touched in this Part
- Local: `s5_wickfade/collect.py`, `deploy/*` (adapted unit + 2 timers + scripts),
  `SHA256SUMS`; S3 `s3_maker.py`, S6 `s6_harvester.py` (settlement + one-sided book);
  `#1 …/s1_harness/s1_monitor.py` (S5 checks); this record; S3/S6 records.
- VM: `~/#5 wick fade and tape-lag reversion/s5_wickfade/*`, `.venv`, `data/preflight`,
  `data/measure`, units under `/etc/systemd/system/`; `~/s3_feefarm/s3_maker.py`,
  `~/s6_coinflip/s6_harvester.py`; `~/s1_harness/s1_monitor.py` + reference copy.
- Memory: `project_s5_wickfade.md`, index line; S3/S6 memory notes.
- GitHub: `s5_wickfade/` (no data, no venv, no reports except the shipped smoke ones),
  `s3_feefarm/s3_maker.py`, `s6_coinflip/s6_harvester.py`, monitor, records.

---

## Part #2 — Disk enlarged to 200 GB: the 14-day window is now storable (2026-09-26)

The user resized the GCP disk 50 → 200 GB in the console (Storage > Disks > disk > Edit,
the procedure from the S1 record). Applied live on the VM, no service restarted:

| step | result |
|---|---|
| `lsblk` before | disk 200 G, root partition 49 G, 29 GB free |
| `growpart /dev/sda 1` | partition 49 G → 199 G |
| `resize2fs /dev/sda1` | filesystem 193 GB, **174 GB free** (10 % used) |
| services | all ten still active |

At the measured 2.6 GB/day (S5) + ~2.2 GB/day (BME) the disk now holds about a month of
both recorders. The S5 disk guard (2 GiB) and BME's guard stay in place as backstops. Review
date for the S5 collection: **2026-10-10** (14 days), with the first replay on closed day
files from 2026-09-27 onward.
