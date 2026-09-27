#!/usr/bin/env bash
# rotate.sh — compress COMPLETED daily CSVs (never today's, never a file still being written).
# Nothing is deleted. Runs 01:05 UTC.
set -uo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)"
today=$(date -u +%Y%m%d)
for f in "$S"/us_measure/us_rounds/bookstate-*.csv "$S"/us_measure/us_rounds/brti-proxy-*.csv "$S"/us_measure/weather/bucket_quotes-*.csv; do
  [ -f "$f" ] || continue
  case "$f" in *"$today"*) continue;; esac
  gzip -6 "$f" && echo "compressed $(basename "$f")"
done
for l in "$S"/us_measure/*.service.log "$S"/regime_watch/*.service.log "$S"/regime_watch/regime_watch.log; do
  [ -f "$l" ] && [ "$(stat -c %s "$l")" -gt 20000000 ] && { tail -n 20000 "$l" > "$l.tmp" && cat "$l.tmp" > "$l" && rm -f "$l.tmp" && echo "trimmed $(basename "$l")"; }
done
df -h "$S" | tail -1
