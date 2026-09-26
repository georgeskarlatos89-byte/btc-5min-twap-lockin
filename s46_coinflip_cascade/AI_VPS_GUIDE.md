# AI VPS PUSH GUIDE — S46 Paired (S6 Coin-Flip + S4 Cascade)

**Purpose:** This file is for another AI (or human) to push S46 to twapvm and start collecting data / backtesting. No credentials in this file.

## 0. CONTEXT — Which VPS

From `VPS-CONTEXT-PROMPT.md`:

- `polyvps` 34.178.162.193 europe-west4 user ubuntupolymarket3 — **LIVE FLEET (~13 bots) real money**. Do NOT touch unless user says fleet/main box.
- `twapvm` 34.34.13.7 europe-west4-a user ubuntupolymarket3 — **TWAP LOCK-IN box, DRY only**. This is where S46 goes. Two services already: s1-harness.service (observer) + s1-trader.service (gated) + s6-harvester.service.

SSH aliases in `~/.ssh/config` on user's PC. If ambiguous, ASK.

Telegram alerts: group -1002282822022 thread 600 "BTC-5-minute"

## 1. What S46 Is

- S6: maker at bell, both sides 0.49, 20sh, window 0-45s, band 0.45-0.55, hold single leg to measure asymmetry (kill >8pp over 300 legs)
- S4: taker mid-round 15m only, trigger all_liq_10m >=$1.5M/10s + spot confirms >=2bps directional, entry ask <= P_model - fee -0.02, P_model prior 1.5M->0.60,2.5M->0.65,4M->0.70,8M->0.75 + spot bonus cap 0.80, hold to resolution
- Pairing: same LIQ trigger pulls S6 and enters S4. S6 dies in vol, S4 dies in dead tape — uncorrelated.
- Moon Dev knowledge: all_liquidations/10m.json median 1s lag = ONLY intra-round feed. binance_liquidations/10m.json median 163s lag = BANNED intra-round. totals.json 20s context, imbalance 90s background. M3/M4/M7 flat-switch: stale/empty/401 -> flat.

## 2. Package Contents

```
s46_coinflip_cascade/
  s46_harvester.py        # main combined harness, DRY by default
  s46_backtest.py         # event study for S4 P_model
  fill_asymmetry.py       # S6 asymmetry + S4 win-rate report
  README.md               # strategy thesis
  S46-PLAYBOOK.md         # operator playbook, entry rules, kill criteria
  S4-EVENT-STUDY.md       # how to calibrate P_model
  DEPLOYMENT.md           # full deploy steps
  AI_VPS_GUIDE.md         # this file
  .env.example            # env template
  run_preflight.sh        # preflight + backtest + stats
  deploy/
    s46-harvester.service # systemd unit
```

No .env, no logs, no stats in package — created on VM.

## 3. Push to twapvm — Steps for AI

### 3.1 Create folder on twapvm

```bash
ssh twapvm
mkdir -p ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
chmod 700 ~/"#4 S4 S6 Paired"
chmod 700 ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
```

If folder exists, backup old:
```bash
mv ~/"#4 S4 S6 Paired"/s46_coinflip_cascade ~/"#4 S4 S6 Paired"/s46_coinflip_cascade.bak.$(date +%Y%m%d)
mkdir -p ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
```

### 3.2 Copy files

From workspace where this package lives:

```bash
# if you have the zip extracted
scp -r s46_coinflip_cascade/* twapvm:"/home/ubuntupolymarket3/#4 S4 S6 Paired/s46_coinflip_cascade/"

# or from this workspace root
# scp -r /home/user/s46_coinflip_cascade/* twapvm:~/\"#4 S4 S6 Paired\"/s46_coinflip_cascade/
```

Verify:
```bash
ssh twapvm "ls -lh ~/'#4 S4 S6 Paired'/s46_coinflip_cascade/ && md5sum ~/'#4 S4 S6 Paired'/s46_coinflip_cascade/s46_harvester.py"
```

### 3.3 Create .env (CRITICAL, chmod 600, never in git)

Human must paste key from Documents file. AI must NOT invent key. Ask user to create .env via SSH pipe.

Template:
```
MOONDEV_API_KEY=moongroup_xxxxxxxxxxxxxxxx
LIVE_TRADING=0
# POLY keys only after 3-day DRY and human review
# POLY_PRIVATE_KEY=0x...
# POLY_FUNDER=0x...
# POLY_SIGNATURE_TYPE=3
```

On twapvm:
```bash
cat > ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/.env <<'EOF'
MOONDEV_API_KEY=moongroup_...paste...
LIVE_TRADING=0
EOF
chmod 600 ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/.env
```

Key class: moongroup_* (~1 week lifetime, not 24h rotating moonstream_*). M7 alarm (401) tells you when expired.

### 3.4 Install deps (once)

```bash
ssh twapvm
python3 -m pip install websockets py_clob_client_v2 --user
# or inside venv if project uses venv
```

### 3.5 Preflight (read-only, no orders)

```bash
ssh twapvm
cd ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
python3 s46_harvester.py --preflight
```

Expected output:
- 5m and 15m tokens OK for <epoch>
- book Up bid/ask (may be 0.94/0.95 if near resolution, or ~0.50 at bell)
- session gate: QUIET (weekend or 00-12/16-18 UTC) or ACTIVE
- S6 band (0.45,0.55) price 0.49 x20 window 0-45s
- S4 band (0.3,0.85) markets ['15m'] cascade $1.5M spot_confirm 2.0bps
- moondev key present: yes (if .env created) else NO -> flat via M7 (expected before feeds)
- live keys present: no (DRY only) -> expected first 3 days
- hard gates: MET after RTDS connects (feed stale clears after ~10s) or NOT MET flat (if key missing)
- RTDS: wss://ws-live-data.polymarket.com topics chainlink + twap_sixty
- LIQ feed: all_liquidations/10m.json (1s median lag, ONLY)
- BANNED: binance_liquidations/10m.json (163s lag)

If `moondev liq feed stale` persists >60s after preflight with key present, check key validity (401).

### 3.6 Bounded DRY run (1 hour)

```bash
cd ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
python3 s46_harvester.py --minutes 60
```

In another shell:
```bash
tail -f s46_harvester.log
cat s46_stats.json | python3 -m json.tool
cat s46_cascades.csv
cat s46_pulls.csv
python3 fill_asymmetry.py
```

What to expect DRY:
- Weekend baseline: S6 mid often outside 0.45-0.55 at bell -> SKIP mid outside band (normal)
- S6 quotes when mid inside band, 0.49 both sides, DRY (no real orders)
- Pulls: LIQ once per cascade timestamp (fix from S6 record: old bug re-pulled same cascade every 60s), SPOT_MOVE 6bps, IMB_EXTREME 0.85, BAND_EXIT
- S4: triggers rare on baseline funding (OKX funding +0.010%/8h) -> zero S4 on quiet day is correct (P3 waiting is edge)
- Fills simulated from public tape (queue-optimistic) -> proves plumbing, never edge (Part-4 lesson: +5.71pp fake from bypassed gates)
- Logs: s46_harvester.log, s46_fills.csv (S6), s46_s4_fills.csv (S4), s46_pulls.csv, s46_cascades.csv, s46_stats.json

### 3.7 Systemd — long-running collection

```bash
ssh twapvm
sudo cp ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/deploy/s46-harvester.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now s46-harvester.service
systemctl status s46-harvester.service
journalctl -u s46-harvester -f
```

Logs:
- App log: `~/.../s46_coinflip_cascade/s46_harvester.log`
- Service log: `~/.../s46_coinflip_cascade/s46-harvester.service.log` (stdout/stderr)
- Data: `s46_fills.csv`, `s46_s4_fills.csv`, `s46_pulls.csv`, `s46_cascades.csv`, `s46_stats.json`

### 3.8 How to know if collecting

Check these, in order:

1. Service active?
```bash
systemctl is-active s46-harvester.service
```

2. Heartbeat alive? (tick_ts every 60s, S6 record fix for zombie loop)
```bash
cat s46_stats.json | python3 -c "import json,sys,time; s=json.load(open('s46_stats.json')); print(f\"tick_ts age={(time.time()-s.get('tick_ts',0))/60:.1f}min\")"
```
If age >5 min while service active -> loop DEAD (zombie class, S6 had 6h zombie with 52 KeyError, 0 quotes)

3. Quotes in quiet hour?
```bash
grep -c "S6 QUOTE" s46_harvester.log
grep "SKIP" s46_harvester.log | tail -n 20
```
If 0 quotes in full quiet hour (00-12,16-18 UTC, weekends) with service active -> alert (S6 must quote every quiet round)

4. Pulls firing once per cascade?
```bash
cat s46_pulls.csv
cat s46_cascades.csv
```
Expect LIQ pulls with reason `cascade SELL/longs $x.xxM/10s` or `BUY/shorts`, each timestamp once. Old bug: 10 LIQ pulls same event.

5. S6 fills and S4 triggers?
```bash
python3 fill_asymmetry.py
cat s46_s4_fills.csv
```

6. Feed health?
```bash
grep -i "moondev liq" s46_harvester.log | tail
# 401 -> key expired M7 -> flat, need new key
# 429 -> rate limited, backoff 30s
# empty -> M3 flat
```

If collecting, after 1 hour you should see:
- s46_stats.json tick_ts recent
- SKIP logs with reasons (mid outside band is normal)
- At bell (every 5m: :00,:05,:10...), if mid inside band, S6 QUOTE (DRY)
- If cascade occurs (vol), s46_cascades.csv new row + PULL BOTH + S4 TRIGGER attempt

### 3.9 Backtesting — Event Study for P_model

Harvest data location on twapvm: `~/Moondev API/data/all_liquidations/10m/<date>/<epoch>.json.gz` + `manifest_<date>.csv`
Collected since 2026-09-07, 60s cadence, 270-278 rows per snapshot, cannot be backfilled after key expires.

Run:
```bash
cd ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
python3 s46_backtest.py --event-study
cat s46_event_study_report.md
cat s46_event_study.csv | head -n 20
```

Without harvest data, script runs synthetic demo (500 fake cascades) to show report shape.

Real study needs:
- Harvested 10m snapshots -> scan cascades (done by script)
- Join with Kraken 1s spot for 60s/180s continuation
- Join with Gamma 15m resolutions for win-rate
- Join with CLOB prices-history for book repricing speed

Output: `s46_event_study.csv` + `s46_event_study_report.md` with counts by side and size bucket (1.5M,2.5M,4M,8M)

Refine P_model in `s46_harvester.py` S4_P_MODEL + spot bonus based on report.

### 3.10 Telegram monitoring (reuse s1_monitor.py)

Add to `~/s1_harness/s1_monitor.py` (already has S3,S6 checks):

Hourly status line S46:
```
S46: S6 q=xx f=xx pairs=x legs x (w/l) asymmetry Up xx% Down xx% / S4 trig x fills x wins x by_size / pulls LIQ x SPOT x IMB x BAND / skips top reasons / errors / hb age / key status
```

Urgent pings dedup 6h, global breaker:
- loop DEAD heartbeat >5m
- 0 quotes in quiet hour + top SKIP reasons
- 401/403 key expired
- 429/5xx/empty/error storm >10/hour
- FILL REJECTED (band gate) Part-4 class
- tick error
- LIVE order, DAILY STOP, KILL appears/disappears, LIVE_TRADING flipped
- service inactive

Alerts to group -1002282822022 thread 600.

### 3.11 Kill / Flat

Manual kill:
```bash
touch ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/KILL
# makes it FLAT (keeps running), Restart=always cannot loop on it
# to resume: rm KILL
```

Daily stop $20 LIVE only -> writes KILL automatically + flat. DRY P&L simulated never kills (S6 record Part #1 lesson).

### 3.12 Exposure with existing bots

- Old S6 harvester `s6-harvester.service` and new S46 S6 are same strategy — do NOT run both simultaneously, or share net cap $30/5m
- S3 fee-farm 0.45 both sides t+30s..60% cut20 overlaps first 15s with S6 — combined net must stay <$30/5m
- S46 S4 is 15m only, net cap per 5m bucket still applies (same underlying)

Future router should arbitrate quote ownership in overlap.

## 4. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| SKIP moondev liq feed stale | no key or key invalid or network | check .env MOONDEV_API_KEY present, preflight shows yes, wait 60s for first poll |
| 401 Unauthorized | key expired M7 | paste new key from Documents file into .env, chmod 600, restart service |
| 429 Too Many Requests | rate limited, no key? | backoff 30s automatic, check key |
| 0 quotes in quiet hour | mid outside band 0.45-0.55 (normal) or feed stale or session gate | check SKIP reason, if mid outside band -> normal, many rounds not quotable at bell |
| tick_ts age >5m service active | main loop dead (zombie) | check s46_harvester.log for tick error, restart service, alert |
| FILL REJECTED | band gate or net cap or feed stale | Part-4 gate working, loud failure is correct |
| RTDS silent 60s | stall watchdog | auto-reconnect ~3s, check network |
| S4 never triggers | quiet tape baseline funding, correct behavior | wait for US session vol, or check s46_cascades.csv empty = no cascades (normal Sat) |

## 5. Evidence Needed Before LIVE ($10 incubation)

- DRY 3 days: quotes every quiet round, pulls fire once per cascade, band gate rejects, no error storms, heartbeat every 60s
- S6 asymmetry: 300 legs held -> cond rate within 50%±8pp
- S4: at least 10 cascades detected in vol session, spot confirmation logged, P_model edge check working, ask repricing speed measured
- Event study: post-cascade continuation 60s/180s >55% post-fee over 100 triggers, or conservative prior with margin

Then human review, then .env with POLY_PRIVATE_KEY + POLY_FUNDER + LIVE_TRADING=1 (account 2 small size)

## 6. Security

- No credentials in git, chat, logs
- .env chmod 600, folder chmod 700
- No exec/eval/subprocess in code
- Only hosts: api.moondev.com, clob.polymarket.com, gamma-api.polymarket.com, data-api.polymarket.com, ws-live-data.polymarket.com
- KILL file manual kill

## 7. Quick Commands Cheat Sheet

```bash
# preflight
python3 s46_harvester.py --preflight

# 1 hour DRY
python3 s46_harvester.py --minutes 60

# logs
tail -f s46_harvester.log
cat s46_stats.json | python3 -m json.tool
python3 fill_asymmetry.py

# service
sudo systemctl status s46-harvester.service
journalctl -u s46-harvester -f
sudo systemctl restart s46-harvester.service

# backtest
python3 s46_backtest.py --event-study
cat s46_event_study_report.md

# kill
touch KILL
rm KILL
```

## 8. Reference Docs in Workspace

- `BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD` -> S6 + S4 definitions
- `s3_feefarm/docs/MOONDEV 6+ KNOWLEDGE.MD` Part 5b -> freshness, M3/M4/M7, 1s vs 163s lag
- `s3_feefarm/docs/moondev_docs/Moondev Docs/Multi-Exchange Liqs.md` -> endpoints
- `s3_feefarm/docs/moondev_docs/Moondev Docs/Data Freshness.md` -> update freq
- `VPS-CONTEXT-PROMPT.md` -> polyvps vs twapvm

End of guide.
