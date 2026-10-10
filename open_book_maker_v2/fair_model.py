#!/usr/bin/env python3
"""
fair_model.py - probability that a BTC up/down round settles UP (shared by recorder, sim, bot)

SETTLEMENT RULE (verified 381/381 on 1-Hz ticks, audit 2026-09-27):
  Up  <=>  TWAP60 at the round END  >=  TWAP60 at the round START   (ties = Up)
  TWAP60 = the venue's 60-second average of the Chainlink price (RTDS topic
  crypto_prices_twap_sixty). The full-round average is NOT the rule (84.8% only).

MODEL
  Let S = current Chainlink spot, o = TWAP60 snapped at round start, r = seconds left,
  sigma = per-second standard deviation of the spot (see below).
  The end TWAP is the average of the spot over the last 60 s of the round.
    r >= 60 : mean = S,                       var = sigma^2 * (r - 40)
              (average of a random walk over a 60 s window that starts r-60 s from now:
               (r-60) + 60/3 = r - 40)
    r <  60 : part of that window is already in the past and known:
              mean = ((60-r) * avg(spot over the last 60-r s) + r * S) / 60
              var  = sigma^2 * r^3 / 10800          ( (r/60)^2 * r/3 )
    r <= 0  : the feed's own TWAP60 is the settlement value: p = 1 if twap >= o else 0
  p_up = Phi((mean - o) / (SAFETY * sqrt(var)))

SIGMA
  std of 10-second spot changes over the trailing 30 minutes, divided by sqrt(10).
  1-second changes of a 60-s TWAP (the v1 model) are tiny and correlated and gave a
  sigma ~10x too small (model said 0.03% Up while the market said 37%); 10-second
  spot changes are far less affected by that.
"""
import math
from collections import deque

SAFETY = 1.0          # multiplier on sigma (1.0 = as measured; raise to be humbler)
SIGMA_WINDOW_S = 1800
SIGMA_LAG_S = 10
SIGMA_MIN_SAMPLES = 30
SIGMA_FLOOR_BPS = 0.3  # per-second floor, in bps of price (never let p collapse to 0/1 early)


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class FairModel:
    def __init__(self):
        self.spot = deque()          # (ts, value), trailing SIGMA_WINDOW_S + 60 s
        self.twap = None             # latest TWAP60 value from the feed
        self.twap_ts = None
        self._sigma = None
        self._sigma_ts = -1e9

    # ---- feed inputs ---------------------------------------------------------
    def on_spot(self, ts, value):
        if value is None or value <= 0:
            return
        if self.spot and ts < self.spot[-1][0]:
            return                      # out-of-order tick, ignore
        self.spot.append((ts, float(value)))
        cutoff = ts - SIGMA_WINDOW_S - 60
        while self.spot and self.spot[0][0] < cutoff:
            self.spot.popleft()

    def on_twap(self, ts, value):
        if value is None or value <= 0:
            return
        self.twap, self.twap_ts = float(value), ts

    # ---- volatility ----------------------------------------------------------
    def sigma1(self, now=None):
        """per-second sigma in price units; cached for 15 s."""
        if not self.spot:
            return None
        now = self.spot[-1][0] if now is None else now
        if now - self._sigma_ts < 15 and self._sigma is not None:
            return self._sigma
        # sample the spot once per second (last tick in each second), then 10-s differences
        per_sec = {}
        for ts, v in self.spot:
            if ts >= now - SIGMA_WINDOW_S:
                per_sec[int(ts)] = v
        secs = sorted(per_sec)
        diffs = []
        for s in secs:
            prev = per_sec.get(s - SIGMA_LAG_S)
            if prev is not None:
                diffs.append(per_sec[s] - prev)
        price = self.spot[-1][1]
        floor = price * SIGMA_FLOOR_BPS * 1e-4
        if len(diffs) < SIGMA_MIN_SAMPLES:
            self._sigma = max(floor, (self._sigma or 0.0))
        else:
            m = sum(diffs) / len(diffs)
            var = sum((d - m) ** 2 for d in diffs) / (len(diffs) - 1)
            self._sigma = max(floor, math.sqrt(var / SIGMA_LAG_S))
        self._sigma_ts = now
        return self._sigma

    # ---- the probability -----------------------------------------------------
    def p_up(self, o_twap, t_rem, now=None):
        """P(round settles Up) given the open reference o_twap and t_rem seconds left."""
        if o_twap is None or not self.spot:
            return None
        now = self.spot[-1][0] if now is None else now
        r = float(t_rem)
        if r <= 0:
            if self.twap is None:
                return None
            return 1.0 if self.twap >= o_twap else 0.0
        S = self.spot[-1][1]
        sig = self.sigma1(now)
        if sig is None:
            return None
        if r >= 60:
            mean, var = S, sig * sig * (r - 40.0)
        else:
            past_len = 60.0 - r
            past = [v for ts, v in self.spot if ts >= now - past_len]
            avg_past = sum(past) / len(past) if past else S
            mean = (past_len * avg_past + r * S) / 60.0
            var = sig * sig * (r ** 3) / 10800.0
        sd = SAFETY * math.sqrt(max(var, 1e-12))
        return norm_cdf((mean - o_twap) / sd)

    def snapshot(self):
        return dict(spot=self.spot[-1][1] if self.spot else None, twap=self.twap,
                    sigma1=self._sigma, n_spot=len(self.spot))
