#!/usr/bin/env bash
# book_run.sh - match a wallet's BTC 5m fills against the recorded book for the listed days (3 at a time).
#   bash book_run.sh 20261003 20261009 20261013
cd ~/wallets
BME=~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme
one() {
  d="$1"
  ( nice -n 10 gzip -cdf "$BME/events_$d.csv.gz" 2>/dev/null ) \
   | nice -n 10 mawk -F, 'NR==FNR{a[$1]=1; next} ($2=="spot_tick" || $2=="twap_tick" || (($4 in a) && ($2=="price_change" || $2=="bba")))' ~/pm/tokens_btc5m_range_assets.txt - \
   | nice -n 10 python3 fills_vs_book.py ~/pm/tokens_btc5m_range.json "norm1e69/book/fills_$d.csv" --out "norm1e69/book/day_$d.json" 2> "norm1e69/book/day_$d.log"
  echo "[$(date -u +%H:%M:%S)Z] done $d rc=$?"
}
export -f one; export BME
printf '%s\n' "$@" | xargs -P 3 -I{} bash -c 'one {}'
touch norm1e69/book/ALL-DONE
