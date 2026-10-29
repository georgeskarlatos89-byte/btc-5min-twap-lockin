"""bot/test_qbot.py (agent E's >= 16 checks in the test_pm.py style) run as a subprocess:
exit 0, PASS lines present, no FAIL lines. DRY_RUN=true, Telegram suppressed, fresh QD_DATA."""
import os
import re
import subprocess
import sys

import pytest

from qdesk.tests.conftest import PKG, subprocess_env

TEST_QBOT = os.path.join(PKG, "bot", "test_qbot.py")


def test_qbot_self_test_passes(qd_data):
    if not os.path.exists(TEST_QBOT):
        pytest.skip("bot/test_qbot.py not landed")
    r = subprocess.run([sys.executable, TEST_QBOT], capture_output=True, text=True, timeout=600,
                       env=subprocess_env(qd_data), cwd=os.path.dirname(PKG))
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    passes = re.findall(r"(?m)^\s*(?:\[?PASS\]?|PASS\b|ok\b).*$", out) + re.findall(r"(?mi)\bPASS\b", out)
    fails = re.findall(r"(?m)^.*\bFAIL(?:ED)?\b.*$", out)
    fails = [f for f in fails if "0 fail" not in f.lower() and "failed=0" not in f.lower().replace(" ", "")]
    assert r.returncode == 0, f"rc={r.returncode}\n{out[-3000:]}"
    assert len(re.findall(r"(?i)\bPASS\b", out)) >= 16, f"expected >= 16 PASS lines\n{out[-3000:]}"
    assert not fails, fails[:5]


def test_bot_refuses_live_mode(qd_data):
    qbot = os.path.join(PKG, "bot", "qbot.py")
    if not os.path.exists(qbot):
        pytest.skip("bot/qbot.py not landed")
    env = subprocess_env(qd_data); env["DRY_RUN"] = "false"
    r = subprocess.run([sys.executable, qbot, "--dry-run", "--date", "20260926"], capture_output=True, text=True, timeout=60,
                       env=env, cwd=os.path.dirname(PKG))
    assert r.returncode != 0, "qbot must SystemExit when DRY_RUN != 'true' (INTERFACES §9)"
    assert "paper only" in (r.stdout + r.stderr).lower()
