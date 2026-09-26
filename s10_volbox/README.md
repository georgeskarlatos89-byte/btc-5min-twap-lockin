# S10 — Vol-Event Binary Box ("own the whipsaw, don't guess it")

Pairs naturally with S6. S6 harvests the coin-flip open in QUIET tape (low vol, no event);
S10 harvests WHIPSAW rounds when the calendar or a vol-regime signal says "this round
contains an event." They trade opposite weathers and share the same execution plumbing.

**Markets:** 15m BTC Up/Down primarily (releases land inside one 15m candle); 5m monitored
for secondary whipsaw fills.
**Style:** both-sides maker AND pair taker.
**Edge class:** convexity in event rounds.

## Two harvests

| leg | how | cost | payoff |
|---|---|---|---|
| **(a) Pair arbitrage** (taker) | Buy Up + Down FOK whenever combined ask < **$0.97 post-fee** | ≤ $0.97 | $1.00 |
| **(b) Maker straddle** (maker) | Rest BUY @ **0.45** on Up AND Down around the event | $0.90 if both fill | $1.00 |

Losers: panic takers on both sides of the same whipsaw — they pay twice, once each direction.

## Gates (code-enforced, not willpower)

- **Calendar gate:** rounds containing 8:30 / 10:00 ET data or 14:00 ET FOMC → box mode armed
  from round-open (30s pre-arm, 120s aftershock tail). Covers CPI/NFP/PPI/PCE/GDP/FOMC/etc.
- **Vol gate:** trailing-1h realized σ > **1.8× baseline** arms box mode even off-calendar
  (cascade regimes). Baseline is calibrated from release-free data (Saturday run).
- **Leg-risk rule (strict, opposite of S6):** if only one leg fills and the other hasn't
  filled within **60s**, CHASE the missing leg aggressively up to combined cost $0.97;
  beyond that, **UNWIND the lone leg at model fair (0.50)**. NEVER carry a naked event
  position — that is S4/S5's job, not this one.
- **Part-4 live-band gate:** every fill runs through `admissible()` before it is recorded;
  fills outside the 0.40–0.60 whipsaw band FAIL LOUDLY.
- **Feed-health flat switch:** Moon Dev stale / empty / 401 → FLAT; RTDS stale → FLAT.
- **Net cap:** $30 max naked directional across BTC 5m/15m (Part-3 global cap shared with
  S3/S6); completed pairs net to zero.
- **Daily stop:** $20 (LIVE only; DRY logs but does not KILL).

## RBI status (as of 2026-09-26, Saturday)

- **R (research):** done — causal edge is the whipsaw misquote (one-sided panic, the other
  side not requoted fast enough); losers identified as panic takers on both sides.
- **B (backtest):** pending. `--calibrate` today builds the vol-gate baseline from a
  release-free tape (Saturday). Historical release-round pair-arb frequency and two-leg
  maker fill rates still need to be measured against harvested data (see playbook).
- **I (incubate):** $10/leg (~20 shares), first live test at the next US-session data
  release. Kill rules encoded in `kill_test.py`:
  - two-leg fill rate < 30% over 20 armed events → kill;
  - lone-leg unwind losses > pair gains over 20 events → kill.

## Files

| file | purpose |
|---|---|
| `s10_box.py` | runner — DRY by default; same plumbing pattern as `s6_harvester.py` |
| `kill_test.py` | reads `s10_stats.json`, prints the two kill criteria |
| `S10-PLAYBOOK.md` | operator rules and sequencing |
| `PAIRING-S6.md` | how S10 and S6 coexist (opposite weathers, shared cap) |
| `AI-VPS-DEPLOYMENT-GUIDE.md` | handoff for VPS deployment (same format as S6's) |
| `deploy/s10-box.service` | systemd unit template |

## Outputs

`s10_box.log`, `s10_fills.csv`, `s10_pulls.csv`, `s10_pairs.csv`, `s10_stats.json`.

## Quick start

```bash
# 1. Syntax / dependency check
python3 -m py_compile s10_box.py kill_test.py

# 2. Preflight (read-only, no orders)
python3 s10_box.py --preflight

# 3. Calibrate vol baseline on today's release-free tape (Saturday)
python3 s10_box.py --calibrate --minutes 60

# 4. Bounded DRY run (watch how it behaves around a real release window)
python3 s10_box.py --minutes 180

# 5. Install systemd (see AI-VPS-DEPLOYMENT-GUIDE.md)
```

DRY until `.env` says `LIVE_TRADING=1` with real Polymarket keys. No keys, no orders.
