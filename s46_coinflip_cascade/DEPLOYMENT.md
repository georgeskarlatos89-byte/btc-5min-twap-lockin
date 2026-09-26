# S46 Deployment — twapvm (34.34.13.7)

**Box:** twapvm 34.34.13.7 europe-west4-a user ubuntupolymarket3 — THE TWAP LOCK-IN box, DRY, no .env on box yet
**Existing services on box:** s1-harness.service (observer), s1-trader.service (gated), s6-harvester.service (S6 DRY)

## Why twapvm and not polyvps

- polyvps 34.178.162.193 is LIVE fleet (~13 bots) with real money — do not disturb
- twapvm is the clean S1/S6 box, designed for this new strategy, no real money yet

## Steps

### 1. Create folder on twapvm

```bash
ssh twapvm
mkdir -p ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
chmod 700 ~/"#4 S4 S6 Paired"
chmod 700 ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
```

### 2. Copy package (from local workspace)

```bash
# from your PC or this workspace
scp -r s46_coinflip_cascade/* twapvm:"/home/ubuntupolymarket3/#4 S4 S6 Paired/s46_coinflip_cascade/"
```

Verify md5:
```bash
ssh twapvm "cd ~/'#4 S4 S6 Paired'/s46_coinflip_cascade && md5sum s46_harvester.py s46_backtest.py"
```

### 3. Create .env (chmod 600, never in git, never displayed)

On twapvm, create `.env` from Documents file containing Moon Dev key (26-char moongroup_*):

```bash
ssh twapvm
cat > ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/.env <<'EOF'
MOONDEV_API_KEY=moongroup_xxxxxxxxxxxxxxxx
LIVE_TRADING=0
# POLY keys only when ready for $10 incubation, after 3-day DRY
# POLY_PRIVATE_KEY=0x...
# POLY_FUNDER=0x...
# POLY_SIGNATURE_TYPE=3
EOF
chmod 600 ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/.env
```

Key lifetime ~1 week (moongroup_* not 24h rotating). M7 alarm (401) tells you when expired.

### 4. Install deps (once)

```bash
ssh twapvm
python3 -m pip install websockets py_clob_client_v2 --user
```

### 5. Preflight

```bash
ssh twapvm
cd ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
python3 s46_harvester.py --preflight
```

Expected:
- 5m and 15m tokens OK
- book fetch OK
- session gate quiet/active
- moondev key present: yes
- live keys present: no (DRY only) -> expected first 3 days
- hard gates MET after RTDS connects (feed stale clears after 10s)
- S6 band 0.45-0.55, S4 band 0.30-0.85, cascade $1.5M

If `moondev liq feed stale` persists >60s after preflight, check key.

### 6. Bounded DRY run (1 hour)

```bash
python3 s46_harvester.py --minutes 60
# in another shell
tail -f s46_harvester.log
cat s46_stats.json | python3 -m json.tool
cat s46_cascades.csv
cat s46_pulls.csv
python3 fill_asymmetry.py
```

What to expect DRY (weekend):
- S6: mid often outside 0.45-0.55 at bell → SKIP mid outside band (normal, many rounds not quotable)
- S6 quotes when mid inside band, 0.49 both sides
- Pulls: LIQ once per cascade timestamp (not every poll), SPOT_MOVE 6bps, IMB_EXTREME 0.85, BAND_EXIT
- S4: triggers rare on baseline funding tape (OKX funding +0.010%/8h baseline) — zero S4 on quiet day is correct (P3 waiting is edge)
- Fills simulated from tape (queue-optimistic) — proves plumbing, never edge

### 7. Systemd

```bash
ssh twapvm
sudo cp ~/"#4 S4 S6 Paired"/s46_coinflip_cascade/deploy/s46-harvester.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now s46-harvester.service
systemctl status s46-harvester.service
journalctl -u s46-harvester -f
```

Logs:
- `s46_harvester.log` (app)
- `s46-harvester.service.log` (systemd stdout/stderr)
- `s46_fills.csv`, `s46_s4_fills.csv`, `s46_pulls.csv`, `s46_cascades.csv`, `s46_stats.json`

### 8. Telegram monitoring (reuse s1_monitor.py)

Add to `~/s1_harness/s1_monitor.py`:

Hourly status line:
```
S46: S6 q=xx f=xx pairs=x legs x (w/l) asymmetry Up xx% Down xx% / S4 trig x fills x wins x by_size / pulls LIQ x SPOT x IMB x BAND / skips top reasons / errors / hb age / key status
```

Urgent pings dedup 6h:
- main loop DEAD (tick_ts older than 5 min while service active)
- 0 quotes in full quiet hour + top SKIP reasons
- Moon Dev 401/403 (key expired) — both S6+S4 blind → flat
- 429/5xx/empty feed/error storm >10/hour
- FILL REJECTED (band gate) — Part-4 class
- tick error
- any (LIVE) order line, DAILY STOP, KILL appears/disappears, LIVE_TRADING flipped
- service inactive

Alerts go to Telegram group -1002282822022 thread 600 (BTC-5-minute)

### 9. Event study for P_model

On twapvm, after 7 days of harvest:

```bash
cd ~/"#4 S4 S6 Paired"/s46_coinflip_cascade
python3 s46_backtest.py --event-study
cat s46_event_study_report.md
```

Harvest data location: `~/Moondev API/data/all_liquidations/10m/<date>/` — 270-278 rows per snapshot, 60s cadence, cannot be backfilled after key expires.

Join with:
- Kraken 1s spot for 60s/180s continuation
- Gamma 15m resolutions for win-rate
- CLOB prices-history for book repricing speed

Refine P_model in `s46_harvester.py` S4_P_MODEL + spot bonus.

### 10. Incubation ($10) after DRY

Only after:
- 3 days DRY: S6 quotes every quiet round, pulls fire once per cascade, band gate rejects, no error storms, heartbeat every 60s
- S6 asymmetry: 300 legs held → cond rate within 50%±8pp
- S4: at least 10 cascades detected, spot confirmation logged, P_model edge check working
- Event study: post-cascade continuation >55% post-fee over 100 triggers (or conservative prior with margin)

Then human review, then `.env` with POLY_PRIVATE_KEY + POLY_FUNDER + LIVE_TRADING=1 (account 2, small size).

Kill criteria:
- S6 asymmetry >8pp over 300 legs → killed
- S4 <55% post-fee win over 100 triggers → killed
- 2 consecutive weeks adverse > gross → killed

### 11. Exposure with existing bots

- S6 (old) and S46 S6 are same strategy — do not run both at same time, or share net cap $30/5m
- S3 fee-farm (0.45 both sides, t+30s..60%, cut20) overlaps first 15s with S6 window — combined net must stay <$30/5m
- S46 S4 is 15m only, so net cap per 5m bucket still applies (same underlying — cascade blows both up)

Future router should arbitrate quote ownership in overlap rather than blindly running both.

### 12. Security

- No credentials in git, chat, or logs
- .env chmod 600, folder chmod 700
- No exec/eval/subprocess in code
- Only hosts: api.moondev.com, clob.polymarket.com, gamma-api.polymarket.com, data-api.polymarket.com, ws-live-data.polymarket.com
- KILL file manual kill: `touch KILL` → flat

## Reference: Moon Dev feeds

- all_liquidations/10m.json — 1s median lag, ONLY intra-round feed (S4+S6 pull)
- binance_liquidations/10m.json — 163s median lag, BANNED intra-round (post-hoc only)
- all_liquidations/totals.json — 20s, 6 windows, long vs short, by_exchange — context
- imbalance/1h.json — 90s cadence, background pull trigger
- Data Freshness doc: tick 500ms, liq real-time websocket, REST 30s
- M5 lesson: measure natural variance before alert threshold (liq 1h 1055->4167 natural swing 75%)
