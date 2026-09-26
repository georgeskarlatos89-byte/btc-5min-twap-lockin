# S10 Vol-Event Binary Box — VPS handoff guide for another AI

## Mission

Deploy S10 alongside the existing S6 harvester on the Polymarket VPS in a safe, observable
way. S10 harvests whipsaws in event rounds; S6 harvests the open in quiet tape. They share
execution plumbing, the Moon Dev feed, and the $30 net-directional cap; they do not share
orders or ledgers. The immediate goal is **baseline calibration + DRY data collection**,
not live trading.

## Non-negotiable safety rules

1. Never copy credentials into the repository, logs, Telegram, or chat.
2. `.env` lives only on the VPS, chmod 600. Start with `LIVE_TRADING=0`.
3. Do not set `LIVE_TRADING=1` until: (a) calendar gate is verified against a real US-session
   release in DRY; (b) ≥20 armed events in DRY show two-leg fill rate ≥30% and unwind losses
   ≤ pair gains; (c) a human approves.
4. The KILL file must work: `touch s10_volbox/KILL` flats the bot immediately (it keeps
   running but will not quote or take).
5. Confirm Moon Dev auth is healthy. Stale/empty/401 → FLAT, never blind.
6. **Never carry a naked event position.** The leg-risk rule (60s chase → $0.97 cap → unwind)
   is what makes S10 different from a straddle that turns into a directional bet on a
   spike. If a code change leaves legs naked past the timeout, REVERT.
7. S10 + S3 + S6 share BTC exposure. Combined net directional must stay under $30 per
   5-minute bucket.
8. Do not claim edge from DRY fills. Queue position is optimistic; only real fills count.
9. ET calendar: the code uses `zoneinfo` (America/New_York) when available. If the host
   lacks tzdata, the log prints an EST-fallback warning; install `tzdata` before Monday
   (EDT matters).

## Package contents

- `s10_box.py` — runner (DRY by default; same skeleton as `s6_harvester.py`).
- `kill_test.py` — prints the two kill criteria from `s10_stats.json`.
- `README.md`, `S10-PLAYBOOK.md`, `PAIRING-S6.md` (this guide's siblings).
- `deploy/s10-box.service` — systemd template; adapt paths/user.

## Today's first step — vol-baseline calibration (Saturday)

The strategy says "nothing fires today (Saturday, no releases); calibrate the vol-gate
threshold against a release-free baseline." That is the `--calibrate` mode:

```bash
cd ~/btc-5min-twap-lockin/s10_volbox
python3 s10_box.py --calibrate --minutes 120
```

It connects RTDS, sits for 2 hours sampling 1s Chainlink ticks, and prints:
```
Baseline sigma_5m  = X.XX bps
Baseline sigma_1h  = X.XX bps (scaled)
Vol gate threshold = X.XX bps (1.8x 5m baseline)
SIGMA_BASE_BPS suggested value: X.X
```

If the printed baseline differs materially from the default `SIGMA_BASE_BPS = 7.0`, update
the constant at the top of `s10_box.py` AND annotate `s10_stats.json` by running one
preflight with the new value. Commit the change with a Part entry in a living record.

## VPS deployment sequence

```bash
mkdir -p ~/btc-5min-twap-lockin/s10_volbox
chmod 700 ~/btc-5min-twap-lockin/s10_volbox
# copy this package in, preserving the s10_volbox directory
cd ~/btc-5min-twap-lockin
python3 -m py_compile s10_volbox/s10_box.py s10_volbox/kill_test.py
```

Create `s10_volbox/.env` on the VPS only (chmod 600):

```dotenv
# Required for feed collection. Never commit.
MOONDEV_API_KEY=REPLACE_ON_VPS   # same key as S3/S6

# Keep disabled during calibration + DRY.
LIVE_TRADING=0

# Do not add POLY_PRIVATE_KEY / POLY_FUNDER until human-approved incubation step.
```

Then:
```bash
chmod 600 s10_volbox/.env
python3 s10_volbox/s10_box.py --preflight
python3 s10_volbox/s10_box.py --minutes 60     # bounded DRY across one or two rounds
```

Watch the log for: clean round discovery, BOOK pulls for `mid=…` near the open, correct
ET wall-clock (no EST-fallback warning in a week where EDT is active — note: late October
2026 is EDT until Nov 1), no unexpected `(LIVE)` lines, clean heartbeat every 60s.

## Systemd installation

Edit `deploy/s10-box.service` with the real user and absolute paths (same pattern as
`s6-harvester.service` — unprivileged user, no root). Then:

```bash
sudo cp s10_volbox/deploy/s10-box.service /etc/systemd/system/s10-box.service
sudo systemctl daemon-reload
sudo systemctl enable --now s10-box.service
sudo systemctl status s10-box.service --no-pager
journalctl -u s10-box.service -f
```

To emergency-flat without stopping the process:
```bash
touch ~/btc-5min-twap-lockin/s10_volbox/KILL
```

To stop fully:
```bash
sudo systemctl stop s10-box.service
```

## Telegram monitoring (extend the existing s1_monitor.py)

Add an S10 line to the hourly status digest (same pattern as S6). Pings (dedup 6h):

| trigger | why |
|---|---|
| heartbeat older than 5 min while service is active | dead-loop class (the S3 zombie lesson) |
| BOX ARMED on calendar | informational, one ping per event |
| PAIR-ARB FIRE | informational (these are the convexity moments) |
| UNWIND of a lone leg | informational — these feed kill-criterion #2 |
| 0 armed-rounds in a week that had scheduled releases | calendar gate broken |
| Moon Dev 401/429/empty/error storms | data problems |
| FILL REJECTED (band gate) | Part-4 class |
| tick error | one ping per 6h |
| any (LIVE) order line, DAILY STOP, KILL present/cleared, LIVE_TRADING flipped | real-money events |
| service inactive | existing inactive-service check |

Do NOT add a fills/hour band for S10 — fills cluster entirely around event prints; a flat
tape is correct 95% of the day.

## Backtest / B-step (still needed before live)

The DRY runner collects ground truth (`s10_pairs.csv`, `s10_fills.csv`) but the full
historical B-step should:

1. Harvest historical BTC 5m/15m market books / trades for the last 60 days at
   8:30/10:00/14:00 ET releases (and around cascade-flagged rounds).
2. Reconstruct (a) how often combined ask post-fee < $0.97, and (b) whether resting 0.45
   bids on both sides fill within the event window (optimistic queue assumption only).
3. Apply the Part-4 band gate BEFORE recording simulated fills.
4. Measure: two-leg fill rate per armed event, average combined cost, chase frequency
   under the 60s/$0.97 rule, and net of unwind losses.
5. Split by event type (CPI vs FOMC vs cascade-off-calendar).
6. Report confidence intervals — 20 events is the LIVE incubation minimum but 60+ is needed
   for a serious CI on a 30% fill rate.

If the data say two-leg rate is <30% post-fee, kill the strategy before LIVE, per the
playbook.

## Live incubation gate — human approval required

Do not enable live orders unless all of the following hold:

- Calendar gate observed live (correct ET) across at least 2 DRY releases.
- ≥20 armed events in DRY with two-leg fill rate ≥30% and unwind ≤ pair gains.
- `kill_test.py` prints two greens (both rules PASS).
- S3/S6/S10 combined funding reviewed and $30/5m cap confirmed at size.
- Human explicitly approves the exact size, start date, and `LIVE_TRADING=1` change.
- Rollback + KILL-file procedure tested.

The Moon Dev RBI lesson: research first, backtest as a filter, incubate tiny only after
plumbing and evidence justify it. Do not convert this handoff into a profit promise.
