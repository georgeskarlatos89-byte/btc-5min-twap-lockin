# Live deployment (2026-10-10 16:01 UTC, user instruction "run it live with $10 size")

- VM: twapvm, folder `~/#11 open-book maker/` (symlink `~/obm`), service `obm-maker.service`
  (systemd, Restart=always, log `~/obm/maker_bot.log`, orders `~/obm/bot_logs/orders.jsonl`,
  rounds `~/obm/bot_logs/bot_rounds.csv`).
- Account: Polymarket account 2 (the S1 / btc5bot account), proxy signing
  py_clob_client_v2 + signature_type 3 + funder. `.env` built FILE-TO-FILE from
  `~/s1_harness/.env` (chmod 600); keys never passed through chat.
- Settings: DRY_RUN=false, LIVE_QUOTE_SIZE=20 shares per side (~$10 at 0.50),
  MAX_NET_SHARES=40, DAILY_STOP_USD=20, orders posted GTC with post_only=True.
- Kill: `touch "~/#11 open-book maker/KILL"` cancels everything within ~1 s; `rm` resumes.
- Stop for good: `sudo systemctl disable --now obm-maker.service` (orders are cancelled on
  SIGINT only; after a hard stop run the kill file first or cancel in the UI).
- Telegram: notify.py -> the BTC-5-minute thread (start, fills, hourly, daily stop, kill, errors).
- Chain audit of live rounds: `python3 chain_verify.py --csv bot_logs/bot_rounds.csv`.
- First-round incident: the fill parser read the taker's trade record; fixed 16:08 UTC to
  read our `maker_orders[]` entry (see maker_bot.poll_fills). Fills before the fix were
  seeded as "seen" and are not in the bot's ledger: 20 UP @0.51 + 20 UP @0.12 in round
  1791648300 (cost $12.60, round settled Down).
