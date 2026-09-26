# S10 operator playbook

1. **Today (Saturday):** run `python3 s10_box.py --calibrate --minutes 120` against a
   release-free tape. Record the printed `sigma_baseline_bps` and the 1.8× threshold.
   That is your vol-gate number for Monday; do not guess it.
2. Start with `--preflight`; confirm both 5m and 15m markets resolve to tokens, Moon Dev
   key is present, and calendar gate fires at the correct ET wall-clock (verify with
   `date` in America/New_York — if ET fallback warning appears, install tzdata).
3. Run **at least 3 days DRY** spanning one real scheduled release (e.g. Tuesday's 8:30 ET
   print). Confirm: (a) box mode arms on calendar and disarms cleanly after the round;
   (b) pair-arb sniffs fire when top-of-book combined ask < $0.97; (c) maker quotes land
   pre-round and pull on band exit; (d) any one-leg fill is chased within 60 s, and if
   the missing leg can't be had under $0.97 combined, the lone leg unwinds at model fair;
   (e) NO naked leg survives past `LEG_TIMEOUT_S + a few seconds`; (f) no error storms.
4. Inspect `s10_pairs.csv` for post-fee costs actually seen. Compute average pair cost
   and win rate (pairs always pay $1, so win rate should be ~100% — but chases and
   unwinds matter).
5. Run `python3 kill_test.py` after each session. Only promote to live when 20+ armed
   events are logged, two-leg rate ≥ 30%, and unwind losses ≤ pair gains.
6. **Incubate** $10/leg (~20 shares) at the next US-session data release after DRY passes.
   Stay at size for 2 weeks; do not scale on first-day P&L.
7. S10 and S6 are complementary weathers: S6 only trades when S10 is DISARMED (quiet tape,
   no event); S10 only trades when ARMED (calendar or vol). They will almost never be
   quoting the same round at the same time, but the $30 net cap is still shared — do not
   double size.
8. KILL switch: `touch KILL` flats immediately (same pattern as S6).
