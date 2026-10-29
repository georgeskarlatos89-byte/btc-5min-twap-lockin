#!/usr/bin/env python3
"""_stubs.py - minimal stand-ins for qdesk.common.* and qdesk.features.features, used ONLY by bot/test_qbot.py when the
real modules (agents A and C) are not importable yet. install() registers a stub only for a module whose real import
fails, so the tests always prefer the real code. Never imported by the bot itself.
"""
import collections, hashlib, importlib, json, math, os, sys, time, types
from datetime import datetime, timezone

FEATURE_COLUMNS = ("market,start,side,asset,t_el,time_left,bb,ba,bb_sz,ba_sz,spread,mid,imb3,sum_ask2,sum_bid2,"
                   "spot,open_twap,spot_bps_vs_open,mom_2s,mom_5s,spot_vol_30s,flow_sell_10s,flow_buy_10s,trade_n_10s,sweep_flag,"
                   "ask_ok_100ms,ask_sz_100ms,bid_lvl_sz,sell_at_bb_next1s,sell_thru_bb_next1s,"
                   "session_hour,dow,macro_blackout,liq_long_5m_usd,liq_short_5m_usd,hl_funding_1h,hl_oi_usd,src").split(",")


# ---------------------------------------------------------------- dsl
def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def field_value(f, row, other_row=None):
    pre, base = (f.split(":", 1) if ":" in f else (None, f))
    if pre == "other":
        return field_value(base, other_row or {}, None)
    v = _num((row or {}).get({"ask": "ba", "bid": "bb"}.get(base, base)))
    return abs(v) if (pre == "abs" and v is not None) else v


def eval_when(when, row, other_row=None):
    for c in when or []:
        v = field_value(c["f"], row, other_row); op = c["op"]; x = c["v"]
        if v is None: return False
        ok = {"<": lambda: v < x, "<=": lambda: v <= x, ">": lambda: v > x, ">=": lambda: v >= x, "==": lambda: v == x,
              "in": lambda: v in [float(i) for i in x], "between": lambda: x[0] <= v <= x[1]}[op]()
        if not ok: return False
    return True


def chosen_side(rule, row_up, row_down):
    when = (rule.get("entry") or {}).get("when") or []; side = rule.get("side")
    if side == "up": return "Up" if eval_when(when, row_up, row_down) else None
    if side == "down": return "Down" if eval_when(when, row_down, row_up) else None
    if side == "both": return "both" if (eval_when(when, row_up, row_down) and eval_when(when, row_down, row_up)) else None
    if side == "signal_sign":
        v = field_value(rule.get("side_field", ""), row_up, row_down)
        if v is None or v == 0: return None
        s = "Up" if v > 0 else "Down"
        return s if eval_when(when, row_up if s == "Up" else row_down, row_down if s == "Up" else row_up) else None
    return None


def canonical_hash(rule):
    d = {k: v for k, v in rule.items() if k not in ("id", "name", "mechanism", "who_loses", "why_this_could_fail", "novelty_vs_prior", "prediction")}
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_rule(rule):
    errs = []
    for k in ("market", "side", "entry", "exit", "limits"):
        if k not in rule: errs.append(f"missing {k}")
    return errs


# ---------------------------------------------------------------- hashing (copied from open_book_maker_v2/make_strategy.py)
QDESK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_FILES_BOT = ["bot/qbot.py", "bot/paper_fill.py", "bot/gate.py", "common/dsl.py", "features/features.py"]


def code_hash(paths):
    h = hashlib.sha256()
    for fn in paths:
        p = fn if os.path.isabs(fn) else os.path.join(QDESK_DIR, fn)
        h.update(fn.encode() + b"\0")
        try:
            with open(p, "rb") as f: h.update(f.read().replace(b"\r\n", b"\n"))
        except FileNotFoundError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def corpus_hash(files):
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: os.path.basename(x)):
        h.update(os.path.basename(p).encode() + b"\0")
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- fees / settle / tg / schemas
def taker_fee(price, shares): return round(0.07 * price * (1 - price) * shares, 5)
def maker_fee(price, shares): return 0.0
def maker_rebate(price, shares): return round(0.20 * taker_fee(price, shares), 5)


def settle_pnl(legs, winner):
    cost = sum(l["price"] * l["size"] for l in legs); fees = sum(l.get("fee", 0) for l in legs)
    pay = sum(l["size"] for l in legs if l["side"] == winner)
    up = sum(l["size"] for l in legs if l["side"] == "Up"); dn = sum(l["size"] for l in legs if l["side"] == "Down")
    return {"cost": cost, "payout": pay, "fees": fees, "net": pay - cost - fees, "pairs": min(up, dn)}


SENT = []


def alert(key, text, cls="warn", source="qdesk"):
    SENT.append((key, text, cls, source)); return True


def heartbeat(stage, ok, note=""):
    d = os.path.join(os.path.expanduser(os.environ.get("QD_DATA") or "~/qdesk/data"), "state"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"hb_{stage}.json"); json.dump({"ts": int(time.time()), "stage": stage, "ok": ok, "note": note}, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)


def append_csv(path, header, row):
    import csv
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(header)
        w.writerow(row)


def load_json(path): return json.load(open(path))
def save_json(path, obj):
    json.dump(obj, open(path + ".tmp", "w"), indent=1); os.replace(path + ".tmp", path)
def validate(schema_name, obj): return []


# ---------------------------------------------------------------- FeatureState (live-only subset: row_now)
class FeatureState:
    def __init__(self, market, start, up_asset, down_asset, spot_lag=1.44):
        self.market = market; self.start = int(start); self.T = 300 if market == "5m" else 900
        self.assets = {"Up": up_asset, "Down": down_asset}; self.other = {up_asset: down_asset, down_asset: up_asset}
        self.spot_lag = spot_lag
        self.bids = collections.defaultdict(dict); self.asks = collections.defaultdict(dict); self.bb = {}; self.ba = {}
        self.spot_q = collections.deque(); self.spot_hist = collections.deque(maxlen=120); self.spot = None; self.open_twap = None
        self.trades = collections.deque(maxlen=2000)

    def on_price_change(self, asset, side, price, size, bb, ba, t):
        L = self.bids[asset] if side == "BUY" else self.asks[asset]
        if size <= 0: L.pop(float(price), None)
        else: L[float(price)] = float(size)
        if bb: self.bb[asset] = float(bb)
        if ba: self.ba[asset] = float(ba)

    def on_bba(self, asset, bb, ba, t):
        if bb: self.bb[asset] = float(bb)
        if ba: self.ba[asset] = float(ba)

    def on_trade(self, asset, side, price, size, t):
        self.trades.append((t, asset, side, float(price), float(size)))

    def on_book(self, asset, bids, asks, t):
        self.bids[asset] = {float(p): float(s) for p, s in bids if float(s) > 0}; self.asks[asset] = {float(p): float(s) for p, s in asks if float(s) > 0}
        if self.bids[asset]: self.bb[asset] = max(self.bids[asset])
        if self.asks[asset]: self.ba[asset] = min(self.asks[asset])

    def on_spot(self, t_source, value):
        self.spot_q.append((t_source + self.spot_lag, float(value)))

    def on_twap(self, t, value):
        if self.open_twap is None and 0 <= t - self.start <= 3: self.open_twap = float(value)

    def on_ext(self, t, liq_long=None, liq_short=None, funding=None, oi=None):
        pass

    def _drain(self, t):
        while self.spot_q and self.spot_q[0][0] <= t:
            tr, v = self.spot_q.popleft(); self.spot = v; self.spot_hist.append((tr, v))

    def flush(self, t_now):
        return []

    def row_now(self, side, t):
        self._drain(t); a = self.assets[side]; o = self.other[a]
        bb = self.bb.get(a); ba = self.ba.get(a)
        r = {k: "" for k in FEATURE_COLUMNS}
        d = datetime.fromtimestamp(t, timezone.utc)
        r.update(market=self.market, start=self.start, side=side, asset=a, t_el=int(t - self.start), time_left=self.T - int(t - self.start),
                 bb=bb, ba=ba, bb_sz=self.bids[a].get(bb, 0.0) if bb else 0.0, ba_sz=self.asks[a].get(ba, 0.0) if ba else 0.0,
                 spread=(ba - bb) if (bb is not None and ba is not None) else "", mid=((ba + bb) / 2) if (bb is not None and ba is not None) else "",
                 spot=self.spot, open_twap=self.open_twap, session_hour=d.hour, dow=d.weekday(), macro_blackout=0, src="live",
                 sum_ask2=(self.ba.get(a, 0) + self.ba.get(o, 0)) if (a in self.ba and o in self.ba) else "",
                 sum_bid2=(self.bb.get(a, 0) + self.bb.get(o, 0)) if (a in self.bb and o in self.bb) else "")
        if self.spot is not None and self.open_twap:
            r["spot_bps_vs_open"] = (self.spot - self.open_twap) / self.open_twap * 1e4
        for name, lag in (("mom_2s", 2), ("mom_5s", 5)):
            old = next((v for tr, v in reversed(self.spot_hist) if tr <= t - lag), None)
            r[name] = ((self.spot - old) / old * 1e4) if (old and self.spot is not None) else ""
        r["flow_sell_10s"] = sum(s for (tt, aa, sd, p, s) in self.trades if t - 10 < tt <= t and ((aa == a and sd == "SELL") or (aa == o and sd == "BUY")))
        r["flow_buy_10s"] = sum(s for (tt, aa, sd, p, s) in self.trades if t - 10 < tt <= t and ((aa == a and sd == "BUY") or (aa == o and sd == "SELL")))
        r["trade_n_10s"] = sum(1 for (tt, aa, sd, p, s) in self.trades if t - 10 < tt <= t and aa in (a, o))
        return r


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items(): setattr(m, k, v)
    return m


STUBS = {
    "qdesk.common.dsl": dict(eval_when=eval_when, chosen_side=chosen_side, canonical_hash=canonical_hash, validate_rule=validate_rule, field_value=field_value),
    "qdesk.common.hashing": dict(code_hash=code_hash, corpus_hash=corpus_hash, CODE_FILES_BOT=CODE_FILES_BOT, CODE_FILES_ENGINE=[]),
    "qdesk.common.fees": dict(taker_fee=taker_fee, maker_fee=maker_fee, maker_rebate=maker_rebate),
    "qdesk.common.settle": dict(settle_pnl=settle_pnl),
    "qdesk.common.tg": dict(alert=alert, heartbeat=heartbeat, append_csv=append_csv, SENT=SENT),
    "qdesk.common.schemas": dict(load_json=load_json, save_json=save_json, validate=validate, FEATURE_COLUMNS=FEATURE_COLUMNS),
    "qdesk.features.features": dict(FeatureState=FeatureState, FEATURE_COLUMNS=tuple(FEATURE_COLUMNS)),
}


def _ensure_pkg(name):
    if name in sys.modules: return sys.modules[name]
    try:
        return importlib.import_module(name)
    except ImportError:
        m = types.ModuleType(name); m.__path__ = []; sys.modules[name] = m
        parent = name.rsplit(".", 1)[0] if "." in name else None
        if parent: setattr(_ensure_pkg(parent), name.rsplit(".", 1)[1], m)
        return m


def install():
    """register a stub for every module whose real import fails; returns the list of stubbed module names."""
    used = []
    for name, attrs in STUBS.items():
        try:
            importlib.import_module(name); continue
        except Exception:
            pass
        pkg, leaf = name.rsplit(".", 1)
        parent = _ensure_pkg(pkg)
        m = _mod(name, **attrs); sys.modules[name] = m; setattr(parent, leaf, m); used.append(name)
    return used
