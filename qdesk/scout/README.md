# qdesk scout/ and recorder/ (agent B)

Every module: Python 3.12, stdlib + `websockets` + `python-dotenv`; `--date YYYYMMDD` and `--dry-run` on every CLI;
`sys.path.insert(0, <repo root>)` then `from qdesk.scout import ...`; writers use tmp + `os.replace`; no secrets printed;
network calls go through `sc_util.http_json` (UA header on every request, 5 retries with backoff, 0.2 s pacing, 429 slept
longer, 401/403/404 not retried, never raises). Data dir = `QD_DATA` (default `~/qdesk/data`). Endpoint parameter names
below were verified live on 2026-10-29 from this PC (and the VM for Moon Dev, which answered 429/401 - see moondev_pull).

**sc_util.py** - shared helpers: data paths, `http_json(url, headers, data) -> (ok, obj, note)`, `save_json` (uses
`common.schemas.save_json` when importable), `load_labels` (copied from `paired_maker/pm_tokens.py`), day helpers,
`heartbeat` fallback (same `hb_<stage>.json` format as `common.tg.heartbeat`). No network at import.

**tokens_range.py** - `--variant 5m|15m --from YYYYMMDD --to YYYYMMDD [--labels csv] --out map.json`. Generalised
`pm_tokens.py`: one Gamma slug lookup per round (`btc-updown-5m-<start>` step 300 / `btc-updown-15m-<start>` step 900,
`&closed=true` first then without), 4 threads. Output = the `~/pm/tokens_btc5m_range.json` format (asset -> start,
outcome, other, winner, condition_id) + `<out>_assets.txt`. Winner = chain label from the relabeled CSV (`start_time`
ISO, `chain_outcome`) when given, else Gamma `closed` + `outcomePrices` 1/0; disagreements counted. No listing call is
made (if one is added it must be `order=endDate&ascending=true`, never `createdAt`). ~4 KB per round; 19 days of 5m
= ~3 MB. Failure: missing rounds are counted (`missing`), nothing raises. Verified live: 96/96 15m rounds of 2026-10-28.

**gamma_rounds.py** - `run(date, dry_run)`: yesterday's 5m + 15m rounds via `tokens_range.build`, merged by asset into
`data/tokens/tokens_btc5m_live.json` / `tokens_btc15m_live.json` (idempotent; re-runs overwrite the same keys) +
`_assets.txt`. Labels from `QD_LABELS_5M`/`QD_LABELS_15M` or the VM relabeled CSVs when present. Returns the
`rounds_yesterday` block (n, up_rate, chain_verified, gamma_disagree, missing). 384 requests, ~2 min at 0.2 s pacing.
Verified live: 288/288 5m + 96/96 15m for 2026-10-28, up_rate 0.4965 / 0.4792.

**prices_archive.py** - for every round of yesterday in the live maps pulls
`GET https://clob.polymarket.com/prices-history?market=<token>&fidelity=1&startTs=<start-60>&endTs=<end+60>` for both
tokens (response `{"history":[{"t","p"}]}`; parameter names verified; `interval=max` together with `startTs` returns an
empty history, so `interval` is never sent). Writes `data/prices1m/day_YYYYMMDD.csv.gz` (`asset,t,p`); assets already
in the file are skipped (second run = 0 requests, verified). ~1-2 points per minute per token -> ~0.3-0.5 MB/day gz.
`run()` returns `(ok, {days_archived, rounds_with_history, assets_new, requests, failed}, note)`. Failure: per-request;
a day with no live map rounds is reported `ok=false`.

**moondev_pull.py** - urllib client, `X-API-Key` + browser UA (Cloudflare 1010 otherwise) from `~/qdesk/.env`
(`MOONDEV_API_KEY`, `MOONDEV_BASE_URL` default `https://api.moondev.com`); the only scout module that reads that file.
Endpoints: `totals /api/all_liquidations/totals.json`, `liq_10m .../10m.json`, `liq_30d .../30d.json`,
`imbalance_1h /api/imbalance/1h.json`, `hip3_24h /api/hip3_liquidations/24h.json`, `profitable_traders
/api/poly/profitable-traders`, `top_traders /api/poly/whales/top-traders`. Raw JSON gz to
`data/ext/moondev/<name>_YYYYMMDD.json.gz` (10m.json ~50-300 KB, the rest small). `api_get`, `pull`, `pull_all`,
`live_totals` (recorder, 10 s timeout) and `wallet_seeds` all return `(ok, data, note)` and never raise; `--dry-run`
returns `(False, None, "dry")` with no network. `liq_totals_5m(data)` parses `windows -> 5m -> long/short` with the
field aliases the strategy bots accept and returns `(None, None)` when unknown (evidence then lists an anomaly with the
raw archive to look at). NOT verified live: the PC key is expired (401) and the VM key answered 429 on every endpoint
during the probe (shared with bots polling every 2 s) - the first real night will show the true shapes in the archive.

**hl_pull.py** - `POST https://api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs"}` (BTC ctx: `funding`,
`openInterest`, `markPx`; `oi_usd = openInterest * markPx`) and `{"type":"fundingHistory","coin":"BTC","startTime":ms,
"endTime":ms}` (`fundingRate`, `time` ms). Writes `data/ext/hl/funding_YYYYMMDD.csv.gz` (`t,funding,oi_usd,mark`;
hourly rows have empty oi/mark, the snapshot row has all). `meta_btc()` is what the recorder polls every 60 s.
Verified live: 24 hourly rows + snapshot (funding 1.25e-5, OI $3.49 B, mark 80226). ~1 KB/day.

**wallet_scan.py** - seeds = Moon Dev profitable/top traders (today's or yesterday's archive) U
`GET https://data-api.polymarket.com/v1/leaderboard?timePeriod=WEEK&limit=50&orderBy=PNL` (fields `proxyWallet`,
`pnl`, `vol`; the `/leaderboard?window=7d` form from the brief answers 404 and is kept only as a fallback). Up to 20
wallets: `/activity` and taker-only `/trades` in 1-hour windows (`get`/`pull` copied from `wallet_audits/wallet_pull.py`,
offset caps 5,000 / 10,000), `kind()` + `tkey()` taker join copied from `wallet_analyze.py`, official
`/v2/user-pnl?user=<addr>&interval=all&fidelity=1d` (cumulative daily points; 7-day deltas of `trade_pnl`, `fees_paid`,
`taker_rebate+maker_rebate`, `settled_pnl`). Output `data/ext/wallets/scan_YYYYMMDD.json`, class rule as specified
(speed_maker / rebate_farm / unknown / directional). Budget 15 min and 2,000 requests enforced (partial file + note).
Verified live on 2 wallets, 3 h: 16 requests, 50 s. ~10-30 KB/night. Failure: per wallet; seeds missing -> scanned 0.

**macro.py** - `HARDCODED_MACRO_EVENTS_2026`, `get_macro_events`, `check_macro_blackout(now_ts, round_type)` copied
verbatim from `s8_router.py` (events cached after the first call because the recorder asks every second), plus
`is_blackout(ts, market) -> 0|1` for the feature column and `next_event(ts) -> {"name","utc","impact"}`. The calendar
ends 2026-11-12: evidence raises an anomaly 14 days before the end; the owner extends it monthly.

**evidence.py** - the Scout stage. Runs gamma_rounds -> prices_archive -> moondev_pull -> hl_pull -> wallet_scan ->
features/regime/macro -> paper_bot, each guarded; writes `data/evidence/evidence_YYYYMMDD.json` (INTERFACES §4, validated
with `common.schemas.validate("evidence")`, 0 errors on the dry-run), `hb_scout.json` via `common.tg.heartbeat`.
Features split: fit <= 20261004, frozen holdout 20261005..20261014, forward = later recorder days. Regime = median
`spot_vol_30s` and quiet/active hours (bottom/top third of mean `trade_n_10s` per UTC hour) from the newest 7 feature days,
Up side, every 10th second (~1 M rows/day read in ~20 s). Labels: moondev/hyperliquid/wallets/regime `MEASURED`,
`NA` when empty. Exit 0 always; exit 2 only when the file cannot be written. `--skip wallets,moondev` for a quick run.
Real-night budget: gamma 2 min + prices 3 min + wallets <= 15 min + regime 1 min ~ 20 min worst case.

**recorder/feat_recorder.py** - the always-on service (`qd-recorder.service`). Feed code copied from
`paired_maker/pm_bot.py` (`gamma_market` generalised to 5m/15m, `market_feed`, `rtds_feed` with the exact RTDS
subscribe payload - compact-JSON filters - and `PING` every 5 s). Message shapes verified live 2026-10-29: `book`
(bids/asks lists, timestamp ms), `price_change` (`price_changes[]` with `asset_id, price, size, side, best_bid,
best_ask`), `last_trade_price` (`asset_id, price, size, side, timestamp`). One `FeatureState` per round (5m and 15m);
rounds roll on the 300 / 900 s clock, the next round's tokens are prefetched 40 s early, the market ws is resubscribed
(reconnect) whenever the asset set changes, a finished round stays alive 10 s for the look-ahead flush. The ws handler
(`on_market_msg`) only routes to `on_price_change/on_trade/on_book`; replay on this PC (Python 3.13) runs ~17k msgs/s
end to end (~0.06 ms per message average); the reported `handler_max_ms` (25 ms) is the one-off first message of a round,
which includes the `FeatureState` construction. `flush(now)` every second -> `data/features/live_YYYYMMDD.csv` (header =
`FEATURE_COLUMNS`), gzipped to `day_YYYYMMDD.csv.gz` at the UTC day change (appends if the gz exists, live file removed;
a restart appends to the open live file). Moon Dev `live_totals` every 30 s and HL `meta_btc` every 60 s in a thread,
last values forward-filled through `on_ext`, failures only change `ext_ok` in the heartbeat. Heartbeat
`data/state/hb_recorder.json` every 30 s: `ts, utc, stage, ok, note, msgs_per_s, msgs_total, unrouted, handler_max_ms,
reconnects{market,rtds,slow_consumer}, rows_written, rounds_active, spot, spot_age_s, rtds, ext, ext_ok`; ok=false when no
market event for 60 s or `~/qdesk/KILL` exists (feeds idle). ws close 1013 "slow consumer" -> counted, immediate
reconnect. Size: 2 sides x 86,400 s = 172,800 rows/day + 15m rows = ~260k rows ~ 8-10 MB gz per day.
`--dry-run [--fixture path] [--max-rows N]` replays `tests/fixtures/bme_sample_20260926_first_hour.csv.gz` by turning
each BME row into the ws-shaped dict (price_change -> `price_change`, trade -> `last_trade_price`, spot_tick/twap_tick ->
RTDS updates with the SOURCE timestamp, bba/book -> `on_bba`) and pushing it through the same handler; the token map comes
from `tests/fixtures/tokens_btc5m_range.json` (then `QD_TOKENS`, `data/tokens/`, `~/pm/`); rows go to
`data/features/dry_YYYYMMDD.csv`; prints JSON counts (`rows, price_change, trade, spot, twap, rounds, rows_out,
handler_max_ms, fixture_error`). A truncated fixture (still being written) is replayed as far as it goes. Missing fixture
or missing `FeatureState` -> note + exit 0. Measured on the first 100k fixture rows: 1 round, 246 rows (123 s x 2 sides),
5 s; full rounds give 600 rows each.
