# arena-ai-btc-5-minute new strategies #10 vol-event binary box ("own the whipsaw, don't guess it") — living record

Every run or change to strategy #10 is logged here as a Part. Same rules as the other
records: plain language, no claims without data, no keys or addresses ever.

**What S10 is, in one breath:** most of the day it does nothing. When a US economic number
is due (8:30, 10:00 or 14:00 New York time on weekdays) or Bitcoin suddenly gets much more
volatile than its normal baseline, "box mode" arms for that 15-minute round. While armed it
does two things at once: it rests a 45-cent buy on BOTH the Up and Down tokens (a completed
pair costs 90 cents and always pays $1.00), and it watches for moments when the two asks
added together cost less than 97 cents after fees, buying both at once. If only one side
fills, it will NOT sit on that bet: after 60 seconds it chases the other side if the pair can
still be had under 97 cents, otherwise it sells the lone leg. The idea: panicking traders
during a whipsaw pay on both sides, and S10 is the one collecting.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder `~/#10 vol-event binary box/s10_volbox/`
(symlink `~/s10_volbox`), systemd unit `s10-box.service`. Calibration output in the sibling
folder `~/#10 vol-event binary box/calibration-2026-09-26/`. Package source: this folder's
`s10_volbox/` (= the zip, byte-identical, 12 files). GitHub:
`georgeskarlatos89-byte/btc-5min-twap-lockin` → `s10_volbox/`.

---

## Part #1 — Package review, ten plumbing fixes, local + VPS verification, calibration + DRY deploy (2026-09-26, Saturday)

### Context
The user handed over the S10 package with two guides (`AI-VPS-HANDOFF.md`,
`AI-VPS-DEPLOYMENT-GUIDE.md`) and asked to follow them, verify the pipeline works before the
VPS, and give the strategy its own folder. Today is Saturday: the guides say no releases
today, so the first job is the release-free volatility calibration.

### Package check
- `sha256sum -c SHA256SUMS`: 11 of 11 OK; zip vs folder: 12 files, 0 mismatches; compiles.
- Security read of every file: hosts are Polymarket (RTDS, CLOB, Gamma, data API) and
  Moon Dev only; the order code path (`py_clob_client_v2`) is only reachable with
  `LIVE_TRADING=1` plus keys, which do not exist in the `.env`. No eval/exec/subprocess. Clean.
- The engine is the S3/S6 skeleton (same feeds, gates, pulls) with S10's box logic on top.

### Ten fixes found by reading the code (strategy constants byte-identical before/after, 40 of them)
| # | bug | effect if left | fix |
|---|---|---|---|
| 1 | settlement check ran on the CURRENT round only (`e > T+60` can never be true) — the same bug another session fixed in S3 today | no round ever settles → kill-test counters stay 0 forever | settle finished rounds from the round table, retry every 30 s |
| 2 | calendar gate worked in whole ET minutes, so the documented 30 s pre-arm could never trigger, and the round key was always the current round so `e` was never negative | quotes go in at t+0..2 s, not t−30 s | seconds-precise gate; the next round's state is created 30 s early |
| 3 | calendar armed every day, weekends included | Saturday/Sunday "events" with no release pollute the 20-event sample | weekdays only (still a crude bucket calendar, see caveats) |
| 4 | `disarm()` only logged while still armed, but both call sites run after expiry | the "⬜ BOX DISARMED" line the guide says to look for never appears | log on leaving an armed state |
| 5 | a chased leg is a taker order but was booked at the raw ask, and the $0.97 cap was compared pre-fee | pair gains overstated by ~1.7 ¢/share, which feeds kill rule #2 | ask + fee, cap compared post-fee (0.52 no longer fits, 0.50 does) |
| 6 | kill test counted every armed round, so one release = 1 × 15m + 3 × 5m = 4 "events" | 20-event verdict after ~5 releases | 15m round = the event; 5m rounds counted separately |
| 7 | DRY pair-arb assumed a 20-share FOK fills at top-of-book regardless of size | fake pairs on thin books | needs ≥ 20 shares at the best ask on BOTH sides, else `PAIR-ARB THIN` |
| 8 | no phantom-fill guard (S6 has one) | a tape print from before our quote existed counts as our fill | ignore prints older than the quote |
| 9 | disarmed rounds logged nothing (the straddle call is gated on armed) | "alive but flat" indistinguishable from dead in the log | one `SKIP … box mode disarmed` line per round |
| 10 | calibrate summary printed 5m sigma ÷ √12 as "sigma_1h" | wrong label only | × √12 |

Offline tests (Windows, Python 3.13): calendar gate on Monday 2026-09-28 EDT arms at
12:29:31Z, not at 12:29:29Z, tail ends 12:47:00Z, 10:00 and FOMC buckets found, Saturday
never arms, 12:00 UTC clear; DISARMED line logged; chase at ask 0.50 → booked 0.5175 and
paired, chase at 0.52 → unwind; kill test counts 15m as the event; phantom and thin-book
guards hold. All pass. Note: the guides list an S3-style 60-minute unit-test suite nowhere;
these are my tests.

### Local verification before the VPS (the user's rule)
Temp copy + temporary `.env` with the Moon Dev key (deleted after): preflight OK (both
rounds' tokens, ET wall-clock 06:41 EDT, key present, live keys absent); 6-minute DRY run:
liq feed 137 rows, RTDS connected, heartbeat written, sigma peak 3.9 bps, 0 errors, 0 quotes
(correct: Saturday, disarmed), `kill_test.py` → INSUFFICIENT SAMPLE with zeroes.

### VPS deploy — run mechanism
1. `~/#10 vol-event binary box/s10_volbox/` (chmod 700) + `deploy/`, symlink `~/s10_volbox`;
   checksums 11/11 on the VM; compile OK.
2. `.env` (chmod 600): the Moon Dev key copied VM-side from S3's `.env` with `sed`, piped
   straight into the file (the guides say reuse that key; nothing displayed), `LIVE_TRADING=0`.
3. Preflight 10:49:57Z: 15m + 5m tokens OK, calendar clear, vol gate clear (baseline 7.0,
   threshold 12.6 bps), key yes, live keys no, KILL absent, ET wall-clock 06:49 (EDT, no
   fallback warning → tzdata present). `systemd-analyze verify` clean.
4. Calibration (the guide's "today" step): `--calibrate --minutes 120` started 10:49:58Z in
   the sibling folder `calibration-2026-09-26/` (own copy of the engine + env, so its stats
   file never races the real one). Output `calib.log`; PID file `calib.pid`.
5. DRY smoke `run_box.sh dry 30` started 10:50:05Z in the real folder, PID file `smoke.pid`.
   First lines: liq feed 240 rows, RTDS connected, `SKIP 15m …: box mode disarmed`,
   `SKIP 5m …: box mode disarmed` (correct for Saturday).

### DRY smoke result (30 min, 10:50:05Z–11:20:07Z, Saturday = disarmed all the way, as the guide expects)
| guide check | result |
|---|---|
| starts, RTDS connected, liq feed shape line | ✅ (240 rows) |
| heartbeat in `s10_stats.json` every 60 s | ✅ (age 45 s when read) |
| every round → `SKIP …: box mode disarmed` | ✅ 6 × 5m, 3 × 15m |
| no maker quotes, no pair-arb, no arms | ✅ 0 / 0 / calendar 0, vol 0 |
| Moon Dev 401/403/429 absent | ✅ |
| no `(LIVE)` lines, no tracebacks, no tick/tape errors | ✅ 0 |
| rounds settle (fix #1) | ✅ 7 `ROUND-RESULT` lines (n=7), all two-leg=0, pnl $0 |
| `kill_test.py` | INSUFFICIENT SAMPLE, all zeroes |
| sigma peak | 2.97 bps |

One thing the smoke exposed: the engine's own rolling 24-hour median baseline (written to
`sigma_baseline_bps` in the stats after six 5-minute samples) had already replaced the 7.0
default with **2.75 bps** by the end of the run, because the Saturday tape is that quiet. The
vol gate then fires at 1.8 × 2.75 ≈ 5.0 bps, which is ordinary weekday volatility (5.3 bps
was measured on a Wednesday morning for S3). So on Monday morning the box may arm on "vol"
during normal tape until the rolling median catches up with weekday levels. This is the
strategy's own design, not a bug I introduced, and I have not changed it; see the calibration
note below for the decision it implies.

### Service + KILL (11:20Z)
`s10-box.service` enabled and active 11:20:09Z from the strategy folder, first tick logged
the two SKIP lines and reconnected RTDS. First KILL test: the file made the box flat (hard
gate) but left no trace in the log, because SKIP is logged once per round. Fix #11: the
engine now logs `🛑 KILL file present — FLAT` / `🔔 KILL file removed` on the transition
(the monitor also pings on the file itself). Re-tested after the restart: both lines appear
within 5 s.

### Calibration (Saturday, release-free) — interim at 27 min, final numbers land ~12:50Z
Running in `calibration-2026-09-26/calib.log`: n=1,618 samples, sigma5m 2.3 bps, sigma1h
2.8 bps (the code's 1h window, same 5-minute-horizon units). The guide's rule: if the
suggested `SIGMA_BASE_BPS` differs from the 7.0 default by more than ±2 bps, change the
constant. It will (Saturday is ~2–3 bps). **I am not changing it on a Saturday number, and
here is why, in plain words:** the 7.0 default comes from the strategy document's weekday
measurement (5m σ ≈ 7.0–7.7 bps, Kraken, 12 h sample). A weekend baseline is "quiet-tape
quiet", not "release-free weekday quiet". Setting the baseline to 2.5 would make the vol gate
arm at 4.5 bps, i.e. on most ordinary weekday mornings, turning a release-event strategy into
an always-on one. Meanwhile the engine's rolling 24 h median already adapts by itself (it is
at 2.75 bps now and will climb through Monday). The decision for the user / strategy author:
(a) keep 7.0 as the floor and let the rolling median only move ABOVE it, (b) calibrate on a
weekday without releases instead, or (c) accept the weekend number. Until decided, the code
runs as shipped (rolling median, no floor). The final calibration print will be appended
here as Part #2 when the run ends.

### Telegram wiring (shared `s1_monitor.py`)
Coordinated with the S8 session that was editing the same file this morning (it finished
and handed over at 10:45Z); my stanza was applied on top of its copy, S8 text untouched.
Hourly status gains an S10 line: armed/disarmed counts by source, maker quotes, dry fills,
pairs, pair-arb fires (and "too thin" refusals), chases, unwinds, pulls, skipped rounds with
reasons, errors, heartbeat age, kill-test progress (events of 20, two-leg %, pair gains vs
unwind losses), sigma baseline/peak, key status, LIVE/KILL. Pings exactly per the guide's
table: heartbeat dead 🚨, BOX ARMED / PAIR-ARB FIRE / UNWIND informational (one each),
calendar gate silent on a weekday after 14:00 ET ⚠, Moon Dev 401/403/429/error storm, gate
rejections 🚨, tick error ⚠, LIVE lines / DAILY STOP / KILL / LIVE_TRADING flip, EST fallback
⚠, service inactive. No fills/hour band (the guide forbids it: flat 95 % of the day is right).
Offline tests: digest-only → 0 pings; armed+arb+unwind → 3; urgent → 4; dead loop → 1;
calendar-silent → 1; LIVE flip → 1.

### Caveats that matter for the research (plain words)
- **There is no real economic calendar.** The gate arms every weekday at 8:30, 10:00 and
  14:00 ET whether or not a number is actually released. Many "armed events" will be quiet
  rounds, which dilutes the 20-event kill test. A real release calendar is the first B-step
  improvement to make.
- **No backtest exists.** DRY fills use the optimistic tape rule that fooled this project
  four times. The pair-arb "combined ask < $0.97 with 20 shares on both sides" is the real
  question, and only the DRY data around real prints can start answering it.
- **Pace:** 3 buckets × 5 weekdays = 15 armed 15m rounds a week, so the 20-event minimum is
  about 1.5 weeks of calendar time, but the guide wants 60+ for a serious interval.
- **Pairing with S6/S46:** S6 flattens above 1.5× baseline, S10 arms above 1.8×; the gap is
  intentional and untouched.

---

## Part #2 — Calibration result, kill-test status after 43 hours, data pushed for analysis (2026-09-28, Monday 06:36Z)

### Context
The user asked to confirm: "S10 has met its own kill rule. It shows 43 armed events with 0%
two-leg completion, and its rule says kill below 30%", then to push all collected data to
GitHub for analysis. Checked against the VM, not from memory.

### Calibration final (Saturday 10:49Z-12:50Z, 7,095 samples)
| item | value |
|---|---|
| Baseline sigma_5m | 2.17 bps |
| Baseline sigma_1h (scaled, fixed label) | 7.52 bps |
| Vol gate threshold at 1.8x | 3.91 bps |
| Suggested SIGMA_BASE_BPS | 2.2 (default 7.0) |

Not applied, for the reason given in Part #1: a weekend baseline arms the gate on ordinary
tape. The data below shows that this is exactly what happened through the rolling median.

### Kill test, read from `s10_stats.json` at 2026-09-28 06:36Z
| item | value |
|---|---|
| armed events (15m rounds) | 45 (43 when the user looked) |
| two-leg completions | 0 |
| two-leg rate | 0.0 % -> rule 1 prints KILL (needs n >= 20, rate >= 30 %) |
| rule 2 (unwind losses <= pair gains) | PASS, trivially: both are $0.00 |
| armed 5m rounds (counted separately) | 115 |
| maker quotes / fills | 268 / 0 |
| pair-arb fires | 0 (2 refused as too thin) |
| rounds settled | 673 |

**So yes, by the letter of its own rule S10 is in KILL.** What the number is made of:

| what | value |
|---|---|
| calendar (release) arms | **0** |
| volatility arms | 56: sigma over baseline 44, liquidation cascade 10, imbalance 2 |
| baseline at the moment of a sigma arm | min 1.20, median 3.00, max 5.10 bps |
| sigma at the moment of a sigma arm | min 2.2, median 6.7, max 12.0 bps |
| how quote pairs ended | 123 of 134 by BAND_EXIT (mid left 0.40-0.60), 6 by disarm, 3 by cascade |
| quote lifetime | median 31 s (p25 19 s, p75 47 s) |
| pulls | 682: SPOT_MOVE 541, BAND_EXIT 95, LIQ cascade 44, IMB 2 |
| errors | 18 RTDS normal closes (reconnected), 8 cut-off Moon Dev answers, 0 tracebacks |

### Honest reading
1. The sample contains **no macro release at all**. It ran Saturday 11:20Z to Monday 06:36Z;
   the first calendar bucket of its life is today 12:30Z. The strategy's actual claim
   ("whipsaws around US data prints") has been tested on zero prints.
2. Every armed event came from the volatility gate, and most of those came from the
   weekend-quiet rolling baseline (median 3.0 bps at arm time, against the 7.0 the strategy
   document measured on a weekday). Part #1 flagged this risk; the data confirms it.
3. What the data does show, and it is real evidence: in vol-armed rounds the 0.45 straddle
   never filled once in 268 quotes, because the market leaves the 0.40-0.60 band within about
   half a minute. The maker half of S10 does not work in that regime. The pair-arb half
   found a sub-$0.97 combined ask twice, both times without 20 shares of depth.
4. Not yet checked (analysis to do on the pushed data): whether any taker SELL at or below
   0.45 printed while a quote was resting, which separates "no one sold to us" from "the DRY
   fill rule could not see it".

### Other session's change, for the record
`s10_box.py` was changed on the VM 2026-09-28 05:39Z by another session (backup kept as
`s10_box.py.pre-mdretry-20260928`): one immediate retry when the ~290 KB Moon Dev answer
arrives cut off. Plumbing only. Both versions are in the data folder so the analysis knows
which code produced which lines.

### Data pushed
Folder `S10 data collected 2026-09-26 to 2026-09-28/` (local #10 folder and GitHub
`#10 vol-event binary box/`): full engine log, service log, pulls CSV, stats JSON, smoke log,
2-hour calibration log, kill-test output, log analysis, both engine versions, snapshot time.
No fills or pairs CSV exists because no fill or pair ever happened.

---

## Part #3 — Tape check: sellers DID trade at or below 0.45 while quotes rested; the "0 fills" was a measurement bug (2026-09-28, 06:44Z)

### Context
The user asked to check the open question from Part #2: did any seller actually trade at or
below 45 cents while a quote was resting? Answered from the public trade tape
(`data-api.polymarket.com/trades`, taker side) for all 134 quote pairs S10 placed between
2026-09-26 12:37Z and 2026-09-28 05:39Z. Script, per-window CSV and summary are in the data
folder.

### Method
Each quote window was rebuilt from the engine log (placed → first pull / disarm / window end).
For each window, every public trade in that market inside the window was classified:
- **Route A** (the engine's own fill rule): a taker SELL of a token at 0.45 or less.
- **Route B** (complementary match, never checked by the engine): a taker BUY of the OTHER
  token at 0.55 or more. On Polymarket a buy of Up at 0.55 can be matched against a resting
  buy of Down at 0.45, so it can fill our bid too.

### Result
| item | value |
|---|---|
| quote windows checked | 134 (35 on 15m, 99 on 5m), 0 errors |
| placed BEFORE the round opened | 128 of 134 |
| window length | median 30 s (p25 19 s, p75 45 s) |
| trades inside all windows | 9,569 |
| windows with no trade at all | 3 |
| **Route A: windows with a taker SELL ≤ 0.45** | **19** (36 prints, 560.7 shares) |
| Route B: windows with a taker BUY of the other token ≥ 0.55 | 102 (2,035 prints, 58,830 shares) |
| windows with any fillable print | 102 |
| windows where BOTH of our sides were fillable | 27 (20 %) |
| one side only | 75 (our Down bid 50, our Up bid 25) |

By series:

| series | windows | none | one side only | both sides |
|---|---|---|---|---|
| 15m (the kill-test events) | 35 | 20 | 12 | 3 (9 %) |
| 5m | 99 | 12 | 63 | 24 (24 %) |

### Why the engine logged 0 fills
A bug I introduced in Part #1. Fix #2 there creates the next round's state 30 seconds before
it opens so quotes can rest early. But the tape cursor of a round started at the round's
START time, which for a pre-created round is in the future. Every trade printed before the
open was therefore skipped as "old". Since 128 of 134 quote pairs rested before the open and
were pulled within about half a minute, the fill detector was blind for almost the whole
experiment. A second, smaller flaw: the phantom guard compared an integer tape second with a
fractional placement time, dropping prints in the same second as the quote.

**Fixed 06:46Z:** cursor starts at `min(round start, now) - 1`; phantom guard compares whole
seconds. Constants identical, all earlier tests pass, new test: a pre-open print after the
quote is recorded as a fill, a print older than the quote is still refused. Backups on the
VM: `s10_box.py.pre-tapecursor-20260928`, `s10_stats.before-tapecursor-fix-20260928.json`.
Route B was NOT added to the fill rule: that is a modelling decision (see below).

### What this changes, and what it does not
1. **The Part #2 number is invalid as evidence.** "45 events, 0 two-leg" measured a blind
   detector, not the market. The 45 armed events up to 06:46Z must not be used for the kill
   test. Counting restarts from the fix.
2. **The honest upper bound is still under the kill line.** Even if every fillable print had
   filled us (it would not: queue position, size), both legs were reachable in 3 of 35
   fifteen-minute windows (9 %) and 27 of 134 overall (20 %). The rule needs 30 %.
3. **The familiar pattern again.** 75 windows were fillable on one side only, and that side
   is the one the market is moving against (someone buying Up at 0.55+ fills our DOWN bid
   while Up is rising). That is the adverse selection that killed the paper-maker, EXP-D
   and gabagool lines: a resting bid gets the losing side.
4. **Still zero macro releases in the sample.** All of this is weekend and early-Monday
   volatility arming. The strategy's actual claim is untested.
5. **Pre-open resting is a side effect worth a decision.** The 30 s pre-arm was written for
   calendar rounds; in vol mode it also places quotes 30 s before every round, which is where
   almost all quotes lived. Whether vol-armed rounds should pre-place at all is a strategy
   question.

### Decisions for the user
- Kill now (the optimistic bound is below 30 % and the fill pattern is adverse), or run
  through this week's releases with the detector now working.
- Whether DRY fills should also count Route B. Without it the simulation under-counts fills;
  with it the simulation gets more optimistic. Suggested: log Route B as its own counter,
  never as fills.

### Also fixed today: data folder could not be opened on Windows
Every file in `S10 data collected 2026-09-26 to 2026-09-28/` has a full path of 279-301
characters; Explorer, Notepad and Excel stop at 260. A short-path copy now lives at
`C:\Users\Administrator\polymarket-bot\S10 data 2026-09-28\` (longest path 86 characters)
with a READ ME FIRST file. Nothing was deleted or moved.
