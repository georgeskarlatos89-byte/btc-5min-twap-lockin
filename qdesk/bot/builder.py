#!/usr/bin/env python3
"""builder.py - the Builder stage (05:30 UTC): turns tonight's CANDIDATE into the paper bot's active rule.

  python3 bot/builder.py --date YYYYMMDD [--dry-run] [--data DIR]

Reads data/hyps/scorecard_<date>.json, skeptic_<date>.json, hyps_<date>.json, results_<date>.json and
preregistration_<date>.json. When the verdict is CANDIDATE:<id> and the skeptic is ok it writes
  data/bot/gate_pass.json  (INTERFACES section 4: code_hash over hashing.CODE_FILES_BOT, corpus_hash over the feature
                            day files the backtest used, features_hash from the preregistration, paper_only true)
  data/bot/active_rule.json
  data/bot/strategy_state.json (status PAPER, backtest_avg_R from the results)
ONLY IF the current strategy_state is OBSERVE/RETIRED/DECAYING (or absent) or the incumbent has >= 7 paper days
and the new scorecard total is higher. Every decision goes to strategy_log.csv. It never calls systemctl: when the
bot must pick up a new rule it writes data/bot/RESTART-REQUESTED and prints it. Heartbeat "builder". Exit 0 always.
--dry-run: computes and prints everything, writes nothing except the heartbeat; sets LAB_NO_TELEGRAM=1.
"""
import argparse, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from qdesk.bot import _util

MIN_INCUMBENT_DAYS = 7


def log(msg):
    print(f"[builder {_util.iso()}] {msg}", flush=True)


def find_rule(hyps, rule_id):
    for r in (hyps or {}).get("hypotheses") or []:
        if isinstance(r, dict) and r.get("id") == rule_id:
            return r
    return None


def backtest_avg_R(results, rule_id):
    """net per dollar of the OOS test slice (= avg R per dollar traded), fit when no test ran."""
    r = ((results or {}).get("rules") or {}).get(rule_id) or {}
    for k in ("test", "fit"):
        d = r.get(k)
        if isinstance(d, dict) and d.get("net_per_dollar") is not None:
            return float(d["net_per_dollar"]), k
    tr = [t.get("R") for t in r.get("trades") or [] if isinstance(t, dict) and t.get("R") is not None]
    if tr:
        return sum(float(x) for x in tr) / len(tr), "trades"
    return None, None


def feature_files(ddir, days):
    out = []
    for d in days or []:
        p = os.path.join(ddir, "features", f"day_{d}.csv.gz")
        if os.path.exists(p): out.append(p)
    return out


def days_in_paper(state, now_ts):
    since = _util.parse_iso((state or {}).get("since"))
    return (now_ts - since) / 86400.0 if since else 0.0


def decide_activation(state, incumbent_total, new_total, now_ts):
    """-> (activate: bool, reason)."""
    status = (state or {}).get("status") or "OBSERVE"
    if status in ("OBSERVE", "RETIRED", "DECAYING") or not (state or {}).get("rule_id"):
        return True, f"incumbent status {status}"
    days = days_in_paper(state, now_ts)
    if days < MIN_INCUMBENT_DAYS:
        return False, f"incumbent PAPER for {days:.1f} days (< {MIN_INCUMBENT_DAYS}); candidate held"
    if incumbent_total is not None and new_total is not None and new_total > incumbent_total:
        return True, f"incumbent PAPER {days:.1f} days, candidate total {new_total} > incumbent {incumbent_total}"
    return False, f"incumbent PAPER {days:.1f} days, candidate total {new_total} not above incumbent {incumbent_total}; candidate held"


def run(date, ddir, dry_run=False, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    hd = os.path.join(ddir, "hyps"); bd = os.path.join(ddir, "bot")
    sc = _util.load_json(os.path.join(hd, f"scorecard_{date}.json"))
    out = dict(date=date, verdict=None, activated=False, restart=False, reason="")
    if not isinstance(sc, dict):
        out["reason"] = f"no scorecard_{date}.json"; log(out["reason"]); return out
    verdict = str(sc.get("verdict") or "NOTHING_PASSED"); out["verdict"] = verdict
    if not verdict.startswith("CANDIDATE:"):
        out["reason"] = f"verdict {verdict}: nothing to build"; log(out["reason"])
        if not dry_run: _util.strategy_log(ddir, "", "NOTHING_PASSED", f"builder {date}")
        return out
    rule_id = verdict.split(":", 1)[1].strip()
    sk = _util.load_json(os.path.join(hd, f"skeptic_{date}.json"), {}) or {}
    if sk.get("ok") is not True:
        out["reason"] = f"candidate {rule_id} but skeptic ok={sk.get('ok')}: not built"; log(out["reason"])
        if not dry_run: _util.strategy_log(ddir, rule_id, "HELD", "skeptic not ok")
        return out
    hyps = _util.load_json(os.path.join(hd, f"hyps_{date}.json"), {}) or {}
    rule = find_rule(hyps, rule_id)
    if rule is None:
        out["reason"] = f"candidate {rule_id} not found in hyps_{date}.json"; log(out["reason"])
        if not dry_run: _util.strategy_log(ddir, rule_id, "HELD", "rule missing from hyps file")
        return out
    dsl = _util.common("dsl"); hashing = _util.common("hashing")
    errs = dsl.validate_rule(rule)
    if errs:
        out["reason"] = f"candidate {rule_id} fails validate_rule: {errs[:3]}"; log(out["reason"])
        if not dry_run: _util.strategy_log(ddir, rule_id, "HELD", "validate_rule: " + "; ".join(errs[:3]))
        return out
    rule_hash = dsl.canonical_hash(rule)
    prereg = _util.load_json(os.path.join(hd, f"preregistration_{date}.json"), {}) or {}
    pre = next((h for h in prereg.get("hypotheses") or [] if isinstance(h, dict) and h.get("id") == rule_id), None)
    if pre and pre.get("rule_hash") and pre["rule_hash"] != rule_hash:
        out["reason"] = f"candidate {rule_id}: rule_hash differs from the preregistration ({pre['rule_hash'][:12]} vs {rule_hash[:12]}): not built"; log(out["reason"])
        if not dry_run: _util.strategy_log(ddir, rule_id, "HELD", "rule hash != prereg hash")
        return out
    results = _util.load_json(os.path.join(hd, f"results_{date}.json"), {}) or {}
    bt, bt_src = backtest_avg_R(results, rule_id)
    entry = next((r for r in sc.get("rules") or [] if isinstance(r, dict) and r.get("id") == rule_id), {}) or {}
    new_total = entry.get("total")
    test_days = sorted(set((prereg.get("test_days") or []) + [d for w in prereg.get("windows") or [] for d in (w.get("test") or [])]))
    fit_days = prereg.get("fit_days") or []
    used = feature_files(ddir, list(fit_days) + test_days)
    corpus = hashing.corpus_hash(used) if used else ""
    state = _util.load_json(os.path.join(bd, "strategy_state.json"), {}) or {}
    inc_gate = _util.load_json(os.path.join(bd, "gate_pass.json"), {}) or {}
    activate, why = decide_activation(state, inc_gate.get("scorecard_total"), new_total, now_ts)
    out.update(rule_id=rule_id, rule_hash=rule_hash, backtest_avg_R=bt, scorecard_total=new_total, corpus_files=len(used), reason=why)
    log(f"candidate {rule_id} total={new_total} backtest_avg_R={bt} ({bt_src}) corpus_files={len(used)}: {'ACTIVATE' if activate else 'HOLD'} ({why})")
    if not activate:
        if not dry_run: _util.strategy_log(ddir, rule_id, "HELD", why)
        return out
    gate_doc = {"generated": _util.iso(now_ts), "rule_id": rule_id, "rule_hash": rule_hash,
                "code_hash": hashing.code_hash(hashing.CODE_FILES_BOT), "corpus_hash": corpus,
                "features_hash": prereg.get("features_hash") or "", "scorecard_total": new_total,
                "criteria": entry.get("scores") or {}, "test_days": test_days, "skeptic_ok": True,
                "gate_pass": True, "paper_only": True, "backtest_avg_R": bt, "built_from": f"scorecard_{date}.json"}
    if not corpus:
        gate_doc["gate_pass"] = False; gate_doc["note"] = "no feature day files found for the corpus hash: gate_pass false"
        log("WARNING: no feature files found -> gate_pass.json written with gate_pass=false (bot stays in OBSERVE)")
    new_state = {"rule_id": rule_id, "status": "PAPER", "trades": 0, "avg_R": None, "backtest_avg_R": bt, "since": _util.iso(now_ts)}
    out.update(activated=True, restart=True, gate_pass=gate_doc["gate_pass"])
    if dry_run:
        log(f"DRY RUN: would write gate_pass.json, active_rule.json, strategy_state.json and RESTART-REQUESTED for {rule_id}")
        return out
    _util.save_json(os.path.join(bd, "gate_pass.json"), gate_doc)
    _util.save_json(os.path.join(bd, "active_rule.json"), rule)
    _util.save_json(os.path.join(bd, "strategy_state.json"), new_state)
    _util.strategy_log(ddir, rule_id, "ACTIVATED", f"{why}; total={new_total}; backtest_avg_R={bt}; gate_pass={gate_doc['gate_pass']}")
    marker = os.path.join(bd, "RESTART-REQUESTED")
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"{_util.iso(now_ts)} rule {rule_id} activated by builder {date}; restart qd-bot to load it\n")
    log(f"RESTART REQUIRED: qd-bot must be restarted to load {rule_id} (marker {marker}; this stage never calls systemctl)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_util.day_str()); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", default=None); ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--days", nargs="*"); ap.add_argument("--hyps"); ap.add_argument("--force-threshold", type=float)
    a = ap.parse_args()
    if a.dry_run: os.environ["LAB_NO_TELEGRAM"] = "1"
    ddir = _util.data_dir(a.data)
    try:
        out = run(a.date, ddir, a.dry_run)
        note = f"{out.get('verdict')} activated={out.get('activated')} restart={out.get('restart')} {out.get('reason', '')}"
        _util.write_heartbeat(ddir, "builder", True, note)
        print("RESTART REQUIRED" if out.get("restart") else "no restart needed")
    except Exception as e:
        traceback.print_exc()
        _util.write_heartbeat(ddir, "builder", False, f"{type(e).__name__}: {str(e)[:200]}")
    sys.exit(0)


if __name__ == "__main__":
    main()
