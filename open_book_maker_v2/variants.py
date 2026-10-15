#!/usr/bin/env python3
"""variants.py - the pre-registered variant registry (VARIANT-EXPERIMENT-PLAN.md section 3).

Nothing here may be edited mid-sweep: add a variant to the plan first, then here.
Grids are fit on FIT_DAYS only (the first three replay days); verdicts come from TEST_DAYS.
"""
FIT_DAYS = ["2026-09-26", "2026-09-27", "2026-09-28"]          # 09-26 is a partial day (recorder started 09:01Z)
TEST_DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
             "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12",
             "2026-10-13", "2026-10-14"]
PHASE_A_DAYS = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]   # the 2026-10-10 result to reproduce
PILOT_CAPITAL = 1000.0

# id -> (description, list of cfg dicts to run; >1 = grid fit on FIT_DAYS, best edge/share selected)
VARIANTS = {
    "V0": ("baseline reproduction (instrument check)", [dict()]),
    "V1": ("one-sided conviction: quote only the side cheap by >= theta vs book mid",
           [dict(one_sided_theta=0.03), dict(one_sided_theta=0.05)]),
    "V2": ("flow-toxicity gate: top-of-book quiet (|imb| < q) OR our side >= k x opposite",
           [dict(imb_q=0.2, support_k=2.0), dict(imb_q=0.2, support_k=3.0),
            dict(imb_q=0.4, support_k=2.0), dict(imb_q=0.4, support_k=3.0)]),
    "V3": ("wider half-spread (both reported)", [dict(half_spread=0.03), dict(half_spread=0.04)]),
    "V4": ("late-window maker: quote only t_el in [180, 240] s", [dict(window=(180, 240))]),
    "V5": ("open-auction fade: quote only the first 15 s, then cancel, no re-quote", [dict(open_fade_secs=15)]),
    "V6": ("no re-quote into a move: a filled side stays off for the rest of the round", [dict(no_requote_after_fill=True)]),
}

PREDICTIONS = {
    "V0": "reproduces -6.25c/share +- CI on the Phase-A days",
    "V1": "still negative (selection remains); loss per round shrinks",
    "V2": "FAIL; no filter has identified dumb flow here",
    "V3": "rate unchanged or worse (wider spread selects bigger moves through us)",
    "V4": "negative (every window loses) - cleanest test of monetizing the fair-model edge passively",
    "V5": "negative",
    "V6": "loss per filled round drops materially; rate still negative (damage limiter, not revival)",
    "V7": "diagnostic only: autopsy memo",
}


def cfg_tag(d):
    if not d:
        return "base"
    return "_".join(f"{k}{v if not isinstance(v, (list, tuple)) else '-'.join(str(x) for x in v)}" for k, v in sorted(d.items()))
