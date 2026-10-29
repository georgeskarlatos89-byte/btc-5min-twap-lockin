#!/usr/bin/env bash
# build_days.sh - build the per-second feature store from recorded BME days (style of inspect_run.sh).
#   P=3 bash features/build_days.sh 20260926 20260927 ...        (default P=3)
#   TOK15=~/qdesk/data/tokens/tokens_btc15m_range.json  -> also 15m rounds (assets added to the mawk filter)
# Each day = gzip -cd | mawk (spot_tick, twap_tick + book/price_change/bba/trade of the listed assets) | python3.
# Output: $QD_DATA/features/day_<YYYYMMDD>.csv.gz + logs in $QD_DATA/state/features_<day>.log; marker ALL-DONE.
# Skips days already built. Read-only on the BME folder.
set -u
QD=${QD_ROOT:-$HOME}/qdesk
DATA=${QD_DATA:-$HOME/qdesk/data}
BME=${BME:-$HOME/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme}
TOK=${TOK:-$HOME/pm/tokens_btc5m_range.json}; ASSETS=${ASSETS:-$HOME/pm/tokens_btc5m_range_assets.txt}
TOK15=${TOK15:-}
PY=${PY:-$( [ -x "$QD/.venv/bin/python3" ] && echo "$QD/.venv/bin/python3" || echo python3 )}
mkdir -p "$DATA/features" "$DATA/state"
ALLASSETS="$DATA/state/features_assets.txt"
cat "$ASSETS" > "$ALLASSETS"
if [ -n "$TOK15" ] && [ -s "$TOK15" ]; then
  "$PY" -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1])).keys()))' "$TOK15" >> "$ALLASSETS"
fi
one_day() {
  d="$1"
  out="$DATA/features/day_$d.csv.gz"
  [ -s "$out" ] && { echo "skip $d (done)"; return; }
  [ -s "$BME/events_$d.csv.gz" ] || { echo "skip $d (no BME file)"; return; }
  echo "[$(date -u +%H:%M:%S)Z] start $d"
  extra=""; [ -n "$TOK15" ] && extra="--tokens15 $TOK15"
  ( nice -n 15 gzip -cdf "$BME/events_$d.csv.gz" 2>/dev/null ) \
   | nice -n 15 mawk -F, 'NR==FNR{a[$1]=1; next} ($2=="spot_tick" || $2=="twap_tick" || (($4 in a) && ($2=="price_change" || $2=="trade" || $2=="bba" || $2=="book")))' "$ALLASSETS" - \
   | nice -n 15 "$PY" "$QD/features/bme_to_features.py" "$TOK" --day "$d" --out "$out" $extra 2> "$DATA/state/features_$d.log"
  echo "[$(date -u +%H:%M:%S)Z] done $d rc=$?"
}
export -f one_day; export BME TOK TOK15 ALLASSETS DATA QD PY
printf '%s\n' "$@" | xargs -P "${P:-3}" -I{} bash -c 'one_day {}'
touch "$DATA/features/ALL-DONE"
echo "[$(date -u +%H:%M:%S)Z] ALL-DONE"
