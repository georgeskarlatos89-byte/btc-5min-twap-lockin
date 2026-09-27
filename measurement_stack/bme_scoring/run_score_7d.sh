#!/usr/bin/env bash
# run_score_7d.sh - full-dataset BME score in bounded memory, one decompression per file.
# Read-only on the BME data. Writes only into this folder and the two report files.
set -uo pipefail
cd "$(dirname "$0")"
BME="$HOME/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme"
WORK="$PWD/work"; mkdir -p "$WORK"; : > "$WORK/ticks.csv"; : > "$WORK/s1_parts.txt"
for f in $(ls "$BME"/events_*.csv* | sort); do
  t0=$(date +%s)
  gzip -cdf "$f" 2>/dev/null | tee >(grep -F ',spot_tick,' >> "$WORK/ticks.csv") | mawk -f s1_bursts.awk | sed "s|^|$(basename "$f") |" >> "$WORK/s1_parts.txt"
  echo "[$(date -u +%H:%M:%SZ)] $(basename "$f") done in $(( $(date +%s) - t0 )) s: $(tail -n 1 "$WORK/s1_parts.txt")"
done
sync; sleep 1
python3 -u bme_score_stream.py --min-rounds 100 --ticks "$WORK/ticks.csv" --s1parts "$WORK/s1_parts.txt"
python3 - <<'EOF'
import json, os, sys
sys.path.insert(0, os.path.expanduser("~/mstack/common"))
import labcommon
r = json.load(open(os.path.expanduser("~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/bme_score_report_7d.json")))
s = r["signals"]; a = s["S3_imbalance"]["all"]; q = s["S2_pretick_pulls"]["by_quintile_of_pretick_pulls"]
labcommon.alert("bme_score_7d", "BME 7-day score finished (" + r["days"][0] + " to " + r["days"][-1] + ").\n"
    f"S3 depth imbalance, the only signal with a gate: {a.get('rounds_scored')} rounds, picks the winner {100 * (a.get('sign_accuracy') or 0):.1f}% of the time, "
    f"expected value {a.get('ev_per_share_post_fee')} per share after fees (gate: +0.02). Verdict: {s['S3_imbalance']['verdict']}.\n"
    f"S2 pulls before a tick: P(up) {q.get('Q1', {}).get('p_up')} in the lowest fifth vs {q.get('Q5', {}).get('p_up')} in the highest, so no direction signal.\n"
    f"S1: {s['S1_flicker']['batch_bursts_ge5_same_ms']:,} same-millisecond bursts (descriptive only).\n"
    "Report: bme_score_report_7d.md", "once", "bme_score")
EOF
