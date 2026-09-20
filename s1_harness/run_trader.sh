#!/bin/bash
cd "$(dirname "$0")/.."
pip install --quiet --disable-pip-version-check websockets py-clob-client >/dev/null 2>&1
exec python3 -u s1_harness/trader.py
