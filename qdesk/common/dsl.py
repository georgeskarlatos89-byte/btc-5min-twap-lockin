#!/usr/bin/env python3
"""dsl.py - the hypothesis rule language (INTERFACES.md section 3). stdlib only.

A rule is plain JSON. This module decides, deterministically, whether a rule is well formed
(`validate_rule`), what its identity is (`canonical_hash`), and whether its entry conditions hold
on a feature row (`eval_when`, `chosen_side`). The engine, the bot and the pre-registration step
all import these four functions so one definition of "the rule fired" exists.

Field grammar (the `f` of a condition):
  <col>          any FEATURE_COLUMN except market/start/side/asset/src
  ask            this side's best ask  (= row["ba"])
  bid            this side's best bid  (= row["bb"])
  abs:<col>      absolute value of <col> (col may be ask/bid or a feature column)
  other:<col>    <col> read on the OTHER side's row (col may be ask/bid or a feature column)
NA rule: a value that is None or "" (feature CSVs write NA as the empty string) makes the
condition False. Numbers may arrive as strings from the CSV reader and are coerced with float().
"""
import hashlib
import json

try:
    from qdesk.common.schemas import FEATURE_COLUMNS
except Exception:  # pragma: no cover - direct import of this file outside the package
    from schemas import FEATURE_COLUMNS  # type: ignore

NON_RULE_COLUMNS = frozenset(("market", "start", "side", "asset", "src"))
BASE_FIELDS = tuple(c for c in FEATURE_COLUMNS if c not in NON_RULE_COLUMNS) + ("ask", "bid")
BASE_FIELD_SET = frozenset(BASE_FIELDS)
OPS = ("<", "<=", ">", ">=", "==", "in", "between")
SIDES = ("up", "down", "both", "signal_sign")
MARKETS = ("5m", "15m")
MAX_SIZE_USD = 5.0
# keys dropped from the canonical hash: identity and prose (and the forecast, which is not logic)
HASH_EXCLUDED_KEYS = frozenset(("id", "name", "mechanism", "who_loses", "why_this_could_fail",
                                "novelty_vs_prior", "prediction"))
TIME_FIELDS = ("t_el", "time_left", "session_hour", "dow", "macro_blackout")


# ----------------------------------------------------------------------------- field helpers
def split_field(f):
    """'abs:mom_5s' -> ('abs', 'mom_5s'); 'ask' -> (None, 'ask'). Unknown prefixes stay in the base."""
    if isinstance(f, str) and ":" in f:
        pre, base = f.split(":", 1)
        if pre in ("abs", "other"):
            return pre, base
    return None, f


def field_ok(f):
    """True when f is an allowed condition field (see the module docstring)."""
    if not isinstance(f, str) or not f:
        return False
    pre, base = split_field(f)
    return base in BASE_FIELD_SET


def base_field(f):
    """The feature column a field finally reads ('other:abs' is not allowed; one prefix only)."""
    return split_field(f)[1]


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cond_errors(c, where):
    errs = []
    if not isinstance(c, dict):
        return [f"{where}: condition must be an object"]
    f, op, v = c.get("f"), c.get("op"), c.get("v")
    if not field_ok(f):
        errs.append(f"{where}: field {f!r} is not allowed")
    if op not in OPS:
        errs.append(f"{where}: op {op!r} not in {list(OPS)}")
        return errs
    if op == "in":
        if not isinstance(v, list) or not v or not all(_is_num(x) for x in v):
            errs.append(f"{where}: 'in' needs a non-empty list of numbers")
    elif op == "between":
        if not (isinstance(v, list) and len(v) == 2 and all(_is_num(x) for x in v) and v[0] <= v[1]):
            errs.append(f"{where}: 'between' needs [lo, hi] with lo <= hi")
    else:
        if not _is_num(v):
            errs.append(f"{where}: op {op!r} needs a number")
    extra = set(c) - {"f", "op", "v"}
    if extra:
        errs.append(f"{where}: unknown keys {sorted(extra)}")
    return errs


def _when_errors(when, where):
    if not isinstance(when, list):
        return [f"{where}: must be a list of conditions"]
    errs = []
    for i, c in enumerate(when):
        errs += _cond_errors(c, f"{where}[{i}]")
    return errs


def _leg_errors(leg, side, where):
    errs = []
    if not isinstance(leg, dict):
        return [f"{where}: leg must be an object"]
    t = leg.get("type")
    if t not in ("taker", "maker"):
        return [f"{where}.type must be 'taker' or 'maker'"]
    size = leg.get("size_usd")
    if not _is_num(size) or size <= 0 or size > MAX_SIZE_USD:
        errs.append(f"{where}.size_usd must be a number in (0, {MAX_SIZE_USD}]")
    if t == "taker":
        if leg.get("price") != "ask":
            errs.append(f"{where}.price must be 'ask' for a taker leg")
        if "max_price" in leg and not (_is_num(leg["max_price"]) and 0 < leg["max_price"] < 1):
            errs.append(f"{where}.max_price must be a number in (0, 1)")
        extra = set(leg) - {"type", "price", "size_usd", "max_price"}
    else:
        if leg.get("price") != "bb":
            errs.append(f"{where}.price must be 'bb' for a maker leg")
        ot = leg.get("offset_ticks", 0)
        if not (isinstance(ot, int) and not isinstance(ot, bool)) or ot < 0:
            errs.append(f"{where}.offset_ticks must be an integer >= 0")
        ttl = leg.get("ttl_s")
        if not _is_num(ttl) or ttl <= 0:
            errs.append(f"{where}.ttl_s must be a number > 0")
        if "pair_cap" in leg:
            if side != "both":
                errs.append(f"{where}.pair_cap is only allowed with side 'both'")
            elif not (_is_num(leg["pair_cap"]) and 0 < leg["pair_cap"] < 2):
                errs.append(f"{where}.pair_cap must be a number in (0, 2)")
        extra = set(leg) - {"type", "price", "size_usd", "offset_ticks", "ttl_s", "pair_cap"}
    if extra:
        errs.append(f"{where}: unknown keys {sorted(extra)}")
    return errs


def exit_mode(rule):
    """'hold_to_settle' | 'ttl' | 'rule' | None. Accepts the string form and the object form."""
    ex = rule.get("exit") if isinstance(rule, dict) else None
    if ex == "hold_to_settle":
        return "hold_to_settle"
    if isinstance(ex, dict):
        return ex.get("mode")
    return None


def _exit_errors(ex):
    if ex == "hold_to_settle":
        return []
    if not isinstance(ex, dict):
        return ["exit: must be {'mode': ...} or the string 'hold_to_settle'"]
    mode = ex.get("mode")
    if mode == "hold_to_settle":
        extra = set(ex) - {"mode"}
        return [f"exit: unknown keys {sorted(extra)}"] if extra else []
    if mode == "ttl":
        errs = []
        if not _is_num(ex.get("ttl_s")) or ex.get("ttl_s") <= 0:
            errs.append("exit.ttl_s must be a number > 0")
        extra = set(ex) - {"mode", "ttl_s"}
        if extra:
            errs.append(f"exit: unknown keys {sorted(extra)}")
        return errs
    if mode == "rule":
        errs = _when_errors(ex.get("when"), "exit.when")
        leg = ex.get("leg")
        if not (isinstance(leg, dict) and leg.get("type") == "taker" and leg.get("price") == "bid"):
            errs.append("exit.leg must be {'type':'taker','price':'bid'}")
        extra = set(ex) - {"mode", "when", "leg"}
        if extra:
            errs.append(f"exit: unknown keys {sorted(extra)}")
        return errs
    return [f"exit.mode {mode!r} not in ['hold_to_settle', 'ttl', 'rule']"]


def _limits_errors(lim):
    if not isinstance(lim, dict):
        return ["limits: must be an object"]
    errs = []
    for k in ("max_trades_per_round", "max_rounds_per_day"):
        v = lim.get(k)
        if not (isinstance(v, int) and not isinstance(v, bool)) or v < 1:
            errs.append(f"limits.{k} must be an integer >= 1")
    cd = lim.get("cooldown_s")
    if not _is_num(cd) or cd < 0:
        errs.append("limits.cooldown_s must be a number >= 0")
    extra = set(lim) - {"max_trades_per_round", "cooldown_s", "max_rounds_per_day"}
    if extra:
        errs.append(f"limits: unknown keys {sorted(extra)}")
    return errs


def _prediction_errors(p):
    if not isinstance(p, dict):
        return ["prediction: must be an object"]
    errs = []
    for k in ("net_per_dollar", "trades_per_day"):
        if not _is_num(p.get(k)):
            errs.append(f"prediction.{k} must be a number")
    if p.get("label") not in ("ESTIMATE", "MEASURED"):
        errs.append("prediction.label must be 'ESTIMATE' or 'MEASURED'")
    return errs


# ----------------------------------------------------------------------------- public API
def validate_rule(rule):
    """Return a list of human-readable problems; an empty list means the rule is well formed."""
    if not isinstance(rule, dict):
        return ["rule must be a JSON object"]
    errs = []
    for k in ("id", "name"):
        if not isinstance(rule.get(k), str) or not rule.get(k).strip():
            errs.append(f"{k} must be a non-empty string")
    if rule.get("market") not in MARKETS:
        errs.append(f"market must be one of {list(MARKETS)}")
    side = rule.get("side")
    if side not in SIDES:
        errs.append(f"side must be one of {list(SIDES)}")
    sf = rule.get("side_field")
    if side == "signal_sign":
        if not field_ok(sf):
            errs.append("side_field is required with side 'signal_sign' and must be an allowed field")
    elif sf not in (None, ""):
        if not field_ok(sf):
            errs.append(f"side_field {sf!r} is not an allowed field")
    entry = rule.get("entry")
    if not isinstance(entry, dict):
        errs.append("entry must be an object with 'when' and 'leg'")
    else:
        errs += _when_errors(entry.get("when"), "entry.when")
        errs += _leg_errors(entry.get("leg"), side, "entry.leg")
        extra = set(entry) - {"when", "leg"}
        if extra:
            errs.append(f"entry: unknown keys {sorted(extra)}")
    if "exit" not in rule:
        errs.append("exit is required")
    else:
        errs += _exit_errors(rule.get("exit"))
    if "limits" not in rule:
        errs.append("limits is required")
    else:
        errs += _limits_errors(rule.get("limits"))
    if "prediction" not in rule:
        errs.append("prediction is required")
    else:
        errs += _prediction_errors(rule.get("prediction"))
    for k in ("mechanism", "who_loses", "why_this_could_fail", "novelty_vs_prior"):
        v = rule.get(k)
        if not isinstance(v, str) or len(v.strip()) < 10:
            errs.append(f"{k} must be a sentence (string, >= 10 chars)")
    known = {"id", "name", "market", "side", "side_field", "entry", "exit", "limits", "prediction",
             "mechanism", "who_loses", "why_this_could_fail", "novelty_vs_prior"}
    extra = set(rule) - known
    if extra:
        errs.append(f"unknown top-level keys {sorted(extra)}")
    return errs


def canonical_rule(rule):
    """The logic-only view of a rule (keys in HASH_EXCLUDED_KEYS removed; exit string normalised)."""
    out = {k: v for k, v in rule.items() if k not in HASH_EXCLUDED_KEYS}
    if out.get("exit") == "hold_to_settle":
        out["exit"] = {"mode": "hold_to_settle"}
    return out


def canonical_hash(rule):
    """sha256 hex of the canonical JSON (sorted keys, compact separators) of canonical_rule(rule).
    Stable across key order, whitespace and prose edits; changes when any logic field changes."""
    s = json.dumps(canonical_rule(rule), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def entry_fields(rule):
    """Sorted set of base columns used by the entry conditions (+ side_field); prefixes stripped."""
    out = set()
    entry = rule.get("entry") or {}
    for c in entry.get("when") or []:
        if isinstance(c, dict) and isinstance(c.get("f"), str):
            out.add(base_field(c["f"]))
    if rule.get("side") == "signal_sign" and isinstance(rule.get("side_field"), str):
        out.add(base_field(rule["side_field"]))
    return sorted(out)


def to_num(v):
    """None/''/unparseable -> None (NA); bools -> 0/1; numeric strings -> float."""
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() in ("na", "nan", "none", "null"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def field_value(f, row, other_row=None):
    """Resolve a condition field on a row (see module docstring). Returns a float or None (NA)."""
    pre, base = split_field(f)
    if pre == "other":
        if not other_row:
            return None
        return field_value(base, other_row, None)
    if base == "ask":
        v = to_num(row.get("ba")) if row else None
    elif base == "bid":
        v = to_num(row.get("bb")) if row else None
    else:
        v = to_num(row.get(base)) if row else None
    if pre == "abs" and v is not None:
        v = abs(v)
    return v


def eval_cond(c, row, other_row=None):
    """One condition. NA (None or '') on either side is False; unknown op is False."""
    x = field_value(c.get("f"), row, other_row)
    if x is None:
        return False
    op, v = c.get("op"), c.get("v")
    try:
        if op == "in":
            return any(abs(x - float(y)) < 1e-9 for y in v)
        if op == "between":
            return float(v[0]) <= x <= float(v[1])
        y = float(v)
    except (TypeError, ValueError):
        return False
    if op == "<":
        return x < y
    if op == "<=":
        return x <= y
    if op == ">":
        return x > y
    if op == ">=":
        return x >= y
    if op == "==":
        return abs(x - y) < 1e-9
    return False


def eval_when(when, row, other_row=None):
    """AND of all conditions. An empty list is True (a rule with no entry filter fires always)."""
    if not when:
        return True
    return all(eval_cond(c, row, other_row) for c in when)


def chosen_side(rule, row_up, row_down):
    """Which side the rule wants at this second, or None.
    side 'up'/'down': that side's conditions (its row first, the other row as `other:`).
    side 'both': both sides' conditions must hold (each row with the other as `other:`) -> 'both'.
    side 'signal_sign': side_field read on the Up row (other = Down); > 0 -> Up, < 0 -> Down,
    0 or NA -> None; then that side's conditions must hold."""
    when = (rule.get("entry") or {}).get("when") or []
    side = rule.get("side")
    if side == "up":
        return "Up" if row_up and eval_when(when, row_up, row_down) else None
    if side == "down":
        return "Down" if row_down and eval_when(when, row_down, row_up) else None
    if side == "both":
        if row_up and row_down and eval_when(when, row_up, row_down) and eval_when(when, row_down, row_up):
            return "both"
        return None
    if side == "signal_sign":
        if not row_up:
            return None
        sig = field_value(rule.get("side_field"), row_up, row_down)
        if sig is None or sig == 0:
            return None
        if sig > 0:
            return "Up" if eval_when(when, row_up, row_down) else None
        return "Down" if row_down and eval_when(when, row_down, row_up) else None
    return None
