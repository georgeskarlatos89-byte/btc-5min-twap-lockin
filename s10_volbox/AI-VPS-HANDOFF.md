# S10 Vol-Event Binary Box — AI-to-AI VPS Handoff

This package is S10 ("own the whipsaw, don't guess it") built to pair with the existing S6
Coin-Flip Harvester already running on `twapvm`. It is **DRY by default**. The immediate
mission is **vol-baseline calibration and DRY data collection**, not live trading. Read
this entire file before touching the VPS.

## Package contents (byte-identical to source zip)

```
s10_volbox/
├── s10_box.py                 # runner (DRY default); same plumbing skeleton as s6_harvester.py
├── kill_test.py               # prints the two kill criteria from s10_stats.json
├── run_box.sh                 # convenience launcher (preflight|calibrate|dry N|live|killtest)
├── README.md                  # strategy overview, RBI status, quick start
├── S10-PLAYBOOK.md            # operator rules + sequencing
├── PAIRING-S6.md              # how S10 + S6 coexist (opposite weathers, shared cap)
├── AI-VPS-DEPLOYMENT-GUIDE.md # long-form deployment reference
├── .env.example               # copy to .env, chmod 600, paste Moon Dev key
└── deploy/
    └── s10-box.service        # systemd unit template (edit paths/user)
```

SHA256 (verify after unzip on the VPS):
```
(cd s10_volbox && sha256sum *.py *.sh *.md .env.example deploy/*.service)
```

## What S10 does in one breath

- When a **calendar round** contains a US macro release (8:30/10:00 ET data or 14:00 ET FOMC
  bucket — covers CPI/NFP/PPI/PCE/GDP/FOMC/ISM/etc.), **box mode arms** 30s before round-open
  and holds for the round + 120s aftershocks.
- When **trailing-1h realized σ > 1.8× baseline** (cascade regime), box mode arms off-calendar.
- While armed it runs two harvests **simultaneously**:
  - **(a) Pair arbitrage (taker):** buy Up + Down FOK whenever combined ask post-fee < **$0.97**.
  - **(b) Maker straddle:** rest BUY @ **0.45** on BOTH Up and Down. If both fill → cost $0.90
    pays $1.00.
- **Leg-risk rule (opposite of S6):** if one leg fills and the other hasn't in **60s**, CHASE
  the missing leg up to combined cost $0.97; beyond that, **UNWIND the lone leg at model fair**.
  S10 **never** carries a naked event position — that is S4/S5's job.
- All fills pass through the Part-4 band gate (0.40–0.60) BEFORE they are recorded; no fill
  bypasses gates; DRY runs the exact same gates as LIVE.

## Today's job (today is Saturday, 2026-09-26 — NO releases): CALIBRATE

The strategy doc says "today is used to calibrate the vol-gate threshold against a
release-free baseline." That is what `--calibrate` does. **Do not skip this.** The default
`SIGMA_BASE_BPS = 7.0` is inherited from S3/S6; we need today's empirical baseline.

## VPS facts (from the existing S6 living record)

- Host: `twapvm` at `34.34.13.7` (NL).
- User: `ubuntupolymarket3`.
- Existing strategy folder naming convention: `~/<strategy-number> <slug>/<code_folder>/`,
  e.g. `~/#6 coin-flip harvester at round open/s6_coinflip/` (symlink `~/s6_coinflip`).
- Existing services: s1-harness, s1-trader, s1-monitor, s9-watcher, s3-maker, s6-harvester.
- Moon Dev API key lives in the existing `~/s6_coinflip/.env` (and `~/s3_feefarm/.env`).
  **RE-USE THE SAME KEY** for S10 — do not request a fresh one. It has a ~1 week lifetime.
- s1_monitor.py sends Telegram updates (hourly digest + urgent pings). S10 needs a new
  checker stanza added to s1_monitor.py (see "Telegram" below).
- Python: `/usr/bin/python3`. The `py_clob_client_v2` package is already installed for S3/S6.
  If `import websockets` fails on the box, `pip3 install --user websockets` (same package S3/S6
  use for RTDS).
- Systemd services run as `ubuntupolymarket3`, WorkingDirectory points at the strategy's
  code folder; logs are appended to a `.service.log` in the same folder.

## Deploy sequence (copy-paste grade)

All steps below are run over SSH as `ubuntupolymarket3@twapvm` unless stated.

### 1. Stage the folder (matches existing layout)

```bash
mkdir -p ~/"#10 vol-event binary box"
cd ~/"#10 vol-event binary box"
# Transfer this zip and unpack it here:
unzip s10_volbox_test_package.zip
cd s10_volbox
chmod 700 .
chmod +x run_box.sh
python3 -m py_compile s10_box.py kill_test.py && echo "COMPILE OK"
```

Create symlink for tooling (matches S6 pattern):
```bash
ln -sfn ~/"#10 vol-event binary box/s10_volbox" ~/s10_volbox
```

### 2. Write .env (chmod 600, never commit)

```bash
cp .env.example .env
chmod 600 .env
# Pull the existing Moon Dev key from S6 (same key, do not rotate):
sed -n 's/^MOONDEV_API_KEY=//p' ~/s6_coinflip/.env
# Paste that into .env:
#   MOONDEV_API_KEY=<same 26-char key as s3/s6>
#   LIVE_TRADING=0
# Leave POLY_PRIVATE_KEY / POLY_FUNDER commented out — NO live orders yet.
vi .env
```

Verify: `grep MOONDEV_API_KEY .env | wc -c` should be >30. `LIVE_TRADING` must be `0`.

### 3. Preflight

```bash
./run_box.sh preflight
```

Expected: both 15m and 5m tokens resolve "OK"; Moon Dev key present = "yes"; live keys = "no";
`LIVE_TRADING: 0`; KILL = absent; ET wallclock printed (verify against `TZ=America/New_York date`
on the box — if the preflight prints "(EST fallback: verify tzdata)", install tzdata:
`sudo apt-get install -y tzdata` — late September is EDT (UTC-4); an uncorrected EST fallback
makes the calendar gate fire 1h late, which misses every print).

### 4. Baseline calibration (the "today" step — Saturday, release-free)

Run 2 hours of RTDS sampling. This can run in a tmux/screen window or in the background with
nohup. It does NOT place orders, does NOT touch Polymarket keys, only reads RTDS + Moon Dev:

```bash
./run_box.sh calibrate 120 2>&1 | tee calib.log
```

After it finishes it prints something like:
```
Baseline sigma_5m  = X.XX bps
Baseline sigma_1h  = X.XX bps (scaled)
Vol gate threshold = X.XX bps (1.8x 5m baseline)
SIGMA_BASE_BPS suggested value: X.X
```

If the suggested `SIGMA_BASE_BPS` is materially different from the default `7.0` (more than
±2 bps), edit the constant near the top of `s10_box.py` and re-run preflight. Log the
calibration numbers in a new `S10-LIVING-RECORD.md` (create it — Part #1 "calibration run";
record date, N samples, baseline, threshold, whether tzdata was needed, anything unusual).

### 5. Bounded DRY smoke test (30–60 min)

```bash
./run_box.sh dry 60 2>&1 | tee smoke.log
```

**What to look for (must ALL be true):**
- Service starts, prints "S10 vol-event binary box starting" line.
- `RTDS connected; subscribing chainlink-spot` within a few seconds.
- Heartbeat tick in `s10_stats.json` updates every 60s (cat "tick_ts" field).
- On a Saturday (disarmed), expect: `box mode disarmed` → every round `SKIP …: box mode
  disarmed`. No maker quotes, no pair-arb fires. That is CORRECT.
- Moon Dev 401/403/429 is ABSENT once the right key is in `.env`.
- `moondev liq feed` shape log appears within ~5s:
  `liq feed shape: N rows, sample={…}`.
- NO lines containing `(LIVE)` (LIVE_TRADING=0 → impossible).
- NO tracebacks in the log. A single `tick error …` is acceptable if it recovers; a storm is not.
- `kill_test.py` at the end prints all zeroes and `INSUFFICIENT SAMPLE`.

Ctrl-C when done.

### 6. First live-gate test: arm on calendar (Mon/Tue 8:25–8:45 ET)

Schedule a bounded DRY run around a real 8:30 ET print. Start 10 min before, stop 10 min after:

```bash
# 08:20 ET = 12:20 UTC (EDT) = 13:20 UTC (EST fallback). Adjust for the day's offset.
./run_box.sh dry 30 2>&1 | tee release_830.log
```

Verify in the log:
- `🔵 BOX ARMED (calendar) — calendar CPI/NFP/… @08:30 ET` appears **before** the print
  (the pre-arm is 30s, so expect it ~12:30:00–12:30:30 UTC if EDT).
- Maker quotes (DRY) post for 15m round around 0.45 both sides.
- If the tape whipsaws, you should see pair-arb sniff messages (combined ask prints).
- Around 60s after any one-leg fill, either `CHASE FILL …` or `UNWIND …` appears.
  **No leg should stay naked past 60s + a few seconds** — grep for `naked` or stale
  MAKER_FILLED without EXITED/PAIRED past `LEG_TIMEOUT_S`.
- `⬜ BOX DISARMED — calendar window passed` fires cleanly within a minute or two after the round.

### 7. Install systemd (only AFTER calibration + a clean smoke test)

Edit `deploy/s10-box.service` if paths/user differ (they should match the existing S6 unit
exactly, just substituting folder and script names). The file already points at
`ubuntupolymarket3` and the `#10 vol-event binary box/s10_volbox` folder.

```bash
sudo cp ~/s10_volbox/deploy/s10-box.service /etc/systemd/system/s10-box.service
sudo systemctl daemon-reload
sudo systemctl enable --now s10-box.service
sudo systemctl status s10-box.service --no-pager
journalctl -u s10-box.service -f | tail -50
```

Verify: `active (running)`, no immediate crash loop, log file `s10-box.service.log` is
appended to, heartbeat in `s10_stats.json` advances every 60s.

Test KILL switch:
```bash
touch ~/s10_volbox/KILL
# Within one 2-second tick, the log should show hard gate "KILL file present" on SKIP lines.
# No maker quotes, no pair-arb fires while KILL exists.
rm ~/s10_volbox/KILL   # only after confirming
```

### 8. Telegram monitoring (extend s1_monitor.py)

Add an S10 stanza to `~/s1_harness/s1_monitor.py` following the S6 pattern. Add these ping
rules (dedup 6h unless noted):

| trigger | why |
|---|---|
| heartbeat >5 min stale while `s10-box.service` is active | dead-loop class (the S3-zombie lesson) |
| `🔵 BOX ARMED (calendar)` appears | informational, one ping per event |
| `🎯 PAIR-ARB FIRE` appears | informational (the convexity moments) |
| `🚪 UNWIND` appears | informational — feeds kill-criterion #2 |
| 0 armed calendar rounds across a week that has scheduled releases | calendar gate broken |
| Moon Dev 401/403/429/empty/error storm >10/hour | data problems |
| `❌ … REJECTED (gate)` | Part-4 class |
| `tick error` in the log | one ping per 6h |
| Any `(LIVE)` order line, DAILY STOP, KILL present/cleared, LIVE_TRADING flipped | real-money events |
| `s10-box.service` inactive | same existing inactive-service pattern |

**Do NOT add a fills/hour band.** S10 fills cluster around event prints — a flat tape 95% of
the day is correct.

### 9. Determine if it is collecting OR backtesting (and report back)

After install, look at `~/s10_volbox/s10_stats.json` and answer these questions in your
reply:

1. **`tick_ts` is advancing every 60s?** → feed is alive.
2. **`arms.calendar` and `arms.vol` are populating across days?** → box is being armed
   on real signals.
3. **`maker_quotes > 0`?** → maker quotes are being placed (DRY or LIVE).
4. **`taker_pairs` > 0 and `s10_pairs.csv` has rows with post-fee combined cost?** → pair-arb
   sniffer is seeing real whipsaw dislocations. **This is the first empirical piece of the
   B-step — write the average combined cost observed.**
5. **`kill_test.events_armed` ≥ 20?** → sample large enough to render kill verdicts. If not,
   note how many events are accumulating per week (rough rate) and estimate when it will
   hit 20.
6. **Run `python3 ~/s10_volbox/kill_test.py` and paste the output.**
7. **Any `UNWIND` lines in the log around real releases?** → note rate and P&L contribution.

The answer to "collecting OR backtesting" is:
- **Collecting (DRY) right now**: we are capturing real whipsaw pair-arb frequency,
  real two-leg maker fill rates (DRY-optimistic upper bound), real unwind frequency,
  and real vol-regime arming. This is the data the B-step needs.
- **Historical backtest still needed** (separate task): the B-step also needs a backward
  look over ~60 days of release rounds to estimate two-leg rates without waiting weeks for
  live prints. Do NOT block DRY data collection on that; collection and backtest can run in
  parallel.

### 10. What NOT to do (strict)

- Do NOT flip `LIVE_TRADING=1` now. Live requires: ≥20 armed DRY events, `kill_test.py`
  green on BOTH rules, human approval, tested KILL + rollback.
- Do NOT tighten the 1.5×/1.8× S6/S10 vol-gate gap — the no-man's-land between them is
  intentional.
- Do NOT raise the $0.97 pair cap or the 0.45 maker bid. These are the incubation parameters
  from the playbook; changing them turns the experiment into a different strategy.
- Do NOT add an "early exit" or "hold to resolution" override to S10 — that converts it
  into S4/S5. The leg-risk rule (chase then unwind) is what makes this NOT a naked
  directional book.
- Do NOT combine S6 and S10 on the same side of a leg and call it diversification. They
  are separated by gates; if gates misfire, the $30/5m cap is the backstop.
- Do NOT write `.env`, logs, or `s10_stats.json` to the git repo. `.gitignore` should
  exclude `.env`, `*.log`, `*.csv`, `s10_stats.json`, `KILL`.

### 11. Files to commit back to the repo (if you make fixes)

Only code/docs. Never secrets, never logs:
- `s10_volbox/s10_box.py`
- `s10_volbox/kill_test.py`
- `s10_volbox/run_box.sh`
- `s10_volbox/README.md`
- `s10_volbox/S10-PLAYBOOK.md`
- `s10_volbox/PAIRING-S6.md`
- `s10_volbox/AI-VPS-DEPLOYMENT-GUIDE.md`
- `s10_volbox/.env.example`
- `s10_volbox/deploy/s10-box.service`
- `s10_volbox/S10-LIVING-RECORD.md` (new — Part #1 = calibration, Part #2 = smoke, etc.)
- Add a note to `VPS-CONTEXT-PROMPT.md` at the repo root listing s10-box as the 8th service.

## Final check before you walk away

```bash
sudo systemctl is-active s10-box.service    # should print "active"
ls -la ~/s10_volbox/KILL                    # should NOT exist
cat ~/s10_volbox/.env | grep LIVE_TRADING   # should be 0
python3 ~/s10_volbox/kill_test.py           # report output back
tail -20 ~/s10_volbox/s10_box.log           # should show heartbeat + no errors
```

Report back with: calibration baseline value, smoke test log highlights, whether tzdata was
needed, kill_test.py output after the first few armed events, and any deviations from the
expected behavior above.
