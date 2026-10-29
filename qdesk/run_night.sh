#!/usr/bin/env bash
# run_night.sh — qdesk stage runner (INTERFACES.md §6).
#
#   bash run_night.sh <stage> [--date YYYYMMDD] [--dry-run] [--no-llm] [--days D1 D2 ...] [--hyps file] [--force-threshold N]
#
# stages: scout | quant | builder | brief | weekly | all      (all = scout -> llm/hypothesize -> quant -> builder -> brief)
#         (hidden: hypothesize, so one step of "all" can be re-run by hand)
#
# - QD_ROOT  = parent of this package dir (C:\g\tlfix locally, ~ on twapvm); every script imports "from qdesk...".
# - QD_DATA  = env or ~/qdesk/data.
# - python   = ~/qdesk/.venv/bin/python if present, else python3 (else python).
# - --dry-run sets LAB_NO_TELEGRAM=1 (no Telegram) and QD_DRY_RUN=1; the flag is also passed to every entry point.
# - every stage appends "[iso] stage=<s> start/end rc=.. dur=..s" to data/state/<stage>.log.
# - exit 0 on success (NOTHING_PASSED is a success), exit 2 on the first stage failure after writing
#   data/state/hb_<stage>.json ok=false (qdesk.common.tg.heartbeat; fallback: the JSON is written directly)
#   and one warn-class alert.
# - never calls sudo or systemctl (deploy/install.sh prints those lines for the owner).
set -uo pipefail

QD_PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QD_ROOT="${QD_ROOT:-$(dirname "$QD_PKG")}"
QD_DATA="${QD_DATA:-$HOME/qdesk/data}"
export QD_ROOT QD_DATA
export PYTHONPATH="$QD_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

if [ -x "$HOME/qdesk/.venv/bin/python" ]; then PY="$HOME/qdesk/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 1; }
[ $# -ge 1 ] || usage
STAGE="$1"; shift
case "$STAGE" in scout|quant|builder|brief|weekly|all|hypothesize) ;; -h|--help|*) usage ;; esac

# ---- flags (all are passed through unchanged; we only look at --dry-run / --no-llm / --date) -----------------
DRY=0; NOLLM=0; DATE=""
ARGS=("$@")
i=0
while [ $i -lt ${#ARGS[@]} ]; do
  case "${ARGS[$i]}" in
    --dry-run) DRY=1 ;;
    --no-llm)  NOLLM=1 ;;
    --date)    i=$((i+1)); DATE="${ARGS[$i]:-}" ;;
  esac
  i=$((i+1))
done
[ -n "$DATE" ] || DATE="$(date -u +%Y%m%d)"
if [ "$DRY" = 1 ]; then export LAB_NO_TELEGRAM=1 QD_DRY_RUN=1; fi

STATE="$QD_DATA/state"
mkdir -p "$STATE" "$QD_DATA/hyps" "$QD_DATA/evidence" "$QD_DATA/bot" "$QD_DATA/reports" 2>/dev/null || true
iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

entry_of() {
  case "$1" in
    scout)       echo "scout/evidence.py" ;;
    hypothesize) echo "llm/hypothesize.py" ;;
    quant)       echo "quant/night.py" ;;
    builder)     echo "bot/builder.py" ;;
    brief)       echo "report/brief.py" ;;
    weekly)      echo "report/weekly.py" ;;
  esac
}

# write hb_<stage>.json ok=false + warn alert; never fails the runner itself
fail_heartbeat() {
  local st="$1" rc="$2" note="stage $1 exited rc=$2 (date $DATE)"
  QD_STAGE="$st" QD_NOTE="$note" "$PY" - <<'PYEOF' 2>/dev/null || true
import os, sys, json, time
sys.path.insert(0, os.environ["QD_ROOT"])
st, note = os.environ["QD_STAGE"], os.environ["QD_NOTE"]
try:
    from qdesk.common import tg
    tg.heartbeat(st, False, note)
    try: tg.alert(f"stage_fail_{st}", f"qdesk stage {st} FAILED: {note}", cls="warn", source="run_night")
    except Exception as e: print(f"alert failed: {e.__class__.__name__}")
except Exception as e:
    # fallback: write the heartbeat JSON directly (same shape as INTERFACES §4)
    p = os.path.join(os.environ["QD_DATA"], "state", f"hb_{st}.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump({"ts": int(time.time()), "stage": st, "ok": False, "note": note + f" (tg unavailable: {e.__class__.__name__})"}, open(tmp, "w"), indent=1)
    os.replace(tmp, p)
PYEOF
}

run_stage() {
  local st="$1" entry script t0 t1 rc
  entry="$(entry_of "$st")"; script="$QD_PKG/$entry"
  local log="$STATE/$st.log"
  if [ ! -f "$script" ]; then
    echo "[$(iso)] stage=$st MISSING $script" | tee -a "$log" >&2
    fail_heartbeat "$st" 127; return 127
  fi
  echo "[$(iso)] stage=$st start date=$DATE dry=$DRY no_llm=$NOLLM py=$PY args: ${ARGS[*]:-}" >> "$log"
  t0=$(date +%s)
  ( cd "$QD_ROOT" && "$PY" "$script" "${ARGS[@]}" ) >> "$log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "[$(iso)] stage=$st end rc=$rc dur=$((t1-t0))s" >> "$log"
  echo "stage=$st rc=$rc dur=$((t1-t0))s log=$log"
  if [ $rc -ne 0 ]; then fail_heartbeat "$st" "$rc"; fi
  return $rc
}

overall=0
if [ "$STAGE" = "all" ]; then
  seq=(scout)
  [ "$NOLLM" = 1 ] || seq+=(hypothesize)
  seq+=(quant builder brief)
  T0=$(date +%s)
  echo "[$(iso)] stage=all start date=$DATE seq=${seq[*]}" >> "$STATE/all.log"
  for st in "${seq[@]}"; do
    run_stage "$st" || { overall=$?; break; }
  done
  echo "[$(iso)] stage=all end rc=$overall dur=$(( $(date +%s) - T0 ))s" >> "$STATE/all.log"
else
  run_stage "$STAGE" || overall=$?
fi

if [ "$overall" -ne 0 ]; then
  echo "run_night: FAILED ($STAGE rc=$overall) — see $STATE/*.log" >&2
  exit 2
fi
exit 0
