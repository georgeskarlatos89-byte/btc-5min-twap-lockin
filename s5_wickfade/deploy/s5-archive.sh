#!/usr/bin/env bash
# Retention procedure (handoff guide section 4): compress CLOSED daily files only. Never deletes data.
# A day file is closed once it is not today's file and has not been written for 30 minutes.
set -euo pipefail
cd "$(dirname "$0")/../data"
today="$(date -u +%F).jsonl"
for f in *.jsonl; do
  [[ -e "$f" ]] || continue
  [[ "$f" == "$today" ]] && continue
  if [[ -z "$(find "$f" -mmin -30)" ]]; then gzip -9 "$f" && echo "archived $f"; fi
done
