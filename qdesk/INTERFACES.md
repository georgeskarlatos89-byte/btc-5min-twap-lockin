# qdesk INTERFACES — the shared contract (frozen 2026-10-29; change only by editing this file first)

Everything below is binding for every module. Agents own disjoint folders (see "Build ownership") and code only
against what is written here. Python 3.12, stdlib + `numpy` (tier-1 engine only) + `websockets` (feeds) +
`python-dotenv`. No pandas. All times UTC. All money in USDC dollars, prices in dollars (0.01-0.99), sizes in shares.

## 0. Paths
- Repo: `C:\g\tlfix\qdesk\` (git; code only). VM (twapvm, user ubuntupolymarket3): `~/qdesk/` (same tree) + `~/qdesk/data/` (never in git).
- `sys.path`: every script does `sys.path.insert(0, <repo root = parent of the package dir>)` and imports `from qdesk.common import ...`. The repo root is `C:\g\tlfix` locally and `~` on the VM (`~/qdesk` is the package). Package marker: `qdesk/__init__.py` (empty) in every subfolder.
- Data dir: `QD_DATA` env, default `~/qdesk/data`. Subfolders: `tokens/ labels/ features/ prices1m/ ext/{moondev,hl,wallets}/ evidence/ hyps/ bot/ reports/ state/`, plus `~/qdesk/untrusted/`.
- Existing inputs on the VM (read-only): BME days `~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/events_YYYYMMDD.csv.gz` (usable 20260926..20261014); token map `~/pm/tokens_btc5m_range.json` (asset -> {start, outcome:"Up"|"Down", other, winner, condition_id}) + `~/pm/tokens_btc5m_range_assets.txt`; chain labels `~/mstack/history_tools/history/BTC_fiveminute_long.relabeled.csv` (columns incl. `start_time` ISO, `chain_outcome`) and `BTC_fifteen_long.csv`; Telegram env `~/s1_harness/telegram.env` (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID=600); desk secrets `~/qdesk/.env` (MOONDEV_API_KEY, MOONDEV_BASE_URL optional) — placed by the owner, never written by code.
- Fixture for tests (in git): `qdesk/tests/fixtures/bme_sample_20260926_first_hour.csv.gz` (first ~48 min of 2026-09-26, BTC 5m assets only, header line kept) and `tokens_btc5m_range.json`.

## 1. BME row format (input to history build and tier-2 replay)
CSV header `ts_ms,event_type,market,asset,side,price,size,best_bid,best_ask,hash16,extra`. event_type in {price_change, bba, trade, book, spot_tick, twap_tick}. Rules learned (binding):
- best bid/ask come ONLY from columns 7/8 (`best_bid`,`best_ask`); never max/min of levels (stale levels are never zeroed). Empty string = no update. EMPTY-SIDE RULE (amended 2026-10-29): an explicit best_bid <= 0.005 means the bid side is empty -> bb = NA; an explicit best_ask <= 0.005 or >= 0.995 (the mirror of the other token's empty bid, written as 1.0) -> ba = NA; a `book` snapshot with no bids/asks sets that side NA. Derived columns become NA with it. Live feeds must pass the explicit value (e.g. "0"), never None, when a side empties.
- `price_change` side `BUY` = a bid level (price,size; size 0 = removed), side `SELL` = an ask level. Every level change is mirrored on both tokens (Up BUY 0.10 == Down SELL 0.90); keep levels per asset as given.
- `trade` rows are recorded ONCE per trade on the asset traded: side `SELL` at p = taker sold this asset (hits this asset's bids at p); side `BUY` at p = taker bought this asset = hits the OTHER asset's bids at 1-p.
- `spot_tick` (col price = chainlink BTC/USD, col asset empty) carries the SOURCE time; we receive it 1.44 s later: apply `t_recv = ts_ms/1000 + 1.44`. `twap_tick` = the 60-s TWAP; the round's `open_twap` = first twap_tick within 3 s after the round start.
- Rounds: 5m start = multiple of 300 s, 15m start = multiple of 900 s. Elapsed `t_el = t - start`; `T = 300 or 900`.

## 2. Feature store (THE central contract) — `data/features/day_YYYYMMDD.csv.gz`
One row per (market, start, side, integer second s), emitted once events up to `s + 1.2` s have been seen (the 100 ms / 1 s look-ahead helpers need that). Header = `FEATURE_COLUMNS` exactly in this order:

```
market,start,side,asset,t_el,time_left,bb,ba,bb_sz,ba_sz,spread,mid,imb3,sum_ask2,sum_bid2,
spot,open_twap,spot_bps_vs_open,mom_2s,mom_5s,spot_vol_30s,
flow_sell_10s,flow_buy_10s,trade_n_10s,sweep_flag,
ask_ok_100ms,ask_sz_100ms,bid_lvl_sz,sell_at_bb_next1s,sell_thru_bb_next1s,
session_hour,dow,macro_blackout,liq_long_5m_usd,liq_short_5m_usd,hl_funding_1h,hl_oi_usd,src
```
Definitions (state at second s = last event with t <= s):
- market "5m"|"15m"; start epoch int; side "Up"|"Down"; asset token id string; t_el int; time_left = T - t_el.
- bb, ba best bid/ask of THIS side's token; bb_sz/ba_sz = resting size at bb/ba from the levels dict (0 if unknown); spread = ba-bb; mid = (bb+ba)/2; imb3 = sum of sizes at bb, bb-0.01, bb-0.02 divided by (that + sum at ba, ba+0.01, ba+0.02) (0.5 when both 0); sum_ask2 = ba_Up + ba_Down; sum_bid2 = bb_Up + bb_Down.
- spot = latest chainlink value RECEIVED by s (source lag applied); open_twap as above; spot_bps_vs_open = (spot-open)/open*1e4; mom_2s/mom_5s = spot now minus spot received at or before s-2 / s-5, in bps of that older value; spot_vol_30s = population std of the 1-s spot changes (bps) over the last 30 s.
- flow_sell_10s = taker shares that hit THIS side's bids in (s-10, s] (SELL on this asset + BUY on the other asset); flow_buy_10s = taker shares that lifted THIS side's asks in the window (BUY on this asset + SELL on the other); trade_n_10s = count of those trades; sweep_flag = 1 if in (s-1, s] >= 30 shares hit this side's bids OR bb dropped >= 0.02 vs the bb at s-1.
- ask_ok_100ms = 1 if the best ask at s+0.1 (first event state at or after s+0.1) is <= ba at s; ask_sz_100ms = resting size at that ask then; bid_lvl_sz = size resting at bb at s; sell_at_bb_next1s = taker shares that hit this side's bids AT price bb (bb at s) in (s, s+1]; sell_thru_bb_next1s = taker shares that hit this side's bids BELOW bb in (s, s+1].
- session_hour = UTC hour of s; dow = 0 Mon..6 Sun; macro_blackout = 1 if `scout.macro.check_macro_blackout(s, market)` says blocked.
- liq_long_5m_usd / liq_short_5m_usd: Moon Dev all-liquidations totals over the last 5 min (forward-filled; empty when unknown); hl_funding_1h: Hyperliquid BTC funding rate (hourly, forward-filled); hl_oi_usd: BTC open interest USD. Empty string = NA.
- src = "bme" | "live" | "ext:<name>".
Numbers are written with up to 6 decimals; booleans as 0/1; NA as empty string. Readers must treat empty as NA.

`qdesk/features/features.py` exposes:
```python
FEATURE_COLUMNS: tuple[str, ...]           # exactly the header above
class FeatureState:
    def __init__(self, market: str, start: int, up_asset: str, down_asset: str, spot_lag: float = 1.44): ...
    def on_price_change(self, asset, side, price, size, bb, ba, t): ...   # bb/ba may be None
    def on_bba(self, asset, bb, ba, t): ...
    def on_trade(self, asset, side, price, size, t): ...
    def on_book(self, asset, bids: list[tuple[float,float]], asks: list[tuple[float,float]], t): ...   # full snapshot (live ws)
    def on_spot(self, t_source: float, value: float): ...     # applies spot_lag internally
    def on_twap(self, t: float, value: float): ...
    def on_ext(self, t: float, liq_long=None, liq_short=None, funding=None, oi=None): ...
    def flush(self, t_now: float) -> list[dict]: ...           # returns rows (Up and Down) for every second s with s+1.2 <= t_now not yet emitted
    def row_now(self, side: str, t: float) -> dict: ...        # LIVE: current-state row WITHOUT the look-ahead helpers (set to NA); used by the bot
```
The same class is used by `features/bme_to_features.py`, `recorder/feat_recorder.py`, and `bot/qbot.py`.

## 3. Hypothesis DSL — `common/schemas/hypothesis.schema.json`
```json
{"id":"H20261030-03","name":"late-round momentum taker, quiet hours","market":"5m",
 "side":"signal_sign",                 // "up" | "down" | "both" | "signal_sign"
 "side_field":"mom_5s",                // required when side == signal_sign: positive -> Up, negative -> Down
 "entry":{"when":[{"f":"t_el","op":">=","v":200},{"f":"t_el","op":"<=","v":270},
                  {"f":"abs:spot_bps_vs_open","op":">=","v":4},{"f":"ask","op":"<=","v":0.80},
                  {"f":"session_hour","op":"in","v":[0,1,2,3,4,5]},{"f":"macro_blackout","op":"==","v":0}],
          "leg":{"type":"taker","price":"ask","size_usd":5,"max_price":0.85}},
 "exit":{"mode":"hold_to_settle"},
 "limits":{"max_trades_per_round":1,"cooldown_s":30,"max_rounds_per_day":60},
 "prediction":{"net_per_dollar":0.01,"trades_per_day":25,"label":"ESTIMATE"},
 "mechanism":"one paragraph","who_loses":"one paragraph","why_this_could_fail":"mandatory paragraph",
 "novelty_vs_prior":"how it differs from the dead families"}
```
- `f` allowed: any FEATURE_COLUMN except market/start/side/asset/src, plus `ask` (= this side's ba), `bid` (= bb), `abs:<col>`, `other:<col>` (the other side's column). `op` in {"<","<=",">",">=","==","in","between"}; `v` number | list (in) | [lo,hi] (between). A condition on a NA value is False.
- leg.type "taker": price "ask" (limit at the current ask; fills only if the ask is still <= that at t+0.1 s), `size_usd` <= 5, optional `max_price`. leg.type "maker": `price` "bb" with `offset_ticks` (0 = at bb, 1 = one cent below), `size_usd` <= 5, `ttl_s` (cancel after), optional `pair_cap` (only with side "both": other side's bid capped so avg pair <= pair_cap).
- exit.mode: "hold_to_settle" | {"mode":"ttl","ttl_s":N} (sell at bid as taker after N s) | {"mode":"rule","when":[...],"leg":{"type":"taker","price":"bid"}}.
- `common/dsl.py`: `validate_rule(rule) -> list[str]` (empty = ok), `canonical_hash(rule) -> str` (sha256 of canonical JSON without id/name/text fields), `eval_when(when, row, other_row) -> bool`, `chosen_side(rule, row_up, row_down) -> "Up"|"Down"|"both"|None`.

## 4. Nightly files (`data/hyps/`, `data/evidence/`, `data/bot/`)
- `evidence_YYYYMMDD.json`: `{"date","generated","sources":[{"name","ok","rows","note"}],"rounds_yesterday":{"5m":{"n","up_rate","chain_verified","gamma_disagree"},"15m":{...}},"price1m":{"days_archived","rounds_with_history"},"features":{"days_available","newest","fit_days":[],"frozen_holdout_days":[],"forward_days":[]},"regime":{"spot_vol_30s_median_bps","hours_quiet":[],"hours_active":[],"macro_next":{"name","utc","impact"}},"moondev":{...,"label":"MEASURED|ESTIMATE"},"hyperliquid":{...},"wallets":{"scanned","top":[{"wallet","pnl_7d","btc5m_share","maker_share","rounds_per_day","class"}]},"paper_bot":{"active_rule","trades_7d","avg_R","backtest_avg_R","status"},"prior_evidence_ref","anomalies":[]}`.
- `hyps_YYYYMMDD.json`: `{"date","model","hypotheses":[<rule>...],"rejected":[{"id","reasons":[]}]}`.
- `preregistration_YYYYMMDD.json`: `{"date","written","hypotheses":[{"id","rule_hash","prediction"}],"fit_days":[],"test_days":[],"windows":[{"fit":[],"test":[]}],"thresholds":<scorecard thresholds>,"n_hypotheses","bonferroni_alpha","holdout_touch_allowed":bool,"code_hash","features_hash"}`.
- `results_YYYYMMDD.json`: `{"engine":"rule_engine_1s"|"rule_replay_events","lat":0.1,"spot_lag":1.44,"rules":{"<id>":{"per_day":{"YYYYMMDD":{"trades","cost","gross","fees","net","rounds"}},"trades":[{"day","start","side","t_el","leg","price","size","fill":"full|partial|none","fee","winner","pnl","R","ctx":{...}}],"fit":{"net_per_dollar","ci95":[lo,hi],"n"},"test":{...}|null,"windows":[{"fit":[],"test":[],"net_per_dollar","n"}],"mc_dd":{"p50","p95","n":1000},"fill_model":"taker_ask_ok_100ms|maker_1s_queue(ESTIMATE)|events_exact"}}}`. R = pnl / size_usd of the trade.
- `scorecard_YYYYMMDD.json`: `{"date","rules":[{"id","scores":{"oos_tstat","max_dd","n_trades","edge_after_costs","stability"},"total","pass":bool,"reasons":[],"why_this_could_fail"}],"passed":[],"verdict":"NOTHING_PASSED"|"CANDIDATE:<id>","holdout_used":bool,"n_tried_cum_on_holdout"}`.
  Thresholds (1..5): oos_tstat (<0 | 0-1 | 1-2 | 2-3 | >3); max_dd = MC p95 drawdown as % of $100 (>25 | 15-25 | 10-15 | 5-10 | <5); n_trades OOS (<30 => score 1 = auto-fail | 30-60 | 60-120 | 120-300 | >300); edge_after_costs = Bonferroni-adjusted CI low of net per dollar (mean<0 | mean>0 & CI low<0 | 0-0.5% | 0.5-1.5% | >1.5%); stability = windows with net>0 of 5 (<=1 | 2 | 3 | 4 | 5 and none < -1%). Pass = total >= 18 AND min score > 1 AND (tier-2 edge score >= 3 when tier 2 ran) AND skeptic ok.
- `skeptic_YYYYMMDD.json`: `{"ok":bool,"findings":[{"severity":"high|med|low","what","where"}],"holdout_reuse":bool,"weakened_tests":bool,"lookahead_fields":[]}`.
- `data/state/holdout_ledger.json`: `{"weeks":{"2026-W44":{"touched_by":["H.."],"n":1}},"n_cum":7}`.
- `data/bot/gate_pass.json`: `{"generated","rule_id","rule_hash","code_hash","corpus_hash","features_hash","scorecard_total","criteria":{...},"test_days":[],"skeptic_ok":true,"gate_pass":true,"paper_only":true}`; `active_rule.json` = the rule JSON; `strategy_state.json`: `{"rule_id","status":"OBSERVE|PAPER|DECAYING|RETIRED","trades","avg_R","backtest_avg_R","since"}`; `strategy_log.csv`: `ts,rule_id,event,reason`.
- `data/bot/paper_trades.csv` header: `ts_utc,rule_id,market,start,side,leg,decided_t_el,price_wanted,price_filled,size,fill_ms,fill_status,fee,winner,pnl,R,backtest_avg_R,ctx_json`. `decisions-YYYYMMDD.jsonl.gz`: one JSON per evaluation `{"t","start","side","rule_id","fired":bool,"killed_by":str|null,"would_price","ctx":{feature row}}` (settled later with `would_pnl` in a sidecar `decisions-YYYYMMDD.settled.jsonl.gz`).
- `data/state/hb_<stage>.json`: `{"ts","stage","ok":bool,"note"}` written at the end of every stage; `data/state/llm_cost.csv`: `ts,stage,model,seconds,input_chars,output_chars,ok`.
- `prior_evidence.json` (repo root of qdesk): `{"version":3,"families":[{"family":"resting_bid_maker","signature":{"leg.type":"maker","exit.mode":"hold_to_settle","entry_fields_subset_of":["t_el","time_left","session_hour","dow","macro_blackout"]},"evidence":"...","result":"FAIL ...","date":"2026-10-27"}, ...]}`; `quant/prereg.is_known_dead(rule) -> str|None` returns the family name when the rule's leg/exit/entry-field set matches a signature.

## 5. Shared functions (`qdesk/common/`)
```python
# schemas.py
FEATURE_COLUMNS  (re-exported from features/features.py by import; schemas.py must not import numpy)
def validate(schema_name: str, obj) -> list[str]      # "hypothesis"|"evidence"|"results"|"scorecard"|"gate_pass"|"prereg"; stdlib checker (required keys, types, enums)
def load_json(path) -> obj ; def save_json(path, obj)   # tmp + os.replace, indent=1
# fees.py
def taker_fee(price: float, shares: float) -> float    # round(0.07*p*(1-p)*shares, 5)
def maker_fee(price, shares) -> 0.0
def maker_rebate(price, shares) -> float               # 0.20 * taker_fee
def taker_rebate_pct(wv_30d_usd: float) -> tuple[str, float]   # ("Bronze",0.03) ... ("Obsidian",0.50), ("None",0.0) under 2000
# settle.py
def settle_pnl(legs: list[dict], winner: str) -> dict  # legs: {"side","price","size","fee"}; returns {"cost","payout","fees","net","pairs"}
def brier(p_up: float, winner: str) -> float
# stats.py
def boot_ci(vals, n=2000, seed=7, alpha=0.05) -> tuple[float,float]   # CI of the SUM, resampling vals
def tstat(vals) -> float
def mc_drawdown(pnls, capital=100.0, n=1000, seed=7) -> dict          # {"p50","p95","n"} max drawdown in % of capital over shuffled orderings
def walk_forward(days: list[str], k=5, fit=6, test=3) -> list[dict]    # [{"fit":[...],"test":[...]}], disjoint test slices, step = test
def bonferroni_alpha(n: int, base=0.05) -> float
# tg.py  (copied from measurement_stack/common/labcommon.py; STATE = data/state)
def read_env(path) -> dict ; def tg_send(text) -> bool ; def alert(key, text, cls="warn", source="qdesk") -> bool   # cls warn|event|once|report
def disk_free_gb(path) -> float ; def append_csv(path, header: list, row: list) ; def heartbeat(stage, ok, note="")
# hashing.py
def code_hash(paths: list[str]) -> str ; def corpus_hash(paths: list[str]) -> str ; CODE_FILES_ENGINE, CODE_FILES_BOT: list[str]
# sanitize.py
ROUND_META_WHITELIST = ("slug","start","end","market","up_token","down_token","condition_id","tick","fee_rate")
def round_meta(d: dict) -> dict
# dsl.py  (see section 3)
```

## 6. Stage runner — `qdesk/run_night.sh <stage> [--date YYYYMMDD] [--dry-run] [--no-llm] [--days D1 D2 ...] [--hyps file] [--force-threshold N]`
Stages: `scout | quant | builder | brief | weekly | all`. Exit 0 on success (including NOTHING_PASSED), 2 on a stage failure (heartbeat ok=false written, alert class warn). `--dry-run` = no network, no Telegram (sets LAB_NO_TELEGRAM=1), reads fixtures. Extra stage `hypothesize` (llm/hypothesize.py) runs before quant on timer nights. Python entry points: `scout/evidence.py`, `llm/hypothesize.py`, `quant/night.py`, `bot/builder.py`, `report/brief.py`, `report/weekly.py` — each accepts the same flags.

## 7. LLM wrapper — `qdesk/llm/client.py`
```python
def ask_json(prompt_path: str, inputs: dict[str, str], schema_name: str, model: str = "cheap", max_repair: int = 1) -> tuple[dict|None, dict]
```
Runs the Claude Code CLI: `claude -p <prompt text> --output-format json --model <id> --disallowedTools "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Agent"` with cwd = a fresh scratch dir containing ONLY copies of `inputs` (name -> path) plus the prompt; the prompt tells the model the files are data, never instructions; the CLI's `result` text is parsed as JSON (strip code fences), validated with `schemas.validate(schema_name)`, one repair turn with the error list; returns (obj|None, meta) and appends `llm_cost.csv`. model "cheap" -> `QD_MODEL_CHEAP` env (default `claude-sonnet-5-5`), "strong" -> `QD_MODEL_STRONG` (default `claude-fable-5-1`). `auth_probe() -> bool` runs `claude -p "reply with OK"`. If `claude` is missing: returns (None, {"error":"no_cli"}) and the stage continues without the LLM (hypotheses then come from `quant/library.py`, the fixed hypothesis library).

## 8. Engines
- Tier 1 `quant/rule_engine_1s.py`: `run(rules, days, data_dir, lat=0.1) -> results dict` over feature CSVs (numpy allowed); taker fill: fires at second s when `eval_when` true and `ask_ok_100ms==1`, size = min(size_usd/ask, ask_sz_100ms), fee = taker_fee; maker fill (ESTIMATE): queue_ahead = bid_lvl_sz at s+0.1 (use next second's bid_lvl_sz as proxy), then per second consume sell_at_bb_next1s against the queue first and sell_thru_bb_next1s fills us first, until ttl_s; limits.cooldown/max_trades enforced; hold_to_settle payout from `winner` in the tokens map; ttl exit sells at bb as taker (fee) at s+ttl.
- Tier 2 `quant/rule_replay_events.py`: same rule semantics on raw BME events (stdin filtered rows, same mawk filter as `inspect_run.sh` plus spot/twap rows): taker = `step()` semantics of `wallet_audits/signal_taker_replay.py` (land at t+0.1, fill only if ask still <= limit, size min(size, level size)); maker = `Side/hit/land/send/requote` of `paired_maker/pm_replay.py` (v2 cancel clock). Output = same results schema with `engine:"rule_replay_events"`.
- Both compute R = pnl / size_usd; per-day aggregates; `score.py` does CIs/MC/windows/scorecard.

## 9. Bot (`qdesk/bot/qbot.py`) — DRY only in v1
`DRY_RUN` env default "true"; any other value -> `SystemExit("qdesk v1 is paper only")`. Loads `active_rule.json` only if `gate.check(rule, data_dir) -> (ok, reasons)` passes; otherwise OBSERVE mode. Feeds copied from `paired_maker/pm_bot.py` (market ws `wss://ws-subscriptions-clob.polymarket.com/ws/market`, RTDS `wss://ws-live-data.polymarket.com`, gamma lookup `btc-updown-5m-<start>`), `FeatureState.row_now` each top-of-book change and spot tick, `dsl` decision, `paper_fill.PaperBook` (taker: fill if the ask at +100 ms still <= limit, size min(size, ask size); maker: queue rules against live `last_trade_price` messages, deduped by (ts,price,size)), paper trade log + decisions log, `decay.check(data_dir)`, kill file `~/qdesk/KILL`, paper daily stop $10 (log only), heartbeat every 30 s.

## 10. Timers (system units, pattern of measurement_stack/deploy/ms-daily-report.service): qd-rotate 00:50, qd-scout 01:00, qd-quant 03:00, qd-builder 05:30, qd-brief 07:30, qd-weekly Sun 08:00, qd-health */15; services qd-recorder, qd-bot (`Restart=always`, `RestartSec=15`). `deploy/install.sh` only prints the sudo commands.

## 11. Build ownership (exclusive)
A `common/*`, `INTERFACES.md` (read-only for others), `requirements.txt`, `prior_evidence.json`, `__init__.py` files • B `scout/*`, `recorder/*` • C `features/*`, `quant/*` • D `llm/*`, `report/*` • E `bot/*` • F `deploy/*`, `run_night.sh`, `tests/*`, `README.md`.
Rules: no commits (the integrator commits); copied functions keep their original names with `# copied from <path>`; only `llm/client.py` and `scout/moondev_pull.py` read `~/qdesk/.env`; writers use tmp + `os.replace`; every script has `--dry-run` and `--date`; print no secrets ever; nothing runs on the live VM polyvps; VM twapvm may be used read-only for data checks.
