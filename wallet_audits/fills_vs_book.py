#!/usr/bin/env python3
"""fills_vs_book.py - line a wallet's BTC 5m fills up against OUR recorded order book (BME archive) for one day.

  zcat events_YYYYMMDD.csv.gz | mawk (filter) | python3 fills_vs_book.py tokens.json fills_day.csv --out day.json

For every fill: the book state just before the fill second (best bid/ask on his side and the other side, sum of
asks, sum of bids, chainlink spot move since the round open in bps, seconds into the round) and flags: was his
price at/above the ask (taker lift) or at/below the bid (maker fill)?
For every round: how many seconds the two best asks summed below 1.00 / 0.97 / 0.965 (the fee-covered pair
arbitrage) and the depth available at those moments. Read-only."""
import argparse, collections, csv, json, math, sys
Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))

ap = argparse.ArgumentParser(); ap.add_argument("tokens"); ap.add_argument("fills"); ap.add_argument("--out", required=True)
ap.add_argument("--spot-lag", type=float, default=1.44); a = ap.parse_args()
tokens = json.load(open(a.tokens))
fills = []
for r in csv.DictReader(open(a.fills)):
    if r["asset"] in tokens: fills.append(dict(ts=int(float(r["timestamp"])), asset=r["asset"], side=r["side"], price=float(r["price"]), size=float(r["size"]), usd=float(r["usdcSize"] or 0)))
fills.sort(key=lambda x: x["ts"]); fi = 0
bb = {}; ba = {}; basz = {}; bbsz = {}; spot = None; opens = {}; spot_q = collections.deque()
levels_ask = collections.defaultdict(dict); levels_bid = collections.defaultdict(dict)
spot_hist = collections.deque()   # (receive time, value) for momentum lookback


def spot_ago(t, secs):
    v = None
    for ts, val in reversed(spot_hist):
        if ts <= t - secs: v = val; break
    return v

out_fills = []; rounds = collections.defaultdict(lambda: dict(sec_lt100=0, sec_lt97=0, sec_lt965=0, min_sum=9.9, depth_at_min=0.0, secs=0, last_sec=None))


def good(p): return p is not None and p > 0.005


def state(asset):
    tk = tokens[asset]; o = tk["other"]
    return dict(bb=bb.get(asset), ba=ba.get(asset), obb=bb.get(o), oba=ba.get(o),
                basz=levels_ask[asset].get(ba.get(asset), 0.0) if good(ba.get(asset)) else 0.0,
                obasz=levels_ask[o].get(ba.get(o), 0.0) if good(ba.get(o)) else 0.0)


for line in sys.stdin:
    r = line.rstrip("\n").split(",")
    if len(r) < 9: continue
    k = r[1]
    if k == "spot_tick": spot_q.append((int(r[0]) / 1000.0 + a.spot_lag, float(r[5]))); continue
    if k == "twap_tick":
        st = (int(r[0]) // 300000) * 300
        if st not in opens and int(r[0]) / 1000.0 - st <= 3: opens[st] = float(r[5])
        continue
    asset = r[3]; tk = tokens.get(asset)
    if tk is None: continue
    t = int(r[0]) / 1000.0
    while spot_q and spot_q[0][0] <= t:
        rt, spot = spot_q.popleft(); spot_hist.append((rt, spot))
        while spot_hist and spot_hist[0][0] < t - 30: spot_hist.popleft()
    # fills whose second has arrived: record the state BEFORE applying this event
    while fi < len(fills) and fills[fi]["ts"] <= t:
        f = fills[fi]; fi += 1; s = state(f["asset"]); ftk = tokens[f["asset"]]; op = opens.get(ftk["start"])
        sum_asks = (s["ba"] + s["oba"]) if (good(s["ba"]) and good(s["oba"])) else None
        sum_bids = (s["bb"] + s["obb"]) if (good(s["bb"]) and good(s["obb"])) else None
        e_ = f["ts"] - ftk["start"]; fair = None
        if spot and op:
            fu = Phi(((spot - op) / op * 1e4) / (6.8 * math.sqrt(max(5.0, 300.0 - e_) / 300.0))); fair = fu if ftk["outcome"] == "Up" else 1 - fu
        s2 = spot_ago(f["ts"], 2); s5 = spot_ago(f["ts"], 5)
        out_fills.append(dict(f, outcome=ftk["outcome"], start=ftk["start"], e=e_, winner=ftk["winner"], **s, sum_asks=sum_asks, sum_bids=sum_bids,
                              spot_bps=((spot - op) / op * 1e4) if (spot and op) else None, fair=fair,
                              d2=((spot - s2) / s2 * 1e4) if (spot and s2) else None, d5=((spot - s5) / s5 * 1e4) if (spot and s5) else None,
                              at_ask=(good(s["ba"]) and f["price"] >= s["ba"] - 1e-9), at_bid=(good(s["bb"]) and f["price"] <= s["bb"] + 1e-9)))
    if k == "price_change":
        p = round(float(r[5]), 2); sz = float(r[6]); L = levels_bid[asset] if r[4] == "BUY" else levels_ask[asset]
        if sz <= 0: L.pop(p, None)
        else: L[p] = sz
    if k in ("price_change", "bba"):
        if r[7] and good(float(r[7])): bb[asset] = float(r[7])
        if r[8] and good(float(r[8])): ba[asset] = float(r[8])
    # per-second arbitrage opportunity bookkeeping (once per second per round, on the Up token's events)
    if tk["outcome"] == "Up" and 0 <= t - tk["start"] <= 300:
        sec = int(t); R = rounds[tk["start"]]
        if R["last_sec"] != sec:
            R["last_sec"] = sec; R["secs"] += 1; s = state(asset)
            if good(s["ba"]) and good(s["oba"]):
                sm = s["ba"] + s["oba"]
                if sm < 1.0: R["sec_lt100"] += 1
                if sm < 0.97: R["sec_lt97"] += 1
                if sm < 0.965: R["sec_lt965"] += 1
                if sm < R["min_sum"]: R["min_sum"] = sm; R["depth_at_min"] = min(s["basz"], s["obasz"])
# fills after the last event of the day
while fi < len(fills):
    f = fills[fi]; fi += 1; ftk = tokens[f["asset"]]
    out_fills.append(dict(f, outcome=ftk["outcome"], start=ftk["start"], e=f["ts"] - ftk["start"], winner=ftk["winner"], bb=None, ba=None, obb=None, oba=None, basz=0, obasz=0, sum_asks=None, sum_bids=None, spot_bps=None, fair=None, d2=None, d5=None, at_ask=None, at_bid=None))
json.dump(dict(fills=out_fills, rounds={str(k): v for k, v in rounds.items()}), open(a.out, "w"))
n = len(out_fills); ta = sum(1 for x in out_fills if x["at_ask"]); tb = sum(1 for x in out_fills if x["at_bid"])
print(f"fills {n} at/above ask {ta} ({100 * ta / n if n else 0:.1f}%) at/below bid {tb} ({100 * tb / n if n else 0:.1f}%) rounds tracked {len(rounds)}", file=sys.stderr)
