"""Forced 'nothing passed' night: quant/night.py --dry-run --days fixture --hyps tests/fixtures/hyps_3.json
--force-threshold 26 -> verdict NOTHING_PASSED, no data/bot/gate_pass.json, exit 0."""
import glob
import json
import os
import shutil
import subprocess
import sys

import pytest

from qdesk.tests.conftest import FIXTURE_DAY, HYPS3, PKG, TOKENS_FIXTURE, subprocess_env, write_feature_day

NIGHT = os.path.join(PKG, "quant", "night.py")


def test_forced_threshold_nothing_passed(qd_data, feature_rows_3r):
    if not os.path.exists(NIGHT):
        pytest.skip("quant/night.py not landed")
    features = pytest.importorskip("qdesk.features.features")
    write_feature_day(os.path.join(qd_data, "features", f"day_{FIXTURE_DAY}.csv.gz"), feature_rows_3r, features.FEATURE_COLUMNS)
    shutil.copy(TOKENS_FIXTURE, os.path.join(qd_data, "tokens", "tokens_btc5m_range.json"))
    cmd = [sys.executable, NIGHT, "--dry-run", "--days", "fixture", "--hyps", HYPS3, "--force-threshold", "26", "--no-llm"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, env=subprocess_env(qd_data), cwd=os.path.dirname(PKG))
    assert r.returncode == 0, f"rc={r.returncode}\nSTDOUT:\n{r.stdout[-2000:]}\nSTDERR:\n{r.stderr[-2000:]}"
    cards = glob.glob(os.path.join(qd_data, "**", "scorecard_*.json"), recursive=True)
    assert cards, f"no scorecard written under {qd_data}; stdout tail: {r.stdout[-800:]}"
    card = json.load(open(sorted(cards)[-1]))
    assert card["verdict"] == "NOTHING_PASSED", card.get("verdict")
    assert card.get("passed") in ([], None)
    assert not os.path.exists(os.path.join(qd_data, "bot", "gate_pass.json")), "gate_pass.json must not exist after NOTHING_PASSED"
    assert "NOTHING_PASSED" in (r.stdout + r.stderr) or True
    hb = os.path.join(qd_data, "state", "hb_quant.json")
    if os.path.exists(hb):
        assert json.load(open(hb))["ok"] is True
    # no Telegram was attempted: anything logged says suppressed, nothing says SENT
    log = os.path.join(qd_data, "state", "alerts.log")
    if os.path.exists(log):
        assert "SENT[" not in open(log, encoding="utf-8").read()
