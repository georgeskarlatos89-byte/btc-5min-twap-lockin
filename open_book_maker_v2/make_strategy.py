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
"""
import math, os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass


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
TICK = 0.01
QUOTE_SIZE = _envf("LIVE_QUOTE_SIZE", 20.0)        # shares per side, SAME in sim and bot
MAX_NET_SHARES = _envf("MAX_NET_SHARES", 40.0)
QUEUE_FILL_PROB = 0.5
MAKER_FEE = 0.0


def round_tick(p):
    """nearest venue tick, halves rounded up (0.455 -> 0.46), immune to float noise."""
    return round(math.floor(p / TICK + 0.5 + 1e-9) * TICK, 2)


class Book:
    def __init__(self, bids, asks):
        self.bids = bids or []   # [(price, size)] sorted desc
        self.asks = asks or []   # [(price, size)] sorted asc

    @property
    def bb(self): return self.bids[0][0] if self.bids else None

    @property
    def ba(self): return self.asks[0][0] if self.asks else None


def decide_quotes(fair_p, t_rem, label, net_shares, book=None):
    """-> (bid, ask) resting prices (either may be None) or None when not quoting."""
    if fair_p is None or not (FAIR_LO <= fair_p <= FAIR_HI):
        return None
    if t_rem is not None and t_rem < NO_QUOTE_SECS.get(label, 30):
        return None
    bid = round_tick(fair_p - HALF_SPREAD)
    ask = round_tick(fair_p + HALF_SPREAD)
    bid = min(max(bid, 0.01), 0.99)
    ask = min(max(ask, 0.01), 0.99)
    if ask <= bid:
        ask = round_tick(bid + TICK)
    # post-only: never cross the touch
    if book is not None:
        if book.ba is not None and bid >= book.ba:
            bid = None
        if book.bb is not None and ask <= book.bb:
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

    def add(self, t, side, price, size, fair):
        self.fills.append((t, side, price, size, fair))

    def settle(self, settled_up):
        payoff = 1.0 if settled_up else 0.0
        pnl = 0.0
        for _, side, price, size, _ in self.fills:
            pnl += (payoff - price) * size if side == "BUY" else (price - payoff) * size
        return pnl - MAKER_FEE * sum(s for _, _, _, s, _ in self.fills)

    def adverse_selection(self, settled_up):
        """mean realized edge per filled share (>0: fills were at good prices)."""
        if not self.fills:
            return None
        payoff = 1.0 if settled_up else 0.0
        es = [(payoff - p) if side == "BUY" else (p - payoff) for _, side, p, _, _ in self.fills]
        return sum(es) / len(es)
