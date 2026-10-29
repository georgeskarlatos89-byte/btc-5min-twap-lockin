"""Parity: the first round of the fixture through FeatureState directly AND through the live
recorder's message-handler path, fed synthetic ws-shaped messages built from the same BME rows
(CLOB market ws: price_change / last_trade_price; RTDS: crypto_prices_chainlink / twap_sixty).
The two paths must emit identical rows (look-ahead helpers included). If the recorder exposes no
handler the test is xfailed with the reason (INTERFACES §2 names the class, not the recorder's
handler entry point; the recorder's Recorder.on_market_msg/on_rtds_msg/flush_all are used)."""
import importlib
import inspect
import json

import pytest

from qdesk.tests.conftest import bme_rows, tokens, drive_bme, rounds_in, round_assets

features = pytest.importorskip("qdesk.features.features")
FC = features.FEATURE_COLUMNS
T = 300

def synthetic_messages(rows, start, up, down):
    """(t_recv_seconds, kind, msg) built from BME rows of one round, in the original order."""
    assets = {up, down}
    out = []
    for r in rows:
        t = int(r[0]) / 1000.0
        et = r[1]
        if et == "spot_tick":
            out.append((t + 1.44, "rtds", {"topic": "crypto_prices_chainlink", "type": "update",
                                           "payload": {"symbol": "btc/usd", "value": float(r[5]), "timestamp": int(r[0])}}))
        elif et == "twap_tick":
            out.append((t, "rtds", {"topic": "crypto_prices_twap_sixty", "type": "update",
                                    "payload": {"symbol": "btc/usd", "value": float(r[5]), "timestamp": int(r[0])}}))
        elif r[3] in assets:
            if et == "price_change":
                ch = {"asset_id": r[3], "side": r[4], "price": r[5], "size": r[6] or "0", "hash": r[9] or ""}
                if r[7]:
                    ch["best_bid"] = r[7]
                if r[8]:
                    ch["best_ask"] = r[8]
                out.append((t, "market", {"event_type": "price_change", "market": r[2], "price_changes": [ch], "timestamp": r[0]}))
            elif et == "trade":
                out.append((t, "market", {"event_type": "last_trade_price", "asset_id": r[3], "market": r[2], "side": r[4],
                                          "price": r[5], "size": r[6], "timestamp": r[0], "fee_rate_bps": "0"}))
            elif et == "bba":
                out.append((t, "market", {"event_type": "price_change", "market": r[2], "timestamp": r[0],
                                          "price_changes": [{"asset_id": r[3], "side": "", "price": "", "size": "", "best_bid": r[7], "best_ask": r[8]}]}))
    return out


def test_recorder_path_matches_feature_state():
    rows = bme_rows()
    tok = tokens()
    starts = rounds_in(rows, tok)
    st = starts[0]
    up, down = round_assets(tok, st)
    assert up and down
    sub = [r for r in rows if int(r[0]) / 1000.0 <= st + T + 5]
    ref = drive_bme(sub, tok, features.FeatureState, starts=[st])
    assert ref, "reference path emitted nothing"

    rec_mod = pytest.importorskip("qdesk.recorder.feat_recorder")
    Recorder = getattr(rec_mod, "Recorder", None)
    if Recorder is None or not all(hasattr(Recorder, n) for n in ("on_market_msg", "on_rtds_msg", "add_round", "flush_all")):
        pytest.xfail("recorder/feat_recorder.py exposes no Recorder.on_market_msg/on_rtds_msg/add_round/flush_all; "
                     "INTERFACES §2 only names FeatureState")

    class Capture:                                   # stands in for the recorder's DayWriter
        def __init__(self): self.rows = []
        def write(self, row): self.rows.append(dict(row))
        def flush(self): pass
        def rotate(self, *a): pass
        def close(self): pass

    cap = Capture()
    rec = Recorder(cap, dry=True)
    rec.add_round("5m", st, up, down)               # round known before the first event (same as the reference path)
    last_flush = None
    for t, kind, msg in synthetic_messages(sub, st, up, down):
        (rec.on_market_msg if kind == "market" else rec.on_rtds_msg)(msg, t)
        tf = t if kind == "market" else t - 1.44      # flush on event time, never on a spot RECEIVE time
        if last_flush is None or tf - last_flush >= 1.0:
            rec.flush_all(tf); last_flush = tf
    rec.flush_all(st + T + 60)
    for k in list(rec.rounds):
        rec.drop_round(k, st + T + 60)
    got = [r for r in cap.rows if int(r.get("start", -1)) == st and 0 <= int(r.get("t_el", -1)) < T]
    assert got, "recorder emitted no rows for the round"

    key = lambda r: (int(r["start"]), int(r["t_el"]), r["side"])
    R, G = {key(r): r for r in ref}, {key(r): r for r in got}
    assert set(G) == set(R), f"row keys differ: only_ref={len(set(R) - set(G))} only_rec={len(set(G) - set(R))}"
    diffs = []
    for k in sorted(R):
        for c in FC:
            if c == "src":
                continue                     # 'bme' vs 'live' is expected
            a, b = R[k].get(c), G[k].get(c)
            na = a in (None, ""); nb = b in (None, "")
            if na != nb or (not na and abs(float(a) - float(b)) > 1e-6 if _isnum(a) and _isnum(b) else (not na and str(a) != str(b))):
                diffs.append((k, c, a, b))
                if len(diffs) >= 15:
                    break
        if len(diffs) >= 15:
            break
    assert not diffs, diffs


def _isnum(v):
    try:
        float(v); return True
    except (TypeError, ValueError):
        return False
