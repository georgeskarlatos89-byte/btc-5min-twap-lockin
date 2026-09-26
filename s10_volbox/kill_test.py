#!/usr/bin/env python3
"""S10 kill-test reporter. Prints two-leg fill rate and unwind-vs-pair P&L."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "s10_stats.json")

def main():
    if not os.path.exists(STATS):
        print(f"no stats file at {STATS} — zero observations"); return
    s = json.load(open(STATS))
    kt = s.get("kill_test", {})
    n_evt = kt.get("events_armed", 0)
    n_two = kt.get("events_two_leg", 0)
    u_loss = kt.get("events_unwind_loss", 0.0)
    p_gain = kt.get("events_pair_gain", 0.0)
    rate = (n_two / n_evt * 100) if n_evt else 0.0
    print("=" * 64)
    print("S10 KILL-TEST REPORT")
    print("=" * 64)
    print(f"armed events:        {n_evt}")
    print(f"two-leg completions: {n_two}")
    print(f"two-leg fill rate:   {rate:5.1f}%   (kill if < 30%, n>=20)")
    print(f"pair gains:          ${p_gain:+.2f}")
    print(f"unwind losses:       -${u_loss:.2f}")
    print(f"net (events):        ${p_gain - u_loss:+.2f}")
    print(f"total strategy pnl:  ${s.get('pnl',0.0):+.2f}")
    print(f"day pnl:             ${s.get('day_pnl',0.0):+.2f}")
    print()
    # verdicts
    if n_evt < 20:
        print(f"INSUFFICIENT SAMPLE — need ≥20 armed events, have {n_evt}.")
    else:
        if rate < 30.0:
            print("🔴 KILL (rule 1): two-leg fill rate < 30%")
        else:
            print("🟢 rule 1 (two-leg ≥ 30%): PASS")
        if u_loss > p_gain:
            print("🔴 KILL (rule 2): unwind losses > pair gains over 20 events")
        else:
            print("🟢 rule 2 (unwind ≤ pair gains): PASS")
    print()
    print(f"maker_quotes:  {s.get('maker_quotes', 0)}")
    print(f"maker_fills:   {s.get('maker_fills', 0)}")
    print(f"taker_pairs:   {s.get('taker_pairs', 0)}")
    print(f"pairs_won:     {s.get('pairs_won', 0)}")
    print(f"chases:        {s.get('chases', 0)}")
    print(f"arms: calendar={s.get('arms',{}).get('calendar',0)} vol={s.get('arms',{}).get('vol',0)}")
    print(f"sigma baseline: {s.get('sigma_baseline_bps',0):.2f} bps   peak: {s.get('sigma_peak_bps',0):.2f} bps")
    print("=" * 64)

if __name__ == "__main__":
    main()
