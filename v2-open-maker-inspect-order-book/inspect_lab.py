#!/usr/bin/env python3
"""inspect_lab.py - v2-open-maker-inspect-order-book: the notebook questions measured on the recorded order book.

  zcat events_YYYYMMDD.csv.gz | mawk (filter, see inspect_run.sh) | python3 inspect_lab.py tokens.json --out day.json

Queue rules are the same as pm_replay.py (our order joins a price level BEHIND the size already resting there; a taker
trade at our level eats that queue first; a trade through our price fills us first; a shrinking level shrinks the queue).
Order latency --lat (0.1 s = measured from twapvm). Our hypothetical orders do not change the recorded book.

Experiments (numbers refer to the plan file "BTC 5-minute - ORDER BOOK READER plan ... 2026-10-26.md"):
  timed  2a/2b/2c : one 20-share bid per (side, post time e0, o cents below the best bid), held to the end of the round.
                    Records fill, queue ahead at landing, time to fill, cost and settlement value, price parity.
  crowd  2c       : resting size at bb, bb-1c, bb-2c, bb-3c and at ba, ba+1c at every post time.
  flow   2d       : the continuous "join the best bid" paired tactic (window 60-240 s, pair cap 0.97, inv 40, size 20,
                    v2 cancel model) as base / sweep-pull / imbalance-gate / both.
  exits  2e       : for every fill of the base tactic: A sell at once at the best bid (taker fee), B pair at once by
                    buying the other side at its ask if the pair costs <= 0.97 (taker fee) else hold, C hold to settlement.
  rebate 2f       : makers pay no fee; the crypto taker fee is 0.07*p*(1-p) per share and 20% of it is redistributed daily
                    to makers (docs.polymarket.com fees page, read 2026-10-26). Credit = 0.2*0.07*p*(1-p) per maker-filled
                    share (0.35% of notional at p=0.5; gabigol's measured rebate was 0.37% of his maker turnover).
Read-only; no keys.
"""
import argparse, collections, json, math, sys

ap = argparse.ArgumentParser()
ap.add_argument("tokens"); ap.add_argument("--lat", type=float, default=0.1); ap.add_argument("--size", type=float, default=20.0)
ap.add_argument("--cap-shares", type=float, default=200.0); ap.add_argument("--sweep-sh", type=float, default=30.0)
ap.add_argument("--quiet", type=float, default=5.0); ap.add_argument("--out", required=True)
a = ap.parse_args()
tokens = json.load(open(a.tokens))
LAT, SIZE, CAP = a.lat, a.size, a.cap_shares
E0 = [30, 60, 90, 120, 150, 180, 210, 240, 270, 293]      # seconds elapsed; 293 = 4:53 (7 s left)
OFFS = [0, 1, 2]
QS, QE = 60, 240
FEE_RATE, REBATE_SHARE = 0.07, 0.20
fee = lambda p: FEE_RATE * p * (1 - p)
rebate = lambda p: REBATE_SHARE * fee(p)
FLOW = ["base", "sweep", "imb", "both"]

levels_bid = collections.defaultdict(dict); levels_ask = collections.defaultdict(dict); bb = {}; ba = {}
tape = collections.defaultdict(collections.deque)      # asset -> (t, size) of taker volume hitting THIS asset's bids
bb_hist = collections.defaultdict(collections.deque)   # asset -> (t, bb)
quiet_until = collections.defaultdict(float)
n_rows = 0


def good(p):
    return p is not None and p > 0.005


# ---------------- timed orders (2a/2b/2c) ----------------
class Ord:
    __slots__ = ("price", "qa", "rem", "filled", "cost", "first", "land_t", "live", "meta")

    def __init__(s, price, land_t, meta):
        s.price = price; s.qa = 0.0; s.rem = SIZE; s.filled = 0.0; s.cost = 0.0; s.first = None; s.land_t = land_t; s.live = False; s.meta = meta


timed = collections.defaultdict(list); posted = collections.defaultdict(set); crowd = []


def land_timed(asset, t):
    for o in timed[asset]:
        if not o.live and t >= o.land_t:
            o.live = True; b = bb.get(asset)
            o.qa = float(levels_bid[asset].get(o.price, 0.0)) if (good(b) and o.price <= b + 1e-9) else 0.0
            o.meta["qa0"] = o.qa


def hit_ord(o, price, size, t):
    if not o.live or o.rem <= 0: return
    if price < o.price - 1e-9: f = min(o.rem, size)
    elif abs(price - o.price) < 1e-9:
        if o.qa >= size: o.qa -= size; return
        f = min(o.rem, size - o.qa); o.qa = 0.0
    else: return
    if f <= 0: return
    o.rem -= f; o.filled += f; o.cost += f * o.price
    if o.first is None: o.first = t


def maybe_post(asset, tk, t, e):
    b = bb.get(asset)
    if not good(b): return
    for e0 in E0:
        if e < e0 or e0 in posted[asset]: continue
        posted[asset].add(e0)
        if e > e0 + 2.0: continue                       # feed gap: no event near the wanted second, skip honestly
        cents = int(round(b * 100)); k = ba.get(asset)
        crowd.append(dict(start=tk["start"], outcome=tk["outcome"], e0=e0, bb=b, ba=k,
                          odd=cents % 2, r5=int(cents % 5 == 0),
                          sb=[float(levels_bid[asset].get(round(b - i / 100, 2), 0.0)) for i in range(4)],
                          sa=([float(levels_ask[asset].get(round(k + i / 100, 2), 0.0)) for i in range(2)] if good(k) else None)))
        for o in OFFS:
            p = round(b - o / 100, 2)
            if p < 0.02: continue
            pc = int(round(p * 100))
            timed[asset].append(Ord(p, t + LAT, dict(start=tk["start"], outcome=tk["outcome"], e0=e0, o=o, bb0=b, price=p,
                                                      odd=pc % 2, r5=int(pc % 5 == 0), post_t=t)))


# ---------------- flow tactic (2d) = pm_replay join, v2 cancel model, + filters ----------------
class Side:
    __slots__ = ("price", "qa", "rem", "filled", "cost", "fills", "pend_price", "pend_t", "cancel_t")

    def __init__(s):
        s.price = None; s.qa = 0.0; s.rem = 0.0; s.filled = 0.0; s.cost = 0.0; s.fills = []
        s.pend_price = None; s.pend_t = None; s.cancel_t = None


state = {v: collections.defaultdict(Side) for v in FLOW}
exit_q = collections.deque(); exits = []


def hit_side(v, asset, price, size, t):
    s = state[v].get(asset)
    if s is None or s.price is None or s.rem <= 0: return
    if price < s.price - 1e-9: f = min(s.rem, size)
    elif abs(price - s.price) < 1e-9:
        if s.qa >= size: s.qa -= size; return
        f = min(s.rem, size - s.qa); s.qa = 0.0
    else: return
    if f <= 0: return
    s.rem -= f; s.filled += f; s.cost += f * s.price; s.fills.append((t, s.price, f))
    if v == "base": exit_q.append((t + LAT, asset, s.price, f))


def land(s, asset, t):
    if s.cancel_t is not None and t >= s.cancel_t:
        s.price = None; s.rem = 0.0; s.cancel_t = None
    if s.pend_t is None or t < s.pend_t: return
    want = s.pend_price; s.pend_price = None; s.pend_t = None
    if want is None: return
    if s.cancel_t is not None:
        s.price = None; s.rem = 0.0; s.cancel_t = None
    s.price = want; s.rem = min(SIZE, CAP - s.filled)
    b = bb.get(asset)
    s.qa = float(levels_bid[asset].get(want, 0.0)) if (good(b) and want <= b + 1e-9) else 0.0


def send(s, want, t):
    if s.price is not None and s.rem > 0 and s.cancel_t is None: s.cancel_t = t + LAT
    s.pend_price = want; s.pend_t = t + LAT


def sweep_now(asset, t):
    tp = tape[asset]
    while tp and tp[0][0] < t - 1.0: tp.popleft()
    vol = sum(x[1] for x in tp)
    h = bb_hist[asset]
    while len(h) > 1 and h[1][0] <= t - 1.0: h.popleft()
    b = bb.get(asset); b1 = h[0][1] if h else None
    return vol >= a.sweep_sh or (good(b) and good(b1) and b <= b1 - 0.02 + 1e-9)


def imbalance_ok(asset):
    b = bb.get(asset); k = ba.get(asset)
    if not good(b) or not good(k): return False
    sb = sum(float(levels_bid[asset].get(round(b - i / 100, 2), 0.0)) for i in range(3))
    sa = sum(float(levels_ask[asset].get(round(k + i / 100, 2), 0.0)) for i in range(3))
    return sb >= sa


def requote(v, asset, tk, t, e):
    s = state[v][asset]
    land(s, asset, t)
    live_price = s.price if (s.price is not None and s.rem > 0 and s.cancel_t is None) else None
    target = s.pend_price if s.pend_t is not None else live_price
    b = bb.get(asset); want = None
    if not (e < QS or e > QE or not good(b) or s.filled >= CAP - 1e-9):
        o = state[v][tk["other"]]; want = b
        if v in ("sweep", "both"):
            if sweep_now(asset, t): quiet_until[(v, asset)] = t + a.quiet
            if t < quiet_until[(v, asset)]: want = None
        if want is not None and v in ("imb", "both") and not imbalance_ok(asset): want = None
        if want is not None and o.filled > s.filled + 1e-9:
            want = min(want, math.floor((0.97 - o.cost / o.filled) * 100 + 1e-9) / 100.0)
        if want is not None and s.filled - o.filled >= 40 - 1e-9: want = None
        if want is not None:
            want = round(want, 2)
            if not (0.02 <= want <= 0.97): want = None
    if want is None:
        if target is not None: send(s, None, t)
        return
    if target is None or abs(target - want) >= 0.0099: send(s, want, t)


def resolve_exits(t):
    while exit_q and exit_q[0][0] <= t:
        due, ast, p, f = exit_q.popleft()
        tkk = tokens[ast]; b = bb.get(ast); oa = ba.get(tkk["other"])
        A = (b - fee(b) - p) * f if good(b) else None
        B = (1 - p - oa - fee(oa)) * f if (good(oa) and p + oa <= 0.97 + 1e-9) else None
        exits.append(dict(start=tkk["start"], outcome=tkk["outcome"], price=p, f=f, A=A, B=B))


# ---------------- main loop ----------------
for line in sys.stdin:
    r = line.rstrip("\n").split(",")
    if len(r) < 9: continue
    k = r[1]
    if k in ("spot_tick", "twap_tick"): continue
    asset = r[3]; tk = tokens.get(asset)
    if tk is None: continue
    n_rows += 1
    t = int(r[0]) / 1000.0; e = t - tk["start"]
    if k == "price_change":
        p = round(float(r[5]), 2); sz = float(r[6])
        L = levels_bid[asset] if r[4] == "BUY" else levels_ask[asset]
        if sz <= 0: L.pop(p, None)
        else: L[p] = sz
        if r[4] == "BUY":
            for o in timed[asset]:
                if o.live and abs(o.price - p) < 1e-9 and sz < o.qa: o.qa = sz
            for v in FLOW:
                s = state[v].get(asset)
                if s is not None and s.price is not None and abs(s.price - p) < 1e-9 and sz < s.qa: s.qa = sz
        if r[7] and good(float(r[7])): bb[asset] = float(r[7]); bb_hist[asset].append((t, bb[asset]))
        if r[8] and good(float(r[8])): ba[asset] = float(r[8])
    elif k == "bba":
        if r[7] and good(float(r[7])): bb[asset] = float(r[7]); bb_hist[asset].append((t, bb[asset]))
        if r[8] and good(float(r[8])): ba[asset] = float(r[8])
    elif k == "trade":
        p = float(r[5]); sz = float(r[6])
        if r[4] == "SELL": tgt, hp = asset, round(p, 2)
        else: tgt, hp = tk["other"], round(1 - p, 2)
        tape[tgt].append((t, sz))
        if t - tokens[tgt]["start"] <= 300:
            land_timed(tgt, t)
            for o in timed[tgt]: hit_ord(o, hp, sz, t)
        for v in FLOW:
            s = state[v].get(tgt)
            if s is not None: land(s, tgt, t)
            hit_side(v, tgt, hp, sz, t)
    if e <= 300:
        land_timed(asset, t); maybe_post(asset, tk, t, e)
    for v in FLOW: requote(v, asset, tk, t, e)
    resolve_exits(t)

# ---------------- output ----------------
winner = {tk["start"]: tk["winner"] for tk in tokens.values()}
res = dict(rows=n_rows, lat=LAT, size=SIZE, fee_rate=FEE_RATE, rebate_share=REBATE_SHARE, timed=[], crowd=crowd, flow={}, exits=[])
for asset, lst in timed.items():
    tk = tokens[asset]; w = winner.get(tk["start"])
    if not w: continue
    for o in lst:
        m = o.meta; val = o.filled * (1.0 if w == tk["outcome"] else 0.0)
        res["timed"].append(dict(m, winner=w, filled=o.filled, cost=o.cost, payout=val, profit=val - o.cost,
                                 rebate=o.filled * rebate(o.price), tfill=((o.first - m["post_t"]) if o.first else None)))
for v in FLOW:
    R = collections.defaultdict(dict)
    for asset, s in state[v].items():
        tk = tokens[asset]; R[tk["start"]][tk["outcome"]] = s
    rows = []
    for st in sorted(R):
        up = R[st].get("Up"); dn = R[st].get("Down"); w = winner.get(st)
        us = up.filled if up else 0.0; ds = dn.filled if dn else 0.0; uc = up.cost if up else 0.0; dc = dn.cost if dn else 0.0
        if us + ds <= 0 or not w: continue
        pay = us if w == "Up" else ds
        reb = sum(f * rebate(p) for s in (up, dn) if s for (_, p, f) in s.fills)
        rows.append(dict(start=st, winner=w, up_sh=us, dn_sh=ds, cost=uc + dc, payout=pay, profit=pay - uc - dc, rebate=reb,
                         pairs=min(us, ds), fills=(len(up.fills) if up else 0) + (len(dn.fills) if dn else 0)))
    res["flow"][v] = rows
    c = sum(r["cost"] for r in rows); p = sum(r["profit"] for r in rows)
    print(f"flow {v:<6} rounds {len(rows):4d} cost {c:9.2f} profit {p:8.2f} ({100 * p / c if c else 0:+.2f}%)", file=sys.stderr)
for x in exits:
    w = winner.get(x["start"])
    if not w: continue
    x["C"] = ((1.0 if w == x["outcome"] else 0.0) - x["price"]) * x["f"]
    res["exits"].append(x)
json.dump(res, open(a.out, "w"))
print(f"timed orders {len(res['timed'])}  crowd {len(crowd)}  exits {len(res['exits'])}  rows {n_rows}", file=sys.stderr)
