#!/usr/bin/env bash
# run_backtest.sh — execute S8 per-session attribution backtest
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

exec python3 s8_backtest.py --save-report --save-csv
