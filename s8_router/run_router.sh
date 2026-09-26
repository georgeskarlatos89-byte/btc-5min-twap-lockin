#!/usr/bin/env bash
# run_router.sh — launch the S8 Session Regime Router daemon
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ "$1" == "--preflight" ]; then
    exec python3 s8_router.py --preflight
elif [ "$1" == "--status" ]; then
    exec python3 s8_router.py --status
else
    echo "Starting S8 Session Regime Router daemon..."
    exec python3 s8_router.py --daemon --poll 5.0
fi
