#!/usr/bin/env python3
"""rule_engine_1s.py - tier-1 rule engine over the per-second feature store (INTERFACES.md section 8).

run(rules, days, data_dir, lat=0.1, tokens=None) -> results dict (section 4 schema, engine "rule_engine_1s").

Per day the feature CSV is loaded ONCE into numpy column arrays, Up and Down rows are aligned on (start, t_el),
every rule's entry conditions are evaluated vectorised (NA -> False, same semantics as common/dsl.eval_when, which
is also run on every fired row as a cross-check), then a sequential pass applies the limits and the fill models:

  taker  (MEASURED fill model "taker_ask_ok_100ms"): fires at second s when the conditions hold AND ask_ok_100ms == 1
         (the ask was still <= the ask of s at the first event after s + 0.1); size = min(size_usd / ask, ask_sz_100ms);
         fee = fees.taker_fee. The same instant/size signal_taker_replay.step() uses on raw events.
  maker  (ESTIMATE fill model "maker_1s_queue(ESTIMATE)"): limit at bb - offset_ticks cents; queue_ahead = bid_lvl_sz of
         the post second (proxy for the level at s + 0.1); every later second the taker hits AT our level
         (sell_at_bb_next1s) eat the queue first, hits THROUGH the level (sell_thru_bb_next1s) fill us first, a
         shrinking level shrinks the queue; cancelled after ttl_s. Fee 0; the maker rebate is reported beside the
         pnl, never inside it.
  exits  hold_to_settle: payout from the winner of the tokens map (settle.settle_pnl);
         ttl: sold at the bid of second s + ttl_s as a taker (fee), whole position (tier 2 checks the bid size);
         rule: first later second where exit.when holds -> sold at that bid as a taker; otherwise settlement.
  limits max_trades_per_round, cooldown_s (per rule, from the last FILL), max_rounds_per_day; one open position per
         (round, side) for ttl/rule exits.
  R = pnl / size_usd of the leg (the capital the rule asked for), so a partial fill does not inflate R.

Rows whose winner is unknown are skipped (counted in per_day["unsettled"]).
"""
import csv
import glob
import gzip
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    import numpy as np
except ImportError as e:  # pragma: no cover
    raise ImportError("numpy is required by quant/rule_engine_1s.py (pip install numpy in the qdesk venv): %s" % e)

from qdesk.features.features import FEATURE_COLUMNS, STRING_COLUMNS, T_OF  # noqa: E402

ENGINE = "rule_engine_1s"
SPOT_LAG = 1.44
NUMERIC_COLUMNS = tuple(c for c in FEATURE_COLUMNS if c not in STRING_COLUMNS)


def _common():
    try:
        from qdesk.common import dsl, fees, settle
    except ImportError as e:
        raise ImportError("qdesk.common (dsl, fees, settle) is required by quant/rule_engine_1s.py: %s" % e)
    return dsl, fees, settle


# ----------------------------------------------------------------------------- data
def feature_path(data_dir, day):
    return os.path.join(data_dir, "features", f"day_{day}.csv.gz")


def load_day(path):
    """feature csv(.gz) -> {"n": int, "cols": {col: np.ndarray}, "market": list, "side": list}"""
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        if tuple(header) != FEATURE_COLUMNS:
            raise ValueError(f"{path}: header differs from FEATURE_COLUMNS")
        rows = list(rd)
    n = len(rows)
    cols = {}
    idx = {c: i for i, c in enumerate(FEATURE_COLUMNS)}
    for c in NUMERIC_COLUMNS:
        i = idx[c]
        arr = np.empty(n, dtype=float)
        for j, r in enumerate(rows):
            v = r[i]
            arr[j] = float(v) if v != "" else np.nan
        cols[c] = arr
    out = {"n": n, "cols": cols}
    for c in STRING_COLUMNS:
        i = idx[c]
        out[c] = [r[i] for r in rows]
    return out


def align_sides(day, market):
    """index arrays iu, idn (same length) of Up / Down rows of `market`, aligned on (start, t_el), time ordered."""
    mk = day["market"]
    sd = day["side"]
    st = day["cols"]["start"]
    te = day["cols"]["t_el"]
    up = {}
    dn = {}
    for j in range(day["n"]):
        if mk[j] != market:
            continue
        key = (int(st[j]), int(te[j]))
        if sd[j] == "Up":
            up[key] = j
        elif sd[j] == "Down":
            dn[key] = j
    keys = sorted(set(up) & set(dn))
    iu = np.array([up[k] for k in keys], dtype=int)
    idn = np.array([dn[k] for k in keys], dtype=int)
    return keys, iu, idn


def load_tokens(data_dir, tokens=None):
    """-> {(market, start): winner}. tokens: dict | path | list of paths | None (data_dir/tokens/*.json)."""
    paths = []
    maps = []
    if isinstance(tokens, dict):
        maps.append(("5m", tokens))
    elif isinstance(tokens, str):
        paths = [tokens]
    elif isinstance(tokens, (list, tuple)):
        paths = list(tokens)
    else:
        paths = sorted(glob.glob(os.path.join(data_dir, "tokens", "*.json")))
    for p in paths:
        mk = "15m" if "15m" in os.path.basename(p) else "5m"
        try:
            with open(p, "r", encoding="utf-8") as f:
                maps.append((mk, json.load(f)))
        except (OSError, ValueError):
            continue
    win = {}
    for mk, m in maps:
        if not isinstance(m, dict):
            continue
        for asset, tk in m.items():
            if not isinstance(tk, dict) or "start" not in tk:
                continue
            w = tk.get("winner")
            mkk = tk.get("market") or mk
            if w in ("Up", "Down"):
                win[(mkk, int(tk["start"]))] = w
    return win


# ----------------------------------------------------------------------------- vectorised DSL
def _vec_field(f, C, i_self, i_other):
    pre, base = (f.split(":", 1) if (":" in f and f.split(":", 1)[0] in ("abs", "other")) else (None, f))
    if pre == "other":
        return _vec_field(base, C, i_other, i_self)
    col = {"ask": "ba", "bid": "bb"}.get(base, base)
    if col not in C:
        return np.full(len(i_self), np.nan)
    v = C[col][i_self]
    return np.abs(v) if pre == "abs" else v


def _vec_cond(c, C, i_self, i_other):
    x = _vec_field(c["f"], C, i_self, i_other)
    op, v = c["op"], c["v"]
    ok = ~np.isnan(x)
    with np.errstate(invalid="ignore"):
        if op == "<":
            m = x < v
        elif op == "<=":
            m = x <= v
        elif op == ">":
            m = x > v
        elif op == ">=":
            m = x >= v
        elif op == "==":
            m = np.abs(x - v) < 1e-9
        elif op == "in":
            m = np.zeros(len(x), dtype=bool)
            for y in v:
                m |= np.abs(x - float(y)) < 1e-9
        elif op == "between":
            m = (x >= v[0]) & (x <= v[1])
        else:
            m = np.zeros(len(x), dtype=bool)
    return m & ok


def vec_when(when, C, i_self, i_other):
    m = np.ones(len(i_self), dtype=bool)
    for c in when or []:
        m &= _vec_cond(c, C, i_self, i_other)
    return m


def rule_fields(rule):
    """DSL field names the rule reads (entry + side_field + exit.when); used for ctx."""
    out = []
    for c in ((rule.get("entry") or {}).get("when") or []):
        out.append(c["f"])
    if rule.get("side") == "signal_sign" and rule.get("side_field"):
        out.append(rule["side_field"])
    ex = rule.get("exit")
    if isinstance(ex, dict) and ex.get("mode") == "rule":
        for c in ex.get("when") or []:
            out.append(c["f"])
    seen = []
    for f in out:
        if f not in seen:
            seen.append(f)
    return seen


def row_dict(C, j):
    """feature row j as a dict (floats; NA -> None) for dsl.eval_when and ctx."""
    d = {}
    for c in NUMERIC_COLUMNS:
        v = C[c][j]
        d[c] = None if (isinstance(v, float) and math.isnan(v)) else float(v)
    return d


def _nz(v):
    return None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)


# ----------------------------------------------------------------------------- engine
class _RuleDayRunner:
    def __init__(self, rule, day_str, D, keys, iu, idn, winners, lat, dsl, fees, settle):
        self.rule = rule
        self.day = day_str
        self.D = D
        self.C = D["cols"]
        self.keys = keys
        self.iu = iu
        self.idn = idn
        self.winners = winners
        self.lat = lat
        self.dsl, self.fees, self.settle = dsl, fees, settle
        self.market = rule.get("market", "5m")
        self.T = T_OF[self.market]
        self.leg = rule["entry"]["leg"]
        self.size_usd = float(self.leg["size_usd"])
        self.exit = rule.get("exit") if isinstance(rule.get("exit"), dict) else {"mode": "hold_to_settle"}
        self.lim = rule.get("limits") or {}
        self.pos = {}                     # key (start, t_el) -> position in aligned arrays
        for p, k in enumerate(keys):
            self.pos[k] = p
        self.fields = rule_fields(rule)
        self.trades = []
        self.n_nofill = 0
        self.n_unsettled = 0
        self.n_dsl_mismatch = 0

    # ---- helpers
    def idx(self, side, p):
        return int(self.iu[p]) if side == "Up" else int(self.idn[p])

    def idx_other(self, side, p):
        return int(self.idn[p]) if side == "Up" else int(self.iu[p])

    def at(self, col, side, p):
        return float(self.C[col][self.idx(side, p)])

    def pos_of(self, start, t_el):
        return self.pos.get((start, t_el))

    def ctx(self, side, p):
        r = row_dict(self.C, self.idx(side, p))
        o = row_dict(self.C, self.idx_other(side, p))
        out = {}
        for f in self.fields:
            out[f] = _nz(self.dsl.field_value(f, r, o))
        out["ask"] = r.get("ba")
        out["bid"] = r.get("bb")
        return out

    def winner(self, start):
        return self.winners.get((self.market, start))

    # ---- candidates
    def candidates(self):
        C, iu, idn = self.C, self.iu, self.idn
        when = self.rule["entry"].get("when") or []
        side = self.rule.get("side")
        m_up = vec_when(when, C, iu, idn)
        m_dn = vec_when(when, C, idn, iu)
        if side == "up":
            return [("Up", m_up)]
        if side == "down":
            return [("Down", m_dn)]
        if side == "both":
            return [("both", m_up & m_dn)]
        if side == "signal_sign":
            sig = _vec_field(self.rule["side_field"], C, iu, idn)
            with np.errstate(invalid="ignore"):
                return [("Up", m_up & (sig > 0)), ("Down", m_dn & (sig < 0))]
        return []

    def exit_mask(self, side):
        ex = self.exit
        if ex.get("mode") != "rule":
            return None
        if side == "Up":
            return vec_when(ex.get("when") or [], self.C, self.iu, self.idn)
        return vec_when(ex.get("when") or [], self.C, self.idn, self.iu)

    # ---- fills
    def taker_fill(self, side, p):
        """-> (price, size, fee, fill_status) or None when nothing filled."""
        ask = self.at("ba", side, p)
        ok = self.at("ask_ok_100ms", side, p)
        asz = self.at("ask_sz_100ms", side, p)
        if math.isnan(ask) or ask <= 0:
            return None
        mp = self.leg.get("max_price")
        if mp is not None and ask > float(mp) + 1e-9:
            return None
        if math.isnan(ok) or ok != 1 or math.isnan(asz) or asz <= 0:
            self.n_nofill += 1
            return None
        want = self.size_usd / ask
        size = min(want, asz)
        fee = self.fees.taker_fee(ask, size)
        return ask, size, fee, ("full" if size >= want - 1e-9 else "partial")

    def maker_fill(self, side, p, price_cap=None):
        """ESTIMATE 1-s queue model. -> (price, size, fee=0, fill_status, fill_t_el) or None."""
        bb = self.at("bb", side, p)
        if math.isnan(bb):
            return None
        off = int(self.leg.get("offset_ticks", 0) or 0)
        price = round(bb - off / 100.0, 2)
        if price_cap is not None:
            price = min(price, round(math.floor(price_cap * 100 + 1e-9) / 100.0, 2))
        if price < 0.01:
            return None
        want = self.size_usd / price
        rem = want
        filled = 0.0
        qa = self.at("bid_lvl_sz", side, p)          # proxy for the level size at s + 0.1
        if math.isnan(qa):
            qa = 0.0
        ttl = float(self.leg.get("ttl_s") or 60)
        start, t0 = self.keys[p]
        first_fill = None
        k = p
        last_t = t0
        while rem > 1e-9:
            if k >= len(self.keys) or self.keys[k][0] != start:
                break
            _, tk = self.keys[k]
            if tk - t0 > ttl:
                break
            bbk = self.at("bb", side, k)
            at_bb = self.at("sell_at_bb_next1s", side, k)
            thru = self.at("sell_thru_bb_next1s", side, k)
            at_bb = 0.0 if math.isnan(at_bb) else at_bb
            thru = 0.0 if math.isnan(thru) else thru
            f = 0.0
            if not math.isnan(bbk):
                if abs(bbk - price) < 1e-9:
                    lvl = self.at("bid_lvl_sz", side, k)
                    if not math.isnan(lvl) and lvl < qa:
                        qa = lvl                                     # shrinking level shrinks the queue
                    if qa >= at_bb:
                        qa -= at_bb
                    else:
                        f += min(rem, at_bb - qa)
                        qa = 0.0
                    f += min(rem - f, thru)                          # through our price: we fill first
                elif bbk > price:
                    # hits below the touch may be at our level: they eat the queue first (ESTIMATE)
                    if qa >= thru:
                        qa -= thru
                    else:
                        f += min(rem, thru - qa)
                        qa = 0.0
                else:
                    f += min(rem, at_bb + thru)                      # we are the best bid now: every hit is ours
            if f > 0:
                filled += f
                rem -= f
                if first_fill is None:
                    first_fill = tk + 1
            last_t = tk + 1
            k += 1
        if filled <= 1e-9:
            self.n_nofill += 1
            return None
        return price, filled, 0.0, ("full" if rem <= 1e-9 else "partial"), (first_fill if first_fill is not None else last_t)

    # ---- exits
    def close(self, side, p_entry, price, size, fee_in, start, exit_mask):
        """-> dict(exit_kind, exit_t_el, exit_price, proceeds, fee_out, winner, pnl)"""
        ex = self.exit
        mode = ex.get("mode", "hold_to_settle")
        w = self.winner(start)
        t0 = self.keys[p_entry][1]
        if mode == "ttl":
            pe = self.pos_of(start, int(t0 + float(ex["ttl_s"])))
            if pe is not None:
                bid = self.at("bb", side, pe)
                if not math.isnan(bid) and bid > 0:
                    fo = self.fees.taker_fee(bid, size)
                    return {"exit": "ttl", "exit_t_el": self.keys[pe][1], "exit_price": bid, "proceeds": bid * size,
                            "fee_out": fo, "winner": w, "pnl": bid * size - fo - price * size - fee_in}
        elif mode == "rule" and exit_mask is not None:
            k = p_entry + 1
            while k < len(self.keys) and self.keys[k][0] == start:
                if exit_mask[k]:
                    bid = self.at("bb", side, k)
                    if not math.isnan(bid) and bid > 0:
                        fo = self.fees.taker_fee(bid, size)
                        return {"exit": "rule", "exit_t_el": self.keys[k][1], "exit_price": bid, "proceeds": bid * size,
                                "fee_out": fo, "winner": w, "pnl": bid * size - fo - price * size - fee_in}
                k += 1
        if w not in ("Up", "Down"):
            return None
        st = self.settle.settle_pnl([{"side": side, "price": price, "size": size, "fee": fee_in}], w)
        return {"exit": "settle", "exit_t_el": self.T, "exit_price": 1.0 if w == side else 0.0, "proceeds": st["payout"],
                "fee_out": 0.0, "winner": w, "pnl": st["net"]}

    # ---- main
    def run(self):
        cands = self.candidates()
        masks = {s: m for s, m in cands}
        any_mask = np.zeros(len(self.keys), dtype=bool)
        for m in masks.values():
            any_mask |= m
        exit_masks = {"Up": self.exit_mask("Up"), "Down": self.exit_mask("Down")}
        max_tr = int(self.lim.get("max_trades_per_round", 1))
        cooldown = float(self.lim.get("cooldown_s", 0))
        max_rounds = int(self.lim.get("max_rounds_per_day", 10 ** 9))
        rounds_traded = set()
        per_round = {}
        open_until = {}            # (start, side) -> t_el until which the position is open (ttl/rule exits)
        last_fill_t = -1e18
        leg_type = self.leg.get("type")
        for p in np.nonzero(any_mask)[0]:
            start, t_el = self.keys[p]
            t_abs = start + t_el
            if per_round.get(start, 0) >= max_tr:
                continue
            if start not in rounds_traded and len(rounds_traded) >= max_rounds:
                continue
            if t_abs - last_fill_t < cooldown:
                continue
            sides = []
            if masks.get("both") is not None and masks["both"][p]:
                sides = ["Up", "Down"]
            else:
                for s in ("Up", "Down"):
                    if masks.get(s) is not None and masks[s][p]:
                        sides.append(s)
            if not sides:
                continue
            if any(open_until.get((start, s), -1) >= t_el for s in sides):
                continue
            # cross-check the vectorised evaluation against the shared DSL on this row
            ru = row_dict(self.C, int(self.iu[p]))
            rd = row_dict(self.C, int(self.idn[p]))
            cs = self.dsl.chosen_side(self.rule, ru, rd)
            if cs is None or (cs != "both" and cs not in sides) or (cs == "both" and sides != ["Up", "Down"]):
                self.n_dsl_mismatch += 1
                continue
            legs = []
            pair_cap = self.leg.get("pair_cap") if len(sides) == 2 else None
            for s in sides:
                if leg_type == "taker":
                    f = self.taker_fill(s, p)
                    if f is None:
                        continue
                    price, size, fee, status = f
                    fill_t_el = t_el
                else:
                    cap = None
                    if pair_cap is not None and legs:
                        cap = float(pair_cap) - legs[0]["price"]
                    f = self.maker_fill(s, p, cap)
                    if f is None:
                        continue
                    price, size, fee, status, fill_t_el = f
                legs.append({"side": s, "price": price, "size": size, "fee": fee, "fill": status, "fill_t_el": fill_t_el})
            if not legs:
                continue
            got = False
            for lg in legs:
                s = lg["side"]
                cl = self.close(s, p, lg["price"], lg["size"], lg["fee"], start, exit_masks[s])
                if cl is None:
                    self.n_unsettled += 1
                    continue
                got = True
                pnl = cl["pnl"]
                tr = {"day": self.day, "start": int(start), "side": s, "t_el": int(t_el), "leg": leg_type,
                      "price": round(lg["price"], 4), "size": round(lg["size"], 6), "fill": lg["fill"],
                      "fee": round(lg["fee"] + cl["fee_out"], 5), "winner": cl["winner"], "pnl": round(pnl, 6),
                      "R": round(pnl / self.size_usd, 6), "cost": round(lg["price"] * lg["size"], 6),
                      "proceeds": round(cl["proceeds"], 6), "exit": cl["exit"], "exit_t_el": cl["exit_t_el"],
                      "exit_price": cl["exit_price"], "fill_t_el": lg["fill_t_el"],
                      "ctx": self.ctx(s, p)}
                if leg_type == "maker":
                    tr["rebate"] = self.fees.maker_rebate(lg["price"], lg["size"])
                self.trades.append(tr)
                if cl["exit"] in ("ttl", "rule"):
                    open_until[(start, s)] = cl["exit_t_el"]
                else:
                    open_until[(start, s)] = self.T
            if got:
                per_round[start] = per_round.get(start, 0) + 1
                rounds_traded.add(start)
                last_fill_t = t_abs
        return self.trades


def per_day_summary(trades):
    cost = sum(t["cost"] for t in trades)
    fees = sum(t["fee"] for t in trades)
    net = sum(t["pnl"] for t in trades)
    return {"trades": len(trades), "cost": round(cost, 6), "gross": round(net + fees, 6), "fees": round(fees, 6),
            "net": round(net, 6), "rounds": len({t["start"] for t in trades})}


def aggregate(trades, boot=True):
    """{"net_per_dollar", "ci95": [lo, hi] of net per dollar (bootstrap of the SUM of pnl / total cost), "n"}"""
    n = len(trades)
    cost = sum(t["cost"] for t in trades)
    net = sum(t["pnl"] for t in trades)
    npd = (net / cost) if cost > 0 else None
    ci = [None, None]
    if boot and n >= 2 and cost > 0:
        try:
            from qdesk.common.stats import boot_ci
            lo, hi = boot_ci([t["pnl"] for t in trades])
            ci = [round(lo / cost, 6), round(hi / cost, 6)]
        except ImportError:
            pass
    return {"net_per_dollar": round(npd, 6) if npd is not None else None, "ci95": ci, "n": n}


def run(rules, days, data_dir, lat=0.1, tokens=None, verbose=True):
    """tier 1 over the feature store. rules: list of DSL rules; days: ['YYYYMMDD', ...]; tokens: see load_tokens."""
    dsl, fees, settle = _common()
    winners = load_tokens(data_dir, tokens)
    rules = [r for r in rules if not dsl.validate_rule(r)]
    res = {"engine": ENGINE, "lat": lat, "spot_lag": SPOT_LAG, "days": sorted(days), "rules": {}}
    for r in rules:
        fm = "taker_ask_ok_100ms" if r["entry"]["leg"]["type"] == "taker" else "maker_1s_queue(ESTIMATE)"
        res["rules"][r["id"]] = {"per_day": {}, "trades": [], "fit": None, "test": None, "windows": [],
                                 "mc_dd": {"p50": 0.0, "p95": 0.0, "n": 1000}, "fill_model": fm, "rule_hash": dsl.canonical_hash(r)}
    for day in sorted(days):
        p = feature_path(data_dir, day)
        if not os.path.exists(p):
            if verbose:
                print(f"[rule_engine_1s] {day}: no feature file {p}, skipped", file=sys.stderr)
            continue
        D = load_day(p)
        aligned = {}
        for r in rules:
            mk = r.get("market", "5m")
            if mk not in aligned:
                aligned[mk] = align_sides(D, mk)
            keys, iu, idn = aligned[mk]
            if not keys:
                res["rules"][r["id"]]["per_day"][day] = dict(per_day_summary([]), no_fill=0, unsettled=0)
                continue
            rr = _RuleDayRunner(r, day, D, keys, iu, idn, winners, lat, dsl, fees, settle)
            trades = rr.run()
            blk = res["rules"][r["id"]]
            blk["trades"].extend(trades)
            blk["per_day"][day] = dict(per_day_summary(trades), no_fill=rr.n_nofill, unsettled=rr.n_unsettled,
                                       dsl_mismatch=rr.n_dsl_mismatch)
            if verbose:
                s = blk["per_day"][day]
                print(f"[rule_engine_1s] {day} {r['id']:<10} trades {s['trades']:4d} cost {s['cost']:8.2f} net {s['net']:+8.3f} "
                      f"no_fill {rr.n_nofill} unsettled {rr.n_unsettled} dsl_mismatch {rr.n_dsl_mismatch}", file=sys.stderr)
    for rid, blk in res["rules"].items():
        blk["fit"] = aggregate(blk["trades"])
        try:
            from qdesk.common.stats import mc_drawdown
            blk["mc_dd"] = mc_drawdown([t["pnl"] for t in blk["trades"]])
        except ImportError:
            pass
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True, help="json: list of rules or {'hypotheses': [...]}")
    ap.add_argument("--days", nargs="+", required=True)
    ap.add_argument("--data-dir", default=os.environ.get("QD_DATA", os.path.expanduser("~/qdesk/data")))
    ap.add_argument("--tokens", nargs="*", default=None, help="tokens map json path(s); default data_dir/tokens/*.json")
    ap.add_argument("--lat", type=float, default=0.1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    with open(a.rules, "r", encoding="utf-8") as f:
        rules = json.load(f)
    if isinstance(rules, dict):
        rules = rules.get("hypotheses") or rules.get("rules") or []
    res = run(rules, a.days, a.data_dir, lat=a.lat, tokens=(a.tokens if a.tokens else None))
    if a.out and not a.dry_run:
        from qdesk.common.schemas import save_json
        save_json(a.out, res)
        print(f"[rule_engine_1s] wrote {a.out}", file=sys.stderr)
    for rid, blk in res["rules"].items():
        print(f"{rid:<10} n {blk['fit']['n']:5d} net/$ {blk['fit']['net_per_dollar']} ci {blk['fit']['ci95']} mc_dd p95 {blk['mc_dd']['p95']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
