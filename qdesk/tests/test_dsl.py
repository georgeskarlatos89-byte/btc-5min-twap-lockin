"""common/dsl.py against INTERFACES §3: accept the example, reject forbidden fields/ops,
canonical_hash stable across key order, eval_when with abs:/other:/NA, chosen_side."""
import copy
import json

import pytest

dsl = pytest.importorskip("qdesk.common.dsl")

EXAMPLE = {
    "id": "H20261030-03", "name": "late-round momentum taker, quiet hours", "market": "5m",
    "side": "signal_sign", "side_field": "mom_5s",
    "entry": {"when": [{"f": "t_el", "op": ">=", "v": 200}, {"f": "t_el", "op": "<=", "v": 270},
                       {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 4}, {"f": "ask", "op": "<=", "v": 0.80},
                       {"f": "session_hour", "op": "in", "v": [0, 1, 2, 3, 4, 5]}, {"f": "macro_blackout", "op": "==", "v": 0}],
              "leg": {"type": "taker", "price": "ask", "size_usd": 5, "max_price": 0.85}},
    "exit": {"mode": "hold_to_settle"},
    "limits": {"max_trades_per_round": 1, "cooldown_s": 30, "max_rounds_per_day": 60},
    "prediction": {"net_per_dollar": 0.01, "trades_per_day": 25, "label": "ESTIMATE"},
    "mechanism": "one paragraph", "who_loses": "one paragraph", "why_this_could_fail": "mandatory paragraph",
    "novelty_vs_prior": "how it differs from the dead families",
}


def rule(**patch):
    r = copy.deepcopy(EXAMPLE)
    r.update(patch)
    return r


def test_example_accepted():
    assert dsl.validate_rule(EXAMPLE) == []


def test_hyps_3_fixture_rules_accepted():
    from qdesk.tests.conftest import load_hyps3
    for h in load_hyps3()["hypotheses"]:
        assert dsl.validate_rule(h) == [], h["id"]


@pytest.mark.parametrize("field", ["winner", "src", "market", "start", "side", "asset", "pnl", "outcomePrices", "other:abs:mom_5s", ""])
def test_forbidden_field_rejected(field):
    r = rule()
    r["entry"]["when"].append({"f": field, "op": ">", "v": 0})
    assert dsl.validate_rule(r), field


@pytest.mark.parametrize("op", ["!=", "regex", "contains", "=", ">>"])
def test_forbidden_op_rejected(op):
    r = rule()
    r["entry"]["when"][0]["op"] = op
    assert dsl.validate_rule(r), op


def test_size_cap_and_mandatory_prose():
    r = rule()
    r["entry"]["leg"]["size_usd"] = 6
    assert dsl.validate_rule(r)
    r = rule()
    del r["why_this_could_fail"]
    assert dsl.validate_rule(r)
    r = rule()
    del r["side_field"]
    assert dsl.validate_rule(r), "signal_sign without side_field must be rejected"
    r = rule(side="sideways")
    assert dsl.validate_rule(r)
    r = rule(exit={"mode": "ttl"})
    assert dsl.validate_rule(r), "ttl exit needs ttl_s"


def test_canonical_hash_stable_across_key_order_and_prose():
    h0 = dsl.canonical_hash(EXAMPLE)
    reordered = json.loads(json.dumps(EXAMPLE), object_pairs_hook=lambda kv: dict(reversed(kv)))
    assert list(reordered.keys()) != list(EXAMPLE.keys())
    assert dsl.canonical_hash(reordered) == h0
    assert dsl.canonical_hash(rule(id="OTHER", name="other name", mechanism="x")) == h0
    assert len(h0) == 64 and all(c in "0123456789abcdef" for c in h0)
    changed = rule()
    changed["entry"]["when"][0]["v"] = 201
    assert dsl.canonical_hash(changed) != h0


ROW_UP = {"t_el": 250, "time_left": 50, "bb": 0.61, "ba": 0.63, "mom_5s": 2.5, "spot_bps_vs_open": -5.0,
          "session_hour": 3, "macro_blackout": 0, "sweep_flag": "", "spot_vol_30s": None}
ROW_DOWN = {"t_el": 250, "time_left": 50, "bb": 0.37, "ba": 0.39, "mom_5s": -2.5, "spot_bps_vs_open": 5.0,
            "session_hour": 3, "macro_blackout": 0, "sweep_flag": "", "spot_vol_30s": None}


def test_eval_when_abs_other_and_na():
    W = lambda *c: list(c)
    assert dsl.eval_when(W({"f": "abs:spot_bps_vs_open", "op": ">=", "v": 4}), ROW_UP, ROW_DOWN)
    assert not dsl.eval_when(W({"f": "spot_bps_vs_open", "op": ">=", "v": 4}), ROW_UP, ROW_DOWN)
    assert dsl.eval_when(W({"f": "other:ba", "op": "<", "v": 0.40}), ROW_UP, ROW_DOWN)
    assert dsl.eval_when(W({"f": "other:ask", "op": "<", "v": 0.40}), ROW_UP, ROW_DOWN)
    assert dsl.eval_when(W({"f": "ask", "op": "==", "v": 0.63}, {"f": "bid", "op": "==", "v": 0.61}), ROW_UP, ROW_DOWN)
    assert dsl.eval_when(W({"f": "session_hour", "op": "in", "v": [0, 1, 2, 3]}), ROW_UP, ROW_DOWN)
    assert dsl.eval_when(W({"f": "t_el", "op": "between", "v": [200, 270]}), ROW_UP, ROW_DOWN)
    # NA ('' or None) -> False, whatever the op
    assert not dsl.eval_when(W({"f": "sweep_flag", "op": "==", "v": 0}), ROW_UP, ROW_DOWN)
    assert not dsl.eval_when(W({"f": "spot_vol_30s", "op": "<", "v": 100}), ROW_UP, ROW_DOWN)
    assert not dsl.eval_when(W({"f": "other:spot_vol_30s", "op": "<", "v": 100}), ROW_UP, ROW_DOWN)
    assert not dsl.eval_when(W({"f": "other:mom_5s", "op": "<", "v": 0}), ROW_UP, None)
    assert dsl.eval_when([], ROW_UP, ROW_DOWN)
    # CSV strings are numbers too
    assert dsl.eval_when(W({"f": "t_el", "op": ">=", "v": 200}), {"t_el": "250"}, None)


def test_chosen_side():
    assert dsl.chosen_side(EXAMPLE, ROW_UP, ROW_DOWN) == "Up"
    neg_up = dict(ROW_UP, mom_5s=-1.0)
    assert dsl.chosen_side(EXAMPLE, neg_up, ROW_DOWN) == "Down"
    assert dsl.chosen_side(EXAMPLE, dict(ROW_UP, mom_5s=0.0), ROW_DOWN) is None
    assert dsl.chosen_side(EXAMPLE, dict(ROW_UP, mom_5s=""), ROW_DOWN) is None
    r = rule(side="up"); r.pop("side_field")
    assert dsl.chosen_side(r, ROW_UP, ROW_DOWN) == "Up"
    r = rule(side="down"); r.pop("side_field")
    assert dsl.chosen_side(r, ROW_UP, ROW_DOWN) == "Down"
    r = rule(side="both"); r.pop("side_field")
    r["entry"]["when"] = [{"f": "ask", "op": "<=", "v": 0.65}]
    assert dsl.chosen_side(r, ROW_UP, ROW_DOWN) == "both"
    r["entry"]["when"] = [{"f": "ask", "op": "<=", "v": 0.50}]
    assert dsl.chosen_side(r, ROW_UP, ROW_DOWN) is None
