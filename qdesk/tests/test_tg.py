"""common/tg.py: LAB_NO_TELEGRAM=1 -> alert writes data/state/alerts.log and sends nothing;
7 warn alerts -> 6 sent + 1 mute notice (then muted); the report class bypasses the cap; heartbeat shape."""
import json
import os

import pytest

tg = pytest.importorskip("qdesk.common.tg")


def _reset(qd_data):
    for fn in ("alerts.json", "alerts.log", "alerts.json.lock"):
        p = os.path.join(qd_data, "state", fn)
        if os.path.exists(p):
            os.remove(p)


def test_suppressed_alert_goes_to_log(qd_data):
    _reset(qd_data)
    assert os.environ.get("LAB_NO_TELEGRAM") == "1"
    assert tg.alert("unit_test_key", "hello from the test suite", cls="warn", source="tests") is False
    log = os.path.join(qd_data, "state", "alerts.log")
    assert os.path.exists(log), "alerts.log must be written even when Telegram is suppressed"
    txt = open(log, encoding="utf-8").read()
    assert "hello from the test suite" in txt and "suppressed" in txt.lower()
    assert "SENT[" not in txt
    # dedup: the same warn key within 6 h is not even attempted
    assert tg.alert("unit_test_key", "again", cls="warn", source="tests") is False
    assert "DEDUP" in open(log, encoding="utf-8").read()


def test_seven_warn_alerts_six_sent_then_mute(qd_data, monkeypatch):
    _reset(qd_data)
    sent = []
    monkeypatch.setattr(tg, "tg_send", lambda text: (sent.append(text), True)[1])
    results = [tg.alert(f"k{i}", f"warn number {i}", cls="warn", source="tests") for i in range(7)]
    assert results == [True] * 6 + [False], results
    assert len(sent) == 7, "6 alerts + the one mute notice"
    assert "mut" in sent[-1].lower() and "alerts.log" in sent[-1]
    # 8th: muted, nothing sent
    assert tg.alert("k7", "warn number 7", cls="warn", source="tests") is False
    assert len(sent) == 7
    log = open(os.path.join(qd_data, "state", "alerts.log"), encoding="utf-8").read()
    assert "MUTE ENGAGED" in log and "MUTED[" in log
    # report class bypasses the mute/cap
    assert tg.alert("brief", "morning brief", cls="report", source="tests") is True
    assert len(sent) == 8 and "morning brief" in sent[-1]
    # event class is still muted
    assert tg.alert("ev", "event", cls="event", source="tests") is False
    assert len(sent) == 8
    # once class: only ever once (after the mute window it would send once; here it is muted but still recorded)
    st = json.load(open(os.path.join(qd_data, "state", "alerts.json")))
    assert st["mute_until"] > 0 and st["muted_n"] >= 1


def test_heartbeat_shape(qd_data):
    p = tg.heartbeat("unit", True, "note text")
    hb = json.load(open(os.path.join(qd_data, "state", "hb_unit.json")))
    assert {"ts", "stage", "ok", "note"} <= set(hb)
    assert hb["stage"] == "unit" and hb["ok"] is True and hb["note"] == "note text"
    assert isinstance(hb["ts"], (int, float))
    rd = tg.read_heartbeat("unit") if hasattr(tg, "read_heartbeat") else hb
    assert rd["ok"] is True


def test_append_csv_and_disk_free(qd_data):
    p = os.path.join(qd_data, "state", "llm_cost.csv")
    hdr = ["ts", "stage", "model", "seconds", "input_chars", "output_chars", "ok"]
    tg.append_csv(p, hdr, [1, "quant", "cheap", 1.5, 10, 20, True])
    tg.append_csv(p, hdr, [2, "quant", "cheap", 2.5, 10, 20, False])
    lines = open(p, encoding="utf-8").read().strip().splitlines()
    assert lines[0] == ",".join(hdr) and len(lines) == 3
    assert tg.disk_free_gb(qd_data) > 0
