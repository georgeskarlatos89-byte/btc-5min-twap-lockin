#!/usr/bin/env python3
"""sanitize.py - strip look-ahead from round metadata (INTERFACES.md section 5). stdlib only.

External and gamma round objects carry the outcome ("winner", "closed", "outcomePrices",
"umaResolutionStatus", ...). Anything that is fed to a feature row or a hypothesis must first pass
through `round_meta`, which keeps ONLY the whitelisted descriptive keys. The outcome joins later,
in settle.py, from the tokens map.
"""
ROUND_META_WHITELIST = ("slug", "start", "end", "market", "up_token", "down_token", "condition_id", "tick", "fee_rate")

# common spellings of the same descriptive keys in gamma / tokens-map objects -> whitelist name
ALIASES = {
    "conditionId": "condition_id",
    "condition": "condition_id",
    "startTime": "start",
    "start_time": "start",
    "endTime": "end",
    "end_time": "end",
    "tick_size": "tick",
    "tickSize": "tick",
    "minimum_tick_size": "tick",
    "feeRate": "fee_rate",
    "fee": "fee_rate",
    "up": "up_token",
    "down": "down_token",
    "upToken": "up_token",
    "downToken": "down_token",
}

# documented examples of what is dropped (not exhaustive: everything outside the whitelist goes)
LOOKAHEAD_KEYS = ("winner", "closed", "outcome", "outcomePrices", "outcome_prices", "resolved",
                  "umaResolutionStatus", "resolutionSource", "lastTradePrice", "bestBid", "bestAsk",
                  "volume", "liquidity", "chain_outcome", "settle", "payout", "pnl", "R")


def round_meta(d):
    """Return a new dict with only ROUND_META_WHITELIST keys (aliases mapped first).
    Never mutates the input. Non-dict input -> {}."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        name = ALIASES.get(k, k)
        if name in ROUND_META_WHITELIST and name not in out:
            out[name] = v
    return out


def lookahead_fields(d):
    """Sorted list of keys in d that would be stripped - handy for the skeptic's report."""
    if not isinstance(d, dict):
        return []
    return sorted(k for k in d if ALIASES.get(k, k) not in ROUND_META_WHITELIST)
