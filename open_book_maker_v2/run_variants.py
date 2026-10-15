#!/usr/bin/env python3
"""run_variants.py - execute the pre-registered sweep (VARIANT-EXPERIMENT-PLAN.md), sim only.

  python run_variants.py --replay replay/*.jsonl --labels <chain corpus csv> [--only V0 V3] [--runs-dir variant_runs]

For every variant and every grid point: a fresh output folder variant_runs/<id>_<tag>/, a full
replay through maker_sim.py (same code path as the bot), the R-gate report on FIT and TEST days,
chain_verify on a sample, and one summary table (VARIANT-RESULTS.md + variant_results.json).
V0 is additionally replayed on the Phase-A days alone (instrument check against the
2026-10-10 result: edge -6.25c/share, return on notional -7.95%).
Grids: the point with the best edge/share on FIT days is the variant's selected run; every
point is still reported. Nothing here places orders or reads credentials.
"""
import argparse, glob, json, os, subprocess, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from variants import VARIANTS, PREDICTIONS, FIT_DAYS, TEST_DAYS, PHASE_A_DAYS, PILOT_CAPITAL, cfg_tag

PY = sys.executable


def sh(args, cwd=HERE):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def run_one(vid, cfg, files, labels, out):
    os.makedirs(out, exist_ok=True)
    for fn in ("sim_rounds.csv", "sim_fills.jsonl", "maker_sim.log", "gate_pass.json", "gate_result.json"):
        try: os.remove(os.path.join(out, fn))
        except FileNotFoundError: pass
    cfg = dict(cfg); cfg["id"] = vid
    rc, so, se = sh([PY, os.path.join(HERE, "maker_sim.py"), "--replay", *files, "--labels", labels,
                     "--cfg", json.dumps(cfg), "--out", out])
    if rc != 0:
        raise SystemExit(f"{vid} replay failed rc={rc}\n{se[-2000:]}")
    rc, so, se = sh([PY, os.path.join(HERE, "maker_sim.py"), "--report", "--out", out,
                     "--test-days", *TEST_DAYS, "--fit-days", *FIT_DAYS, "--pilot-capital", str(PILOT_CAPITAL)])
    if rc != 0:
        raise SystemExit(f"{vid} report failed rc={rc}\n{se[-2000:]}")
    res = json.load(open(os.path.join(out, "gate_result.json")))
    # chain audit of a sample (plan R1: chain-verified, end-to-end path)
    rc, so, se = sh([PY, os.path.join(HERE, "chain_verify.py"), "--csv", os.path.join(out, "sim_rounds.csv"), "--sample", "40"])
    audit = {}
    try:
        audit = json.loads(so.strip().splitlines()[-1])
    except Exception:
        audit = {"raw": so[-300:], "err": se[-300:]}
    res["chain_audit"] = audit
    return res


def fmt(m, k, pct=False, cents=False):
    v = m.get(k)
    if v is None: return "-"
    if isinstance(v, list):
        return "[" + ", ".join(fmt({"x": x}, "x", pct, cents) for x in v) + "]"
    if cents: return f"{100 * v:+.2f}c"
    if pct: return f"{100 * v:+.2f}%"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", nargs="+", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--runs-dir", default=os.path.join(HERE, "variant_runs"))
    a = ap.parse_args()
    files = sorted(set(sum([glob.glob(p) for p in a.replay], [])))
    os.makedirs(a.runs_dir, exist_ok=True)
    results = []
    t0 = datetime.now(timezone.utc)
    for vid, (desc, grid) in VARIANTS.items():
        if a.only and vid not in a.only:
            continue
        runs = []
        for cfg in grid:
            tag = cfg_tag(cfg); out = os.path.join(a.runs_dir, f"{vid}_{tag}")
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {vid} {tag} ...", flush=True)
            res = run_one(vid, cfg, files, a.labels, out)
            res["tag"] = tag; res["desc"] = desc; res["out"] = os.path.basename(out)
            runs.append(res)
            print(f"   test: edge {fmt(res['test'], 'edge_per_share', cents=True)} win {fmt(res['test'], 'fill_win_rate', pct=True)} "
                  f"roc {fmt(res['test'], 'return_on_cost', pct=True)} fills {res['test']['fills']} pass={res['gate_pass']}", flush=True)
        # grid selection on FIT days only
        if len(runs) > 1:
            sel = max(runs, key=lambda r: (r["fit"] or {}).get("edge_per_share", -9))
        else:
            sel = runs[0]
        for r in runs:
            r["selected"] = (r is sel)
        results.extend(runs)
        if vid == "V0":
            out = os.path.join(a.runs_dir, "V0_phaseA_check")
            pa_files = [f for f in files if any(d in os.path.basename(f) for d in PHASE_A_DAYS)]
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] V0 instrument check on {len(pa_files)} Phase-A day(s) ...", flush=True)
            os.makedirs(out, exist_ok=True)
            for fn in ("sim_rounds.csv", "sim_fills.jsonl", "maker_sim.log"):
                try: os.remove(os.path.join(out, fn))
                except FileNotFoundError: pass
            rc, so, se = sh([PY, os.path.join(HERE, "maker_sim.py"), "--replay", *pa_files, "--labels", a.labels, "--out", out])
            st = json.load(open(os.path.join(out, "maker_stats.json")))
            chk = dict(variant="V0_phaseA_check", rounds=st["rounds"], fills=st["fills"], pnl_usd=st["pnl_usd"],
                       return_on_notional=st["return_on_notional"], return_ci95=st["return_ci95"],
                       mean_adverse_edge=st["mean_adverse_edge"],
                       expected=dict(rounds=1141, fills=3580, pnl_usd=-2825.0, return_on_notional=-0.0795, mean_adverse_edge=-0.0625),
                       reproduced=(st["rounds"] == 1141 and st["fills"] == 3580 and abs(st["pnl_usd"] + 2825.0) < 1.0))
            results.append(chk)
            print(f"   instrument check: rounds {st['rounds']} fills {st['fills']} pnl {st['pnl_usd']} adv {st['mean_adverse_edge']} reproduced={chk['reproduced']}", flush=True)
    json.dump(dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), fit_days=FIT_DAYS, test_days=TEST_DAYS,
                   pilot_capital=PILOT_CAPITAL, files=[os.path.basename(f) for f in files], results=results),
              open(os.path.join(a.runs_dir, "variant_results.json"), "w"), indent=1)
    # ---- summary table
    L = [f"# VARIANT-RESULTS - OPEN-BOOK-MAKER sim sweep ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)", "",
         f"Replay corpus: {len(files)} day files ({os.path.basename(files[0])} .. {os.path.basename(files[-1])}). "
         f"Fit days: {', '.join(FIT_DAYS)}. Test days: {len(TEST_DAYS)} days {TEST_DAYS[0]} .. {TEST_DAYS[-1]}. "
         f"Pilot capital for R5: ${PILOT_CAPITAL:g}. Size 20 sh/side, cap 40. Runtime {int((datetime.now(timezone.utc) - t0).total_seconds())} s.", "",
         "Verdict = all of R1..R6 on TEST days. Every grid point is listed; `sel` marks the point chosen on FIT days.", "",
         "| run | sel | fit edge/sh | fit win | TEST rounds | fills | edge/sh [CI] | win rate [CI] | be(meas) | adv+5s buy/sell | ret on cost [CI] | worst day | R1 R2 R3 R4 R5 R6 | verdict | chain audit |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if "test" not in r:
            continue
        t = r["test"]; f = r.get("fit") or {}
        c = t["criteria"]; flags = " ".join("Y" if c[k] else "n" for k in sorted(c))
        au = r.get("chain_audit", {}); au_s = f"{au.get('match', '?')}/{au.get('checked', '?')} differ {au.get('differ', '?')}"
        L.append(f"| {r['variant']}_{r['tag']} | {'*' if r.get('selected') else ''} | {fmt(f, 'edge_per_share', cents=True)} | {fmt(f, 'fill_win_rate', pct=True)} | "
                 f"{t['rounds']} | {t['fills']} | {fmt(t, 'edge_per_share', cents=True)} {fmt(t, 'edge_ci95', cents=True)} | "
                 f"{fmt(t, 'fill_win_rate', pct=True)} {fmt(t, 'fill_win_ci95', pct=True)} | {fmt(t, 'break_even_measured', pct=True)} | "
                 f"{fmt(t, 'adv5_buy', cents=True)} / {fmt(t, 'adv5_sell', cents=True)} | {fmt(t, 'return_on_cost', pct=True)} {fmt(t, 'return_on_cost_ci95', pct=True)} | "
                 f"${t['worst_day_usd']:.0f} | {flags} | {'PASS' if r['gate_pass'] else 'FAIL'} | {au_s} |")
    L += ["", "## Pre-registered predictions vs outcome", ""]
    for vid in VARIANTS:
        rs = [r for r in results if r.get("variant") == vid and "test" in r]
        if not rs: continue
        sel = [r for r in rs if r.get("selected")] or rs
        t = sel[0]["test"]
        L.append(f"- **{vid}** ({VARIANTS[vid][0]}): predicted \"{PREDICTIONS[vid]}\". Got: edge {fmt(t, 'edge_per_share', cents=True)}/share, "
                 f"win {fmt(t, 'fill_win_rate', pct=True)}, loss per filled round ${(t['pnl_usd'] / t['rounds_with_fills']) if t['rounds_with_fills'] else 0:.2f}, "
                 f"verdict {'PASS' if sel[0]['gate_pass'] else 'FAIL'}.")
    chk = [r for r in results if r.get("variant") == "V0_phaseA_check"]
    if chk:
        c = chk[0]
        L += ["", f"## V0 instrument check (Phase-A days only): rounds {c['rounds']} fills {c['fills']} pnl ${c['pnl_usd']} "
                  f"adverse edge {c['mean_adverse_edge']} vs expected {c['expected']} -> reproduced = {c['reproduced']}"]
    open(os.path.join(a.runs_dir, "VARIANT-RESULTS.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
