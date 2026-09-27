#!/usr/bin/env bash
# finish_relabel.sh — one-time: wait for the full 5m chain relabel, then run the 15m corpus.
# The gamma corpora (*_long.csv) are NOT overwritten: the nightly refresh rewrites them from
# gamma, so the chain-verified corpus lives next to them as *_long.relabeled.csv and is
# regenerated from the checkpoint every night (seconds once the checkpoint is complete).
set -uo pipefail
cd "$(dirname "$0")"
while [ -f relabel_5m.pid ] && kill -0 "$(cat relabel_5m.pid)" 2>/dev/null; do sleep 60; done
echo "[$(date -u +%FT%TZ)] 5m relabel finished"; tail -n 12 relabel_5m.log
python3 -u chain_relabel.py --csv history/BTC_fifteen_long.csv --workers 3 > relabel_15m.log 2>&1
echo "[$(date -u +%FT%TZ)] 15m relabel finished"; tail -n 12 relabel_15m.log
python3 - <<'EOF'
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(".")), "common"))
sys.path.insert(0, "../common")
import labcommon
a = json.load(open("history/BTC_fiveminute_long.relabel_stats.json")); b = json.load(open("history/BTC_fifteen_long.relabel_stats.json"))
labcommon.alert("relabel_done", "✅ Full on-chain relabel finished.\n"
    f"5m corpus: {a['chain_verified']} of {a['rows']} rounds verified on Polygon, {a['labels_changed']} labels differed from gamma.\n"
    f"15m corpus: {b['chain_verified']} of {b['rows']} verified, {b['labels_changed']} differed.\n"
    "Chain-verified corpora: history_tools/history/*_long.relabeled.csv (refreshed nightly).", "once", "history")
EOF
