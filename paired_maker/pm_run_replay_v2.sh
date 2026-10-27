#!/usr/bin/env bash
# pm_run_replay.sh - run pm_replay_v2.py over a list of BME days, 2 days at a time (each pipeline = zcat + mawk + python).
#   bash pm_run_replay.sh "0.0 0.05 0.1 0.2 1.0" 20260926 20260927 ...
# Output: ~/pm/replay_out_v2/day_<YYYYMMDD>.json + .log ; DONE marker ~/pm/replay_out_v2/ALL-DONE
set -u
cd ~/pm
BME=~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme
LATS="$1"; shift
mkdir -p replay_out_v2
one_day() {
  d="$1"
  [ -s "replay_out_v2/day_$d.json" ] && { echo "skip $d (done)"; return; }
  echo "[$(date -u +%H:%M:%S)Z] start $d"
  ( nice -n 15 gzip -cdf "$BME/events_$d.csv.gz" 2>/dev/null ) \
   | nice -n 15 mawk -F, 'NR==FNR{a[$1]=1; next} ($2=="spot_tick" || $2=="twap_tick" || (($4 in a) && ($2=="price_change" || $2=="trade" || $2=="bba")))' tokens_btc5m_range_assets.txt - \
   | nice -n 15 python3 pm_replay_v2.py tokens_btc5m_range.json --lat $LATS --out "replay_out_v2/day_$d.json" 2> "replay_out_v2/day_$d.log"
  echo "[$(date -u +%H:%M:%S)Z] done $d rc=$?"
}
export -f one_day; export BME LATS
printf '%s\n' "$@" | xargs -P "${P:-2}" -I{} bash -c 'one_day {}'
touch replay_out_v2/ALL-DONE
echo "[$(date -u +%H:%M:%S)Z] ALL-DONE"
