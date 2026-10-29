#!/usr/bin/env python3
"""signal_taker_replay.py - the "stale ask" taker tactic implied by the norm1e69 audit, replayed on the recorded book.

  zcat events_YYYYMMDD.csv.gz | mawk (spot_tick, twap_tick, price_change, bba for BTC 5m assets) | python3 signal_taker_replay.py tokens.json --out day.json

Rule: whenever a side's best ask is below the model fair value (chainlink spot vs the round's open, same formula as
pm_replay: Phi(bps / (6.8 * sqrt(left/300)))) by at least theta, send a limit buy AT that ask. It lands lat seconds
later and fills only if the best ask is still at or below our price, for min(SIZE, size resting at the ask).
Taker fee 0.07*p*(1-p) per share. Hold to settlement. Cooldown 2 s per side after a fill, cap CAP shares per side.
Variants: theta x spot lag (0 = we see chainlink as fast as the exchange's takers do; 1.44 s = our measured RTDS lag).
Output rows are pm_replay_report.py compatible (profit = AFTER fees; fees also stored per round)."""
import argparse, collections, json, math, sys

ap = argparse.ArgumentParser(); ap.add_argument("tokens"); ap.add_argument("--out", required=True)
ap.add_argument("--theta", type=float, nargs="+", default=[0.02, 0.03, 0.05, 0.08]); ap.add_argument("--lags", type=float, nargs="+", default=[0.0, 1.44])
ap.add_argument("--lat", type=float, default=0.1); ap.add_argument("--size", type=float, default=20.0); ap.add_argument("--cap", type=float, default=200.0)
ap.add_argument("--cooldown", type=float, default=2.0); ap.add_argument("--qs", type=float, default=10.0); ap.add_argument("--qe", type=float, default=295.0)
a = ap.parse_args(); tokens = json.load(open(a.tokens))
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
fee = lambda p: 0.07 * p * (1 - p)
VARS = [(th, lg) for lg in a.lags for th in a.theta]
name = lambda th, lg: f"theta{th:g} lag{lg:g}"


class S:
    __slots__ = ("filled", "cost", "fees", "n", "pend", "last")

    def __init__(s): s.filled = 0.0; s.cost = 0.0; s.fees = 0.0; s.n = 0; s.pend = None; s.last = -1e9


state = {v: collections.defaultdict(S) for v in VARS}
ba = {}; levels_ask = collections.defaultdict(dict); opens = {}
spot_q = {lg: collections.deque() for lg in a.lags}; spot = {lg: None for lg in a.lags}
active = {}      # asset -> tk for assets whose round is in progress (seen recently)


def good(p): return p is not None and p > 0.005


def fair_of(tk, lg, t):
    op = opens.get(tk["start"]); sp = spot[lg]
    if op is None or sp is None: return None
    left = max(5.0, 300.0 - (t - tk["start"]))
    fu = Phi(((sp - op) / op * 1e4) / (6.8 * math.sqrt(left / 300.0)))
    return fu if tk["outcome"] == "Up" else 1 - fu


def step(asset, tk, t):
    e = t - tk["start"]; k = ba.get(asset)
    for v in VARS:
        th, lg = v; s = state[v][asset]
        if s.pend is not None and t >= s.pend[1]:
            lim, _, sz = s.pend; s.pend = None
            if good(k) and k <= lim + 1e-9:
                f = min(sz, float(levels_ask[asset].get(k, 0.0)) or sz)
                if f > 0: s.filled += f; s.cost += f * k; s.fees += f * fee(k); s.n += 1; s.last = t
        if s.pend is None and a.qs <= e <= a.qe and good(k) and s.filled < a.cap - 1e-9 and t - s.last >= a.cooldown:
            fv = fair_of(tk, lg, t)
            if fv is not None and fv - k >= th:
                s.pend = (k, t + a.lat, min(a.size, a.cap - s.filled))


for line in sys.stdin:
    r = line.rstrip("\n").split(",")
    if len(r) < 9: continue
    k = r[1]; t = int(r[0]) / 1000.0
    if k == "spot_tick":
        v = float(r[5])
        for lg in a.lags: spot_q[lg].append((t + lg, v))
        continue
    if k == "twap_tick":
        st = (int(r[0]) // 300000) * 300
        if st not in opens and t - st <= 3: opens[st] = float(r[5])
        continue
    asset = r[3]; tk = tokens.get(asset)
    if tk is None: continue
    for lg in a.lags:
        q = spot_q[lg]
        while q and q[0][0] <= t: spot[lg] = q.popleft()[1]
    if k == "price_change":
        if r[4] == "SELL":
            p = round(float(r[5]), 2); sz = float(r[6])
            if sz <= 0: levels_ask[asset].pop(p, None)
            else: levels_ask[asset][p] = sz
    if k in ("price_change", "bba") and r[8] and good(float(r[8])): ba[asset] = float(r[8])
    if tk["start"] <= t <= tk["start"] + 300: active[asset] = tk
    for asst in [x for x in active if not (active[x]["start"] <= t <= active[x]["start"] + 300)]: active.pop(asst, None)
    for asst, atk in active.items(): step(asst, atk, t)

winner = {tk["start"]: tk["winner"] for tk in tokens.values()}
res = {"variants": {}}
for v in VARS:
    R = collections.defaultdict(dict)
    for asset, s in state[v].items():
        tk = tokens[asset]; R[tk["start"]][tk["outcome"]] = s
    rows = []
    for st in sorted(R):
        up = R[st].get("Up"); dn = R[st].get("Down"); w = winner.get(st)
        us = up.filled if up else 0.0; ds = dn.filled if dn else 0.0; uc = up.cost if up else 0.0; dc = dn.cost if dn else 0.0
        fe = (up.fees if up else 0.0) + (dn.fees if dn else 0.0)
        if us + ds <= 0 or not w: continue
        pay = us if w == "Up" else ds
        rows.append(dict(start=st, winner=w, up_sh=us, dn_sh=ds, cost=uc + dc, payout=pay, fees=fe, profit=pay - uc - dc - fe, gross=pay - uc - dc,
                         pairs=min(us, ds), fills=(up.n if up else 0) + (dn.n if dn else 0)))
    res["variants"][name(*v)] = dict(cfg=dict(theta=v[0], lag=v[1], lat=a.lat), rounds=rows)
    c = sum(x["cost"] for x in rows); p = sum(x["profit"] for x in rows); g = sum(x["gross"] for x in rows)
    print(f"{name(*v):<18} rounds {len(rows):4d} fills {sum(x['fills'] for x in rows):5d} cost {c:9.2f} gross {g:8.2f} after fees {p:8.2f} ({100 * p / c if c else 0:+.2f}%)", file=sys.stderr)
json.dump(res, open(a.out, "w"))
