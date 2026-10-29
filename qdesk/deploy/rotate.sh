#!/usr/bin/env bash
# rotate.sh — qdesk daily rotation (qd-rotate 00:50 UTC; also called by disk_guard.py with --aggressive).
# Pattern: measurement_stack/deploy/rotate.sh. NEVER touches anything outside $QD_DATA (default ~/qdesk/data).
#
#   1. features/live_<YYYYMMDD>.csv  (yesterday or older, left by the recorder) -> gzip to day_<YYYYMMDD>.csv.gz
#      (if a day_ file from the BME build already exists, the recorder file becomes live_<day>.csv.gz instead; nothing clobbered)
#   2. state/*.log > 20 MB -> keep the last 20k lines          (--aggressive: > 5 MB)
#   3. delete: features > 120 d, ext/* > 30 d, bot/decisions-* > 60 d, llm_scratch > 2 d
#      (--aggressive: features > 60 d, ext > 14 d, decisions > 30 d, llm_scratch > 0 d, reports > 90 d)
#   prices1m/, labels/, tokens/, evidence/, hyps/ are never deleted (small, archived forever).
set -uo pipefail
AGG=0; DRY=0
for a in "$@"; do case "$a" in --aggressive) AGG=1;; --dry-run) DRY=1;; esac; done

D="${QD_DATA:-$HOME/qdesk/data}"
D="$(cd "$D" 2>/dev/null && pwd)" || { echo "rotate: data dir missing: ${QD_DATA:-$HOME/qdesk/data}"; exit 0; }
case "$D" in "$HOME/qdesk/data"*|/tmp/*|"${QD_ROTATE_ALLOW:-/nonexistent}"*) ;;
  *) echo "rotate: refusing to run outside ~/qdesk/data ($D)"; exit 1;; esac

today=$(date -u +%Y%m%d)
FEAT_D=120; EXT_D=30; DEC_D=60; SCR_D=2; LOG_B=20000000; REP_D=100000
if [ "$AGG" = 1 ]; then FEAT_D=60; EXT_D=14; DEC_D=30; SCR_D=0; LOG_B=5000000; REP_D=90; fi
cut() { date -u -d "$1 days ago" +%Y%m%d; }   # filename-date cutoff (lexical compare works for YYYYMMDD)
say() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
do_rm() { if [ "$DRY" = 1 ]; then say "would delete $1"; else rm -rf -- "$1" && say "deleted $(basename "$1")"; fi; }

# 1. recorder day files -> gzip (never today's: it is still being written)
for f in "$D"/features/live_*.csv; do
  [ -f "$f" ] || continue
  d="$(basename "$f" .csv)"; d="${d#live_}"
  [ "$d" \< "$today" ] || continue
  out="$D/features/day_$d.csv.gz"; [ -e "$out" ] && out="$D/features/live_$d.csv.gz"
  if [ "$DRY" = 1 ]; then say "would gzip $(basename "$f") -> $(basename "$out")"; continue; fi
  if gzip -6 -c "$f" > "$out.tmp" && mv -f "$out.tmp" "$out"; then rm -f -- "$f"; say "compressed $(basename "$f") -> $(basename "$out")"; else rm -f -- "$out.tmp"; say "gzip FAILED $(basename "$f")"; fi
done

# 2. logs
for l in "$D"/state/*.log; do
  [ -f "$l" ] || continue
  if [ "$(stat -c %s "$l")" -gt "$LOG_B" ]; then
    if [ "$DRY" = 1 ]; then say "would trim $(basename "$l")"; continue; fi
    tail -n 20000 "$l" > "$l.tmp" && cat "$l.tmp" > "$l" && rm -f "$l.tmp" && say "trimmed $(basename "$l")"
  fi
done

# 3. retention by the date in the filename (features, decisions, reports) or by mtime (ext, llm_scratch)
fc=$(cut $FEAT_D); dc=$(cut $DEC_D); rc=$(cut $REP_D)
for f in "$D"/features/day_*.csv.gz "$D"/features/live_*.csv.gz; do
  [ -f "$f" ] || continue; b="$(basename "$f")"; d="${b#day_}"; d="${d#live_}"; d="${d%%.*}"
  [[ "$d" =~ ^[0-9]{8}$ ]] && [ "$d" \< "$fc" ] && do_rm "$f"
done
for f in "$D"/bot/decisions-*.jsonl.gz "$D"/bot/decisions-*.settled.jsonl.gz; do
  [ -f "$f" ] || continue; b="$(basename "$f")"; d="${b#decisions-}"; d="${d%%.*}"
  [[ "$d" =~ ^[0-9]{8}$ ]] && [ "$d" \< "$dc" ] && do_rm "$f"
done
if [ "$AGG" = 1 ]; then
  for f in "$D"/reports/*; do
    [ -f "$f" ] || continue; d="$(basename "$f" | grep -oE '[0-9]{8}' | head -1)"
    [ -n "$d" ] && [ "$d" \< "$rc" ] && do_rm "$f"
  done
fi
if [ -d "$D/ext" ]; then
  while IFS= read -r f; do [ -n "$f" ] && do_rm "$f"; done < <(find "$D/ext" -type f -mtime +"$EXT_D" ! -name 'validation_*.json' 2>/dev/null)
fi
for s in "$D/llm_scratch" "$D/state/llm_scratch"; do
  [ -d "$s" ] || continue
  while IFS= read -r f; do [ -n "$f" ] && do_rm "$f"; done < <(find "$s" -mindepth 1 -maxdepth 1 -mtime +"$SCR_D" 2>/dev/null)
done

say "rotate done aggressive=$AGG dry=$DRY data=$(du -sh "$D" 2>/dev/null | cut -f1) free=$(df -h "$D" | tail -1 | awk '{print $4}')"
