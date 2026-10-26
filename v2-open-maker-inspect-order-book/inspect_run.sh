#!/usr/bin/env bash
# inspect_run.sh - run inspect_lab.py over a list of recorded BME days (each pipeline = gzip + mawk + python).
#   P=2 bash inspect_run.sh 20260926 20260927 ...
# Output: inspect_out/day_<YYYYMMDD>.json + .log ; marker inspect_out/ALL-DONE
set -u
cd ~/v2-open-maker-inspect-order-book
BME=~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme
TOK=~/pm/tokens_btc5m_range.json; ASSETS=~/pm/tokens_btc5m_range_assets.txt
mkdir -p inspect_out
one_day() {
  d="$1"
  [ -s "inspect_out/day_$d.json" ] && { echo "skip $d (done)"; return; }
  echo "[$(date -u +%H:%M:%S)Z] start $d"
  ( nice -n 15 gzip -cdf "$BME/events_$d.csv.gz" 2>/dev/null ) \
   | nice -n 15 mawk -F, 'NR==FNR{a[$1]=1; next} (($4 in a) && ($2=="price_change" || $2=="trade" || $2=="bba"))' "$ASSETS" - \
   | nice -n 15 python3 inspect_lab.py "$TOK" --lat 0.1 --out "inspect_out/day_$d.json" 2> "inspect_out/day_$d.log"
  echo "[$(date -u +%H:%M:%S)Z] done $d rc=$?"
}
export -f one_day; export BME TOK ASSETS
printf '%s\n' "$@" | xargs -P "${P:-2}" -I{} bash -c 'one_day {}'
touch inspect_out/ALL-DONE
echo "[$(date -u +%H:%M:%S)Z] ALL-DONE"
