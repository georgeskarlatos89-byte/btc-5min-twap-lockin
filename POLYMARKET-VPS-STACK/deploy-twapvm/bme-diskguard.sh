#!/bin/bash
# bme-diskguard.sh - stop the BME capture BEFORE the disk fills.
# The recorder writes ~5.8 GB/day raw (gzipped to ~2 GB at UTC midnight). A full disk would take
# the S1 observer, the trader, the S9 watcher and the monitor down with it. Runs every 5 min
# (bme-diskguard.timer, as root). Nothing else is ever touched.
MIN_FREE_MB=2048
LOG=/home/ubuntupolymarket3/POLYMARKET-VPS-STACK/bme-diskguard.log
free=$(df --output=avail -m / | tail -1 | tr -d ' ')
if [ "$free" -lt "$MIN_FREE_MB" ] && systemctl is-active --quiet bme-capture.service; then
    systemctl stop bme-capture.service
    echo "$(date -u +%FT%TZ) STOPPED bme-capture: free ${free} MB < ${MIN_FREE_MB} MB (resize the disk, then: sudo systemctl start bme-capture)" >> "$LOG"
    chown ubuntupolymarket3:ubuntupolymarket3 "$LOG" 2>/dev/null
fi
exit 0
