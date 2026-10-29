#!/usr/bin/env python3
"""paper_fill.py - paper fills for the qdesk v1 bot (INTERFACES section 9). No orders ever leave this process.

PaperBook keeps per-asset bid/ask level dicts from the CLOB market websocket (`book` snapshots and
`price_change` updates; best bid/ask from the message fields when given) and resolves two kinds of paper legs:

  taker  : limit at the ask seen at decision time; resolved `taker_delay` (100 ms) later: it fills iff the best ask
           then is still <= the limit, size = min(size, resting size at that ask), fee = taker_fee (fees.py).
           (same rule as wallet_audits/signal_taker_replay.step and the tier-1 engine's ask_ok_100ms)
  maker  : rests at bb - offset_ticks * tick; queue_ahead = resting size at that level at placement; consumed by the
           live `last_trade_price` tape: a taker SELL on this asset (or a BUY on the other asset at 1 - p) AT our
           price eats the queue first, BELOW our price fills us first; a level shrink caps queue_ahead; ttl cancel.
           (queue rules copied from paired_maker/pm_replay.py hit/land)

Trades are deduped by (timestamp, price, size) because the ws may repeat a last_trade_price message.
"""
import collections, itertools

TICK = 0.01
TAKER_DELAY = 0.1
_FEE = None


def _taker_fee_formula(price, shares):
    """fallback = the formula frozen in INTERFACES section 5 (fees.taker_fee); the real module is preferred."""
    return round(0.07 * price * (1 - price) * shares, 5)


def taker_fee(price, shares):
    global _FEE
    if _FEE is None:
        try:
            from qdesk.common.fees import taker_fee as f
            _FEE = f
        except ImportError:
            _FEE = _taker_fee_formula
    return _FEE(price, shares)


def _levels(seq):
    """accept [(price,size)] or [{"price","size"}] (ws book snapshot shape)."""
    out = {}
    for x in seq or []:
        if isinstance(x, dict):
            p, s = float(x["price"]), float(x.get("size") or 0)
        else:
            p, s = float(x[0]), float(x[1])
        if s > 0:
            out[p] = s
    return out


class PaperBook:
    def __init__(self, fee_fn=None, taker_delay=TAKER_DELAY, tick=TICK, dedupe_max=50000):
        self.bids = collections.defaultdict(dict)      # asset -> {price: size}
        self.asks = collections.defaultdict(dict)
        self.bb = {}; self.ba = {}                      # best bid/ask per asset (from message fields)
        self.pair = {}                                  # asset -> the other outcome's asset
        self.orders = {}                                # order_id -> order dict
        self.fills = []                                 # every fill: ts, order_id, leg, side, asset, price, size, fee, status
        self._seen = collections.OrderedDict()          # (timestamp, price, size) -> 1
        self._dedupe_max = dedupe_max
        self._seq = itertools.count(1)
        self.fee_fn = fee_fn or taker_fee
        self.taker_delay = taker_delay; self.tick = tick
        self.n_trades = 0; self.n_dupes = 0

    # ---------- pair + book state
    def set_pair(self, a, b):
        self.pair[a] = b; self.pair[b] = a

    def on_book(self, asset, bids, asks, t, bb=None, ba=None):
        """full snapshot: replaces the levels; best from the fields when given, else from the fresh snapshot."""
        self.bids[asset] = _levels(bids); self.asks[asset] = _levels(asks)
        nb = bb if bb else (max(self.bids[asset]) if self.bids[asset] else None)
        na = ba if ba else (min(self.asks[asset]) if self.asks[asset] else None)
        if nb is not None: self.bb[asset] = float(nb)
        if na is not None: self.ba[asset] = float(na)
        for o in self._live_makers(asset):
            o["qa"] = min(o["qa"], self.bids[asset].get(o["price"], 0.0))

    def on_price_change(self, asset, side, price, size, t, bb=None, ba=None):
        """one level change; side BUY = bid level, SELL = ask level (size 0 = removed); bb/ba from the message."""
        price = round(float(price), 4); size = float(size or 0)
        L = self.bids[asset] if side == "BUY" else self.asks[asset]
        if size <= 0: L.pop(price, None)
        else: L[price] = size
        if bb: self.bb[asset] = float(bb)
        if ba: self.ba[asset] = float(ba)
        if side == "BUY":
            for o in self._live_makers(asset):
                if abs(o["price"] - price) < 1e-9 and o["qa"] > size:
                    o["qa"] = size                      # level shrank: nobody can be ahead of us beyond what rests

    # ---------- tape (last_trade_price)
    def on_trade(self, asset, side, price, size, ts, t):
        """taker trade on `asset`: SELL at p hits this asset's bids at p; BUY at p hits the OTHER asset's bids at 1-p.
        Returns the list of maker fills it produced. Deduped by (ts, price, size)."""
        price = float(price); size = float(size or 0)
        key = (str(ts), round(price, 4), round(size, 4))
        if key in self._seen:
            self.n_dupes += 1; return []
        self._seen[key] = 1
        if len(self._seen) > self._dedupe_max:
            self._seen.popitem(last=False)
        self.n_trades += 1
        if size <= 0: return []
        if side == "SELL":
            hit_asset, hit_price = asset, price
        elif side == "BUY":
            hit_asset = self.pair.get(asset)
            if hit_asset is None: return []
            hit_price = round(1.0 - price, 4)
        else:
            return []
        fills = []
        for o in self._live_makers(hit_asset):
            f = self._hit(o, hit_price, size, t)
            if f: fills.append(f)
        return fills

    def _hit(self, o, price, size, t):
        # copied from C:\g\tlfix\paired_maker\pm_replay.py (hit): at our price the queue ahead eats first, below fills us first
        if price < o["price"] - 1e-9:
            f = min(o["rem"], size)
        elif abs(price - o["price"]) < 1e-9:
            if o["qa"] >= size:
                o["qa"] -= size; return None
            f = min(o["rem"], size - o["qa"]); o["qa"] = 0.0
        else:
            return None
        if f <= 0: return None
        return self._fill(o, o["price"], f, t, maker=True)

    # ---------- orders
    def submit(self, order, t):
        """order: {leg:"taker"|"maker", asset, side:"Up"|"Down", size (shares), price (taker: limit = the ask seen;
        maker: optional explicit resting price), offset_ticks (maker), ttl_s (maker), ...meta}. Returns order_id."""
        o = dict(order)
        oid = "p%d" % next(self._seq)
        o.update(id=oid, t_submit=t, filled=0.0, cost=0.0, fee=0.0, fills=[], t_done=None)
        a = o["asset"]; o["size"] = float(o["size"])
        if o.get("leg") == "taker":
            lim = o.get("price")
            if lim is None: lim = self.ba.get(a)
            if lim is None:
                o["status"] = "none"; o["why"] = "no ask"; o["t_done"] = t
            else:
                o["limit"] = float(lim); o["status"] = "pending"; o["resolve_t"] = t + self.taker_delay
        else:
            o["leg"] = "maker"
            if o.get("price") is None:
                b = self.bb.get(a)
                if b is None:
                    o["status"] = "none"; o["why"] = "no bid"; o["t_done"] = t; self.orders[oid] = o; return oid
                o["price"] = round(b - int(o.get("offset_ticks") or 0) * self.tick, 2)
            o["price"] = round(float(o["price"]), 2)
            o["rem"] = o["size"]; o["qa"] = float(self.bids[a].get(o["price"], 0.0))
            o["expire_t"] = (t + float(o["ttl_s"])) if o.get("ttl_s") else None
            o["status"] = "resting"
        self.orders[oid] = o
        return oid

    def on_tick(self, t):
        """resolve pending takers (after taker_delay) and expire makers past their ttl. Returns new fills."""
        out = []
        for o in self.orders.values():
            st = o["status"]
            if st == "pending" and t >= o["resolve_t"]:
                a = o["asset"]; ask = self.ba.get(a)
                if ask is not None and ask <= o["limit"] + 1e-9:
                    lvl = float(self.asks[a].get(round(ask, 4), 0.0))
                    f = min(o["size"], lvl or o["size"])
                    if f > 0:
                        out.append(self._fill(o, ask, f, t, maker=False))
                        o["status"] = "full" if f >= o["size"] - 1e-9 else "partial"
                    else:
                        o["status"] = "none"; o["why"] = "empty level"
                else:
                    o["status"] = "none"; o["why"] = "ask %s > limit %s" % (ask, o["limit"])
                o["t_done"] = t
            elif st == "resting" and o["expire_t"] is not None and t >= o["expire_t"]:
                o["status"] = "partial" if o["filled"] > 0 else "expired"; o["why"] = "ttl"; o["t_done"] = t
        return out

    def cancel(self, oid, t, why="cancel"):
        o = self.orders.get(oid)
        if o and o["status"] in ("resting", "pending"):
            o["status"] = "partial" if o["filled"] > 0 else "cancelled"; o["why"] = why; o["t_done"] = t
            return True
        return False

    def cancel_all(self, t, why="cancel", asset=None):
        n = 0
        for oid, o in list(self.orders.items()):
            if asset is None or o["asset"] == asset:
                n += self.cancel(oid, t, why)
        return n

    def _fill(self, o, price, size, t, maker):
        fee = 0.0 if maker else self.fee_fn(price, size)
        o["filled"] += size; o["cost"] += price * size; o["fee"] += fee
        if maker:
            o["rem"] -= size
            if o["rem"] <= 1e-9:
                o["status"] = "full"; o["t_done"] = t
        f = dict(ts=t, order_id=o["id"], leg=o["leg"], side=o.get("side"), asset=o["asset"], price=price, size=size,
                 fee=fee, status=o["status"], fill_ms=round((t - o["t_submit"]) * 1000, 1))
        o["fills"].append(f); self.fills.append(f)
        return f

    def _live_makers(self, asset):
        return [o for o in self.orders.values() if o["leg"] == "maker" and o["status"] == "resting" and o["asset"] == asset]

    def live_orders(self, asset=None):
        return [o for o in self.orders.values() if o["status"] in ("resting", "pending") and (asset is None or o["asset"] == asset)]

    def status(self, oid):
        o = self.orders.get(oid)
        return o["status"] if o else None

    def forget(self, oids):
        for oid in oids:
            self.orders.pop(oid, None)
