#!/usr/bin/env python3
"""qdesk/report/weekly.py — the Sunday 08:00 UTC review (Telegram <= 4,000 chars + full markdown copy).

    python qdesk/report/weekly.py --date 20261101 [--dry-run] [--no-llm] [--model cheap|strong] [--days 7]

Sections (all numbers from files):
  NIGHTS            verdict per scorecard of the week, rules tried, passes
  PAPER vs BACKTEST per rule from data/bot/paper_trades.csv (settled n, avg R, backtest avg R, ratio, DECAYING)
  BRIER vs MARKET   per rule: market Brier = (price paid − won)^2 on the bought side vs the rule's own
                    Brier (it bets 1.0) — labelled ESTIMATE because the price is the fill, not the mid
  HOLDOUT LEDGER    weeks touched, n_cum, Bonferroni-adjusted alpha
  KILLS             from data/bot/decisions-*.jsonl.gz (+ .settled sidecar): evaluations, fired, killed by reason,
                    false-kills = killed and would_pnl > 0
  RECORDER COVERAGE rows per day in data/features/day_*.csv.gz
  PRIOR EVIDENCE    proposed additions (deterministic candidate families + LLM wording), flagged "needs owner approval"
  DECISIONS NEEDED
The LLM (llm/prompts/weekly.md) only words the summary and the proposals; its numbers are checked against the
facts file. Output: data/reports/weekly_<date>.md ; Telegram via qdesk.common.tg.alert("weekly-<date>", ..., "report").
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from qdesk.report import render as R   # noqa: E402

PROMPT = os.path.join(REPO_ROOT, "qdesk", "llm", "prompts", "weekly.md")
PRIOR_EVIDENCE = os.path.join(REPO_ROOT, "qdesk", "prior_evidence.json")


# ----------------------------------------------------------------------------- facts
def week_days(date, days):
    return [R.day_minus(date, i) for i in range(days - 1, -1, -1)]


def nights(inp, days):
    out = []
    for d in days:
        sc = inp.json(f"hyps/scorecard_{d}.json")
        if not sc:
            continue
        rules = sc.get("rules", [])
        best = max(rules, key=lambda r: R.fnum(r.get("total"), 0), default=None)
        out.append({"date": d, "verdict": sc.get("verdict"), "n": len(rules), "passed": sc.get("passed") or [],
                    "best_id": best.get("id") if best else None, "best_total": best.get("total") if best else None,
                    "holdout_used": sc.get("holdout_used")})
    return out


def paper_vs_backtest(inp):
    trades = R.read_csv_dicts(inp.path("bot/paper_trades.csv"))
    st = inp.json("bot/strategy_state.json") or {}
    per = {}
    for t in trades:
        rid = t.get("rule_id") or "?"
        p = per.setdefault(rid, {"rule_id": rid, "n": 0, "settled": 0, "R": [], "pnl": 0.0, "bt": R.fnum(t.get("backtest_avg_R")),
                                "brier_mkt": [], "brier_rule": [], "fills": {"full": 0, "partial": 0, "none": 0}})
        p["n"] += 1
        p["fills"][t.get("fill_status") or "none"] = p["fills"].get(t.get("fill_status") or "none", 0) + 1
        w = t.get("winner")
        if w in (None, "", "None"):
            continue
        p["settled"] += 1
        r = R.fnum(t.get("R"))
        if r is not None:
            p["R"].append(r)
        p["pnl"] += R.fnum(t.get("pnl"), 0.0)
        price = R.fnum(t.get("price_filled")) or R.fnum(t.get("price_wanted"))
        won = (w == t.get("side"))
        if price is not None:
            p["brier_mkt"].append(R.brier(price, won))
            p["brier_rule"].append(R.brier(1.0, won))
    rows = []
    for rid, p in per.items():
        avg = sum(p["R"]) / len(p["R"]) if p["R"] else None
        bt = p["bt"] if p["bt"] is not None else (R.fnum(st.get("backtest_avg_R")) if st.get("rule_id") == rid else None)
        ratio = (avg / bt) if (avg is not None and bt) else None
        status = st.get("status") if st.get("rule_id") == rid else "-"
        decaying = status == "DECAYING" or (ratio is not None and len(p["R"]) >= 20 and ratio < 0.5)
        rows.append({"rule_id": rid, "n": p["n"], "settled": p["settled"], "avg_R": avg, "backtest_avg_R": bt, "ratio": ratio,
                     "pnl": p["pnl"], "status": status, "decaying": decaying, "fills": p["fills"],
                     "brier_mkt": (sum(p["brier_mkt"]) / len(p["brier_mkt"])) if p["brier_mkt"] else None,
                     "brier_rule": (sum(p["brier_rule"]) / len(p["brier_rule"])) if p["brier_rule"] else None,
                     "hit_rate": (1 - sum(p["brier_rule"]) / len(p["brier_rule"])) if p["brier_rule"] else None})
    return rows, st


def holdout(inp):
    led = inp.json("state/holdout_ledger.json") or {"weeks": {}, "n_cum": 0}
    n_cum = int(R.fnum(led.get("n_cum"), 0) or 0)
    weeks = led.get("weeks") or {}
    return {"n_cum": n_cum, "alpha": R.bonferroni_alpha(max(1, n_cum)), "weeks": {w: {"n": v.get("n"), "by": v.get("touched_by")} for w, v in weeks.items() if isinstance(v, dict)},
            "over": [w for w, v in weeks.items() if isinstance(v, dict) and (v.get("n") or 0) > 1]}


def kills(inp, days):
    tot = {"evaluations": 0, "fired": 0, "killed": 0, "by": {}, "false_kills": 0, "false_kill_pnl": 0.0, "settled_killed": 0, "days": 0}
    for d in days:
        p = inp.path(f"bot/decisions-{d}.jsonl.gz")
        if not p:
            continue
        tot["days"] += 1
        recs = R.read_jsonl_gz(p)
        tot["evaluations"] += len(recs)
        for r in recs:
            if r.get("fired"):
                tot["fired"] += 1
            if r.get("killed_by"):
                tot["killed"] += 1
                tot["by"][r["killed_by"]] = tot["by"].get(r["killed_by"], 0) + 1
        for r in R.read_jsonl_gz(inp.path(f"bot/decisions-{d}.settled.jsonl.gz") or ""):
            if r.get("killed_by") and r.get("would_pnl") is not None:
                tot["settled_killed"] += 1
                if R.fnum(r.get("would_pnl"), 0) > 0:
                    tot["false_kills"] += 1
                    tot["false_kill_pnl"] += R.fnum(r.get("would_pnl"), 0)
    return tot


def coverage(inp, days):
    out = []
    for d in days:
        p = inp.path(f"features/day_{d}.csv.gz")
        out.append({"date": d, "rows": R.count_lines_gz(p) if p else None})
    return out


def failed_families(inp, days):
    """Group the week's FAILED rules by (leg type, exit mode, sorted entry-field set); report n nights, n rules,
    trades and edges. Candidates for prior_evidence need >= 2 nights or >= 30 OOS trades in total."""
    fam = {}
    for d in days:
        sc = inp.json(f"hyps/scorecard_{d}.json")
        hy = inp.json(f"hyps/hyps_{d}.json")
        res = inp.json(f"hyps/results_{d}.json") or {}
        if not sc or not hy:
            continue
        rules = {h.get("id"): h for h in hy.get("hypotheses", []) if isinstance(h, dict)}
        for r in sc.get("rules", []):
            if r.get("pass"):
                continue
            h = rules.get(r.get("id"))
            if not h:
                continue
            leg = ((h.get("entry") or {}).get("leg") or {}).get("type", "?")
            ex = h.get("exit") or {}
            exm = ex.get("mode") if isinstance(ex, dict) else str(ex)
            fields = sorted({str(c.get("f", "")).replace("abs:", "").replace("other:", "") for c in (h.get("entry") or {}).get("when", []) if isinstance(c, dict)})
            key = f"{leg}|{exm}|{','.join(fields)}"
            f = fam.setdefault(key, {"leg": leg, "exit": exm, "fields": fields, "nights": set(), "rules": [], "n_trades": 0, "edges": []})
            f["nights"].add(d)
            f["rules"].append(r.get("id"))
            rr = (res.get("rules") or {}).get(r.get("id")) or {}
            t = rr.get("test") or rr.get("fit") or {}
            f["n_trades"] += int(R.fnum(t.get("n"), 0) or 0)
            if t.get("net_per_dollar") is not None:
                f["edges"].append(R.fnum(t.get("net_per_dollar")))
    out = []
    for key, f in fam.items():
        out.append({"signature": key, "leg": f["leg"], "exit": f["exit"], "fields": f["fields"], "nights": len(f["nights"]),
                    "rules": f["rules"], "n_trades": f["n_trades"],
                    "edge_mean": (sum(f["edges"]) / len(f["edges"])) if f["edges"] else None,
                    "candidate": len(f["nights"]) >= 2 or f["n_trades"] >= 30})
    out.sort(key=lambda x: (-x["nights"], -x["n_trades"]))
    return out


def gather(inp, date, ndays):
    days = week_days(date, ndays)
    rows, st = paper_vs_backtest(inp)
    prior = R.load_json(PRIOR_EVIDENCE, {}) or {}
    return {"date": date, "days": days, "nights": nights(inp, days), "paper": rows, "state": st, "holdout": holdout(inp),
            "kills": kills(inp, days), "coverage": coverage(inp, days), "failed_families": failed_families(inp, days),
            "prior_families": [f.get("family") for f in prior.get("families", [])],
            "llm": R.llm_cost_summary(inp.path("state/llm_cost.csv"), hours=24 * ndays), "disk": R.disk_line(inp.data)}


# ----------------------------------------------------------------------------- wording
def template_summary(facts):
    n_n = len(facts["nights"])
    passes = sum(len(n["passed"]) for n in facts["nights"])
    lines = [f"{n_n} night(s) with a scorecard in the last {len(facts['days'])} days; {passes} rule(s) passed."]
    for p in facts["paper"]:
        if p["avg_R"] is not None:
            lines.append(f"Paper bot {p['rule_id']}: {p['settled']} settled, avg R {R.fmt(p['avg_R'], 3)} vs backtest {R.fmt(p['backtest_avg_R'], 3)}"
                         + (" — DECAYING" if p["decaying"] else ""))
    k = facts["kills"]
    if k["evaluations"]:
        lines.append(f"Decisions log: {k['evaluations']} evaluations, {k['fired']} fired, {k['killed']} killed, {k['false_kills']} false-kills (would have won).")
    thin = [f for f in facts["failed_families"] if not f["candidate"]]
    if thin:
        lines.append(f"{len(thin)} failed family(ies) seen on one night only with < 30 trades: not evidence yet.")
    return lines[:6]


def template_proposals(facts):
    out = []
    for f in facts["failed_families"]:
        if not f["candidate"]:
            continue
        name = f"{f['leg']}_{f['exit']}_{'_'.join(f['fields'][:3]) or 'nofields'}"[:60]
        if name in facts["prior_families"]:
            continue
        out.append({"family": name, "signature_hint": f"leg {f['leg']} / exit {f['exit']} / fields {','.join(f['fields'])}",
                    "evidence": f"{len(f['rules'])} rule(s) on {f['nights']} night(s), {f['n_trades']} trades, mean edge {R.pct(f['edge_mean'], 2)}",
                    "result": "FAIL below the 18/25 gate", "rules": f["rules"]})
    return out


def llm_wording(facts, model, dry_run, no_llm):
    tl, tp = template_summary(facts), template_proposals(facts)
    if dry_run or no_llm:
        return tl, tp, "template wording (" + ("dry-run" if dry_run else "--no-llm") + ")"
    try:
        from qdesk.llm import client
    except Exception as e:
        return tl, tp, f"template wording (client import failed: {e.__class__.__name__})"
    if not client.find_cli():
        return tl, tp, "template wording (no_cli)"
    d = os.path.join(client.state_dir(), "llm_scratch", "_inputs_weekly")
    os.makedirs(d, exist_ok=True)
    small = {k: facts[k] for k in ("date", "days", "nights", "paper", "holdout", "kills", "coverage", "failed_families", "prior_families")}
    fp = os.path.join(d, "week_facts.json")
    R.save_json(fp, small)
    inputs = {"week_facts.json": fp}
    if os.path.isfile(PRIOR_EVIDENCE):
        inputs["prior_evidence.json"] = PRIOR_EVIDENCE
    obj, meta = client.ask_json(PROMPT, inputs, "weekly_words", model=model, max_repair=1, stage="weekly")
    if obj is None:
        return tl, tp, f"template wording (llm {meta.get('error')})"
    allowed = R.numbers_in_obj(small)
    lines = [R.trunc(x, 160) for x in (obj.get("summary_lines") or [])[:6] if isinstance(x, str) and R.guard_numbers(x, allowed)]
    props = []
    for p in obj.get("prior_evidence_proposals") or []:
        if not isinstance(p, dict):
            continue
        if R.guard_numbers(p.get("evidence", ""), allowed) and p.get("family") not in facts["prior_families"]:
            props.append({"family": R.trunc(p.get("family"), 60), "signature_hint": R.trunc(p.get("signature_hint", ""), 160),
                          "evidence": R.trunc(p.get("evidence"), 300), "result": R.trunc(p.get("result"), 120)})
    return (lines or tl), (props or tp), f"LLM wording {meta.get('model')} {meta.get('seconds', 0):.0f}s"


# ----------------------------------------------------------------------------- render
def render_weekly(facts, summary, proposals, wnote, used_samples):
    out = [f"QDESK WEEKLY {R.date_dash(facts['date'])} — {facts['days'][0]}..{facts['days'][-1]} ({wnote})"]
    out.append(R.section("SUMMARY", "\n".join(f"- {s}" for s in summary)))
    rows = [[n["date"], n["verdict"], n["n"], n["best_id"] or "-", n["best_total"] if n["best_total"] is not None else "-", "yes" if n["holdout_used"] else "no"] for n in facts["nights"]]
    out.append(R.section("NIGHTS", R.table(rows, ["date", "verdict", "rules", "best", "tot", "holdout"], "llrlrl") if rows else "no scorecards this week"))
    rows = [[p["rule_id"], p["status"], p["settled"], R.fmt(p["avg_R"], 3), R.fmt(p["backtest_avg_R"], 3), R.fmt(p["ratio"], 2), R.fmt(p["pnl"], 2), "DECAYING" if p["decaying"] else ""] for p in facts["paper"]]
    out.append(R.section("PAPER vs BACKTEST (backtest avg R is an ESTIMATE)", R.table(rows, ["rule", "status", "n", "avgR", "btR", "ratio", "pnl$", "flag"], "llrrrrrl") if rows else "no paper trades"))
    rows = [[p["rule_id"], p["settled"], R.fmt(p["brier_mkt"], 3), R.fmt(p["brier_rule"], 3), R.pct(p["hit_rate"], 1, sign=False)] for p in facts["paper"] if p["brier_mkt"] is not None]
    out.append(R.section("BRIER vs MARKET (ESTIMATE: price paid as the market probability; rule bets 1.0)", R.table(rows, ["rule", "n", "brier_mkt", "brier_rule", "hit"], "lrrrr") if rows else "no settled trades"))
    h = facts["holdout"]
    wk = ", ".join(f"{w} n={v['n']} {v['by']}" for w, v in sorted(h["weeks"].items())) or "never touched"
    out.append(R.section("HOLDOUT LEDGER", f"n_cum {h['n_cum']} -> adjusted alpha {h['alpha']:.4f} (Bonferroni)\nweeks: {wk}" + (f"\nOVER LIMIT: {h['over']}" if h["over"] else "")))
    k = facts["kills"]
    by = ", ".join(f"{a} {b}" for a, b in sorted(k["by"].items(), key=lambda x: -x[1])) or "-"
    out.append(R.section("KILLS (decisions log)", f"{k['days']} day(s): {k['evaluations']} evaluations, {k['fired']} fired, {k['killed']} killed [{by}]\n"
                         f"false-kills {k['false_kills']}/{k['settled_killed']} settled-killed, would-have-won P&L ${R.fmt(k['false_kill_pnl'], 2)}"))
    rows = [[c["date"], R.fmt(c["rows"], 0) if c["rows"] is not None else "missing"] for c in facts["coverage"]]
    out.append(R.section("RECORDER COVERAGE (feature rows per day)", R.table(rows, ["date", "rows"], "lr")))
    pl = []
    for p in proposals:
        pl.append(f"- [needs owner approval] {p['family']}: {p.get('signature_hint', '')}\n  evidence: {p['evidence']}\n  result: {p['result']}" + (f"\n  rules: {', '.join(p['rules'])}" if p.get("rules") else ""))
    out.append(R.section("PROPOSED prior_evidence.json ADDITIONS (nothing is applied automatically)", "\n".join(pl) if pl else "none this week"))
    dec = []
    if proposals:
        dec.append(f"approve or reject {len(proposals)} prior_evidence addition(s) above")
    for p in facts["paper"]:
        if p["decaying"]:
            dec.append(f"{p['rule_id']} is DECAYING (ratio {R.fmt(p['ratio'], 2)}): retire or keep observing")
    if h["over"]:
        dec.append("holdout touched more than once in a week: freeze the holdout until reviewed")
    if any(c["rows"] in (None, 0) for c in facts["coverage"]):
        dec.append("recorder gaps this week: check qd-recorder")
    if facts["llm"]["calls"] and facts["llm"]["ok"] < facts["llm"]["calls"]:
        dec.append(f"LLM calls failed: {facts['llm']['calls'] - facts['llm']['ok']}/{facts['llm']['calls']} (check claude login)")
    free, used = facts["disk"]
    if free is not None and free < 3:
        dec.append(f"disk free {R.fmt(free, 1)} GB < 3 GB: rotate")
    out.append(R.section("DECISIONS NEEDED", "\n".join(f"- {d}" for d in dec) if dec else "- none"))
    out.append(f"ops: disk free {R.fmt(free, 1)} GB, data {R.fmt(used, 2)} GB; LLM calls this week {facts['llm']['calls']} ({facts['llm']['ok']} ok, {R.fmt(facts['llm']['seconds'], 0)} s)"
               + (f"\n(dry-run: {len(used_samples)} input(s) from report/samples)" if used_samples else ""))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default=os.environ.get("QD_WEEKLY_MODEL", "cheap"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    inp = R.Inputs(dry_run=a.dry_run)
    ok, note = True, ""
    try:
        facts = gather(inp, a.date, a.days)
        summary, proposals, wnote = llm_wording(facts, a.model, a.dry_run, a.no_llm)
        text = render_weekly(facts, summary, proposals, wnote, inp.used_samples)
        R.save_json(os.path.join(inp.data, "reports", f"weekly_{a.date}_facts.json"), {k: v for k, v in facts.items() if k != "disk"} | {"proposals": proposals})
    except Exception as e:
        ok = False
        note = f"weekly failed: {e.__class__.__name__}: {str(e)[:200]}"
        text = f"QDESK WEEKLY {R.date_dash(a.date)}: FAILED — {note}"
    out_md = os.path.join(inp.data, "reports", f"weekly_{a.date}.md")
    R.save_text(out_md, text + "\n")
    tg_text = R.hard_cap(text, R.TG_MAX)
    sent = R.send_report(f"weekly-{a.date}", tg_text)
    if not a.quiet:
        print(text)
        print(f"\n--- {len(text)} chars (telegram {len(tg_text)}), telegram_sent={sent}, written {out_md}, {time.time()-t0:.1f}s")
    R.heartbeat("weekly", ok, note or f"{len(text)} chars, sent={sent}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
