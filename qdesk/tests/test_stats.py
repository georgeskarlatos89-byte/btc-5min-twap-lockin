"""common/stats.py (INTERFACES §5): boot_ci reproducible with seed 7, mc_drawdown keys/p95>=p50,
walk_forward test slices disjoint and ordered, bonferroni."""
import random

import pytest

stats = pytest.importorskip("qdesk.common.stats")

_R = random.Random(11)
VALS = [_R.gauss(0.02, 0.5) for _ in range(200)]
_R3 = random.Random(3)
PNLS = [_R3.gauss(0.0, 1.0) for _ in range(300)]


def test_boot_ci_reproducible_and_sane():
    a = stats.boot_ci(VALS, seed=7)
    b = stats.boot_ci(VALS, seed=7)
    assert a == b
    lo, hi = a
    assert lo < hi
    assert lo <= sum(VALS) <= hi
    lo2, hi2 = stats.boot_ci(VALS, n=500, seed=7)
    assert lo2 < hi2
    assert stats.boot_ci([5.0] * 10, seed=7) == (50.0, 50.0)
    lo3, hi3 = stats.boot_ci(VALS, seed=7, alpha=0.5)
    assert lo <= lo3 < hi3 <= hi, "narrower alpha band must sit inside the 95% band"


def test_tstat_sign_and_growth():
    assert stats.tstat([1.0, 2.0, 3.0, 4.0, 5.0]) > 0
    assert stats.tstat([-1.0, -2.0, -3.0, -4.0, -5.0]) < 0
    assert abs(stats.tstat([-1.0, 1.0, -1.0, 1.0])) < 1e-9
    assert stats.tstat([0.1, -0.1, 0.1, -0.1] * 25 + [0.05] * 100) > stats.tstat([0.1, -0.1, 0.1, -0.1] + [0.05])


def test_mc_drawdown():
    pnls = PNLS
    assert len(set(pnls)) > 100
    d = stats.mc_drawdown(pnls, capital=100.0, n=1000, seed=7)
    assert d["p95"] > d["p50"] > 0
    assert set(d) >= {"p50", "p95", "n"} and d["n"] == 1000
    assert d["p95"] >= d["p50"] >= 0
    assert d == stats.mc_drawdown(pnls, capital=100.0, n=1000, seed=7)
    assert stats.mc_drawdown([1.0, 1.0, 1.0], n=50)["p95"] == 0
    assert stats.mc_drawdown([-10.0, -10.0], capital=100.0, n=50)["p50"] == pytest.approx(20.0)


def test_walk_forward_disjoint_ordered():
    days = [f"202610{d:02d}" for d in range(1, 26)]        # 25 days
    wins = stats.walk_forward(days, k=5, fit=6, test=3)
    assert 1 <= len(wins) <= 5
    seen_test = set()
    prev_last = None
    for w in wins:
        assert set(w) >= {"fit", "test"}
        assert len(w["fit"]) == 6 and len(w["test"]) == 3
        assert w["fit"] == sorted(w["fit"]) and w["test"] == sorted(w["test"])
        assert max(w["fit"]) < min(w["test"]), "fit days precede test days"
        assert not (set(w["test"]) & set(w["fit"]))
        assert not (set(w["test"]) & seen_test), "test slices must be disjoint"
        seen_test |= set(w["test"])
        if prev_last is not None:
            assert min(w["test"]) > prev_last, "windows ordered in time"
        prev_last = max(w["test"])
        assert set(w["fit"]) | set(w["test"]) <= set(days)
    assert stats.walk_forward(days[:5], k=5, fit=6, test=3) == []


def test_bonferroni():
    assert stats.bonferroni_alpha(10) == pytest.approx(0.005)
    assert stats.bonferroni_alpha(1) == pytest.approx(0.05)
    assert stats.bonferroni_alpha(20, base=0.1) == pytest.approx(0.005)
    assert stats.bonferroni_alpha(0) == pytest.approx(0.05)
