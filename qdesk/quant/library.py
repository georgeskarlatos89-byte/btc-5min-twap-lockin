#!/usr/bin/env python3
"""library.py - the fixed hypothesis library of qdesk (used when the LLM stage gives nothing).

Every rule is written in the DSL of INTERFACES.md section 3 and must pass common/dsl.validate_rule and NOT match a
dead family of prior_evidence.json (quant/prereg.is_known_dead). Every prediction is an ESTIMATE: nothing here
has been measured yet; the whole point of the night is to measure it. Numbers in the entries (bps thresholds,
windows) are starting points, not fitted values.

Dead families to stay away from (prior_evidence.json): resting-bid / paired / timed / flow-filtered MAKERS,
the spot-gap stale-ask TAKER without momentum, and the late-round (t_el >= 240) favourite TAKER.

  python3 library.py            -> validates every rule, prints id / name / dead-family check
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

NO_BLACKOUT = {"f": "macro_blackout", "op": "==", "v": 0}
QUIET_HOURS = [0, 1, 2, 3, 4, 5, 6]          # UTC Asia session (S8 router: lowest realised vol)
US_HOURS = [13, 14, 15, 16, 17, 18, 19, 20]  # UTC US cash session


def _rule(id_, name, market, side, when, leg, exit_, limits, pred, mech, loses, fail, novelty, side_field=None):
    r = {"id": id_, "name": name, "market": market, "side": side}
    if side_field:
        r["side_field"] = side_field
    r.update({"entry": {"when": when, "leg": leg}, "exit": exit_, "limits": limits,
              "prediction": dict(pred, label="ESTIMATE"), "mechanism": mech, "who_loses": loses,
              "why_this_could_fail": fail, "novelty_vs_prior": novelty})
    return r


TAKER5 = {"type": "taker", "price": "ask", "size_usd": 5}
HOLD = {"mode": "hold_to_settle"}
LIM1 = {"max_trades_per_round": 1, "cooldown_s": 30, "max_rounds_per_day": 80}

HYPOTHESES = [
    _rule("LIB-01", "momentum-sign taker after a 4 bps 5-s move, mid round, quiet vol", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [60, 230]}, {"f": "abs:mom_5s", "op": ">=", "v": 4},
           {"f": "spot_vol_30s", "op": "<=", "v": 2.5}, {"f": "ask", "op": "<=", "v": 0.80}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.85), HOLD, LIM1, {"net_per_dollar": 0.008, "trades_per_day": 20},
          "A 4 bps move inside 5 s in a quiet-vol regime is a jump, not noise; the Polymarket book reprices with the "
          "1.4 s source lag plus the makers' own loop, so the ask on the side spot just moved toward is briefly stale.",
          "Makers whose quotes lag the Chainlink feed and takers who fade the jump expecting instant mean reversion.",
          "The 4 bps jump may be fully priced by the time the ask is checked 100 ms later; the taker fee (up to 1.75c) "
          "is the same size as the expected mispricing; quiet-vol regimes may simply have too few 4 bps jumps to trade.",
          "Uses momentum (mom_5s) and a vol-regime gate; the dead stale-ask family keyed only on the spot-vs-open gap.",
          side_field="mom_5s"),
    _rule("LIB-02", "late-round momentum continuation, quiet hours (200-270 s)", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [200, 270]}, {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 4},
           {"f": "abs:mom_5s", "op": ">=", "v": 1}, {"f": "ask", "op": "<=", "v": 0.80},
           {"f": "session_hour", "op": "in", "v": QUIET_HOURS}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.85), HOLD, {"max_trades_per_round": 1, "cooldown_s": 30, "max_rounds_per_day": 60},
          {"net_per_dollar": 0.01, "trades_per_day": 25},
          "With 30-100 s left and spot 4+ bps from the open TWAP, the 60-s settlement TWAP has little time to reverse; "
          "if the 5-s momentum still points the same way the ask <= 0.80 under-prices the lock-in.",
          "Makers who quote off the mid of the last minute instead of the TWAP arithmetic, and late fade takers.",
          "The S1 late-round study (t_el >= 240) found no edge because the leader is already priced; the 200-240 s part "
          "of this window may be no different, and the momentum filter may only remove the trades that were right.",
          "Requires momentum agreement (mom_5s) and quiet hours; S1 bought the favourite from any state after 240 s.",
          side_field="mom_5s"),
    _rule("LIB-03u", "early-round overreaction fade (buy Up after a 6 bps 5-s drop), ttl 60 s", "5m", "up",
          [{"f": "t_el", "op": "between", "v": [15, 90]}, {"f": "mom_5s", "op": "<=", "v": -6},
           {"f": "ask", "op": "<=", "v": 0.50}, {"f": "spot_vol_30s", "op": ">=", "v": 1.0}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.52), {"mode": "ttl", "ttl_s": 60}, LIM1, {"net_per_dollar": 0.006, "trades_per_day": 8},
          "Early in the round a sharp 6 bps drop pushes the Up ask under 0.50 while 4+ minutes remain; part of such "
          "spikes is wick noise that the next minute retraces, so the Up ask recovers and we sell to the bid at ttl.",
          "Takers who chase the spike and makers who pull Up bids too far on the first print.",
          "The S5 wick-fade recorder never showed a tradable reversion after fees; a 6 bps drop in 5 s is often the start "
          "of a cascade, not its end; the ttl exit pays a second fee and crosses the spread.",
          "Mean-reversion (against momentum) with a ttl exit; no prior rule faded spot moves as a taker."),
    _rule("LIB-03d", "early-round overreaction fade (buy Down after a 6 bps 5-s rise), ttl 60 s", "5m", "down",
          [{"f": "t_el", "op": "between", "v": [15, 90]}, {"f": "mom_5s", "op": ">=", "v": 6},
           {"f": "ask", "op": "<=", "v": 0.50}, {"f": "spot_vol_30s", "op": ">=", "v": 1.0}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.52), {"mode": "ttl", "ttl_s": 60}, LIM1, {"net_per_dollar": 0.006, "trades_per_day": 8},
          "Mirror of LIB-03u: a sharp 6 bps rise early in the round pushes the Down ask under 0.50; wick noise retraces "
          "and the Down ask recovers before the ttl sell.",
          "Takers who chase the spike and makers who pull Down bids too far on the first print.",
          "Same as LIB-03u: cascades continue more often than wicks revert; two taker fees; S5 found nothing.",
          "Mean-reversion with a ttl exit, mirror side of LIB-03u."),
    _rule("LIB-04", "book-lag scalp: 3 bps 2-s jump, no sweep, sell after 30 s", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [30, 240]}, {"f": "abs:mom_2s", "op": ">=", "v": 3},
           {"f": "sweep_flag", "op": "==", "v": 0}, {"f": "ask", "op": "between", "v": [0.35, 0.65]},
           {"f": "spread", "op": "<=", "v": 0.02}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.66), {"mode": "ttl", "ttl_s": 30},
          {"max_trades_per_round": 2, "cooldown_s": 20, "max_rounds_per_day": 100}, {"net_per_dollar": 0.004, "trades_per_day": 30},
          "A 3 bps jump within 2 s that the tape has not reacted to yet (no sweep) means the book is behind the feed; "
          "buy the momentum side in the tight 0.35-0.65 zone and sell to the bid 30 s later once the book has caught up.",
          "Makers whose quotes lag the Chainlink feed by the source lag plus their own requote time.",
          "Our own reaction is ~0.1 s after a feed that is already 1.44 s late; the makers who matter are faster; the "
          "two taker fees cost up to 3.5c per share against a hoped-for 1-2c move.",
          "Two taker legs with a 30 s ttl; prior families held to settlement or rested bids."
          , side_field="mom_2s"),
    _rule("LIB-05", "active-regime momentum, US hours, hold", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [90, 230]}, {"f": "abs:mom_5s", "op": ">=", "v": 5},
           {"f": "spot_vol_30s", "op": ">=", "v": 3.0}, {"f": "ask", "op": "between", "v": [0.50, 0.78]},
           {"f": "session_hour", "op": "in", "v": US_HOURS}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.80), HOLD, LIM1, {"net_per_dollar": 0.008, "trades_per_day": 15},
          "In the active US session a 5 bps 5-s move with high 30-s vol tends to continue (trend persistence inside "
          "cascades); buying the momentum side at 0.50-0.78 with 70-210 s left captures the drift to settlement.",
          "Mean-reversion takers who sell the momentum side into a trend, and slow makers.",
          "High-vol regimes mean the 60-s TWAP can reverse fully in 70 s; the book in US hours is the sharpest so the "
          "ask is rarely stale; momentum persistence in 5-minute BTC is weak and may be negative after fees.",
          "Vol-regime and session gate on a momentum taker; the dead taker families had no vol or momentum field.",
          side_field="mom_5s"),
    _rule("LIB-06", "15m momentum continuation after an 8 bps displacement", "15m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [300, 720]}, {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 8},
           {"f": "abs:mom_5s", "op": ">=", "v": 1}, {"f": "ask", "op": "<=", "v": 0.80}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.85), HOLD, {"max_trades_per_round": 1, "cooldown_s": 60, "max_rounds_per_day": 40},
          {"net_per_dollar": 0.01, "trades_per_day": 10},
          "The 15-minute market is thinner and its makers slower; an 8 bps displacement with 3-10 minutes left and "
          "momentum still in the same direction is under-priced at an ask <= 0.80.",
          "Makers who copy 5m-market quoting to the 15m book, and fade takers.",
          "The 15m book may be too thin to fill $5 at the ask 100 ms later; fewer rounds per day (96) means the trade "
          "count may never reach 30 out of sample; 15m assets may not even be in the BME recording (check first).",
          "First 15m rule; the dead families were measured on 5m only.",
          side_field="mom_5s"),
    _rule("LIB-07", "bid-heavy book + momentum agreement (imb3 >= 0.65)", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [60, 230]}, {"f": "abs:mom_2s", "op": ">=", "v": 2},
           {"f": "imb3", "op": ">=", "v": 0.65}, {"f": "ask", "op": "<=", "v": 0.70}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.72), {"mode": "ttl", "ttl_s": 45}, LIM1, {"net_per_dollar": 0.005, "trades_per_day": 15},
          "When the top three bid levels of a side hold 65%+ of the near-touch size AND spot just moved toward that side, "
          "the ask is about to be lifted by others; buying first and selling to the (now higher) bid 45 s later.",
          "Makers resting asks that are about to be run through, and takers who arrive after the requote.",
          "Order-book imbalance on Polymarket is dominated by a few large makers' inventory, not informed flow; the "
          "inspect-order-book lab found no book rule with an edge (34/34 FAIL) although those were maker rules.",
          "Taker with an imbalance gate and a ttl exit; the dead flow-filtered family was a maker leg.",
          side_field="mom_2s"),
    _rule("LIB-08", "taker-flow confirmed momentum (50+ shares lifted this side in 10 s)", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [45, 230]}, {"f": "flow_buy_10s", "op": ">=", "v": 50},
           {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 3}, {"f": "abs:mom_5s", "op": ">=", "v": 2},
           {"f": "ask", "op": "<=", "v": 0.75}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.78), HOLD, LIM1, {"net_per_dollar": 0.006, "trades_per_day": 15},
          "Takers lifting 50+ shares of a side in 10 s while spot is 3+ bps on that side and still moving means the "
          "informed flow agrees with the feed; join the flow before the ask steps up.",
          "Makers who keep re-posting asks at the old level against informed takers.",
          "Taker flow on Polymarket BTC rounds is mostly rebate farming (norm1e69 buys both sides) and carries no "
          "information; following it adds the fee on top of theirs.",
          "Flow confirmation (flow_buy_10s) combined with momentum; no prior taker rule read the tape.",
          side_field="mom_5s"),
    _rule("LIB-09u", "cheap-tail recovery (Up ask <= 0.10 with spot back near open, 120-240 s)", "5m", "up",
          [{"f": "t_el", "op": "between", "v": [120, 240]}, {"f": "ask", "op": "<=", "v": 0.10},
           {"f": "spot_bps_vs_open", "op": ">=", "v": -1.5}, {"f": "mom_5s", "op": ">=", "v": 1}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.12), HOLD, LIM1, {"net_per_dollar": 0.03, "trades_per_day": 4},
          "After a move the losing side's ask collapses to <= 0.10 and stays there while spot has already come back to "
          "within 1.5 bps of the open and is rising: the tail is priced as dead although the round is a coin flip again.",
          "Makers who left stale cheap asks after the first move and takers who overpaid for the favourite.",
          "Cheap asks at 0.10 are usually tiny (a few shares), so $5 does not fill; the settlement TWAP still carries the "
          "earlier move so 'spot back at open' is not 'TWAP back at open'; wins are rare and the sample will be small.",
          "Conditioned on a reversal back to the open plus momentum, not on a static spot gap; buys the cheap side."),
    _rule("LIB-09d", "cheap-tail recovery (Down ask <= 0.10 with spot back near open, 120-240 s)", "5m", "down",
          [{"f": "t_el", "op": "between", "v": [120, 240]}, {"f": "ask", "op": "<=", "v": 0.10},
           {"f": "spot_bps_vs_open", "op": "<=", "v": 1.5}, {"f": "mom_5s", "op": "<=", "v": -1}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.12), HOLD, LIM1, {"net_per_dollar": 0.03, "trades_per_day": 4},
          "Mirror of LIB-09u for the Down side: the Down ask is <= 0.10 while spot is back within 1.5 bps of the open "
          "and falling.",
          "Makers who left stale cheap asks after the first move and takers who overpaid for the favourite.",
          "Same as LIB-09u: tiny asks, TWAP memory of the earlier move, small samples.",
          "Mirror side of LIB-09u."),
    _rule("LIB-10", "liquidation-cascade continuation (Moon Dev long liquidations >= $2M in 5 min)", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [30, 200]}, {"f": "liq_long_5m_usd", "op": ">=", "v": 2000000},
           {"f": "mom_2s", "op": "<=", "v": -1}, {"f": "ask", "op": "<=", "v": 0.70}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.72), HOLD, LIM1, {"net_per_dollar": 0.01, "trades_per_day": 3},
          "A burst of long liquidations ($2M+ in 5 min) is forced selling that continues for minutes; while spot is "
          "still ticking down, buy Down at <= 0.70 and hold to settlement (the S46 cascade idea, now as a taker).",
          "Leveraged longs being liquidated and makers who quote the round as a fair coin during a cascade.",
          "The liquidation feed is 30-s polled and the BME history has NO liquidation column (NA -> the rule never fires "
          "in the backtest, so it cannot pass tonight); the cascade is usually over by the time the total crosses $2M.",
          "External-data conditioned entry; no prior rule used the liquidation feed as a taker trigger.",
          side_field="mom_2s"),
    _rule("LIB-11", "both-asks discount: sum of asks <= 0.96, buy both sides as taker", "5m", "both",
          [{"f": "t_el", "op": "between", "v": [10, 290]}, {"f": "sum_ask2", "op": "<=", "v": 0.96},
           {"f": "ask", "op": ">=", "v": 0.03}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.95), HOLD, {"max_trades_per_round": 1, "cooldown_s": 5, "max_rounds_per_day": 120},
          {"net_per_dollar": 0.01, "trades_per_day": 5},
          "When the Up ask plus the Down ask is <= 0.96 both sides can be bought for less than the $1 payout; the two "
          "taker fees (<= 3.5c) leave a locked profit if both legs fill at the 100 ms check.",
          "Makers who moved one side's ask down without lifting the other, in the 100 ms before the arb closes.",
          "Both legs must fill 100 ms later; the leg that was mispriced is the one that disappears (adverse selection "
          "on the fill), leaving a naked leg - exactly the EXP-D failure mode; sum_ask2 <= 0.96 may never occur.",
          "Two taker legs at the ask (pair arb); the dead paired family rested maker bids."),
    _rule("LIB-12", "quiet-hours early displacement taker (2 bps in the first 40 s, vol <= 3)", "5m", "signal_sign",
          [{"f": "t_el", "op": "between", "v": [5, 40]}, {"f": "session_hour", "op": "in", "v": [0, 1, 2, 3, 4, 5]},
           {"f": "spot_vol_30s", "op": "<=", "v": 3}, {"f": "abs:spot_bps_vs_open", "op": ">=", "v": 2},
           {"f": "abs:mom_2s", "op": ">=", "v": 0.5}, {"f": "ask", "op": "<=", "v": 0.60}, NO_BLACKOUT],
          dict(TAKER5, max_price=0.62), HOLD, {"max_trades_per_round": 1, "cooldown_s": 60, "max_rounds_per_day": 40},
          {"net_per_dollar": 0.005, "trades_per_day": 15},
          "In the Asia quiet hours the book opens near 0.50/0.50 and reprices slowly; a 2 bps displacement in the first "
          "40 s that is still moving, bought at <= 0.60, captures the gap before the makers catch up.",
          "Coin-flip makers who quote both sides at 0.49-0.52 at the open without watching spot.",
          "S8 found Asia the worst session and S2 showed most early moves revert; 2 bps in 40 s is inside the noise of "
          "spot_vol_30s; the open TWAP is only known 3 s after the start.",
          "Taker with quiet-hours, vol cap and momentum; the dead early maker families quoted 0.45-0.49 both sides.",
          side_field="spot_bps_vs_open"),
]


def get_hypotheses():
    import copy
    return copy.deepcopy(HYPOTHESES)


def main():
    from qdesk.common.dsl import validate_rule, canonical_hash
    try:
        from qdesk.quant.prereg import is_known_dead
    except Exception:
        is_known_dead = None
    bad = 0
    ids = set()
    for r in HYPOTHESES:
        errs = validate_rule(r)
        dead = is_known_dead(r) if is_known_dead else "?"
        if r["id"] in ids:
            errs.append("duplicate id")
        ids.add(r["id"])
        flag = "OK " if not errs and not dead else "BAD"
        bad += 1 if (errs or dead) else 0
        print(f"{flag} {r['id']:<8} {canonical_hash(r)[:12]} dead={dead} {r['name']}")
        for e in errs:
            print("     -", e)
    print(f"{len(HYPOTHESES)} rules, {bad} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
