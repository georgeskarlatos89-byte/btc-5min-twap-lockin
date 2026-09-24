# arena-ai-btc-5-minute new strategies #3 Fee-Farm Two-Sided Maker — living record

Every run or change to strategy #3 is logged here as a Part. Same rules as the #1 record:
plain language, no claims without data, no keys or addresses ever.

**What S3 is, in one breath:** a market-maker for the Polymarket BTC 5-minute and 15-minute
Up/Down rounds. During quiet hours it rests a buy at 0.45 on BOTH the Up token and the Down
token (50 shares each). If both sides fill, it paid 0.90 for a pair that always pays 1.00 at
resolution (one side wins), so it pockets 0.10 per pair with no directional bet. If only one
side fills it is "naked" (a real bet) and the rule says cut that leg 20 s later. It pulls all
quotes when the market gets violent (liquidation cascade, spot jump, extreme buy/sell imbalance
from the Moon Dev feed) because that is when naked legs get run over.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder `~/s3_feefarm/`, systemd unit
`s3-maker.service` (6th service on the box, next to observer, trader, monitor, S9 watcher and
BME capture). Package source: the other session's git clone `btc-5min-twap-lockin/s3_feefarm/`
inside this folder. GitHub: `georgeskarlatos89-byte/btc-5min-twap-lockin` → `s3_feefarm/`.

---

## Part #1 — Package review, five plumbing fixes, DRY deploy, Telegram wiring (2026-09-23/24)

### Context
The user handed over the S3 package (README, playbook, `s3_maker.py`, `s3_backtest.py`, the
B-step report + rows CSV, `run_*.sh`, a systemd unit), the Moon Dev docs bundle, and a fresh
Moon Dev API key (file in `Documents/`, key never displayed anywhere). Ask: apply the strategy
on the VPS, Telegram updates like S1 (strategy name + plain description, urgent-only), and
error notifications for data mismatch / failed API calls, since the Moon Dev key expires after
an unknown period.

### What the package's own backtest says (read before deploying)
`s3_backtest_report.md`, 2026-09-22, ONE 24-hour window, 380 rounds:

| version | net | pairs | naked legs | adverse / gross capture |
|---|---|---|---|---|
| doc-verbatim (bid 0.49, breakeven offer) | **−$781** | 71 | 117 | 20.2× (adverse wins) |
| best of 32 in the sweep (cut20, bid 0.45, 60 % window) | **+$69** | 48 | 131 | 0.68× |
| the other 31 sweep versions | all negative | | | |

Fill-count baseline for alert thresholds: rounds-with-fills per hour mean 7.52, σ 2.12,
min 2, max 12 (n = 25 h). 10,044 "touch fills" were rejected by the band gate (mid outside
0.45–0.55) — those are the fake fills that once produced a +5.71 pp illusion.

**Honest reading:** the deployed constants ARE the best-of-32 from one day. Picking the best
of 32 on one day and then running exactly that is textbook over-fitting. The backtest's fill
rule ("the first taker SELL print at or below our bid means we got filled") is the optimistic
queue assumption this project has already found invalid four times (paper-maker fill bug,
EXP-D, gabagool no-chase: "a resting bid only fills on the losing side", naked legs won 9 %).
So the package's own numbers do not justify money; they justify a plumbing test.

### Security scan of the package (user asked "make sure it has no bad code")
- Read every `.py`, `.sh`, `.service`, `.md` in `s3_feefarm/` (about 100 KB). No network
  destinations other than `api.moondev.com`, `clob.polymarket.com`, `gamma-api.polymarket.com`,
  `data-api.polymarket.com`, `ws-live-data.polymarket.com`. No `eval`/`exec`/`subprocess`/
  `os.system`, no base64 blobs, no key exfiltration, no writes outside its own folder.
- The Moon Dev docs zip: 33 markdown/text files, no executables.
- Verdict: clean. Bugs, yes (below); malice, no.

### Five bugs found by probing the REAL feeds (fixed; strategy constants untouched)
| # | bug | effect if left | fix |
|---|---|---|---|
| 1 | liquidation parser looked for keys `data`/`rows`; the live feed nests rows under `liquidations` | 200 OK but "0 rows" forever → the cascade pull can never fire → the maker keeps quoting into the exact moments it should hide | parser accepts `liquidations` too |
| 2 | imbalance feed is a dict (`by_coin.BTC.buy_volume_usd / sell_volume_usd`); parser iterated it as a list of rows | `AttributeError` every 90 s, imbalance pull never fires | dict-aware parser, ratio = buy / (buy+sell) |
| 3 | `ClobClient.cancel(order_id=…)` does not exist in `py_clob_client_v2` (it is `cancel_orders([id])`) | in LIVE a resting quote could never be pulled — stuck bids | `cancel_orders([oid])`, errors logged |
| 4 | the POST /order RESPONSE dict was stored as the "order id" | every later cancel would send a dict as an id → fail | `order_id_of(resp)` reads `orderID` |
| 5 | daily stop (−$20) wrote the KILL file on DRY (paper) P&L | a paper loss would flat the strategy and, with the S9 watcher pattern, could restart-loop (the exact storm S1 had on 09-20) | daily stop only arms when `LIVE_TRADING=1`; DRY logs "level reached, no KILL" |

Also noted (not changed): the report's "Best version … adverse/gross 157.70" prints the
adverse-loss dollars where the ratio (0.68) belongs. Cosmetic.

Feed probes (real key, 2026-09-23): `/api/all_liquidations/10m.json` → 200 in ~110 ms, 144–200
rows/10 min across binance/bybit/okx/hyperliquid, BTC usually 0–2 of them.
`/api/imbalance/1h.json` → 200, BTC block present. No key → 401 (so a dead key is loud, not
silent). `X-API-Key` header auth.

The 12 strategy constants, verified unchanged after patching: QUOTE_PRICE 0.45, QUOTE_SHARES 50,
QUOTE_START_S 30, QUOTE_END_FRAC 0.60, BAND (0.45, 0.55), INV_GRACE_S 20, EXIT_MODE cut20,
LIQ_CASCADE_USD 1,500,000, SPOT_MOVE_BPS 6.0, IMB_EXTREME 0.85, NET_CAP_5M 30, DAILY_STOP 20.

### Deploy (DRY) — run mechanism
1. `~/s3_feefarm/` created on twapvm; all package files + `docs/` (Moon Dev docs zip, the
   knowledge MD, 33 extracted docs). md5 of every code file = local copy.
2. `.env` (chmod 600) contains ONLY `MOONDEV_API_KEY=…` and `LIVE_TRADING=0`. There are NO
   Polymarket keys in it, so the process physically cannot place an order even if every gate
   were flipped. Adding account-2 keys is a deliberate future step (the I-step).
3. `deploy/s3-maker.service` rewritten for this VM (user, absolute paths, `Restart=always`
   is safe here because KILL makes it flat instead of exiting; stdout/stderr appended to
   `s3-maker.service.log`). Enabled + started 2026-09-23 21:33Z.
4. Startup log: "liq feed shape: 144 rows", "RTDS connected; subscribing chainlink-spot",
   0 tracebacks. Quoting begins at the next quiet window (00:00–12:00 UTC, 16:00–18:00 UTC,
   all weekend); outside those hours the maker is intentionally idle.
5. Disk: BME capture is the big writer (~2.2 GB/day); S3 writes a few KB/h. The 50 GB disk
   from Part #21 of the #1 record covers it.

### Telegram wiring (in the shared `s1_monitor.py`, restarted 21:36Z, quiet start confirmed)
Hourly status now carries an S3 line with the plain-English description:
"S3 Fee-Farm Two-Sided Maker: rests a buy at 0.45 on BOTH sides of each BTC 5m/15m round
during quiet hours, keeps completed pairs (0.90 → 1.00), cuts stray legs after 20 s, and pulls
its quotes on liquidation cascades / spot jumps (Moon Dev feed). DRY: fills are simulated from
the public tape." followed by the hour's counts: quotes, rounds with fills, pairs, pulls by
trigger, inventory exits, errors, key status.

Urgent pings (dedup 6 h, global circuit breaker still applies):
| trigger | why it matters | class |
|---|---|---|
| Moon Dev 401/403 (hourly key probe or in-log) | key expired → both pull triggers blind → maker quotes into cascades | 🚨 once / 6 h |
| Moon Dev 429 or 5xx / timeouts | feed degraded; pulls late | ⚠ dedup |
| liq feed returns 0 rows for a full hour | data mismatch (the exact bug #1 symptom) | ⚠ |
| `DAILY STOP` / KILL file appears or disappears | strategy stopped itself, or someone re-armed it | 🚨 |
| `FILL REJECTED` (band gate) storm | fills being faked outside 0.45–0.55 | 🚨 dedup |
| any `(LIVE)` order line | real money moved | 🟢 every time |
| S3 `.env` `LIVE_TRADING` flips | someone armed live mode | 🚨 |
| > 10 errors / h, RTDS reconnect storms | plumbing broken | ⚠ |
| rounds-with-fills per quiet hour outside 2–12 | outside the backtest's natural band (M5 lesson: never narrower than the observed swing) | ⚠ |
| service inactive | already covered by the existing service check (`s3-maker` added to SERVICES) | 🚨 |

Offline tests before shipping: digest-only lines → 0 pings; LIVE quote + gate violation + key
death → 3 pings; probe 401 → 1 ping; LIVE flip → 1 ping; hourly status contains the S3 line.
On the VM after restart: only "monitor started" logged; key probe state = "moondev key OK
(129 liq rows in 10 min)"; 0 Telegram errors; working copy = reference copy.

### What to expect, and what DRY can and cannot prove
- **Can prove (plumbing):** feeds parse, pulls fire on real cascades with real timestamps,
  quiet-session gate honours the clock, quote cadence and rounds-with-fills per hour sit inside
  the 2–12 band, the band gate rejects touch-fills, the 20 s cut fires, daily stop logic, no
  error storms, key expiry gets caught within an hour.
- **Cannot prove (fill realism):** whether a real resting 0.45 bid actually gets filled on
  BOTH sides in a quiet round, or only on the side that is about to lose. DRY uses the same
  optimistic tape rule as the backtest, so a green DRY P&L is NOT evidence of edge. Only real
  resting orders (the I-step, tiny size, account 2) can answer that, and this project's
  history says the answer has been "no" four times.
- **Timeline:** the package asks for 3 quiet-hour days of DRY. First quiet window opens
  2026-09-24 00:00 UTC. Judge at ~2026-09-27: pull-trigger counts vs the number of real
  cascades in the Moon Dev feed, fills/h band, error count, and whether paired fills are even
  simulated at the backtest's 18.7 % rate.
- **Moon Dev RBI mapping:** R (research) = the package's playbook; B (backtest) = the 32-way
  sweep, already done and mostly negative; I (implement) = this DRY run → then the I-step with
  real orders. Nothing here is a "verified strategy" yet.

### Files touched in this Part
- VM: `~/s3_feefarm/*` (new), `~/s1_harness/s1_monitor.py` + reference copy (S3 checks).
- Local: this folder's `btc-5min-twap-lockin/s3_feefarm/{s3_maker.py, deploy/s3-maker.service}`
  patched; `#1 …/s1_harness/s1_monitor.py` extended; this record created.
- Memory: `project_s3_feefarm.md` (new), `reference_moondev_api.md` (feed shapes, key facts).
- GitHub: `s3_feefarm/` (code, docs, report, rows CSV — no `.env`, no logs, no `_cache`),
  `s1_harness/s1_monitor.py`, this record.

---

## Part #2 — First quiet session was a zombie: main loop died at 00:07Z, fixed 06:32Z (2026-09-24)

### Context
S3 entered its first quiet session at 00:00 UTC. While preparing strategy #6 (which reuses
this code) I checked what S3 had actually done in six quiet hours.

### Data
| what | value |
|---|---|
| quotes | 2 (both sides of ONE round, 00:05:54Z) |
| rounds settled (`n` in stats) | 0 |
| `KeyError('live_quotes')` in the log | 52 (33 in the Moon Dev loop, 19 in the RTDS loop) |
| FILL REJECTED lines | 6, all 00:07:00Z, `mid 0.42 outside band` |
| pulls counted in stats | LIQ 10, SPOT_MOVE 2 — but 0 `PULL BOTH` log lines and an empty pulls CSV |
| service state | active the whole time |
| monitor pings | "0 rounds-with-fills" band warnings at 00:00 and 06:00, error-storm and reconnect-storm warnings at 01:44–01:51; nothing said "the loop is dead" |

### Cause
`do_pull()` wrote its CSV line with `r["live_quotes"]`, a field no round state ever had, so
every pull raised after the lock and the stats counter were set but before the quotes were
marked PULLED and before `PULL BOTH` was logged. At 00:07Z the mid left the band, the band-exit
pull ran inside `tick_loop`, which had no exception guard, and the loop task ended. Systemd
only watches the process, so the service stayed "active" with no quotes and no settlements.
The 6 FILL REJECTED lines are the tape loop still seeing the never-pulled quote as resting.
Second suspect, the volatility gate, was measured and cleared: Chainlink ticks every 1.0 s,
5-min sigma 5.3 bps vs the 10.5 bps threshold.

### Fixes (shared with S6; constants untouched, verified byte-identical)
1. Pull CSV counts resting placed quotes (no phantom field).
2. `tick_loop` wrapped in try/except (`tick error …`), heartbeat `tick_ts` in `s3_stats.json`
   every 60 s; the monitor alerts when it is older than 5 min while the service is active.
3. One cascade fires one pull (the feed keeps a cascade for 10 min, so the same event had
   re-pulled every poll after each 60 s lock; the 10 "LIQ" pulls were one cascade).
4. `SKIP <series> <round>: <reasons>` logged once per round, so flat periods are explained;
   the hourly status shows the top reasons.
5. Daily stop is a gate only when `LIVE_TRADING=1` (simulated P&L must not flatten a DRY run).

### Run mechanism
Fixed `s3_maker.py` shipped (md5 d6f4690e), stats snapshot kept as
`s3_stats.before-fix-20260924.json`, service restarted 06:32Z. Result: quoted the 15m round at
06:33:10Z and the 5m round at 06:36:27Z; both pulled seconds later by `BAND_EXIT mid=0.45`
(the mid sits right on the band edge in these rounds; expect short quote lives and frequent
band pulls — that is the strategy's rule, not a bug). Monitor restarted 06:39Z on the generic
maker checker (S3 + S6), quiet start.

### Lesson (saved to memory)
"Service active" is not "strategy alive". Every asyncio strategy loop needs a crash guard and
a heartbeat the monitor checks, and every gate skip must be visible in the log.
