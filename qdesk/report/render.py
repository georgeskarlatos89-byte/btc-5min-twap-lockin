#!/usr/bin/env python3
"""qdesk/report/render.py — shared helpers for brief.py / weekly.py (and llm/skeptic.py).

Pure stdlib. Nothing here talks to the network; the Telegram/heartbeat wrappers import
qdesk.common.tg inside the function and fall back to local files when common/ is not there yet.
"""
import csv
import glob
import gzip
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
TG_MAX = 4000


# ----------------------------------------------------------------------------- time / paths
def utc_now():
    return datetime.now(timezone.utc)


def iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.strptime(str(s)[:20].rstrip("Z"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.fromtimestamp(float(s), timezone.utc)
        except Exception:
            return None


def hhmm(s):
    d = parse_iso(s)
    return d.strftime("%H:%MZ") if d else "--:--"


def date_dash(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def day_minus(yyyymmdd, n):
    return (datetime.strptime(yyyymmdd, "%Y%m%d") - timedelta(days=n)).strftime("%Y%m%d")


def data_dir():
    return os.path.realpath(os.environ.get("QD_DATA") or os.path.expanduser("~/qdesk/data"))


class Inputs:
    """Resolves data files: the real data dir first, then (dry-run only) report/samples/."""

    def __init__(self, dry_run=False):
        # samples are a render test only: used ONLY when QD_USE_SAMPLES=1 (never mixed into a real night by default)
        self.dry_run = dry_run and os.environ.get("QD_USE_SAMPLES") == "1"
        self.data = data_dir()
        self.used_samples = []

    def path(self, rel, must_exist=True):
        p = os.path.join(self.data, rel)
        if os.path.exists(p):
            return p
        if self.dry_run:
            q = os.path.join(SAMPLES, rel)
            if os.path.exists(q):
                self.used_samples.append(rel)
                return q
        return None if must_exist else p

    def glob(self, rel_pattern):
        out = glob.glob(os.path.join(self.data, rel_pattern))
        if not out and self.dry_run:
            out = glob.glob(os.path.join(SAMPLES, rel_pattern))
            if out:
                self.used_samples.append(rel_pattern)
        return sorted(out)

    def json(self, rel, default=None):
        p = self.path(rel)
        return load_json(p, default) if p else default

    def latest_json(self, rel_pattern, before=None, default=None):
        files = self.glob(rel_pattern)
        if before:
            files = [f for f in files if os.path.basename(f) < before]
        return (load_json(files[-1], default), files[-1]) if files else (default, None)


# ----------------------------------------------------------------------------- io
def load_json(path, default=None):
    try:
        from qdesk.common.schemas import load_json as _lj   # noqa
        return _lj(path)
    except Exception:
        pass
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    try:
        from qdesk.common.schemas import save_json as _sj   # noqa
        return _sj(path, obj)
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def save_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_csv_dicts(path, limit=None):
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            out.append(row)
            if limit and i + 1 >= limit:
                break
    return out


def read_jsonl_gz(path, limit=None):
    out = []
    if not path or not os.path.exists(path):
        return out
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            for i, ln in enumerate(f):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
                if limit and len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def count_lines_gz(path, cap=5_000_000):
    """Rows in a csv.gz without the header (streamed, capped)."""
    n = 0
    try:
        with gzip.open(path, "rb") as f:
            for _ in f:
                n += 1
                if n > cap:
                    break
    except Exception:
        return None
    return max(0, n - 1)


def fnum(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


# ----------------------------------------------------------------------------- formatting
def fmt(x, nd=3, na="n/a"):
    v = fnum(x)
    if v is None:
        return na
    if nd == 0:
        return f"{v:,.0f}"
    return f"{v:.{nd}f}"


def pct(x, nd=1, na="n/a", sign=True):
    v = fnum(x)
    if v is None:
        return na
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def ci(lo, hi, nd=1):
    if fnum(lo) is None or fnum(hi) is None:
        return "CI n/a"
    return f"CI[{pct(lo, nd)},{pct(hi, nd)}]"


def trunc(s, n):
    s = str(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def hard_cap(text, n=TG_MAX, marker="\n[cut]"):
    if len(text) <= n:
        return text
    return text[: n - len(marker)].rstrip() + marker


def table(rows, headers, aligns=None, sep=" "):
    """Monospace table: rows = list of lists (any values -> str). aligns 'l'/'r' per column."""
    rows = [[str(c) for c in r] for r in rows]
    headers = [str(h) for h in headers]
    ncol = len(headers)
    aligns = list(aligns or "l" * ncol)
    widths = [len(h) for h in headers]
    for r in rows:
        for i in range(min(ncol, len(r))):
            widths[i] = max(widths[i], len(r[i]))

    def line(r):
        cells = []
        for i in range(ncol):
            c = r[i] if i < len(r) else ""
            cells.append(c.rjust(widths[i]) if aligns[i] == "r" else c.ljust(widths[i]))
        return sep.join(cells).rstrip()
    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out)


def section(title, body):
    body = (body or "").rstrip()
    return f"{title}\n{body}\n" if body else f"{title}\n(none)\n"


_NUM = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:[.,]\d+)?%?")


def numbers_in(text):
    """The set of numeric tokens in a text (used to reject LLM wording with invented numbers)."""
    out = set()
    for m in _NUM.findall(str(text)):
        tok = m.replace(",", "").rstrip("%").lstrip("+")
        try:
            v = float(tok)
        except Exception:
            continue
        out.add(round(v, 6))
        out.add(round(abs(v), 6))
    return out


def numbers_in_obj(obj):
    """Every number that appears in a JSON object (values, and numbers inside strings), plus their
    percent / rounded forms so '50.3%' matches 0.503 and 'about 2 bps' matches 1.9 rounded."""
    out = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            v = float(x)
            out.update({round(v, 6), round(abs(v), 6), round(v * 100, 6), round(abs(v) * 100, 6), round(v, 1), round(v, 0), round(v * 100, 1), round(v * 100, 0)})
        elif isinstance(x, str):
            out.update(numbers_in(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(obj)
    return out


def guard_numbers(line, allowed):
    """True if every number in `line` is in `allowed` (dates/times like 2026-11-05 are tolerated as
    their components are in the files; the day-of-month alone may not be, so parts of ISO dates are
    stripped first)."""
    t = re.sub(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?", " ", str(line))
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    t = re.sub(r"\b(5m|15m|1h|5-?min(ute)?|15-?min(ute)?|60-?s|0\.1 ?s|100 ?ms|1\.44 ?s)\b", " ", t)
    for v in numbers_in(t):
        if v not in allowed:
            return False
    return True


# ----------------------------------------------------------------------------- ops wrappers
def heartbeat(stage, ok, note=""):
    try:
        from qdesk.common.tg import heartbeat as hb   # noqa
        return hb(stage, ok, note)
    except Exception:
        pass
    save_json(os.path.join(data_dir(), "state", f"hb_{stage}.json"),
              {"ts": iso(), "stage": stage, "ok": bool(ok), "note": note})


def send_report(key, text):
    """qdesk.common.tg.alert(key, text, cls='report'); fallback: append to data/state/alerts.log.
    Honours LAB_NO_TELEGRAM=1 (set by --dry-run). Returns True only if a Telegram message went out."""
    try:
        from qdesk.common.tg import alert   # noqa
        return bool(alert(key, text, cls="report", source="qdesk"))
    except Exception:
        pass
    try:
        p = os.path.join(data_dir(), "state", "alerts.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{iso()}] {'SUPPRESSED ' if os.environ.get('LAB_NO_TELEGRAM') else 'NO-TG-MODULE '}{key} >> "
                    + text.replace("\n", " | ")[:400] + "\n")
    except Exception:
        pass
    return False


def disk_line(path=None):
    path = path or data_dir()
    try:
        from qdesk.common.tg import disk_free_gb   # noqa
        free = float(disk_free_gb(path))
    except Exception:
        try:
            free = shutil.disk_usage(path if os.path.exists(path) else os.path.expanduser("~")).free / 1e9
        except Exception:
            free = None
    used = dir_size_gb(path)
    return free, used


def dir_size_gb(path, cap_files=200000):
    if not path or not os.path.isdir(path):
        return None
    tot, n = 0, 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                tot += os.path.getsize(os.path.join(root, fn))
            except Exception:
                pass
            n += 1
            if n > cap_files:
                return tot / 1e9
    return tot / 1e9


def llm_cost_summary(path, hours=24):
    """rows in the last `hours` from llm_cost.csv -> {calls, ok, seconds, by_stage}."""
    rows = read_csv_dicts(path)
    cutoff = utc_now() - timedelta(hours=hours)
    out = {"calls": 0, "ok": 0, "seconds": 0.0, "by_stage": {}}
    for r in rows:
        d = parse_iso(r.get("ts"))
        if not d or d < cutoff:
            continue
        out["calls"] += 1
        out["ok"] += 1 if str(r.get("ok")) == "1" else 0
        out["seconds"] += fnum(r.get("seconds"), 0.0)
        st = r.get("stage", "?")
        out["by_stage"][st] = out["by_stage"].get(st, 0) + 1
    return out


def heartbeats(inp, stages=("scout", "quant", "builder", "bot", "recorder")):
    out = {}
    for st in stages:
        hb = inp.json(f"state/hb_{st}.json")
        if hb:
            ts = hb.get("utc") or hb.get("ts")
            out[st] = {"ts": ts, "ok": hb.get("ok"), "note": hb.get("note", "")}
    return out


def bonferroni_alpha(n, base=0.05):
    try:
        from qdesk.common.stats import bonferroni_alpha as _b   # noqa
        return float(_b(int(n), base))
    except Exception:
        return base / max(1, int(n or 1))


def brier(p, won):
    """Brier score of probability p for a binary outcome (won True/False). Same arithmetic as
    common.settle.brier(p_up, winner); kept local so the report has no numeric dependency."""
    p = R_f(p)
    return None if p is None else (p - (1.0 if won else 0.0)) ** 2


def R_f(x):
    return fnum(x)
