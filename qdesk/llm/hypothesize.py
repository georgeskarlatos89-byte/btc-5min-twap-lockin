#!/usr/bin/env python3
"""qdesk/llm/hypothesize.py — Quant step 1: >= 10 hypotheses as DSL rules (INTERFACES §3/§4).

    python qdesk/llm/hypothesize.py --date 20261030 [--dry-run] [--model strong|cheap] [--no-llm]

Inputs (copied into the LLM scratch dir as DATA):
    data/evidence/evidence_YYYYMMDD.json   (falls back to the newest evidence file)
    qdesk/prior_evidence.json              (dead families; an empty v3 file is used if missing)
    data/hyps/scorecard_<prev>.json        (newest scorecard before this date, optional)
    feature_columns.txt                    (generated: the allowed field list)
Output:
    data/hyps/hyps_YYYYMMDD.json  {"date","model","hypotheses":[rule...],"rejected":[{"id","reasons"}]}

Every returned rule is validated (common.dsl.validate_rule + common.schemas when present, local check
otherwise), re-numbered H<date>-NN, checked against the dead families (quant.prereg.is_known_dead when
present) and against the look-ahead fields; failures go to "rejected" with reasons.
`--dry-run` writes the 10 bundled sample rules (llm/samples/hyps_sample.json) without calling the CLI.
`--no-llm` (or a missing CLI) writes an empty hypotheses list so quant/library.py can take over.
"""
import argparse
import copy
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from qdesk.llm import client   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "prompts", "hypothesize.md")
SAMPLE = os.path.join(HERE, "samples", "hyps_sample.json")
PRIOR_EVIDENCE = os.path.join(REPO_ROOT, "qdesk", "prior_evidence.json")

# frozen in INTERFACES §2; features.features.FEATURE_COLUMNS is preferred when importable
FEATURE_COLUMNS_FROZEN = (
    "market,start,side,asset,t_el,time_left,bb,ba,bb_sz,ba_sz,spread,mid,imb3,sum_ask2,sum_bid2,"
    "spot,open_twap,spot_bps_vs_open,mom_2s,mom_5s,spot_vol_30s,"
    "flow_sell_10s,flow_buy_10s,trade_n_10s,sweep_flag,"
    "ask_ok_100ms,ask_sz_100ms,bid_lvl_sz,sell_at_bb_next1s,sell_thru_bb_next1s,"
    "session_hour,dow,macro_blackout,liq_long_5m_usd,liq_short_5m_usd,hl_funding_1h,hl_oi_usd,src").split(",")
NOT_RULE_FIELDS = ("market", "start", "side", "asset", "src")
LOOKAHEAD_FIELDS = ("ask_ok_100ms", "ask_sz_100ms", "bid_lvl_sz", "sell_at_bb_next1s", "sell_thru_bb_next1s")


def feature_columns():
    try:
        from qdesk.features.features import FEATURE_COLUMNS   # noqa
        return list(FEATURE_COLUMNS)
    except Exception:
        return list(FEATURE_COLUMNS_FROZEN)


def allowed_fields():
    return [c for c in feature_columns() if c not in NOT_RULE_FIELDS]


def rule_fields(rule):
    """Every raw field name referenced by entry/exit conditions and side_field (abs:/other: stripped)."""
    out = []
    conds = list((rule.get("entry") or {}).get("when") or [])
    ex = rule.get("exit") or {}
    if isinstance(ex, dict):
        conds += list(ex.get("when") or [])
    for c in conds:
        f = str((c or {}).get("f", ""))
        for pre in ("abs:", "other:"):
            if f.startswith(pre):
                f = f[len(pre):]
        out.append(f)
    if rule.get("side_field"):
        out.append(str(rule["side_field"]))
    return out


def field_errors(rule):
    allowed = set(allowed_fields()) | {"ask", "bid"}
    errs = []
    for f in rule_fields(rule):
        if f in LOOKAHEAD_FIELDS:
            errs.append(f"look-ahead field used: {f}")
        elif f not in allowed:
            errs.append(f"unknown field: {f}")
    return errs


def known_dead(rule):
    try:
        from qdesk.quant.prereg import is_known_dead   # noqa
        return is_known_dead(rule)
    except Exception:
        return None


def heartbeat(stage, ok, note=""):
    try:
        from qdesk.common.tg import heartbeat as hb   # noqa
        return hb(stage, ok, note)
    except Exception:
        pass
    client.save_json(os.path.join(client.state_dir(), f"hb_{stage}.json"),
                     {"ts": client.utc_iso(), "stage": stage, "ok": bool(ok), "note": note})


def newest(pattern, before=None):
    files = sorted(glob.glob(pattern))
    if before:
        files = [f for f in files if os.path.basename(f) < before]
    return files[-1] if files else None


def build_inputs(date, dry_run, tmpdir):
    d = client.data_dir()
    inputs, notes = {}, []
    ev = os.path.join(d, "evidence", f"evidence_{date}.json")
    if not os.path.isfile(ev):
        alt = newest(os.path.join(d, "evidence", "evidence_*.json"))
        if alt:
            notes.append(f"evidence_{date}.json missing; using {os.path.basename(alt)}")
            ev = alt
        elif dry_run:
            ev = os.path.join(REPO_ROOT, "qdesk", "report", "samples", "evidence", "evidence_20261030.json")
            notes.append("dry-run: sample evidence")
        else:
            ev = None
    if ev and os.path.isfile(ev):
        inputs["evidence.json"] = ev
    pe = PRIOR_EVIDENCE
    if not os.path.isfile(pe):
        pe = os.path.join(tmpdir, "prior_evidence.json")
        client.save_json(pe, {"version": 3, "families": [], "note": "prior_evidence.json not found in repo"})
        notes.append("prior_evidence.json missing in repo; empty list used")
    inputs["prior_evidence.json"] = pe
    sc = newest(os.path.join(d, "hyps", "scorecard_*.json"), before=f"scorecard_{date}.json")
    if sc:
        inputs["scorecard_prev.json"] = sc
    fc = os.path.join(tmpdir, "feature_columns.txt")
    with open(fc, "w", encoding="utf-8") as f:
        f.write("# allowed rule fields (plus ask, bid, abs:<col>, other:<col>)\n")
        f.write("\n".join(allowed_fields()) + "\n")
        f.write("\n# LOOK-AHEAD fill helpers, never allowed in a rule:\n" + "\n".join(LOOKAHEAD_FIELDS) + "\n")
    inputs["feature_columns.txt"] = fc
    return inputs, notes


def process_rules(raw_rules, raw_rejected, date):
    """Validate, renumber and partition the model's rules. Returns (hypotheses, rejected)."""
    hyps, rejected = [], []
    for r in (raw_rejected or []):
        if isinstance(r, dict):
            rejected.append({"id": str(r.get("id", "?")), "reasons": [str(x) for x in (r.get("reasons") or [])] or ["rejected by the model"]})
    n = 0
    seen_hash = set()
    for i, rule in enumerate(raw_rules or []):
        if not isinstance(rule, dict):
            rejected.append({"id": f"raw[{i}]", "reasons": ["not an object"]})
            continue
        rule = copy.deepcopy(rule)
        n += 1
        rule["id"] = f"H{date}-{n:02d}"
        reasons = client.local_validate_rule(rule) + client.shared_rule_errors(rule) + field_errors(rule)
        dead = known_dead(rule)
        if dead:
            reasons.append(f"known dead family: {dead}")
        try:
            from qdesk.common.dsl import canonical_hash   # noqa
            h = canonical_hash(rule)
        except Exception:
            h = json.dumps({k: v for k, v in rule.items() if k in ("market", "side", "side_field", "entry", "exit", "limits")}, sort_keys=True)
        if h in seen_hash:
            reasons.append("duplicate of an earlier rule (same canonical hash)")
        seen_hash.add(h)
        if reasons:
            rejected.append({"id": rule["id"], "reasons": sorted(set(reasons))})
            continue
        hyps.append(rule)
    return hyps, rejected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default=os.environ.get("QD_HYP_MODEL", "strong"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    d = client.data_dir()
    out = a.out or os.path.join(d, "hyps", f"hyps_{a.date}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmpdir = os.path.join(client.state_dir(), "llm_scratch", "_inputs_hyp")
    os.makedirs(tmpdir, exist_ok=True)

    doc = {"date": a.date, "model": None, "hypotheses": [], "rejected": [], "notes": []}
    if a.dry_run:
        sample = client.load_json(SAMPLE)
        hyps, rejected = process_rules(sample.get("hypotheses"), sample.get("rejected"), a.date)
        doc.update({"model": "dry-run-sample", "hypotheses": hyps, "rejected": rejected,
                    "notes": ["dry-run: bundled sample, no CLI call"]})
    elif a.no_llm or not client.find_cli():
        doc.update({"model": "none", "notes": ["no LLM (" + ("--no-llm" if a.no_llm else "no_cli") + "); quant/library.py supplies the rules"]})
    else:
        inputs, notes = build_inputs(a.date, a.dry_run, tmpdir)
        doc["notes"] += notes
        if "evidence.json" not in inputs:
            doc["notes"].append("no evidence file at all; the model gets prior_evidence + fields only")
        obj, meta = client.ask_json(PROMPT, inputs, "hyps", model=a.model, max_repair=1, stage="hypothesize",
                                    extra_vars={"DATE_COMPACT": a.date})
        doc["model"] = meta.get("model")
        doc["llm_meta"] = {k: meta.get(k) for k in ("seconds", "repairs", "num_turns", "cost_usd", "error", "errors")}
        if obj is None:
            doc["notes"].append(f"LLM failed: {meta.get('error')}; {meta.get('errors')}")
        else:
            hyps, rejected = process_rules(obj.get("hypotheses"), obj.get("rejected"), a.date)
            doc["hypotheses"], doc["rejected"] = hyps, rejected
            if obj.get("warnings"):
                doc["notes"].append("model warnings: " + "; ".join(str(w) for w in obj["warnings"])[:500])
    client.save_json(out, doc)
    n = len(doc["hypotheses"])
    ok = n >= 10 or (a.no_llm or doc["model"] == "none")
    note = f"{n} hypotheses, {len(doc['rejected'])} rejected, model {doc['model']}, {time.time()-t0:.0f}s"
    heartbeat("hypothesize", ok, note)
    print(f"[hypothesize] {a.date}: {note} -> {out}")
    for r in doc["rejected"][:20]:
        print(f"  rejected {r['id']}: {'; '.join(r['reasons'])[:200]}")
    for n_ in doc["notes"]:
        print("  note:", n_)
    return 0 if (ok or n > 0) else 2


if __name__ == "__main__":
    sys.exit(main())
