#!/usr/bin/env python3
"""test_v2.py - unit tests for the v2 fixes (run: python test_v2.py). No network."""
import json, math, os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_strategy import Book, Resting, decide_quotes, detect_fills, RoundAccount, round_tick, QUOTE_SIZE, MAX_NET_SHARES, VariantCfg, code_hash
from fair_model import FairModel

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    fails += 0 if cond else 1


rng = random.Random(1)

# --- E3: an unchanged book must never fill -------------------------------------
prev = Book([(0.40, 100)], [(0.44, 100)]); new = Book([(0.40, 100)], [(0.44, 100)])
r = Resting(); r.update((0.42, 0.46)); r.update((0.42, 0.46))     # rested >= 1 tick
n = sum(len(detect_fills(prev, new, r, rng)) for _ in range(1000))
check("E3 unchanged book, our bid above the touch -> 0 fills", n == 0, f"{n} fills")
r = Resting(); r.update((0.38, 0.46)); r.update((0.38, 0.46))
n = sum(len(detect_fills(prev, new, r, rng)) for _ in range(1000))
check("E3 unchanged book, normal resting -> 0 fills", n == 0, f"{n} fills")

# --- real sweep through our level fills ~50% and is consumed (E5) ---------------
hits = 0
for i in range(1000):
    r = Resting(); r.update((0.38, 0.46)); r.update((0.38, 0.46))
    f = detect_fills(prev, Book([(0.37, 100)], [(0.44, 100)]), r, rng)
    hits += len(f)
    if f:
        assert r.bid is None, "filled bid must be consumed"
check("sweep 0.40->0.37 through our 0.38 fills about 50%", 400 < hits < 600, f"{hits}/1000")
r = Resting(); r.update((0.38, 0.46)); r.update((0.38, 0.46))
f1 = detect_fills(prev, Book([(0.37, 100)], [(0.44, 100)]), r, random.Random(3))
f2 = detect_fills(Book([(0.37, 100)], [(0.44, 100)]), Book([(0.36, 100)], [(0.44, 100)]), r, random.Random(3))
check("E5 a consumed quote cannot fill again", not (f1 and f2), f"first={len(f1)} second={len(f2)}")

# --- a quote placed this tick cannot fill this tick -----------------------------
r = Resting(); r.update((0.38, 0.46))
n = sum(len(detect_fills(prev, Book([(0.37, 100)], [(0.44, 100)]), r, rng)) for _ in range(200))
check("fresh quote (age 0) never fills on its first tick", n == 0, f"{n}")

# --- E4: post-only ---------------------------------------------------------------
check("round_tick is half-up and float-safe", (round_tick(0.455), round_tick(0.485), round_tick(0.4871)) == (0.46, 0.49, 0.49), f"{round_tick(0.455)} {round_tick(0.485)} {round_tick(0.4871)}")
q = decide_quotes(0.47, 100, "5m", 0, Book([(0.40, 1)], [(0.44, 1)]))
check("E4 bid that would cross the ask (0.46 vs ask 0.44) is dropped", q is not None and q[0] is None and q[1] == 0.49, f"{q}")
q = decide_quotes(0.47, 100, "5m", 0, Book([(0.49, 1)], [(0.50, 1)]))
check("E4 ask at/below the best bid is dropped", q is not None and q[1] is None and q[0] == 0.46, f"{q}")
q = decide_quotes(0.47, 100, "5m", 0, Book([(0.45, 1)], [(0.50, 1)]))
check("normal case keeps both sides", q == (0.46, 0.49), f"{q}")

# --- E11: venue tick + shared size -----------------------------------------------
q = decide_quotes(0.4871, 100, "5m", 0, None)
check("E11 prices are on the 0.01 tick", q == (0.47, 0.50), f"{q}")
check("E11 sim size equals the bot size from .env (default 20)", QUOTE_SIZE == 20.0 and MAX_NET_SHARES == 40.0, f"{QUOTE_SIZE}/{MAX_NET_SHARES}")
check("no quote in the last 20 s", decide_quotes(0.5, 19, "5m", 0, None) is None)
check("no quote outside the fair band", decide_quotes(0.95, 100, "5m", 0, None) is None)
check("inventory cap drops the bid when long-full", decide_quotes(0.5, 100, "5m", 40, None) == (None, 0.52))

# --- accounting ---------------------------------------------------------------------
a = RoundAccount(); a.add(1, "BUY", 0.46, 20, 0.47); a.add(2, "SELL", 0.49, 20, 0.47)
check("settle Up: +0.54*20 - 0.51*20 = +0.60", abs(a.settle(True) - 0.60) < 1e-9, f"{a.settle(True):.4f}")
check("settle Down: -0.46*20 + 0.49*20 = +0.60", abs(a.settle(False) - 0.60) < 1e-9, f"{a.settle(False):.4f}")

# --- E1/E2: fair model on a synthetic random walk ------------------------------------
m = FairModel()
rw = random.Random(5); price = 80000.0; t0 = 1_000_000
for s in range(1800):
    price += rw.gauss(0, 8.0)          # 8 USD per second ~ 1 bps/s
    m.on_spot(t0 + s, price); m.on_twap(t0 + s, price)
sig = m.sigma1(t0 + 1799)
check("E1 sigma recovered from 10-s differences is ~8 USD/s", 6.0 < sig < 10.0, f"{sig:.2f}")
o = price
p_flat = m.p_up(o, 200, t0 + 1799)
check("E1 at the open reference with 200 s left p ~ 0.5", 0.45 < p_flat < 0.55, f"{p_flat:.3f}")
m.on_spot(t0 + 1800, o - 6.0); p6 = m.p_up(o, 200, t0 + 1800)
check("E1 6 USD below open, 200 s left -> p stays near 0.5 (v1 said 0.017)", 0.40 < p6 < 0.50, f"{p6:.3f}")
m.on_spot(t0 + 1801, o - 300.0); p300 = m.p_up(o, 200, t0 + 1801)
check("300 USD below open, 200 s left -> p small but not zero", 0.0 < p300 < 0.05, f"{p300:.4f}")
m.on_twap(t0 + 1802, o + 0.01)
check("E2 at the end: TWAP60 >= open -> Up (ties Up)", m.p_up(o, 0) == 1.0)
m.on_twap(t0 + 1803, o - 0.01)
check("E2 at the end: TWAP60 < open -> Down", m.p_up(o, 0) == 0.0)
# r < 60: the known part of the window pins the answer
m2 = FairModel()
for s in range(1800):
    m2.on_spot(t0 + s, 80000.0 + (s % 7) * 0.1); m2.on_twap(t0 + s, 80000.0)
m2.on_spot(t0 + 1800, 80000.0 + 40.0)
p10 = m2.p_up(80000.0, 10, t0 + 1800)
check("r<60: 50 s of known prices at open + spot 40 USD above with 10 s left -> near-certain Up", p10 > 0.9, f"{p10:.3f}")
m3 = FairModel()
for s in range(1800):
    m3.on_spot(t0 + s, 79990.0); m3.on_twap(t0 + s, 80000.0)      # 50 s of the window already 10 USD BELOW open
m3.on_spot(t0 + 1800, 80000.0 + 40.0)
p10b = m3.p_up(80000.0, 10, t0 + 1800)
check("r<60: known part pins it: spot +40 cannot outweigh 50 s at -10 -> p < 0.5", p10b < 0.5, f"{p10b:.3f}")

# --- E6: the simulator CSV carries what chain_verify needs -----------------------------
import maker_sim
check("E6 sim CSV has condition_id and outcome columns", "condition_id" in maker_sim.CSV_COLS and "outcome" in maker_sim.CSV_COLS)
import chain_verify
check("E6 chain_verify accepts a `settled` column", chain_verify.label_of({"settled": "Up"}) == "up" and chain_verify.label_of({"outcome": "down"}) == "down")

# --- E7: .env is loaded by the bot ---------------------------------------------------
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "maker_bot.py")).read()
check("E7 maker_bot loads .env", "load_dotenv(" in src)
check("E8 fills reconciled with TradeParams(maker_address=...)", "TradeParams(maker_address" in src and "get_trades(maker_only" not in src)
check("E9 realized P&L carried across rounds", "self.realized +=" in src)
check("E10 recorder uses events?slug= and never caches a failure",
      "events?slug=" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "book_recorder.py")).read()
      and "token_fail_ts" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "book_recorder.py")).read())

# --- VARIANT-EXPERIMENT-PLAN (2026-10-15): variants + the gate lock -------------------
bk = Book([(0.45, 1000)], [(0.50, 1000)])
check("V0 VariantCfg() is the baseline and changes nothing", VariantCfg().is_baseline() and decide_quotes(0.47, 100, "5m", 0, bk, VariantCfg()) == decide_quotes(0.47, 100, "5m", 0, bk))
check("V1 one-sided: fair 0.52 vs mid 0.475 (+4.5c >= 3c) -> bid only, capped one tick under the 0.50 ask", decide_quotes(0.52, 100, "5m", 0, bk, VariantCfg(one_sided_theta=0.03)) == (0.49, None), f"{decide_quotes(0.52, 100, '5m', 0, bk, VariantCfg(one_sided_theta=0.03))}")
check("V1 one-sided: fair 0.43 vs mid 0.475 (-4.5c) -> ask only, capped one tick over the 0.45 bid", decide_quotes(0.43, 100, "5m", 0, bk, VariantCfg(one_sided_theta=0.03)) == (None, 0.46), f"{decide_quotes(0.43, 100, '5m', 0, bk, VariantCfg(one_sided_theta=0.03))}")
check("V1 one-sided: inside theta -> no quote", decide_quotes(0.48, 100, "5m", 0, bk, VariantCfg(one_sided_theta=0.03)) is None)
quiet = Book([(0.45, 1000)], [(0.50, 1100)]); heavy_bid = Book([(0.45, 3000)], [(0.50, 1000)])
check("V2 quiet book (|imb|<0.2) -> both sides", decide_quotes(0.47, 100, "5m", 0, quiet, VariantCfg(imb_q=0.2, support_k=2.0)) == (0.46, 0.49))
check("V2 bid-heavy book (3:1): bid has support, ask does not", decide_quotes(0.47, 100, "5m", 0, heavy_bid, VariantCfg(imb_q=0.2, support_k=2.0)) == (0.46, None))
check("V2 no sizes -> no quote", decide_quotes(0.47, 100, "5m", 0, Book([(0.45, 0)], [(0.50, 0)]), VariantCfg(imb_q=0.2, support_k=2.0)) is None)
check("V3 half-spread 0.03 widens both sides", decide_quotes(0.50, 100, "5m", 0, bk, VariantCfg(half_spread=0.03)) == (0.47, 0.53))
check("V4 window 180-240: t_el 100 -> none, t_el 200 -> quotes", decide_quotes(0.47, 200, "5m", 0, bk, VariantCfg(window=(180, 240))) is None and decide_quotes(0.47, 100, "5m", 0, bk, VariantCfg(window=(180, 240))) == (0.46, 0.49))
check("V5 open fade 15 s: t_el 10 -> quotes, t_el 16 -> none", decide_quotes(0.47, 290, "5m", 0, bk, VariantCfg(open_fade_secs=15)) == (0.46, 0.49) and decide_quotes(0.47, 284, "5m", 0, bk, VariantCfg(open_fade_secs=15)) is None)
check("V6 no re-quote: BUY filled -> bid off, ask stays", decide_quotes(0.47, 100, "5m", 0, bk, VariantCfg(no_requote_after_fill=True), {"BUY"}) == (None, 0.49))
c = VariantCfg.from_dict(VariantCfg(id="V4", window=(180, 240)).as_dict())
check("VariantCfg round-trips through JSON", c.id == "V4" and c.window == (180.0, 240.0))
a = RoundAccount(); a.add(0, "BUY", 0.51, 20, 0.5); a.add(1, "SELL", 0.60, 20, 0.5)
check("cost: BUY 0.51 pays 0.51/sh, SELL 0.60 (= Down at 0.40) pays 0.40/sh", abs(a.cost - (0.51 * 20 + 0.40 * 20)) < 1e-9, f"{a.cost}")
check("fills_won: settled Up -> only the BUY won", a.fills_won(True) == 1 and a.fills_won(False) == 1)
h1 = code_hash(); check("code_hash is stable and 16 hex", h1 == code_hash() and len(h1) == 16 and all(ch in "0123456789abcdef" for ch in h1), h1)
src_bot = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "maker_bot.py"), encoding="utf-8").read()
check("R8 bot refuses LIVE without a fresh matching gate_pass.json", "def gate_lock_check" in src_bot and "LIVE REFUSED by the gate lock" in src_bot and "code hash mismatch" in src_bot)
# the lock, exercised: a stale / mismatched pass file must be refused, a fresh matching one accepted
import importlib, time as _time
os.environ["DRY_RUN"] = "false"; os.environ["OBM_NO_TELEGRAM"] = "1"
tmpd = tempfile.mkdtemp(); gp = os.path.join(tmpd, "gate_pass.json"); os.environ["GATE_PASS_FILE"] = gp
import maker_bot
maker_bot.DRY_RUN = False; maker_bot.GATE_PASS_FILE = gp
check("R8 lock: no file -> refused", maker_bot.gate_lock_check()[0] is False)
json.dump(dict(gate_pass=True, generated_ts=_time.time() - 8 * 86400, corpus_hash="abc", code_hash=code_hash()), open(gp, "w"))
check("R8 lock: 8 days old -> refused", maker_bot.gate_lock_check()[0] is False)
json.dump(dict(gate_pass=True, generated_ts=_time.time(), corpus_hash="abc", code_hash="deadbeefdeadbeef"), open(gp, "w"))
check("R8 lock: code hash mismatch -> refused", maker_bot.gate_lock_check()[0] is False)
json.dump(dict(gate_pass=True, generated_ts=_time.time(), corpus_hash="abc", code_hash=code_hash(), variant="Vx", generated="now"), open(gp, "w"))
check("R8 lock: fresh + matching -> accepted", maker_bot.gate_lock_check()[0] is True)
os.environ["DRY_RUN"] = "true"; maker_bot.DRY_RUN = True

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
