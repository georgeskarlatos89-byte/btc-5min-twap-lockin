#!/usr/bin/env bash
# Stop-before-exhaustion (handoff guide section 4): stops ONLY s5-collector when free space on the data
# disk drops below 2 GiB. Never deletes data. Restart by hand after freeing/enlarging the disk.
set -euo pipefail
cd "$(dirname "$0")/../data"
free_kb=$(df -Pk . | awk 'END {print $4}')
if (( free_kb < 2097152 )) && systemctl is-active --quiet s5-collector.service; then
  echo "$(date -u +%FT%TZ) free ${free_kb} KiB < 2 GiB: stopping s5-collector" >> ../s5-diskguard.log
  systemctl stop s5-collector.service
fi
