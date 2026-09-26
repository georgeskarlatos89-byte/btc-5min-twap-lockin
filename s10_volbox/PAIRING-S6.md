# S10 + S6 pairing map

S6 (coin-flip harvester) and S10 (vol-event binary box) are intentionally separate
processes and separate ledgers, but they are designed as **opposite-weather hedges**
within Group A (liquidity/fee farming):

| | S6 | S10 |
|---|---|---|
| weather | QUIET tape, round open | VOL / EVENT rounds |
| when it fires | quiet UTC hours, weekends, no release | calendar-flagged rounds OR σ>1.8× baseline |
| bid price | 0.49 both sides | maker 0.45 both sides; taker pair-arb <0.97 post-fee |
| window | t+0 → +45 s | pre-round -30s → 50% of 15m round |
| lone leg | HELD to resolution (kill-test = fill asymmetry) | CHASED within 60s up to $0.97, else UNWOUND at fair |
| hypothesis | 0.49 is a fair coin once you survive first-45s flow | whipsaws misprice pairs; convexity pays for chases |
| naked event risk? | yes, deliberately (it's the experiment) | NEVER — leg-risk rule unwinds |
| kill at | 300 fills, side win-rate >8pp from 50% | two-leg rate <30% OR unwind > pair gain over 20 events |

**Why they pair naturally:**
- Their gates are mutually exclusive in normal conditions: S6 has a vol-override that
  FLATTENS it when sigma exceeds 1.5× baseline; S10 ARMS when sigma exceeds 1.8× baseline.
  In vol spikes S6 is pulled and S10 takes over.
- Calendar gate: when S10 arms for 8:30 ET data, S6's session gate (US active hours)
  would likely already keep it flat, but even if not, S6's vol override will pull it.
- Both harvest Group-A revenue (maker rebates / short-vol convexity) but from different
  regimes, so they don't double-down on the same risk factor.
- Same $30 net cap per 5-min bucket, same Moon Dev feed, same RTDS spot, same KILL file
  semantics, same DRY-by-default safety model.

**Overlap edge case:** the moment a cascade fires during an S6 quiet round: S6 pulls (it
sees σ breach 1.5× baseline), then S10 arms at 1.8× baseline. In the gap between 1.5× and
1.8× both are flat — that's intentional (no-man's-land, don't guess). Do not tighten the
gap.

**Portfolio implication:** S3+S6+S10b all farm group-A; do not incubate more than two of
them live at once until you've measured pairwise correlation across real regimes.
