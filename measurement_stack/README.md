# Measurement stack (read-only research)

Built 2026-09-27 from `HANDOFF-PROMPT.md`. Nothing here places an order or holds a key.
Public market data only, low request rates, no authenticated surface.

Lives on `twapvm` in `~/arena-ai-copy btc-5-minute workspace/measurement_stack/`
(shortcut `~/mstack`). Source of truth for the code: this folder, mirrored in the repo.

## What runs

| unit | kind | what it does |
|---|---|---|
| `ms-us-recorder.service` | daemon | records the US venue's BTC Up/Down 15m + 1h rounds (anonymous websocket), samples 4 exchange order books at 1 Hz, computes a BRTI-proxy TWAP60 at every boundary, captures the venue's settlement, writes `rounds.csv` with OK / DIVERGENCE |
| `ms-nws-watch.service` | daemon | watches NWS forecast updates and station observations for the 5 priced cities, keeps a live quote feed on all 60 temperature buckets, writes bucket-sum verdicts and the reaction-lag table |
| `ms-regime-watch.service` | daemon | hashes 12 rule sources every 10 min (fees, tick, taker delay, docs, US legal documents, US config store, weather rule text, US round types) |
| `ms-health.timer` | every 5 min | heartbeat + data-freshness check, urgent Telegram alerts only |
| `ms-history-refresh.timer` | 00:10 UTC | refreshes the 7-day price corpus, extends the deep outcome corpora, chain-verifies new rounds |
| `ms-rotate.timer` | 01:05 UTC | compresses completed daily files, deletes nothing |
| `ms-daily-report.timer` | 07:20 UTC | re-runs the 7-day backtest, the proxy validation and the mispricing monitors, writes `reports/DAILY-REPORT-<date>.md`, sends ONE Telegram summary |

## Folders

```
backtest7d/      backtest7d.py, cache/ (labels), out/backtest from <today> to <earliest>/
history_tools/   chain_relabel.py, history_backfill*.py, history/ (corpora), history-refresh.sh
us_measure/      us_round_recorder.py, nws_watch.py, validation_report.py, us_rounds/, weather/
regime_watch/    regime_watch.py, regime_state.json, snapshots/, changes.jsonl
monitors/        mispricing.py, out/
report/          daily_report.py, stack_health.py
reports/         DAILY-REPORT-YYYY-MM-DD.md
common/          labcommon.py (rate-limited Telegram, heartbeat, gzip stream)
state/           heartbeats, alerts.json, alerts.log, timer logs
deploy/          systemd units + timers, rotate.sh
```

## Daily operations

```bash
systemctl is-active ms-us-recorder ms-nws-watch ms-regime-watch
systemctl list-timers 'ms-*'
cat ~/mstack/reports/DAILY-REPORT-$(date -u +%F).md
tail -n 20 ~/mstack/state/alerts.log
python3 ~/mstack/regime_watch/regime_watch.py --status
python3 ~/mstack/us_measure/validation_report.py      # proxy vs venue, any time
python3 ~/mstack/backtest7d/backtest7d.py --no-fetch  # re-score from cache
```

Stop a recorder without removing it: `touch ~/mstack/us_measure/KILL` (both recorders idle,
they keep running and keep their heartbeat). Remove the file to resume.

## Telegram rules

Same thread as the fleet, own rate limiter: same alert at most once per 6 h, at most 6
messages per hour in total, one "muted" notice when the cap is hit, everything also written
to `state/alerts.log`. Set `LAB_NO_TELEGRAM=1` for test runs.

| alert | meaning |
|---|---|
| 🚨 REGIME-CHANGE | a critical rule source changed: fees, tick, taker delay, weather station or source, US round types, US legal documents |
| ⚠ regime watcher BLIND | a source failed 5 cycles in a row; its silence is not evidence |
| 🚨 service not active / heartbeat stale | a daemon is dead or its loop stopped |
| 🚨 US recorder: no venue frame | the anonymous feed changed or blocked this IP |
| ⚠ fewer than 3 constituent books | the BRTI proxy is degraded |
| ⚠ US round divergence | proxy and venue disagree on a round whose gap was at least 2 bps |
| 🔎 weather bucket set below 0.985 | the six asks sum below 1; measurement only |
| 🔎 bucket below the running high still bid | a bucket that can no longer win still has a bid |
| 📋 daily report | one message a day |

Information-class changes (public docs, US config store, US series list, incentive docs) are
written to `regime_watch/changes.jsonl` and listed in the daily report, never pinged.

## Standing constraints

Measure, never attack. No probing of authenticated surfaces, no promo or bonus interaction,
no schema brute-forcing, no endpoint hammering. No real orders until the user re-approves
after seeing backtest and validation results. S1 stays hard-blocked. The decompiled app
archives are evidence and are not modified.
