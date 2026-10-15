#!/usr/bin/env python3
"""pm_replay.py - STEP 3: the paired-maker tactic replayed on the real recorded book WITH an order-latency model.

  zcat events_YYYYMMDD.csv.gz | mawk -F, '(filter)' | python3 pm_replay.py tokens.json --lat 0 0.05 0.1 0.2 1.0 --out day.json

Derived from the September queue-aware replay (s9_work/maker_sim3.py, tactic "G model-5c cap.97 inv40"
and "C join cap.97 inv40"). Same queue rules:
  * our order joins a price level BEHIND the size already resting there (queue_ahead);
  * a taker trade at our level eats queue_ahead first; a trade THROUGH our price fills us first;
  * levels shrinking lets the queue shrink ("standard" rule).
NEW: a real latency model instead of "re-quote once per react seconds".
  lat = seconds from our decision to the order being live at the exchange (cancel of the old order
  lands at the same time). While the new order is in flight the OLD order stays live and can be run
  through; the new order joins the queue behind whatever rests at its level when it lands. The book
  we decide on is the book as received (feed lag ~12 ms measured, ignored).
  lat=0 is the "instant" ceiling; lat=1.0 approximates the old 1 s loop.
  --spot-lag: the recorded chainlink spot ticks carry the SOURCE timestamp; we only receive them ~1.44 s
  later (measured), so the replay applies each tick 1.44 s after its stamp. The September replay
  did not do this and was optimistic for the "model" tactic.
Outputs one JSON per day: per variant, per round: shares/cost per side, payout, profit, pairs, fills.
Read-only; no keys.
"""
import argparse, collections, json, math, sys

ap = argparse.ArgumentParser()
ap.add_argument("tokens")
ap.add_argument("--lat", type=float, nargs="+", default=[0.0, 0.05, 0.1, 0.2, 1.0])
ap.add_argument("--size", type=float, default=20.0)
ap.add_argument("--cap-shares", type=float, default=200.0)
ap.add_argument("--modes", nargs="+", default=["model", "join"])
ap.add_argument("--spot-lag", type=float, default=1.44, help="seconds the RTDS chainlink spot reaches us after its own timestamp (measured 1.44 s on twapvm 2026-10-15)")
ap.add_argument("--out", required=True)
a = ap.parse_args()
tokens = json.load(open(a.tokens))

VARIANTS = {}
for mode in a.modes:
    for lat in a.lat:
        VARIANTS[f"{mode} lat{lat:g}"] = dict(mode=mode, cap=0.97, inv=40, edge=(0.05 if mode == "model" else 0.0), lat=lat)

QS, QE = 60, 240
SIZE, CAP = a.size, a.cap_shares
P_MIN, P_MAX = 0.02, 0.97
SIGMA = 6.8
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))


class Side:
    __slots__ = ("price", "qa", "rem", "filled", "cost", "fills", "pend_price", "pend_t")

    def __init__(s):
        s.price = None; s.qa = 0.0; s.rem = 0.0; s.filled = 0.0; s.cost = 0.0; s.fills = []
        s.pend_price = None; s.pend_t = None        # order in flight: price (None = cancel only) and landing time


levels = collections.defaultdict(dict); bb = {}; ba = {}
state = {v: collections.defaultdict(Side) for v in VARIANTS}
spot = None; opens = {}; n_rows = 0
spot_q = collections.deque()      # (time we actually receive it, value): the recorded tick carries the SOURCE time


def hit(v, asset, price, size, t):
    s = state[v].get(asset)
    if s is None or s.price is None or s.rem <= 0: return
    if price < s.price - 1e-9: f = min(s.rem, size)
    elif abs(price - s.price) < 1e-9:
        if s.qa >= size: s.qa -= size; return
        f = min(s.rem, size - s.qa); s.qa = 0.0
    else: return
    if f <= 0: return
    s.rem -= f; s.filled += f; s.cost += f * s.price; s.fills.append((t, s.price, f))


def land(s, asset, t):
    """the in-flight order lands: old order gone, new one (if any) joins the queue behind the level size now"""
    if s.pend_t is None or t < s.pend_t: return
    want = s.pend_price; s.pend_price = None; s.pend_t = None
    if want is None:
        s.price = None; s.rem = 0.0; return
    s.price = want; s.rem = min(SIZE, CAP - s.filled)
    b = bb.get(asset)
    s.qa = float(levels[asset].get(want, 0.0)) if (b is not None and want <= b + 1e-9) else 0.0


def send(s, want, t, lat):
    """decide a new price (None = cancel). With lat=0 it lands now."""
    if lat <= 0:
        s.pend_price = want; s.pend_t = t; return
    s.pend_price = want; s.pend_t = t + lat


def requote(v, cfg, asset, tk, t, e):
    s = state[v][asset]
    land(s, asset, t)
    live_price = s.price if (s.price is not None and s.rem > 0) else None
    target = s.pend_price if s.pend_t is not None else live_price      # what we have already asked for
    b = bb.get(asset); aa = ba.get(asset)
    want = None
    if not (e < QS or e > QE or b is None or s.filled >= CAP - 1e-9):
        o = state[v][tk["other"]]
        if cfg["mode"] == "join":
            want = b
        else:
            op = opens.get(tk["start"])
            if spot is not None and op is not None:
                left = max(5.0, 300.0 - e)
                f = Phi(((spot - op) / op * 1e4) / (SIGMA * math.sqrt(left / 300.0)))
                fair = f if tk["outcome"] == "Up" else 1 - f
                want = math.floor((fair - cfg["edge"]) * 100 + 1e-9) / 100.0
                if aa is not None and want >= aa - 1e-9: want = round(aa - 0.01, 2)    # never cross: maker only
        if want is not None:
            if cfg["cap"] is not None and o.filled > s.filled + 1e-9:                  # other side held unpaired
                want = min(want, math.floor((cfg["cap"] - o.cost / o.filled) * 100 + 1e-9) / 100.0)
            if cfg["inv"] is not None and s.filled - o.filled >= cfg["inv"] - 1e-9: want = None
            if want is not None:
                want = round(want, 2)
                if not (P_MIN <= want <= P_MAX): want = None
    if want is None:
        if target is not None: send(s, None, t, cfg["lat"])
        return
    if target is None or abs(target - want) >= 0.0099:
        send(s, want, t, cfg["lat"])
    if cfg["lat"] <= 0: land(s, asset, t)


for line in sys.stdin:
    r = line.rstrip("\n").split(",")
    if len(r) < 9: continue
    k = r[1]
    if k == "spot_tick":
        spot_q.append((int(r[0]) / 1000.0 + a.spot_lag, float(r[5]))); continue
    if k == "twap_tick":
        st = (int(r[0]) // 300000) * 300
        if st not in opens and int(r[0]) / 1000.0 - st <= 3: opens[st] = float(r[5])
        continue
    asset = r[3]; tk = tokens.get(asset)
    if tk is None: continue
    n_rows += 1
    t = int(r[0]) / 1000.0; e = t - tk["start"]
    while spot_q and spot_q[0][0] <= t:
        spot = spot_q.popleft()[1]
    if k == "price_change":
        p = round(float(r[5]), 2); sz = float(r[6])
        if r[4] == "BUY":
            if sz <= 0: levels[asset].pop(p, None)
            else: levels[asset][p] = sz
            for v in VARIANTS:
                s = state[v].get(asset)
                if s is not None and s.price is not None and abs(s.price - p) < 1e-9 and sz < s.qa: s.qa = sz
        if r[7]: bb[asset] = float(r[7])
        if r[8]: ba[asset] = float(r[8])
    elif k == "bba":
        if r[7]: bb[asset] = float(r[7])
        if r[8]: ba[asset] = float(r[8])
    elif k == "trade":
        p = float(r[5]); sz = float(r[6])
        if r[4] == "SELL": tgt, hp = asset, round(p, 2)
        else: tgt, hp = tk["other"], round(1 - p, 2)
        for v in VARIANTS:
            s = state[v].get(tgt)
            if s is not None: land(s, tgt, t)
            hit(v, tgt, hp, sz, t)
    for v, cfg in VARIANTS.items(): requote(v, cfg, asset, tk, t, e)

winner = {tk["start"]: tk["winner"] for tk in tokens.values()}
res = {"rows": n_rows, "opens": len(opens), "size": SIZE, "cap_shares": CAP, "spot_lag": a.spot_lag, "variants": {}}
for v in VARIANTS:
    R = collections.defaultdict(dict)
    for asset, s in state[v].items():
        tk = tokens[asset]; R[tk["start"]][tk["outcome"]] = s
    rows = []
    for st in sorted(R):
        up = R[st].get("Up"); dn = R[st].get("Down"); w = winner.get(st)
        us = up.filled if up else 0.0; ds = dn.filled if dn else 0.0; uc = up.cost if up else 0.0; dc = dn.cost if dn else 0.0
        if us + ds <= 0 or not w: continue
        pay = us if w == "Up" else ds
        rows.append(dict(start=st, winner=w, up_sh=us, dn_sh=ds, up_avg=(uc / us if us else None), dn_avg=(dc / ds if ds else None),
                         cost=uc + dc, payout=pay, profit=pay - uc - dc, pairs=min(us, ds),
                         fills=(len(up.fills) if up else 0) + (len(dn.fills) if dn else 0)))
    res["variants"][v] = dict(cfg=VARIANTS[v], rounds=rows)
    c = sum(r["cost"] for r in rows); p = sum(r["profit"] for r in rows)
    print(f"{v:<16} rounds {len(rows):4d} fills {sum(r['fills'] for r in rows):5d} cost {c:9.2f} profit {p:8.2f} ({100 * p / c if c else 0:+.2f}%)", file=sys.stderr)
json.dump(res, open(a.out, "w"))
