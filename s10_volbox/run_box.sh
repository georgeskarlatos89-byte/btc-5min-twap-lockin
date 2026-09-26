#!/usr/bin/env bash
# Convenience launcher. Usage: ./run_box.sh [preflight|calibrate|dry N|live]
set -e
cd "$(dirname "$0")"
case "${1:-dry}" in
  preflight) python3 -u s10_box.py --preflight ;;
  calibrate) python3 -u s10_box.py --calibrate --minutes "${2:-120}" ;;
  dry)      python3 -u s10_box.py --minutes "${2:-180}" ;;
  live)     python3 -u s10_box.py ;;
  killtest) python3 -u kill_test.py ;;
  *) echo "usage: $0 {preflight|calibrate [min]|dry [min]|live|killtest}"; exit 1 ;;
esac
