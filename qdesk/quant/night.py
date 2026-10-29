#!/usr/bin/env python3
"""night.py - the Quant stage (03:00 UTC) of the qdesk night (INTERFACES.md sections 4, 6, 8).

  python3 quant/night.py [--date YYYYMMDD] [--dry-run] [--no-llm] [--days D1 D2 ...|fixture] [--hyps file]
                         [--force-threshold N] [--force-tier2] [--tier2-minutes 120]

1. hypotheses: --hyps file, else data/hyps/hyps_<date>.json (LLM stage), else quant/library.py; invalid rules
   (common/dsl.validate_rule) and dead-family clones (prereg.is_known_dead) are rejected and listed.
2. days: BME fit 20260926..20261004, FROZEN holdout 20261005..20261014 (never in tier 1), forward = feature days
   after 20261014 (live recorder). Windows = stats.walk_forward(fit + forward, k=5, fit=6, test=3).
3. pre-registration written BEFORE any evaluation (prereg.write_prereg).
4. tier 1 (rule_engine_1s) over fit + forward days -> results_<date>.json; score -> scorecard.
5. tier 2 (rule_replay_events on raw BME) on the frozen holdout days for the top-3 rules ONLY when the holdout
   ledger allows a touch this ISO week AND (best tier-1 total >= pass_total or --force-tier2); capped at
   --tier2-minutes; ledger records the touch; the scorecard is re-scored with the tier-2 trades (--final semantics).
6. scorecard_<date>.json, heartbeat("quant"). Exit 0 also when NOTHING_PASSED (that is a successful night);
   exit 2 on a stage failure (heartbeat ok=false).
All outputs go to data/hyps/ (the builder reads them there). --dry-run: no network, no Telegram, no BME pipeline
unless the BME files exist locally; files are still written under QD_DATA so the run can be inspected.
"""
import argparse
import datetime as _dt
import glob
import json
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

QDESK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIT_RANGE = ("20260926", "20261004")
HOLDOUT_RANGE = ("20261005", "20261014")
FIXTURE_DAY = "20260926"
BME_DIR = os.path.expanduser("~/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme")


def log(msg):
    print(f"[quant/night {_dt.datetime.now(_dt.timezone.utc).strftime('%H:%M:%S')}Z] {msg}", file=sys.stderr, flush=True)


def day_range(a, b):
    d0 = _dt.datetime.strptime(a, "%Y%m%d")
    d1 = _dt.datetime.strptime(b, "%Y%m%d")
    out = []
    while d0 <= d1:
        out.append(d0.strftime("%Y%m%d"))
        d0 += _dt.timedelta(days=1)
    return out


def load_rules(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict):
        d = d.get("hypotheses") or d.get("rules") or []
    return list(d)


def pick_hypotheses(a, data_dir):
    if a.hyps:
        return load_rules(a.hyps), f"file:{a.hyps}"
    p = os.path.join(data_dir, "hyps", f"hyps_{a.date}.json")
    if os.path.exists(p):
        rules = load_rules(p)
        if rules:
            return rules, f"llm:{p}"
    from qdesk.quant.library import get_hypotheses
    return get_hypotheses(), "library"


def tokens_paths(data_dir):
    ps = sorted(glob.glob(os.path.join(data_dir, "tokens", "*.json")))
    if not ps:
        fx = os.path.join(QDESK_DIR, "tests", "fixtures", "tokens_btc5m_range.json")
        if os.path.exists(fx):
            ps = [fx]
    return ps


def run_tier2(rules, days, data_dir, tokens5, tokens15, deadline, out_path, dry_run):
    """tier 2 per day via gzip | mawk | rule_replay_events.py; merges the per-day results; None when nothing ran."""
    from qdesk.common.schemas import load_json, save_json
    script = os.path.join(QDESK_DIR, "quant", "rule_replay_events.py")
    rules_path = os.path.join(data_dir, "hyps", f".tier2_rules_{os.getpid()}.json")
    save_json(rules_path, rules)
    assets = os.path.join(data_dir, "state", "tier2_assets.txt")
    with open(assets, "w", encoding="utf-8") as f:
        for tp in [tokens5] + ([tokens15] if tokens15 else []):
            with open(tp, "r", encoding="utf-8") as g:
                f.write("\n".join(json.load(g).keys()) + "\n")
    def one(d):
        src = os.path.join(BME_DIR, f"events_{d}.csv.gz")
        if not os.path.exists(src):
            log(f"tier 2: no BME file for {d}, skipped")
            return None
        left = deadline - time.time()
        if left <= 60:
            log(f"tier 2: time cap reached before {d}, skipped")
            return None
        part = os.path.join(data_dir, "hyps", f".tier2_{d}_{os.getpid()}.json")
        extra = f" --tokens15 {shlex.quote(tokens15)}" if tokens15 else ""
        cmd = (f"nice -n 15 gzip -cdf {shlex.quote(src)} | nice -n 15 mawk -F, 'NR==FNR{{a[$1]=1; next}} "
               f"($2==\"spot_tick\" || $2==\"twap_tick\" || (($4 in a) && ($2==\"price_change\" || $2==\"trade\" || $2==\"bba\" || $2==\"book\")))' "
               f"{shlex.quote(assets)} - | nice -n 15 {shlex.quote(sys.executable)} {shlex.quote(script)} {shlex.quote(tokens5)}{extra} "
               f"--rules {shlex.quote(rules_path)} --day {d} --out {shlex.quote(part)}")
        try:
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=left)
        except subprocess.TimeoutExpired:
            log(f"tier 2: {d} hit the time cap, dropped")
            return None
        if r.returncode != 0 or not os.path.exists(part):
            log(f"tier 2: {d} failed rc={r.returncode}: {(r.stderr or '')[-300:]}")
            return None
        res = load_json(part)
        os.remove(part)
        log(f"tier 2: {d} done")
        return d, res

    import concurrent.futures as cf
    merged = None
    with cf.ThreadPoolExecutor(max(1, int(os.environ.get("QD_TIER2_P", "3")))) as ex:
        outs = [o for o in ex.map(one, days) if o is not None]
    for d, res in sorted(outs, key=lambda x: x[0]):
        if merged is None:
            merged = res
            merged["days"] = [d]
        else:
            merged["days"].append(d)
            for rid, blk in res["rules"].items():
                mb = merged["rules"].setdefault(rid, blk)
                if mb is not blk:
                    mb["trades"].extend(blk["trades"])
                    mb["per_day"].update(blk["per_day"])
    try:
        os.remove(rules_path)
    except OSError:
        pass
    if merged is not None and not dry_run:
        save_json(out_path, merged)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="ignore data/hyps/hyps_<date>.json; library unless --hyps")
    ap.add_argument("--days", nargs="*", default=None, help="feature days to use ('fixture' = 20260926); default all on disk")
    ap.add_argument("--hyps", default=None)
    ap.add_argument("--force-threshold", type=int, default=None, help="override the pass total (e.g. 26 = nothing can pass)")
    ap.add_argument("--force-tier2", action="store_true")
    ap.add_argument("--tier2-minutes", type=float, default=120.0)
    ap.add_argument("--data-dir", default=os.environ.get("QD_DATA", os.path.expanduser("~/qdesk/data")))
    a = ap.parse_args()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    data_dir = a.data_dir
    for sub in ("hyps", "state", "features", "tokens"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
    t0 = time.time()
    try:
        note = night(a, data_dir)
        ok = True
    except Exception as e:  # stage failure
        import traceback
        traceback.print_exc()
        note = f"FAILED: {type(e).__name__}: {e}"
        ok = False
    try:
        from qdesk.common.tg import heartbeat
        heartbeat("quant", ok, f"{note} ({time.time() - t0:.0f} s)")
    except Exception as e:
        log(f"heartbeat not written: {e}")
    print(note)
    return 0 if ok else 2


def night(a, data_dir):
    from qdesk.common import dsl, stats
    from qdesk.common.schemas import save_json, validate
    from qdesk.quant import prereg as PR, rule_engine_1s as T1, score as SC, holdout_ledger as HL
    if a.no_llm and not a.hyps:
        a.hyps = None
    # ---- hypotheses
    if a.no_llm and not a.hyps:
        from qdesk.quant.library import get_hypotheses
        rules, origin = get_hypotheses(), "library"
    else:
        rules, origin = pick_hypotheses(a, data_dir)
    accepted, rejected = [], []
    seen = set()
    for r in rules:
        errs = dsl.validate_rule(r)
        dead = None if errs else PR.is_known_dead(r)
        h = None if errs else dsl.canonical_hash(r)
        if errs:
            rejected.append({"id": r.get("id"), "reasons": errs[:5]})
        elif dead:
            rejected.append({"id": r.get("id"), "reasons": [f"known dead family: {dead}"]})
        elif h in seen:
            rejected.append({"id": r.get("id"), "reasons": ["duplicate rule hash"]})
        else:
            seen.add(h)
            accepted.append(r)
    log(f"hypotheses from {origin}: {len(rules)} in, {len(accepted)} accepted, {len(rejected)} rejected")
    for x in rejected:
        log(f"  rejected {x['id']}: {x['reasons']}")
    if not accepted:
        raise RuntimeError("no valid hypothesis left after validation / dead-family check")
    # ---- days
    on_disk = sorted(os.path.basename(p)[4:12] for p in glob.glob(os.path.join(data_dir, "features", "day_*.csv.gz")))
    if a.days:
        want = [FIXTURE_DAY if d == "fixture" else d for d in a.days]
        avail = sorted(d for d in set(want) if d in on_disk)
        missing = sorted(set(want) - set(avail))
        if missing:
            log(f"feature files missing for {missing}")
    else:
        avail = on_disk
    holdout_all = day_range(*HOLDOUT_RANGE)
    fit_all = set(day_range(*FIT_RANGE))
    fit_days = [d for d in avail if d in fit_all]
    forward = [d for d in avail if d > HOLDOUT_RANGE[1]]
    skipped_hold = [d for d in avail if d in holdout_all]
    if skipped_hold:
        log(f"frozen holdout days {skipped_hold} are never used in tier 1")
    if not fit_days and not forward:
        fit_days = [d for d in avail if d not in holdout_all]          # odd day sets (tests): treat as fit
    tier1_days = sorted(set(fit_days) | set(forward))
    windows = stats.walk_forward(tier1_days, k=5, fit=6, test=3)
    thr = json.loads(json.dumps(SC.DEFAULT_THRESHOLDS))
    if a.force_threshold is not None:
        thr["pass_total"] = int(a.force_threshold)
    week = HL.iso_week(a.date)
    may = HL.may_touch(week, data_dir)
    # ---- pre-registration BEFORE evaluation
    pre_path = PR.write_prereg(a.date, accepted, sorted(set(fit_days) | set(forward)), holdout_all, windows, thr, data_dir,
                               holdout_touch_allowed=may)
    with open(pre_path, "r", encoding="utf-8") as f:
        pre = json.load(f)
    log(f"preregistration written {pre_path}: {len(accepted)} rules, {len(tier1_days)} tier-1 days, {len(windows)} windows, "
        f"alpha {pre['bonferroni_alpha']:.4g}, holdout touch allowed {may}")
    # ---- tier 1
    toks = tokens_paths(data_dir)
    res = T1.run(accepted, tier1_days, data_dir, lat=0.1, tokens=toks)
    res["date"] = a.date
    res["hypotheses_origin"] = origin
    res["rejected"] = rejected
    by_id = {r["id"]: r for r in accepted}
    card = SC.score(res, pre, thresholds=thr, n_tried_cum=HL.n_cum(data_dir), rules_by_id=by_id)
    res_path = os.path.join(data_dir, "hyps", f"results_{a.date}.json")
    save_json(res_path, res)
    # ---- tier 2 (frozen holdout) for the top 3
    top = [c["id"] for c in card["rules"][:3]]
    best = card["rules"][0]["total"] if card["rules"] else 0
    tier2_ran = False
    if top and may and (best >= thr["pass_total"] or a.force_tier2):
        t5 = next((p for p in toks if "15m" not in os.path.basename(p)), None)
        t15 = next((p for p in toks if "15m" in os.path.basename(p)), None)
        if t5 is None:
            log("tier 2 skipped: no 5m tokens map")
        else:
            log(f"tier 2 on the frozen holdout for {top} (cap {a.tier2_minutes:.0f} min)")
            t2 = run_tier2([by_id[i] for i in top], holdout_all, data_dir, t5, t15, time.time() + 60 * a.tier2_minutes,
                           os.path.join(data_dir, "hyps", f"results_{a.date}_tier2.json"), a.dry_run)
            if t2 is not None:
                tier2_ran = True
                for rid in top:
                    HL.record_touch(week, rid, data_dir)
                card = SC.score(res, pre, thresholds=thr, tier2=t2, n_tried_cum=HL.n_cum(data_dir), rules_by_id=by_id)
                save_json(res_path, res)
    else:
        why = "no rule" if not top else ("holdout already touched this week" if not may else f"best tier-1 total {best} < {thr['pass_total']}")
        log(f"tier 2 not run: {why}")
    card["tier2_ran"] = tier2_ran
    card["hypotheses_origin"] = origin
    card["rejected"] = rejected
    errs = validate("scorecard", card)
    if errs:
        log("scorecard schema problems: " + "; ".join(errs[:5]))
    rerrs = validate("results", res)
    if rerrs:
        log("results schema problems: " + "; ".join(rerrs[:5]))
    sc_path = os.path.join(data_dir, "hyps", f"scorecard_{a.date}.json")
    save_json(sc_path, card)
    for c in card["rules"]:
        log(f"  {c['id']:<10} total {c['total']:2d} {c['scores']} oos n={c['oos']['n']} pass={c['pass']}")
    return f"quant {a.date}: verdict {card['verdict']} ({len(accepted)} rules, {len(tier1_days)} days, tier2 {'yes' if tier2_ran else 'no'}) -> {sc_path}"


if __name__ == "__main__":
    sys.exit(main())
