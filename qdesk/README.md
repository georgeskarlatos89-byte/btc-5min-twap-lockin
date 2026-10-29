# qdesk — the overnight quant desk (paper only)

qdesk is a small, unattended research desk for Polymarket BTC 5-minute / 15-minute up-down markets. Every night on
twapvm it (1) gathers evidence, (2) asks the Claude Code CLI for hypotheses written in a tiny rule language, (3) backtests
them with a frozen, pre-registered scorecard, (4) hands at most one survivor to a paper bot, and (5) sends a morning brief
to Telegram thread 600. It never trades real money; v1 has no live order path at all (`DRY_RUN=true` is enforced in code).

**Honest expectation: most nights end with `NOTHING_PASSED`. That is the desk working, not failing.** Every rule family
we tested before (resting-bid makers, 34 order-book rules, stale-ask takers, the Thorp vol edge, the flat-book gate, the
EXP-D hedge) is already dead and listed in `prior_evidence.json`; a new rule must beat fees, a Bonferroni-adjusted CI,
five rolling windows, a Monte-Carlo drawdown and a skeptic audit before the bot even paper-trades it.

The binding contract for every module is **[INTERFACES.md](INTERFACES.md)** — paths, the feature store, the rule DSL,
nightly file shapes, shared functions, the stage runner, engines, the bot and the timers. Change it first, code second.

## The nightly flow (UTC, system timers)

| time | unit | what |
|---|---|---|
| 00:50 | `qd-rotate` | gzip yesterday's recorder day file, trim logs, delete old features/ext/decisions/scratch (`deploy/rotate.sh`) |
| 01:00 | `qd-scout` | `scout/evidence.py` → `data/evidence/evidence_YYYYMMDD.json` (rounds, chain labels, 1-min prices, Moon Dev, Hyperliquid, wallets, regime, macro) — a failed source never stops the night |
| 03:00 | `qd-quant` | `llm/hypothesize.py` (≥ 10 DSL rules, or `quant/library.py` when the CLI is missing) → `quant/night.py`: preregistration **before** any evaluation, tier-1 engine on the feature store, tier-2 event replay for the top rules on the frozen holdout, scorecard, skeptic |
| 05:30 | `qd-builder` | `bot/builder.py` → `data/bot/gate_pass.json` + `active_rule.json` only when a rule passed |
| 07:30 | `qd-brief` | `report/brief.py` → one Telegram message (≤ 4,000 chars, thread 600) + markdown in `data/reports/` |
| Sun 08:00 | `qd-weekly` | `report/weekly.py` → paper vs backtest, Brier, holdout ledger, proposed `prior_evidence.json` additions |
| every 15 min | `qd-health` | `deploy/disk_guard.py` → disk guard, recorder/bot heartbeats, daily `claude` auth probe |
| always | `qd-recorder` | `recorder/feat_recorder.py` writes today's per-second feature rows (the only true nightly holdout) |
| always | `qd-bot` | `bot/qbot.py` paper bot; OBSERVE mode unless a fresh gate_pass matches the rule/code/corpus hashes |

`bash run_night.sh all` runs scout → hypothesize → quant → builder → brief in one go (used by hand, not by the timers).

## File map

```
qdesk/
  INTERFACES.md          the contract (read first)          prior_evidence.json   dead families the skeptic rejects
  run_night.sh           stage runner (§6)                  requirements.txt      websockets, python-dotenv, numpy
  common/   schemas.py dsl.py fees.py settle.py stats.py tg.py hashing.py sanitize.py   (agent A)
  features/ features.py (FeatureState, FEATURE_COLUMNS) bme_to_features.py              (agent C)
  quant/    night.py prereg.py rule_engine_1s.py rule_replay_events.py score.py library.py holdout_ledger.py (agent C)
  scout/    evidence.py gamma_rounds.py tokens_range.py prices_archive.py moondev_pull.py hl_pull.py wallet_scan.py macro.py (agent B)
  recorder/ feat_recorder.py                                                             (agent B)
  llm/      client.py hypothesize.py skeptic.py prompts/                                 (agent D)
  report/   brief.py weekly.py                                                           (agent D)
  bot/      qbot.py paper_fill.py gate.py decay.py builder.py test_qbot.py               (agent E)
  deploy/   qd-*.service qd-*.timer rotate.sh disk_guard.py install.sh                  (agent F)
  tests/    conftest.py test_*.py validate_external.py fixtures/                         (agent F)
data/ (VM only, never in git): tokens/ labels/ features/ prices1m/ ext/ evidence/ hyps/ bot/ reports/ state/
```

Repo root is the **parent** of this folder (`C:\g\tlfix` locally, `~` on the VM) so every script imports `from qdesk.common import ...`.

## Running stages by hand

```bash
cd ~                                   # repo root on the VM (C:\g\tlfix locally)
bash qdesk/run_night.sh scout   --dry-run                 # no network, no Telegram, fixtures only
bash qdesk/run_night.sh quant   --no-llm --hyps qdesk/tests/fixtures/hyps_3.json --days 20260926 20260927
bash qdesk/run_night.sh quant   --force-threshold 26 --dry-run   # the forced "nothing passed" night
bash qdesk/run_night.sh builder --date 20261030
bash qdesk/run_night.sh brief   --dry-run                 # brief goes to data/state/alerts.log instead of Telegram
bash qdesk/run_night.sh weekly
bash qdesk/run_night.sh all     --no-llm
python3 qdesk/deploy/disk_guard.py --dry-run
bash qdesk/deploy/rotate.sh --dry-run
python3 qdesk/tests/validate_external.py --name binance_1s --date 20260926     # external data, by hand only
```

Flags (`INTERFACES §6`): `--date YYYYMMDD`, `--dry-run`, `--no-llm`, `--days D1 D2 ...`, `--hyps file`, `--force-threshold N`.
Each stage appends timing to `data/state/<stage>.log`, writes `data/state/hb_<stage>.json`, and the runner exits 2 on a
stage failure after a warn-class alert. The runner never calls `sudo` or `systemctl`.

Tests: `pytest qdesk/tests -q` (stdlib + numpy; modules that have not landed are skipped, the recorder parity test is
xfailed until the recorder exposes a message handler).

## Owner's manual steps (never through chat; `bash deploy/install.sh` prints all of this with the exact paths)

1. **twapvm only** (`ssh twapvm`, user `ubuntupolymarket3`). polyvps is never touched.
2. `mkdir -p ~/qdesk/data/{tokens,labels,features,prices1m,ext/moondev,ext/hl,ext/wallets,evidence,hyps,bot,reports,state} ~/qdesk/untrusted`
3. venv: `python3 -m venv ~/qdesk/.venv && ~/qdesk/.venv/bin/pip install -r ~/qdesk/requirements.txt` (`run_night.sh`
   and the recorder/bot units use it automatically).
4. Secrets, copied **file to file**: `grep -h '^MOONDEV_API_KEY=' <existing bot .env> > ~/qdesk/.env && chmod 600 ~/qdesk/.env`.
   Only `llm/client.py` and `scout/moondev_pull.py` read it. Confirm `grep -c TELEGRAM_THREAD_ID=600 ~/s1_harness/telegram.env` prints 1.
5. node 20 + Claude Code CLI + login (subscription, no API key):
   `curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs`,
   `npm config set prefix ~/.npm-global && npm install -g @anthropic-ai/claude-code`, `export PATH=~/.npm-global/bin:$PATH`,
   `claude login` (headless URL/code flow), then `claude -p "reply with OK" --output-format json`. The units carry
   `~/.npm-global/bin` in PATH; `qd-health` repeats the probe once a day and alerts when the login expires.
6. Step 0 data: `~/qdesk/.venv/bin/python ~/qdesk/scout/tokens_range.py --variant 15m`, then the **19-day feature build**
   (BME days 20260926..20261014, ≈ 4 h at P=3, nice 15):
   `B=~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme; for d in 20260926 20260927 ... 20261014; do nice -n 15 ~/qdesk/.venv/bin/python ~/qdesk/features/bme_to_features.py ~/pm/tokens_btc5m_range.json --in $B/events_$d.csv.gz --day $d --out ~/qdesk/data/features/day_$d.csv.gz; done`
   (or agent C's `features/build_days.sh`, which adds the mawk pre-filter and P=3; see `bme_to_features.py --help`).
7. Units: `sudo cp ~/qdesk/deploy/qd-*.service ~/qdesk/deploy/qd-*.timer /etc/systemd/system/ && sudo systemctl daemon-reload`,
   `sudo systemctl enable --now qd-rotate.timer qd-scout.timer qd-quant.timer qd-builder.timer qd-brief.timer qd-weekly.timer qd-health.timer`,
   `sudo systemctl enable --now qd-recorder.service qd-bot.service`, then `systemctl list-timers 'qd-*'`.
8. Approve the frozen thresholds and the fit/test day split before the first real night; extend `scout/macro.py` monthly.
9. No Alpaca, no real money; any LIVE path needs a new plan and a written go.

## What to expect

- `NOTHING_PASSED` on most nights, with the brief explaining which criterion killed the best rule and its own
  "why this could fail" paragraph. A `CANDIDATE:<id>` verdict only means the paper bot will *paper*-trade it at $5 max per
  trade on a $100 paper book; after 20 settled paper trades with avg R below half the backtest it is flagged DECAYING,
  after 40 it is RETIRED automatically.
- Every number in the brief is labelled MEASURED or ESTIMATE; maker fills are always ESTIMATE (1-s queue model).
- Nightly runtime ≈ 20-40 min (tier 2 ≤ 2 h only when a rule reaches ≥ 18 in tier 1); disk ≤ 2 GB with rotation.
- The LLM runs on the Claude subscription through the CLI; `data/state/llm_cost.csv` logs seconds and characters, not dollars.

## Kill / stop

- **Idle the bot now:** `touch ~/qdesk/KILL` (the bot stops evaluating; the recorder keeps recording). Remove the file to resume.
- **Stop everything** (owner, printed by `install.sh`):
  `sudo systemctl stop qd-bot.service qd-recorder.service qd-rotate.timer qd-scout.timer qd-quant.timer qd-builder.timer qd-brief.timer qd-weekly.timer qd-health.timer`
  and `sudo systemctl disable ...` the same list to keep them down across reboots.
- **Disk guard:** `qd-health` runs `rotate.sh --aggressive`, writes `data/state/SKIP_TIER2` and alerts once when the data
  dir exceeds 1.8 GB or free space drops under 3 GB.
- Logs: `~/qdesk/data/state/<unit>.log` and `<stage>.log`; heartbeats `hb_<stage>.json`; alerts `alerts.log`.
