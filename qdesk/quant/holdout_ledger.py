#!/usr/bin/env python3
"""holdout_ledger.py - who touched the frozen BME holdout (20261005..20261014) and when (INTERFACES.md section 4).

data/state/holdout_ledger.json = {"weeks": {"2026-W44": {"touched_by": ["H.."], "n": 1}}, "n_cum": 7}
Rule: at most ONE rule per ISO week may be evaluated on the frozen test slice (tier 2); the cumulative count
feeds the Bonferroni alpha of later nights. Writers use tmp + os.replace.
"""
import datetime as _dt
import json
import os
import sys
import tempfile

MAX_TOUCHES_PER_WEEK = 1


def ledger_path(data_dir):
    return os.path.join(data_dir, "state", "holdout_ledger.json")


def iso_week(date):
    """'YYYYMMDD' | datetime | None(now) -> '2026-W44'"""
    if date is None:
        d = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(date, str):
        d = _dt.datetime.strptime(date[:8], "%Y%m%d")
    else:
        d = date
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def load(data_dir):
    p = ledger_path(data_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("weeks", {})
    d.setdefault("n_cum", 0)
    return d


def _save(data_dir, d):
    p = ledger_path(data_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_ledger_", suffix=".json", dir=os.path.dirname(p))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
        f.write("\n")
    os.replace(tmp, p)
    return p


def may_touch(week, data_dir):
    """True when fewer than MAX_TOUCHES_PER_WEEK rules touched the holdout in this ISO week."""
    d = load(data_dir)
    wk = d["weeks"].get(week) or {}
    return int(wk.get("n", 0)) < MAX_TOUCHES_PER_WEEK


def record_touch(week, rule_id, data_dir):
    d = load(data_dir)
    wk = d["weeks"].setdefault(week, {"touched_by": [], "n": 0})
    wk["touched_by"].append(rule_id)
    wk["n"] = len(wk["touched_by"])
    d["n_cum"] = int(d.get("n_cum", 0)) + 1
    d["updated"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _save(data_dir, d)


def n_cum(data_dir):
    return int(load(data_dir).get("n_cum", 0))


if __name__ == "__main__":
    dd = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QD_DATA", os.path.expanduser("~/qdesk/data"))
    print(json.dumps(load(dd), indent=1))
    print("this week", iso_week(None), "may_touch", may_touch(iso_week(None), dd), "n_cum", n_cum(dd))
