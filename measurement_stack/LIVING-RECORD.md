# arena-ai-copy btc-5-minute workspace — living record

Every run or change to the measurement stack is logged here as a Part. Plain language, no
claims without data, no keys or addresses ever. Nothing in this stack places an order.

**What this is, in one breath:** the other strategies try to make money. This stack only
measures. It answers four questions with data: (1) which source tells the truth about who
won a round, (2) whether our own calculator can reproduce the US venue's settlement,
(3) how weather markets react when the official forecast changes, (4) whether the venue
quietly changed its rules. Then it writes one report a day.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder
`~/arena-ai-copy btc-5-minute workspace/measurement_stack/` (shortcut `~/mstack`).
Local source: this folder's `measurement_stack/`. GitHub:
`georgeskarlatos89-byte/btc-5min-twap-lockin` → `measurement_stack/`.

---

## Part #1 — Workspace study, reconciliation, build of all six deliverables (2026-09-27)

### Context
The user handed over the research workspace (400 files, 169 MB) and `HANDOFF-PROMPT.md`:
read everything, reconcile it with the deployed code, then build the measurement and
backtest stack on the current VPS, 7-day backtest first.

### What was read
All 400 files. 230 are byte-identical to the repo (Polymarket doc mirrors, Moon Dev docs, nine
index files). The new material: `MASTER-RUNBOOK.md`, `STRATEGY-STACK-RERANKED.md`, `app_lab`
(US venue playbook, latency baseline, decompiled-app analysis, two reference recorders, 13
minutes of recorded data), `maker_lab` (history tools, 76,670 + 27,088 round corpora, quoting
simulator), `regime_watch`, `ui_lab` (UI vs API findings, DevTools dumps), `s1_harness` (an
older copy of what production runs), `polymarket secrets` (a news investigation plus a claim
audit), five archives (the decompiled Android app was listed, not modified), six uploads.
Full table of what exists where, ten disagreements and eleven code defects:
`measurement_stack/RECONCILIATION-AND-BUILD-ORDER.md`.

### The headline result: the 7-day backtest (deliverable 1)
Window 2026-09-20 12:15 → 2026-09-27 12:15 UTC, 2,688 rounds (2,016 × 5m, 672 × 15m), every
one verified on Polygon (`payoutNumerators`, 0 RPC errors, 123 s).

**Who tells the truth about the winner (vs chain):**

| label source | rounds | wrong | wrong % |
|---|---|---|---|
| public `past-results` outcome | 2,510 | 253 | 10.08 |
| public gamma outcome | 2,680 | 0 | 0 |
| the label production saw at settle time | 2,480 | 0 | 0 |

`past-results` by boundary gap: under 1 bp 36.6 % wrong, 1–2 bp 26.3 %, 2–4 bp 13.2 %,
4–8 bp 3.7 %, over 8 bp 0.4 %. The workspace's claim is confirmed and is worse than stated
in the 2–4 bp band.

**Why it is wrong (new finding, settles disagreements D2 and D3):** on 364 of 364 rounds the
`openPrice` / `closePrice` that `past-results` serves equal our recorded Chainlink **SPOT**
tick at the boundary, to half a cent. They are not the TWAP values the workspace assumed.
So its `outcome` is "spot at the end vs spot at the start", while the market pays on the
60-second TWAP. On our own 1 Hz ticks, `TWAP60(end) >= TWAP60(start)` reproduced the chain
on **381 of 381** rounds (281 × 5m, 100 × 15m), razor rounds included.

**Which settlement rule is real (our recorded values, 2,480 full-coverage rounds):**

| rule | accuracy vs chain |
|---|---|
| TWAP60 end value vs TWAP60 open | 99.35 % (100 % above 1 bp; the 16 misses are all under 1 bp and come from 2-decimal rounding in the observer file) |
| last-60 s average vs open | 95.04 % |
| TWAP60 end vs SPOT open | 93.02 % |
| full-round TWAP average vs open (what S1 and the maker lab assume) | 84.84 % |

Production's living record #1 was right, the workspace's S1 and maker-lab premise is wrong.

**S1 as actually deployed (133 real dry signals, the asks the bot saw):**

| view | trades | wins | win rate | break-even | net after fees |
|---|---|---|---|---|---|
| with the standing rules ($10, 1/round, 3/hour, $20 daily stop), chain labels | 78 | 59 | 75.6 % | 82.6 % | **−$56.57** |
| same, scored on public `past-results` labels | 46 | 25 | 54.4 % | 82.6 % | −$165.51 |
| every signal, no caps, chain labels | 133 | 111 | 83.5 % | 82.6 % | +$15.14 |
| every signal, no caps, public labels | 127 | 76 | 59.8 % | 82.6 % | −$369.78 |

Reading: S1 buys at an average price near 0.80, so it must win about 83 % of the time just
to pay the fee. It wins 83.5 %. That is break-even before slippage, not an edge. The $20 daily
stop made it worse (4 days stopped). A backtest on the public label would have been off by
21 percentage points and $109, with 33 of 133 signals mis-scored. Per session on chain labels:
Asia −$32.43, US +$17.99, off-hours −$5.28, weekend −$36.86. S1 stays hard-blocked.

Other strategies hold nothing to resolution yet (S3 3 fills all exited, S46 0, S10 0, S2 0
candidates), so no label can change their result. The S1 observer rows were excluded from
scoring: they store the Up token's ask at round END (0.001, 1.00), and scoring them produced
a fictitious +$67,243 on the first run.

### Deliverables 2–6: what was built and how it behaves
| # | deliverable | state | first data |
|---|---|---|---|
| 2 | US round recorder + BRTI proxy (`ms-us-recorder`) | running | 4 exchange books live; venue settlement arrives 0.14–0.19 s after the boundary; first full comparison: proxy gap −5.0 bps → Down, venue settled Down → OK. 1 of 200 comparisons |
| 3 | NWS watcher + bucket-sum scanner (`ms-nws-watch`) | running | all 5 stations parsed from the venue's rule text (KNYC, KMIA, KSFO, KLAX, KMDW); 60 buckets quoted; today's executable ask-sums 1.05–1.15, tomorrow's 1.14–3.15; no buy-arb |
| 4 | Regime watch (`ms-regime-watch`) | running, baseline on 12 sources | soak test: 8 clean cycles across 3 rollovers. Caught one real docs change (Polymarket's 27 Sep changelog entry) |
| 5 | Mispricing monitors (`monitors/mispricing.py`) | run by the daily report | `.com` books are one-sided or empty 74 % (5m) and 78 % (15m) of the final minute, 16 % and 34 % of the minute before; cross-venue 15m mean price gap 1.3¢ on 3 shared rounds |
| 6 | Daily report + health (`ms-daily-report.timer` 07:20 UTC, `ms-health.timer` every 5 min) | running | first report written and sent as ONE Telegram message |
| + | History corpus tools + daily refresh (`ms-history-refresh.timer` 00:10 UTC) | installed | 1,000-round relabel test: 0 errors, 0 label changes; full 76,670-round relabel running |

### Corrections to the handoff's stated facts (from live data)
| handoff says | measured |
|---|---|
| weather buckets are $1°F | 2°F wide, six per city ("64 or below", "65 to 66", … "73 or above") |
| `.com` has a 1 h updown product | discontinued; 5m and 15m only |
| regime watch is deployed, upgrade it | never deployed on either VM |
| `GetMarketSettlement` returns the settlement | it omits the field when Down wins (zero is dropped) |
| `past-results` open/close are TWAP stream values | they are Chainlink SPOT values |
| latency "from East Africa" | the VPS is in the Netherlands: 139–401 ms per call |

### Defects fixed while productizing (reference code → stack)
Regime watch: tick-size field is dynamic per round and flipped the hash at every rollover
(the workspace's own false alarm); fetch errors were written into the hashed text (a timeout
became a "rule change"); the US sentinel re-alerted on every restart; snapshots collided
within one second; Telegram had no thread and no rate limit. US recorder: sessions not
aligned to the round grid; wrong open reference when joining late; dead second-witness
code; raw frames unbounded. Weather: forecast parser empty for 3 of 5 cities; malformed CSV
header; lag clock started at detection, not at the NWS update time.

### My own errors in this Part (logged, not hidden)
| # | error | evidence | fix |
|---|---|---|---|
| E1 | added a generator to a tuple in the tick study | first backtest crashed after the chain stage | list concatenation; re-run from cache |
| E2 | scored the observer rows as entry prices | +$67,243 "profit" | excluded from scoring, reason printed in the report |
| E3 | `pgrep -f` / `kill` matched my own shell three times | SSH session died with exit 255 | match by process name (`pkill -x`) or test for a file |
| E4 | shell heredocs halved backslashes in two patch scripts | `SyntaxError: unterminated string` | patch files written with the editor, not the shell |
| E5 | oracle-walk volatility from 1-second proxy changes | model 0.93 vs market 0.675 | 30-second changes |
| E6 | `.com` one-sided statistic read 0 % | recorder skips one-sided seconds | measured as missing seconds against expected |

### What to expect
- The report arrives once a day at 07:20 UTC. Alerts are rare by design (6 h dedup, 6 per hour cap).
- The BRTI proxy needs 200 comparisons: 96 fifteen-minute rounds a day, so about 2 days.
- Weather reaction lag needs forecast updates where the high actually changes: a few per city per day.
- Nothing here is a trading signal. No real orders until the user re-approves after seeing
  backtest and validation results.

### Files touched in Part #1
- VM: `~/arena-ai-copy btc-5-minute workspace/measurement_stack/*` (new), 3 services + 4 timers
  in `/etc/systemd/system/ms-*`. No existing service, file or strategy was modified. The shared
  `s1_monitor.py` was not touched.
- Local: `measurement_stack/` (code, deploy units, outputs), this record.
- Memory: `project_measurement_stack.md`, `reference_polymarket_label_sources.md`.
- GitHub: `measurement_stack/` (code, units, reports, backtest CSVs; no caches, no raw data).

---

## Part #2 - Moon Dev key audit, fleet health audit, four monitor fixes, hourly PDF report (2026-09-27)

### Context
The user asked three things in a row: is the Moon Dev key still working (a second key file
had been handed over), are all strategies running and collecting, and then: apply the four
fixes I had listed and add a well formatted PDF of the status report after each hourly status,
without altering any strategy.

### Moon Dev key audit (read-only, fingerprints only)
| finding | data |
|---|---|
| key file 26-09 vs key file 24-09 | identical key (same sha256 prefix), so nothing to refresh |
| twapvm | 5 `.env` files carry it (S3, S46, S10, S10 calibration, retired S6), all chmod 600 |
| polyvps | harvester `.env` carries it; an old 24-hour `moonstream_` key sits unused in `~/.env` |
| live probe | 6 of 6 endpoints 200 with valid JSON on both servers; 40 of 40 consecutive polls valid on twapvm |
| errors today | key rejected 0, rate-limited 0, truncated responses 2 to 3 per strategy (retried 2 s later) |

My error E7: the 40-poll burst on polyvps picked the expired key and drew forty 429 answers.
Checked immediately after: the valid key still returned 200 from that server and the
harvester never stopped writing.

### Fleet audit (read-only)
All 14 services active, no failed units, 169 GB free. Every strategy was writing data within
its expected interval. Three misleading things in the Telegram messages, and four strategies
that run but collect almost nothing (S3 3 fills in 435 rounds, S46 0 fills in 552 quotes,
S2 0 candidates, S10 0 fills): those are strategy rules working as written, left untouched.
My error E8: my audit first reported S5 as stale because my file pattern missed its `.gz`
stream; the file was growing at about 8 KB per second.

### The four fixes (monitor only, no strategy file changed)
| # | problem | fix | verified by |
|---|---|---|---|
| 1 | "s2-collect is activating" alert 3 times in 13 h: S2 restarts 10 s after each 60-minute session and the monitor looked inside that gap | a service must be down on two consecutive checks (30 s apart) | simulated states [activating, active, activating, activating, active] gave alerts [no, no, no, YES, no] |
| 2 | hourly status cut at 4,000 characters: S46 ended mid-sentence, S5 and S10 never appeared | status is built as sections and sent in as many messages as needed | simulation: 2 messages of 3,210 and 2,709 characters, all 9 strategies, last line complete |
| 3 | S46 "all-time rounds 0" while its log held 438 settled rounds (its counter only moves on a fill) | for the paired maker the monitor counts settled rounds in the log | now reads "438 settled (0 with a fill)" |
| 4 | BME reached 7 of 7 days but the score was 6 days old | see below | running |

Deploy mechanism: pulled the LIVE monitor from the VPS (it is shared with another session,
md5 5febedce), patched that copy, checked the live md5 again right before installing, kept
`s1_monitor.py.pre-pdf-20260927` as backup, restarted only `s1-monitor`. Restart counters of
every strategy service were identical before and after.

### Hourly PDF report (new)
`measurement_stack/report/status_pdf.py`, own virtual environment with reportlab. After the
hourly messages the monitor writes `state/status_latest.json` and starts the PDF job detached,
so a PDF problem can never block the monitor. Contents: four summary tiles (services running,
disk free, errors, alerts), a services table (state, running for, restarts, memory), one card
per strategy with its plain description and labelled rows, the measurement stack, then errors
and alerts. Routine feed reconnects are listed separately from real errors. PDFs are kept in
`reports/status/` for 14 days. Test: a PDF of today's latest status was generated and
delivered to the Telegram thread (HTTP 200, 56 KB, 4 pages).

### BME 7-day scoring
The original scorer keeps one counter entry per millisecond and hash in memory. On the full
week (about 600 million events) it was stopped by the 1.3 GB memory cap I had set after four
minutes. The cap did its job: no other service was affected. The original file is unchanged.
A streaming version (`measurement_stack/bme_scoring/bme_score_stream.py`) computes the same
signals in one pass at about 40 MB and adds what the gate actually asks for signal S3: the
post-fee expected value per share. Result: see Part #3.

### Part #2 addendum - first automatic report, relabel result, scoring status (14:00-14:30 UTC)
| item | result |
|---|---|
| 14:00 UTC hourly status | sent automatically as 2 messages (6,077 characters in total), PDF `STATUS-2026-09-27-1400.pdf` delivered (HTTP 200, 78 KB) |
| full 5m chain relabel | 76,663 of 76,670 rounds verified on Polygon, 0 labels differed from gamma, 0 RPC errors; the 7 others never resolved on-chain |
| 15m chain relabel | started automatically, running at lowest priority |
| recorders during my heavy jobs | all writing, S1 observer coverage 99-100 %, 0 strategy restarts, BME reconnects 4 per hour (same as before) |

BME scoring took four attempts. (1) original scorer: out of memory at the 1.3 GB cap.
(2) streaming Python with Python's gzip reader: about 40,000 rows per second, six hours.
(3) window sweep bug of mine (E9): every open key re-scanned once per second of data.
(4) current: `run_score_7d.sh` = one `gzip | grep + mawk` pass per file, Python only for the
statistics, lowest priority, 1.2 GB memory cap, result announced on Telegram when done.
The server itself is the limit: two slow cores, and another session was running a headless
browser at 400 % CPU at the same time (load average up to 20, CPU pressure above 90 %).

Two things the first hour of the new reporting exposed:
- **S8 says S3 is blocked, S3 keeps quoting.** S8 switched to the volatility override at
  13:43 UTC and lists S3 as blocked. S3 still placed quotes at 13:50 and 14:00. Both are DRY, so
  no money is involved, but the S3 hook into S8 is not doing its job. Not changed by me
  (strategy code, another session's work).
- **Restarting the monitor causes one false S5 alert.** The monitor reads S5's compressed
  stream incrementally; after a restart it must re-read the day's file, and for about five
  minutes it believes S5 is silent. S5 itself never stopped (2,485 Kraken messages that hour).

---

## Part #3 - Why S3 kept quoting while S8 said "blocked" (read-only investigation, 2026-09-27)

### Context
The user asked to find out why, without changing anything, and whether another strategy is involved.

### Finding
No other strategy is involved and the hook did not fail. S3 and the S8 daemon use the same
rule (block makers above 10.5 bps) but two different volatility numbers.

| | S8 daemon (what the status shows) | S3 (what actually gates its quotes) |
|---|---|---|
| how S3 is connected | writes `s8_state.json` every 5 s | imports `s8_router` as a library and calls `evaluate_strategy_gate("S3", now, current_sigma=sigma5m_bps())`; it never reads the state file |
| price source | Kraken 1-minute closes | Chainlink spot, 1 tick per second |
| window | trailing 1 hour | last 600 ticks (10 minutes) |
| method | standard deviation of 5-minute returns | standard deviation of 1-second changes, scaled by the square root of 300 |

Replayed minute by minute for 13:20-14:40 UTC from recorded data (81 minutes):

| | S3's number | S8 daemon's number |
|---|---|---|
| highest value | 7.74 bps | 11.86 bps |
| minutes above 10.5 bps | 0 | 49 (13:38 to 14:26) |

So for 49 minutes the daemon said "S3 blocked" while S3, asking the same library with its own
number, was told "allowed" every time. S3's method is structurally lower: it assumes each
second is independent, so a steady drift over minutes barely registers. In this period it
never came within 2.7 bps of the threshold.

### Who consults S8 at all
Only S3. S1 trader, S2, S46, S5 and S10 neither import the router nor read its state file, so
the "allowed / blocked" lists in the status describe S8's opinion, not what those programs do.

### Not changed
Nothing. Both programs are DRY, no money involved. The decision is the user's: either S3
reads the daemon's state file (one number for everybody), or the daemon's number is declared
informational. The monitor's alert text ("the S3 hook is not active (import failed?)") guesses
the wrong cause; the import works.

## Part #4 - Ten-day log audit, 2026-09-27 14:31 UTC to 2026-10-07 10:30 UTC (read-only)

Everything below comes from the logs, CSVs and state files on twapvm (audit script
`C:\g\tenday_audit.py`, output saved in the session). No strategy was changed. Times are UTC.

### The short version
- All 14 services are up. Nothing is failed. Disk: 108 GB free of 193 (44 % used).
- Every strategy is still DRY (no money). The fleet collected every single round, every day
  (384 rounds a day, 10 days in a row). Three things interrupted it, all explained below:
  a 92-minute VM stop on Sep 27, a 5-second restart of everything on Sep 30 (automatic Ubuntu
  update of systemd), and Polymarket US's own maintenance windows on Oct 1 and Oct 5.
- Moon Dev key: WORKING. Same key (fingerprint 845329c77e) in 8 `.env` files, three endpoints
  answered 200 in 58-101 ms today, 0 auth errors and 0 rate-limit errors in 10 days. The only
  errors are 1-8 cut-off answers or timeouts per strategy per day, retried automatically.
- The BME 7-day scoring from Sep 27 NEVER FINISHED: the VM stop at 16:16 killed it after one
  file. Relaunched today 10:31 on Sep 30 - Oct 6 (unit `bme-score-7d-oct`, Nice 19, 1.5 GB cap,
  ~10-12 h); it will post one Telegram message when done.
- One finding that changes a verdict: the US proxy "NOT TRUSTWORTHY" verdict is partly an
  artefact. From Oct 1 12:00 to Oct 2, and again on Oct 5, the US venue's websocket sent a
  settlement of exactly 0.0 (67 rounds). Real settlements are 0.99 / 0.01. The recorder read
  0.0 as "Down"; the HTTP witness said Up (1.0) on 26 of them, and so did our proxy. Scored
  against the HTTP witness the proxy agrees 97.78 % overall and 98.69 % on clear rounds
  (|gap| >= 2 bps), instead of 95.49 % / 96.3 %. Still below the 99 % bar, but no longer "wrong
  on clear rounds" by a wide margin.

### What the hourly PDFs and the daily reports are
- Hourly PDF (`STATUS-<date>-<HH>00.pdf`, 237 sent, 24 per full day, every one HTTP 200): the
  same text as the hourly Telegram status, laid out as tiles (services up/down, disk, error
  count, alert count), one card per strategy with its counters for the hour, a measurement
  section, then "errors" (real) separated from "routine reconnects" (feeds dropping and
  reconnecting, which is normal here), and an alert table. It is a snapshot of ONE hour.
- Daily report (`DAILY-REPORT-<date>.md`, one per day 09-27 to 10-07, 07:20 each morning): the
  7-day S1 backtest re-run on chain labels, the public-label error count, the US proxy
  validation score, the weather bucket-sum note, rule changes seen by the regime watch, and
  the number of alerts sent. It is a rolling 7-day view, so the S1 numbers move every day.
  Reading of the series: S1 on the trailing 7 days went -$56.57 (09-27) -> -$27.06 (10-06) ->
  +$1.03 (10-07) after fees, always within a few dollars of break-even at 85-86 % wins. That is
  the same "no edge" answer three different ways, not a trend.

### Day by day

**Sat 27 Sep (from 14:31).** 14:42 the full on-chain relabel finished: 76,663 of 76,670 5-minute
rounds verified on Polygon, 0 labels differed from gamma; the 15-minute corpus likewise.
16:16 the VM was STOPPED (last journal line 16:16:42) and booted again 17:48:28 - 92 minutes
dark. It came back with 4 cores and 16 GB RAM (it had 2 slow cores before), which is what a
GCP machine-type change looks like; the logs do not say who did it. The monitor fired the
expected "rounds.csv frozen 98 min", "trader silent 93 min", two "heartbeat 92 min old"
alerts at 17:48 and everything restarted by itself. Casualty: the BME 7-day score job (a
transient unit) died after finishing only the Sep 21 file. 22:42 / 22:53 the 15-minute rule
sentinel fired twice (see "sentinel flaps" below). All strategies otherwise normal: S3 202
quotes, 2 DRY fills; S46 202 quotes, 0 fills; S10 armed 24 times, 0 fills; S2 24 sessions /
164k ticks; S5 2.3 GB.

**Sun 28 Sep.** Quiet. S1 24 dry signals, 20 wins. S3 462 quotes, 1 fill, 0 pairs. S46 508
quotes, 73 cascades seen, 0 fills. S10 armed 42 times (Sunday vol spikes), 0 fills. S2 170k
ticks, 4 "ask ceiling" rejections. S5 4.0 GB. S8 flipped ASIA <-> US_VOL_OVERRIDE 14 times
(the trailing-hour sigma hovered at 10.5-10.7 bps, right on the 10.5 threshold). 00:44 / 00:54
the 15m sentinel fired again. Fleet alerts 57, of which 42 are "S10 armed" pings.

**Mon 29 Sep.** S1 17 signals, 11 wins (worst day). S3 1 fill. S10 armed 14. 14:57 S9 got a
burst of HTTP 403 on its book fetch (one hour, then clean). 18:00 the US recorder's first
proxy-vs-venue divergence alert (1h round, gap -3.09 bps). Incentives docs changed
("Deposit $10, receive $25" from Oct 1) - noted, nothing to do.

**Tue 30 Sep.** 06:54:41 Ubuntu's unattended-upgrade installed systemd 255.4-1ubuntu8.17 and
systemd re-executed itself, which stopped and restarted every service in ~5 s (this is the
"all services restarted 06:54:56" in the audit, NOT a reboot; uptime is unbroken since Sep
27). Monitor fired "monitor online" and a 3-minute S5 stale pair, then normal. 05:21 weather:
MDW "68 or below" bucket still bid 0.47 after the station had already recorded 69.8 F
(a stale-bid mispricing, logged). 12:41 weather SFO six asks summed to 0.980. 15:37 S10's
first DRY lone-leg unwind: Up 10 sh bought 0.45, sold 0.35, -$1.18. Polymarket docs added an
"equity TWAP" section. S3 0 fills, S46 0 fills.

**Wed 1 Oct - the messy day.** Polymarket US went into maintenance. Regime watch saw the
round types vanish 10:08, return 10:28, vanish 11:49, return 12:09 (4 CRITICAL alerts); the US
recorder saw no frames for 471 s at 11:10; 9 rounds got NO-SETTLEMENT. From 12:00 onward the
venue websocket sent settlement 0.0 on 52 rounds (see short version) - this produced the
28 "DIVERGENCE" rounds and 23 divergence alerts between 13:45 and 23:00, all of the form "proxy
says Up, venue says Down", which were false: the HTTP witness said Up too. 08:11 weather SFO
asks summed to 0.770 (all six quoted, oldest quote 8 s) - the cheapest full set seen in the
period; it was 0.83 five minutes later. 08:15 S10 fired a DRY pair-arb (Up 0.58 + Down 0.34 =
$0.9528 after fee, expect +$0.94). 12:25 S8 macro blackout for Initial Jobless Claims. 16:00
the monitor noted S3 and S46 placed 0 quotes in a quiet hour (S8 US block). S2 had 23
sessions (one lost to the restart). Stack alerts: 34 sent, 22 deduplicated.

**Thu 2 Oct.** The 0.0 settlements continued until 10:00 (10 more 1h rounds flagged, same
false pattern), then the feed was normal again. 04:20 S2 produced its FIRST hypothetical
candidate in 10 days: round 1790914800, Down, displacement -6.5 bps, model fair 0.81,
research-only, no order. 12:25 S8 blackout for Non-Farm Payrolls. 13:51 the US legal
rulebook PDF changed (790 KB -> 827 KB, new hash; content not diffed by the watcher). S46 saw
141 cascades (most of the period), still 0 fills. S10 armed 34 times. S5 4.15 GB (largest
day). Daily report counted 39 alerts for the previous day.

**Fri 3 Oct.** Quietest day: S1 3 signals, S3 0 S8-gate skips (the gate never blocked),
S46 0 cascades, S10 armed 10. 21:18 / 21:28 the 5-minute sentinel fired twice: for ten
minutes the 5m market carried a `clob_rewards.rates` entry (asset 0xc011..., 0.001 per day)
and then went back to null. That is a market-maker reward being switched on and off, not a
fee change. Fleet alerts only 17.

**Sat 4 Oct.** S1 8 signals, 6 wins. 13:48 weather NYC asks summed 0.980; 21:00 SFO 0.970.
S10 armed 25. BME reconnect storms twice (code 1013 "slow consumer", see below). Nothing else.

**Sun 5 Oct.** S10 armed 57 times (most of the period), 1 DRY fill, 1 unwind at 08:21
(Down 2 sh, -$0.21). 06:57 / 07:07 and 14:43 / 14:53 the 15m sentinel fired twice more.
15:54 - 18:57 Polymarket US had another outage: round types NONE-DETECTED 15:54, back 16:15,
gone 16:25, back 18:57; recorder saw no frames for 301 s at 16:05; 3 rounds settled 0.0 and
8 settled 1.0 (the venue briefly sent 1.0 / 0.0 instead of 0.99 / 0.01). 20:38 a REAL change:
the US venue added daily rounds - `btc-updown-1d` and `cpc-btc-updown-1d` now exist alongside
15m and 1h. Our recorder ignores them (it subscribes to 15m and 1h only). 13:55 S8 blackout
for ISM Services PMI. Weather SFO asks summed 0.950 at 14:53 (lowest of the week apart from
Oct 1). Fleet alerts 73, 57 of them "S10 armed".

**Mon 6 Oct.** S10 3 DRY fills: 14:25 Up filled 0.45, chased Down 0.45 at 14:26 (combined
$0.917, pair completed); 15:30 Up "0 sh" fill (size rounded to zero) unwound at 15:31 for
-$0.01; 16:00 Down 20 sh filled, no partner, unwound. S10 stats now: pairs completed 2, won 2,
DRY P&L +$1.15. S3 0 fills. 11:07 weather MIA asks 0.980. Moon Dev cut-off answers were the
most of the period (S3 4, S46 4, S10 8), all retried, key fine. Polymarket docs added
"Migrate from RTDS to PolyBolt" - WATCH THIS: every recorder here reads RTDS; if RTDS is
retired the whole fleet goes blind. Nothing announced as a date yet.

**Tue 7 Oct (to 10:30).** 02:01 S8 vol override at 13.48 bps (the biggest spike of the
period; S3 kept quoting with its own lower number, as explained in Part #3). 02:30 one real
divergence (15m, proxy Down by 16 bps, venue Up - the HTTP witness agreed with the venue, so
this one is a genuine proxy miss). 05:46 weather NYC asks 0.980. 06:55 S5 restarted - this is
its DAILY restart (it has done so at the same minute every day; 7 restarts = 7 days, by
design). 10:00 PDF sent (20 errors, 2 alerts in the hour). 10:31 BME scoring relaunched.

### Per-strategy totals for the ten days
| strategy | what it did | result |
|---|---|---|
| S1 observer + trader (DRY, hard-blocked) | 384 rounds/day recorded, 10/10 days complete; 163 dry signals, 137 wins (84 %) | ledger n=294, +$12.89 before fees = break-even; stays blocked |
| S3 fee-farm maker (DRY) | 202-462 quotes/day, 142-387 pulls/day, S8 skips 120-140 on weekdays | 7 DRY fills in 10 days, 0 pairs, +$5.35; the backtest expected 2-12 fills per HOUR - the fill model is the problem, not the gate |
| S46 cascade + coin-flip (DRY) | 202-508 quotes/day, 433 cascades seen | 0 fills, 0 legs held, S4 0 triggers; kill test cannot run with 0 legs |
| S10 vol-event box (DRY) | armed 300 times (calendar 21, vol 292), 7 DRY fills, 2 pair-arbs | 2 pairs completed, both won, +$1.15 DRY; 3 lone legs unwound for -$1.40 |
| S2 open-print recorder | 24 sessions/day, ~170k ticks/day | 1 candidate (Oct 2); 95 % of rounds rejected as "spot stale" by the 2 s gate (decision pending since Part #1) |
| S5 wick-fade recorder | 1.4-4.2 GB/day gz, 38 GB total | growing 195-255 MB/h; the monitor projects 64-84 GB per 14 days |
| S8 router | ~15-30 regime flips/day, 3 macro blackouts | only S3 listens; "S8 says blocked" alert still fires daily (known) |
| S9 whale coattails | ce25 wallet 5,992 follows, 25 % wins, EV -0.24 | gabigol / gabagool22 silent 16.5 days; nothing to copy |
| BME book recorder | 1.0-3.1 GB/day, 384/384 rounds in every integrity report | reconnect storms most days (code 1013 "slow consumer, send buffer full"): the recorder is too slow for the feed on a loaded box |
| Measurement stack | 1,154 US comparisons, 730 weather lag rows, 113 BUY-ARB scans, 20+ rule changes | see verdict note in short version |

### Open points for the user (nothing done without a go)
1. Disk: 108 GB free, consumption ~6.5 GB/day (S5 ~3.5 + BME ~3) -> about 16 days, i.e.
   around Oct 23. S5's review date in memory is Oct 10. Decide: stop/shorten S5, or add disk.
2. The "S10 armed" alert class is 60-80 % of all fleet alerts (24-57 a day). It is an event
   class that should be a counter in the hourly status. Monitor-only change, needs a go.
3. US recorder: ignore websocket settlements of exactly 0.0 / 1.0 and prefer the HTTP witness
   when the two disagree (a measurement-stack fix, not a strategy). Also subscribe to the new
   1-day rounds? Needs a go.
4. Sentinel-15m flaps (10 min apart, 4 pairs) are almost certainly the same reward-rate
   toggle proven on the 5m market on Oct 3; the snapshot folder only keeps the last 4
   states so the Sep 27 pair cannot be re-diffed. Keeping more snapshots would settle it.
5. RTDS -> PolyBolt migration page appeared in the docs on Oct 5. Every collector reads RTDS.
6. The still-open decisions from Parts #1-#3: S2 freshness gate, S3 reading S8's state file.

## Part #5 - OPEN-BOOK-MAKER (strategy #11): review, v2 rebuild, Phase A on existing data (2026-10-10)

The user handed `open_book_maker.zip` (two-sided fair-value maker for BTC 5m rounds; Phase A =
capture + simulate, Phase B = $10 live pilot). Read every file, tested a scratch copy against
the live market, found 12 errors (review file `REVIEW - open_book_maker - 2026-10-10.md` in
`…/arena-ai-btc-5-minute new strategies open maker/`), then on "Fix all" rebuilt it as
`open_book_maker_v2/` (changes in `CHANGES-v2.md`) and ran Phase A on data already on twapvm.

### What was wrong (short)
Fair model ~10x overconfident (said 0.03 % Up when the market said 37 %) and built on the
full-round-average rule instead of TWAP60 end vs start; fill model counted a 50 % fill every
second on an unchanged book (473 fills per 1,000 ticks, -$190 in one minute of real data);
no post-only check in the sim; quotes never consumed; chain verifier crashed on the sim's
own CSV; .env never loaded; live fills never reconciled (wrong client call); daily stop
dead; first failed market lookup cached for the whole round; sim and bot used different
prices and sizes; daily re-replay double-counted.

### v2 (all unit tests pass locally and on the VM)
Shared `fair_model.py` (end-TWAP rule, sigma from 10-s spot changes over 30 min, the last
60 s use the known part of the window); conservative fill model (fill only when the displayed
best bid falls through our price, quote consumed, 50 % queue probability); post-only in sim
and bot; tick 0.01; one size/cap from .env; sim CSV carries condition_id + outcome; dotenv;
TradeParams(maker_address); realized P&L across rounds; events?slug= lookup with retries;
dedup + gate report with bootstrap CI; `bme_to_replay.py` builds replay days from BME book
state + S2 ticks (Step 0 reuse, no new recorder). VM folder `~/#11 open-book maker/`
(no service).

### Phase A result on 2026-10-06..09 (1,141 rounds, chain audit 60/60)
Fair model calibrated and honest (Brier equal to the market early, better late). Strategy:
3,580 fills, **-$2,825**, return on notional **-7.95 %** (CI -10.2 to -5.6) vs the -5.5 %
public benchmark, adverse edge **-6.25 c per share**, every day, every time window and every
fair band negative; fills land on the winning side 43.8 % of the time. Gate FAIL on 4 of 5
criteria, no ITERATE window. Caveat: the sim counts only sweep-through fills (the toxic ones
by construction); benign touch fills would need to outnumber them ~4:1 at the full half-spread
to break even. Recommendation: do not fund Phase B; keep the fair model and pipeline.
Results: `open_book_maker_v2/results/` and `RESULTS - phase A on existing data … .md`.

### Part #5 addendum - LIVE at $10 per side (user decision, 2026-10-10 16:01 UTC)
The user read the Phase A result and said "run it live with $10 size, I don't trust this
data". Done, with the account-2 proxy path proven by S1's preflight (NL, 65.07 pUSD, creds
accepted): service `obm-maker.service` on twapvm, folder `~/#11 open-book maker/`, `.env`
copied file-to-file from `~/s1_harness/.env`, 20 shares per side, inventory cap 40, daily
stop $20, GTC post_only orders, kill file, Telegram via `notify.py`. Dry rehearsal and kill
drill passed before the flip. Incident in the first live round: the fill parser booked the
TAKER's trade record (140 Down shares) instead of our maker entry; paused with the kill file,
fixed at 16:08 (our fill = `maker_orders[]` entry with our funder; late fills booked into
realized), restarted clean. Real first-round result (outside the bot's ledger): 20 UP @0.51
+ 20 UP @0.12 bought seconds before a drop, round settled Down, -$12.60 (balance 65.07 ->
52.47) - the adverse-selection pattern of the sim, on round one. Round 16:10: 54 Up quotes,
0 fills; 16:15: a DOWN fill 20 @0.49, booked correctly. Details: `open_book_maker_v2/deploy/README-live.md`.

### Part #5 close - stopped for good (2026-10-10 17:07 UTC)
Live outcome in 25 minutes: 3 rounds with fills, 3 losses, every fill on the losing side
(16:05 40 Up -> Down, -$12.60; 16:15 20 Down -> Up, -$9.80; 16:20 40 Up + 20 Down -> Down,
-$10.20). Total -$32.60; account 65.07 -> 32.46 after the 16:20 payout arrived on its own.
The bot's $20 daily stop tripped at 16:24. User: "Stop it for good" -> KILL file set, service
`obm-maker` disabled, cancel_all confirmed, `.env` back to DRY_RUN=true. Verdict: the sim's
adverse-selection finding was right and, live, worse (-$10 per filled round vs -$1 simulated).
Strategy #11 is closed. Reusable: `fair_model.py` (calibrated), the chain-verified replay
pipeline, the account-2 live plumbing (py_clob_client_v2 sig3 + post_only + kill file +
Telegram) in `open_book_maker_v2/`.

**Part #5 summary file (2026-10-14):** the complete minute-by-minute live record (every CLOB fill, balance path 65.07 -> 32.46, outcomes verified on chain + Gamma) side by side with the Phase A backtest is in `open_book_maker_v2/SUMMARY - live test and backtest - 2026-10-10.md` (short-path copy: `C:\g\open_book_maker - live test and backtest summary - 2026-10-10.md`; VM `~/obm/`). Raw fills: `open_book_maker_v2/results/live_fills_from_clob_2026-10-10.csv`.

**Part #6 (2026-10-15) - arena VARIANT-EXPERIMENT-PLAN, steps 1-2 done, $0:** the arena workspace AI wrote a sim-only revival plan for #11 (V0-V7, gate R1-R10 with a gate_pass.json code lock, sunset clause). Assessment: `open_book_maker_v2/ASSESSMENT - arena variant experiment plan - 2026-10-15.md`. Step 1: the bundle shipped the broken v1 code; swapped to v2 (28 tests pass), re-zipped as `open_book_maker - bundle with v2 code synced - 2026-10-15.zip`. Step 2: replay corpus on twapvm `~/obm/replay/` grown from 4 days to 19 days (2026-09-26..10-14, 5,313 rounds, 508 MB). Steps 3-5 (R-gate + lock, V0+V7, V1-V6, sunset) await the owner.

**Part #7 (2026-10-15 11:13Z) - arena variant sweep RUN, every variant FAILED, #11 archived under the sunset clause, $0:** steps 3-5 of the VARIANT-EXPERIMENT-PLAN executed sim-only on twapvm (`~/obm/run_variants.py`, transient unit obm-variant-sweep, 72 min). Corpus 19 replay days 2026-09-26..10-14; grids fitted on 09-26..28, verdicts on the 16 test days 09-29..10-14 (4,580 rounds), 20 sh/side, cap 40. Instrument check: V0 on the Phase-A days reproduced the 2026-10-10 numbers exactly (1,141 rounds, 3,580 fills, -$2,825.0, -6.25c). Test-day edge per share: V0 -3.88c [-4.44,-3.33], V1 -2.54c (theta 0.03), V2 -3.38c (q0.4 k3), V3 -3.42c (hs 0.04), V4 -5.73c, V5 -3.81c, V6 -3.58c; return on cost -6.5% to -12.1%; worst day -$289 to -$1,157; V0 and V6 lost on 16 of 16 days. R1 passed everywhere, R2/R3/R5/R6 failed everywhere, R4 passed only for V1 as a mid-relative artefact (settlement still negative). No `gate_pass.json` written; the bot's R8 lock keeps LIVE refused. Chain audit 40/40 match in all 12 runs. Autopsy (V7, V0 test days, 12,928 fills): every counted fill is preceded by a 5.5-tick sweep in the prior second, the mid is -3.2c against us at +5 s and +30 s, -3.9c at settlement, worse later in the round (180-240 s: 8-tick sweep, -4.8c; 240-300 s: 17 ticks, -12.1c); break-even would need 7.3 benign touch fills per round (2.6 per counted bad fill) which the live test (6 of 6 fills adverse) never showed. Every pre-registered prediction held. Sunset clause applied: #11 archived permanently, no maker-at-fair variants on this book, `fair_model.py` to taker-side research only. Results: `open_book_maker_v2/VARIANT RESULTS - all 7 variants failed - 2026-10-15.md`, `open_book_maker_v2/variant_runs/` (VARIANT-RESULTS.md, variant_results.json, AUTOPSY.md x2, per-run gate_result/run_meta/maker_stats); raw sim CSVs on the VM only. Nothing live touched; polyvps untouched.

**Part #8 (2026-10-27 03:40Z) - paired-maker step-3 replay FAIL (19 days, every latency incl. instant) + v2-open-maker-inspect-order-book lab FAIL (34/34 rules), the resting-bid family is closed, $0:** (1) Paired maker (`~/pm`, repo `paired_maker/`): both-sides 20-share maker, window 60-240 s, pair cap 0.97, inv 40, replayed on the recorded book 2026-09-26..10-14 (5,430 rounds) with the queue rules, the measured chainlink lag 1.44 s and an order-latency model 0/0.05/0.1/0.2/1.0 s (0.1 s = measured from twapvm with tiny live orders 10-15). join: instant -0.20% CI [-3,514, +1,082] pairs 87.7% 9/19 days; 0.05 s -3.96%; **0.1 s -4.48% CI [-26,154, -21,879] pairs 83.8% 0/19 days**; 0.2 s -4.79%; 1.0 s -5.70%. model (fair-5c): instant -3.66%, 0.1 s -6.37% pairs 66.7%, 1.0 s -7.86%. Gate (CI low > 0 AND pairs >= 95%) FAILS everywhere, even the impossible 0 ms ceiling -> step-3 stop rule applied: no step-4 live queue test, no step-5 co-location, bot stays DRY-locked (LIVE needs a passing report file, none exists). The run died twice (10-15 20:23Z, 10-24 22:06Z) because twapvm killed user units on ssh logout (`Linger=no`), fixed 10-26 (`loginctl enable-linger`), last 3 days rerun with the same v1 model; a 3-day v2 cancel-clock sensitivity run (lats 0/0.02/0.05/0.1, days 09-28/10-03/10-09) started 10-27 03:45Z for the record only. (2) The user's notebook idea (25-10) built as `v2-open-maker-inspect-order-book` (repo folder = VM `~/v2-open-maker-inspect-order-book`, commit d4237bc): `book_reader.py` = the browser "inspect" as a program (snapshot / live 1-line-per-second per side / replay of any recorded round); `inspect_lab.py` + `inspect_report.py` measured on the same 19 days (5,432 rounds, lat 0.1 s, 11 h run): 2a one 20-share bid at bb/-1c/-2c posted at 10 times and held: fills 94% early -> 34% at 30 s left -> 10% at 7 s left, worth -1.2..-3.7c/share at settlement in EVERY cell; 2b both sides at the notebook times (1:00/2:00/3:00/4:00/4:30/4:53 left): -4.5% to -18% return, pairs 60-93%, 30/30 FAIL; 2c odd/even/round prices: odd-cent "crowd" is the 1c/99c walls (6,175 sh vs 580 at even), best parity still -1.8c/share; 2d flow filters on the continuous join tactic: base -4.0% (0/19 days), sweep-pull -2.5% (pairs 30%), imbalance -1.2% CI [-3,283, +1,164] (pairs 56%), both -0.5% CI straddles 0 on 3% of the volume (pairs 10%) -> less loss by not trading, never an edge; 2e exits for 113,206 fills: sell-at-once (taker, fee) -3.1c/share vs hold -1.8c; pair-at-once as taker available on 747 fills only, and holding beat it there too; 2f maker rebate = 20% of the 0.07*p*(1-p) taker fee (docs) ~0.6% of money in (matches gabigol's 0.37%), cannot flip any row. 34 rules, 0 PASS. Facts for later: CLOB REST /book lists bids ascending / asks descending (best = last); BME rows mirror every level change on both tokens but record each trade once; stale levels exist in the archive -> use the bb/ba columns. Results: `C:\g\paired maker - REPLAY RESULTS with latency - FAIL, project stops - 2026-10-27.md`, `C:\g\v2-open-maker-inspect-order-book - RESULTS all 34 rules failed - 2026-10-27.md` (copies: working folder #12, Desktop `open_book_maker results`, repo `paired_maker/results/`, `v2-open-maker-inspect-order-book/results/`, VM). Nothing live touched; account 2 $32.41; polyvps untouched.

**Part #9 (2026-10-29) - norm1e69 "+$315k maker robot" X post audited and its replication tested: FAIL, $0:** wallet 0x41e2...a354 (polymarket @norm1e69). Official pnl API: gross $319,750 (the post's number), fees -$271,189, after fees $48,564, taker rebate $106,217 + maker rebate $13,541, net $168,322 on $13.77M volume; 79/81 days gross-positive but 59/81 after fees; bot stopped 2026-10-23. Full feed (1,335,054 rows, paged by 1-hour windows past the 5,000 offset cap) + taker-only feed (808,180): 80% of dollars are TAKER fills (fee inside the amount paid), trades 91.6% of BTC 5m rounds at all hours, enters median 21 s after open at ~46c with no direction (first side wins 47%), buys both sides in 91% of rounds all round, 0.2% sells, $95 max fill, prices 11-87c (modes 4-11c and 90c), pair cost median 0.991 (<0.96 rounds +8.5%, >1.02 rounds -4.3%). Screenshot "profitable" rows = winning legs only (Sep 28 8PM round: Up 4,044@0.393 + Down 4,114@0.609, net -$52). Oct 12-21 lost after fees every day; rebates kept it green. Exact maker/taker split on our 19 recorded days (5,358 rounds): maker +0.14c/share, taker +1.28c gross -1.19c fee = +0.09c net -> edge ~0.2% of money; profit = tiered Taker Rebate Program (39% of his fees back). Book match (3 days): sum of asks at his buys 1.010 median, never <1.00 (no pair arb, 0.05% of seconds with zero depth); taker lifts NOT timed to chainlink (toward 43% / away 50%, 0.1 bps). Replication rule (lift ask when model fair > ask + theta 2/3/5/8c, lat 0.1, lags 0 and 1.44 s) on 19 days: ALL 8 FAIL, gross -3.0..-4.7% before fees, 0/19 days, lag irrelevant. Other AI's MAKER-INTEL.md corrected (maker/sells/early/loss-mode/"no daily PnL endpoint" all wrong; address/cap/mix right). Paired-maker v2 cancel-clock check (3 days): join 0.1 s -4.92%, 0.02 s -1.65%, verdict unchanged. Files: `C:\g\norm1e69 wallet audit - the real numbers behind the X post - 2026-10-27.md`, `C:\g\norm1e69 - CONDITIONS and replication RULES - verdict - 2026-10-29.md` (copies: working folder #13, Desktop, repo wallet_audits/results, VM ~/wallets/norm1e69/). Nothing live touched; account 2 $32.41.
