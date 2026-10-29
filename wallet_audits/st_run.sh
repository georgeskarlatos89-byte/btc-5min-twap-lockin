#!/usr/bin/env bash
# st_run.sh - run signal_taker_replay.py over recorded BME days.   P=2 bash st_run.sh 20260926 ... 20261014
set -u
cd ~/wallets
BME=~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme
mkdir -p st_out
one_day() {
  d="$1"
  [ -s "st_out/day_$d.json" ] && { echo "skip $d (done)"; return; }
  echo "[$(date -u +%H:%M:%S)Z] start $d"
  ( nice -n 15 gzip -cdf "$BME/events_$d.csv.gz" 2>/dev/null ) \
   | nice -n 15 mawk -F, 'NR==FNR{a[$1]=1; next} ($2=="spot_tick" || $2=="twap_tick" || (($4 in a) && ($2=="price_change" || $2=="bba")))' ~/pm/tokens_btc5m_range_assets.txt - \
   | nice -n 15 python3 signal_taker_replay.py ~/pm/tokens_btc5m_range.json --out "st_out/day_$d.json" 2> "st_out/day_$d.log"
  echo "[$(date -u +%H:%M:%S)Z] done $d rc=$?"
}
export -f one_day; export BME
printf '%s\n' "$@" | xargs -P "${P:-2}" -I{} bash -c 'one_day {}'
touch st_out/ALL-DONE
echo "[$(date -u +%H:%M:%S)Z] ALL-DONE"
