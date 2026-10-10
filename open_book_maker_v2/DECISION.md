# DECISION.md — timeframes & go/no-go criteria

## Timeline (14 days total, two phases)

### PHASE A — evidence (Days 1–7): capture + simulate, ZERO money at risk
1. `book_recorder.py` runs 24/7 capturing rounds (288/day).
   **FIRST: check whether the VPS already has BTC 5-minute data from earlier
   collection bots** (see instructions.md step 0) — existing data counts toward
   the gate and can collapse this phase from 7 days to hours.
2. `maker_sim.py` replays captured days; `chain_verify.py` settles each round
   against Polygon ground truth.
3. **GATE (must pass ALL to proceed):**
   - ≥ **200 settled sim rounds**
   - net PnL after zero-fee accounting **above 0** AND clearly better than
     the −5.5% public passive-MM benchmark (bootstrap CI excludes −5.5%)
   - adverse-selection edge at fills ≥ 0 on average (fills happen at prices
     still good vs fair)
   - no single-day blow-up > $20 equivalent in sim accounting

### PHASE B — incubation (Days 8–14): $10 live pilot
- `.env`: `DRY_RUN=false`, `LIVE_QUOTE_SIZE=20`, stop $20/day. Kill file ready.
- Run 7 days (~2,000 available rounds; bot trades only the gated windows).
- Chain-verify every live round; compare live fills vs sim fill model.

## DECISION DAY (end of Day 14) — exactly three outcomes

| Outcome | Criterion | Action |
|---|---|---|
| ✅ SCALE | Live PnL > 0 after costs on ≥150 live fills, fill model matches sim within 1.5×, no regime change detected | Raise size one rung ($10 → $50), keep all rails |
| 🔁 ITERATE | PnL ≤ 0 but adverse selection diagnosed (e.g., specific time-of-round or fair-band where fills are toxic) | Tune quoting windows, re-run Phase A on fresh 3 days, then 3 more pilot days |
| ❌ KILL | No diagnosable fix, or venue rule/fee change hits makers, or any integrity mismatch (chain vs venue) | Stop, keep data, the strategy is archived — not the wallet |

## Why these numbers
- 200 rounds ≈ one day of markets, but spread over 7 days to sample different
  volatility regimes (quiet weekdays + weekend + any macro spike).
- 150 live fills ≈ enough for the fill model's queue-position assumption to be
  falsified or confirmed at ~95% confidence.
- The −5.5% benchmark is the publicly known "maker way" failure mode; beating
  only $0 is not enough — we must beat the known corpse.

## Early-exit triggers (any day, no waiting)
- KILL file needed twice for the same root cause → stop and diagnose.
- regime_watch flags a maker-fee or rewards change → pause live, re-run Phase A
  math under the new rules.
- chain vs venue resolution disagreement on any traded round → investigate
  before the next round.
