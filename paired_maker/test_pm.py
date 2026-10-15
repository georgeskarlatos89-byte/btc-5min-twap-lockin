#!/usr/bin/env python3
"""test_pm.py - unit checks for the paired maker's quoting rules (no network, no keys)."""
import asyncio, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
os.environ["DRY_RUN"] = "true"
import pm_bot

fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1


def mk(mode="model"):
    os.environ["PM_MODE"] = mode; os.environ["PM_SIZE"] = "5"; os.environ["PM_CAP_SHARES"] = "10"; os.environ["PM_INV"] = "10"
    cfg = pm_bot.Cfg(); log = lambda s: None; log = types.SimpleNamespace(__call__=lambda self, s: None)
    b = pm_bot.Bot(cfg, lambda s: None)
    import time as _t
    st = int(_t.time()) - 120                       # 120 s into a round (inside the 60-240 s window)
    b.round = dict(start=st, condition_id="c", up="U", down="D")
    b.sides = {"Up": pm_bot.Side("Up", "U"), "Down": pm_bot.Side("Down", "D")}
    b.bb = {"U": 0.50, "D": 0.48}; b.ba = {"U": 0.52, "D": 0.50}
    b.opens[st] = 100000.0; b.spot = 100000.0
    return b


b = mk()
up = b.sides["Up"]; dn = b.sides["Down"]
check("outside the window -> no quote (t=30)", b.want_price(up, 30) is None)
check("outside the window -> no quote (t=250)", b.want_price(up, 250) is None)
w = b.want_price(up, 120)
check("flat spot: fair 0.50 - 5c edge = 0.45", w == 0.45)
b.spot = 100000.0 * (1 + 0.0010)      # +10 bp with 180 s left -> fair well above 0.5
w2 = b.want_price(up, 120)
check("spot up -> Up bid rises", w2 is not None and w2 > 0.45)
check("never crosses: want < best ask", w2 < b.ba["U"])
b.ba["U"] = 0.46
check("ask at 0.46 -> capped one tick inside", b.want_price(up, 120) == 0.45)
b.ba["U"] = 0.99; b.spot = 100000.0
# pair cap: holding Down at avg 0.60 -> Up may not exceed 0.97 - 0.60 = 0.37
dn.filled = 5; dn.cost = 3.0
check("pair cap limits the lagging leg", b.want_price(up, 120) == 0.37)
dn.filled = 0; dn.cost = 0.0
# inventory: Up 10 ahead -> stop
up.filled = 10; check("cap shares reached -> no quote", b.want_price(up, 120) is None)
up.filled = 0
# join mode
bj = mk("join"); check("join mode quotes the best bid", bj.want_price(bj.sides["Up"], 120) == 0.50)
# decide() sets a want and wakes the worker; a second identical decision does not
b.decide(1.0); check("decide sets want", up.want == 0.45 and up.ev.is_set())
up.ev.clear(); up.working_price = 0.45; up.want = 0.45; b.decide(2.0); check("unchanged want -> no event", not up.ev.is_set())
b.bb["U"] = 0.30; b.ba["U"] = 0.31; b.decide(3.0); check("book change -> new want one tick inside ask", up.want == 0.30 and up.ev.is_set())


# gate lock
import json, tempfile, time
tmp = tempfile.mkdtemp(); gf = os.path.join(tmp, "REPLAY-RESULTS.json")
os.environ["DRY_RUN"] = "false"; os.environ["GATE_PASS_FILE"] = gf; os.environ["PM_GATE_LAT"] = "0.1"; os.environ["PM_MODE"] = "model"
cfg = pm_bot.Cfg(); msgs = []
check("LIVE refused without gate file", pm_bot.gate_check(cfg, msgs.append) is False)
json.dump({"summary": {"model lat0.1": {"gate": False, "cfg": {"mode": "model", "lat": 0.1}}}}, open(gf, "w"))
check("LIVE refused when the variant failed", pm_bot.gate_check(cfg, msgs.append) is False)
json.dump({"summary": {"model lat0.1": {"gate": True, "cfg": {"mode": "model", "lat": 0.1}}}}, open(gf, "w"))
check("LIVE allowed with a passing fresh gate", pm_bot.gate_check(cfg, msgs.append) is True)
os.utime(gf, (time.time() - 8 * 86400, time.time() - 8 * 86400))
check("LIVE refused when the gate is stale", pm_bot.gate_check(cfg, msgs.append) is False)
os.environ["DRY_RUN"] = "true"

print("\nALL PASS" if fails == 0 else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
