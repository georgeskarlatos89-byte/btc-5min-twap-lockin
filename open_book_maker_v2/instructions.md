# instructions.md — run OPEN-BOOK-MAKER on a fresh VPS

Audience: you (the setup AI or human) deploying this bundle. Read
`DECISION.md` before touching anything. The golden rule: **nothing trades
live until the Phase-A gate passes.**

---

## STEP 0 — CHECK FOR EXISTING DATA FIRST (do this before collecting anything)

The owner already runs bots on this VPS that COLLECT BTC 5-minute data. Do NOT
blindly start a fresh recorder over it. Find and reuse it:

```bash
# common places an earlier collector would write
ls -lah ~ /home/*/ /var/log 2>/dev/null | grep -iE 'book|round|5m|btc|jsonl|csv'
find / -name '*.jsonl' -mmin -20160 2>/dev/null | head -40   # last 14 days
find / -iname '*btc*5m*' -o -iname '*round*' 2>/dev/null | head -40
crontab -l 2>/dev/null; ps aux | grep -iE 'record|collect' | grep -v grep
```

Then inspect any candidate files: one JSON/CSV row per second-or-round with
fields like `ts,start,twap,bb,ba,fair_p` → it's compatible.

- If found → note path + schema + date range, and tell the owner before doing
  anything else. Convert it to the recorder's schema (one record per second,
  keys: ts,label,start,t_el,t_rem,twap,spot,acc_avg,gap_bps,fair_p,
  fair_p_end,bb,ba,mid,spread,bids,asks) into `book_data/YYYY-MM-DD.jsonl`,
  or point `maker_sim.py --replay` at it if its schema already matches.
- If NOT found → proceed to Step 3 and collect fresh.

**Do not delete or overwrite any existing data. Additive only.**

## STEP 1 — VPS requirements
- Ubuntu/Debian, ≥1 GB RAM, Python 3.11+, stable clock (NTP on).
- Outbound: gamma-api.polymarket.com, clob.polymarket.com,
  ws-live-data.polymarket.com, Polygon RPC (for chain_verify).

## STEP 2 — Unpack bundle
```bash
mkdir -p ~/open_book_maker && tar -xzf open_book_maker.tar.gz -C ~/open_book_maker
cd ~/open_book_maker
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env     # owner fills keys, never you
```

## STEP 3 — Phase A: capture + simulate (no money)
```bash
. .venv/bin/activate
python book_recorder.py          # 24/7 under tmux/systemd; writes book_data/
```
After each captured day:
```bash
# replay every captured day through the fill model (pass the jsonl files)
python maker_sim.py --replay book_data/*.jsonl     # -> sim_rounds.csv rows
python chain_verify.py --csv sim_rounds.csv        # settle vs Polygon truth
```
Check the gate (DECISION.md): ≥200 settled rounds, PnL > −5.5% benchmark with
CI, adverse-selection edge ≥ 0.

## STEP 4 — Phase B: live pilot (ONLY after the gate + owner approval)
Owner sets in `.env`: `DRY_RUN=false`, fills burner-wallet keys, `chmod 600`.
```bash
python maker_bot.py
```
Watch the first hour personally. Confirm: quotes appear, cancels fire,
no taker fills, kill file works (`touch KILL` → orders cancel in ~1s →
`rm KILL` to resume). Then let it run 7 days under systemd.

systemd unit (recommended):
```ini
# /etc/systemd/system/openbookmaker.service
[Unit]
Description=OPEN-BOOK-MAKER
After=network-online.target
[Service]
WorkingDirectory=/root/open_book_maker
ExecStart=/root/open_book_maker/.venv/bin/python maker_bot.py
EnvironmentFile=/root/open_book_maker/.env
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

## STEP 5 — Daily ops (2 minutes)
```bash
tail -f bot_logs/orders.jsonl        # quote/cancel/fill activity
tail bot_logs/bot_rounds.csv         # one row per round
```
After Day 14 → apply DECISION.md, report to owner with numbers.

## NEVER
- Paste private keys into chat/logs/files other than `.env` (chmod 600).
- Set `DRY_RUN=false` without a passed gate AND explicit owner approval.
- Raise size/stops beyond `.env` values.
- Delete existing data found in Step 0.
