#!/usr/bin/env bash
# One bounded session per invocation; systemd can restart it.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -e KILL ]]; then
  echo 'KILL exists; refusing collection.' >&2
  exit 2
fi
mkdir -p data/sessions
# Refuse new sessions below 2 GiB free; never delete research data automatically.
available_kb=$(df -Pk data | awk 'END {print $4}')
if (( available_kb < 2097152 )); then
  echo 'Less than 2 GiB free; refusing collection.' >&2
  exit 2
fi
session="data/sessions/$(date -u +%Y%m%dT%H%M%SZ)-$$"
exec .venv/bin/python -u capture.py --minutes 60 --out "$session"
