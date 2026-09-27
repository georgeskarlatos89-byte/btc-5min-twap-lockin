# Reconciliation (workspace vs repo vs VPS) and build order

Written 2026-09-27 after reading every file in the workspace (400 files), the repo
`btc-5min-twap-lockin` at commit `3edd40c`, and both VMs (read-only look).
Rule used: the repo's living records describe production reality, the workspace describes
measurement evidence. Every disagreement is listed, none is silently resolved.

## 1. What exists where

| item | workspace | repo | VPS (twapvm) | status |
|---|---|---|---|---|
| Polymarket doc mirrors (API reference 135, Predictions 56, Perps 26, Changelogs 4) + 9 root index files | yes | yes | reference copy | byte-identical, nothing to do |
| Moon Dev docs (31) + `MOONDEV 6+ KNOWLEDGE.MD` | yes | yes (`s3_feefarm/docs/`) | yes | identical |
| `BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD` | yes | yes | yes | identical |
| S1 harness: `twap_lockin_harness.py`, `trader.py`, services, playbook | OLD version (09-19/20) | patched version | patched, running | repo wins. Workspace copy lacks: proxy signing, live block, stall watchdog, closed=true scoring |
| S1 monitor, preflight, hypo check, rounds integrity, S9 watcher | no | yes | yes, running | production only |
| Strategies #2 #3 #4/#6 (S46) #5 #8 #10 | no (only the strategy memo + re-rank) | yes | yes, all DRY or read-only | production only |
| BME book recorder (1 s book state, 7 days on disk) | no | yes | yes, running | production only. Supersedes `maker_lab/book_recorder.py` |
| `maker_lab` history tools (`history_backfill*.py`, `chain_relabel.py`, `chain_verify.py`, `stratified_chain_audit.py`) + corpora (76,670 × 5m, 27,088 × 15m, 7-day price corpus) | yes | NO | NO | **missing in production, needed for deliverable 1** |
| `maker_lab` quoting simulator (`maker_sim.py`, `make_strategy.py`, `maker_report.py`) | yes | no | no | NOT deployed on purpose: S3/S46/S10 already run the maker family DRY, and this simulator prices fair value with the falsified full-round-average rule. Kept as reference |
| `regime_watch` (rule-change sentinel incl. US feeds, weather canary, updown sentinel) | yes | NO | NO (no `/opt` stack on either VM) | **missing, deliverable 4 is a first deploy, not an upgrade** |
| `app_lab/us_round_recorder.py`, `nws_watch.py` | yes (reference implementations + 13 min of data) | NO | NO | **missing, deliverables 2 and 3** |
| `ui_lab` (headless-browser UI vs API comparator, DevTools dumps) | yes | NO | NO | not in the build list. Findings kept; Chromium on the VPS not installed |
| `polymarket secrets` (news investigation + claim audit) | yes | NO | NO | background reading, verified parameters carried into the stack |
| Decompiled Android app (3 zips, 22,832 files) | yes | NO | NO | evidence archive, untouched per handoff §3 |
| Mispricing monitors, daily dashboard | described only | no | no | **deliverables 5 and 6, to build** |

## 2. Disagreements (stated, not smoothed over)

| # | workspace says | production reality | consequence |
|---|---|---|---|
| D1 | Runbook recommends `FORCE_LIVE=1` for S1 ("$10 incubation you approved") | S1's premise was falsified on live rounds; S1 is hard-blocked by `S1_LIVE_BLOCKED`, `FORCE_LIVE` cannot bypass it | S1 stays DRY. The runbook's §3 and §8.1 are obsolete |
| D2 | S1/maker-lab docs: settlement = Chainlink TWAP **averaged over the whole round** vs open ("70/70 at gap > 4 bps") | Living record #1: settlement = **end value** of the 60 s TWAP feed vs the open; on disagreement rounds end-value 25/25, full-average 0/25 | The 70/70 came from rounds where both rules agree. The 7-day backtest measures both rules against chain labels |
| D3 | HISTORY-FINDINGS: the public `outcome` (= sign(close − open) of the TWAP stream at the boundaries) is wrong vs chain on 27.5–35 % of sub-2 bp rounds | If settlement is end-value vs open, sign(close − open) should be RIGHT | Both cannot be fully true. Recomputed from the raw audit rows: past-results wrong 11/40, 14/40, 3/40, 1/40, 0/40 by gap bucket; gamma corpus wrong 0/200. So either the past-results boundary values are not the exact ticks settlement uses, or the rule is neither. Deliverable 1 tests this on 7 days with our own recorded ticks |
| D4 | Runbook paths `/opt/s1`, `/opt/regime_watch`, `/opt/maker_lab`, root-owned | Fleet convention: strategy-named folder in the service user's home + symlink, own systemd unit | New stack follows the fleet convention |
| D5 | "Burner wallet, ~$20 USDC" | Account 2 (proxy wallet, signature type 3) is the project's trading account; nothing here trades | No wallet is touched by this build |
| D6 | Handoff oracle table lists a `.com` 1 h updown product | The workspace's own audit (2026-09-26) found hourly Up/Down rounds discontinued on `.com`; the sentinel labels `1h`/`60m` never resolved | `.com` = 5m and 15m only. 1 h exists only on the US venue |
| D7 | `polymarket secrets/00 - INDEX.md` still cites "taker max 1.56 %, 100 % rebated" | Its own audit and live `feeSchedule`: rate 0.07 (1.75 % at 50¢), rebate 20 % | 0.07 and 20 % are used everywhere |
| D8 | `chain_audit_stratified.json` reports 40 of 40 wrong for every source in every bucket | The raw `.jsonl` next to it gives the real table (see D3) | The summary JSON is a broken artifact. The `gamma_fresh` column is always "down" because the script indexed a JSON string, not a list |
| D9 | Runbook: "regime-watch … upgrade an existing install" | Never installed anywhere | First deploy with a fresh baseline |
| D10 | Workspace telemetry ran in a sandbox at `/home/user/…` from Nairobi | VPS is in the Netherlands | Latency numbers in `US-VENUE-BASELINE.md` do not transfer; re-measured by the recorder |

## 3. Defects found by reading the reference code (fixed during productizing)

| file | defect | effect if shipped as is |
|---|---|---|
| `regime_watch.py` | US updown sentinel keeps its "already alerted" flag in memory only | one Telegram alert on every restart, forever |
| `regime_watch.py` | Telegram sender has no thread support and no rate limit | alerts land in the wrong chat and can storm |
| `regime_watch.py` | workspace log shows a `REGIME-CHANGE` on `sentinel-5m` one second after baseline while all three snapshots are byte-identical | false alarm source, to be reproduced and removed before alerts are enabled |
| `us_round_recorder.py` | session length fixed at 900 s from start, not aligned to the round grid | the first part of every new round is missed and the open reference is taken late |
| `us_round_recorder.py` | falls back to an instantaneous proxy price when the open TWAP was not captured | validation rows with a wrong reference |
| `us_round_recorder.py` | `settlement_poller` never receives any pending slug | second witness is dead code |
| `us_round_recorder.py` | raw frames append forever (5.3 MB per 13 min, about 600 MB a day) | disk growth without rotation |
| `nws_watch.py` | looks for a forecast period literally named "Today" | no forecast high for 3 of 5 cities in the recorded sample |
| `nws_watch.py` | `bucket_sums.csv` header has 6 names, rows have 7 values | malformed CSV |
| `nws_watch.py` | polls every 30 min and stamps the reaction clock at detection time | the lag table cannot measure lag to the actual NWS update |
| `maker_sim.py`, `ui_api_comparator.py`, `backtest_s1.py` | settle on gamma without `closed=true` and treat "not accepting orders" as final | stale-side labels, the exact error the chain corpus exists to remove |

## 4. Build order

1. **7-day backtest engine** (first, because its label-accuracy result decides how much the
   public sources can be trusted): chain labels + public labels + our own recorded ticks for
   the same rounds, S1 signals as actually generated in production, CSV outputs, report.
2. **History corpus tooling on the VPS**: deep gamma corpus, 7-day price corpus, chain relabel,
   daily refresh timer (feeds deliverable 1 every day).
3. **Regime watch**: fix the three defects, baseline, run quiet, then enable alerts.
4. **US round recorder + BRTI proxy**: grid-aligned sessions, gzip rotation, heartbeat,
   `rounds.csv` with OK/DIVERGENCE, validation report at 200 rounds.
5. **NWS watcher + bucket-sum scanner**: fixed forecast parser, fixed CSV, continuous bucket
   snapshots so a reaction lag can actually be measured.
6. **Mispricing monitors** (from recorder data): one-sided-book statistic by
   minute-to-resolution, market-vs-oracle divergence, `.com` vs US comparison on shared windows.
7. **One daily report** + a separate rate-limited Telegram sender (the shared `s1_monitor.py`
   belongs to other sessions and is not modified).

Nothing in this build places orders or holds keys. Public market data only, low request
rates, no authenticated surface, no promo or bonus interaction, decompiled archive untouched.
