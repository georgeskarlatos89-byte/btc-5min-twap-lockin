#!/usr/bin/env python3
"""features.py - the ONE per-second feature definition of qdesk (INTERFACES.md section 2).

FeatureState keeps the order book / tape / spot / twap / external state of ONE round (market "5m" or "15m",
both tokens) and emits one row per (side, integer second s) once events up to s + 1.2 s have been seen
(the 100 ms and 1 s look-ahead helpers need that).  The same class drives the history build
(features/bme_to_features.py), the live recorder (recorder/feat_recorder.py) and the paper bot (bot/qbot.py),
so every engine sees the same numbers.

Book / tape rules copied from v2-open-maker-inspect-order-book/inspect_lab.py (levels from price_change rows,
bb/ba ONLY from the best_bid/best_ask columns, trades recorded once and mirrored to the other token, sweep rule);
spot source-lag queue copied from paired_maker/pm_replay.py (spot_q, +1.44 s); opens from the first twap_tick
within 3 s after the round start (pm_replay.py).

State at second s = last event with t <= s.  Per-event work is O(1)-ish (dicts + deques); no numpy here.

Look-ahead helpers (resolved by later events, see _after_event / flush):
  ask_ok_100ms / ask_sz_100ms : state after the first event with t >= s + 0.1 (same instant signal_taker_replay.step()
                                checks a pending limit).  If NO event of this round arrives in (s, s + 1.2] the book
                                did not change, so ask_ok = 1 and ask_sz = the resting size at that ask.
  sell_at_bb_next1s / sell_thru_bb_next1s : taker hits on this side's bids in (s, s + 1] at / below the bb of s.
"""
import collections
import datetime as _dt
import math

FEATURE_COLUMNS = (
    "market", "start", "side", "asset", "t_el", "time_left", "bb", "ba", "bb_sz", "ba_sz", "spread", "mid",
    "imb3", "sum_ask2", "sum_bid2",
    "spot", "open_twap", "spot_bps_vs_open", "mom_2s", "mom_5s", "spot_vol_30s",
    "flow_sell_10s", "flow_buy_10s", "trade_n_10s", "sweep_flag",
    "ask_ok_100ms", "ask_sz_100ms", "bid_lvl_sz", "sell_at_bb_next1s", "sell_thru_bb_next1s",
    "session_hour", "dow", "macro_blackout", "liq_long_5m_usd", "liq_short_5m_usd", "hl_funding_1h", "hl_oi_usd", "src",
)
LOOKAHEAD_COLUMNS = ("ask_ok_100ms", "ask_sz_100ms", "sell_at_bb_next1s", "sell_thru_bb_next1s")
STRING_COLUMNS = ("market", "side", "asset", "src")
T_OF = {"5m": 300, "15m": 900}
SWEEP_SHARES = 30.0          # copied from inspect_lab.py (--sweep-sh default)
SWEEP_DROP = 0.02            # bb dropped >= 2c vs the bb one second earlier
LOOKAHEAD_S = 1.2            # a row for second s is complete once events up to s + 1.2 were seen
ASK_EMPTY = 0.995           # best_ask at/above this = no ask (mirror of an empty bid side)
LAT_CHECK = 0.1              # ask_ok_100ms: first event state at or after s + 0.1
SPOT_HIST_KEEP = 45.0        # seconds of received spot history kept (spot_vol_30s needs 31 s)


def good(p):
    # copied from v2-open-maker-inspect-order-book/inspect_lab.py
    return p is not None and p > 0.005


def _bps(new, old):
    return (new - old) / old * 1e4 if (new is not None and old) else None


def fmt_num(x):
    """number -> string with up to 6 decimals; None -> '' (NA); bool -> 0/1."""
    if x is None:
        return ""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return ""
        if x == int(x) and abs(x) < 1e15:
            return str(int(x))
        s = f"{x:.6f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def row_to_list(row):
    """feature dict -> list of strings in FEATURE_COLUMNS order (NA = empty string)."""
    return [str(row.get(c, "")) if c in STRING_COLUMNS else fmt_num(row.get(c)) for c in FEATURE_COLUMNS]


class _Pending:
    """a second s waiting for its look-ahead helpers (resolved by later events, emitted by flush)."""
    __slots__ = ("s", "rows", "ask_ref", "ask_ok", "ask_sz", "bb_ref")

    def __init__(self, s, rows, ask_ref, bb_ref):
        self.s = s
        self.rows = rows                  # {"Up": dict, "Down": dict}
        self.ask_ref = ask_ref            # {side: ba at s or None}
        self.ask_ok = {}                  # side -> 0/1/None once the first event at/after s+0.1 was seen
        self.ask_sz = {}
        self.bb_ref = bb_ref              # {side: bb at s or None}


class FeatureState:
    def __init__(self, market, start, up_asset, down_asset, spot_lag=1.44, blackout_fn=None, src="bme"):
        if market not in T_OF:
            raise ValueError("market must be '5m' or '15m'")
        self.market = market
        self.start = int(start)
        self.T = T_OF[market]
        self.end = self.start + self.T
        self.up_asset = str(up_asset)
        self.down_asset = str(down_asset)
        self.assets = {self.up_asset: "Up", self.down_asset: "Down"}
        self.asset_of = {"Up": self.up_asset, "Down": self.down_asset}
        self.other = {self.up_asset: self.down_asset, self.down_asset: self.up_asset}
        self.spot_lag = float(spot_lag)
        self.blackout_fn = blackout_fn
        self.src = src
        # book (per asset)
        self.levels_bid = {self.up_asset: {}, self.down_asset: {}}
        self.levels_ask = {self.up_asset: {}, self.down_asset: {}}
        self.bb = {}
        self.ba = {}
        self.bb_hist = {self.up_asset: collections.deque(), self.down_asset: collections.deque()}   # (t, bb)
        # tape: hits on THIS asset's bids (t, size, hit_price); lifts of THIS asset's asks (t, size)
        self.tape = {self.up_asset: collections.deque(), self.down_asset: collections.deque()}
        self.lifts = {self.up_asset: collections.deque(), self.down_asset: collections.deque()}
        # spot / twap
        self.spot_q = collections.deque()        # (t_recv, value)  copied from paired_maker/pm_replay.py
        self.spot_hist = collections.deque()     # (t_recv, value) received, kept SPOT_HIST_KEEP s
        self.spot = None
        self.open_twap = None
        # ext (forward-filled)
        self.ext = {"liq_long_5m_usd": None, "liq_short_5m_usd": None, "hl_funding_1h": None, "hl_oi_usd": None}
        # clock / rows
        self.last_t = None
        self.next_s = self.start                 # next integer second to snapshot
        self.pending = collections.deque()       # _Pending in increasing s
        self.n_emitted = 0
        self.n_events = 0

    # ------------------------------------------------------------------ clock
    def _advance(self, t):
        """an event at time t is about to be applied: snapshot every integer second s with next_s <= s < t."""
        if self.last_t is not None and t < self.last_t:
            t = self.last_t                      # out-of-order stamp: never go back
        lim = min(math.ceil(t), self.end)        # seconds start .. end-1 only
        while self.next_s < lim:
            self._snapshot(self.next_s)
            self.next_s += 1
        self.last_t = t

    def _apply_spot(self, upto):
        q = self.spot_q
        if q and q[0][0] <= upto:
            h = self.spot_hist
            while q and q[0][0] <= upto:
                tr, v = q.popleft()
                self.spot = v
                h.append((tr, v))
            while len(h) > 2 and h[1][0] < upto - SPOT_HIST_KEEP:
                h.popleft()

    def _after_event(self, t):
        """look-ahead helpers for pending seconds: first event state at or after s + 0.1."""
        for pnd in self.pending:
            if len(pnd.ask_ok) == 2:
                continue
            if t < pnd.s + LAT_CHECK:
                break                             # pending is ordered by s; later ones are even further away
            for asset, side in self.assets.items():
                if side in pnd.ask_ok:
                    continue
                k = self.ba.get(asset)
                ref = pnd.ask_ref[side]
                if ref is None:
                    pnd.ask_ok[side] = None
                    pnd.ask_sz[side] = None
                elif not good(k):                     # ask side emptied within 100 ms: a taker limit cannot fill
                    pnd.ask_ok[side] = 0
                    pnd.ask_sz[side] = 0.0
                else:
                    ok = 1 if k <= ref + 1e-9 else 0
                    pnd.ask_ok[side] = ok
                    pnd.ask_sz[side] = float(self.levels_ask[asset].get(k, 0.0)) if ok else 0.0

    # ------------------------------------------------------------------ event handlers (signatures of INTERFACES section 2)
    def on_price_change(self, asset, side, price, size, bb, ba, t):
        asset = str(asset)
        if asset not in self.assets:
            self.on_clock(t)
            return
        self._advance(t)
        self.n_events += 1
        p = round(float(price), 2)
        sz = float(size)
        L = self.levels_bid[asset] if side == "BUY" else self.levels_ask[asset]
        if sz <= 0:
            L.pop(p, None)
        else:
            L[p] = sz
        self._set_bba(asset, bb, ba, t)
        self._after_event(t)

    def on_bba(self, asset, bb, ba, t):
        asset = str(asset)
        if asset not in self.assets:
            self.on_clock(t)
            return
        self._advance(t)
        self.n_events += 1
        self._set_bba(asset, bb, ba, t)
        self._after_event(t)

    def _set_bba(self, asset, bb, ba, t):
        # bb/ba ONLY from the columns, never from the levels (inspect_lab.py rule). Empty string / None = no update.
        # An explicit best_bid <= 0.005 means the bid side is EMPTY (BME writes 0) -> bb becomes NA; an explicit
        # best_ask >= 0.995 (the mirror of an empty bid on the other token, written as 1.0) or <= 0.005 -> ba NA.
        if bb is not None and bb != "":
            b = float(bb)
            if good(b):
                if self.bb.get(asset) != b:
                    self.bb[asset] = b
                    self.bb_hist[asset].append((t, b))
            else:
                self.bb.pop(asset, None)
        if ba is not None and ba != "":
            k = float(ba)
            if good(k) and k < ASK_EMPTY:
                self.ba[asset] = k
            else:
                self.ba.pop(asset, None)

    def on_trade(self, asset, side, price, size, t):
        asset = str(asset)
        if asset not in self.assets:
            self.on_clock(t)
            return
        self._advance(t)
        self.n_events += 1
        p = float(price)
        sz = float(size)
        # copied from inspect_lab.py: SELL = taker hit THIS asset's bids at p; BUY = taker bought this asset
        # = hit the OTHER asset's bids at 1-p (and lifted this asset's asks)
        if side == "SELL":
            tgt, hp, lifted = asset, round(p, 2), self.other[asset]
        else:
            tgt, hp, lifted = self.other[asset], round(1 - p, 2), asset
        self.tape[tgt].append((t, sz, hp))
        self.lifts[lifted].append((t, sz))
        self._after_event(t)

    def on_book(self, asset, bids, asks, t):
        """full snapshot (live ws). Replaces the level dicts; bb/ba taken from the snapshot's best levels because
        a snapshot is the only source then (the recorder calls on_bba afterwards when the feed gives one)."""
        asset = str(asset)
        if asset not in self.assets:
            self.on_clock(t)
            return
        self._advance(t)
        self.n_events += 1
        lb = {}
        la = {}
        for p, s in bids:
            if float(s) > 0:
                lb[round(float(p), 2)] = float(s)
        for p, s in asks:
            if float(s) > 0:
                la[round(float(p), 2)] = float(s)
        self.levels_bid[asset] = lb
        self.levels_ask[asset] = la
        self._set_bba(asset, max(lb) if lb else 0.0, min(la) if la else 0.0, t)     # empty side -> NA
        self._after_event(t)

    def on_spot(self, t_source, value):
        # copied from paired_maker/pm_replay.py: the recorded tick carries the SOURCE time, we receive it spot_lag later
        self.spot_q.append((float(t_source) + self.spot_lag, float(value)))

    def on_twap(self, t, value):
        if self.open_twap is None and 0 <= t - self.start <= 3:
            self.open_twap = float(value)

    def on_ext(self, t, liq_long=None, liq_short=None, funding=None, oi=None):
        if liq_long is not None:
            self.ext["liq_long_5m_usd"] = float(liq_long)
        if liq_short is not None:
            self.ext["liq_short_5m_usd"] = float(liq_short)
        if funding is not None:
            self.ext["hl_funding_1h"] = float(funding)
        if oi is not None:
            self.ext["hl_oi_usd"] = float(oi)

    def on_clock(self, t):
        """an event of another asset of the same feed happened at t: advances the clock (snapshots) and resolves
        the look-ahead helpers; changes no book state."""
        self._advance(t)
        self._after_event(t)

    # ------------------------------------------------------------------ rows
    def _spot_at_or_before(self, t):
        h = self.spot_hist
        for i in range(len(h) - 1, -1, -1):
            if h[i][0] <= t:
                return h[i][1]
        return None

    def _spot_vol_30s(self, s):
        vals = []
        h = self.spot_hist
        j = len(h) - 1
        for k in range(31):                       # spot at or before s-k, k = 0..30
            tt = s - k
            while j >= 0 and h[j][0] > tt:
                j -= 1
            if j < 0:
                return None
            vals.append(h[j][1])
        ch = []
        for k in range(30):
            old = vals[k + 1]
            if not old:
                return None
            ch.append((vals[k] - old) / old * 1e4)
        m = sum(ch) / 30.0
        return math.sqrt(sum((c - m) ** 2 for c in ch) / 30.0)

    def _side_row(self, asset, s, t_el):
        side = self.assets[asset]
        other = self.other[asset]
        b = self.bb.get(asset)
        k = self.ba.get(asset)
        lb = self.levels_bid[asset]
        la = self.levels_ask[asset]
        bb_sz = float(lb.get(b, 0.0)) if good(b) else 0.0
        ba_sz = float(la.get(k, 0.0)) if good(k) else 0.0
        if good(b) and good(k):
            sb = sum(float(lb.get(round(b - i / 100, 2), 0.0)) for i in range(3))
            sa = sum(float(la.get(round(k + i / 100, 2), 0.0)) for i in range(3))
            imb3 = sb / (sb + sa) if (sb + sa) > 0 else 0.5
        else:
            imb3 = None
        ob = self.bb.get(other)
        ok = self.ba.get(other)
        sum_ask2 = (k + ok) if (good(k) and good(ok)) else None
        sum_bid2 = (b + ob) if (good(b) and good(ob)) else None
        # tape windows (s-10, s]; entries older than 10 s are dropped (the look-ahead needs only (s, s+1])
        fs = 0.0
        n = 0
        hit_1s = 0.0
        tp = self.tape[asset]
        while tp and tp[0][0] <= s - 10.0:
            tp.popleft()
        for (tt, sz, hp) in tp:
            if tt <= s:
                fs += sz
                n += 1
                if tt > s - 1.0:
                    hit_1s += sz
        fb = 0.0
        lf = self.lifts[asset]
        while lf and lf[0][0] <= s - 10.0:
            lf.popleft()
        for (tt, sz) in lf:
            if tt <= s:
                fb += sz
                n += 1
        # sweep: copied from inspect_lab.py sweep_now (tape volume in (s-1, s] or bb dropped 2c vs the bb at s-1)
        h = self.bb_hist[asset]
        while len(h) > 1 and h[1][0] <= s - 1.0:
            h.popleft()
        b1 = h[0][1] if (h and h[0][0] <= s - 1.0) else None
        sweep = 1 if (hit_1s >= SWEEP_SHARES or (good(b) and good(b1) and b <= b1 - SWEEP_DROP + 1e-9)) else 0
        return {
            "market": self.market, "start": self.start, "side": side, "asset": asset, "t_el": t_el, "time_left": self.T - t_el,
            "bb": b if good(b) else None, "ba": k if good(k) else None, "bb_sz": bb_sz, "ba_sz": ba_sz,
            "spread": round(k - b, 6) if (good(b) and good(k)) else None,
            "mid": round((k + b) / 2, 6) if (good(b) and good(k)) else None,
            "imb3": imb3, "sum_ask2": round(sum_ask2, 6) if sum_ask2 is not None else None,
            "sum_bid2": round(sum_bid2, 6) if sum_bid2 is not None else None,
            "bid_lvl_sz": bb_sz, "flow_sell_10s": fs, "flow_buy_10s": fb, "trade_n_10s": n, "sweep_flag": sweep,
        }

    def _rows_at(self, s):
        """rows for both sides at integer second s (state = events with t <= s); look-ahead helpers NA here."""
        self._apply_spot(s)
        t_el = int(s - self.start)
        sp = self.spot
        op = self.open_twap
        sp2 = self._spot_at_or_before(s - 2)
        sp5 = self._spot_at_or_before(s - 5)
        spot_bps = _bps(sp, op) if (sp is not None and op) else None
        mom2 = _bps(sp, sp2) if (sp is not None and sp2) else None
        mom5 = _bps(sp, sp5) if (sp is not None and sp5) else None
        vol = self._spot_vol_30s(s) if sp is not None else None
        dt = _dt.datetime.fromtimestamp(s, _dt.timezone.utc)
        blk = 0
        if self.blackout_fn is not None:
            try:
                v = self.blackout_fn(s, self.market)
                if isinstance(v, (tuple, list)):          # scout.macro.check_macro_blackout -> (bool, event, secs)
                    v = v[0] if v else False
                blk = 1 if v else 0
            except Exception:
                blk = 0
        common = {
            "spot": sp, "open_twap": op, "spot_bps_vs_open": spot_bps, "mom_2s": mom2, "mom_5s": mom5, "spot_vol_30s": vol,
            "session_hour": dt.hour, "dow": dt.weekday(), "macro_blackout": blk, "src": self.src,
            "ask_ok_100ms": None, "ask_sz_100ms": None, "sell_at_bb_next1s": None, "sell_thru_bb_next1s": None,
        }
        common.update(self.ext)
        rows = {}
        for asset, side in self.assets.items():
            r = self._side_row(asset, s, t_el)
            r.update(common)
            rows[side] = r
        return rows

    def _snapshot(self, s):
        rows = self._rows_at(s)
        ask_ref = {side: rows[side]["ba"] for side in rows}
        bb_ref = {side: rows[side]["bb"] for side in rows}
        self.pending.append(_Pending(s, rows, ask_ref, bb_ref))

    def rows_now(self, t):
        """both sides' current-state rows at time t (look-ahead helpers NA). Used by row_now and tier 2."""
        s = int(math.floor(t))
        rows = self._rows_at(s)
        t_el = int(math.floor(t - self.start))
        for r in rows.values():
            r["t_el"] = t_el
            r["time_left"] = self.T - t_el
        return rows

    def row_now(self, side, t):
        """LIVE: current-state row at time t WITHOUT the look-ahead helpers (NA)."""
        return self.rows_now(t)[side]

    def flush(self, t_now):
        """rows (Up and Down) for every pending second s with s + 1.2 <= t_now, in order."""
        out = []
        while self.pending and self.pending[0].s + LOOKAHEAD_S <= t_now:
            pnd = self.pending.popleft()
            s = pnd.s
            for asset, side in self.assets.items():
                r = pnd.rows[side]
                if side in pnd.ask_ok:
                    r["ask_ok_100ms"] = pnd.ask_ok[side]
                    r["ask_sz_100ms"] = pnd.ask_sz[side]
                else:
                    # no event of this round in (s, s+1.2]: the book did not change, the ask is still there
                    ref = pnd.ask_ref[side]
                    if ref is None:
                        r["ask_ok_100ms"] = None
                        r["ask_sz_100ms"] = None
                    else:
                        r["ask_ok_100ms"] = 1
                        r["ask_sz_100ms"] = float(self.levels_ask[asset].get(ref, 0.0))
                bref = pnd.bb_ref[side]
                if bref is not None:
                    at_bb = 0.0
                    thru = 0.0
                    for (tt, sz, hp) in self.tape[asset]:
                        if s < tt <= s + 1.0:
                            if abs(hp - bref) < 1e-9:
                                at_bb += sz
                            elif hp < bref - 1e-9:
                                thru += sz
                    r["sell_at_bb_next1s"] = at_bb
                    r["sell_thru_bb_next1s"] = thru
                else:
                    r["sell_at_bb_next1s"] = None
                    r["sell_thru_bb_next1s"] = None
                out.append(r)
                self.n_emitted += 1
        return out

    def finished(self):
        """True once every second of the round has been snapshotted and emitted."""
        return self.next_s >= self.end and not self.pending
