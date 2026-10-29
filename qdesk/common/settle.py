#!/usr/bin/env python3
"""settle.py - the one settlement arithmetic for a round (INTERFACES.md section 5). stdlib only.

A round pays $1 per share of the winning side and $0 for the loser. `winner` is "Up" or "Down"
and comes from the chain labels / tokens map - never from a feature row (the feature store carries
no outcome on purpose). Fees are whatever the legs already paid (taker_fee at fill time).
"""


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def settle_pnl(legs, winner):
    """legs: [{"side": "Up"|"Down", "price", "size", "fee"}, ...] (size in shares, fee in dollars).
    Returns {"cost", "payout", "fees", "net", "pairs"}:
      cost   = sum(price * size)            dollars spent on all legs
      payout = sum(size) of legs on the winning side
      fees   = sum(fee)
      net    = payout - cost - fees
      pairs  = min(total Up shares, total Down shares)  (shares that were hedged both ways)
    winner None/"" (unsettled) -> payout 0 and net = -cost - fees, so callers must check the label.
    """
    cost = payout = fees = 0.0
    up = down = 0.0
    w = (winner or "").strip().capitalize() if isinstance(winner, str) else ""
    for leg in legs or []:
        side = str(leg.get("side", "")).strip().capitalize()
        price, size, fee = _f(leg.get("price")), _f(leg.get("size")), _f(leg.get("fee"))
        cost += price * size
        fees += fee
        if side == "Up":
            up += size
        elif side == "Down":
            down += size
        if w and side == w:
            payout += size
    return {"cost": round(cost, 6), "payout": round(payout, 6), "fees": round(fees, 6),
            "net": round(payout - cost - fees, 6), "pairs": round(min(up, down), 6)}


def brier(p_up, winner):
    """Squared error of a probability for Up against the outcome (0 = perfect, 1 = worst).
    winner "Up" -> y = 1, "Down" -> y = 0. Returns None when p_up is NA or winner is unknown."""
    p = _f(p_up, None)
    if p is None:
        return None
    w = (winner or "").strip().capitalize() if isinstance(winner, str) else ""
    if w not in ("Up", "Down"):
        return None
    y = 1.0 if w == "Up" else 0.0
    return round((p - y) ** 2, 6)
