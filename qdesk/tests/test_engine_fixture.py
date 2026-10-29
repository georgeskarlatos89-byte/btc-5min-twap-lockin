"""Tier 1 (quant/rule_engine_1s.py) on the fixture features with 3 rules from quant/library.py
(hyps_3.json when the library exposes none) plus one always-firing probe rule: results schema valid,
and one hand-computed taker trade (price, size, fee, pnl, R) matches INTERFACES §8."""
import copy
import json
import os
import shutil

import pytest

from qdesk.tests.conftest import FIXTURE_DAY, TOKENS_FIXTURE, load_hyps3, write_feature_day

features = pytest.importorskip("qdesk.features.features")
engine = pytest.importorskip("qdesk.quant.rule_engine_1s")
schemas = pytest.importorskip("qdesk.common.schemas")
fees = pytest.importorskip("qdesk.common.fees")

PROBE = {
    "id": "HPROBE-UP", "name": "probe: buy Up once between t_el 100 and 130", "market": "5m", "side": "up",
    "entry": {"when": [{"f": "t_el", "op": "between", "v": [100, 130]}, {"f": "ask", "op": "<=", "v": 0.99}],
              "leg": {"type": "taker", "price": "ask", "size_usd": 5}},
    "exit": {"mode": "hold_to_settle"},
    "limits": {"max_trades_per_round": 1, "cooldown_s": 0, "max_rounds_per_day": 100},
    "prediction": {"net_per_dollar": 0.0, "trades_per_day": 1, "label": "ESTIMATE"},
    "mechanism": "test probe that buys Up once per round", "who_loses": "nobody, this is a test probe",
    "why_this_could_fail": "it is a probe with no edge at all", "novelty_vs_prior": "not a hypothesis, only an engine check",
}


def library_rules():
    """3 rules from quant/library.py if it exposes a list/function; else the 3 fixture rules."""
    try:
        import importlib
        lib = importlib.import_module("qdesk.quant.library")
    except ImportError:
        return load_hyps3()["hypotheses"]
    for name in ("RULES", "LIBRARY", "HYPOTHESES", "rules", "library", "hypotheses", "get_rules", "load", "all_rules"):
        obj = getattr(lib, name, None)
        if callable(obj):
            try: obj = obj()
            except TypeError: continue
        if isinstance(obj, dict) and "hypotheses" in obj:
            obj = obj["hypotheses"]
        if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "entry" in obj[0]:
            return obj[:3]
    return load_hyps3()["hypotheses"]


def num(v):
    return None if v in (None, "") else float(v)


@pytest.fixture
def engine_data(qd_data, feature_rows_3r):
    write_feature_day(os.path.join(qd_data, "features", f"day_{FIXTURE_DAY}.csv.gz"), feature_rows_3r, features.FEATURE_COLUMNS)
    shutil.copy(TOKENS_FIXTURE, os.path.join(qd_data, "tokens", "tokens_btc5m_range.json"))
    shutil.copy(TOKENS_FIXTURE, os.path.join(qd_data, "tokens", f"tokens_{FIXTURE_DAY}.json"))
    return qd_data


def test_probe_rule_is_valid():
    dsl = pytest.importorskip("qdesk.common.dsl")
    assert dsl.validate_rule(PROBE) == []


def test_tier1_results_schema_and_hand_computed_trade(engine_data, feature_rows_3r):
    rules = library_rules() + [PROBE]
    res = engine.run(rules, [FIXTURE_DAY], engine_data, lat=0.1)
    assert isinstance(res, dict)
    errs = schemas.validate("results", res)
    assert errs == [], errs
    assert res["engine"] == "rule_engine_1s" and res["lat"] == 0.1 and res["spot_lag"] == 1.44
    assert set(res["rules"]) == {r["id"] for r in rules}
    for rid, rr in res["rules"].items():
        assert FIXTURE_DAY in rr["per_day"], rid
        for tr in rr["trades"]:
            assert tr["fill"] in ("full", "partial", "none")
            assert tr["side"] in ("Up", "Down") and tr["day"] == FIXTURE_DAY
            if tr["fill"] != "none":
                assert tr["R"] == pytest.approx(tr["pnl"] / 5.0, abs=1e-6) or tr["R"] == pytest.approx(tr["pnl"] / (tr["size"] * tr["price"]), abs=1e-6)

    tok = json.load(open(TOKENS_FIXTURE))
    probe = res["rules"]["HPROBE-UP"]
    trades = [t for t in probe["trades"] if t["fill"] in ("full", "partial")]
    assert trades, "probe rule never filled: the engine must fire at the first second with ask_ok_100ms==1 in [100,130]"
    tr = sorted(trades, key=lambda t: (t["start"], t["t_el"]))[0]
    st = int(tr["start"])
    up_rows = {int(r["t_el"]): r for r in feature_rows_3r if int(r["start"]) == st and r["side"] == "Up"}
    # the engine's fill second must be the FIRST second in the window where the ask was still there at +100 ms
    first_ok = next(s for s in range(100, 131) if num(up_rows[s]["ask_ok_100ms"]) == 1.0 and num(up_rows[s]["ba"]) is not None)
    assert int(tr["t_el"]) == first_ok, (tr["t_el"], first_ok)
    row = up_rows[first_ok]
    ask = num(row["ba"]); ask_sz = num(row["ask_sz_100ms"])
    want = 5.0 / ask
    size = min(want, ask_sz) if ask_sz is not None else want
    fee = fees.taker_fee(ask, size)
    winner = next(v["winner"] for v in tok.values() if int(v["start"]) == st)
    pnl = (size if winner == "Up" else 0.0) - size * ask - fee
    assert tr["price"] == pytest.approx(ask, abs=1e-9)
    assert tr["size"] == pytest.approx(size, abs=1e-4)
    assert tr["fee"] == pytest.approx(fee, abs=2e-5)
    assert tr["winner"] == winner
    assert tr["pnl"] == pytest.approx(pnl, abs=1e-4)
    assert tr["fill"] == ("full" if size >= want - 1e-9 else "partial")
    if tr["fill"] == "full":
        assert tr["R"] == pytest.approx(pnl / 5.0, abs=1e-6)
    pd = probe["per_day"][FIXTURE_DAY]
    assert pd["trades"] == len(trades) and pd["net"] == pytest.approx(sum(t["pnl"] for t in trades), abs=1e-4)
    assert set(probe["mc_dd"]) >= {"p50", "p95", "n"} and probe["mc_dd"]["p95"] >= probe["mc_dd"]["p50"]
    assert probe["fill_model"] == "taker_ask_ok_100ms"


def test_rule_limits_respected(engine_data):
    r = copy.deepcopy(PROBE); r["id"] = "HPROBE-2"; r["limits"]["max_trades_per_round"] = 1
    res = engine.run([r], [FIXTURE_DAY], engine_data, lat=0.1)
    per_round = {}
    for t in res["rules"]["HPROBE-2"]["trades"]:
        if t["fill"] != "none":
            per_round[t["start"]] = per_round.get(t["start"], 0) + 1
    assert all(n <= 1 for n in per_round.values()), per_round
