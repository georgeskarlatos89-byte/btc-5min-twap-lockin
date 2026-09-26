#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "=== S46 PREFLIGHT ==="
python3 s46_harvester.py --preflight
echo ""
echo "=== S46 BACKTEST EVENT STUDY (synthetic if no harvest) ==="
python3 s46_backtest.py --event-study
echo ""
echo "=== STATS ==="
python3 fill_asymmetry.py || echo "no stats yet (expected first run)"
