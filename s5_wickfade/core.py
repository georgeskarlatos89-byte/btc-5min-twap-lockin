"""S5 causal signal gates. No wallet, signing, order submission or synthetic fills."""
from collections import deque
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Config:
    spike_bps: float = 8
    spike_seconds: float = 5
    retrace: float = .60
    retrace_seconds: float = 10
    entry_cutoff: float = 180
    edge: float = .02
    max_age: float = 1
    max_spot_gap: float = 2
    notional: float = 10
    max_daily: int = 3
    # Research assumptions, not a verified fee schedule or measured latency.
    fee_per_share: float = .02
    slippage_per_share: float = .005
    latency_seconds: float = .5

class WickDetector:
    def __init__(self, cfg=Config()):
        self.c = cfg
        self.history = deque()
        self.wick = None
        self.last = None
        self.round = None

    def update(self, t, price, round_start):
        if not all(math.isfinite(x) for x in (t, price, round_start)) or price <= 0:
            return None
        if self.last is not None and t <= self.last:
            return None
        if self.round != round_start or (self.last is not None and t-self.last > self.c.max_spot_gap):
            self.history.clear()
            self.wick = None
        self.last, self.round = t, round_start
        if not 0 <= t-round_start < self.c.entry_cutoff:
            self.history.clear()
            self.wick = None
            return None
        while self.history and t-self.history[0][0] > self.c.spike_seconds:
            self.history.popleft()
        w = self.wick
        if w:
            direction = w['direction']
            # A later extreme must still be within five seconds of its baseline.
            if direction*(price-w['peak']) > 0:
                if t-w['base_t'] > self.c.spike_seconds:
                    self.wick = None
                else:
                    w['peak'], w['peak_t'] = price, t
            elif t-w['peak_t'] > self.c.retrace_seconds:
                self.wick = None
            elif direction*(w['peak']-price) / abs(w['peak']-w['base']) >= self.c.retrace:
                signal = dict(w, t=t, price=price, side='Down' if direction == 1 else 'Up',
                              round_start=round_start, reversion_seconds=t-w['peak_t'])
                self.wick = None
                self.history.clear()  # consume wick: never repeatedly trade one retracement
                self.history.append((t, price))
                return signal
        if self.wick is None:
            candidates = [(abs(price/p-1)*1e4, bt, p) for bt,p in self.history]
            if candidates:
                bps, bt, p = max(candidates)
                if bps >= self.c.spike_bps:
                    self.wick = dict(base_t=bt, base=p, peak_t=t, peak=price,
                                     direction=1 if price>p else -1, spike_bps=bps)
        self.history.append((t, price))
        return None


def gate(signal, book, fair, now, daily_count, cfg=Config(), killed=False):
    """Reusable fail-closed gate; fair is an externally supplied calibrated probability.

    fair fields: t, value, side, round_start, validated. No model is inferred here.
    book fields: t, bid, ask, ask_size, side, round_start. t = source timestamp.
    """
    if killed: return 'kill_switch'
    if not 0 <= now-signal['round_start'] < cfg.entry_cutoff: return 'time_gate'
    if daily_count >= cfg.max_daily: return 'daily_cap'
    if now-signal['t'] > cfg.max_age or now < signal['t']: return 'stale_signal'
    if not book or not fair: return 'missing_book_or_fair'
    for item in (book, fair):
        if item.get('side') != signal['side'] or item.get('round_start') != signal['round_start']:
            return 'wrong_market_or_side'
        if not math.isfinite(item['t']) or not 0 <= now-item['t'] <= cfg.max_age:
            return 'stale_or_future_data'
    if not fair.get('validated', False): return 'unvalidated_fair'
    b,a,q,p = book['bid'],book['ask'],book['ask_size'],fair['value']
    if not all(math.isfinite(x) for x in (b,a,q,p)): return 'invalid_numbers'
    if not 0 < b <= a < 1 or not 0 < p < 1 or q <= 0: return 'invalid_book_or_fair'
    if p-a < cfg.edge + cfg.fee_per_share + cfg.slippage_per_share:
        return 'insufficient_net_edge'
    # $10 TOTAL cash budget including assumed entry fees/slippage.
    shares = cfg.notional/(a+cfg.fee_per_share+cfg.slippage_per_share)
    if q < shares: return 'insufficient_depth'
    return 'eligible'


def latency_verdict(quote_windows, detection_to_fill, quote_safety=.1, minimum=30):
    """Conservative diagnostic: lower-quartile opportunity vs p95 real entry latency."""
    if len(quote_windows)<minimum or len(detection_to_fill)<minimum:
        return 'UNMEASURED_BLOCK_LIVE'
    def quantile(xs, q):
        xs=sorted(xs)
        return xs[int((len(xs)-1)*q)]
    return ('KILL' if quantile(quote_windows,.25) <= quantile(detection_to_fill,.95)+quote_safety
            else 'PASS_LATENCY_ONLY')
