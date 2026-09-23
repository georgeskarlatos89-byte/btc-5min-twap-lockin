#!/bin/bash
# launcher: sandbox does not persist pip installs or processes across sessions
cd "$(dirname "$0")/.."
pip install --quiet --disable-pip-version-check websockets >/dev/null 2>&1
python3 -u s3_feefarm/s3_backtest.py "$@"
