#!/bin/bash
# launcher: sandbox does not persist pip installs or processes across sessions
cd "$(dirname "$0")/.."
pip install --quiet --disable-pip-version-check websockets py-clob-client >/dev/null 2>&1
python3 -u s1_harness/twap_lockin_harness.py | tee -a s1_harness/harness.log
