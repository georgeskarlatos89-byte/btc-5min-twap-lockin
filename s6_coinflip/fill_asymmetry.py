#!/usr/bin/env python3
"""Report the pre-registered S6 fill-asymmetry observation.

This reports observations only; it does not claim statistical significance.
2026-09-24 (S6 record Part #1): the kill test is defined on FILLS (legs held to resolution),
but the stats counted SHARES (20 per leg) - 15 legs would have looked like "300 fills".
Now reads side_legs / side_leg_wins (legs); shares are still shown for reference.
"""
import json, sys, math
from pathlib import Path
p = Path(sys.argv[1] if len(sys.argv) > 1 else 's6_stats.json')
if not p.exists():
    print(f'no stats yet: {p}')
    raise SystemExit(0)
s = json.loads(p.read_text())
legs = s.get('side_legs') or {}
leg_wins = s.get('side_leg_wins') or {}
shares = s.get('side_fills', {})
print(f"rounds={s.get('n', 0)} pairs={s.get('pairs', 0)} legs_held={sum(legs.values()):.0f} shares={sum(shares.values()):.0f}")
for side in ('Up', 'Down'):
    n = legs.get(side, 0)
    w = leg_wins.get(side, 0)
    rate = (w / n) if n else 0.0
    ci = 1.96 * math.sqrt(rate * (1 - rate) / n) if n else 0.0     # normal approx, 95%
    print(f"{side}: legs={n:.0f} resolved_won={w:.0f} conditional_rate={rate:.3f} (±{ci:.3f} 95% CI)")
if sum(legs.values()) < 300:
    print('STATUS: sample below the 300-fill kill-test threshold; do not infer edge.')
else:
    print('STATUS: compare each conditional rate with 0.500; asymmetry > 0.08 is a kill signal.')
