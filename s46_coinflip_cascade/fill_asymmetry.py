#!/usr/bin/env python3
"""Report S46 fill-asymmetry (S6) + S4 win-rate.

S6 kill test: conditional win-rate by side after 300 legs held to resolution.
If Up-fill rounds resolve Up >58% or Down-fill rounds resolve Down >58% (8pp from 50%),
hypothesis killed — someone picking you off.

S4 kill test: <55% post-fee win over 100 triggers.

Usage: python3 fill_asymmetry.py [s46_stats.json]
"""
import json, sys, math
from pathlib import Path
p = Path(sys.argv[1] if len(sys.argv) > 1 else 's46_stats.json')
if not p.exists():
    print(f'no stats yet: {p}')
    raise SystemExit(0)
s = json.loads(p.read_text())
s6 = s.get('s6', {})
s4 = s.get('s4', {})
print("=== S46 STATS ===")
print(f"day_pnl=${s.get('day_pnl',0):+.2f} cascades={s.get('cascades',0)} pulls={s.get('pulls',{})}")
print(f"\n--- S6 coin-flip harvester ---")
legs = s6.get('side_legs') or {}
leg_wins = s6.get('side_leg_wins') or {}
print(f"rounds={s6.get('n',0)} quotes={s6.get('quotes',0)} fills={s6.get('fills',0)} pairs={s6.get('pairs',0)} legs_held={sum(legs.values()):.0f} pnl=${s6.get('pnl',0):+.2f}")
for side in ('Up','Down'):
    n = legs.get(side,0)
    w = leg_wins.get(side,0)
    rate = (w/n) if n else 0.0
    ci = 1.96*math.sqrt(rate*(1-rate)/n) if n else 0.0
    print(f"{side}: legs={n:.0f} won={w:.0f} cond_rate={rate:.3f} (±{ci:.3f} 95% CI) {'KILL' if n>=150 and abs(rate-0.5)>0.08 else ''}")
if sum(legs.values()) < 300:
    print('S6 STATUS: sample below 300-leg kill threshold; do not infer edge.')
else:
    print('S6 STATUS: compare each cond rate with 0.500; asymmetry >0.08 is kill signal.')

print(f"\n--- S4 cascade continuation (15m only) ---")
print(f"triggers={s4.get('triggers',0)} fills={s4.get('fills',0)} wins={s4.get('wins',0)} n={s4.get('n',0)} pnl=${s4.get('pnl',0):+.2f} by_size={s4.get('by_size',{})}")
n = s4.get('n',0)
wins = s4.get('wins',0)
if n:
    rate = wins/n
    ci = 1.96*math.sqrt(rate*(1-rate)/n) if n else 0.0
    print(f"S4 win_rate={rate:.3f} (±{ci:.3f} 95% CI) over {n} resolved")
    if n>=100 and rate<0.55:
        print("S4 STATUS: KILL — <55% post-fee win over 100 triggers")
    elif n>=20:
        print(f"S4 STATUS: {'promising' if rate>=0.55 else 'below threshold'} — need 100 for kill test")
else:
    print("S4 STATUS: no settled S4 rounds yet — need cascade events (rare on quiet tape, correct)")
