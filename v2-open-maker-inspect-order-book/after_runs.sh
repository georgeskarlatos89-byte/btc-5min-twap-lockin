#!/usr/bin/env bash
# after_runs.sh - VM-side waiter (detached, survives ssh logout): when the paired-maker replay finishes, build its
# 19-day report; when the inspect lab finishes, build its report. Writes markers REPORT-DONE in each out folder.
set -u
cd ~/v2-open-maker-inspect-order-book
log() { echo "[$(date -u +%FT%TZ)] $*" >> after_runs.log; }
log "waiter started pid $$"
pm_done=0; lab_done=0
for i in $(seq 1 720); do                      # up to 24 h, poll every 2 min
  if [ $pm_done = 0 ] && [ -f ~/pm/replay_out/ALL-DONE ]; then
    ( cd ~/pm && python3 pm_replay_report.py --days "replay_out/day_*.json" --out replay_out/REPLAY-RESULTS.md --gate-lat 0.1 > replay_out/report.log 2>&1 && touch replay_out/REPORT-DONE )
    log "pm replay report built rc=$?"; pm_done=1
  fi
  if [ $lab_done = 0 ] && [ -f inspect_out/ALL-DONE ]; then
    python3 inspect_report.py --days "inspect_out/day_*.json" --out inspect_out/INSPECT-RESULTS.md > inspect_out/report.log 2>&1 && touch inspect_out/REPORT-DONE
    log "inspect report built rc=$?"; lab_done=1
  fi
  [ $pm_done = 1 ] && [ $lab_done = 1 ] && { log "both done, exit"; exit 0; }
  sleep 120
done
log "timed out"
