#!/usr/bin/env python3
"""schemas.py - stdlib validator for the desk's JSON documents (INTERFACES.md sections 2-4).

validate(name, obj) -> list of problems (empty = valid). Names: hypothesis, evidence, results,
scorecard, gate_pass, prereg. The hypothesis check delegates to common/dsl.validate_rule so the
rule grammar lives in one place. The .schema.json files next to this module document the same
shapes for humans and for the LLM prompt; this module is what the code runs.

FEATURE_COLUMNS: imported from qdesk.features.features when that module exists, else the frozen
local copy below. Both MUST stay identical to the header in INTERFACES.md section 2 (checked at
import: a mismatch raises so nobody builds on a drifted column list). This module never imports numpy.
"""
import json
import os
import re
import tempfile

_LOCAL_FEATURE_COLUMNS = (
    "market", "start", "side", "asset", "t_el", "time_left", "bb", "ba", "bb_sz", "ba_sz", "spread", "mid",
    "imb3", "sum_ask2", "sum_bid2",
    "spot", "open_twap", "spot_bps_vs_open", "mom_2s", "mom_5s", "spot_vol_30s",
    "flow_sell_10s", "flow_buy_10s", "trade_n_10s", "sweep_flag",
    "ask_ok_100ms", "ask_sz_100ms", "bid_lvl_sz", "sell_at_bb_next1s", "sell_thru_bb_next1s",
    "session_hour", "dow", "macro_blackout", "liq_long_5m_usd", "liq_short_5m_usd", "hl_funding_1h", "hl_oi_usd", "src",
)


def _load_feature_columns():
    try:
        from qdesk.features.features import FEATURE_COLUMNS as cols  # lazy: features/ may not exist yet
    except Exception:
        return _LOCAL_FEATURE_COLUMNS
    cols = tuple(cols)
    if cols != _LOCAL_FEATURE_COLUMNS:
        raise RuntimeError("qdesk.features.features.FEATURE_COLUMNS differs from INTERFACES section 2 "
                           "(common/schemas.py local copy); fix features.py or INTERFACES.md first")
    return cols


FEATURE_COLUMNS = _load_feature_columns()

SCHEMA_NAMES = ("hypothesis", "evidence", "results", "scorecard", "gate_pass", "prereg")
ENGINES = ("rule_engine_1s", "rule_replay_events")
FILL_MODELS = ("taker_ask_ok_100ms", "maker_1s_queue(ESTIMATE)", "events_exact")
FILL_STATUS = ("full", "partial", "none")
SCORE_KEYS = ("oos_tstat", "max_dd", "n_trades", "edge_after_costs", "stability")
LABELS = ("MEASURED", "ESTIMATE")
BOT_STATUS = ("OBSERVE", "PAPER", "DECAYING", "RETIRED")
DAY_RE = re.compile(r"^\d{8}$")
VERDICT_RE = re.compile(r"^(NOTHING_PASSED|CANDIDATE:.+)$")


# ----------------------------------------------------------------------------- json io
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    """Write obj as JSON (indent=1) to a temp file in the same folder, then os.replace (atomic)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, ensure_ascii=False, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise
    return path


# ----------------------------------------------------------------------------- tiny checker
def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


_TYPES = {
    "str": lambda v: isinstance(v, str),
    "num": _is_num,
    "int": _is_int,
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
    "num|null": lambda v: v is None or _is_num(v),
    "str|null": lambda v: v is None or isinstance(v, str),
    "dict|null": lambda v: v is None or isinstance(v, dict),
    "any": lambda v: True,
}


def _req(obj, spec, where, errs):
    """spec: {key: type_name}; appends 'where.key missing' / 'where.key must be <type>'."""
    if not isinstance(obj, dict):
        errs.append(f"{where}: must be an object")
        return False
    for k, t in spec.items():
        if k not in obj:
            errs.append(f"{where}.{k}: missing")
        elif not _TYPES[t](obj[k]):
            errs.append(f"{where}.{k}: must be {t}")
    return True


def _enum(obj, key, allowed, where, errs):
    if isinstance(obj, dict) and key in obj and obj[key] not in allowed:
        errs.append(f"{where}.{key}: {obj[key]!r} not in {list(allowed)}")


def _days(lst, where, errs):
    if isinstance(lst, list):
        for d in lst:
            if not (isinstance(d, str) and DAY_RE.match(d)):
                errs.append(f"{where}: {d!r} is not YYYYMMDD")


def _list_of_dicts(lst, where, errs):
    if not isinstance(lst, list):
        errs.append(f"{where}: must be a list")
        return []
    bad = [i for i, x in enumerate(lst) if not isinstance(x, dict)]
    if bad:
        errs.append(f"{where}: items {bad[:5]} are not objects")
    return [x for x in lst if isinstance(x, dict)]


# ----------------------------------------------------------------------------- per-schema checks
def _v_hypothesis(obj, errs):
    try:
        from qdesk.common.dsl import validate_rule
    except Exception:
        from dsl import validate_rule  # type: ignore
    errs += validate_rule(obj)


def _v_evidence(obj, errs):
    ok = _req(obj, {"date": "str", "generated": "str", "sources": "list", "rounds_yesterday": "dict",
                    "price1m": "dict", "features": "dict", "regime": "dict", "moondev": "dict",
                    "hyperliquid": "dict", "wallets": "dict", "paper_bot": "dict",
                    "prior_evidence_ref": "str", "anomalies": "list"}, "evidence", errs)
    if not ok:
        return
    if "date" in obj and isinstance(obj["date"], str) and not DAY_RE.match(obj["date"]):
        errs.append("evidence.date: must be YYYYMMDD")
    for i, s in enumerate(_list_of_dicts(obj.get("sources", []), "evidence.sources", errs)):
        _req(s, {"name": "str", "ok": "bool", "rows": "int", "note": "str"}, f"evidence.sources[{i}]", errs)
    ry = obj.get("rounds_yesterday")
    if isinstance(ry, dict):
        for mk in ("5m", "15m"):
            if mk in ry:
                _req(ry[mk], {"n": "int", "up_rate": "num|null", "chain_verified": "int", "gamma_disagree": "int"},
                     f"evidence.rounds_yesterday.{mk}", errs)
    if isinstance(obj.get("price1m"), dict):
        _req(obj["price1m"], {"days_archived": "int", "rounds_with_history": "int"}, "evidence.price1m", errs)
    ft = obj.get("features")
    if isinstance(ft, dict):
        _req(ft, {"days_available": "int", "newest": "str|null", "fit_days": "list",
                  "frozen_holdout_days": "list", "forward_days": "list"}, "evidence.features", errs)
        for k in ("fit_days", "frozen_holdout_days", "forward_days"):
            _days(ft.get(k), f"evidence.features.{k}", errs)
        fit, hold = set(ft.get("fit_days") or []), set(ft.get("frozen_holdout_days") or [])
        if fit & hold:
            errs.append(f"evidence.features: fit_days and frozen_holdout_days overlap {sorted(fit & hold)[:3]}")
    rg = obj.get("regime")
    if isinstance(rg, dict):
        _req(rg, {"spot_vol_30s_median_bps": "num|null", "hours_quiet": "list", "hours_active": "list"}, "evidence.regime", errs)
        if "macro_next" in rg and rg["macro_next"] is not None:
            _req(rg["macro_next"], {"name": "str", "utc": "str", "impact": "str"}, "evidence.regime.macro_next", errs)
    if isinstance(obj.get("moondev"), dict):
        _enum(obj["moondev"], "label", LABELS, "evidence.moondev", errs)
        if "label" not in obj["moondev"]:
            errs.append("evidence.moondev.label: missing (MEASURED|ESTIMATE)")
    wl = obj.get("wallets")
    if isinstance(wl, dict):
        _req(wl, {"scanned": "int", "top": "list"}, "evidence.wallets", errs)
        for i, w in enumerate(_list_of_dicts(wl.get("top", []), "evidence.wallets.top", errs)):
            _req(w, {"wallet": "str", "pnl_7d": "num|null", "btc5m_share": "num|null", "maker_share": "num|null",
                     "rounds_per_day": "num|null", "class": "str"}, f"evidence.wallets.top[{i}]", errs)
    pb = obj.get("paper_bot")
    if isinstance(pb, dict):
        _req(pb, {"active_rule": "str|null", "trades_7d": "int", "avg_R": "num|null", "backtest_avg_R": "num|null",
                  "status": "str"}, "evidence.paper_bot", errs)
        _enum(pb, "status", BOT_STATUS + ("NONE",), "evidence.paper_bot", errs)


def _v_results(obj, errs):
    ok = _req(obj, {"engine": "str", "lat": "num", "spot_lag": "num", "rules": "dict"}, "results", errs)
    if not ok:
        return
    _enum(obj, "engine", ENGINES, "results", errs)
    for rid, r in (obj.get("rules") or {}).items():
        w = f"results.rules[{rid}]"
        if not _req(r, {"per_day": "dict", "trades": "list", "fit": "dict", "test": "dict|null", "windows": "list",
                        "mc_dd": "dict", "fill_model": "str"}, w, errs):
            continue
        _enum(r, "fill_model", FILL_MODELS, w, errs)
        for d, pd in (r.get("per_day") or {}).items():
            if not DAY_RE.match(str(d)):
                errs.append(f"{w}.per_day: key {d!r} is not YYYYMMDD")
            _req(pd, {"trades": "int", "cost": "num", "gross": "num", "fees": "num", "net": "num", "rounds": "int"},
                 f"{w}.per_day[{d}]", errs)
        for i, t in enumerate(_list_of_dicts(r.get("trades", []), f"{w}.trades", errs)):
            _req(t, {"day": "str", "start": "int", "side": "str", "t_el": "num", "leg": "str", "price": "num",
                     "size": "num", "fill": "str", "fee": "num", "winner": "str|null", "pnl": "num", "R": "num"},
                 f"{w}.trades[{i}]", errs)
            _enum(t, "fill", FILL_STATUS, f"{w}.trades[{i}]", errs)
            _enum(t, "side", ("Up", "Down"), f"{w}.trades[{i}]", errs)
            _enum(t, "leg", ("taker", "maker"), f"{w}.trades[{i}]", errs)
            if len(errs) > 200:
                errs.append(f"{w}.trades: too many problems, stopped"); break
        for key in ("fit", "test"):
            blk = r.get(key)
            if isinstance(blk, dict):
                _req(blk, {"net_per_dollar": "num|null", "ci95": "list", "n": "int"}, f"{w}.{key}", errs)
                ci = blk.get("ci95")
                if isinstance(ci, list) and not (len(ci) == 2 and all(x is None or _is_num(x) for x in ci)):
                    errs.append(f"{w}.{key}.ci95: must be [lo, hi]")
        for i, win in enumerate(_list_of_dicts(r.get("windows", []), f"{w}.windows", errs)):
            _req(win, {"fit": "list", "test": "list", "net_per_dollar": "num|null", "n": "int"}, f"{w}.windows[{i}]", errs)
        if isinstance(r.get("mc_dd"), dict):
            _req(r["mc_dd"], {"p50": "num", "p95": "num", "n": "int"}, f"{w}.mc_dd", errs)


def _v_scorecard(obj, errs):
    ok = _req(obj, {"date": "str", "rules": "list", "passed": "list", "verdict": "str", "holdout_used": "bool",
                    "n_tried_cum_on_holdout": "int"}, "scorecard", errs)
    if not ok:
        return
    if isinstance(obj.get("verdict"), str) and not VERDICT_RE.match(obj["verdict"]):
        errs.append("scorecard.verdict: must be NOTHING_PASSED or CANDIDATE:<id>")
    ids = set()
    for i, r in enumerate(_list_of_dicts(obj.get("rules", []), "scorecard.rules", errs)):
        w = f"scorecard.rules[{i}]"
        _req(r, {"id": "str", "scores": "dict", "total": "num", "pass": "bool", "reasons": "list",
                 "why_this_could_fail": "str"}, w, errs)
        if isinstance(r.get("id"), str):
            ids.add(r["id"])
        sc = r.get("scores")
        if isinstance(sc, dict):
            for k in SCORE_KEYS:
                v = sc.get(k)
                if not (_is_int(v) and 1 <= v <= 5):
                    errs.append(f"{w}.scores.{k}: must be an integer 1..5")
            if all(_is_int(sc.get(k)) for k in SCORE_KEYS) and _is_num(r.get("total")):
                if abs(sum(sc[k] for k in SCORE_KEYS) - r["total"]) > 1e-9:
                    errs.append(f"{w}.total: does not equal the sum of the five scores")
            if r.get("pass") is True:
                if any(_is_int(sc.get(k)) and sc[k] <= 1 for k in SCORE_KEYS):
                    errs.append(f"{w}: pass=true but a score is 1 (auto-fail)")
                if _is_num(r.get("total")) and r["total"] < 18:
                    errs.append(f"{w}: pass=true but total < 18")
    for p in obj.get("passed", []):
        if p not in ids:
            errs.append(f"scorecard.passed: {p!r} is not a scored rule id")
    v = obj.get("verdict")
    if isinstance(v, str) and v.startswith("CANDIDATE:") and v[10:] not in obj.get("passed", []):
        errs.append("scorecard.verdict: CANDIDATE id must be in passed")
    if v == "NOTHING_PASSED" and obj.get("passed"):
        errs.append("scorecard.verdict: NOTHING_PASSED but passed is not empty")


def _v_gate_pass(obj, errs):
    ok = _req(obj, {"generated": "str", "rule_id": "str", "rule_hash": "str", "code_hash": "str", "corpus_hash": "str",
                    "features_hash": "str", "scorecard_total": "num", "criteria": "dict", "test_days": "list",
                    "skeptic_ok": "bool", "gate_pass": "bool", "paper_only": "bool"}, "gate_pass", errs)
    if not ok:
        return
    _days(obj.get("test_days"), "gate_pass.test_days", errs)
    if obj.get("paper_only") is False:
        errs.append("gate_pass.paper_only: must be true (qdesk v1 is paper only)")
    if obj.get("gate_pass") is True and obj.get("skeptic_ok") is False:
        errs.append("gate_pass: gate_pass=true with skeptic_ok=false")
    if obj.get("gate_pass") is True and _is_num(obj.get("scorecard_total")) and obj["scorecard_total"] < 18:
        errs.append("gate_pass: gate_pass=true with scorecard_total < 18")


def _v_prereg(obj, errs):
    ok = _req(obj, {"date": "str", "written": "str", "hypotheses": "list", "fit_days": "list", "test_days": "list",
                    "windows": "list", "thresholds": "dict", "n_hypotheses": "int", "bonferroni_alpha": "num",
                    "holdout_touch_allowed": "bool", "code_hash": "str", "features_hash": "str"}, "prereg", errs)
    if not ok:
        return
    hyps = _list_of_dicts(obj.get("hypotheses", []), "prereg.hypotheses", errs)
    for i, h in enumerate(hyps):
        _req(h, {"id": "str", "rule_hash": "str", "prediction": "dict"}, f"prereg.hypotheses[{i}]", errs)
    if _is_int(obj.get("n_hypotheses")) and isinstance(obj.get("hypotheses"), list) and obj["n_hypotheses"] != len(obj["hypotheses"]):
        errs.append("prereg.n_hypotheses: does not equal len(hypotheses)")
    _days(obj.get("fit_days"), "prereg.fit_days", errs)
    _days(obj.get("test_days"), "prereg.test_days", errs)
    fit, test = set(obj.get("fit_days") or []), set(obj.get("test_days") or [])
    if fit & test:
        errs.append(f"prereg: fit_days and test_days overlap {sorted(fit & test)[:3]}")
    seen = set()
    for i, w in enumerate(_list_of_dicts(obj.get("windows", []), "prereg.windows", errs)):
        _req(w, {"fit": "list", "test": "list"}, f"prereg.windows[{i}]", errs)
        for d in w.get("test") or []:
            if d in seen:
                errs.append(f"prereg.windows[{i}]: test day {d} reused in another window")
            seen.add(d)
    a = obj.get("bonferroni_alpha")
    if _is_num(a) and not (0 < a <= 0.05):
        errs.append("prereg.bonferroni_alpha: must be in (0, 0.05]")


_CHECKS = {
    "hypothesis": _v_hypothesis,
    "evidence": _v_evidence,
    "results": _v_results,
    "scorecard": _v_scorecard,
    "gate_pass": _v_gate_pass,
    "prereg": _v_prereg,
}


def validate(schema_name, obj):
    """Return a list of problems for obj under the named schema; [] means valid."""
    if schema_name not in _CHECKS:
        return [f"unknown schema {schema_name!r}; known: {list(_CHECKS)}"]
    errs = []
    if not isinstance(obj, dict):
        return [f"{schema_name}: document must be a JSON object"]
    _CHECKS[schema_name](obj, errs)
    return errs


def schema_path(schema_name):
    """Path of the informational JSON schema file for a schema name."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", f"{schema_name}.schema.json")
