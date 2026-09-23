# S3 — FEE-FARM TWO-SIDED MAKER · OPERATOR PLAYBOOK

Market: Polymarket **BTC Up or Down, 5-minute and 15-minute** rounds.
Style: mid-round market making · Edge class: liquidity provision / fee recycling.
Status: **B step done — doc-verbatim version KILLED, one survivor found** (see
`s3_backtest_report.md`). I step: DRY first (verify the fill model 3 days), then
real money only at the surviving parameters.

## WHAT SURVIVED THE B STEP (2026-09-22, 380 rounds / 24h, 32 variants)

| version | net / 24h | split-half | verdict |
|---|---|---|---|
| **cut20, bid 0.45, window 60%** | **+$69.11** | +43 / +26 | **survivor — encoded in s3_maker.py** |
| doc-verbatim: offer49, bid 0.49, 60% | −$781.33 | −387 / −394 | **KILLED** (rank 31/32) |
| all other 30 variants | negative | both halves negative | killed |

The pair farm works at every price (pairs are +$0.02/sh at 0.49, +$0.10/sh at 0.45);
what kills versions is naked-leg bleed. Two rules do all the saving: **buy at 0.45**
(more edge per pair, cleaner fills) and **cut naked legs at fill+20s** (never sit).
Adverse/gross for the survivor is 0.68 — thin. This is a filter, not a proof (B-step).

## THE EDGE (one sentence)

Takers pay **1.75%** at 50¢ for lottery tickets every round; makers pay 0%, earn the
rebate pool + liquidity rewards, and clean rounds pay the pair — quote both sides at
0.45, keep the pairs, cut the strays fast.

## QUOTES — all must hold (coded in s3_maker.py)

1. **BUY Up @ 0.45 and BUY Down @ 0.45**, 50 shares each (≥50 sh = liquidity-rewards minimum).
2. Window: **t+30s → 60% of round elapsed**. Nothing outside it (P3).
3. Mid must be inside the **0.45–0.55 band** at placement AND at every fill (the Part-4
   live gate — a fill recorded outside the band FAILS LOUDLY; that bug once faked +5.71pp).
4. Session gate: quiet regimes only — **Asia 00:00–12:00 UTC + dead US lunch 16:00–18:00 UTC**,
   all day weekends. (S8 router will own this later; the gate is encoded here, P5.)

## PULL BOTH QUOTES INSTANTLY (all three are live triggers)

| # | trigger | source | note |
|---|---|---|---|
| a | one-sided cascade ≥ **$1.5M / 10s** | Moon Dev `all_liq_10m` (`/api/all_liquidations/10m.json`) | median **1s** lag — the only intra-round liq feed. `binance_liq_10m` (163s stale) is **BANNED** |
| b | spot ≥ **6 bps / 10s** | RTDS chainlink spot | stall watchdog on |
| c | imbalance flips **extreme (≥0.85 / ≤0.15)** | Moon Dev `imbalance/1h` | 90s background cadence (~90s feed lag — background only) |

After any pull: **no re-quotes for 60s**. Every pull is logged to `s3_pulls.csv`
(incubate requirement: verify pulls fire on real cascades, not noise).

## INVENTORY RULE (never "hope" a position — P5)

* One side filled, the other not, for **20s** → **cut the naked leg at the bid** (taker,
  fee `0.07·p·(1−p)` on top) — the B-step survivor `EXIT_MODE="cut20"`.
* `"offer49"` (doc-literal) instead rests a breakeven offer at cost to quote end; the
  backtest says that version dies of naked-leg bleed. Kept selectable, not recommended.
* Both filled (pair) → **hold to resolution**: cost 0.90, pays 1.00 (+$0.10/sh).
* Moon Dev M5 lesson: the backtest report's fill-count σ (7.75/h ± 1.81 measured) is the
  natural baseline — no alert threshold may be tighter until live data re-measures it.

## FEED-HEALTH FLAT-SWITCH (M3/M4/M7)

Moon Dev liq feed stale >60s / empty rows / 401 (key expired) → **strategy goes flat**.
You cannot pull on what you cannot see. Same spirit as the S4/S9 flat-switch rule.

## RISK (encoded, none in willpower)

* 50sh/side ≈ $22.5 at 0.45 (set `QUOTE_SHARES=20` for the doc's strict $10/side I-step).
* Max **$30 net directional** per 5-min window across both series (pairs are hedged).
* **Daily stop $20** → KILL file written automatically + flat. `touch KILL` = manual kill.
* One quote-pair per round per series. No taker entries ever — this strategy only makes.

## EVIDENCE SO FAR

* `s3_backtest_report.md` + `s3_backtest_rows.csv` — B-step results (380 rounds, 32
  variants, split-half stability). Survivor: cut20/0.45/60%, positive in both halves.
* Pull triggers cannot be backtested offline (Moon Dev feeds aren't in this repo) —
  the backtest ran WITHOUT them (band gate only). If the pulls fire on real cascades in
  incubation they are pure upside protection; the pull log must prove it.

## EXPECTATION & SCALING RULE

Pairs earn +$0.10/sh at 0.45 (~$5.00 per 50sh pair). The backtest net is thin
(~$69/24h at 50sh with adverse/gross 0.68 — under the 1.0 kill line but close).
Run DRY 3 days until live fills/hour sits inside the backtest band (7.75 ± 1.81/h),
then $-small real for **2 weeks** per RBI.
**Kill: 2 consecutive weeks where adverse selection > gross capture** (tracked in
`s3_stats.json` as `adverse` vs `gross`), or week-1 live adverse/gross > 0.9
(we are too close to the line to wait two weeks if live is worse than backtest).
Scale ONLY if live matches the backtest (Moon Dev I-step: real money fixes execution
realism; only time fixes sample size).
