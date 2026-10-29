#!/usr/bin/env python3
"""qdesk/llm/skeptic.py — the Skeptic audit (INTERFACES §4 skeptic_YYYYMMDD.json).

    python qdesk/llm/skeptic.py --date 20261030 [--dry-run] [--no-llm] [--model strong|cheap]

Order of work:
 1. Deterministic pre-checks (no LLM):
      prereg_hashes_match        prereg.hypotheses[].rule_hash == canonical_hash(rule) for every rule in results
      results_newer_than_prereg  results file mtime (and generated/written stamps when present) after prereg
      holdout_once_per_week      data/state/holdout_ledger.json: every ISO week n <= 1; this night's touch counted
      fields_whitelisted         every rule field in FEATURE_COLUMNS - {market,start,side,asset,src} + ask/bid/abs:/other:,
                                 and no look-ahead helper (ask_ok_100ms, ask_sz_100ms, bid_lvl_sz, sell_*_next1s)
      n_trades_ge_30_on_pass     any scorecard pass needs test n >= 30 (and n_trades score > 1)
      code_hash_match            prereg.code_hash == current engine code hash (qdesk.common.hashing)
      prereg_covers_results      every evaluated rule id is pre-registered (no post-hoc additions)
 2. LLM audit (llm/prompts/skeptic.md) over scorecard, results SUMMARY (no trade list), prereg, prechecks,
    holdout ledger and `git -C <repo> log -p --since=7.days -- qdesk/quant qdesk/common qdesk/features` (cap 200 KB).
 3. skeptic_YYYYMMDD.json: {"ok","findings":[{"severity","what","where"}],"holdout_reuse","weakened_tests",
    "lookahead_fields":[]} + "prechecks" + "model".  ok=false on any high finding or pre-check failure.
`--dry-run`: reads report/samples when data files are missing, no CLI, no Telegram.
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from qdesk.llm import client                       # noqa: E402
from qdesk.llm.hypothesize import field_errors, rule_fields, LOOKAHEAD_FIELDS   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "prompts", "skeptic.md")
SAMPLES = os.path.join(REPO_ROOT, "qdesk", "report", "samples")
GIT_PATHS = ["qdesk/quant", "qdesk/common", "qdesk/features"]
GIT_CAP = 200 * 1024


# ----------------------------------------------------------------------------- inputs
def find(rel, dry_run):
    p = os.path.join(client.data_dir(), rel)
    if os.path.exists(p):
        return p
    if dry_run:
        q = os.path.join(SAMPLES, rel)
        if os.path.exists(q):
            return q
    return None


def load(rel, dry_run, default=None):
    p = find(rel, dry_run)
    if not p:
        return default, None
    try:
        return client.load_json(p), p
    except Exception:
        return default, p


def iso_week(date):
    try:
        from qdesk.quant.holdout_ledger import iso_week as _w   # noqa
        return _w(date)
    except Exception:
        d = datetime.strptime(date, "%Y%m%d")
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"


def rule_hash(rule):
    try:
        from qdesk.common.dsl import canonical_hash   # noqa
        return canonical_hash(rule), "dsl"
    except Exception:
        import hashlib
        core = {k: rule.get(k) for k in ("market", "side", "side_field", "entry", "exit", "limits") if k in rule}
        return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "local"


def current_code_hash():
    try:
        from qdesk.quant.prereg import engine_code_hash   # noqa
        return engine_code_hash()
    except Exception:
        pass
    try:
        from qdesk.common.hashing import engine_hash   # noqa
        return engine_hash()
    except Exception:
        pass
    try:
        from qdesk.common.hashing import code_hash, CODE_FILES_ENGINE   # noqa
        return code_hash(CODE_FILES_ENGINE)
    except Exception:
        return None


# ----------------------------------------------------------------------------- pre-checks
def prechecks(date, hyps, prereg, results, scorecard, ledger, paths, dry_run):
    checks = []

    def add(name, ok, detail, severity="high"):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "severity": severity})

    rules = {h.get("id"): h for h in (hyps or {}).get("hypotheses", []) if isinstance(h, dict)}
    res_rules = (results or {}).get("rules", {}) if isinstance(results, dict) else {}
    pre_h = {h.get("id"): h for h in (prereg or {}).get("hypotheses", []) if isinstance(h, dict)}
    evaluated = list(res_rules.keys()) if isinstance(res_rules, dict) else []

    # 1 hashes
    if not prereg or not results:
        add("prereg_hashes_match", False, f"missing file(s): prereg={'ok' if prereg else 'MISSING'} results={'ok' if results else 'MISSING'}")
    else:
        bad, missing, n = [], [], 0
        hmode = "?"
        for rid in evaluated:
            r = rules.get(rid)
            if r is None:
                missing.append(rid)
                continue
            h, hmode = rule_hash(r)
            n += 1
            if pre_h.get(rid, {}).get("rule_hash") != h:
                bad.append(rid)
        if hmode == "local" and not dry_run:
            add("prereg_hashes_match", False, "common.dsl.canonical_hash unavailable; cannot verify hashes")
        else:
            detail = f"{n - len(bad)}/{n} rule hashes match ({hmode})"
            if missing:
                detail += f"; evaluated but not in hyps file: {missing[:5]}"
            if hmode == "local":
                detail += " [dry-run local hash: informational only]"
            add("prereg_hashes_match", not bad and not missing if hmode == "dsl" else not missing, detail + (f"; MISMATCH {bad[:5]}" if bad else ""))

    # 1b prereg covers results
    extra = [rid for rid in evaluated if rid not in pre_h]
    add("prereg_covers_results", not extra and bool(prereg), f"{len(evaluated)} evaluated, {len(pre_h)} pre-registered" + (f"; NOT PRE-REGISTERED: {extra[:5]}" if extra else ""))

    # 2 ordering
    pp, rp = paths.get("prereg"), paths.get("results")
    if pp and rp:
        try:
            m_ok = os.path.getmtime(rp) >= os.path.getmtime(pp)
        except Exception:
            m_ok = False
        w, g = (prereg or {}).get("written"), (results or {}).get("generated") or (scorecard or {}).get("generated")
        s_ok = True if not (w and g) else str(g) >= str(w)
        samp = pp.startswith(SAMPLES) or rp.startswith(SAMPLES)
        add("results_newer_than_prereg", (m_ok and s_ok) or samp,
            f"prereg written {w or 'n/a'} / results {g or 'n/a'}; mtime order {'ok' if m_ok else 'WRONG'}" + (" [samples: mtime not meaningful]" if samp else ""))
    else:
        add("results_newer_than_prereg", False, "prereg or results file missing")

    # 3 holdout
    weeks = (ledger or {}).get("weeks", {}) if isinstance(ledger, dict) else {}
    over = {w: v.get("n") for w, v in weeks.items() if isinstance(v, dict) and (v.get("n") or 0) > 1}
    this_w = iso_week(date)
    touched_tonight = bool((scorecard or {}).get("holdout_used"))
    n_this = (weeks.get(this_w) or {}).get("n", 0) if isinstance(weeks.get(this_w), dict) else 0
    tonight_ok = True
    if touched_tonight:
        ids = (weeks.get(this_w) or {}).get("touched_by", []) if isinstance(weeks.get(this_w), dict) else []
        # if the ledger already recorded tonight's touch, n_this includes it; otherwise it must be 0 before tonight
        tonight_ok = n_this <= 1
        if (prereg or {}).get("holdout_touch_allowed") is False:
            tonight_ok = False
        detail_t = f"; tonight touched (ledger {this_w} n={n_this} by {ids})"
    else:
        detail_t = f"; tonight not touched ({this_w} n={n_this})"
    add("holdout_once_per_week", not over and tonight_ok, f"n_cum {(ledger or {}).get('n_cum', 'n/a')}" + (f"; weeks over limit: {over}" if over else "") + detail_t)

    # 4 fields
    unknown, look = [], set()
    for rid, r in rules.items():
        for e in field_errors(r):
            if e.startswith("look-ahead"):
                look.add(e.split(": ", 1)[1])
            else:
                unknown.append(f"{rid}:{e}")
    add("fields_whitelisted", not unknown and not look, f"{len(rules)} rules; {len(unknown)} unknown field(s) {unknown[:3]}; look-ahead {sorted(look)}")

    # 5 n >= 30 on pass
    bad_pass = []
    for r in (scorecard or {}).get("rules", []):
        if r.get("pass"):
            rid = r.get("id")
            n = ((res_rules.get(rid) or {}).get("test") or {}).get("n") if isinstance(res_rules, dict) else None
            sc_n = (r.get("scores") or {}).get("n_trades")
            if n is None or n < 30 or (sc_n is not None and sc_n <= 1):
                bad_pass.append(f"{rid} n={n} score_n={sc_n}")
    passes = [r.get("id") for r in (scorecard or {}).get("rules", []) if r.get("pass")]
    add("n_trades_ge_30_on_pass", not bad_pass, f"{len(passes)} pass(es) {passes[:3]}" + (f"; UNDER 30: {bad_pass}" if bad_pass else ""))

    # 6 code hash
    cur = current_code_hash()
    want = (prereg or {}).get("code_hash")
    samp = bool(pp and pp.startswith(SAMPLES))
    if cur is None:
        add("code_hash_match", dry_run, "qdesk.common.hashing unavailable" + (" [dry-run tolerated]" if dry_run else ""))
    elif samp:
        add("code_hash_match", True, f"sample prereg ({want}) vs current {cur} [samples: not comparable]", "low")
    else:
        add("code_hash_match", cur == want, f"prereg {want} vs current {cur}")

    ok = all(c["ok"] for c in checks if c["severity"] == "high")
    return {"ok": ok, "checks": checks, "lookahead_fields": sorted(look)}


# ----------------------------------------------------------------------------- LLM audit
def results_summary(results):
    out = {"engine": results.get("engine"), "lat": results.get("lat"), "spot_lag": results.get("spot_lag"), "rules": {}}
    for rid, rr in (results.get("rules") or {}).items():
        rr = rr or {}
        out["rules"][rid] = {k: rr.get(k) for k in ("per_day", "fit", "test", "windows", "mc_dd", "fill_model")}
        out["rules"][rid]["n_trades_listed"] = len(rr.get("trades") or [])
    return out


def git_log_text(repo=REPO_ROOT, cap=GIT_CAP):
    try:
        r = subprocess.run(["git", "-C", repo, "log", "-p", "--since=7.days", "--no-color", "--"] + GIT_PATHS,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        txt = r.stdout or ""
        if r.returncode != 0:
            txt = f"(git log failed rc={r.returncode}: {(r.stderr or '')[:200]})\n"
    except Exception as e:
        txt = f"(git log unavailable: {e.__class__.__name__})\n"
    if not txt.strip():
        txt = "(no commits touching qdesk/quant, qdesk/common, qdesk/features in the last 7 days)\n"
    if len(txt.encode("utf-8")) > cap:
        txt = txt.encode("utf-8")[:cap].decode("utf-8", errors="ignore") + "\n[... truncated at 200 KB]\n"
    return txt


def llm_audit(date, model, scorecard, results, prereg, pre, ledger, paths):
    d = os.path.join(client.state_dir(), "llm_scratch", "_inputs_skeptic")
    os.makedirs(d, exist_ok=True)
    files = {}
    client.save_json(os.path.join(d, "scorecard.json"), scorecard or {})
    files["scorecard.json"] = os.path.join(d, "scorecard.json")
    client.save_json(os.path.join(d, "results_summary.json"), results_summary(results or {}))
    files["results_summary.json"] = os.path.join(d, "results_summary.json")
    client.save_json(os.path.join(d, "prereg.json"), prereg or {})
    files["prereg.json"] = os.path.join(d, "prereg.json")
    client.save_json(os.path.join(d, "prechecks.json"), pre)
    files["prechecks.json"] = os.path.join(d, "prechecks.json")
    client.save_json(os.path.join(d, "holdout_ledger.json"), ledger or {})
    files["holdout_ledger.json"] = os.path.join(d, "holdout_ledger.json")
    with open(os.path.join(d, "git_log.txt"), "w", encoding="utf-8") as f:
        f.write(git_log_text())
    files["git_log.txt"] = os.path.join(d, "git_log.txt")
    return client.ask_json(PROMPT, files, "skeptic", model=model, max_repair=1, stage="skeptic")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default=os.environ.get("QD_SKEPTIC_MODEL", "strong"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    date = a.date
    hyps, p_h = load(f"hyps/hyps_{date}.json", a.dry_run, {})
    prereg, p_p = load(f"hyps/preregistration_{date}.json", a.dry_run, None)
    results, p_r = load(f"hyps/results_{date}.json", a.dry_run, None)
    scorecard, p_s = load(f"hyps/scorecard_{date}.json", a.dry_run, None)
    ledger, p_l = load("state/holdout_ledger.json", a.dry_run, {"weeks": {}, "n_cum": 0})
    paths = {"hyps": p_h, "prereg": p_p, "results": p_r, "scorecard": p_s, "ledger": p_l}
    pre = prechecks(date, hyps, prereg, results, scorecard, ledger, paths, a.dry_run)

    doc = {"ok": pre["ok"], "date": date, "findings": [], "holdout_reuse": False, "weakened_tests": False,
           "lookahead_fields": pre["lookahead_fields"], "prechecks": pre, "model": None, "inputs": paths}
    for c in pre["checks"]:
        if not c["ok"]:
            doc["findings"].append({"severity": c["severity"], "what": f"pre-check {c['name']} failed: {c['detail']}", "where": "prechecks"})
            if c["name"] == "holdout_once_per_week":
                doc["holdout_reuse"] = True
    if pre["lookahead_fields"]:
        doc["findings"].append({"severity": "high", "what": f"look-ahead fields in rules: {pre['lookahead_fields']}", "where": paths.get("hyps") or "hyps"})

    if a.dry_run or a.no_llm:
        doc["model"] = "none (" + ("dry-run" if a.dry_run else "--no-llm") + ")"
    elif not client.find_cli():
        doc["model"] = "none (no_cli)"
        doc["findings"].append({"severity": "med", "what": "LLM audit skipped: claude CLI not found", "where": "llm/client.py"})
    else:
        obj, meta = llm_audit(date, a.model, scorecard, results, prereg, pre, ledger, paths)
        doc["model"] = meta.get("model")
        doc["llm_meta"] = {k: meta.get(k) for k in ("seconds", "repairs", "num_turns", "cost_usd", "error", "errors")}
        if obj is None:
            doc["findings"].append({"severity": "med", "what": f"LLM audit failed: {meta.get('error')} {str(meta.get('errors'))[:120]}", "where": "llm/client.py"})
        else:
            for f in obj.get("findings") or []:
                doc["findings"].append({"severity": f.get("severity", "low"), "what": str(f.get("what", ""))[:300], "where": str(f.get("where", ""))[:200]})
            doc["holdout_reuse"] = bool(doc["holdout_reuse"] or obj.get("holdout_reuse"))
            doc["weakened_tests"] = bool(obj.get("weakened_tests"))
            doc["lookahead_fields"] = sorted(set(doc["lookahead_fields"]) | {str(x) for x in (obj.get("lookahead_fields") or [])})
            if obj.get("ok") is False and not any(f.get("severity") == "high" for f in obj.get("findings") or []):
                doc["findings"].append({"severity": "high", "what": "LLM auditor returned ok=false without a high finding", "where": "skeptic llm"})
    high = any(f["severity"] == "high" for f in doc["findings"])
    doc["ok"] = bool(pre["ok"] and not high and not doc["holdout_reuse"] and not doc["weakened_tests"] and not doc["lookahead_fields"])
    out = a.out or os.path.join(client.data_dir(), "hyps", f"skeptic_{date}.json")
    client.save_json(out, doc)
    note = f"ok={doc['ok']} prechecks {sum(c['ok'] for c in pre['checks'])}/{len(pre['checks'])} findings {len(doc['findings'])} model {doc['model']} {time.time()-t0:.0f}s"
    try:
        from qdesk.common.tg import heartbeat   # noqa
        heartbeat("skeptic", True, note)
    except Exception:
        client.save_json(os.path.join(client.state_dir(), "hb_skeptic.json"), {"ts": client.utc_iso(), "stage": "skeptic", "ok": True, "note": note})
    print(f"[skeptic] {date}: {note} -> {out}")
    for c in pre["checks"]:
        print(f"  {'ok ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
    for f in doc["findings"]:
        print(f"  finding {f['severity']}: {f['what'][:160]} @ {f['where'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
