#!/usr/bin/env python3
"""prereg.py - pre-registration of the night's hypotheses (INTERFACES.md section 4) and the dead-family check.

write_prereg(...) is called BEFORE any evaluation: it freezes rule hashes, predictions, the fit/test day sets,
the walk-forward windows, the scorecard thresholds, the Bonferroni alpha and the code/features hashes, so the
Skeptic can later prove the tests were not weakened after seeing the results.

is_known_dead(rule, prior_evidence=None) matches a rule against the family signatures of prior_evidence.json
(repo root of qdesk) using the `signature_semantics` written in that file: families are listed most specific
first and the first match wins. Keys understood: leg.type, exit.mode, side, entry_fields_subset_of,
entry_fields_any_of, entry_fields_none_of, t_el_min_ge, offset_ticks_max. An unknown signature key never
matches (a rule is only rejected on rules we understand).
"""
import datetime as _dt
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

QDESK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIOR_EVIDENCE_PATH = os.path.join(QDESK_DIR, "prior_evidence.json")
KNOWN_SIG_KEYS = ("leg.type", "exit.mode", "side", "entry_fields_subset_of", "entry_fields_any_of",
                  "entry_fields_none_of", "t_el_min_ge", "offset_ticks_max")


def _common():
    try:
        from qdesk.common import dsl, schemas
    except ImportError as e:
        raise ImportError("qdesk.common (dsl, schemas) is required by quant/prereg.py: %s" % e)
    return dsl, schemas


# ----------------------------------------------------------------------------- dead families
def load_prior_evidence(path=None):
    p = path or PRIOR_EVIDENCE_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _exit_mode(rule):
    ex = rule.get("exit")
    if ex == "hold_to_settle":
        return "hold_to_settle"
    if isinstance(ex, dict):
        return ex.get("mode")
    return None


def _entry_fields(rule):
    """base columns used by entry.when (abs:/other: stripped; ask/bid count) plus side_field."""
    out = set()
    for c in ((rule.get("entry") or {}).get("when") or []):
        f = c.get("f") if isinstance(c, dict) else None
        if isinstance(f, str):
            if ":" in f:
                pre, base = f.split(":", 1)
                f = base if pre in ("abs", "other") else f
            out.add(f)
    sf = rule.get("side_field")
    if rule.get("side") == "signal_sign" and isinstance(sf, str):
        if ":" in sf:
            pre, base = sf.split(":", 1)
            sf = base if pre in ("abs", "other") else sf
        out.add(sf)
    return out


def _t_el_min(rule):
    """largest lower bound on t_el among the entry conditions (t_el >= v, t_el > v, between [lo, hi]); None if none."""
    best = None
    for c in ((rule.get("entry") or {}).get("when") or []):
        if not isinstance(c, dict) or c.get("f") != "t_el":
            continue
        op, v = c.get("op"), c.get("v")
        lo = None
        if op in (">=", ">") and isinstance(v, (int, float)):
            lo = float(v)
        elif op == "between" and isinstance(v, list) and len(v) == 2:
            lo = float(v[0])
        elif op == "==" and isinstance(v, (int, float)):
            lo = float(v)
        if lo is not None and (best is None or lo > best):
            best = lo
    return best


def _match(rule, sig):
    leg = (rule.get("entry") or {}).get("leg") or {}
    fields = _entry_fields(rule)
    for key, want in sig.items():
        if key not in KNOWN_SIG_KEYS:
            return False
        if key == "leg.type":
            if leg.get("type") != want:
                return False
        elif key == "exit.mode":
            if _exit_mode(rule) != want:
                return False
        elif key == "side":
            if rule.get("side") != want:
                return False
        elif key == "entry_fields_subset_of":
            if not fields.issubset(set(want)):
                return False
        elif key == "entry_fields_any_of":
            if not (fields & set(want)):
                return False
        elif key == "entry_fields_none_of":
            if fields & set(want):
                return False
        elif key == "t_el_min_ge":
            lo = _t_el_min(rule)
            if lo is None or lo < float(want):
                return False
        elif key == "offset_ticks_max":
            if int(leg.get("offset_ticks", 0) or 0) > int(want):
                return False
    return True


def is_known_dead(rule, prior_evidence=None):
    """family name of the first matching dead family, else None. prior_evidence: dict | path | None (repo file)."""
    if prior_evidence is None:
        try:
            prior_evidence = load_prior_evidence()
        except (OSError, ValueError):
            return None
    elif isinstance(prior_evidence, str):
        prior_evidence = load_prior_evidence(prior_evidence)
    for fam in prior_evidence.get("families") or []:
        sig = fam.get("signature") or {}
        if sig and _match(rule, sig):
            return fam.get("family")
    return None


# ----------------------------------------------------------------------------- hashes
def features_hash(data_dir):
    """sha256 (16 hex) of the sorted feature file names + sizes in data_dir/features."""
    h = hashlib.sha256()
    files = sorted(glob.glob(os.path.join(data_dir, "features", "day_*.csv.gz")))
    for p in files:
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = -1
        h.update(f"{os.path.basename(p)}:{sz}\n".encode())
    return h.hexdigest()[:16]


def engine_code_hash():
    try:
        from qdesk.common.hashing import code_hash, CODE_FILES_ENGINE
        return code_hash(CODE_FILES_ENGINE)
    except Exception:
        h = hashlib.sha256()
        for fn in ("features/features.py", "quant/rule_engine_1s.py", "quant/rule_replay_events.py", "quant/score.py"):
            p = os.path.join(QDESK_DIR, fn)
            h.update(fn.encode() + b"\0")
            try:
                with open(p, "rb") as f:
                    h.update(f.read().replace(b"\r\n", b"\n"))
            except OSError:
                h.update(b"<missing>")
        return h.hexdigest()[:16]


# ----------------------------------------------------------------------------- write
def write_prereg(date, rules, fit_days, test_days, windows, thresholds, data_dir, holdout_touch_allowed=None, path=None):
    """Write data/hyps/preregistration_<date>.json (section 4) BEFORE evaluation; returns the path."""
    dsl, schemas = _common()
    if holdout_touch_allowed is None:
        try:
            from qdesk.quant.holdout_ledger import may_touch, iso_week
            holdout_touch_allowed = bool(may_touch(iso_week(date), data_dir))
        except Exception:
            holdout_touch_allowed = False
    try:
        from qdesk.common.stats import bonferroni_alpha
    except ImportError as e:
        raise ImportError("qdesk.common.stats is required by quant/prereg.py: %s" % e)
    hyps = [{"id": r.get("id"), "rule_hash": dsl.canonical_hash(r), "prediction": dict(r.get("prediction") or {}),
             "why_this_could_fail": r.get("why_this_could_fail") or ""} for r in rules]
    doc = {
        "date": date,
        "written": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hypotheses": hyps,
        "fit_days": sorted(fit_days),
        "test_days": sorted(test_days),
        "windows": [{"fit": list(w["fit"]), "test": list(w["test"])} for w in windows],
        "thresholds": thresholds,
        "n_hypotheses": len(hyps),
        "bonferroni_alpha": bonferroni_alpha(len(hyps)),
        "holdout_touch_allowed": bool(holdout_touch_allowed),
        "code_hash": engine_code_hash(),
        "features_hash": features_hash(data_dir),
    }
    errs = schemas.validate("prereg", doc)
    if errs:
        raise ValueError("prereg document invalid: " + "; ".join(errs[:5]))
    out = path or os.path.join(data_dir, "hyps", f"preregistration_{date}.json")
    schemas.save_json(out, doc)
    return out


if __name__ == "__main__":
    # smoke: every library rule against the dead families
    from qdesk.quant.library import HYPOTHESES
    for r in HYPOTHESES:
        print(r["id"], is_known_dead(r))
