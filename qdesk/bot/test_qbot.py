#!/usr/bin/env python3
"""test_qbot.py - unit checks for the qdesk paper bot (no network, no keys, no VM). Style: paired_maker/test_pm.py.

  python bot/test_qbot.py        (from the qdesk folder)   or   python qdesk/bot/test_qbot.py (from the repo root)

Prefers the real qdesk.common.* / qdesk.features.features; falls back to bot/_stubs.py for whatever is not importable
yet and says so in the first output line.
"""
import gzip, json, os, re, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); sys.path.insert(0, ROOT)
os.environ["DRY_RUN"] = "true"; os.environ["LAB_NO_TELEGRAM"] = "1"
TMP = tempfile.mkdtemp(prefix="qdesk_test_"); os.environ["QD_DATA"] = TMP

from qdesk.bot import _stubs
STUBBED = _stubs.install()
print("stubbed modules: " + (", ".join(STUBBED) if STUBBED else "none (all real)"))

from qdesk.bot import _util, gate, decay, builder, qbot
from qdesk.bot.paper_fill import PaperBook
from qdesk.common import dsl, hashing, fees

fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1


RULE = {"id": "H20261030-03", "name": "late-round momentum taker", "market": "5m", "side": "signal_sign", "side_field": "mom_5s",
        "entry": {"when": [{"f": "t_el", "op": ">=", "v": 200}, {"f": "t_el", "op": "<=", "v": 270},
                           {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 4}, {"f": "ask", "op": "<=", "v": 0.80},
                           {"f": "macro_blackout", "op": "==", "v": 0}],
                  "leg": {"type": "taker", "price": "ask", "size_usd": 5, "max_price": 0.85}},
        "exit": {"mode": "hold_to_settle"},
        "limits": {"max_trades_per_round": 1, "cooldown_s": 30, "max_rounds_per_day": 60},
        "prediction": {"net_per_dollar": 0.01, "trades_per_day": 25, "label": "ESTIMATE"},
        "mechanism": "late momentum continues into the TWAP settlement window", "who_loses": "resting bids that are slow to reprice",
        "why_this_could_fail": "the ask lifts before the taker lands and the fee eats the edge",
        "novelty_vs_prior": "taker not maker, momentum gate not time-only"}

# ---------------------------------------------------------------- 1-3 rule evaluation on synthetic rows
row_up = dict(t_el=210, time_left=90, bb=0.72, ba=0.74, spot_bps_vs_open=6.0, mom_5s=3.0, macro_blackout=0, session_hour=2)
row_dn = dict(t_el=210, time_left=90, bb=0.26, ba=0.28, spot_bps_vs_open=6.0, mom_5s=-3.0, macro_blackout=0, session_hour=2)
check("rule fires on a synthetic row (t_el 210, +6 bps, ask 0.74)", dsl.chosen_side(RULE, row_up, row_dn) == "Up")
check("rule does not fire outside the window (t_el 100)", dsl.chosen_side(RULE, dict(row_up, t_el=100), dict(row_dn, t_el=100)) is None)
check("rule does not fire on NA signal", dsl.chosen_side(RULE, dict(row_up, spot_bps_vs_open=""), row_dn) is None)
check("signal_sign: negative mom_5s -> Down", dsl.chosen_side(RULE, dict(row_up, mom_5s=-2.0), row_dn) == "Down")

# ---------------------------------------------------------------- 4-11 paper fills
def book(asks=((0.74, 50.0),), bids=((0.72, 10.0),)):
    pb = PaperBook(); pb.set_pair("U", "D")
    pb.on_book("U", list(bids), list(asks), 0.0); pb.on_book("D", [(0.26, 100.0)], [(0.28, 100.0)], 0.0)
    return pb

pb = book(); oid = pb.submit(dict(leg="taker", asset="U", side="Up", size=6.76, price=0.74), 0.0)
pb.on_price_change("U", "SELL", 0.74, 0, 0.05, bb=0.72, ba=0.76)          # the ask lifts within 100 ms
check("taker: ask lifted within 100 ms -> no fill", pb.on_tick(0.1) == [] and pb.status(oid) == "none")
pb = book(); oid = pb.submit(dict(leg="taker", asset="U", side="Up", size=6.76, price=0.74), 0.0)
f = pb.on_tick(0.1)
check("taker: ask still there -> full fill at 0.74 with the taker fee", len(f) == 1 and f[0]["size"] == 6.76 and f[0]["price"] == 0.74
      and abs(f[0]["fee"] - fees.taker_fee(0.74, 6.76)) < 1e-9 and pb.status(oid) == "full")
check("taker: not resolved before 100 ms", book().on_tick(0.05) == [] )
pb = book(asks=((0.74, 3.0),)); oid = pb.submit(dict(leg="taker", asset="U", side="Up", size=6.76, price=0.74), 0.0); f = pb.on_tick(0.1)
check("taker: partial fill limited by the ask size (3 of 6.76)", len(f) == 1 and f[0]["size"] == 3.0 and pb.status(oid) == "partial")
pb = book(); oid = pb.submit(dict(leg="maker", asset="U", side="Up", size=5.0, offset_ticks=0, ttl_s=60), 0.0)
o = pb.orders[oid]
check("maker rests at bb with queue_ahead = resting size", o["price"] == 0.72 and o["qa"] == 10.0)
f1 = pb.on_trade("U", "SELL", 0.72, 8.0, "1000", 1.0)
f2 = pb.on_trade("U", "SELL", 0.72, 4.0, "1001", 2.0)
check("maker: queue consumed first (8 of 10), then filled (4 - 2 = 2)", f1 == [] and o["qa"] == 2.0 - 0.0 or (f1 == [] and len(f2) == 1 and f2[0]["size"] == 2.0))
check("maker: trade at our price after the queue fills us", len(f2) == 1 and abs(f2[0]["size"] - 2.0) < 1e-9 and o["qa"] == 0.0)
f3 = pb.on_trade("D", "BUY", 0.28, 10.0, "1002", 3.0)                     # BUY on the other side at 0.28 = SELL on ours at 0.72
check("maker: BUY on the other asset at 1-p hits our bid -> remaining 3 filled", len(f3) == 1 and abs(f3[0]["size"] - 3.0) < 1e-9 and pb.status(oid) == "full")
pb = book(); oid = pb.submit(dict(leg="maker", asset="U", side="Up", size=5.0, offset_ticks=0, ttl_s=60), 0.0)
f = pb.on_trade("U", "SELL", 0.71, 4.0, "2000", 1.0)
check("maker: trade below our price fills us first (queue untouched)", len(f) == 1 and f[0]["size"] == 4.0 and pb.orders[oid]["qa"] == 10.0)
pb.on_price_change("U", "BUY", 0.72, 3.0, 1.5, bb=0.72, ba=0.74)
check("maker: level shrink caps queue_ahead (10 -> 3)", pb.orders[oid]["qa"] == 3.0)
pb = book(); oid = pb.submit(dict(leg="maker", asset="U", side="Up", size=5.0, offset_ticks=1, ttl_s=10), 0.0)
pb.on_tick(9.9); s1 = pb.status(oid); pb.on_tick(10.0)
check("maker: offset 1 tick rests at bb-0.01 and ttl cancels at 10 s", pb.orders[oid]["price"] == 0.71 and s1 == "resting" and pb.status(oid) == "expired")
pb = book(); oid = pb.submit(dict(leg="maker", asset="U", side="Up", size=5.0, offset_ticks=0, ttl_s=60), 0.0)
a = pb.on_trade("U", "SELL", 0.72, 6.0, "3000", 1.0); b = pb.on_trade("U", "SELL", 0.72, 6.0, "3000", 1.1); qa_after_dupe = pb.orders[oid]["qa"]
c = pb.on_trade("U", "SELL", 0.72, 6.0, "3001", 1.2)
check("dedupe: the repeated (ts, price, size) trade is ignored (queue 10 -> 4 once), a new ts counts (fills 2)",
      a == [] and b == [] and pb.n_dupes == 1 and qa_after_dupe == 4.0 and len(c) == 1 and abs(c[0]["size"] - 2.0) < 1e-9)

# ---------------------------------------------------------------- 12-16 gate refusals
def write_gate(ddir, **over):
    g = {"generated": _util.iso(), "rule_id": RULE["id"], "rule_hash": dsl.canonical_hash(RULE), "code_hash": hashing.code_hash(hashing.CODE_FILES_BOT),
         "corpus_hash": "abc123", "features_hash": "f00", "scorecard_total": 20, "criteria": {}, "test_days": [], "skeptic_ok": True,
         "gate_pass": True, "paper_only": True}
    g.update(over); _util.save_json(os.path.join(ddir, "bot", "gate_pass.json"), g)

G = os.path.join(TMP, "gate"); os.makedirs(os.path.join(G, "bot"))
ok, r = gate.check(RULE, G); check("gate: refused when gate_pass.json is missing", ok is False and any("missing" in x for x in r))
write_gate(G, generated=_util.iso(time.time() - 8 * 86400)); ok, r = gate.check(RULE, G)
check("gate: refused when stale (8 days)", ok is False and any("days old" in x for x in r))
write_gate(G, code_hash="deadbeefdeadbeef"); ok, r = gate.check(RULE, G)
check("gate: refused on code hash mismatch", ok is False and any("code hash mismatch" in x for x in r))
write_gate(G, rule_hash="0" * 64); ok, r = gate.check(RULE, G)
check("gate: refused on rule hash mismatch", ok is False and any("rule_hash mismatch" in x for x in r))
write_gate(G, paper_only=False); ok, r = gate.check(RULE, G)
check("gate: refused when paper_only is false", ok is False and any("paper_only" in x for x in r))
write_gate(G, corpus_hash=""); ok, r = gate.check(RULE, G)
check("gate: refused without a corpus_hash", ok is False and any("corpus_hash" in x for x in r))
write_gate(G); ok, r = gate.check(RULE, G)
check("gate: passes with a fresh matching file", ok is True and r == [])

# ---------------------------------------------------------------- 17-19 decay
D = os.path.join(TMP, "decay"); os.makedirs(os.path.join(D, "bot"))
_util.save_json(os.path.join(D, "bot", "active_rule.json"), RULE)
_util.save_json(os.path.join(D, "bot", "strategy_state.json"), {"rule_id": RULE["id"], "status": "PAPER", "trades": 0, "avg_R": None, "backtest_avg_R": 0.02, "since": _util.iso()})
def add_trades(n, R):
    for i in range(n):
        _util.append_csv(os.path.join(D, "bot", "paper_trades.csv"), qbot.PAPER_TRADES_HEADER,
                         [_util.iso(), RULE["id"], "5m", 1800000000 + 300 * i, "Up", "taker", 210, 0.74, 0.74, 6.76, 100, "full", 0.09, "Up", R * 5, R, 0.02, "{}"])
add_trades(19, -0.01); r19 = decay.check(D)
add_trades(1, -0.01); r20 = decay.check(D)
check("decay: 19 losing trades -> still PAPER", r19["status"] == "PAPER" and r19["trades"] == 19 and not r19["changed"])
check("decay: 20 trades with avg_R < 0.5 x backtest -> DECAYING + log row", r20["status"] == "DECAYING" and r20["changed"] and r20["trades"] == 20
      and any(row["event"] == "DECAYING" for row in _util.read_csv(os.path.join(D, "bot", "strategy_log.csv"))))
add_trades(20, -0.01); r40 = decay.check(D)
check("decay: 40 trades still below -> RETIRED and active_rule.json removed", r40["status"] == "RETIRED" and r40["changed"]
      and not os.path.exists(os.path.join(D, "bot", "active_rule.json")) and _util.load_json(os.path.join(D, "bot", "strategy_state.json"))["status"] == "RETIRED")
if "qdesk.common.tg" in STUBBED:
    check("decay: one alert per transition with key decay-<rule_id>", [k for k, *_ in _stubs.SENT if k == f"decay-{RULE['id']}"].__len__() == 2)

# ---------------------------------------------------------------- 20-26 the bot (synthetic ws messages, fake clock)
class QuietLog(qbot.Log):
    def __call__(self, s):
        self.lines.append(s)

T_NOW = [0.0]; qbot.now = lambda: T_NOW[0]
START = 1800000000                                   # multiple of 300
MK = dict(start=START, market="5m", condition_id="0x" + "c" * 20, up="U", down="D", winner=None)

def mkbot(reasons=None, data=None):
    cfg = qbot.Cfg(data or os.path.join(TMP, "bot_run")); os.makedirs(os.path.join(cfg.data, "bot"), exist_ok=True)
    b = qbot.QBot(cfg, QuietLog(cfg), RULE, reasons)
    rc = b.add_round("5m", START, MK)
    T_NOW[0] = START + 200
    b.on_twap(START + 1, 100000.0)
    b.on_spot(START + 190, 100000.0); T_NOW[0] = START + 200.5; b.on_spot(START + 198, 100000.0)
    T_NOW[0] = START + 205.5; b.on_spot(START + 204, 100070.0)              # +7 bps vs open, received at 205.44
    T_NOW[0] = START + 210
    b.on_market_msg({"event_type": "book", "asset_id": "D", "bids": [{"price": "0.26", "size": "100"}], "asks": [{"price": "0.28", "size": "100"}], "timestamp": str(int(T_NOW[0] * 1000))}, T_NOW[0])
    b.on_market_msg({"event_type": "book", "asset_id": "U", "bids": [{"price": "0.72", "size": "100"}], "asks": [{"price": "0.74", "size": "50"}], "timestamp": str(int(T_NOW[0] * 1000))}, T_NOW[0])
    return b, rc

b, rc = mkbot()
check("bot: PAPER mode fires one taker leg at the ask (0.74, $5)", b.mode == "PAPER" and b.n_fired == 1 and len(rc.orders) == 1
      and b.book.orders[rc.orders[0]]["limit"] == 0.74 and abs(b.book.orders[rc.orders[0]]["size"] - round(5 / 0.74, 2)) < 1e-9
      and any(l.startswith("PAPER TAKER") for l in b.log.lines))
T_NOW[0] = START + 210.12; b.tick(T_NOW[0])
check("bot: the leg fills after 100 ms and is logged", b.book.status(rc.orders[0]) == "full" and any(l.startswith("FILL") for l in b.log.lines))
b.on_market_msg({"event_type": "price_change", "price_changes": [{"asset_id": "U", "price": "0.73", "size": "20", "side": "BUY", "best_bid": "0.73", "best_ask": "0.74"}], "timestamp": str(int(T_NOW[0] * 1000))}, T_NOW[0])
recs = [json.loads(x) for v in b.dec.buf.values() for x in v]
check("bot: second signal in the round killed by limit:max_trades_per_round (decision logged)",
      any(r["fired"] and r["killed_by"] == "limit:max_trades_per_round" and r["would_price"] == 0.74 for r in recs))
b.cfg.daily_stop = 4.9; b.settle_round(rc, "Down")
rows = _util.read_csv(os.path.join(b.cfg.data, "bot", "paper_trades.csv"))
with open(os.path.join(b.cfg.data, "bot", "paper_trades.csv"), encoding="utf-8") as f: header = f.readline().strip()
check("bot: paper_trades.csv has the exact section-4 header and a settled losing row (R ~ -1)",
      header == ",".join(qbot.PAPER_TRADES_HEADER) and len(rows) == 1 and rows[0]["winner"] == "Down" and rows[0]["fill_status"] == "full" and abs(float(rows[0]["R"]) + 1.0) < 0.05)
check("bot: paper daily stop is LOG ONLY (line written, process state keeps running)", b.daily_stopped and any(l.startswith("PAPER DAILY STOP") for l in b.log.lines))
S2 = START + 300; T_NOW[0] = S2 + 200; rc2 = b.add_round("5m", S2, dict(MK, start=S2))
b.on_twap(S2 + 1, 100000.0); b.on_spot(S2 + 190, 100000.0); T_NOW[0] = S2 + 200.5; b.on_spot(S2 + 198, 100000.0)
T_NOW[0] = S2 + 205.5; b.on_spot(S2 + 204, 100070.0); T_NOW[0] = S2 + 210
b.on_market_msg({"event_type": "book", "asset_id": "D", "bids": [{"price": "0.26", "size": "100"}], "asks": [{"price": "0.28", "size": "100"}], "timestamp": "1"}, T_NOW[0])
b.on_market_msg({"event_type": "book", "asset_id": "U", "bids": [{"price": "0.72", "size": "100"}], "asks": [{"price": "0.74", "size": "50"}], "timestamp": "1"}, T_NOW[0])
recs = [json.loads(x) for v in b.dec.buf.values() for x in v]
check("bot: after the daily stop a new signal is killed_by daily_stop", any(r["killed_by"] == "daily_stop" and r["start"] == S2 for r in recs))
paths = b.dec.flush()
check("bot: settled sidecar carries would_pnl for the fired decision", any(p.endswith(".settled.jsonl.gz") for p in paths)
      and any("would_pnl" in json.loads(l) for p in paths if p.endswith(".settled.jsonl.gz") for l in gzip.open(p, "rt")))

bo, rco = mkbot(reasons=["gate_pass.json missing"], data=os.path.join(TMP, "bot_obs"))
kl = [l for l in bo.log.lines if l.startswith("KILLED")]
recs = [json.loads(x) for v in bo.dec.buf.values() for x in v]
check("bot: OBSERVE mode journals the killed trade: line 'KILLED rule=<id> <market> <start> <side> t_el=<s> would_price=<p> killed_by=observe:<reason>'",
      bo.mode == "OBSERVE" and len(kl) == 1 and re.match(r"^KILLED rule=H20261030-03 5m 1800000000 Up t_el=\S+ would_price=0\.74 killed_by=observe:gate_pass\.json missing$", kl[0]) is not None
      and any(r["fired"] and str(r["killed_by"]).startswith("observe:") and r["would_price"] == 0.74 for r in recs) and bo.n_fired == 0)

os.environ["DRY_RUN"] = "false"
try:
    qbot.dry_run_guard(); guard_ok = False
except SystemExit as e:
    guard_ok = "paper only" in str(e)
os.environ["DRY_RUN"] = "true"
check("DRY_RUN=false -> SystemExit('qdesk v1 is paper only')", guard_ok and qbot.dry_run_guard() is True)

dl = qbot.DecisionLog(os.path.join(TMP, "rot")); t1 = 1800000000.0; t2 = t1 + 86400
dl.write({"t": t1, "fired": False}, t1); dl.write({"t": t2, "fired": False}, t2); dl.write({"t": t2 + 1, "fired": True}, t2 + 1)
ps = sorted(dl.flush()); names = [os.path.basename(p) for p in ps]
check("decisions file rotates by UTC date (decisions-YYYYMMDD.jsonl.gz)", names == [f"decisions-{_util.day_str(t1)}.jsonl.gz", f"decisions-{_util.day_str(t2)}.jsonl.gz"]
      and len(list(gzip.open(ps[1], "rt"))) == 2)

# ---------------------------------------------------------------- 27-29 builder
B = os.path.join(TMP, "build"); [os.makedirs(os.path.join(B, d)) for d in ("hyps", "bot", "features", "state")]
DATE = "20261030"; DAY = "20261020"
with gzip.open(os.path.join(B, "features", f"day_{DAY}.csv.gz"), "wt") as f: f.write("market,start\n5m,1\n")
_util.save_json(os.path.join(B, "hyps", f"hyps_{DATE}.json"), {"date": DATE, "model": "x", "hypotheses": [RULE], "rejected": []})
_util.save_json(os.path.join(B, "hyps", f"skeptic_{DATE}.json"), {"ok": True, "findings": [], "holdout_reuse": False, "weakened_tests": False, "lookahead_fields": []})
_util.save_json(os.path.join(B, "hyps", f"results_{DATE}.json"), {"engine": "rule_engine_1s", "rules": {RULE["id"]: {"fit": {"net_per_dollar": 0.015, "ci95": [0, 0.03], "n": 100}, "test": {"net_per_dollar": 0.012, "ci95": [0.001, 0.02], "n": 45}}}})
_util.save_json(os.path.join(B, "hyps", f"preregistration_{DATE}.json"), {"date": DATE, "hypotheses": [{"id": RULE["id"], "rule_hash": dsl.canonical_hash(RULE)}], "fit_days": [], "test_days": [DAY], "windows": [], "features_hash": "feat123", "code_hash": "c"})
sc = {"date": DATE, "rules": [{"id": RULE["id"], "scores": {"oos_tstat": 4, "max_dd": 4, "n_trades": 3, "edge_after_costs": 4, "stability": 4}, "total": 19, "pass": True, "reasons": [], "why_this_could_fail": "x"}],
      "passed": [RULE["id"]], "verdict": "NOTHING_PASSED", "holdout_used": True, "n_tried_cum_on_holdout": 1}
_util.save_json(os.path.join(B, "hyps", f"scorecard_{DATE}.json"), sc)
out0 = builder.run(DATE, B)
check("builder: NOTHING_PASSED writes no gate/rule files", not out0["activated"] and not os.path.exists(os.path.join(B, "bot", "gate_pass.json")) and not os.path.exists(os.path.join(B, "bot", "active_rule.json")))
sc["verdict"] = f"CANDIDATE:{RULE['id']}"; _util.save_json(os.path.join(B, "hyps", f"scorecard_{DATE}.json"), sc)
outd = builder.run(DATE, B, dry_run=True)
check("builder: --dry-run decides ACTIVATE but writes nothing", outd["activated"] and not os.path.exists(os.path.join(B, "bot", "gate_pass.json")))
out1 = builder.run(DATE, B)
g = _util.load_json(os.path.join(B, "bot", "gate_pass.json")) or {}
ok, r = gate.check(_util.load_json(os.path.join(B, "bot", "active_rule.json")), B)
check("builder: CANDIDATE + skeptic ok -> gate_pass.json (hashes, paper_only) + active_rule.json + state PAPER + RESTART-REQUESTED, and gate.check passes",
      out1["activated"] and g.get("paper_only") is True and g.get("gate_pass") is True and g.get("features_hash") == "feat123" and g.get("corpus_hash")
      and g.get("code_hash") == hashing.code_hash(hashing.CODE_FILES_BOT) and ok and r == []
      and _util.load_json(os.path.join(B, "bot", "strategy_state.json"))["status"] == "PAPER" and abs(_util.load_json(os.path.join(B, "bot", "strategy_state.json"))["backtest_avg_R"] - 0.012) < 1e-9
      and os.path.exists(os.path.join(B, "bot", "RESTART-REQUESTED")))
sc["rules"][0]["total"] = 21; sc["rules"][0]["id"] = "H-NEW"; sc["verdict"] = "CANDIDATE:H-NEW"; _util.save_json(os.path.join(B, "hyps", f"scorecard_{DATE}.json"), sc)
r2 = dict(RULE, id="H-NEW"); _util.save_json(os.path.join(B, "hyps", f"hyps_{DATE}.json"), {"date": DATE, "model": "x", "hypotheses": [RULE, r2], "rejected": []})
out2 = builder.run(DATE, B)
check("builder: a better candidate is HELD while the incumbent has < 7 paper days", not out2["activated"] and _util.load_json(os.path.join(B, "bot", "active_rule.json"))["id"] == RULE["id"])
st = _util.load_json(os.path.join(B, "bot", "strategy_state.json")); st["since"] = _util.iso(time.time() - 8 * 86400); _util.save_json(os.path.join(B, "bot", "strategy_state.json"), st)
out3 = builder.run(DATE, B)
check("builder: after 7 paper days a higher total replaces the incumbent", out3["activated"] and _util.load_json(os.path.join(B, "bot", "active_rule.json"))["id"] == "H-NEW")

print("\nALL PASS" if fails == 0 else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
