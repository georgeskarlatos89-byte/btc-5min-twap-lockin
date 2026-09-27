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

### Files touched in this Part
- VM: `~/arena-ai-copy btc-5-minute workspace/measurement_stack/*` (new), 3 services + 4 timers
  in `/etc/systemd/system/ms-*`. No existing service, file or strategy was modified. The shared
  `s1_monitor.py` was not touched.
- Local: `measurement_stack/` (code, deploy units, outputs), this record.
- Memory: `project_measurement_stack.md`, `reference_polymarket_label_sources.md`.
- GitHub: `measurement_stack/` (code, units, reports, backtest CSVs; no caches, no raw data).
