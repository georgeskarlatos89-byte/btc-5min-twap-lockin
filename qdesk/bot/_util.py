#!/usr/bin/env python3
"""_util.py - tiny helpers shared by the qdesk bot modules (agent E owns bot/*).

Lazy imports of the sibling packages (qdesk.common.*, qdesk.features.features) with a clear error when a
module written by another agent is not there yet; tmp+os.replace writers; heartbeat file writer in the
INTERFACES section 4 format. No network, no secrets.
"""
import csv, importlib, json, os, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))          # .../qdesk/bot
PKG = os.path.dirname(HERE)                                  # .../qdesk
ROOT = os.path.dirname(PKG)                                  # repo root = parent of the package dir
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def common(name):
    """import qdesk.common.<name> (agent A) with a clear error if it is missing."""
    try:
        return importlib.import_module(f"qdesk.common.{name}")
    except ImportError as e:
        raise ImportError(f"qdesk.common.{name} is not importable (expected at {os.path.join(PKG, 'common', name + '.py')}, "
                          f"sys.path root {ROOT}): {e}") from e


def features_mod():
    """import qdesk.features.features (agent C) with a clear error if it is missing."""
    try:
        return importlib.import_module("qdesk.features.features")
    except ImportError as e:
        raise ImportError(f"qdesk.features.features is not importable (expected at {os.path.join(PKG, 'features', 'features.py')}): {e}") from e


def data_dir(override=None):
    d = override or os.environ.get("QD_DATA") or os.path.join(os.path.expanduser("~"), "qdesk", "data")
    return os.path.expanduser(d)


def iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_str(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).strftime("%Y%m%d")


def parse_iso(s):
    """'2026-10-29T05:30:00Z' -> epoch seconds (None when unparsable)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        s = str(s).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    """tmp + os.replace, indent=1 (same convention as common/schemas.save_json)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
    os.replace(tmp, path)


def append_csv(path, header, row):
    """append one row (list) to a CSV, writing the header when the file is new/empty."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(["" if v is None else v for v in row])


def read_csv(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def write_heartbeat(ddir, stage, ok=True, note=""):
    """data/state/hb_<stage>.json = {"ts","stage","ok","note"} (INTERFACES section 4)."""
    p = os.path.join(ddir, "state", f"hb_{stage}.json")
    try:
        save_json(p, {"ts": iso(), "stage": stage, "ok": bool(ok), "note": str(note)[:300]})
        return True
    except Exception:
        return False


def strategy_log(ddir, rule_id, event, reason=""):
    """data/bot/strategy_log.csv: ts,rule_id,event,reason"""
    append_csv(os.path.join(ddir, "bot", "strategy_log.csv"), ["ts", "rule_id", "event", "reason"],
               [iso(), rule_id or "", event, str(reason).replace("\n", " ")[:300]])
