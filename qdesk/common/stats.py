#!/usr/bin/env python3
"""stats.py - the small, deterministic statistics the desk relies on (INTERFACES.md section 5).
stdlib only (random, math). Every function with randomness takes a seed so two runs agree bit for bit.
"""
import math
import random


# copied from C:\g\tlfix\paired_maker\pm_replay_report.py (boot_ci) - alpha parameter added
def boot_ci(vals, n=2000, seed=7, alpha=0.05):
    """Bootstrap CI of the SUM of vals: n resamples with replacement, seed 7, sorted; returns the
    (alpha/2, 1-alpha/2) quantiles by index (int(q*n)). alpha=0.05 -> indices 50 and 1950 of 2000.
    Empty input -> (None, None)."""
    vals = list(vals)
    if not vals:
        return (None, None)
    rnd = random.Random(seed); m = len(vals); out = []
    for _ in range(n):
        out.append(sum(vals[rnd.randrange(m)] for _ in range(m)))
    out.sort()
    lo_i = min(max(int(alpha / 2 * n), 0), n - 1)
    hi_i = min(max(int((1 - alpha / 2) * n), 0), n - 1)
    return out[lo_i], out[hi_i]


def mean(vals):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else 0.0


def std(vals, ddof=1):
    vals = list(vals)
    n = len(vals)
    if n - ddof <= 0:
        return 0.0
    mu = mean(vals)
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (n - ddof))


def tstat(vals):
    """One-sample t statistic of the mean against 0 (sample std, ddof=1).
    Fewer than 2 values or zero variance -> 0.0 (no evidence either way)."""
    vals = list(vals)
    n = len(vals)
    if n < 2:
        return 0.0
    s = std(vals, 1)
    if s == 0.0:
        return 0.0
    return mean(vals) / (s / math.sqrt(n))


def max_drawdown(pnls):
    """Largest peak-to-trough fall of the cumulative sum (>= 0, in the same unit as pnls)."""
    peak = cum = 0.0
    dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        if peak - cum > dd:
            dd = peak - cum
    return dd


def mc_drawdown(pnls, capital=100.0, n=1000, seed=7):
    """Monte-Carlo max drawdown: shuffle the trade order n times (seed), take the max drawdown of
    each ordering as % of capital; returns {"p50", "p95", "n"}. Ordering 0 is the original order.
    Empty input -> p50 = p95 = 0.0."""
    pnls = [float(p) for p in pnls]
    if not pnls:
        return {"p50": 0.0, "p95": 0.0, "n": n}
    rnd = random.Random(seed)
    dds = []
    cur = list(pnls)
    for i in range(n):
        if i > 0:
            rnd.shuffle(cur)
        dds.append(max_drawdown(cur) / float(capital) * 100.0)
    dds.sort()
    p50 = dds[min(int(0.50 * n), n - 1)]
    p95 = dds[min(int(0.95 * n), n - 1)]
    return {"p50": round(p50, 4), "p95": round(p95, 4), "n": n}


def walk_forward(days, k=5, fit=6, test=3):
    """Rolling fit/test windows over sorted day strings. Window i uses days[i*test : i*test+fit] to
    fit and the next `test` days to test, so test slices are disjoint and step = test. Returns the
    LAST k windows that fit in the data (the most recent days get tested), fewer if the data is
    short, [] when len(days) < fit + test. Fit slices may overlap each other; test slices never do."""
    days = sorted(set(days))
    wins = []
    s = 0
    while s + fit + test <= len(days):
        wins.append({"fit": days[s:s + fit], "test": days[s + fit:s + fit + test]})
        s += test
    return wins[-k:] if k and len(wins) > k else wins


def bonferroni_alpha(n, base=0.05):
    """base / n for n simultaneous tests (n < 1 treated as 1)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1
    return base / max(1, n)
