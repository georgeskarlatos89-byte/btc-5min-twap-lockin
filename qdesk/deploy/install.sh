#!/usr/bin/env bash
# install.sh — PRINTS the owner's install steps for qdesk on twapvm. It runs nothing privileged itself
# (no sudo, no systemctl: those lines are for the owner to paste, per INTERFACES §10 / the plan's "by hand" list).
set -u
H=/home/ubuntupolymarket3; Q="$H/qdesk"; D="$(cd "$(dirname "$0")" && pwd)"
UNITS="qd-rotate qd-scout qd-quant qd-builder qd-brief qd-weekly qd-health"
cat <<EOF
############################################################################################
#  qdesk — owner install steps on twapvm (user ubuntupolymarket3). NOTHING below is executed
#  by this script. Paste the blocks you want. polyvps is never touched.
############################################################################################

# 0. code + data layout (repo root = \$HOME so 'from qdesk...' imports work; data is never in git)
mkdir -p $Q/data/{tokens,labels,features,prices1m,ext/moondev,ext/hl,ext/wallets,evidence,hyps,bot,reports,state} $Q/untrusted
chmod 700 $Q/untrusted

# 1. venv (websockets + python-dotenv + numpy; run_night.sh uses it automatically when present)
python3 -m venv $Q/.venv && $Q/.venv/bin/pip install -U pip && $Q/.venv/bin/pip install -r $Q/requirements.txt

# 2. secrets — copied FILE TO FILE, never pasted into a chat. chmod 600. Only llm/client.py and scout/moondev_pull.py read it.
#    grep -h '^MOONDEV_API_KEY=' <an existing bot .env> > $Q/.env      (and MOONDEV_BASE_URL= if that bot has one)
chmod 600 $Q/.env
grep -c TELEGRAM_THREAD_ID=600 $H/s1_harness/telegram.env     # must print 1 (thread 600 = "BTC-5-minute")

# 3. node 20 + Claude Code CLI + one interactive login (subscription; no API key). Headless: follow the printed URL/code.
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs
npm config set prefix $H/.npm-global && npm install -g @anthropic-ai/claude-code
export PATH=$H/.npm-global/bin:\$PATH && claude --version && claude login
claude -p "reply with OK" --output-format json | head -c 300      # auth probe; qd-health repeats this once a day

# 4. step 0 data (one-off, nice'd; ~4 h for the 19 BME days at P=3). Exact flags: see README "19-day feature build".
#    $Q/.venv/bin/python $Q/scout/tokens_range.py --variant 15m
#    per day: nice -n 15 $Q/.venv/bin/python $Q/features/bme_to_features.py $H/pm/tokens_btc5m_range.json --in <BME dir>/events_<d>.csv.gz --day <d> --out $Q/data/features/day_<d>.csv.gz

# 5. units (system units, pattern of measurement_stack/deploy/ms-daily-report.service). Logs: $Q/data/state/<unit>.log
sudo cp $D/qd-*.service $D/qd-*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now $(for u in $UNITS; do printf '%s.timer ' "$u"; done)
sudo systemctl enable --now qd-recorder.service          # live feature recorder (no keys, no orders)
sudo systemctl enable --now qd-bot.service               # paper bot, OBSERVE mode until a gate_pass exists (DRY_RUN=true enforced)
systemctl list-timers 'qd-*' --all
systemctl status qd-recorder qd-bot --no-pager | head -20

# 6. first manual runs (each stage once by hand, then read the log + heartbeat)
bash $Q/run_night.sh scout --dry-run && cat $Q/data/state/hb_scout.json
bash $Q/run_night.sh quant --no-llm --hyps $Q/tests/fixtures/hyps_3.json --force-threshold 26 --dry-run
$Q/.venv/bin/python $Q/deploy/disk_guard.py --dry-run

# 7. STOP / KILL (owner only)
touch $Q/KILL                                             # bot idles (no paper orders), recorder keeps recording
sudo systemctl stop qd-bot.service qd-recorder.service $(for u in $UNITS; do printf '%s.timer ' "$u"; done)
sudo systemctl disable qd-bot.service qd-recorder.service $(for u in $UNITS; do printf '%s.timer ' "$u"; done)
EOF
