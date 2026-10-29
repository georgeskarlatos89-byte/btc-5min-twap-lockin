# common/ deviations and interpretations of INTERFACES.md (agent A, 2026-10-29)

None of these change a signature in section 5. They are portability fixes and places where the contract left a choice.

1. `tg.py` file lock: the copied `alert()` used `fcntl.flock`, which does not exist on Windows. The lock is wrapped in `_Lock` (fcntl on Linux, no-op on Windows). On the VM the behaviour is identical to labcommon.py.
2. `tg.py disk_free_gb`: `os.statvfs` is Linux-only; replaced by `shutil.disk_usage(...).free / 2**30` (same number on Linux).
3. `tg.py STATE`: created lazily by `_state()` (re-reads `QD_DATA` each call) instead of at import, so tests can redirect the data dir and importing the module has no side effect. `heartbeat(stage, ok, note="")` writes `{"ts","utc","stage","ok","note"}` (`utc` is an extra ISO field beside the section-4 keys).
4. `tg.py PREFIX`: the emoji prefix of the measurement stack is replaced by the plain text `qdesk (paper-only research desk, no orders)` so the messages are distinguishable from the ms-* stack and print safely on any console.
5. `hashing.code_hash(paths)`: the original `make_strategy.code_hash()` hashed a module constant; here it takes the list (section 5 signature) and resolves repo-relative paths against the qdesk package folder, hashing `<missing>` for absent files so a hash always exists.
6. `dsl.canonical_hash`: section 3 says "without id/name/text fields". The excluded set is `id, name, mechanism, who_loses, why_this_could_fail, novelty_vs_prior` **and `prediction`** (a forecast is not rule logic; prereg stores the prediction next to the hash anyway). `exit: "hold_to_settle"` (string) and `exit: {"mode": "hold_to_settle"}` hash identically.
7. `dsl.chosen_side` for `side: "both"` requires the entry conditions to hold on BOTH rows (each row evaluated with the other as `other:`). For `signal_sign` the `side_field` is read on the Up row (positive -> Up, negative -> Down, 0/NA -> None), then the chosen side's conditions must hold.
8. `dsl.validate_rule` requires `limits` with all three keys and the four prose fields to be >= 10 characters; unknown keys anywhere in the rule are errors (keeps LLM output tight; the repair turn fixes it).
9. `stats.walk_forward` returns the LAST k windows that fit (most recent days get tested); fit slices may overlap each other, test slices never do. Fewer than `fit + test` days -> `[]`.
10. `stats.boot_ci` is the pm_replay_report.py algorithm verbatim (resample, sum, sort, index `int(q*n)`), with `alpha` turning into the two quantile indices; `alpha=0.05` reproduces indices 50 and 1950 exactly.
11. `settle.settle_pnl` with an unknown winner (None/"") returns payout 0 (net = -cost - fees) rather than raising; callers must check the label first. `brier` returns None for NA input.
12. `schemas.validate("evidence")` accepts `paper_bot.status` "NONE" in addition to the four bot states (the night before any rule exists).
13. `prior_evidence.json` signatures use extra keys beyond section 4's three (`side`, `entry_fields_any_of`, `entry_fields_none_of`, `t_el_min_ge`, `offset_ticks_max`); their semantics are written in the file under `signature_semantics` for `quant/prereg.is_known_dead` (agent C). Families are listed most specific first.
