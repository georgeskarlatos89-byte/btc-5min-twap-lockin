# S3 — Fee-Farm Two-Sided Maker (the I-step package)

Strategy **S3** from `BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD`, built through the
Moon Dev **RBI** framework (`uploads/MOONDEV 6+ KNOWLEDGE.MD`): Research (the strategy
doc's causal edge) → Backtest (`s3_backtest.py`, run it) → Incubate (`s3_maker.py`).

> **R, one sentence:** entertainment takers pay the 1.75% peak taker fee at 50¢ every
> round; quote both sides at 0.45, harvest the pairs (0.90 → 1.00), and cut the stray
> legs at 20s before adverse selection bleeds you — the fee/rebate/rewards are yours.

## Files

| file | role |
|---|---|
| `s3_backtest.py` | **B step** — replays the real public trade tape of recent rounds, simulates the quotes (32-variant sweep with `--sweep`), writes `s3_backtest_report.md` + `s3_backtest_rows.csv` |
| `s3_maker.py` | **I step** — live gated maker. DRY by default; `--preflight` for a read-only check |
| `S3-PLAYBOOK.md` | operator rules (quotes, pulls, inventory, risk, kill criteria) |
| `run_backtest.sh` / `run_maker.sh` | launchers (same shape as `s1_harness/run_*.sh`) |
| `deploy/s3-maker.service` | systemd unit (same shape as `s1-trader.service`) |

## What the S1 stack already generates — and S3 reuses

S3 was built to plug into what `s1_harness` already proved works (copy-with-attribution,
so `s1_harness` itself stays byte-identical):

* **`gamma_resolution()`** (Part #22: `?closed=true` first — settles ~100s sooner than the plain query)
* **RTDS loop + stall watchdog** (Part #23: a zombie socket froze the observer 85 min)
* **`client_for()` proxy signing** (signature_type=3 / POLY_1271 + `POLY_FUNDER`) — execution plumbing only
* **gates()/KILL/.env/DRY-default/stats.json** pattern and the log vocabulary
  (`QUOTE`, `FILL`, `PULL`, `INV-EXIT`, `ROUND-RESULT`) so `s1_monitor.py`-style
  monitoring can parse S3 the same way
* **Public tape as fill ground truth**: `data-api /trades?market=` returns every print
  (verified: 897 prints on one round) — the maker-side fill detector both modes use

## Moon Dev knowledge → where it is encoded (the "RBI framework" file)

| knowledge | encoding |
|---|---|
| **Part-4 lesson** (paper fills skipped the live band gate → fake +5.71pp) | `admissible()` runs BEFORE any fill is recorded, DRY and LIVE alike; a fill outside the 0.45–0.55 band **fails loudly**. Backtest counts `band_reject` fills the same way |
| **5b feed freshness** (`all_liq_10m` = 1s median lag; `binance_liq_10m` = 163s = banned intra-round; imbalance ~90s = background only) | pull (a) uses ONLY `/api/all_liquidations/10m.json`; the Binance feed is never touched; imbalance only at a 90s background cadence |
| **M3/M4/M7 flat-switch** (empty rows / frozen `updated_at` / 401 key death) | liq feed stale >60s or 401 → strategy flat (you cannot pull on what you cannot see) |
| **M5 lesson** (measure natural variance BEFORE thresholds) | backtest report gives fills/hour mean·σ·min·max as the baseline; playbook forbids tighter alert thresholds until live re-measures |
| **P3** ("always in a trade" = you cannot win) | hard quote window t+30s → 60% elapsed; quiet-session gate (Asia + US lunch + weekends) |
| **P5** (encode, don't intend) | every rule — pulls, inventory, stops, session — is code; `FORCE_LIVE` bypasses only the statistical gates, never the hard ones |
| **I-step** ($10 real, 2–4 weeks, compare to backtest) | DRY default; `LIVE_TRADING=1` + keys + gates → live; sizing tension documented below |

## The one doc tension, flagged not hidden

S3 mechanics say **"≥50 shares each"** (liquidity-rewards minimum) but the incubate
line says **"$10/side"** (≈22 sh at 0.45). Default is `QUOTE_SHARES = 50` (the rewards
leg is part of the edge). Set `QUOTE_SHARES=20` in the constants for the strict $10
I-step. Pick one deliberately — don't drift between them.
Second, larger tension: the doc's quote/exit parameters (0.49, sit at breakeven) were
**killed by the B step**; the shipped parameters (0.45, cut20) are the survivor. The
METHOD (RBI + the kill rule) is the asset, not the parameter set (Moon Dev P1/P4).

## Run

```bash
# B step (public data only, no keys)
python3 s3_feefarm/s3_backtest.py --hours 24 --series both --sweep   # + 32-version grid

# I step — DRY (safe default)
python3 s3_feefarm/s3_maker.py --preflight     # read-only gate report
python3 s3_feefarm/s3_maker.py --minutes 60    # bounded dry run
python3 s3_feefarm/s3_maker.py                 # forever (systemd shape)

# LIVE requires a human-created s3_feefarm/.env (chmod 600, never committed):
#   LIVE_TRADING=1
#   POLY_PRIVATE_KEY=...
#   POLY_FUNDER=...            # the proxy holding the pUSD (signature_type=3)
#   MOONDEV_API_KEY=...        # all_liq_10m + imbalance pulls (key lives ~1 week, M7 alerts)
```

## Kill criteria (pre-registered — S3)

1. **2 consecutive weeks where adverse selection > gross capture** (tracked in `s3_stats.json`).
   Tightened after the B step: week-1 live adverse/gross > 0.9 stops the experiment early.
2. Pull log shows triggers NOT firing on real cascades (protection is fiction) — investigate before continuing.
3. Live fills/hour persistently outside the backtest band (fill model is wrong, not the market).

## B-step outcome (2026-09-22) — read `s3_backtest_report.md`

380 rounds / 24h / 32 variants. The doc-verbatim version (0.49/0.49, sit at breakeven)
is **killed** (net −$781, rank 31/32). One survivor: **bid 0.45 both sides, cut naked
legs at fill+20s, window to 60%** → net **+$69/24h**, positive in both 12h halves
(split-half stability checked). `s3_maker.py` ships at those parameters (`EXIT_MODE="cut20"`).
Caveats: one day of tape, best-of-32 selection, queue-optimistic fills (biases adverse UP),
rebates at 0 (upside not counted), Moon Dev pulls unmodeled (upside not counted).
Also computed: the fill-count baseline for alerts = **7.75 ± 1.81 rounds-with-fills/hour** (M5).
