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
