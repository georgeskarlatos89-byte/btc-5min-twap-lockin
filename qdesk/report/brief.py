#!/usr/bin/env python3
"""qdesk/report/brief.py — the 07:30 UTC morning brief (one Telegram message <= 4,000 chars + markdown copy).

    python qdesk/report/brief.py --date 20261030 [--dry-run] [--no-llm] [--model cheap|strong]

Every number comes from files:
    data/evidence/evidence_<date>.json       scout status, regime, macro, anomalies
    data/hyps/hyps_<date>.json               the rules (names, why_this_could_fail)
    data/hyps/scorecard_<date>.json          scores, verdict
    data/hyps/results_<date>.json            n, edge CI, windows (summary only)
    data/hyps/skeptic_<date>.json            findings
    data/bot/strategy_state.json + paper_trades.csv + strategy_log.csv
    data/state/hb_*.json, llm_cost.csv, disk usage
The LLM (llm/prompts/brief.md) only WORDS the "market in 5 lines" and the strategy one-liners; any
line containing a number that is not in the input files is replaced by a deterministic template.
`--dry-run`: LAB_NO_TELEGRAM=1, no CLI call, missing files fall back to report/samples/ ONLY with QD_USE_SAMPLES=1.
Output: data/reports/brief_<date>.md ; Telegram via qdesk.common.tg.alert("brief-<date>", text, cls="report").
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from qdesk.report import render as R   # noqa: E402

PROMPT = os.path.join(REPO_ROOT, "qdesk", "llm", "prompts", "brief.md")
WHY_CHARS = 170
TOP_N = 3


# ----------------------------------------------------------------------------- facts
def top_rules(scorecard, results, hyps):
    rules_by_id = {h.get("id"): h for h in (hyps or {}).get("hypotheses", []) if isinstance(h, dict)}
    res_rules = (results or {}).get("rules", {}) if isinstance(results, dict) else {}
    rows = []
    for r in (scorecard or {}).get("rules", []):
        rid = r.get("id")
        rr = res_rules.get(rid, {}) if isinstance(res_rules, dict) else {}
        test = rr.get("test") or {}
        fit = rr.get("fit") or {}
        src = "test" if test else ("fit" if fit else None)
        stat = test or fit or {}
        wins = rr.get("windows") or []
        pos = sum(1 for w in wins if R.fnum(w.get("net_per_dollar"), 0) > 0) if wins else None
        h = rules_by_id.get(rid, {})
        rows.append({
            "id": rid, "name": h.get("name") or r.get("name") or "", "total": r.get("total"), "pass": bool(r.get("pass")),
            "scores": r.get("scores") or {}, "reasons": r.get("reasons") or [],
            "n": stat.get("n"), "edge": stat.get("net_per_dollar"), "ci": stat.get("ci95") or [None, None], "edge_src": src,
            "windows_pos": pos, "windows_n": len(wins) if wins else None,
            "mc_p95": (rr.get("mc_dd") or {}).get("p95"), "fill_model": rr.get("fill_model"),
            "why": r.get("why_this_could_fail") or h.get("why_this_could_fail") or "",
            "mechanism": h.get("mechanism", ""), "leg": ((h.get("entry") or {}).get("leg") or {}).get("type"),
        })
    rows.sort(key=lambda x: (-(R.fnum(x["total"], 0)), str(x["id"])))
    return rows


def paper_bot_facts(inp):
    st = inp.json("bot/strategy_state.json") or {}
    trades = R.read_csv_dicts(inp.path("bot/paper_trades.csv"))
    settled = [t for t in trades if t.get("winner") not in (None, "", "None")]
    rs = [R.fnum(t.get("R")) for t in settled if R.fnum(t.get("R")) is not None]
    avg_r = sum(rs) / len(rs) if rs else None
    bt = R.fnum(st.get("backtest_avg_R"))
    if bt is None and settled:
        bt = R.fnum(settled[-1].get("backtest_avg_R"))
    pnl = sum(R.fnum(t.get("pnl"), 0.0) for t in settled)
    last24 = [t for t in trades if (R.parse_iso(t.get("ts_utc")) or datetime(2000, 1, 1, tzinfo=timezone.utc)) >= R.utc_now().replace(microsecond=0) - __import__("datetime").timedelta(hours=24)]
    decaying = (str(st.get("status", "")).upper() == "DECAYING") or (avg_r is not None and bt is not None and bt > 0 and len(rs) >= 20 and avg_r < 0.5 * bt)
    log = R.read_csv_dicts(inp.path("bot/strategy_log.csv"))
    last_event = log[-1] if log else {}
    return {"rule_id": st.get("rule_id"), "status": st.get("status", "OBSERVE" if not st else "?"), "since": st.get("since"),
            "trades_state": st.get("trades"), "trades_csv": len(trades), "settled": len(settled), "avg_R": avg_r,
            "backtest_avg_R": bt, "pnl": pnl, "trades_24h": len(last24), "decaying": decaying,
            "last_event": f"{last_event.get('event','')} {last_event.get('reason','')}".strip() if last_event else ""}


def ops_facts(inp, date):
    free, used = R.disk_line(inp.data)
    cost = R.llm_cost_summary(inp.path("state/llm_cost.csv"), hours=24)
    hbs = R.heartbeats(inp)
    runtime = None
    if "scout" in hbs and "quant" in hbs:
        a, b = R.parse_iso(hbs["scout"]["ts"]), R.parse_iso(hbs["quant"]["ts"])
        if a and b and b > a:
            runtime = (b - a).total_seconds() / 60.0
    return {"disk_free_gb": free, "data_used_gb": used, "llm": cost, "hb": hbs, "scout_to_quant_min": runtime}


def gather(inp, date):
    ev = inp.json(f"evidence/evidence_{date}.json")
    ev_path = inp.path(f"evidence/evidence_{date}.json")
    if ev is None:
        ev, ev_path = inp.latest_json("evidence/evidence_*.json", default={})
    hyps = inp.json(f"hyps/hyps_{date}.json") or {}
    if not hyps.get("hypotheses"):                 # library night (no LLM file): rules + prose live in the pre-registration
        pre = inp.json(f"hyps/preregistration_{date}.json") or {}
        hyps = {"hypotheses": [h for h in pre.get("hypotheses", []) if isinstance(h, dict)], "source": "preregistration"}
        try:
            from qdesk.quant import library as _lib
            full = {r["id"]: r for r in _lib.get_hypotheses()}
            for h in hyps["hypotheses"]:
                h.update({k: v for k, v in full.get(h.get("id"), {}).items() if k not in h})
        except Exception:
            pass
    sc = inp.json(f"hyps/scorecard_{date}.json") or {}
    res = inp.json(f"hyps/results_{date}.json") or {}
    sk = inp.json(f"hyps/skeptic_{date}.json")
    gate = inp.json("bot/gate_pass.json")
    facts = {
        "date": date, "evidence_path": ev_path,
        "verdict": sc.get("verdict") or ("NO_SCORECARD" if not sc else "?"),
        "n_rules": len(sc.get("rules", [])) if sc else 0, "n_hyps": len(hyps.get("hypotheses", [])),
        "n_rejected": len(hyps.get("rejected", [])), "holdout_used": sc.get("holdout_used"),
        "n_tried_cum_on_holdout": sc.get("n_tried_cum_on_holdout"),
        "top_rules": top_rules(sc, res, hyps)[:TOP_N], "engine": res.get("engine"),
        "skeptic": sk, "gate": gate, "bot": paper_bot_facts(inp), "ops": ops_facts(inp, date),
        "evidence": ev or {},
    }
    return facts


# ----------------------------------------------------------------------------- wording (LLM or template)
def template_market_lines(ev):
    lines = []
    ry = (ev.get("rounds_yesterday") or {})
    r5, r15 = ry.get("5m") or {}, ry.get("15m") or {}
    if r5 or r15:
        lines.append(f"Yesterday: {r5.get('n','n/a')} five-minute rounds (up-rate {R.pct(r5.get('up_rate'), 1, sign=False)}), "
                     f"{r15.get('n','n/a')} fifteen-minute rounds (up-rate {R.pct(r15.get('up_rate'), 1, sign=False)}).")
    rg = ev.get("regime") or {}
    if rg:
        lines.append(f"Volatility: median 30-s spot vol {R.fmt(rg.get('spot_vol_30s_median_bps'), 1)} bps ({rg.get('label','?')}); "
                     f"quiet hours {rg.get('hours_quiet')}, active hours {rg.get('hours_active')}.")
    srcs = ev.get("sources") or []
    bad = [s.get("name") for s in srcs if not s.get("ok")]
    lines.append(f"Sources: {len(srcs)-len(bad)}/{len(srcs)} ok" + (f"; FAILED: {', '.join(map(str, bad))}" if bad else "") + ".")
    mn = (rg.get("macro_next") or {})
    if mn:
        lines.append(f"Next macro: {mn.get('name')} at {mn.get('utc')} (impact {mn.get('impact')}).")
    an = ev.get("anomalies") or []
    if an:
        lines.append("Anomaly: " + R.trunc(an[0], 140))
    if not lines:
        lines.append("No evidence file for this date: market not measured.")
    return lines[:5]


def template_one_liner(rule):
    leg = rule.get("leg") or "?"
    return R.trunc(f"{leg} rule: {rule.get('mechanism') or rule.get('name') or 'no mechanism given'}", 160)


def llm_wording(facts, model, dry_run, no_llm):
    """Returns (market_lines, one_liners, note). Falls back to templates on any failure."""
    ev = facts["evidence"]
    tmpl_lines = template_market_lines(ev)
    tmpl_one = {r["id"]: template_one_liner(r) for r in facts["top_rules"]}
    if dry_run or no_llm:
        return tmpl_lines, tmpl_one, "template wording (" + ("dry-run" if dry_run else "--no-llm") + ")"
    try:
        from qdesk.llm import client
    except Exception as e:
        return tmpl_lines, tmpl_one, f"template wording (llm client import failed: {e.__class__.__name__})"
    if not client.find_cli():
        return tmpl_lines, tmpl_one, "template wording (no_cli)"
    scratch_in = os.path.join(client.state_dir(), "llm_scratch", "_inputs_brief")
    os.makedirs(scratch_in, exist_ok=True)
    facts_small = {"date": facts["date"], "verdict": facts["verdict"],
                   "top_rules": [{k: r.get(k) for k in ("id", "name", "total", "n", "edge", "ci", "windows_pos", "windows_n", "why", "mechanism", "leg")} for r in facts["top_rules"]],
                   "paper_bot": {k: facts["bot"].get(k) for k in ("rule_id", "status", "settled", "avg_R", "backtest_avg_R", "decaying")}}
    fp = os.path.join(scratch_in, "facts.json")
    R.save_json(fp, facts_small)
    inputs = {"facts.json": fp}
    if facts.get("evidence_path"):
        inputs["evidence.json"] = facts["evidence_path"]
    obj, meta = client.ask_json(PROMPT, inputs, "brief_words", model=model, max_repair=1, stage="brief")
    if obj is None:
        return tmpl_lines, tmpl_one, f"template wording (llm {meta.get('error')})"
    allowed = R.numbers_in_obj(ev) | R.numbers_in_obj(facts_small)
    lines, dropped = [], 0
    for ln in (obj.get("market_5_lines") or [])[:5]:
        if R.guard_numbers(ln, allowed):
            lines.append(R.trunc(ln, 160))
        else:
            dropped += 1
    if not lines:
        lines = tmpl_lines
    ones = {}
    for r in facts["top_rules"]:
        txt = (obj.get("strategy_one_liners") or {}).get(r["id"])
        if isinstance(txt, str) and txt.strip() and R.guard_numbers(txt, allowed | R.numbers_in_obj(r)):
            ones[r["id"]] = R.trunc(txt, 160)
        else:
            ones[r["id"]] = tmpl_one[r["id"]]
            dropped += 1
    note = f"LLM wording {meta.get('model')} {meta.get('seconds', 0):.0f}s" + (f", {dropped} line(s) replaced (unknown numbers)" if dropped else "")
    return lines, ones, note


# ----------------------------------------------------------------------------- render
def render_brief(facts, market_lines, one_liners, wording_note, used_samples):
    date = facts["date"]
    ev, bot, ops, sk = facts["evidence"], facts["bot"], facts["ops"], facts["skeptic"]
    out = []
    verdict = facts["verdict"]
    best = facts["top_rules"][0] if facts["top_rules"] else None
    vline = f"VERDICT {R.date_dash(date)}: {verdict}"
    if best is not None:
        vline += f" — {facts['n_rules']} rules scored, best {best['id']} {best['total']}/25"
    elif facts["n_rules"] == 0:
        vline += " — no scorecard for this date"
    if facts.get("holdout_used"):
        vline += f" | HOLDOUT TOUCHED (cum {facts.get('n_tried_cum_on_holdout')})"
    out.append(vline)

    # scout
    srcs = ev.get("sources") or []
    parts = [f"{s.get('name')} {'ok' if s.get('ok') else 'FAIL'}" + (f" {s.get('rows')}" if s.get('ok') and s.get('rows') is not None else "") for s in srcs]
    gen = ev.get("generated")
    out.append("SCOUT " + (f"{R.hhmm(gen)} " if gen else "") + (" | ".join(parts) if parts else "no evidence file"))

    # regime + macro
    rg = ev.get("regime") or {}
    mn = rg.get("macro_next") or {}
    hl = ev.get("hyperliquid") or {}
    md = ev.get("moondev") or {}
    regime = (f"REGIME vol30s median {R.fmt(rg.get('spot_vol_30s_median_bps'), 1)} bps [{rg.get('label', 'n/a')}] | quiet {rg.get('hours_quiet')} active {rg.get('hours_active')}"
              f" | funding {R.fmt(hl.get('funding_1h'), 6)} OI ${R.fmt(hl.get('oi_usd'), 0)} [{hl.get('label', 'n/a')}]"
              f" | liq24h L/S ${R.fmt(md.get('liq_long_24h_usd'), 0)}/${R.fmt(md.get('liq_short_24h_usd'), 0)} [{md.get('label', 'n/a')}]"
              f"\nMACRO next: {mn.get('name', 'n/a')} {mn.get('utc', '')} impact {mn.get('impact', 'n/a')}")
    out.append(regime)

    out.append("MARKET IN 5 LINES (" + wording_note + "):\n" + "\n".join(f"- {ln}" for ln in market_lines))

    # top rules
    rl = [f"TOP {min(TOP_N, len(facts['top_rules']))} RULES ({facts['n_hyps']} proposed, {facts['n_rejected']} rejected pre-test, engine {facts.get('engine') or 'n/a'}):"]
    for i, r in enumerate(facts["top_rules"], 1):
        s = r["scores"]
        sc_txt = f"t{s.get('oos_tstat','?')} dd{s.get('max_dd','?')} n{s.get('n_trades','?')} e{s.get('edge_after_costs','?')} s{s.get('stability','?')}"
        stab = f"{r['windows_pos']}/{r['windows_n']}" if r["windows_pos"] is not None else "n/a"
        fm = " [ESTIMATE fill]" if r.get("fill_model") and "ESTIMATE" in str(r["fill_model"]) else ""
        rl.append(f"{i}. {r['id']} {R.trunc(r['name'], 48)} — {r['total']}/25 {'PASS' if r['pass'] else 'FAIL'} ({sc_txt})"
                  f" n={r['n'] if r['n'] is not None else 'n/a'}{'' if r['edge_src'] in (None, 'test') else ' FIT-ONLY'} edge {R.pct(r['edge'], 2)} {R.ci(r['ci'][0], r['ci'][1])} stab {stab}{fm}")
        if one_liners.get(r["id"]):
            rl.append(f"   {one_liners[r['id']]}")
        rl.append(f"   WHY THIS COULD FAIL: {R.trunc(r['why'], WHY_CHARS)}")
        if r["reasons"]:
            rl.append(f"   gate: {R.trunc('; '.join(map(str, r['reasons'])), 120)}")
    if not facts["top_rules"]:
        rl.append("(no rules scored tonight)")
    out.append("\n".join(rl))

    # paper bot
    if bot.get("rule_id"):
        bl = (f"PAPER BOT {bot['rule_id']} status {bot['status']}{' DECAYING' if bot['decaying'] else ''} since {bot.get('since') or 'n/a'}"
              f" | settled {bot['settled']} (24h: {bot['trades_24h']}) avg R {R.fmt(bot['avg_R'], 3)} vs backtest {R.fmt(bot['backtest_avg_R'], 3)} (ESTIMATE)"
              f" | paper P&L ${R.fmt(bot['pnl'], 2)}")
        if bot["avg_R"] is not None and bot["backtest_avg_R"]:
            bl += f" | ratio {R.fmt(bot['avg_R'] / bot['backtest_avg_R'], 2)}"
        if bot["last_event"]:
            bl += f"\n  last: {R.trunc(bot['last_event'], 140)}"
    else:
        bl = "PAPER BOT: OBSERVE mode, no active rule (no gate_pass)"
    g = facts.get("gate") or {}
    if g:
        bl += f"\n  gate_pass {g.get('rule_id')} {g.get('generated', '')[:10]} total {g.get('scorecard_total')} skeptic_ok {g.get('skeptic_ok')} paper_only {g.get('paper_only')}"
    out.append(bl)

    # skeptic
    if sk is None:
        out.append("SKEPTIC: no file for this date")
    else:
        fs = sk.get("findings") or []
        hi = [f for f in fs if f.get("severity") == "high"]
        sl = f"SKEPTIC ok={sk.get('ok')} findings {len(fs)} (high {len(hi)}) holdout_reuse={sk.get('holdout_reuse')} weakened_tests={sk.get('weakened_tests')} lookahead={sk.get('lookahead_fields') or []}"
        for f in (hi or fs)[:2]:
            sl += f"\n  {f.get('severity')}: {R.trunc(f.get('what'), 150)} @ {R.trunc(f.get('where'), 60)}"
        out.append(sl)

    # ops
    hb = ops["hb"]
    hb_txt = " ".join(f"{st} {R.hhmm(v['ts'])}{'' if v['ok'] else '!FAIL'}" for st, v in hb.items()) or "no heartbeats"
    llm = ops["llm"]
    ol = (f"OPS disk free {R.fmt(ops['disk_free_gb'], 1)} GB, data {R.fmt(ops['data_used_gb'], 2)} GB | LLM 24h: {llm['calls']} calls ({llm['ok']} ok) {R.fmt(llm['seconds'], 0)} s"
          f" | stages: {hb_txt}")
    if ops.get("scout_to_quant_min") is not None:
        ol += f" | scout end -> quant end {R.fmt(ops['scout_to_quant_min'], 0)} min"
    stale = [st for st, v in hb.items() if st in ("bot", "recorder") and R.parse_iso(v["ts"]) and (R.utc_now() - R.parse_iso(v["ts"])).total_seconds() > 900]
    if stale and not used_samples:
        ol += f" | STALE heartbeat: {', '.join(stale)}"
    if used_samples:
        ol += f"\n(dry-run: {len(used_samples)} input(s) from report/samples)"
    out.append(ol)
    if used_samples:
        out.insert(0, f"SAMPLE DATA IN THIS BRIEF ({len(used_samples)} input(s) from report/samples, render test only)")
    text = "\n\n".join(out)
    return R.hard_cap(text, R.TG_MAX)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default=os.environ.get("QD_BRIEF_MODEL", "cheap"))
    ap.add_argument("--quiet", action="store_true", help="do not print the brief")
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
        facts = gather(inp, a.date)
        lines, ones, wnote = llm_wording(facts, a.model, a.dry_run, a.no_llm)
        text = render_brief(facts, lines, ones, wnote, inp.used_samples)
    except Exception as e:
        ok = False
        note = f"brief failed: {e.__class__.__name__}: {str(e)[:200]}"
        text = f"VERDICT {R.date_dash(a.date)}: BRIEF FAILED — {note}"
    out_md = os.path.join(inp.data, "reports", f"brief_{a.date}.md")
    R.save_text(out_md, text + "\n")
    sent = R.send_report(f"brief-{a.date}", text)
    if not a.quiet:
        print(text)
        print(f"\n--- {len(text)} chars, telegram_sent={sent}, written {out_md}, {time.time()-t0:.1f}s")
    R.heartbeat("brief", ok, note or f"{len(text)} chars, sent={sent}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
