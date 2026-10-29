#!/usr/bin/env python3
"""selftest.py - run: python qdesk/common/selftest.py   (from any cwd; no network, no Telegram).
Prints one PASS/FAIL line per check and exits 1 if any check failed."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))          # parent of the qdesk package
sys.path.insert(0, REPO_ROOT)
os.environ["LAB_NO_TELEGRAM"] = "1"
os.environ["QD_DATA"] = tempfile.mkdtemp(prefix="qdesk_selftest_")

from qdesk.common import dsl, fees, settle, stats, sanitize, hashing, tg, schemas  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


EXAMPLE = {
    "id": "H20261030-03", "name": "late-round momentum taker, quiet hours", "market": "5m",
    "side": "signal_sign", "side_field": "mom_5s",
    "entry": {"when": [{"f": "t_el", "op": ">=", "v": 200}, {"f": "t_el", "op": "<=", "v": 270},
                       {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 4}, {"f": "ask", "op": "<=", "v": 0.80},
                       {"f": "session_hour", "op": "in", "v": [0, 1, 2, 3, 4, 5]}, {"f": "macro_blackout", "op": "==", "v": 0}],
              "leg": {"type": "taker", "price": "ask", "size_usd": 5, "max_price": 0.85}},
    "exit": {"mode": "hold_to_settle"},
    "limits": {"max_trades_per_round": 1, "cooldown_s": 30, "max_rounds_per_day": 60},
    "prediction": {"net_per_dollar": 0.01, "trades_per_day": 25, "label": "ESTIMATE"},
    "mechanism": "one paragraph of mechanism", "who_loses": "one paragraph on who loses",
    "why_this_could_fail": "mandatory paragraph on failure", "novelty_vs_prior": "how it differs from the dead families",
}


def main():
    # 1. schema / dsl validation
    errs = dsl.validate_rule(EXAMPLE)
    check("validate_rule(example from INTERFACES s3) ok", errs == [], "; ".join(errs))
    check("schemas.validate('hypothesis', example) ok", schemas.validate("hypothesis", EXAMPLE) == [])
    bad = json.loads(json.dumps(EXAMPLE))
    bad["entry"]["when"].append({"f": "winner", "op": "==", "v": 1})
    errs = dsl.validate_rule(bad)
    check("forbidden field 'winner' rejected", any("winner" in e for e in errs), errs[0] if errs else "no error")
    bad2 = json.loads(json.dumps(EXAMPLE)); bad2["entry"]["leg"]["size_usd"] = 6
    check("size_usd > 5 rejected", any("size_usd" in e for e in dsl.validate_rule(bad2)))
    bad3 = json.loads(json.dumps(EXAMPLE)); del bad3["why_this_could_fail"]
    check("missing why_this_could_fail rejected", any("why_this_could_fail" in e for e in dsl.validate_rule(bad3)))
    check("FEATURE_COLUMNS has 38 columns, starts market and ends src",
          len(schemas.FEATURE_COLUMNS) == 38 and schemas.FEATURE_COLUMNS[0] == "market" and schemas.FEATURE_COLUMNS[-1] == "src")

    # 2. canonical hash
    h1 = dsl.canonical_hash(EXAMPLE)
    reordered = json.loads(json.dumps(dict(reversed(list(EXAMPLE.items())))))
    reordered["entry"] = dict(reversed(list(reordered["entry"].items())))
    reordered["name"] = "renamed"; reordered["why_this_could_fail"] = "different prose, same logic"
    h2 = dsl.canonical_hash(reordered)
    check("canonical_hash stable across key order / name / prose", h1 == h2 and len(h1) == 64, h1[:16])
    changed = json.loads(json.dumps(EXAMPLE)); changed["entry"]["when"][0]["v"] = 201
    check("canonical_hash changes when logic changes", dsl.canonical_hash(changed) != h1)
    s = json.loads(json.dumps(EXAMPLE)); s["exit"] = "hold_to_settle"
    check("exit string and object form hash the same", dsl.canonical_hash(s) == h1)

    # 3. eval_when on a sample row (strings as from the CSV), abs:, other:, NA
    row_up = {"t_el": "240", "ba": "0.72", "bb": "0.70", "spot_bps_vs_open": "-5.3", "session_hour": "3",
              "macro_blackout": "0", "mom_5s": "-2.1", "imb3": "0.6"}
    row_down = {"t_el": "240", "ba": "0.31", "bb": "0.28", "spot_bps_vs_open": "-5.3", "session_hour": "3",
                "macro_blackout": "0", "mom_5s": "-2.1", "imb3": "0.4"}
    when = EXAMPLE["entry"]["when"]
    check("eval_when true on sample row (abs: and string numbers)", dsl.eval_when(when, row_up, row_down) is True)
    check("eval_when other: reads the other side", dsl.eval_when([{"f": "other:ask", "op": "<", "v": 0.35}], row_up, row_down)
          and not dsl.eval_when([{"f": "other:ask", "op": "<", "v": 0.30}], row_up, row_down))
    check("eval_when 'between' and 'in'", dsl.eval_when([{"f": "imb3", "op": "between", "v": [0.5, 0.7]},
                                                          {"f": "session_hour", "op": "in", "v": [3]}], row_up))
    na = dict(row_up); na["spot_bps_vs_open"] = ""
    check("NA ('') makes the condition False", dsl.eval_when(when, na, row_down) is False)
    na2 = dict(row_up); na2["spot_bps_vs_open"] = None
    check("NA (None) makes the condition False", dsl.eval_when(when, na2, row_down) is False)
    check("eval_when false when a condition fails", dsl.eval_when(when, dict(row_up, ba="0.90"), row_down) is False)
    check("chosen_side signal_sign negative mom -> Down (Down row must pass)",
          dsl.chosen_side(EXAMPLE, row_up, row_down) == "Down")
    pos = dict(row_up, mom_5s="3.0")
    check("chosen_side signal_sign positive mom -> Up", dsl.chosen_side(EXAMPLE, pos, row_down) == "Up")
    both = json.loads(json.dumps(EXAMPLE)); both["side"] = "both"; both.pop("side_field")
    both["entry"]["leg"] = {"type": "maker", "price": "bb", "offset_ticks": 0, "size_usd": 5, "ttl_s": 20, "pair_cap": 0.97}
    both["entry"]["when"] = [{"f": "t_el", "op": "between", "v": [60, 240]}]
    check("maker both-sides rule validates and chosen_side == 'both'",
          dsl.validate_rule(both) == [] and dsl.chosen_side(both, row_up, row_down) == "both")
    check("entry_fields strips prefixes", dsl.entry_fields(EXAMPLE) == ["ask", "macro_blackout", "mom_5s", "session_hour", "spot_bps_vs_open", "t_el"],
          str(dsl.entry_fields(EXAMPLE)))

    # 4. fees
    check("taker_fee(0.5, 100) == 1.75", fees.taker_fee(0.5, 100) == 1.75, str(fees.taker_fee(0.5, 100)))
    check("taker_fee(0.97, 100) == 0.2037", fees.taker_fee(0.97, 100) == 0.2037, str(fees.taker_fee(0.97, 100)))
    check("maker_fee == 0, maker_rebate == 0.35 at p=0.5/100sh", fees.maker_fee(0.5, 100) == 0.0 and fees.maker_rebate(0.5, 100) == 0.35)
    check("taker_rebate_pct tiers", fees.taker_rebate_pct(1999) == ("None", 0.0) and fees.taker_rebate_pct(2000) == ("Bronze", 0.03)
          and fees.taker_rebate_pct(5e7) == ("Obsidian", 0.50) and fees.taker_rebate_pct(250_000) == ("Gold", 0.18))

    # 5. settle on a pair
    legs = [{"side": "Up", "price": 0.48, "size": 10, "fee": 0.0}, {"side": "Down", "price": 0.47, "size": 10, "fee": 0.0}]
    r = settle.settle_pnl(legs, "Up")
    check("settle_pnl pair: cost 9.5 payout 10 net 0.5 pairs 10",
          r == {"cost": 9.5, "payout": 10.0, "fees": 0.0, "net": 0.5, "pairs": 10.0}, str(r))
    r2 = settle.settle_pnl([{"side": "Up", "price": 0.72, "size": 6.944444, "fee": fees.taker_fee(0.72, 6.944444)}], "Down")
    check("settle_pnl losing taker leg: payout 0, net = -cost - fee", r2["payout"] == 0 and abs(r2["net"] + r2["cost"] + r2["fees"]) < 1e-9, str(r2))
    check("brier(0.7,'Up') == 0.09 and brier(0.7,'Down') == 0.49", settle.brier(0.7, "Up") == 0.09 and settle.brier(0.7, "Down") == 0.49)

    # 6. stats
    vals = [0.5, -0.3, 0.2, 0.1, -0.4, 0.7, 0.0, 0.3, -0.1, 0.2]
    a, b = stats.boot_ci(vals); a2, b2 = stats.boot_ci(vals)
    check("boot_ci deterministic (seed 7, 2000 draws)", (a, b) == (a2, b2) and a <= sum(vals) <= b, f"{a:.3f}..{b:.3f} sum={sum(vals):.2f}")
    a90, b90 = stats.boot_ci(vals, alpha=0.10)
    check("boot_ci alpha=0.10 narrower than alpha=0.05", a90 >= a and b90 <= b)
    check("boot_ci empty -> (None, None)", stats.boot_ci([]) == (None, None))
    check("tstat positive for a positive mean, 0 for n<2", stats.tstat(vals) > 0 and stats.tstat([1.0]) == 0.0, f"{stats.tstat(vals):.3f}")
    days = [f"202610{d:02d}" for d in range(1, 22)]
    wins = stats.walk_forward(days, k=5, fit=6, test=3)
    tests = [d for w in wins for d in w["test"]]
    check("walk_forward 5 windows, test slices disjoint, fit precedes test",
          len(wins) == 5 and len(tests) == len(set(tests)) and all(len(w["fit"]) == 6 and len(w["test"]) == 3 and max(w["fit"]) < min(w["test"]) for w in wins),
          f"last test={wins[-1]['test']}")
    check("walk_forward too few days -> []", stats.walk_forward(days[:5]) == [])
    md = stats.mc_drawdown(vals * 10, capital=100.0)
    check("mc_drawdown shape {p50,p95,n:1000} and p95 >= p50", set(md) == {"p50", "p95", "n"} and md["n"] == 1000 and md["p95"] >= md["p50"] >= 0,
          str(md))
    check("mc_drawdown deterministic", stats.mc_drawdown(vals * 10) == md)
    check("bonferroni_alpha(10) == 0.005", stats.bonferroni_alpha(10) == 0.005)

    # 7. sanitize
    meta = sanitize.round_meta({"slug": "btc-updown-5m-1761700800", "start": 1761700800, "winner": "Up", "closed": True,
                                "outcomePrices": "[1,0]", "conditionId": "0xabc", "tick": 0.01, "umaResolutionStatus": "resolved"})
    check("sanitize strips 'winner' (and closed/outcomePrices), keeps whitelist",
          "winner" not in meta and "closed" not in meta and "outcomePrices" not in meta and meta.get("condition_id") == "0xabc" and meta["slug"].startswith("btc-"),
          str(meta))
    check("sanitize whitelist is the section-5 tuple", sanitize.ROUND_META_WHITELIST == ("slug", "start", "end", "market", "up_token", "down_token", "condition_id", "tick", "fee_rate"))

    # 8. hashing
    h = hashing.code_hash(hashing.CODE_FILES_ENGINE)
    check("code_hash(CODE_FILES_ENGINE) 16 hex, stable", len(h) == 16 and h == hashing.code_hash(hashing.CODE_FILES_ENGINE), h)
    tmpd = tempfile.mkdtemp(prefix="qdesk_corpus_")
    for n, c in (("b.csv", b"2"), ("a.csv", b"1")):
        open(os.path.join(tmpd, n), "wb").write(c)
    ch = hashing.corpus_hash([os.path.join(tmpd, "b.csv"), os.path.join(tmpd, "a.csv")])
    check("corpus_hash order-independent", ch == hashing.corpus_hash([os.path.join(tmpd, "a.csv"), os.path.join(tmpd, "b.csv")]) and len(ch) == 16, ch)

    # 9. schemas io + other validators
    p = os.path.join(os.environ["QD_DATA"], "hyps", "x.json")
    schemas.save_json(p, EXAMPLE)
    check("save_json/load_json round trip (indent=1, atomic)", schemas.load_json(p) == EXAMPLE and open(p).read().startswith('{\n "id"'))
    gp = {"generated": "2026-10-30T05:30:00Z", "rule_id": "H1", "rule_hash": h1, "code_hash": h, "corpus_hash": ch, "features_hash": "x",
          "scorecard_total": 19, "criteria": {}, "test_days": ["20261005"], "skeptic_ok": True, "gate_pass": True, "paper_only": True}
    check("validate('gate_pass') ok", schemas.validate("gate_pass", gp) == [], "; ".join(schemas.validate("gate_pass", gp)))
    check("validate('gate_pass') rejects paper_only=false", any("paper_only" in e for e in schemas.validate("gate_pass", dict(gp, paper_only=False))))
    sc = {"date": "20261030", "rules": [{"id": "H1", "scores": {"oos_tstat": 2, "max_dd": 3, "n_trades": 1, "edge_after_costs": 2, "stability": 3},
                                        "total": 11, "pass": False, "reasons": ["n_trades<30"], "why_this_could_fail": "x"}],
          "passed": [], "verdict": "NOTHING_PASSED", "holdout_used": False, "n_tried_cum_on_holdout": 0}
    check("validate('scorecard') ok on NOTHING_PASSED", schemas.validate("scorecard", sc) == [], "; ".join(schemas.validate("scorecard", sc)))
    check("validate('scorecard') catches wrong total", any("total" in e for e in schemas.validate("scorecard", dict(sc, rules=[dict(sc["rules"][0], total=12)]))))
    pr = {"date": "20261030", "written": "2026-10-30T03:00:00Z", "hypotheses": [{"id": "H1", "rule_hash": h1, "prediction": {}}],
          "fit_days": ["20260926", "20260927"], "test_days": ["20261005"], "windows": wins, "thresholds": {}, "n_hypotheses": 1,
          "bonferroni_alpha": 0.005, "holdout_touch_allowed": False, "code_hash": h, "features_hash": "x"}
    check("validate('prereg') ok", schemas.validate("prereg", pr) == [], "; ".join(schemas.validate("prereg", pr)))
    check("validate('prereg') rejects fit/test overlap", any("overlap" in e for e in schemas.validate("prereg", dict(pr, test_days=["20260926"]))))
    res = {"engine": "rule_engine_1s", "lat": 0.1, "spot_lag": 1.44, "rules": {"H1": {
        "per_day": {"20260926": {"trades": 1, "cost": 5.0, "gross": -5.0, "fees": 0.1, "net": -5.1, "rounds": 288}},
        "trades": [{"day": "20260926", "start": 1758844800, "side": "Up", "t_el": 240, "leg": "taker", "price": 0.72, "size": 6.94,
                    "fill": "full", "fee": 0.098, "winner": "Down", "pnl": -5.1, "R": -1.02, "ctx": {}}],
        "fit": {"net_per_dollar": -1.02, "ci95": [-1.02, -1.02], "n": 1}, "test": None, "windows": [],
        "mc_dd": {"p50": 5.1, "p95": 5.1, "n": 1000}, "fill_model": "taker_ask_ok_100ms"}}}
    check("validate('results') ok", schemas.validate("results", res) == [], "; ".join(schemas.validate("results", res)))
    check("validate('results') rejects bad fill", any("fill" in e for e in schemas.validate("results", json.loads(json.dumps(res).replace('"full"', '"maybe"')))))
    ev = {"date": "20261030", "generated": "2026-10-30T01:00:00Z", "sources": [{"name": "gamma", "ok": True, "rows": 288, "note": ""}],
          "rounds_yesterday": {"5m": {"n": 288, "up_rate": 0.49, "chain_verified": 288, "gamma_disagree": 0}},
          "price1m": {"days_archived": 1, "rounds_with_history": 288},
          "features": {"days_available": 19, "newest": "20261014", "fit_days": ["20260926"], "frozen_holdout_days": ["20261005"], "forward_days": []},
          "regime": {"spot_vol_30s_median_bps": 1.2, "hours_quiet": [0, 1], "hours_active": [13, 14], "macro_next": {"name": "FOMC", "utc": "2026-10-29T18:00:00Z", "impact": "high"}},
          "moondev": {"label": "MEASURED"}, "hyperliquid": {}, "wallets": {"scanned": 0, "top": []},
          "paper_bot": {"active_rule": None, "trades_7d": 0, "avg_R": None, "backtest_avg_R": None, "status": "OBSERVE"},
          "prior_evidence_ref": "prior_evidence.json v3", "anomalies": []}
    check("validate('evidence') ok", schemas.validate("evidence", ev) == [], "; ".join(schemas.validate("evidence", ev)))
    check("validate('evidence') rejects missing moondev.label", any("label" in e for e in schemas.validate("evidence", dict(ev, moondev={}))))
    check("validate unknown schema -> error", schemas.validate("nope", {}) != [])
    for name in schemas.SCHEMA_NAMES:
        json.load(open(schemas.schema_path(name)))
    check("six .schema.json files parse", True)

    # 10. prior_evidence.json
    pe = schemas.load_json(os.path.join(os.path.dirname(HERE), "prior_evidence.json"))
    fams = [f["family"] for f in pe["families"]]
    check("prior_evidence.json v3 with the 6 dead families", pe["version"] == 3 and set(fams) == {
        "resting_bid_maker", "timed_both_sides_post", "flow_filtered_maker", "stale_ask_model_taker", "late_round_taker_s1", "paired_maker_join"}
          and all(set(f) >= {"family", "signature", "evidence", "result", "date"} for f in pe["families"]), ",".join(fams))

    # 11. tg (no network): heartbeat, append_csv, alert under LAB_NO_TELEGRAM, disk_free_gb
    hb = tg.heartbeat("selftest", True, "ok")
    doc = json.load(open(hb))
    check("heartbeat writes data/state/hb_selftest.json {ts,stage,ok,note}", hb.endswith("hb_selftest.json") and {"ts", "stage", "ok", "note"} <= set(doc) and doc["ok"] is True
          and os.path.dirname(hb) == os.path.join(os.environ["QD_DATA"], "state"))
    csvp = os.path.join(os.environ["QD_DATA"], "state", "llm_cost.csv")
    tg.append_csv(csvp, ["ts", "stage", "ok"], [1, "selftest", 1]); tg.append_csv(csvp, ["ts", "stage", "ok"], {"ts": 2, "stage": "x", "ok": 0})
    check("append_csv header once + 2 rows", open(csvp).read().splitlines() == ["ts,stage,ok", "1,selftest,1", "2,x,0"])
    sent = tg.alert("selftest_key", "hello", cls="warn")
    log = open(os.path.join(os.environ["QD_DATA"], "state", "alerts.log")).read()
    check("alert under LAB_NO_TELEGRAM sends nothing, logs suppressed", sent is False and "suppressed" in log)
    check("alert warn dedup within 6 h", tg.alert("selftest_key", "hello again", cls="warn") is False and "DEDUP" in open(os.path.join(os.environ["QD_DATA"], "state", "alerts.log")).read())
    check("disk_free_gb > 0", tg.disk_free_gb() > 0, f"{tg.disk_free_gb():.1f} GB")

    n_fail = RESULTS.count(False)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed" + (f", {n_fail} FAILED" if n_fail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
