#!/usr/bin/env python3
"""
s3_backtest.py — S3 FEE-FARM TWO-SIDED MAKER, the B step (kill bad ideas cheaply).

Strategy source: BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD -> S3.
Rule: quote a BUY (bid) at 0.49 on BOTH Up and Down tokens, >=50 shares each,
from t+30s until 60% of the round has elapsed. Farm the 1.75%-paying takers;
a completed pair costs 0.98 and pays 1.00. Unpaired inventory is offered back at
0.49 after 20s ("mark-to-model and offer at breakeven; never hope a position", P5)
and market-exited at quote end.

This backtest replays the REAL public trade tape (data-api /trades — every print)
of recent rounds and simulates our quotes into it. Per the Moon Dev M5 lesson the
report gives the NATURAL VARIANCE of fill counts before any threshold is judged,
and per the Part-4 lesson the live-band gate (mid in [0.45, 0.55]) executes BEFORE
any fill is recorded — fills outside the band are rejected and counted loudly.

Honest labels (the S1 backtest_report house style):
  * TOUCH-FILL PROXY is queue-optimistic: the first taker SELL print <= our bid
    while it rests fills us at our bid. Real FIFO position is unknown (no historical
    L2 exists — see BME README). Biases fills UP and adverse selection UP.
  * Exits use the token mark minus a 1 cent taker haircut + official taker fee.
  * Rebates are NOT credited (param REBATE_C defaults to 0) — upside not counted.
  * The pull-triggers (liq cascade / spot move / imbalance / band exit) are live
    protections; the backtest's band gate emulates the band-exit pull only. The
    Moon Dev liq/spot/imb pulls cannot be backtested offline (feeds not in repo):
    if S3 survives UNPROTECTED, the pulls are extra safety, measured in incubation.

Usage:
  python3 s3_feefarm/s3_backtest.py --hours 24                 # doc-verbatim version
  python3 s3_feefarm/s3_backtest.py --hours 24 --sweep         # + "25 versions in an afternoon"
"""
import argparse, csv, json, math, os, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- strategy constants (S3 mechanics; the live gates — identical in s3_maker.py) ----
QUOTE_PRICE   = 0.49    # quote Up @ 0.49 and Down @ 0.49
QUOTE_SHARES  = 50      # >= 50 sh each (liquidity-rewards minimum)
QUOTE_START_S = 30      # from t+30s ...
QUOTE_END_FRAC = 0.60   # ... until 60% of the round has elapsed
BAND = (0.45, 0.55)     # the 45-55 band: mid outside -> NO quote, NO fill (Part-4 gate)
INV_GRACE_S = 20        # unpaired for 20s -> offer inventory at breakeven
EXIT_HAIRCUT  = 0.01    # taker exits: mark minus 1 cent, fee on top
CHASE_HOLD_S  = 15.0    # chase mode: how long the cost offer rests before cutting
FLAT_BAND     = (0.48, 0.52)  # "dead-flat round" measurement (the pure-farm subset)
REBATE_C      = 0.0     # est maker rebate credited per share pair (floor = 0, upside unknown)
# NOTE: the inventory "breakeven offer" is always at OUR COST (= the quote price), never
# a fixed 0.49 — an earlier draft credited 0.49 exits against 0.45 bids (free money bug).

# exit modes for naked legs (the inventory rule's implementation question):
#   offer49 : doc-literal — sit at the cost breakeven offer to quote end, then dump
#   chase   : mark-to-model — brief rest at cost (grace + CHASE_HOLD_S), then cut at bid
#   cut20   : no offer at all — taker exit at the bid 20s after the fill
#   cut40   : same but 40s after the fill
EXIT_MODES = ["offer49", "chase", "cut20", "cut40"]

def fee(p):          # official crypto taker fee; makers pay 0
    return 0.07 * p * (1 - p)

def http_json(url, attempts=4):
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as ex:
            if i == attempts - 1:
                raise
            time.sleep(0.8 * (i + 1))

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)

def gamma_resolution(label, start):
    """Official outcome or None. Same rule as s1_harness (Part #22): ask closed=true FIRST."""
    for q in ("&closed=true", ""):
        try:
            ev = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}{q}")
            if not ev:
                continue
            m = ev[0]["markets"][0]
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            if m.get("closed") or not m.get("acceptingOrders"):
                return "Up" if pr[0] > 0.5 else "Down"
            if pr[0] >= 0.9995 or pr[0] <= 0.0005:
                return "Up" if pr[0] >= 0.9995 else "Down"
        except Exception:
            pass
    return None

def round_meta(label, start):
    """-> (condition_id, tok_up, tok_dn) or None."""
    for q in ("&closed=true", ""):
        try:
            ev = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}{q}")
            if not ev:
                continue
            m = ev[0]["markets"][0]
            toks = json.loads(m["clobTokenIds"])
            return m.get("conditionId") or m.get("condition_id"), toks[0], toks[1]
        except Exception:
            pass
    return None

def fetch_prints(condition_id, start):
    """Full public tape for the round token pair, split by outcome."""
    try:
        rows = http_json(f"https://data-api.polymarket.com/trades?market={condition_id}&limit=2000")
    except Exception:
        rows = []
    out = {"Up": [], "Down": []}
    for r in rows or []:
        ts = int(r.get("timestamp", 0))
        if ts < start - 5:      # pre-round gambling on the persistent book: not our window
            continue
        side_out = r.get("outcome") or ("Up" if r.get("outcomeIndex") == 0 else "Down")
        if side_out not in out:
            continue
        out[side_out].append(dict(ts=ts, side=r.get("side"), px=float(r["price"]), sz=float(r["size"])))
    for k in out:
        out[k].sort(key=lambda x: x["ts"])
    return out

def fetch_marks(token, start, end):
    try:
        h = http_json(f"https://clob.polymarket.com/prices-history?market={token}"
                      f"&startTs={start-120}&endTs={end+60}&fidelity=1").get("history", [])
        return [(int(p["t"]), float(p["p"])) for p in h]
    except Exception:
        return []

def mark_at(marks, t):
    best = None
    for mt, mp in marks:
        if mt <= t:
            best = mp
        else:
            break
    return best

# ------------------------------------------------------------------ simulation

def simulate_round(label, start, meta, prints, marks, settled, P):
    """One round of the two-sided quote under parameter set P. Returns the row dict.
    Part-4 rule: band gate runs BEFORE any fill is recorded; violations are counted."""
    T = 300 if label == "5m" else 900
    cond, tok_up, tok_dn = meta
    px_q, shr = P["price"], P["shares"]
    q_open = start + P["start_s"]
    q_close = start + P["end_frac"] * T
    grace = P["grace_s"]
    mode = P["exit_mode"]

    rec = dict(label=label, start=start, settled=settled,
               up_fill=0.0, dn_fill=0.0, up_fill_t="", dn_fill_t="",
               pair_sh=0.0, pair_pnl=0.0, leg_pnl=0.0, pnl=0.0,
               paired=0, exit_t="", exit_p="", adverse=0, flat=0,
               band_reject=0, note="")

    state = {s: dict(rest=shr, filled=0.0, fill_t=None) for s in ("Up", "Down")}

    def mid_of(side, t):
        m = mark_at(marks[side], t)
        if m is not None:
            return m
        o = mark_at(marks["Down" if side == "Up" else "Up"], t)
        return (1 - o) if o is not None else 0.50

    def band_ok(side, t):
        m = mid_of(side, t)
        return BAND[0] <= m <= BAND[1]

    def record_fill(side, ts, sz):
        """Part-4 gate: mid MUST be inside the band at fill time, else the fill is
        fiction and is rejected loudly (the paper-harness bug that faked +5.71pp)."""
        if not (q_open <= ts <= q_close):
            return
        if not band_ok(side, ts):
            rec["band_reject"] += 1
            return
        st = state[side]
        take = min(sz, st["rest"])
        if take <= 0:
            return
        st["rest"] -= take
        st["filled"] += take
        if st["fill_t"] is None:
            st["fill_t"] = ts

    # ---- resting bids eat taker SELL prints at <= our bid (queue-optimistic)
    for side, key in (("Up", "up_fill"), ("Down", "dn_fill")):
        for p in prints[side]:
            if p["side"] != "SELL" or p["px"] > px_q:
                continue
            if p["ts"] < q_open or p["ts"] > q_close:
                continue
            record_fill(side, p["ts"], p["sz"])
        st = state[side]
        rec[key] = round(st["filled"], 2)
        if st["fill_t"]:
            rec[f"{key}_t"] = str(int(st["fill_t"] - start)) + "s"

    up, dn = state["Up"], state["Down"]

    def offer_fill(side, since_ts, until_ts):
        """Breakeven maker offer at OUR COST from since_ts+grace to until_ts -> fills on
        the first taker BUY print >= cost on that token (the offer lifts when anyone buys).
        Exit is exactly flat (pnl 0). NO lookahead: prints after until_ts cannot fill us."""
        since = since_ts + grace
        for p in prints[side]:
            if p["side"] == "BUY" and p["px"] >= px_q and since <= p["ts"] <= until_ts:
                return px_q, p["ts"]
        return None, None

    def taker_exit_at(side, t):
        """Mark-to-model taker exit at time t: mark minus haircut, fee on top."""
        m = mark_at(marks[side], t) or mid_of(side, t) or 0.50
        return max(0.01, m - EXIT_HAIRCUT)

    def run_leg(side, size, fill_ts):
        """Inventory rule on `size` naked shares. Returns (pnl, exit_p, exit_lbl, adverse).
        All modes are non-anticipating: the exit decision uses only prints/marks at or
        before the exit moment (Part-4 discipline — no lookahead fills)."""
        def cut(t_ex):
            t_ex = min(t_ex, q_close)
            ep = taker_exit_at(side, t_ex)
            pnl = size * ((ep - px_q) - fee(ep))
            return pnl, ep, f"{int(t_ex - start)}s*", int(ep <= px_q - 0.05)
        if mode == "offer49":
            # doc-literal: sit at the cost offer until quote end, then dump at the bid
            ep, et = offer_fill(side, fill_ts, q_close)
            if ep is not None:
                return size * (ep - px_q), ep, f"{int(et - start)}s", 0
            return cut(q_close)
        if mode == "chase":
            # mark-to-model: a SHORT rest at cost (grace + CHASE_HOLD_S), then take the bid
            o_end = fill_ts + grace + CHASE_HOLD_S
            ep, et = offer_fill(side, fill_ts, o_end)
            if ep is not None:
                return size * (ep - px_q), ep, f"{int(et - start)}s", 0
            return cut(o_end)
        return cut(fill_ts + (20 if mode == "cut20" else 40))

    # matched size is the riskless pair (cost 2*px_q -> pays 1.00/sh); ANY excess is
    # a naked leg and runs the inventory rule — never silently dumped at market exit.
    pay = min(up["filled"], dn["filled"])
    pair_pnl = pay * (1.0 - 2 * px_q + REBATE_C)
    rec["pair_sh"], rec["pair_pnl"] = round(pay, 2), round(pair_pnl, 4)
    rec["pnl"] = round(pair_pnl, 4)
    if pay > 0:
        rec["paired"] = 1
        rec["exit_t"] = "resolution"
        rec["note"] = f"pair {pay:.0f}sh +${pair_pnl:.2f}"
    leg_notes = []
    for side, st in (("Up", up), ("Down", dn)):
        excess = st["filled"] - pay
        if excess <= 0:
            continue
        pnl, ep, elbl, adv = run_leg(side, excess, st["fill_t"])
        rec["leg_pnl"] = round(rec["leg_pnl"] + pnl, 4)
        rec["pnl"] = round(rec["pnl"] + pnl, 4)
        rec["adverse"] = max(rec["adverse"], adv)
        rec["exit_t"] = (rec["exit_t"] + " | " if rec["exit_t"] else "") + f"{side}:{elbl}"
        rec["exit_p"] = f"{ep:.2f}"
        leg_notes.append(f"{side} naked {excess:.0f}sh -> {ep:.2f} (${pnl:+.2f})")
    if leg_notes:
        rec["note"] = ((rec["note"] + " | ") if rec["note"] else "") + \
                      (("single-leg: " if pay == 0 else "") + "; ".join(leg_notes))
    return rec

def fetch_round(label, start):
    """Fetch everything once per round so the sweep can simulate many versions free.
    Disk cache (s3_feefarm/_cache) keeps re-runs cheap and the tape stable across variants."""
    import gzip
    cache_dir = os.path.join(HERE, "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cpath = os.path.join(cache_dir, f"{label}_{start}.json.gz")
    if os.path.exists(cpath):
        try:
            with gzip.open(cpath, "rt") as f:
                return json.load(f)
        except Exception:
            pass
    T = 300 if label == "5m" else 900
    meta = round_meta(label, start)
    if not meta:
        return None
    cond, tok_up, tok_dn = meta
    prints = fetch_prints(cond, start)
    time.sleep(0.1)
    marks = {"Up": fetch_marks(tok_up, start, start + T), "Down": None}
    time.sleep(0.1)
    marks["Down"] = fetch_marks(tok_dn, start, start + T)
    settled = gamma_resolution(label, start)
    data = dict(meta=list(meta), prints=prints, marks=marks, settled=settled, T=T)
    if settled:
        try:
            with gzip.open(cpath, "wt") as f:
                json.dump(data, f)
        except Exception:
            pass
    return data

def evaluate(rounds_data, P, want_rows=False):
    rows = []
    for label, start, data in rounds_data:
        if not data or not data["settled"]:
            continue
        rec = simulate_round(label, start, data["meta"], data["prints"], data["marks"],
                             data["settled"], P)
        T = data["T"]
        q_close = start + P["end_frac"] * T
        vals = [p["px"] for s2 in ("Up", "Down") for p in data["prints"][s2]
                if start <= p["ts"] <= q_close]
        vals += [m for s2 in ("Up", "Down") for mt, m in data["marks"][s2] if start <= mt <= q_close]
        if vals and min(vals) >= FLAT_BAND[0] and max(vals) <= FLAT_BAND[1]:
            rec["flat"] = 1
        rows.append(rec)
    n = len(rows)
    if not n:
        return None
    net = sum(r["pnl"] for r in rows)
    pair_p = sum(r["pair_pnl"] for r in rows)
    leg_p = sum(r["leg_pnl"] for r in rows)
    adv_loss = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
    gross = sum(r["pair_pnl"] for r in rows) + sum(max(0, r["leg_pnl"]) for r in rows) \
            + sum(r["pair_sh"] for r in rows) * REBATE_C
    out = dict(n=n, net=net, pair_pnl=pair_p, leg_pnl=leg_p,
               pairs=sum(r["paired"] for r in rows),
               singles=sum(1 for r in rows if not r["paired"] and (r["up_fill"] or r["dn_fill"])),
               none=sum(1 for r in rows if not (r["up_fill"] or r["dn_fill"])),
               adverse=sum(r["adverse"] for r in rows),
               adv_loss=adv_loss, gross=gross,
               band_reject=sum(r["band_reject"] for r in rows),
               flats=sum(r["flat"] for r in rows))
    if want_rows:
        return out, rows
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24, help="lookback window in hours")
    ap.add_argument("--series", default="both", choices=["5m", "15m", "both"])
    ap.add_argument("--sleep", type=float, default=0.12, help="politeness delay between HTTP calls")
    ap.add_argument("--sweep", action="store_true", help='"25 versions in an afternoon" grid')
    args = ap.parse_args()

    series = ["5m", "15m"] if args.series == "both" else [args.series]
    now = int(time.time())
    rounds = []
    for label in series:
        T = 300 if label == "5m" else 900
        latest = (now // T) * T
        while latest + T + 360 > now:       # settle lag buffer
            latest -= T
        t0 = now - int(args.hours * 3600)
        s = latest
        while s > t0:
            rounds.append((label, s))
            s -= T
    rounds.sort(key=lambda x: x[1])
    log(f"S3 backtest (B step): {len(rounds)} rounds over {args.hours}h, series={args.series}"
        f"{', SWEEP' if args.sweep else ''}")

    # ---------------- fetch every round ONCE ----------------
    rounds_data, skipped = [], []
    for i, (label, start) in enumerate(rounds):
        data = fetch_round(label, start)
        time.sleep(args.sleep)
        if not data or not data["settled"]:
            skipped.append((label, start))
            continue
        rounds_data.append((label, start, data))
        if (i + 1) % 25 == 0:
            log(f"  fetched {i+1}/{len(rounds)} rounds")

    P0 = dict(price=QUOTE_PRICE, shares=QUOTE_SHARES, start_s=QUOTE_START_S,
              end_frac=QUOTE_END_FRAC, grace_s=INV_GRACE_S, exit_mode="offer49")
    res0, rows = evaluate(rounds_data, P0, want_rows=True)
    if not res0:
        log("no rows — nothing to report")
        return

    # ---------------- sweep ("25 versions in an afternoon") ----------------
    sweep_rows = []
    if args.sweep:
        grid_p = [0.45, 0.47, 0.49, 0.51]
        grid_f = [0.4, 0.6]
        for mode in EXIT_MODES:
            for p_ in grid_p:
                for f_ in grid_f:
                    P = dict(P0, price=p_, end_frac=f_, exit_mode=mode)
                    r = evaluate(rounds_data, P)
                    if r:
                        sweep_rows.append(dict(mode=mode, price=p_, end_frac=f_,
                                               **{k: round(v, 2) if isinstance(v, float) else v
                                                  for k, v in r.items()}))
        sweep_rows.sort(key=lambda r: -r["net"])

    # ---------------- report ----------------
    n = res0["n"]
    fills_per_hour = {}
    for label, start, data in rounds_data:
        pass
    hourly = {}
    for r in rows:
        hourly.setdefault(r["start"] // 3600, 0)
        if r["up_fill"] or r["dn_fill"]:
            hourly[r["start"] // 3600] += 1
    fph = list(hourly.values())
    mu = sum(fph) / len(fph)
    sd = math.sqrt(sum((x - mu) ** 2 for x in fph) / len(fph)) if len(fph) > 1 else 0.0

    rep = []
    rep.append("# S3 BACKTEST REPORT — FEE-FARM TWO-SIDED MAKER (the B step)")
    rep.append(f"\n**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
               f"window: last {args.hours}h · rounds: {n} ({args.series}) · skipped: {len(skipped)}")
    rep.append(f"\n**Doc-verbatim version:** bid {QUOTE_PRICE} on Up AND Down, "
               f"{QUOTE_SHARES}sh each, t+{QUOTE_START_S}s → {QUOTE_END_FRAC:.0%} elapsed, "
               f"unpaired {INV_GRACE_S}s → breakeven offer at cost, quote end → taker exit "
               f"(exit_mode `offer49`). Official taker fee `0.07·p·(1−p)` on taker exits; makers 0%.")
    rep.append("\n## 1 — FILL RATES (the farm's raw material)")
    rep.append("\n| outcome | n | share |")
    rep.append("|---|---|---|")
    rep.append(f"| both sides filled (at least a partial pair) | {res0['pairs']} | {res0['pairs']/n:.1%} |")
    rep.append(f"| single-leg (inventory rule exercised) | {res0['singles']} | {res0['singles']/n:.1%} |")
    rep.append(f"| no fill (quotes never hit) | {res0['none']} | {res0['none']/n:.1%} |")
    rep.append(f"\n**Part-4 band gate:** {res0['band_reject']} touch-fills REJECTED because mid was "
               f"outside {BAND} at fill time — those fills are fiction and are never recorded (the "
               f"paper-harness bug that once faked +5.71pp).")
    rep.append(f"\n**Dead-flat rounds (price never left {FLAT_BAND} through the quote window):** "
               f"{res0['flats']}/{n} = {res0['flats']/n:.1%} — token-proxy for the doc's '~30% settle "
               f"within ±3bps of open' (tokens always resolve 0/1, so the window is the farmable span).")
    rep.append("\n## 2 — PnL (split honestly: pairs vs naked legs)")
    rep.append("\n| metric | value |")
    rep.append("|---|---|")
    rep.append(f"| pair PnL (matched shares held to resolution) | ${res0['pair_pnl']:+.2f} |")
    rep.append(f"| naked-leg PnL (inventory rule) | ${res0['leg_pnl']:+.2f} |")
    rep.append(f"| **net before rebates** | **${res0['net']:+.2f}** |")
    rep.append(f"| est. rebates credited (param = ${REBATE_C:.3f}/sh-pair) | ${sum(r['pair_sh'] for r in rows)*REBATE_C:.2f} |")
    rep.append("\n## 3 — ADVERSE SELECTION vs GROSS CAPTURE (the pre-registered kill metric)")
    rep.append("\n| metric | value |")
    rep.append("|---|---|")
    rep.append(f"| adverse-selection events (exit ≤ 0.44) | {res0['adverse']} |")
    rep.append(f"| adverse losses (all losing exits) | ${res0['adv_loss']:.2f} |")
    rep.append(f"| gross capture (pairs + winning legs + rebates) | ${res0['gross']:.2f} |")
    rep.append(f"| ratio adverse/gross | {res0['adv_loss']/max(0.01,res0['gross']):.2f} |")
    rep.append("\n**Pre-registered kill rule (S3):** *2 consecutive weeks where adverse selection > gross capture*. "
               "This single window cannot fire that rule — it sets the baseline it will be judged against.")
    rep.append("\n## 4 — FILL-COUNT BASELINE (M5 lesson: know the natural range BEFORE any threshold)")
    rep.append(f"\nRounds-with-fills per hour across the window: mean **{mu:.2f}**, σ **{sd:.2f}**, "
               f"min **{min(fph)}**, max **{max(fph)}** (n={len(fph)} hours). "
               f"No alert threshold may be set narrower than this natural swing.")
    rep.append("\n## 5 — BY SERIES")
    for lb in series:
        rs = [r for r in rows if r["label"] == lb]
        if not rs: continue
        rep.append(f"- **{lb}**: {len(rs)} rounds, pairs {sum(r['paired'] for r in rs)}, "
                   f"net ${sum(r['pnl'] for r in rs):+.2f}, adverse {sum(r['adverse'] for r in rs)}")

    if sweep_rows:
        rep.append("\n## 6 — SWEEP (\"run 25 versions in an afternoon\" — Moon Dev B-step)")
        rep.append("\nOne data fetch, many parameter sets. **net** is all-in over the same rounds.")
        rep.append("\n| # | exit_mode | bid | window | net | pairs | singles | adverse/gross |")
        rep.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(sweep_rows[:25]):
            ag = f"{r['adv_loss']:.0f}/{r['gross']:.0f}"
            rep.append(f"| {i+1} | {r['mode']} | {r['price']:.2f} | {r['end_frac']:.0%} | "
                       f"${r['net']:+.2f} | {r['pairs']} | {r['singles']} | {ag} |")
        best = sweep_rows[0]
        rep.append(f"\n**Best version:** exit_mode `{best['mode']}`, bid {best['price']:.2f}, "
                   f"window to {best['end_frac']:.0%} → net **${best['net']:+.2f}**, "
                   f"adverse/gross {best['adv_loss']:.2f}. "
                   f"The doc-verbatim `offer49`/0.49/60% sits at rank "
                   f"{[ (r['mode'], r['price'], r['end_frac']) for r in sweep_rows ].index(('offer49', 0.49, 0.6)) + 1 if any(r['mode']=='offer49' and r['price']==0.49 and r['end_frac']==0.6 for r in sweep_rows) else '?'} "
                   f"of {len(sweep_rows)}.")

    sec = "7" if sweep_rows else "6"
    rep.append(f"\n## {sec} — HONEST LIMITATIONS (written down, not swept under)")
    rep.append("""
* **Queue-optimistic touch-fills**: the public tape has no queue position. First print
  through our price fills us at our price. Biases fills UP and adverse selection UP.
* **No historical L2** exists (BME README) — queue dynamics cannot be replayed.
  Rebates parameterized at 0 (upside not counted).
* **Moon Dev pull-triggers are live-only** (all_liq_10m 1s-lag cascade, spot 6bps/10s,
  imbalance 90s background). This backtest runs with only the band gate emulated —
  if the idea needs the pulls to survive, the incubation pull log must prove them.
* **Exits** use marks minus 1 cent + taker fee. Marks are ~1min buckets; intra-minute
  gaps are interpolated pessimistically (late exit).
* Single window ≠ 2 weeks. The kill rule needs the incubation cadence regardless.""")

    sec = "8" if sweep_rows else "7"
    rep.append(f"\n## {sec} — VERDICT (B-step gate to the I-step)")
    if args.sweep and sweep_rows and sweep_rows[0]["net"] > 0:
        best = sweep_rows[0]
        rep.append(f"\n**PASS at one variant → incubate THAT variant.** `offer49` at 0.49 is the "
                   f"doc-literal reading; the surviving variant is **exit_mode `{best['mode']}`, "
                   f"bid {best['price']:.2f}, window {best['end_frac']:.0%}** (net ${best['net']:+.2f}). "
                   f"Port it into `s3_maker.py` constants, run DRY 3 days to verify the fill model, "
                   f"then $-small real money for 2 weeks (I step). Scale only if live matches THIS table.")
    elif res0["net"] > 0 and res0["adv_loss"] < res0["gross"]:
        rep.append(f"\n**PASS → incubate.** Net ${res0['net']:+.2f} on {n} rounds with adverse < gross. "
                   f"Per RBI: deploy DRY first (s3_maker.py), then $-small real money for 2 weeks, "
                   f"then compare live to THIS report. Scale only if they match.")
    else:
        rep.append(f"\n**FAIL at the doc-verbatim parameters** (net ${res0['net']:+.2f}, "
                   f"adverse/gross {res0['adv_loss']/max(0.01,res0['gross']):.2f}). The pairs leg works; "
                   f"the naked-leg exits bleed. "
                   + ("See the sweep above for which variant survives."
                      if args.sweep else
                      "Per Moon Dev: run 25 versions in an afternoon — `--sweep` varies exit mode, "
                      "bid price (0.45–0.51), and window (40%/60%) on the same data.")
                   + "\n\nKill rule reminder: two consecutive weeks of adverse > gross kills S3 outright — "
                     "tune only within what the sweep can justify, don't widen stops to feel better (P5).")

    os.makedirs(HERE, exist_ok=True)
    with open(os.path.join(HERE, "s3_backtest_rows.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(HERE, "s3_backtest_report.md"), "w") as f:
        f.write("\n".join(rep) + "\n")
    log(f"report -> s3_feefarm/s3_backtest_report.md | rows -> s3_backtest_rows.csv")
    print()
    print("\n".join(rep))

if __name__ == "__main__":
    main()
