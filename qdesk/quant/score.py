#!/usr/bin/env python3
"""score.py - the 1..5 x 5 scorecard of INTERFACES.md section 4 (thresholds frozen in the pre-registration).

score(results, prereg, thresholds=None, tier2=None, n_tried_cum=0) -> scorecard dict

Per rule, on the OUT-OF-SAMPLE trades (the union of the test slices of the pre-registered windows; or the frozen
holdout trades when a tier-2 run on the holdout is merged in):
  oos_tstat        stats.tstat of the per-trade R                      <0 | 0-1 | 1-2 | 2-3 | >3
  max_dd           stats.mc_drawdown(pnls, 100).p95 in % of $100        >25 | 15-25 | 10-15 | 5-10 | <5
  n_trades         number of OOS trades                                  <30 (auto-fail) | 30-60 | 60-120 | 120-300 | >300
  edge_after_costs Bonferroni-adjusted CI low of net per dollar in       mean<0 | mean>0 & CI low<0 | 0-0.5% | 0.5-1.5% | >1.5%
                   (stats.boot_ci of the SUM of pnl with alpha = bonferroni_alpha(n_hypotheses), divided by total cost)
  stability        windows (of the pre-registered ones) with test net>0  <=1 | 2 | 3 | 4 | 5 and none < -1%
Pass = total >= pass_total (18) AND every score > 1 AND (tier-2 edge score >= 3 when tier 2 ran). The Skeptic's
verdict is applied later by the builder (gate), not here. Rules are never re-scored on a different day split than
the pre-registered one; the function also annotates results["rules"][id] with fit/test/windows/mc_dd.

  python3 score.py --results results_D.json --prereg preregistration_D.json --out scorecard_D.json [--tier2 results_D_tier2.json] [--final]
"""
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DEFAULT_THRESHOLDS = {
    "pass_total": 18,
    "min_score": 2,
    "tier2_edge_min": 3,
    "n_trades_min": 30,
    "capital_usd": 100.0,
    "bands": {
        "oos_tstat": [0, 1, 2, 3],                 # score 1 below the first, 5 above the last
        "max_dd_pct": [25, 15, 10, 5],             # reversed: >25 -> 1, <5 -> 5
        "n_trades": [30, 60, 120, 300],
        "edge_ci_low": [0.0, 0.005, 0.015],        # 3: 0-0.5%, 4: 0.5-1.5%, 5: >1.5%; 1/2 by the mean / CI sign
        "stability_windows": 5,
        "stability_worst_npd": -0.01,
    },
}


def _stats():
    try:
        from qdesk.common import stats
    except ImportError as e:
        raise ImportError("qdesk.common.stats is required by quant/score.py: %s" % e)
    return stats


def _npd(trades):
    cost = sum(float(t.get("cost", t["price"] * t["size"])) for t in trades)
    net = sum(float(t["pnl"]) for t in trades)
    return (net / cost) if cost > 0 else None, cost, net


def _block(trades, stats, alpha=0.05):
    npd, cost, net = _npd(trades)
    ci = [None, None]
    if len(trades) >= 2 and cost > 0:
        lo, hi = stats.boot_ci([float(t["pnl"]) for t in trades], alpha=alpha)
        ci = [round(lo / cost, 6), round(hi / cost, 6)]
    return {"net_per_dollar": round(npd, 6) if npd is not None else None, "ci95": ci, "n": len(trades)}


def s_tstat(v, b):
    if v < b[0]:
        return 1
    if v < b[1]:
        return 2
    if v < b[2]:
        return 3
    if v <= b[3]:
        return 4
    return 5


def s_dd(v, b):
    if v > b[0]:
        return 1
    if v > b[1]:
        return 2
    if v > b[2]:
        return 3
    if v >= b[3]:
        return 4
    return 5


def s_n(n, b):
    if n < b[0]:
        return 1
    if n < b[1]:
        return 2
    if n < b[2]:
        return 3
    if n <= b[3]:
        return 4
    return 5


def s_edge(mean_npd, ci_low, b):
    if mean_npd is None or mean_npd < 0:
        return 1
    if ci_low is None or ci_low < b[0]:
        return 2
    if ci_low < b[1]:
        return 3
    if ci_low <= b[2]:
        return 4
    return 5


def s_stab(n_pos, worst, n_windows, worst_floor):
    if n_pos <= 1:
        return 1
    if n_pos == 2:
        return 2
    if n_pos == 3:
        return 3
    if n_pos == 4:
        return 4
    return 5 if (worst is None or worst >= worst_floor) else 4


def score_rule(rid, blk, prereg, thr, stats, alpha, why, tier2_blk=None, holdout_used=False):
    b = thr["bands"]
    trades = blk.get("trades") or []
    fit_days = set(prereg.get("fit_days") or [])
    windows = prereg.get("windows") or []
    test_days = set()
    for w in windows:
        test_days |= set(w.get("test") or [])
    reasons = []
    # ---- per-window test slices (from tier 1)
    win_out = []
    for w in windows:
        tw = [t for t in trades if t["day"] in set(w.get("test") or [])]
        npd, cost, net = _npd(tw)
        win_out.append({"fit": list(w.get("fit") or []), "test": list(w.get("test") or []),
                        "net_per_dollar": round(npd, 6) if npd is not None else None, "n": len(tw), "net": round(net, 6)})
    n_pos = sum(1 for w in win_out if w["n"] > 0 and w["net"] > 0)
    worst = min([w["net_per_dollar"] for w in win_out if w["net_per_dollar"] is not None], default=None)
    # ---- OOS trade set
    if holdout_used and tier2_blk is not None:
        oos = list(tier2_blk.get("trades") or [])
        src = "holdout_tier2"
    else:
        oos = [t for t in trades if t["day"] in test_days]
        src = "window_test_slices"
    R = [float(t["R"]) for t in oos]
    pnls = [float(t["pnl"]) for t in oos]
    ts = stats.tstat(R) if len(R) >= 2 else 0.0
    dd = stats.mc_drawdown(pnls, capital=float(thr.get("capital_usd", 100.0))) if pnls else {"p50": 0.0, "p95": 0.0, "n": 1000}
    npd, cost, net = _npd(oos)
    ci_low = None
    if len(oos) >= 2 and cost > 0:
        lo, hi = stats.boot_ci(pnls, alpha=alpha)
        ci_low = lo / cost
    sc = {"oos_tstat": s_tstat(ts, b["oos_tstat"]), "max_dd": s_dd(dd["p95"], b["max_dd_pct"]), "n_trades": s_n(len(oos), b["n_trades"]),
          "edge_after_costs": s_edge(npd, ci_low, b["edge_ci_low"]),
          "stability": s_stab(n_pos, worst, len(win_out), b.get("stability_worst_npd", -0.01))}
    if not oos:                                    # no out-of-sample evidence at all: nothing can score above 1
        sc["oos_tstat"] = 1
        sc["max_dd"] = 1
    total = sum(sc.values())
    if len(oos) < thr.get("n_trades_min", 30):
        reasons.append(f"n_trades OOS {len(oos)} < {thr.get('n_trades_min', 30)} (auto-fail)")
    if npd is None or npd < 0:
        reasons.append(f"net per dollar OOS {npd if npd is None else round(npd, 4)} not positive")
    elif ci_low is not None and ci_low < 0:
        reasons.append(f"Bonferroni CI low {round(ci_low, 4)} < 0 (alpha {alpha:.4g})")
    if total < thr["pass_total"]:
        reasons.append(f"total {total} < {thr['pass_total']}")
    low = [k for k, v in sc.items() if v < thr.get("min_score", 2)]
    if low:
        reasons.append("score 1 on " + ", ".join(low))
    tier2 = None
    if tier2_blk is not None:
        t2 = list(tier2_blk.get("trades") or [])
        npd2, cost2, net2 = _npd(t2)
        ci2 = None
        if len(t2) >= 2 and cost2 > 0:
            lo2, hi2 = stats.boot_ci([float(t["pnl"]) for t in t2], alpha=alpha)
            ci2 = lo2 / cost2
        e2 = s_edge(npd2, ci2, b["edge_ci_low"])
        tier2 = {"n": len(t2), "net_per_dollar": round(npd2, 6) if npd2 is not None else None,
                 "ci_low": round(ci2, 6) if ci2 is not None else None, "edge_score": e2, "days": sorted({t["day"] for t in t2})}
        if e2 < thr.get("tier2_edge_min", 3):
            reasons.append(f"tier-2 edge score {e2} < {thr.get('tier2_edge_min', 3)}")
    passed = (total >= thr["pass_total"] and min(sc.values()) >= thr.get("min_score", 2) and
              len(oos) >= thr.get("n_trades_min", 30) and (tier2 is None or tier2["edge_score"] >= thr.get("tier2_edge_min", 3)))
    out = {"id": rid, "scores": sc, "total": total, "pass": bool(passed), "reasons": reasons, "why_this_could_fail": why,
           "oos": {"source": src, "n": len(oos), "tstat": round(ts, 4), "net_per_dollar": round(npd, 6) if npd is not None else None,
                   "ci_low_bonferroni": round(ci_low, 6) if ci_low is not None else None, "mc_dd_p95": dd["p95"], "net": round(net, 6)},
           "windows_positive": n_pos, "windows": win_out, "tier2": tier2,
           "fit": _block([t for t in trades if t["day"] in fit_days], stats), "all": _block(trades, stats)}
    # annotate the results block (section 4 keys)
    blk["fit"] = out["fit"]
    blk["test"] = _block(oos, stats, alpha=alpha) if oos else None
    blk["windows"] = [{k: w[k] for k in ("fit", "test", "net_per_dollar", "n")} for w in win_out]
    blk["mc_dd"] = dd
    return out


def score(results, prereg, thresholds=None, tier2=None, n_tried_cum=0, rules_by_id=None):
    """results: tier-1 results dict; prereg: the pre-registration dict; tier2: tier-2 results dict (holdout) or None."""
    stats = _stats()
    thr = thresholds or (prereg.get("thresholds") if isinstance(prereg, dict) else None) or DEFAULT_THRESHOLDS
    if "bands" not in thr:
        thr = dict(DEFAULT_THRESHOLDS, **thr)
    n_h = int(prereg.get("n_hypotheses") or len(prereg.get("hypotheses") or []) or len(results.get("rules") or {}) or 1)
    alpha = float(prereg.get("bonferroni_alpha") or stats.bonferroni_alpha(n_h))
    whys = {}
    for h in prereg.get("hypotheses") or []:
        whys[h.get("id")] = h.get("why_this_could_fail") or ""
    if rules_by_id:
        for rid, r in rules_by_id.items():
            whys[rid] = r.get("why_this_could_fail") or whys.get(rid, "")
    t2rules = (tier2 or {}).get("rules") or {}
    holdout_used = bool(t2rules)
    cards = []
    for rid, blk in (results.get("rules") or {}).items():
        why = whys.get(rid) or blk.get("why_this_could_fail") or "(not supplied)"
        t2b = t2rules.get(rid)
        cards.append(score_rule(rid, blk, prereg, thr, stats, alpha, why, tier2_blk=t2b, holdout_used=(t2b is not None)))
    cards.sort(key=lambda c: (-c["total"], -(c["oos"]["net_per_dollar"] or -9), c["id"]))
    passed = [c["id"] for c in cards if c["pass"]]
    verdict = f"CANDIDATE:{passed[0]}" if passed else "NOTHING_PASSED"
    card = {"date": prereg.get("date") or results.get("date") or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d"),
            "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "engine": results.get("engine"), "thresholds": thr, "bonferroni_alpha": alpha, "n_hypotheses": n_h,
            "rules": cards, "passed": passed, "verdict": verdict, "holdout_used": holdout_used,
            "n_tried_cum_on_holdout": int(n_tried_cum)}
    return card


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tier2", default=None, help="tier-2 (holdout) results json to merge")
    ap.add_argument("--final", action="store_true", help="merge the tier-2 confirmation (requires --tier2)")
    ap.add_argument("--data-dir", default=os.environ.get("QD_DATA", os.path.expanduser("~/qdesk/data")))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    from qdesk.common.schemas import load_json, save_json, validate
    results = load_json(a.results)
    prereg = load_json(a.prereg)
    tier2 = load_json(a.tier2) if (a.tier2 and a.final) else None
    try:
        from qdesk.quant.holdout_ledger import n_cum
        ncum = n_cum(a.data_dir)
    except Exception:
        ncum = 0
    card = score(results, prereg, tier2=tier2, n_tried_cum=ncum)
    errs = validate("scorecard", card)
    if errs:
        print("[score] scorecard schema problems: " + "; ".join(errs[:5]), file=sys.stderr)
    for c in card["rules"]:
        print(f"{c['id']:<10} total {c['total']:2d} {c['scores']} pass={c['pass']} oos n={c['oos']['n']} npd={c['oos']['net_per_dollar']} "
              f"reasons={c['reasons']}")
    print("verdict", card["verdict"])
    if a.out and not a.dry_run:
        save_json(a.out, card)
        save_json(a.results, results)        # annotated fit/test/windows/mc_dd
    return 0


if __name__ == "__main__":
    sys.exit(main())
