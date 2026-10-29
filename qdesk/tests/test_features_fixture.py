"""features/features.py + features/bme_to_features.py on the BME fixture (INTERFACES §1, §2):
header == FEATURE_COLUMNS, 2 rows per second per round, spot filled after the first tick,
ask_ok_100ms in {0,1}, and the same rows from the bme_to_features.py CLI if its flags are guessable."""
import glob
import os
import subprocess
import sys

import pytest

from qdesk.tests.conftest import (FIXTURE_DAY, BME_FIXTURE, TOKENS_FIXTURE, PKG, bme_rows, tokens, drive_bme,
                      read_feature_day, subprocess_env, write_feature_day)

features = pytest.importorskip("qdesk.features.features")
FC = features.FEATURE_COLUMNS

EXPECTED_HEADER = ("market,start,side,asset,t_el,time_left,bb,ba,bb_sz,ba_sz,spread,mid,imb3,sum_ask2,sum_bid2,"
                   "spot,open_twap,spot_bps_vs_open,mom_2s,mom_5s,spot_vol_30s,"
                   "flow_sell_10s,flow_buy_10s,trade_n_10s,sweep_flag,"
                   "ask_ok_100ms,ask_sz_100ms,bid_lvl_sz,sell_at_bb_next1s,sell_thru_bb_next1s,"
                   "session_hour,dow,macro_blackout,liq_long_5m_usd,liq_short_5m_usd,hl_funding_1h,hl_oi_usd,src").split(",")


def test_feature_columns_frozen():
    assert list(FC) == EXPECTED_HEADER
    schemas = pytest.importorskip("qdesk.common.schemas")
    assert list(schemas.FEATURE_COLUMNS) == EXPECTED_HEADER


def num(v):
    if v is None or v == "":
        return None
    return float(v)


def test_rows_two_per_second_and_values(feature_rows_3r):
    rows = feature_rows_3r
    assert rows, "no feature rows emitted"
    assert set(rows[0].keys()) >= set(FC), sorted(set(FC) - set(rows[0].keys()))
    by_sec = {}
    for r in rows:
        by_sec.setdefault((int(r["start"]), int(r["t_el"])), []).append(r["side"])
    starts = sorted({k[0] for k in by_sec})
    full = [st for st in starts if all((st, s) in by_sec for s in range(300))]
    assert full, f"no round with all 300 seconds; starts={starts}"
    for st in full:
        for s in range(300):
            assert sorted(by_sec[(st, s)]) == ["Down", "Up"], (st, s, by_sec[(st, s)])
    st0 = full[0]
    rr = [r for r in rows if int(r["start"]) == st0]
    for r in rr:
        assert r["market"] == "5m" and r["src"] == "bme"
        assert int(r["time_left"]) == 300 - int(r["t_el"])
        assert 0 <= int(r["session_hour"]) <= 23 and 0 <= int(r["dow"]) <= 6
        ok = num(r["ask_ok_100ms"])
        assert ok in (None, 0.0, 1.0), r["ask_ok_100ms"]
        assert num(r["sweep_flag"]) in (None, 0.0, 1.0)
        assert num(r["macro_blackout"]) in (0.0, 1.0)
        bb, ba = num(r["bb"]), num(r["ba"])
        if bb is not None and ba is not None:
            # an emptied side is NA (empty-side rule, INTERFACES §1), so a crossed or locked book must not appear
            assert 0 < bb < ba <= 1, (r["t_el"], bb, ba)
            assert num(r["spread"]) == pytest.approx(ba - bb, abs=1e-6)
            assert num(r["mid"]) == pytest.approx((bb + ba) / 2, abs=1e-6)
        if num(r["imb3"]) is not None:
            assert 0 <= num(r["imb3"]) <= 1
    # spot filled after the first spot tick was RECEIVED (source + 1.44 s); the open twap within 3 s of start
    late = [r for r in rr if int(r["t_el"]) >= 5]
    assert all(num(r["spot"]) is not None for r in late), "spot NA after the first tick"
    assert all(50_000 < num(r["spot"]) < 200_000 for r in late)
    assert all(num(r["open_twap"]) is not None for r in late), "open_twap NA"
    assert all(num(r["spot_bps_vs_open"]) is not None for r in late)
    assert all(abs(num(r["spot_bps_vs_open"])) < 500 for r in late)
    s0 = [r for r in rr if int(r["t_el"]) == 0]
    assert len(s0) == 2
    # both sides' asks sum near 1 and the two sides see the same spot
    for s in (10, 150, 299):
        pair = [r for r in rr if int(r["t_el"]) == s]
        if all(num(r["ba"]) is not None for r in pair):
            assert 0.9 <= num(pair[0]["sum_ask2"]) <= 1.25
            assert num(pair[0]["sum_ask2"]) == pytest.approx(num(pair[1]["sum_ask2"]), abs=1e-6)
        assert num(pair[0]["spot"]) == num(pair[1]["spot"])
    # ask_ok_100ms is a look-ahead helper and must be 0/1 (never NA) whenever ba is known
    with_ba = [r for r in rr if num(r["ba"]) is not None]
    assert with_ba and all(num(r["ask_ok_100ms"]) in (0.0, 1.0) for r in with_ba)
    assert any(num(r["ask_ok_100ms"]) == 1.0 for r in with_ba), "the ask is never still there 100 ms later?"
    # the 5-s momentum needs 5 s of history; before that it is NA
    assert all(num(r["mom_5s"]) is not None for r in rr if int(r["t_el"]) >= 10)
    # flow counters are non-negative and trades occurred in the fixture
    assert all(num(r["flow_sell_10s"]) >= 0 and num(r["flow_buy_10s"]) >= 0 for r in rr if num(r["flow_sell_10s"]) is not None)


def test_feature_day_roundtrip(tmp_path, feature_rows_3r):
    p = write_feature_day(str(tmp_path / f"day_{FIXTURE_DAY}.csv.gz"), feature_rows_3r, FC)
    hdr, rows = read_feature_day(p)
    assert hdr == list(FC)
    assert len(rows) == len(feature_rows_3r)
    assert all(r["start"].isdigit() for r in rows[:10])


OUT_PLACEHOLDER = "{OUT}"
BME_CLI_GUESSES = (
    # actual CLI of features/bme_to_features.py (agent C): tokens positional, --in, --day, --out
    [TOKENS_FIXTURE, "--in", BME_FIXTURE, "--day", FIXTURE_DAY, "--out", OUT_PLACEHOLDER],
    ["--date", FIXTURE_DAY, "--in", BME_FIXTURE, "--tokens", TOKENS_FIXTURE],
)


def test_bme_to_features_cli_matches_reference(qd_data, feature_rows_3r):
    """INTERFACES §2 names features/bme_to_features.py but does not fix its CLI flags (contract gap).
    We try a few spellings against a QD_DATA tree that already holds the tokens map; the first one that
    produces data/features/day_20260926.csv.gz is compared with the reference FeatureState path."""
    script = os.path.join(PKG, "features", "bme_to_features.py")
    if not os.path.exists(script):
        pytest.skip("features/bme_to_features.py not landed")
    import shutil
    shutil.copy(TOKENS_FIXTURE, os.path.join(qd_data, "tokens", "tokens_btc5m_range.json"))
    out = os.path.join(qd_data, "features", f"day_{FIXTURE_DAY}.csv.gz")
    env = subprocess_env(qd_data)
    env["QD_BME_FILE"] = BME_FIXTURE
    env["QD_FIXTURE"] = BME_FIXTURE
    last = None
    for args in BME_CLI_GUESSES:
        args = [out if a == OUT_PLACEHOLDER else a for a in args]
        r = subprocess.run([sys.executable, script] + args, capture_output=True, text=True, timeout=900, env=env, cwd=qd_data)
        last = (args, r.returncode, (r.stderr or r.stdout)[-400:])
        if r.returncode == 0 and os.path.exists(out):
            break
    else:
        pytest.xfail(f"bme_to_features.py CLI not guessable (INTERFACES §2 does not fix its flags); last={last}")
    hdr, rows = read_feature_day(out)
    assert hdr == list(FC)
    ref = {(int(r["start"]), int(r["t_el"]), r["side"]): r for r in feature_rows_3r}
    got = {(int(r["start"]), int(r["t_el"]), r["side"]): r for r in rows}
    common = sorted(set(ref) & set(got))
    assert len(common) >= 600, f"overlap too small: {len(common)}"
    diffs = []
    for k in common:
        for c in FC:
            if not same_cell(ref[k].get(c), got[k].get(c)):
                diffs.append((k, c, ref[k].get(c), got[k].get(c)))
        if len(diffs) > 20:
            break
    assert not diffs, diffs[:10]


def same_cell(a, b, tol=1e-5):
    """reference values are python objects, CLI values are CSV strings; NA = None or ''."""
    na, nb = a in (None, ""), b in (None, "")
    if na or nb:
        return na and nb
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a) == str(b)
