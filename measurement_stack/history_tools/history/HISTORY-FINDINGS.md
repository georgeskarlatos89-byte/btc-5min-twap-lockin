# HISTORY CORPUS — BTC round history: sources, backfills, and the resolution audit

Updated 2026-09-27. Two complementary backfill tools, one on-chain verifier,
and a resolution audit that **corrects an earlier wrong conclusion** (see §4).

## 1. Sources and what they actually give you

| source | retention | content | reliability |
|---|---|---|---|
| `polymarket.com/api/past-results` | ~7 days | full-precision open/close stream values, percentChange, `outcome` | open/close byte-exact (verified); **`outcome` is a DERIVED display field, wrong on razor rounds (see §4)** |
| Gamma series listing (`gamma-api .../events?series_id=10684`) | **entire series history** (5m series started 2025-12-18) | outcome per round + condition_id + closedTime; NO reference prices | single fetch can be stale for hours after settlement; ~0% error observed once settled (700/700 verified, §5) |
| CTF payout vector on Polygon (`chain_verify.py`) | forever | **the money truth**: which token actually paid $1 | authoritative; batched ~0.3s/round |
| Old markets' CLOB `prices-history` / trade tape | — | — | **purged** (probed 60d: 0 points). No historical order-book data exists anywhere. |

## 2. Corpora in this folder

| file | rounds | window | source |
|---|---|---|---|
| `BTC_fiveminute.csv` | 1,998 | 2026-09-19 → 26 (7d) | past-results: full-precision open/close + derived outcome |
| `BTC_fifteen.csv` | 671 | same, 15m variant | past-results |
| `BTC_fiveminute_long.csv` | **76,666** | **2025-12-18 → 2026-09-27** | gamma series 10684 (outcome, closed_time, condition_id) |
| `BTC_fifteen_long.csv` | **27,088** | same, 15m | gamma series |
| `BTC_fiveminute_long.chain.csv` | 500 (seed-42 sample) | — | chain-verified subset: **500/500 gamma label == on-chain payout** |

Deep-corpus stats: up-rate 50.18% (5m) / 49.83% (15m); grid gaps 11 (5m) / 8 (15m);
14 never-resolved rounds on day 2 of the 5m series (2025-12-19, marked outcome='').

Tools: `history_backfill.py` (7-day, prices+derived outcome), `history_backfill_gamma.py`
(deep, outcomes+condition_id), `chain_verify.py` (re-label any corpus against the chain;
full 76k verification ≈ 1.5h — run on the VPS if you want zero label doubt).

## 3. CSV columns

**`BTC_fiveminute.csv` (past-results)**
- `start_time` / `end_time`: round window (UTC). 5m rounds tile the day exactly.
- `open_price_full`: Chainlink BTC/USD TWAP-60s **stream value at window start**
  (= previous round's close; the chain is exact 1990/1997).
- `close_price_full`: stream value at window **end**. NOT the settlement value (see §4).
- `outcome`: up/down — **derived display field**, equals sign(close−open) by construction.
- `percent_change`: (close−open)/open × 100 as served by the endpoint.
- `tail_gap_bps`: (close−open)/open × 10,000 (our derived column).

**`*_long.csv` (gamma)**: `start_time, end_time, outcome` (official), `closed_time`,
`condition_id` (Polygon CTF condition for chain verification).

## 4. THE RESOLUTION AUDIT (corrects earlier claims)

Earlier I wrote "closePrice is the settlement reference; tail-vs-settlement
disagreement is structurally zero". **That was wrong.** It was a self-referential
artifact: past-results' `outcome` field IS sign(close−open) computed server-side,
so agreement was guaranteed. The audit:

1. Market rules (from the event description + `cryptoMarketConfig`):
   *"resolves Up if the time-weighted average price (TWAP) of Bitcoin, generated
   by Chainlink, of the time range is ≥ the price at the beginning of that range"*
   (config: `btc-5m-twap-60`, `twapEnabled`, `twapLookbackSeconds: 60`).
   Settlement compares the **window TWAP** to the open print — ties resolve Up.
2. Ground truth = on-chain CTF `payoutNumerators(conditionId, 0|1)`, index 0 = Up,
   contract `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` (Polygon). Verified clean
   0/1 payouts across all probed rounds.
3. Stratified audit, 200 rounds × 5 gap buckets, chain vs past-results outcome:

   | boundary gap | n | past-results wrong | gamma-corpus wrong |
   |---|---|---|---|
   | <1bp | 40 | **11 (27.5%)** | 0 |
   | 1–2bp | 40 | **14 (35%)** | 0 |
   | 2–4bp | 40 | 3 (7.5%) | 0 |
   | 4–8bp | 40 | 1 (2.5%) | 0 |
   | >8bp | 40 | 0 | 0 |

4. past-results and gamma disagreed on 221/1998 (11%) of overlap rounds — those are
   exactly the rounds where window-TWAP and boundary-sign diverge (late reversals
   inside the window). In them, either source can be the wrong one; only the chain
   always tells the truth. Gamma additionally serves intermittently stale sides for
   hours after settlement (observed flipping to the correct side on re-fetch).

**Implication:** the "last-minute reversal" risk I previously called structurally
zero is real and concentrated: ~28-35% of sub-2bp rounds resolve opposite to the
boundary sign. ~11% of ALL rounds sit in the zone where the visible direction and
the payout can diverge.

## 5. What this means for backtesting and the strategies

- **Directional backtests (S1):** use the gamma long corpus for labels (700/700
  chain-verified). Treat sub-2bp rounds as untradeable noise — they are coin flips
  where even the label source you'd backtest against is unreliable. Rounds with
  |gap| ≥ 4bp have ≤2.5% label error; ≥8bp: 0 observed. S1's `GAP_MIN_BPS=4.0`
  gate remains sound, now with measured backing.
- **Arbiter:** must settle from gamma outcomePrices (S1's arbiter already does) or
  the chain — NEVER from past-results `outcome`.
- **Maker family:** fills depend on order-book dynamics, which the venue does not
  archive (old markets' price/trade history is purged). The only book corpus is
  what `book_recorder.py` records from now on; maker backtesting before that corpus
  accumulates is limited to the round-level stats above.
- Refresh cadence: past-results corpus rolls daily (7-day window); gamma deep
  corpus grows daily (`history_backfill_gamma.py --start <yesterday>` + merge).
