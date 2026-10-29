#!/usr/bin/env python3
"""gate.py - the paper bot's gate lock (INTERFACES section 4 gate_pass.json, section 9).

check(rule, data_dir) -> (ok, reasons). The bot loads active_rule.json only when every condition holds:
  gate_pass.json exists and is readable; gate_pass == true; paper_only == true; generated < 7 days ago;
  rule_id == rule["id"]; rule_hash == dsl.canonical_hash(rule); code_hash == hashing.code_hash(CODE_FILES_BOT);
  corpus_hash present. Logic copied from C:/g/tlfix/open_book_maker_v2/maker_bot.py gate_lock_check (R8 lock:
  the lock lives in code, not in discipline).
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from qdesk.bot import _util

GATE_MAX_AGE_S = 7 * 86400


def gate_path(ddir):
    return os.path.join(ddir, "bot", "gate_pass.json")


def check(rule, ddir, now_ts=None):
    """-> (ok: bool, reasons: list[str]); reasons is empty iff ok."""
    now_ts = time.time() if now_ts is None else now_ts
    p = gate_path(ddir)
    if not os.path.exists(p):
        return False, [f"gate_pass.json missing ({p})"]
    g = _util.load_json(p)
    if not isinstance(g, dict):
        return False, ["gate_pass.json unreadable or not an object"]
    reasons = []
    if g.get("gate_pass") is not True:
        reasons.append("gate_pass is not true")
    if g.get("paper_only") is not True:
        reasons.append("paper_only is not true (v1 is paper only)")
    gen = _util.parse_iso(g.get("generated"))
    if gen is None:
        gen = os.path.getmtime(p)
        reasons.append("generated timestamp unparsable (fell back to file mtime)")
    age = now_ts - gen
    if age > GATE_MAX_AGE_S:
        reasons.append(f"gate_pass.json is {age / 86400:.1f} days old (max 7)")
    if age < -3600:
        reasons.append("generated timestamp is in the future")
    if not isinstance(rule, dict):
        reasons.append("no active rule object")
    else:
        if g.get("rule_id") != rule.get("id"):
            reasons.append(f"rule_id mismatch: gate {g.get('rule_id')} vs rule {rule.get('id')}")
        try:
            dsl = _util.common("dsl")
            h = dsl.canonical_hash(rule)
            if g.get("rule_hash") != h:
                reasons.append(f"rule_hash mismatch: gate {g.get('rule_hash')} vs rule {h}")
        except ImportError as e:
            reasons.append(f"cannot verify rule_hash: {e}")
    try:
        hashing = _util.common("hashing")
        now_h = hashing.code_hash(hashing.CODE_FILES_BOT)
        if g.get("code_hash") != now_h:
            reasons.append(f"code hash mismatch: gate {g.get('code_hash')} vs running {now_h}")
    except ImportError as e:
        reasons.append(f"cannot verify code_hash: {e}")
    if not g.get("corpus_hash"):
        reasons.append("gate_pass.json has no corpus_hash")
    return (not reasons), reasons


def describe(ok, reasons, g=None):
    return "GATE PASS" if ok else "GATE REFUSED: " + "; ".join(reasons)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="print the gate verdict for data/bot/active_rule.json")
    ap.add_argument("--data", default=None); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--date", default=None)
    a = ap.parse_args()
    ddir = _util.data_dir(a.data)
    rule = _util.load_json(os.path.join(ddir, "bot", "active_rule.json"))
    ok, reasons = check(rule, ddir)
    print(describe(ok, reasons)); sys.exit(0)
