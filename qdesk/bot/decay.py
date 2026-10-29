#!/usr/bin/env python3
"""decay.py - DECAYING / RETIRED flags for the active paper rule (plan: bot/decay.py; GrokBot idea "DECAYING flag").

check(data_dir) -> dict. Reads data/bot/paper_trades.csv (settled rows of the active rule only: fill_status full|partial
and a non-empty R), compares avg_R with backtest_avg_R from strategy_state.json:
  DECAYING : >= 20 settled paper trades and avg_R < 0.5 * backtest_avg_R
  RETIRED  : >= 40 settled paper trades and still below  -> active_rule.json is renamed away (rule removed)
  recovery : DECAYING with avg_R back >= 0.5 * backtest -> PAPER again
Every transition writes strategy_state.json, one strategy_log.csv row and ONE Telegram alert via
qdesk.common.tg.alert(key="decay-<rule_id>"). Called by qbot after every settlement; safe to call any time.
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from qdesk.bot import _util

DECAY_N = 20
RETIRE_N = 40
DECAY_FRAC = 0.5


def settled_trades(ddir, rule_id=None):
    rows = _util.read_csv(os.path.join(ddir, "bot", "paper_trades.csv"))
    out = []
    for r in rows:
        if rule_id and r.get("rule_id") != rule_id: continue
        if r.get("fill_status") not in ("full", "partial"): continue
        try:
            out.append(float(r.get("R")))
        except (TypeError, ValueError):
            continue
    return out


def _alert(key, text, log_reason):
    try:
        tg = _util.common("tg")
        return bool(tg.alert(key, text, cls="event", source="qdesk-bot"))
    except Exception as e:                       # tg missing or Telegram down: the state files are the record
        log_reason.append(f"alert not sent ({type(e).__name__})")
        return False


def check(ddir, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    sp = os.path.join(ddir, "bot", "strategy_state.json")
    st = _util.load_json(sp, {}) or {}
    rule = _util.load_json(os.path.join(ddir, "bot", "active_rule.json"))
    rule_id = st.get("rule_id") or (rule or {}).get("id")
    status = st.get("status") or ("PAPER" if rule else "OBSERVE")
    res = dict(rule_id=rule_id, status=status, prev=status, trades=0, avg_R=None, backtest_avg_R=st.get("backtest_avg_R"),
               changed=False, alerted=False)
    if not rule_id:
        return res
    Rs = settled_trades(ddir, rule_id)
    n = len(Rs); avg = (sum(Rs) / n) if n else None
    bt = st.get("backtest_avg_R")
    if bt is None:
        g = _util.load_json(os.path.join(ddir, "bot", "gate_pass.json"), {}) or {}
        bt = g.get("backtest_avg_R")
    try:
        bt = float(bt) if bt is not None else None
    except (TypeError, ValueError):
        bt = None
    below = bt is not None and avg is not None and avg < DECAY_FRAC * bt
    new = status
    if status in ("PAPER", "DECAYING"):
        if n >= RETIRE_N and below: new = "RETIRED"
        elif n >= DECAY_N and below: new = "DECAYING"
        elif status == "DECAYING" and not below: new = "PAPER"
    st.update(rule_id=rule_id, status=new, trades=n, avg_R=(round(avg, 5) if avg is not None else None), backtest_avg_R=bt)
    st.setdefault("since", _util.iso(now_ts))
    notes = []
    if new != status:
        st["since"] = _util.iso(now_ts)
        reason = f"{n} settled paper trades avg_R={avg:.4f} vs backtest {bt:.4f} (threshold {DECAY_FRAC * bt:.4f})" if (avg is not None and bt is not None) else f"{n} trades"
        _util.strategy_log(ddir, rule_id, new, reason)
        if new == "RETIRED":
            ap = os.path.join(ddir, "bot", "active_rule.json")
            if os.path.exists(ap):
                os.replace(ap, os.path.join(ddir, "bot", f"active_rule.retired-{_util.day_str(now_ts)}.json"))
                notes.append("active_rule.json removed")
        res["alerted"] = _alert(f"decay-{rule_id}", f"qdesk paper bot: rule {rule_id} -> {new}. {reason}. {' '.join(notes)}".strip(), notes)
        res["reason"] = reason
    _util.save_json(sp, st)
    res.update(status=new, trades=n, avg_R=avg, backtest_avg_R=bt, changed=(new != status), notes=notes)
    return res


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="run the decay check once and print the result")
    ap.add_argument("--data", default=None); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--date", default=None)
    a = ap.parse_args()
    if a.dry_run: os.environ["LAB_NO_TELEGRAM"] = "1"
    print(json.dumps(check(_util.data_dir(a.data)), indent=1)); sys.exit(0)
