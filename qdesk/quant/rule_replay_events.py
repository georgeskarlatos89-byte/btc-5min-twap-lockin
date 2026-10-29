#!/usr/bin/env python3
"""rule_replay_events.py - tier-2 rule replay on the raw BME event stream (INTERFACES.md section 8).

  zcat events_YYYYMMDD.csv.gz | mawk <filter of build_days.sh> \
      | python3 rule_replay_events.py tokens.json --rules rules.json --day YYYYMMDD --out results_tier2.json
  python3 rule_replay_events.py tokens.json --rules r.json --day 20260926 --in fixture.csv.gz --out x.json

Same rule semantics as tier 1 (quant/rule_engine_1s.py): the decision for integer second s is taken on the SAME
feature row the history build writes for s (FeatureState snapshot = state of the events with t <= s), so the two
tiers fire on the same seconds. What tier 2 does exactly instead of through the 1-s look-ahead proxies:
  taker  = step() of wallet_audits/signal_taker_replay.py: the limit buy at the ask of s lands at s + lat and is checked
           at the first event at or after that instant: it fills only if the best ask is still <= the limit, at the
           current ask, for min(size, size resting at that ask) (unknown level size = whole size, as in step()).
           Fee fees.taker_fee. If it is not there, nothing fills (the next second may fire again).
  maker  = Side / hit / land / send of paired_maker/pm_replay.py (v2 cancel clock): the order joins its level BEHIND
           the resting size when it lands (s + lat), taker hits at the level eat the queue first, hits through the
           level fill us first, a shrinking level shrinks the queue; the cancel sent at s + ttl_s lands lat later.
  exits  ttl / rule: a taker limit SELL at the bid lands at + lat and fills if the bid is still >= the limit for
           min(size, size resting at the bid); the remainder is re-sent at every later second until settlement.
Output = the section-4 results schema with engine "rule_replay_events" and fill_model "events_exact".
Decisions happen only inside the round (t_el 0..T-1); cooldown is measured from the fill's decision second (tier 1 too).
"""
import argparse
import collections
import gzip
import io
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.features.features import FeatureState, T_OF, good  # noqa: E402
from qdesk.features.bme_to_features import load_rounds, day_bounds, open_input, blackout_loader, parse_book  # noqa: E402
from qdesk.quant.rule_engine_1s import rule_fields, per_day_summary, aggregate, SPOT_LAG  # noqa: E402

ENGINE = "rule_replay_events"


def _common():
    try:
        from qdesk.common import dsl, fees, settle
    except ImportError as e:
        raise ImportError("qdesk.common (dsl, fees, settle) is required by quant/rule_replay_events.py: %s" % e)
    return dsl, fees, settle


def _nz(v):
    return None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)


# ----------------------------------------------------------------------------- maker order (copied queue rules)
class Side:
    # copied from C:\g\tlfix\paired_maker\pm_replay.py (class Side) - one order, size/cap passed in
    __slots__ = ("price", "qa", "rem", "filled", "cost", "fills", "pend_price", "pend_t", "cancel_t", "size")

    def __init__(s, size):
        s.price = None; s.qa = 0.0; s.rem = 0.0; s.filled = 0.0; s.cost = 0.0; s.fills = []
        s.pend_price = None; s.pend_t = None; s.cancel_t = None; s.size = size


def hit(s, price, size, t):
    # copied from C:\g\tlfix\paired_maker\pm_replay.py (hit) - takes the Side directly
    if s is None or s.price is None or s.rem <= 0: return
    if price < s.price - 1e-9: f = min(s.rem, size)
    elif abs(price - s.price) < 1e-9:
        if s.qa >= size: s.qa -= size; return
        f = min(s.rem, size - s.qa); s.qa = 0.0
    else: return
    if f <= 0: return
    s.rem -= f; s.filled += f; s.cost += f * s.price; s.fills.append((t, s.price, f))


def land(s, levels_bid, bb, t):
    # copied from C:\g\tlfix\paired_maker\pm_replay.py (land) - levels/bb passed in
    if s.cancel_t is not None and t >= s.cancel_t:
        s.price = None; s.rem = 0.0; s.cancel_t = None
    if s.pend_t is None or t < s.pend_t: return
    want = s.pend_price; s.pend_price = None; s.pend_t = None
    if want is None:
        return
    if s.cancel_t is not None:
        s.price = None; s.rem = 0.0; s.cancel_t = None
    s.price = want; s.rem = s.size - s.filled
    s.qa = float(levels_bid.get(want, 0.0)) if (bb is not None and want <= bb + 1e-9) else 0.0


def send(s, want, t, lat):
    # copied from C:\g\tlfix\paired_maker\pm_replay.py (send) - v2 cancel clock
    if lat <= 0:
        s.pend_price = want; s.pend_t = t; return
    if s.price is not None and s.rem > 0 and s.cancel_t is None:
        s.cancel_t = t + lat
    s.pend_price = want; s.pend_t = t + lat


# ----------------------------------------------------------------------------- per (rule, round) state
class RuleRound:
    __slots__ = ("rule", "key", "fs", "per_round", "open", "pend_taker", "makers", "sells", "last_fill_s", "trades_open",
                 "n_nofill", "n_assumed")

    def __init__(self, rule, key, fs):
        self.rule = rule
        self.key = key
        self.fs = fs
        self.per_round = 0
        self.open = {}            # side -> position dict (entry filled, waiting for exit/settle)
        self.pend_taker = []      # [dict(side, limit, land_t, size, s, ctx)]
        self.makers = {}          # side -> dict(order=Side, s, ctx, cancel_s, done)
        self.sells = []           # [dict(side, pos, limit, land_t, size)]
        self.last_fill_s = None
        self.n_nofill = 0
        self.n_assumed = 0


class Replay:
    def __init__(self, rules, day, winners, lat, dsl, fees, settle):
        self.rules = rules
        self.day = day
        self.winners = winners
        self.lat = lat
        self.dsl, self.fees, self.settle = dsl, fees, settle
        self.rr = {}              # (rule_id, key) -> RuleRound
        self.trades = {r["id"]: [] for r in rules}
        self.rounds_traded = {r["id"]: set() for r in rules}
        self.last_fill_s = {r["id"]: -1e18 for r in rules}
        self.fields = {r["id"]: rule_fields(r) for r in rules}
        self.stats = {r["id"]: {"no_fill": 0, "assumed": 0, "unsettled": 0} for r in rules}
        self.busy = {}            # key -> set of RuleRound with an order in flight / resting / an exit pending

    # ---- helpers
    def rr_of(self, rule, key, fs):
        k = (rule["id"], key)
        x = self.rr.get(k)
        if x is None:
            x = RuleRound(rule, key, fs)
            self.rr[k] = x
        return x

    def ctx(self, rule, row, other):
        out = {}
        for f in self.fields[rule["id"]]:
            out[f] = _nz(self.dsl.field_value(f, row, other))
        out["ask"] = _nz(row.get("ba"))
        out["bid"] = _nz(row.get("bb"))
        return out

    # ---- decisions at integer second s on the snapshot rows (identical to the tier-1 feature row)
    def decide(self, key, fs, s, rows):
        market, start = key
        t_el = s - start
        ru, rd = rows["Up"], rows["Down"]
        for rule in self.rules:
            if rule.get("market", "5m") != market:
                continue
            rid = rule["id"]
            x = self.rr_of(rule, key, fs)
            # exits first (ttl / rule) for open positions
            ex = rule.get("exit") if isinstance(rule.get("exit"), dict) else {"mode": "hold_to_settle"}
            for side, pos in list(x.open.items()):
                if pos.get("closing") or pos.get("closed"):
                    continue
                row = ru if side == "Up" else rd
                oth = rd if side == "Up" else ru
                want_exit = False
                if ex.get("mode") == "ttl" and t_el >= pos["t_el"] + float(ex["ttl_s"]):
                    want_exit = True
                elif ex.get("mode") == "rule" and t_el > pos["t_el"] and self.dsl.eval_when(ex.get("when") or [], row, oth):
                    want_exit = True
                if want_exit:
                    pos["closing"] = True
                    pos["exit_kind"] = ex.get("mode")
                    pos["exit_t_el"] = t_el
                    self.send_sell(x, side, pos, row, s)
            for side, pos in x.open.items():
                if pos.get("closing") and not pos.get("closed") and pos.get("sell_pending") is None and pos["rem"] > 1e-9:
                    row = ru if side == "Up" else rd
                    self.send_sell(x, side, pos, row, s)          # re-send the remainder at the new bid
            # entry
            lim = rule.get("limits") or {}
            if x.per_round >= int(lim.get("max_trades_per_round", 1)):
                continue
            if start not in self.rounds_traded[rid] and len(self.rounds_traded[rid]) >= int(lim.get("max_rounds_per_day", 10 ** 9)):
                continue
            if s - self.last_fill_s[rid] < float(lim.get("cooldown_s", 0)):
                continue
            if x.pend_taker or any(not m.get("done") for m in x.makers.values()):
                continue                                           # an entry is already in flight / resting
            cs = self.dsl.chosen_side(rule, ru, rd)
            if cs is None:
                continue
            sides = ["Up", "Down"] if cs == "both" else [cs]
            if any(sd in x.open for sd in sides):
                continue
            leg = rule["entry"]["leg"]
            size_usd = float(leg["size_usd"])
            for sd in sides:
                row = ru if sd == "Up" else rd
                oth = rd if sd == "Up" else ru
                ask = _nz(row.get("ba"))
                bb = _nz(row.get("bb"))
                if leg["type"] == "taker":
                    if ask is None or ask <= 0:
                        continue
                    mp = leg.get("max_price")
                    if mp is not None and ask > float(mp) + 1e-9:
                        continue
                    x.pend_taker.append({"side": sd, "limit": ask, "land_t": s + self.lat, "size": size_usd / ask, "s": s,
                                         "ctx": self.ctx(rule, row, oth)})
                else:
                    if bb is None:
                        continue
                    off = int(leg.get("offset_ticks", 0) or 0)
                    price = round(bb - off / 100.0, 2)
                    if len(sides) == 2 and leg.get("pair_cap") is not None and sd == "Down" and "Up" in x.makers:
                        price = min(price, round(math.floor((float(leg["pair_cap"]) - x.makers["Up"]["order"].pend_price) * 100 + 1e-9) / 100.0, 2))
                    if price < 0.01:
                        continue
                    o = Side(size_usd / price)
                    send(o, price, s, self.lat)
                    x.makers[sd] = {"order": o, "s": s, "ctx": self.ctx(rule, row, oth), "cancel_s": s + float(leg.get("ttl_s") or 60),
                                    "done": False, "cancel_sent": False}
            if self.in_flight(x):
                self.busy.setdefault(key, set()).add(x)

    def send_sell(self, x, side, pos, row, s):
        bid = _nz(row.get("bb"))
        if bid is None or bid <= 0:
            return
        pos["sell_pending"] = {"limit": bid, "land_t": s + self.lat, "size": pos["rem"], "s": s}
        self.busy.setdefault(x.key, set()).add(x)

    @staticmethod
    def in_flight(x):
        if x.pend_taker:
            return True
        if any(not m["done"] for m in x.makers.values()):
            return True
        return any(p.get("sell_pending") is not None for p in x.open.values())

    # ---- event-time resolution (state AFTER the event at t)
    def resolve(self, key, fs, t):
        market, start = key
        bset = self.busy.get(key)
        if not bset:
            return
        for x in list(bset):
            rule = x.rule
            rid = rule["id"]
            # taker entries (step() of signal_taker_replay.py)
            keep = []
            for pt in x.pend_taker:
                if t < pt["land_t"]:
                    keep.append(pt)
                    continue
                asset = fs.asset_of[pt["side"]]
                k = fs.ba.get(asset)
                if good(k) and k <= pt["limit"] + 1e-9:
                    lvl = float(fs.levels_ask[asset].get(k, 0.0))
                    if lvl <= 0:
                        self.stats[rid]["assumed"] += 1
                    f = min(pt["size"], lvl or pt["size"])
                    if f > 0:
                        fee = self.fees.taker_fee(k, f)
                        self.open_position(x, rule, pt["side"], k, f, fee, pt["s"], pt["ctx"], "taker",
                                           "full" if f >= pt["size"] - 1e-9 else "partial", fill_t=t)
                        continue
                self.stats[rid]["no_fill"] += 1
            x.pend_taker = keep
            # maker entries
            for sd, m in list(x.makers.items()):
                if m["done"]:
                    continue
                o = m["order"]
                asset = fs.asset_of[sd]
                land(o, fs.levels_bid[asset], fs.bb.get(asset), t)
                if not m["cancel_sent"] and t >= m["cancel_s"]:
                    send(o, None, m["cancel_s"], self.lat)
                    m["cancel_sent"] = True
                    land(o, fs.levels_bid[asset], fs.bb.get(asset), t)
                dead = m["cancel_sent"] and o.price is None and o.pend_t is None
                if o.rem <= 1e-9 and o.filled > 0 or dead:
                    m["done"] = True
                    if o.filled > 0:
                        self.open_position(x, rule, sd, o.cost / o.filled, o.filled, 0.0, m["s"], m["ctx"], "maker",
                                           "full" if o.rem <= 1e-9 else "partial", fill_t=o.fills[0][0] if o.fills else t)
                    else:
                        self.stats[rid]["no_fill"] += 1
            # exits in flight
            for sd, pos in x.open.items():
                sp = pos.get("sell_pending")
                if sp is None or t < sp["land_t"]:
                    continue
                pos["sell_pending"] = None
                asset = fs.asset_of[sd]
                b = fs.bb.get(asset)
                if good(b) and b >= sp["limit"] - 1e-9:
                    lvl = float(fs.levels_bid[asset].get(b, 0.0))
                    f = min(sp["size"], lvl or sp["size"])
                    if f > 0:
                        fee = self.fees.taker_fee(b, f)
                        pos["proceeds"] += b * f
                        pos["fee_out"] += fee
                        pos["rem"] -= f
                        pos["exit_prices"].append((b, f))
                        if pos["rem"] <= 1e-9:
                            pos["closed"] = True
            if not self.in_flight(x):
                bset.discard(x)

    def open_position(self, x, rule, side, price, size, fee, s, ctx, leg, fill, fill_t):
        rid = rule["id"]
        start = x.key[1]
        x.open[side] = {"price": price, "size": size, "fee": fee, "t_el": s - start, "ctx": ctx, "leg": leg, "fill": fill,
                        "rem": size, "proceeds": 0.0, "fee_out": 0.0, "exit_prices": [], "closing": False, "closed": False,
                        "sell_pending": None, "fill_t_el": round(fill_t - start, 3)}
        x.per_round += 1
        self.rounds_traded[rid].add(start)
        self.last_fill_s[rid] = s

    # ---- settlement of a finished round
    def settle_round(self, key):
        market, start = key
        self.busy.pop(key, None)
        w = self.winners.get((market, start))
        for rule in self.rules:
            x = self.rr.pop((rule["id"], key), None)
            if x is None:
                continue
            rid = rule["id"]
            size_usd = float(rule["entry"]["leg"]["size_usd"])
            for side, pos in x.open.items():
                rem = pos["rem"]
                if rem > 1e-9 and w not in ("Up", "Down"):
                    self.stats[rid]["unsettled"] += 1
                    continue
                st = self.settle.settle_pnl([{"side": side, "price": pos["price"], "size": rem, "fee": 0.0}], w) if rem > 1e-9 else {"payout": 0.0}
                payout = st["payout"] if rem > 1e-9 else 0.0
                cost = pos["price"] * pos["size"]
                pnl = pos["proceeds"] + payout - cost - pos["fee"] - pos["fee_out"]
                if pos.get("closing") and pos["exit_prices"]:
                    exit_kind = pos["exit_kind"]
                    exit_price = sum(p * f for p, f in pos["exit_prices"]) / sum(f for p, f in pos["exit_prices"])
                    exit_t_el = pos["exit_t_el"]
                else:
                    exit_kind, exit_price, exit_t_el = "settle", (1.0 if w == side else 0.0), T_OF[market]
                tr = {"day": self.day, "start": int(start), "side": side, "t_el": int(pos["t_el"]), "leg": pos["leg"],
                      "price": round(pos["price"], 4), "size": round(pos["size"], 6), "fill": pos["fill"],
                      "fee": round(pos["fee"] + pos["fee_out"], 5), "winner": w, "pnl": round(pnl, 6),
                      "R": round(pnl / size_usd, 6), "cost": round(cost, 6), "proceeds": round(pos["proceeds"] + payout, 6),
                      "exit": exit_kind, "exit_t_el": exit_t_el, "exit_price": round(exit_price, 4),
                      "fill_t_el": pos["fill_t_el"], "ctx": pos["ctx"]}
                if pos["leg"] == "maker":
                    tr["rebate"] = self.fees.maker_rebate(pos["price"], pos["size"])
                self.trades[rid].append(tr)


# ----------------------------------------------------------------------------- driver
def replay(rules, inp, day, tokens_path, tokens15_path=None, lat=0.1, spot_lag=SPOT_LAG, blackout=True, verbose=True):
    dsl, fees, settle = _common()
    rules = [r for r in rules if not dsl.validate_rule(r)]
    d0, d1 = day_bounds(day)
    rounds = {"5m": load_rounds(tokens_path, "5m")}
    if tokens15_path:
        rounds["15m"] = load_rounds(tokens15_path, "15m")
    winners = {}
    sched = []
    asset_round = {}
    for mk, rr in rounds.items():
        for st, r in rr.items():
            winners[(mk, st)] = r.get("winner")
            if d0 <= st < d1:
                sched.append((st, mk))
                asset_round[r["Up"]] = (mk, st)
                asset_round[r["Down"]] = (mk, st)
    sched.sort()
    blk = blackout_loader() if blackout else None
    rp = Replay(rules, day, winners, lat, dsl, fees, settle)
    live = {}
    decided = {}      # key -> next second to decide
    sched_i = 0
    n_in = n_used = 0
    t0 = time.time()

    def make_state(key):
        mk, st = key
        r = rounds[mk][st]
        fs = FeatureState(mk, st, r["Up"], r["Down"], spot_lag=spot_lag, blackout_fn=blk, src="bme")
        live[key] = fs
        decided[key] = st
        return fs

    def after(key, fs, t):
        # decisions on the new snapshot seconds (same rows as the tier-1 feature file), then event-time resolution
        nxt = decided[key]
        for pnd in fs.pending:
            if pnd.s >= nxt:
                rp.decide(key, fs, pnd.s, pnd.rows)
                nxt = pnd.s + 1
        decided[key] = nxt
        rp.resolve(key, fs, t)
        fs.flush(t)                                   # drop emitted seconds (rows not needed here)

    def retire(t):
        for key in [k for k, fs in live.items() if t > fs.end + 5.0]:
            rp.settle_round(key)
            live.pop(key, None)

    fin = open_input(inp)
    last_t = None
    try:
        for line in fin:
            n_in += 1
            if n_in == 1 and line.startswith("ts_ms"):
                continue
            r = line.rstrip("\r\n").split(",")
            if len(r) < 9:
                continue
            try:
                t = int(r[0]) / 1000.0
            except ValueError:
                continue
            k = r[1]
            while sched_i < len(sched) and sched[sched_i][0] - 90.0 <= t:
                key = (sched[sched_i][1], sched[sched_i][0])
                if key not in live:
                    make_state(key)
                sched_i += 1
            if k == "spot_tick":
                try:
                    v = float(r[5])
                except ValueError:
                    continue
                for fs in live.values():
                    fs.on_spot(t, v)
                continue
            if k == "twap_tick":
                try:
                    v = float(r[5])
                except ValueError:
                    continue
                for fs in live.values():
                    fs.on_twap(t, v)
                continue
            asset = r[3]
            key = asset_round.get(asset)
            if key is None:
                continue
            fs = live.get(key)
            if fs is None:
                if t > key[1] + T_OF[key[0]] + 5.0:
                    continue
                fs = make_state(key)
            n_used += 1
            if k == "price_change":
                fs.on_price_change(asset, r[4], r[5], r[6] or 0.0, r[7] or None, r[8] or None, t)
                if r[4] == "BUY":
                    p = round(float(r[5]), 2)
                    sz = float(r[6] or 0.0)
                    sd = fs.assets[asset]
                    for x in rp.busy.get(key, ()):
                        m = x.makers.get(sd)
                        if m is not None and not m["done"]:
                            o = m["order"]
                            if o.price is not None and abs(o.price - p) < 1e-9 and sz < o.qa:
                                o.qa = sz                      # shrinking level shrinks the queue (pm_replay.py)
            elif k == "bba":
                fs.on_bba(asset, r[7] or None, r[8] or None, t)
            elif k == "trade":
                fs.on_trade(asset, r[4], r[5], r[6] or 0.0, t)
                p = float(r[5])
                sz = float(r[6] or 0.0)
                if r[4] == "SELL":
                    tgt, hp = asset, round(p, 2)
                else:
                    tgt, hp = fs.other[asset], round(1 - p, 2)
                sd = fs.assets[tgt]
                for x in rp.busy.get(key, ()):
                    m = x.makers.get(sd)
                    if m is not None and not m["done"]:
                        land(m["order"], fs.levels_bid[tgt], fs.bb.get(tgt), t)
                        hit(m["order"], hp, sz, t)
            elif k == "book":
                bk = parse_book(r)
                if bk is None:
                    continue
                fs.on_book(asset, bk[0], bk[1], t)
            else:
                continue
            for key2, fs2 in live.items():
                if key2 != key:
                    fs2.on_clock(t)
            for key2, fs2 in list(live.items()):
                after(key2, fs2, t)
            last_t = t
            if (n_used & 4095) == 0:
                retire(t)
    except EOFError as e:
        print(f"[rule_replay_events] WARNING truncated gzip input after {n_in} rows: {e}", file=sys.stderr)
    finally:
        fin.close()
    # end of stream: settle everything that is open
    for key in list(live):
        rp.settle_round(key)
    live.clear()
    res = {"engine": ENGINE, "lat": lat, "spot_lag": spot_lag, "days": [day], "rules": {}}
    for rule in rules:
        rid = rule["id"]
        trades = rp.trades[rid]
        blk_ = {"per_day": {day: dict(per_day_summary(trades), **rp.stats[rid])}, "trades": trades, "fit": aggregate(trades),
                "test": None, "windows": [], "mc_dd": {"p50": 0.0, "p95": 0.0, "n": 1000}, "fill_model": "events_exact",
                "rule_hash": dsl.canonical_hash(rule)}
        try:
            from qdesk.common.stats import mc_drawdown
            blk_["mc_dd"] = mc_drawdown([t["pnl"] for t in trades])
        except ImportError:
            pass
        res["rules"][rid] = blk_
        if verbose:
            s = blk_["per_day"][day]
            print(f"[rule_replay_events] {day} {rid:<10} trades {s['trades']:4d} cost {s['cost']:8.2f} net {s['net']:+8.3f} "
                  f"no_fill {s['no_fill']} assumed_size {s['assumed']} unsettled {s['unsettled']}", file=sys.stderr)
    if verbose:
        print(f"[rule_replay_events] {day}: rows {n_in} used {n_used} in {time.time() - t0:.1f} s", file=sys.stderr)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens", help="5m tokens map json")
    ap.add_argument("--tokens15", default=None)
    ap.add_argument("--rules", required=True, help="json list of rules or {'hypotheses': [...]}")
    ap.add_argument("--day", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--in", dest="inp", default=None, help="input BME csv(.gz); default stdin")
    ap.add_argument("--lat", type=float, default=0.1)
    ap.add_argument("--spot-lag", type=float, default=SPOT_LAG)
    ap.add_argument("--no-blackout", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    with open(a.rules, "r", encoding="utf-8") as f:
        rules = json.load(f)
    if isinstance(rules, dict):
        rules = rules.get("hypotheses") or rules.get("rules") or []
    res = replay(rules, a.inp, a.day, a.tokens, a.tokens15, lat=a.lat, spot_lag=a.spot_lag, blackout=not a.no_blackout)
    if not a.dry_run:
        from qdesk.common.schemas import save_json
        save_json(a.out, res)
        print(f"[rule_replay_events] wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
