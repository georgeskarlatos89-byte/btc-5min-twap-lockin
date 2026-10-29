"""qdesk test harness (agent F). Written against INTERFACES.md; modules that have not landed yet are
skipped via importorskip (the integrator re-runs `pytest tests -q` after every agent lands).

Shared helpers:
  bme_rows()                 fixture rows (tolerates a truncated gzip while the fixture is being copied)
  tokens()                   tests/fixtures/tokens_btc5m_range.json
  drive_bme(...)             feed BME rows through FeatureState per INTERFACES §1 -> feature rows (list of dict)
  write_feature_day(...)     rows -> data/features/day_YYYYMMDD.csv.gz with the exact FEATURE_COLUMNS header
  qd_data (fixture)          a fresh QD_DATA tree per test with LAB_NO_TELEGRAM=1
"""
import csv
import gzip
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                       # .../qdesk
REPO_ROOT = os.path.dirname(PKG)                  # parent of the package: C:\g\tlfix locally, ~ on the VM
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
FIXTURES = os.path.join(HERE, "fixtures")
BME_FIXTURE = os.path.join(FIXTURES, "bme_sample_20260926_first_hour.csv.gz")
TOKENS_FIXTURE = os.path.join(FIXTURES, "tokens_btc5m_range.json")
HYPS3 = os.path.join(FIXTURES, "hyps_3.json")
FIXTURE_DAY = "20260926"
DATA_SUBDIRS = ("tokens", "labels", "features", "prices1m", "ext/moondev", "ext/hl", "ext/wallets",
                "evidence", "hyps", "bot", "reports", "state")

_BME_CACHE = {}


MAX_SPAN_S = 960      # first 3 five-minute rounds (+ margin): the full fixture is 2.35 M rows, too big to hold in memory


def bme_rows(limit_rows=None, max_span_s=MAX_SPAN_S):
    """BME fixture rows as lists of 11 strings (header skipped), only the first `max_span_s` seconds.
    A truncated gzip (EOFError while the fixture is still being copied) yields the rows read so far."""
    key = (limit_rows or 0, max_span_s)
    if key in _BME_CACHE:
        return _BME_CACHE[key]
    if not os.path.exists(BME_FIXTURE):
        pytest.skip(f"fixture missing: {BME_FIXTURE}")
    rows = []
    try:
        with gzip.open(BME_FIXTURE, "rt", newline="") as f:
            r = csv.reader(f)
            hdr = next(r)
            assert hdr[:9] == ["ts_ms", "event_type", "market", "asset", "side", "price", "size", "best_bid", "best_ask"], hdr
            t0 = None
            for row in r:
                if len(row) < 9:
                    continue
                ts = int(row[0])
                if t0 is None:
                    t0 = ts
                if max_span_s and ts - t0 > max_span_s * 1000:
                    break
                rows.append(row)
                if limit_rows and len(rows) >= limit_rows:
                    break
    except EOFError:
        pass                                      # truncated member: keep what we have
    if not rows:
        pytest.skip("fixture has no rows")
    _BME_CACHE[key] = rows
    return rows


def tokens():
    if not os.path.exists(TOKENS_FIXTURE):
        pytest.skip(f"fixture missing: {TOKENS_FIXTURE}")
    with open(TOKENS_FIXTURE) as f:
        return json.load(f)


def load_hyps3():
    with open(HYPS3) as f:
        return json.load(f)


def _px(v):
    """best bid/ask columns 7/8: empty string = no update (None); an explicit value is passed through as a float
    so FeatureState applies the empty-side rule (bid <= 0.005 or ask >= 0.995 -> that side NA; INTERFACES §1)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rounds_in(rows, tok, T=300):
    """Sorted round starts present in the rows (assets known to the tokens map)."""
    seen = set()
    for r in rows:
        a = r[3]
        if a and a in tok:
            seen.add(int(tok[a]["start"]))
    return sorted(seen)


def round_assets(tok, start):
    up = down = None
    for a, v in tok.items():
        if int(v.get("start", -1)) == start:
            if v.get("outcome") == "Up":
                up = a
            elif v.get("outcome") == "Down":
                down = a
    return up, down


def drive_bme(rows, tok, FeatureState, market="5m", T=300, max_rounds=None, spot_lag=1.44, starts=None):
    """Feed BME rows into one FeatureState per round exactly as INTERFACES §1 prescribes and return
    every emitted feature row (dict) with 0 <= t_el < T. This is the reference path the fixture and
    parity tests compare against; bme_to_features.py must produce the same rows."""
    starts = list(starts) if starts else rounds_in(rows, tok, T)
    if max_rounds:
        starts = starts[:max_rounds]
    states = {}
    for st in starts:
        up, down = round_assets(tok, st)
        if up and down:
            states[st] = FeatureState(market, st, up, down, spot_lag)
    if not states:
        pytest.skip("no complete round (Up+Down assets) in the fixture/tokens overlap")
    t_max = max(states) + T + 5
    out = []
    last_flush = None
    t = None
    for r in rows:
        t = int(r[0]) / 1000.0
        if t > t_max:
            break
        et = r[1]
        if et == "spot_tick":
            v = float(r[5])
            for fs in states.values():
                fs.on_spot(t, v)
        elif et == "twap_tick":
            v = float(r[5])
            for fs in states.values():
                fs.on_twap(t, v)
        elif et in ("price_change", "bba", "trade"):
            a = r[3]
            st = tok.get(a, {}).get("start")
            fs = states.get(st) if st is not None else None
            if fs is None:
                continue
            if et == "price_change":
                fs.on_price_change(a, r[4], float(r[5]), float(r[6] or 0), _px(r[7]), _px(r[8]), t)
            elif et == "bba":
                fs.on_bba(a, _px(r[7]), _px(r[8]), t)
            else:
                fs.on_trade(a, r[4], float(r[5]), float(r[6] or 0), t)
        if last_flush is None or t - last_flush >= 1.0:
            for fs in states.values():
                out.extend(fs.flush(t))
            last_flush = t
    if t is not None:
        for fs in states.values():
            out.extend(fs.flush(t + T + 5))
    return [row for row in out if 0 <= int(row["t_el"]) < T]


def fmt(v):
    """CSV cell per INTERFACES §2: NA -> '', bool -> 0/1, floats with up to 6 decimals."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return ""
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(v)


def write_feature_day(path, rows, columns):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        w.writerow([fmt(row.get(c)) for c in columns])
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)
    return path


def read_feature_day(path):
    with gzip.open(path, "rt", newline="") as f:
        r = csv.reader(f)
        hdr = next(r)
        return hdr, [dict(zip(hdr, row)) for row in r]


def make_data_tree(root):
    for d in DATA_SUBDIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


@pytest.fixture
def qd_data(tmp_path, monkeypatch):
    """Fresh QD_DATA tree; Telegram suppressed; DRY_RUN=true."""
    root = make_data_tree(str(tmp_path / "data"))
    monkeypatch.setenv("QD_DATA", root)
    monkeypatch.setenv("LAB_NO_TELEGRAM", "1")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("QD_ROOT", REPO_ROOT)
    return root


def subprocess_env(data_root):
    env = dict(os.environ)
    env.update({"QD_DATA": data_root, "LAB_NO_TELEGRAM": "1", "DRY_RUN": "true", "QD_ROOT": REPO_ROOT,
                "PYTHONPATH": REPO_ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    return env


@pytest.fixture(scope="session")
def feature_rows_3r():
    """Feature rows for the first 3 complete rounds of the fixture (session-cached; ~1 M events)."""
    features = pytest.importorskip("qdesk.features.features")
    rows = bme_rows()
    tok = tokens()
    return drive_bme(rows, tok, features.FeatureState, max_rounds=3)
