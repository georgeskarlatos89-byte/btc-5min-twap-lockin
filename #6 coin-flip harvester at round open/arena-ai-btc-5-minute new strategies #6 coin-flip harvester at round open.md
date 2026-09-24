# arena-ai-btc-5-minute new strategies #6 coin-flip harvester at round open — living record

Every run or change to strategy #6 is logged here as a Part. Same rules as the #1 and #3
records: plain language, no claims without data, no keys or addresses ever.

**What S6 is, in one breath:** at the exact moment a BTC 5-minute or 15-minute Up/Down round
opens, gamblers rush in and pay the maximum taker fee to buy either side of what is basically
a coin flip at 0.50. S6 rests a buy at 0.49 on BOTH the Up and the Down token (20 shares each,
about $10 a side) for the first 45 seconds only, then pulls. If both sides fill it paid 0.98
for a pair that pays 1.00 (2¢ per share, no directional bet). If only ONE side fills, S6
deliberately keeps that leg to the end of the round instead of cutting it. That is the actual
experiment: does the trader who hit our bid know something? After 300 held legs, if either
side wins more than 8 points away from 50%, the idea is killed.

**Where it lives:** `twapvm` (34.34.13.7, NL), folder
`~/#6 coin-flip harvester at round open/s6_coinflip/` (symlink `~/s6_coinflip` → same place),
systemd unit `s6-harvester.service` (7th service on the box). Package source: this folder's
`s6_coinflip/` (also inside `s6_coinflip_test_package.zip`, byte-identical). GitHub:
`georgeskarlatos89-byte/btc-5min-twap-lockin` → `s6_coinflip/`.

---

## Part #1 — Package review, S3 zombie found and fixed in the shared code, DRY deploy, Telegram wiring (2026-09-24)

### Context
The user handed over the S6 package (harvester, asymmetry report, README, playbook, S3 pairing
note, deployment guide, systemd template) and asked for a VPS deploy in a strategy-named
folder, the Moon Dev key from the Documents file, and Telegram updates like S3 (name + plain
description, urgent-only, error notifications for data mismatch / failed API calls).

### What the package is
`s6_harvester.py` is a copy of the S3 maker (with the five S3 plumbing fixes from S3 Part #1
already inside) with S6's own settings and one behavioural change:

| setting | S3 | S6 |
|---|---|---|
| bid | 0.45 | 0.49 |
| shares/side | 50 | 20 (~$10) |
| window | t+30 s → 60 % of the round | t+0 → 45 s |
| lone leg | cut after 20 s | HELD to resolution (the experiment) |
| pair cost | 0.90 | 0.98 |
| kill test | adverse > gross 2 weeks | 300 held legs, side win-rate > 8 pp from 50 % |

Pull triggers, gates, feeds, band (0.45–0.55), quiet hours (00–12, 16–18 UTC, weekends), $30
net cap per 5-min bucket, $20 daily stop, KILL file: identical to S3. The zip and the folder
are byte-identical (7 files). Security read of every file: only Polymarket + Moon Dev hosts, no
exec/eval/subprocess, no secrets, no writes outside its folder. Clean.

**No backtest exists for S6.** The guide says so itself and asks for one before any real money.

### Found first: the S3 DRY run had been a zombie for six hours
Checking S3 before cloning its code (it entered its first quiet session at 00:00 UTC):

| what | value |
|---|---|
| quotes in 6 quiet hours | 2 (one round, 00:05:54Z) |
| rounds settled | 0 |
| `KeyError('live_quotes')` in the log | 52 |
| service state the whole time | active |

Cause, from the code: the pull routine wrote a CSV line using a field `live_quotes` that no
round ever had. Every pull raised. At 00:07Z the mid left the band, the band-exit pull ran
inside the main tick loop, the loop died, and nothing restarted it because systemd only sees
the process, not the loop. From then on: no quotes, no settlements, while feeds kept
reconnecting. In LIVE this same bug would have meant resting quotes were never cancelled on a
pull. I measured the volatility gate too, since it was the other suspect: Chainlink ticks
arrive every 1.0 s and the trailing 5-min sigma was 5.3 bps against a 10.5 bps threshold, so
that gate was not the problem.

### Fixes applied to the SHARED code (S3 `s3_maker.py` and S6 `s6_harvester.py`), strategy constants untouched
| # | bug | fix |
|---|---|---|
| 1 | `KeyError('live_quotes')` on every pull → main loop dead, quotes never cancelled | count resting placed quotes instead |
| 2 | main tick loop had no crash guard and no liveness signal | body wrapped in try/except (`tick error …` logged); heartbeat `tick_ts` written to the stats file every 60 s |
| 3 | a cascade stays in the 10-min Moon Dev window, so the same cascade re-pulled every poll once the 60 s lock expired (S3 logged 10 "LIQ" pulls, all the same event) | fire once per cascade (track the newest cascade timestamp acted on; latest crossing wins so a new cascade behind an old one still fires) |
| 4 | gate skips were stored but never logged → six flat hours were invisible | `SKIP <series> <round>: <reasons>` once per round |
| 5 | the $20 daily stop was a hard gate on SIMULATED P&L too → a DRY run flats itself for the rest of the day after a few paper losses (for S6 that is three held legs) | daily-stop gate only when `LIVE_TRADING=1` (memory rule: kill switches never trip on paper P&L) |

S6-only fixes: the kill test is defined on FILLS (legs) but the stats counted SHARES (20 per
leg, so 15 legs would have looked like 300) → new `side_legs` / `side_leg_wins` counters,
`fill_asymmetry.py` reads them and prints a 95 % interval; the module docstring still
described S3 (0.45, t+30 s, cut20) → rewritten; the systemd template used `%i`/`%h`
placeholders → absolute paths, service user, log capture.

Offline tests before shipping: pull no longer raises and marks both quotes PULLED; cascade
scan returns the newest crossing (old SELL cascade then new BUY cascade → BUY fires); DRY
daily stop does not gate; both files compile; all 17 strategy constants byte-identical before
and after.

### Deploy (DRY) — run mechanism
1. `~/#6 coin-flip harvester at round open/s6_coinflip/` created (chmod 700), package copied,
   md5 of every file = local copy; symlink `~/s6_coinflip` for tooling.
2. `.env` (chmod 600) written from the Documents key file over an SSH pipe, never displayed:
   `MOONDEV_API_KEY=…` and `LIVE_TRADING=0`. No Polymarket keys → cannot place orders.
3. Preflight 06:32:49Z: 5m and 15m tokens OK, session QUIET, key present, live keys absent,
   KILL absent (the two "feed stale" gates are expected before the feeds connect).
4. Bounded 4-min run across the 06:35Z round open: liq feed 249 rows parsed, RTDS connected,
   heartbeat written, the 06:35 5m round was skipped with `mid 0.58 outside band` (the book at
   the bell was already skewed). No errors.
5. `s6-harvester.service` installed, enabled, active 06:37:10Z.
6. S3 restarted on the fixed code 06:32Z: quoted the 15m round at 06:33:10Z and the 5m round
   at 06:36:27Z, both pulled seconds later by `BAND_EXIT mid=0.45` (the mid sits right on the
   band edge in these rounds; expect many short quote lives).

### Telegram wiring (shared `s1_monitor.py`, one generic checker for S3 and S6)
Hourly status now has an S6 line with the plain-English description and the hour's counts:
quotes, fills, pairs, legs held (won/lost), kill-test sample by side out of 300, pulls by
trigger, skipped rounds with the top gate reasons, errors, heartbeat age, key status.

Urgent pings (dedup 6 h, global breaker still applies):
| trigger | why |
|---|---|
| main loop DEAD (heartbeat older than 5 min while the service is active) | today's zombie class; message names the restart command |
| 0 quotes in a full quiet hour, with the top SKIP reasons | S6 must quote every round in quiet hours |
| Moon Dev 401/403 (hourly probe or in-log) | key expired → both pull triggers blind → flat |
| Moon Dev 429 / 5xx / empty feed / error storm > 10 per hour | data problems |
| `FILL REJECTED` (band gate) | Part-4 class |
| `tick error` (main-loop exception that the guard survived) | one ping per 6 h |
| any `(LIVE)` order line, DAILY STOP, KILL appears/disappears, `LIVE_TRADING` flipped | real-money events |
| service inactive | existing service check (`s6-harvester` added) |

No fills-per-hour band for S6: there is no backtest baseline, and the guide says never set a
threshold before the natural range is known. The S3 band text now quotes the report's real
numbers (7.52 ± 2.12). Offline tests: digest-only lines → 0 pings; LIVE quote + gate
violation + key death + tick error → 4; dead heartbeat → 1; zero-quote quiet hour → band +
no-quotes; LIVE flip → 1.

### What to expect, in plain words
- **It is collecting data, not making money.** DRY means no orders exist. A "fill" is a
  guess: "someone sold at or below 0.49 while our pretend bid was up, so assume we got it".
  That guess is the same optimistic rule that fooled this project four times before (a real
  resting bid tends to fill only on the side about to lose).
- **What DRY can prove in ~3 quiet-hour days (judge around 2026-09-27):** the bot quotes every
  quiet round (SKIP reasons tell why not), pulls fire on real cascades exactly once, the band
  gate rejects touch-fills, held legs get settled and counted by side, no error storms, key
  expiry gets caught within an hour.
- **What it cannot prove:** whether 0.49 bids fill both sides at the bell, and whether the
  held legs really win ~50 %. Only real resting orders (tiny size, account 2, after a human
  says go) can answer that.
- **How long to 300 held legs:** unknown until the first days show the leg rate. At the bell
  the mid is often already outside 0.45–0.55 (the first S6 round was skipped for that reason),
  so many rounds will not even quote. If DRY legs arrive at, say, 5 per quiet hour, 300 takes
  about a week of quiet hours; if at 1 per hour, more than a month. The hourly status line
  shows the running count so the pace is visible.
- **Moon Dev key:** same 26-char key as S3, lifetime unknown (README says ~1 week). A
  "key REJECTED" ping from either strategy is the signal to paste a new one into both `.env`
  files.
- **Pairing with S3:** both DRY, so no real exposure overlap. The guide's $30 combined cap
  only matters once either one goes live.

### Files touched in this Part
- VM: `~/#6 coin-flip harvester at round open/s6_coinflip/*` (new), `~/s3_feefarm/s3_maker.py`
  (fixed), `~/s1_harness/s1_monitor.py` + reference copy (generic maker checks).
- Local: this folder's `s6_coinflip/{s6_harvester.py, fill_asymmetry.py, deploy/s6-harvester.service}`
  patched; `#3 …/s3_feefarm/s3_maker.py` patched; `#1 …/s1_harness/s1_monitor.py`; this record;
  S3 record Part #2.
- Memory: `project_s6_coinflip.md` (new), `project_s3_feefarm.md` (zombie + fixes),
  `feedback_alert_storm_rules.md` ("active" ≠ alive: every strategy loop needs a heartbeat).
- GitHub: `s6_coinflip/` (no `.env`, logs, stats), `s3_feefarm/s3_maker.py`,
  `s1_harness/s1_monitor.py`, both records.
