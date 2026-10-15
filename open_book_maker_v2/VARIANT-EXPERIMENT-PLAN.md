# VARIANT-EXPERIMENT-PLAN.md — pre-registered sim experiments for an OPEN-BOOK-MAKER revival

**Status: strategy #11 is CLOSED (2026-10-10, −$32.60 live, gate-failed backtest).**
This document is the ONLY authorized path to revisit it. **SIM ONLY. ZERO DOLLARS.**
Any AI or human working this plan is bound by MASTER-RUNBOOK.md §9 "Rules added
2026-10-14" — especially: *a failed gate is a verdict, not a hypothesis; live money
never validates a backtest.*

Read first, in order: `postmortem-2026-10-10/SUMMARY…`, `…/REVIEW…` (12 errors),
`…/CHANGES…`, then `DECISION.md`. Assets on twapvm: `open_book_maker_v2/` (fixed
code), BME `bookstate_1s_*.csv` (since 2026-09-21), S2 `ticks.jsonl`, S1 `rounds.csv`,
chain-verified corpora in `mstack/history_tools/history/`, `bme_to_replay.py`.

---

## 1. Measured facts every variant must attack (SUMMARY §4, 1,141 rounds, v2 code)

| # | fact | number |
|---|---|---|
| F1 | filled shares that won | 43.8% (break-even ≈48.5% at avg entry ~0.485) |
| F2 | both sides lose | Up −3.6¢/share, Down −4.3¢/share |
| F3 | every time window loses | worst: first 60 s and last 60 s pre-blackout |
| F4 | every fair-price band loses | "no corner of the strategy to keep" |
| F5 | loss rate | −6.25¢/filled share; −7.95% of filled money; worst day −$977 |
| F6 | fair model IS honest | Brier 0.195 vs market 0.195 @60 s; **0.055 vs 0.111 @240 s** |
| F7 | no noise-flow corner found | 4 days × every slice |
| F8 | maker rewards pool | `rates: null` on live 5m markets (probed 2026-10-14) — nothing to farm |

The mechanism in one line: resting bids are filled by informed flow exactly when the
price runs through them. The 4.7-point win-rate gap (43.8 vs 48.5) is the
counterparty's information, and no bankroll changes who clicks your bid.

## 2. Protocol (pre-registered — deviations void the run)

- **Fit/test split:** filters & thresholds may be fit ONLY on fit days (first 3 replay
  days); every reported criterion is computed on held-out test days. No peeking.
- **One variant = one delta** from the v2 baseline config. Report ALL variants, winners
  and losers (multiple-comparison honesty: 7 looks at the data means one false positive
  is likely; require sign-consistency on fit days too).
- **V0 is an instrument check:** the baseline must reproduce SUMMARY's −6.25¢/share
  within bootstrap CI before any variant number is believed.
- **Every run executes the settlement path end-to-end:** sim → `sim_rounds.csv` →
  `chain_verify.py` → `maker_sim.py --report` (which writes `gate_pass.json`, §4 R7).
- Sim and bot share one quoting/fill/sizing code path (`make_strategy.py`) — never fork.

## 3. The variants

| ID | delta vs v2 baseline (20 sh/side, 1.5¢ half-spread) | attacks | pre-registered prediction |
|---|---|---|---|
| V0 | none — reproduce SUMMARY | instrument | reproduces −6.25¢ ± CI |
| V1 | **one-sided conviction:** quote only the side our fair says is cheap by ≥θ vs book mid (θ∈{3¢,5¢}, fit on fit days); never quote the other side | F2 (live round 3 filled BOTH sides) | still negative — selection remains; loss/round shrinks |
| V2 | **flow-toxicity gate:** quote only when top-of-book depth imbalance \|I\|<q (quiet book) OR our side depth ≥ k× opposite (support); q,k fit on fit days | F7 (hunt a dumb-flow state) | FAIL — no filter has ever identified dumb flow here (S3 imbalance was EV-negative as a taker signal) |
| V3 | **wider spreads:** half-spread 3.0¢ and 4.0¢ (two runs) | F5 (spread vs adverse-selection race) | rate unchanged or worse — widening selects only bigger moves through you (Glosten–Milgrom logic); if CI beats gate: surprise → mechanism memo before ANY next step |
| V4 | **late-window maker:** quote only t∈[180,240] s, hard cancel outside | F3×F6 tension (our info edge peaks exactly there) | negative (F3 says every window loses) — but this is the cleanest test of whether the fair-model edge (F6) can EVER be monetized passively |
| V5 | **open-auction fade** (owner's 2026-09 hypothesis): first bids at the bell, cancel-all at t=15 s, no re-quote | F3 first-60s toxicity vs possible positioning (dumb) flow at the open | negative |
| V6 | **no re-quote into a move:** after any fill on side S, never re-quote S this round | live rounds 1 & 3 damage pattern (second/third tranches bought the crash) | loss per filled round drops materially; rate still negative — a damage limiter, not a revival |
| V7 | **diagnostic only (no quotes):** fill-selection autopsy — for every sim fill log: was top-of-book moving THROUGH our price in the prior 1–2 s? adverse drift over next 5 s? % "run-through" fills by time-of-round | the mechanism itself | n/a — evidence memo that guides V8+ design or the burial |

## 4. Revival criteria — the welded gate (ALL required, test days only)

- **R1** ≥200 settled rounds, chain-verified, end-to-end path executed (§2).
- **R2** edge per filled share > 0 with bootstrap 95% CI lower bound > 0.
- **R3** fill win rate: point ≥50% AND CI lower bound > 48.5% (the break-even line).
- **R4** adverse-selection edge at fills ≥ 0 per side (fill price still good vs fair at fill+5 s).
- **R5** worst sim day > −2% of pilot capital at pilot sizing.
- **R6** net return on filled money: CI lower bound > 0. (Beating the −5.5% corpse is no
  longer sufficient — that bar let a −7.95% strategy feel close. Positive or buried.)
- **R7** `maker_sim.py --report` writes `gate_pass.json`: {timestamp, variant id, code
  hash, replay-corpus hash, every criterion value + CI}.
- **R8** **mechanical lock:** `maker_bot.py` REFUSES `DRY_RUN=false` unless a
  `gate_pass.json` exists, is <7 days old, and its code+corpus hashes match the running
  code. The lock lives in code, not in discipline. (v2 code-change request.)
- **R9** owner's explicit written go, logged with timestamp; the flip is logged.
- **R10** sizing at go: worst-round swing ≤1–2% of live capital. A shape that swings
  ~$10/round needs a ~$500–1,000 account. Never $65 again.

## 5. Sunset clause

If none of V1–V6 satisfies R1–R6 on test days: **#11 is archived permanently.** The
surviving asset — the calibrated fair model (F6) — transfers to `truth_lab/` / S1-side
use, where knowing the present is used to TAKE, never to REST. No further maker sim
work unless NEW external evidence appears (regime_watch flags rewards `rates` non-null,
a flow-composition change, or a venue microstructure change) — and even then, work
restarts from this document's protocol, not from enthusiasm.

## 6. Report format (per variant, no narrative without numbers)

Table: variant | config delta | fit-day metrics | test-day metrics (every R-criterion
with CI) | fill win rate | per-side edges | worst day | verdict PASS/FAIL | one
paragraph mechanism read. Plus the V7 autopsy memo. Full CSVs kept under
`open_book_maker_v2/variant_runs/<id>/`.

## 7. Forbidden

- Live orders, `.env` edits, credential requests in chat. Ever.
- "I don't trust this data" → audit the pipeline against the REVIEW E1–E12 checklist.
  It is an audit trigger, never a trade trigger.
- Adding a variant mid-sweep without first writing it into §3 (pre-registration is the
  entire point).
- Reporting winners only.
