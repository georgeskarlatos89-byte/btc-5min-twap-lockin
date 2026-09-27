#!/usr/bin/env bash
# history-refresh.sh — daily history corpus maintenance (systemd timer, 00:10 UTC).
# 1) rewrites the rolling 7-day full-precision price corpus (the endpoint keeps ~1 week)
# 2) extends the deep outcome corpora (gamma series, incremental)
# 3) chain-verifies whatever is new (resumable checkpoint; cheap once the full relabel has run)
# Read-only public endpoints, stdlib only. Safe to re-run.
set -uo pipefail
cd "$(dirname "$0")"
echo "[$(date -u +%FT%TZ)] history refresh start"
python3 history_backfill.py --symbol BTC --variant fiveminute --days 7 | tail -n 3
python3 history_backfill.py --symbol BTC --variant fifteen  --days 7 | tail -n 3
python3 history_backfill_gamma.py --symbol BTC --variant fiveminute --incremental | tail -n 3
python3 history_backfill_gamma.py --symbol BTC --variant fifteen  --incremental | tail -n 3
python3 chain_relabel.py --csv history/BTC_fiveminute_long.csv --workers 3 | tail -n 12
python3 chain_relabel.py --csv history/BTC_fifteen_long.csv  --workers 3 | tail -n 12
echo "[$(date -u +%FT%TZ)] history refresh done"
