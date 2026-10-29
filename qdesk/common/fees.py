#!/usr/bin/env python3
"""fees.py - Polymarket BTC up/down fee arithmetic (INTERFACES.md section 5). stdlib only.

Taker fee on the 5m/15m crypto markets: 0.07 * p * (1-p) per share (max 1.75c at p = 0.5),
rounded to 5 decimals as the exchange does per fill. Makers pay nothing and receive a daily
maker rebate (shown as a side column, never inside a pass/fail gate). The taker rebate tiers are
from "Taker Rebate Program.md" (7 tiers, 30-day weighted volume); they are informational only.
"""
FEE_RATE = 0.07
MAKER_REBATE_SHARE = 0.20

# (30-day weighted volume threshold USD, tier name, rebate fraction of taker fees) - ascending
TAKER_REBATE_TIERS = (
    (2_000.0, "Bronze", 0.03),
    (20_000.0, "Silver", 0.08),
    (200_000.0, "Gold", 0.18),
    (1_000_000.0, "Platinum", 0.32),
    (4_000_000.0, "Diamond", 0.44),
    (10_000_000.0, "Obsidian", 0.50),
)


def taker_fee(price, shares):
    """round(0.07 * p * (1 - p) * shares, 5). taker_fee(0.5, 100) == 1.75."""
    p = float(price)
    return round(FEE_RATE * p * (1.0 - p) * float(shares), 5)


def maker_fee(price, shares):
    """Makers pay no fee."""
    return 0.0


def maker_rebate(price, shares):
    """0.20 * the taker fee the same fill would have paid (an ESTIMATE of the daily rebate share)."""
    return round(MAKER_REBATE_SHARE * taker_fee(price, shares), 5)


def taker_rebate_pct(wv_30d_usd):
    """('None', 0.0) under $2,000 of 30-day weighted volume, then ('Bronze', 0.03) ... ('Obsidian', 0.50)."""
    try:
        wv = float(wv_30d_usd)
    except (TypeError, ValueError):
        return ("None", 0.0)
    name, pct = "None", 0.0
    for threshold, tier, rebate in TAKER_REBATE_TIERS:
        if wv >= threshold:
            name, pct = tier, rebate
    return (name, pct)
