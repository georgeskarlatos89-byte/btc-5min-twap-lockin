#!/usr/bin/env python3
"""
make_strategy.py - quoting strategy v2 + fill model + accounting (pure, unit-testable)

STRATEGY (two-sided fair-value market making on BTC up/down rounds)
  fair = FairModel.p_up (settlement rule: TWAP60 end vs TWAP60 start).
  Rest a BUY on the Up token at fair - HALF_SPREAD and a BUY on the Down token at
  (1 - fair) - HALF_SPREAD. In the unified Polymarket book a Down bid at q is the same
  order as an Up ask at 1 - q, so the sim works on the Up book only.
  Hard rules:
    - quote only when fair in [FAIR_LO, FAIR_HI]
    - no quotes in the last NO_QUOTE_SECS of the round
    - POST-ONLY: a bid at or above the best ask (or an ask at or below the best bid)
      would be a taker order -> that side is not quoted
    - prices on the venue tick (TICK = 0.01); size = QUOTE_SIZE; net inventory capped at
      MAX_NET_SHARES (both come from .env so the sim and the bot use the SAME numbers)

VARIANTS (VARIANT-EXPERIMENT-PLAN.md, 2026-10-15) - every pre-registered variant is ONE
  delta from the baseline, expressed as a VariantCfg and applied inside decide_quotes so the
  sim and the bot keep one code path. VariantCfg() with no arguments IS the v2 baseline (V0).
    half_spread            V3  wider spread (0.03 / 0.04)
    one_sided_theta        V1  quote only the side our fair says is cheap by >= theta vs book mid
    imb_q / support_k      V2  quote a side only when the top-of-book is quiet (|imbalance| < q)
                               or our side's displayed size is >= k x the opposite side
                               (top-of-book ONLY: the replay has one level per side)
    window                 V4  quote only when t_el in [lo, hi] seconds
    open_fade_secs         V5  quote only in the first N seconds of the round, then cancel
    no_requote_after_fill  V6  once a side is filled, never re-quote that side this round

FILL MODEL (v2, conservative - top-of-book snapshots only, no trade tape)
  Our bid at qb is assumed hit when the displayed best bid falls THROUGH our price:
  previous best bid >= qb and new best bid < qb, i.e. sellers ate the book down past us.
  If our bid is above the displayed best bid we are the touch and a seller hitting us
  leaves no trace in the snapshot -> NO fill is counted (under-count, never over-count).
  A quote must have rested for at least one full tick before it can fill, each crossing
  fills with probability QUEUE_FILL_PROB, and a filled quote is CONSUMED (it comes back
  only when the strategy re-posts it on a later tick). Symmetric for the ask.

ACCOUNTING: maker fee 0 (the venue charges makers nothing on these markets); rebates
and rewards are never counted. BUY = bought Up shares, SELL = sold Up shares
(= bought Down shares).

GATE LOCK (plan R7/R8): code_hash() fingerprints the strategy/sim/model/bot sources; the
sim writes it into gate_pass.json and the bot refuses LIVE unless the hashes match.
"""
import hashlib, math, os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))


def _envf(k, d):
    try:
        return float(os.environ.get(k, d))
    except (TypeError, ValueError):
        return d


# ---- config (all tunable, all logged) ---------------------------------------
HALF_SPREAD = 0.015
FAIR_LO = 0.08
FAIR_HI = 0.92
NO_QUOTE_SECS = {"5m": 20, "15m": 30}
ROUND_SECS = {"5m": 300, "15m": 900}
TICK = 0.01
QUOTE_SIZE = _envf("LIVE_QUOTE_SIZE", 20.0)        # shares per side, SAME in sim and bot
MAX_NET_SHARES = _envf("MAX_NET_SHARES", 40.0)
QUEUE_FILL_PROB = 0.5
MAKER_FEE = 0.0

# files whose content defines "the code" for the gate lock (R7/R8)
CODE_FILES = ["make_strategy.py", "maker_sim.py", "fair_model.py", "maker_bot.py"]


def code_hash():
    """sha256 (first 16 hex) over the strategy/sim/model/bot sources, in a fixed order."""
    h = hashlib.sha256()
    for fn in CODE_FILES:
        p = os.path.join(HERE, fn)
        h.update(fn.encode() + b"\0")
        try:
            with open(p, "rb") as f:
                h.update(f.read().replace(b"\r\n", b"\n"))
        except FileNotFoundError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def corpus_hash(files):
    """sha256 (first 16 hex) over the replay corpus: basename + content of every file."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda x: os.path.basename(x)):
        h.update(os.path.basename(p).encode() + b"\0")
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()[:16]


def round_tick(p):
    """nearest venue tick, halves rounded up (0.455 -> 0.46), immune to float noise."""
    return round(math.floor(p / TICK + 0.5 + 1e-9) * TICK, 2)


class VariantCfg:
    """one pre-registered delta from the baseline. No arguments == the v2 baseline (V0)."""
    FIELDS = ("half_spread", "one_sided_theta", "imb_q", "support_k", "window",
              "open_fade_secs", "no_requote_after_fill")

    def __init__(self, id="V0", half_spread=None, one_sided_theta=None, imb_q=None,
                 support_k=None, window=None, open_fade_secs=None, no_requote_after_fill=False):
        self.id = id
        self.half_spread = HALF_SPREAD if half_spread is None else float(half_spread)
        self.one_sided_theta = None if one_sided_theta is None else float(one_sided_theta)
        self.imb_q = None if imb_q is None else float(imb_q)
        self.support_k = None if support_k is None else float(support_k)
        self.window = None if window is None else (float(window[0]), float(window[1]))
        self.open_fade_secs = None if open_fade_secs is None else float(open_fade_secs)
        self.no_requote_after_fill = bool(no_requote_after_fill)

    def as_dict(self):
        d = {"id": self.id}
        for k in self.FIELDS:
            v = getattr(self, k)
            d[k] = list(v) if isinstance(v, tuple) else v
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(**{k: d.get(k) for k in ("id",) + cls.FIELDS if k in d})

    def is_baseline(self):
        return (self.half_spread == HALF_SPREAD and self.one_sided_theta is None and self.imb_q is None
                and self.support_k is None and self.window is None and self.open_fade_secs is None
                and not self.no_requote_after_fill)


BASELINE = VariantCfg()


class Book:
    def __init__(self, bids, asks):
        self.bids = bids or []   # [(price, size)] sorted desc
        self.asks = asks or []   # [(price, size)] sorted asc

    @property
    def bb(self): return self.bids[0][0] if self.bids else None

    @property
    def ba(self): return self.asks[0][0] if self.asks else None

    @property
    def bb_sz(self): return self.bids[0][1] if self.bids else None

    @property
    def ba_sz(self): return self.asks[0][1] if self.asks else None

    @property
    def mid(self):
        if self.bb is None or self.ba is None:
            return None
        return (self.bb + self.ba) / 2.0


def decide_quotes(fair_p, t_rem, label, net_shares, book=None, cfg=None, filled_sides=None):
    """-> (bid, ask) resting prices (either may be None) or None when not quoting.
       cfg=None / VariantCfg() is the baseline; filled_sides = set of sides already filled
       this round ({'BUY','SELL'}), only used by the no-re-quote variant."""
    cfg = BASELINE if cfg is None else cfg
    if fair_p is None or not (FAIR_LO <= fair_p <= FAIR_HI):
        return None
    if t_rem is not None and t_rem < NO_QUOTE_SECS.get(label, 30):
        return None
    t_el = None if t_rem is None else ROUND_SECS.get(label, 300) - t_rem
    if cfg.window is not None:
        if t_el is None or not (cfg.window[0] <= t_el <= cfg.window[1]):
            return None
    if cfg.open_fade_secs is not None:
        if t_el is None or t_el > cfg.open_fade_secs:
            return None
    hs = cfg.half_spread
    bid = round_tick(fair_p - hs)
    ask = round_tick(fair_p + hs)
    bid = min(max(bid, 0.01), 0.99)
    ask = min(max(ask, 0.01), 0.99)
    if ask <= bid:
        ask = round_tick(bid + TICK)
    # V1 one-sided conviction: only the side our fair says is cheap by >= theta vs the book mid.
    # The quote stays a MAKER order: when fair - hs would cross the ask it is capped one tick
    # below the ask (improving the touch), symmetric for the ask side.
    if cfg.one_sided_theta is not None:
        mid = book.mid if book is not None else None
        if mid is None:
            return None
        d = fair_p - mid
        if d >= cfg.one_sided_theta:
            ask = None            # Up is cheap on the book -> buy Up only
            if book.ba is not None:
                bid = min(bid, round_tick(book.ba - TICK))
        elif -d >= cfg.one_sided_theta:
            bid = None            # Up is rich -> buy Down (= sell Up) only
            if book.bb is not None:
                ask = max(ask, round_tick(book.bb + TICK))
        else:
            return None
    # post-only: never cross the touch
    if book is not None:
        if bid is not None and book.ba is not None and bid >= book.ba:
            bid = None
        if ask is not None and book.bb is not None and ask <= book.bb:
            ask = None
    # V2 flow-toxicity gate on the top of book (quiet book OR our side has support)
    if cfg.imb_q is not None or cfg.support_k is not None:
        bs = book.bb_sz if book is not None else None
        as_ = book.ba_sz if book is not None else None
        if not bs or not as_:
            return None
        imb = (bs - as_) / (bs + as_)
        quiet = cfg.imb_q is not None and abs(imb) < cfg.imb_q
        bid_ok = quiet or (cfg.support_k is not None and bs >= cfg.support_k * as_)
        ask_ok = quiet or (cfg.support_k is not None and as_ >= cfg.support_k * bs)
        if not bid_ok:
            bid = None
        if not ask_ok:
            ask = None
    # V6 no re-quote into a move: a filled side stays off for the rest of the round
    if cfg.no_requote_after_fill and filled_sides:
        if "BUY" in filled_sides:
            bid = None
        if "SELL" in filled_sides:
            ask = None
    # inventory guard: stop feeding the side that grows exposure
    if net_shares >= MAX_NET_SHARES:
        bid = None
    if net_shares <= -MAX_NET_SHARES:
        ask = None
    if bid is None and ask is None:
        return None
    return (bid, ask)


class Resting:
    """what is resting on the book right now: price + how many ticks it has rested."""
    def __init__(self):
        self.bid = None; self.bid_age = 0
        self.ask = None; self.ask_age = 0

    def update(self, quote):
        """apply the strategy's wanted quote: new price -> fresh order (age 0)."""
        nb = quote[0] if quote else None
        na = quote[1] if quote else None
        if nb != self.bid:
            self.bid, self.bid_age = nb, 0
        else:
            self.bid_age += 1 if nb is not None else 0
        if na != self.ask:
            self.ask, self.ask_age = na, 0
        else:
            self.ask_age += 1 if na is not None else 0

    def as_tuple(self):
        return (self.bid, self.ask)


def detect_fills(prev_book, new_book, resting, rng, size=None):
    """-> [(side, price, size)]; consumes the filled side of `resting`."""
    fills = []
    size = QUOTE_SIZE if size is None else size
    if resting is None or prev_book is None or new_book is None:
        return fills
    qb, qa = resting.bid, resting.ask
    pb, nb = prev_book.bb, new_book.bb
    if (qb is not None and resting.bid_age >= 1 and pb is not None and nb is not None
            and pb >= qb > nb):
        if rng.random() < QUEUE_FILL_PROB:
            fills.append(("BUY", qb, size))
            resting.bid, resting.bid_age = None, 0
    pa, na = prev_book.ba, new_book.ba
    if (qa is not None and resting.ask_age >= 1 and pa is not None and na is not None
            and pa <= qa < na):
        if rng.random() < QUEUE_FILL_PROB:
            fills.append(("SELL", qa, size))
            resting.ask, resting.ask_age = None, 0
    return fills


class RoundAccount:
    """per-round fill ledger + settlement valuation"""
    def __init__(self):
        self.fills = []          # (t, side, price, size, fair_at_t)
        self.n_quotes = 0

    @property
    def net_shares(self):
        return sum(s if side == "BUY" else -s for _, side, _, s, _ in self.fills)

    @property
    def notional(self):
        return sum(p * s for _, _, p, s, _ in self.fills)

    @property
    def cost(self):
        """money actually paid: a BUY at p costs p/share, a SELL at p (= Down buy) costs 1-p."""
        return sum((p if side == "BUY" else 1.0 - p) * s for _, side, p, s, _ in self.fills)

    @property
    def shares(self):
        return sum(s for _, _, _, s, _ in self.fills)

    def add(self, t, side, price, size, fair):
        self.fills.append((t, side, price, size, fair))

    def settle(self, settled_up):
        payoff = 1.0 if settled_up else 0.0
        pnl = 0.0
        for _, side, price, size, _ in self.fills:
            pnl += (payoff - price) * size if side == "BUY" else (price - payoff) * size
        return pnl - MAKER_FEE * sum(s for _, _, _, s, _ in self.fills)

    def fills_won(self, settled_up):
        """number of fills that ended on the winning side."""
        return sum(1 for _, side, _, _, _ in self.fills if (side == "BUY") == bool(settled_up))

    def adverse_selection(self, settled_up):
        """mean realized edge per filled share (>0: fills were at good prices)."""
        if not self.fills:
            return None
        payoff = 1.0 if settled_up else 0.0
        es = [(payoff - p) if side == "BUY" else (p - payoff) for _, side, p, _, _ in self.fills]
        return sum(es) / len(es)
