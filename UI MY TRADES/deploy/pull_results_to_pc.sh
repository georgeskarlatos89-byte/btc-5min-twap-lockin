#!/usr/bin/env bash
# Copies the result CSVs from the VPS into this PC's "UI MY TRADES/btc" and "UI MY TRADES/weather".
# Run from Git Bash:  bash deploy/pull_results_to_pc.sh
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
for d in btc weather; do
  mkdir -p "$HERE/$d"
  scp -q "twapvm:ui_trader/UI MY TRADES/$d/*.csv" "$HERE/$d/" && echo "$d: $(ls "$HERE/$d" | wc -l) files"
done
