"""S2 research gate. No signing, wallets, order submission, or implicit fills."""
import math
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    threshold_5m: float = 5
    threshold_15m: float = 8
    max_ask: float = .60
    budget: float = 10  # hypothetical total cost including fee
    fee_rate: float = .07  # documented assumption, NOT fetched market fee
    slippage: float = .005
    min_edge: float = .02
    max_age: float = 2
    sigma_5m: float = 7.5  # research assumption, not an estimate

    def __post_init__(self):
        if not (0 < self.threshold_5m <= 10 and 0 < self.threshold_15m <= 10):
            raise ValueError('KILL: thresholds must be in (0,10] bps')

def evaluate(r, c=Config()):
    def skip(why): return {'action': 'skip', 'reason': why}
    try:
        for k in ('start','duration','now','open','open_ts','open_received','spot','spot_ts','spot_received','book_ts','book_received','ask','bid','ask_size','tick'):
            if not math.isfinite(float(r[k])): return skip('invalid_numeric')
        T, elapsed = r['duration'], r['now']-r['start']
        if T not in (300,900): return skip('duration')
        if not 5 <= elapsed <= 15: return skip('entry_window')
        if r.get('reference_source') != 'chainlink_spot_candidate': return skip('reference_source')
        # Bell proxy uses only an observation at/before the bell, received by bell.
        if not 0 <= r['start']-r['open_ts'] <= c.max_age: return skip('open_timestamp')
        if not r['open_ts'] <= r['open_received'] <= r['start']: return skip('open_unavailable_at_bell')
        for p in ('spot','book'):
            if not 0 <= r['now']-r[p+'_ts'] <= c.max_age: return skip(p+'_stale')
            if not r[p+'_ts'] <= r[p+'_received'] <= r['now']: return skip(p+'_availability')
        if min(r['open'],r['spot']) <= 0: return skip('price')
        disp = (r['spot']/r['open']-1)*10000
        if abs(disp)+1e-9 < (c.threshold_5m if T==300 else c.threshold_15m): return skip('noise_zone')
        side = 'Up' if disp>0 else 'Down'
        if r.get('side') != side: return skip('wrong_book_side')
        ask,bid,tick = r['ask'],r['bid'],r['tick']
        if not 0 < bid < ask < 1 or not 0 < tick < 1: return skip('invalid_book')
        if ask > c.max_ask: return skip('ask_ceiling')
        # Illustrative terminal diffusion model; invalid for unverified TWAP semantics.
        sigma = c.sigma_5m*math.sqrt((T-elapsed)/300)
        fair = .5*(1+math.erf(abs(disp)/sigma/math.sqrt(2)))
        p = ask+c.slippage
        if p > c.max_ask: return skip('slippage_ceiling')
        fee = c.fee_rate*p*(1-p)
        cost = p+fee
        shares = c.budget/cost
        if shares < float(r.get('min_order_size',0)): return skip('minimum_order_size')
        if r['ask_size'] < shares: return skip('insufficient_top_depth')
        if fair-cost < c.min_edge: return skip('model_edge')
        quote = math.floor((min(fair-.01,ask-tick,c.max_ask)+1e-10)/tick)*tick
        return dict(action='candidate',side=side,displacement_bps=disp,model_fair=fair,
                    assumed_cost_per_share=cost,shares=shares,budget=c.budget,
                    maker_quote_intent=round(quote,8),maker_fill=False,
                    live_eligible=False,reference_verified=False)
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        return skip('malformed_row')
