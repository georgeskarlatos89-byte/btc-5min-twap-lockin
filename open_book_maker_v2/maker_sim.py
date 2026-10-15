#!/usr/bin/env python3
"""
maker_sim.py (v2 + variants) - maker quoting simulator (paper mode; never places orders)

  --replay F [F2 ...]      replay recorder JSONL files (book_recorder.py or bme_to_replay.py)
                           through the SAME strategy/fill code as the bot. Files are processed
                           one at a time (one file = one day) to keep memory flat.
  --labels CSV             chain-verified labels (start_time,condition_id,chain_outcome) used
                           to settle replayed rounds without touching gamma. Rounds not in the
                           file fall back to gamma events (closed + outcomePrices).
  --cfg JSON               VariantCfg as JSON (VARIANT-EXPERIMENT-PLAN.md). Omit = baseline V0.
  --out DIR                output folder (default: this folder). One folder per variant run.
  --report                 evaluate the gate from DIR/sim_rounds.csv and exit:
       --test-days D [D..] rows used for every R-criterion (held-out days)
       --fit-days D [D..]  rows reported separately as "fit" (never used for the verdict)
       --pilot-capital USD capital used by R5 (worst day > -2% of it); default 1000

Outputs in DIR: sim_rounds.csv (one row per settled round, with condition_id + outcome so
chain_verify.py can audit it), sim_fills.jsonl, maker_stats.json (the old Phase-A gate),
run_meta.json (cfg, code hash, corpus hash), gate_result.json (R1-R6 with CIs, every run)
and gate_pass.json ONLY when R1-R6 all pass on the test days (plan R7).

Revival criteria (plan section 4), all on test days:
  R1 >= 200 settled rounds            R2 edge per filled share > 0, bootstrap CI low > 0
  R3 fill win rate: point >= 50% AND CI low > break-even (48.5% in the plan; the measured
     break-even = mean cost per share is reported next to it)
  R4 edge vs fair 5 s after the fill >= 0 on BOTH sides
  R5 worst day > -2% of pilot capital  R6 return on money paid: CI low > 0
"""
import argparse, csv, json, os, random, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from make_strategy import (Book, Resting, decide_quotes, detect_fills, RoundAccount, VariantCfg,
                           QUOTE_SIZE, MAX_NET_SHARES, QUEUE_FILL_PROB, HALF_SPREAD, TICK,
                           code_hash, corpus_hash)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
CFG = VariantCfg()
BENCH = -0.055   # public passive-MM benchmark (marv): return on filled notional
BREAK_EVEN_PLAN = 0.485
CSV_COLS = ["mode", "label", "round_start", "day", "n_quotes", "n_fills", "buys", "sells",
            "net_shares", "notional", "avg_buy", "avg_sell", "fair_mean_at_fill",
            "settled", "outcome", "condition_id", "label_source", "pnl_usd", "adv_sel_edge",
            "model_cert_hit", "model_cert_n",
            # added 2026-10-15 for the R-gate and the V7 autopsy
            "fills_won", "shares", "cost", "adv5_buy_sum", "adv5_buy_n", "adv5_sell_sum", "adv5_sell_n",
            "touch_bid", "touch_ask", "improve_bid", "improve_ask", "variant"]


def paths():
    return (os.path.join(OUT, "sim_rounds.csv"), os.path.join(OUT, "sim_fills.jsonl"),
            os.path.join(OUT, "maker_stats.json"))


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(OUT, "maker_sim.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------- round state -------------------------------------------------------
def new_round(start):
    return dict(acct=RoundAccount(), prev_book=None, resting=Resting(), rng=random.Random(start),
                cert_hit=0, cert_n=0, fill_fairs=[], condition_id=None, last_fair=None,
                filled_sides=set(), pending5=[], touch_bid=0, touch_ask=0, improve_bid=0, improve_ask=0)


def tick_one(rs, T, label, start, now, book, fair):
    """one engine tick: detect fills on the quote that was resting, then decide the new quote."""
    acct = rs["acct"]
    t_rem = start + T - now
    if t_rem <= 0:
        return
    # R4 bookkeeping: fair 5 s after each fill
    if fair is not None:
        for p in rs["pending5"]:
            if p[3] is None and now >= p[0] + 5:
                p[3] = fair
    # V7 bookkeeping: how often our resting quote sat AT the touch (fills invisible to a
    # top-of-book snapshot) or IMPROVED the touch
    r = rs["resting"]
    if book is not None:
        if r.bid is not None and book.bb is not None:
            if r.bid == book.bb: rs["touch_bid"] += 1
            elif r.bid > book.bb: rs["improve_bid"] += 1
        if r.ask is not None and book.ba is not None:
            if r.ask == book.ba: rs["touch_ask"] += 1
            elif r.ask < book.ba: rs["improve_ask"] += 1
    for (side, price, size) in detect_fills(rs["prev_book"], book, rs["resting"], rs["rng"]):
        acct.add(now, side, price, size, fair)
        rs["fill_fairs"].append((fair, side))
        rs["filled_sides"].add(side)
        rs["pending5"].append([now, side, price, None])
        log(f"FILL {label} {start} t={now - start:.0f}s {side} {size:.0f}@{price:.2f} fair={fair if fair is None else round(fair, 3)}")
    quote = (decide_quotes(fair, t_rem, label, acct.net_shares, book, CFG, rs["filled_sides"])
             if book is not None else None)
    rs["resting"].update(quote)
    if quote is not None:
        acct.n_quotes += 1
    rs["prev_book"] = book
    if fair is not None:
        rs["last_fair"] = fair


# ---------- settlement --------------------------------------------------------
def gamma_settle(label, start):
    """-> (outcome 'up'/'down', condition_id) via gamma events once the round is closed."""
    try:
        m = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")[0]["markets"][0]
        pr = [float(x) for x in json.loads(m["outcomePrices"])]
        if m.get("closed") or not m.get("acceptingOrders") or pr[0] >= 0.9995 or pr[0] <= 0.0005:
            return ("up" if pr[0] > 0.5 else "down"), m.get("conditionId")
    except Exception:
        pass
    return None, None


def load_labels(path):
    """chain-verified corpus: start_time (ISO) -> (chain_outcome, condition_id)."""
    out = {}
    if not path:
        return out
    for r in csv.DictReader(open(path, newline="")):
        lab = (r.get("chain_outcome") or "").strip().lower()
        if lab not in ("up", "down"):
            continue
        st = r.get("start_time") or r.get("round_start") or ""
        try:
            start = int(st) if st.isdigit() else int(datetime.strptime(st, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        out[start] = (lab, r.get("condition_id"))
    return out


def existing_keys():
    csv_path = paths()[0]
    if not os.path.exists(csv_path):
        return set()
    return {(r["label"], int(r["round_start"])) for r in csv.DictReader(open(csv_path, newline=""))}


def close_round(w, label, T, start, rs, outcome, cid, source):
    acct = rs["acct"]
    up = outcome == "up"
    for (fair, side) in rs["fill_fairs"]:
        if fair is None:
            continue
        model_up = fair >= 0.5
        rs["cert_n"] += 1
        rs["cert_hit"] += 1 if ((model_up and side == "BUY") or ((not model_up) and side == "SELL")) else 0
    pnl = acct.settle(up); adv = acct.adverse_selection(up)
    buys = [f for f in acct.fills if f[1] == "BUY"]; sells = [f for f in acct.fills if f[1] == "SELL"]
    fairs = [f[4] for f in acct.fills if f[4] is not None]
    a5 = {"BUY": [0.0, 0], "SELL": [0.0, 0]}
    for (t, side, price, fair5) in rs["pending5"]:
        f5 = fair5 if fair5 is not None else rs["last_fair"]
        if f5 is None:
            continue
        e = (f5 - price) if side == "BUY" else (price - f5)
        a5[side][0] += e; a5[side][1] += 1
    row = dict(mode="replay", label=label, round_start=start,
               day=datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d"),
               n_quotes=acct.n_quotes, n_fills=len(acct.fills), buys=len(buys), sells=len(sells),
               net_shares=round(acct.net_shares, 1), notional=round(acct.notional, 2),
               avg_buy=round(sum(f[2] for f in buys) / len(buys), 4) if buys else "",
               avg_sell=round(sum(f[2] for f in sells) / len(sells), 4) if sells else "",
               fair_mean_at_fill=round(sum(fairs) / len(fairs), 4) if fairs else "",
               settled="Up" if up else "Down", outcome=outcome, condition_id=cid or "",
               label_source=source, pnl_usd=f"{pnl:.4f}", adv_sel_edge=f"{adv:.4f}" if adv is not None else "",
               model_cert_hit=rs["cert_hit"], model_cert_n=rs["cert_n"],
               fills_won=acct.fills_won(up), shares=round(acct.shares, 2), cost=round(acct.cost, 4),
               adv5_buy_sum=round(a5["BUY"][0], 4), adv5_buy_n=a5["BUY"][1],
               adv5_sell_sum=round(a5["SELL"][0], 4), adv5_sell_n=a5["SELL"][1],
               touch_bid=rs["touch_bid"], touch_ask=rs["touch_ask"],
               improve_bid=rs["improve_bid"], improve_ask=rs["improve_ask"], variant=CFG.id)
    w.writerow(row)
    with open(paths()[1], "a") as f:
        for (t, side, price, size, fair) in acct.fills:
            f.write(json.dumps({"label": label, "start": start, "t": t, "side": side, "price": price,
                                "size": size, "fair": fair, "outcome": outcome, "variant": CFG.id}) + "\n")
    return pnl, len(acct.fills)


# ---------- replay ------------------------------------------------------------
def replay_file(fp, labels, done, w):
    """replay one file (one day) and settle its rounds. -> (rounds, settled, dropped, skipped_recs)"""
    recs = []
    with open(fp) as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    recs.sort(key=lambda r: (r["label"], r["start"], r["ts"]))
    rounds = {}
    skipped = 0
    for r in recs:
        key = (r["label"], r["start"])
        if key in done:
            skipped += 1; continue
        T = 300 if r["label"] == "5m" else 900
        rs = rounds.setdefault(key, new_round(r["start"]))
        if r.get("condition_id"):
            rs["condition_id"] = r["condition_id"]
        book = None
        if r.get("bb") is not None or r.get("ba") is not None:
            book = Book(r.get("bids") and [(p, s) for p, s in r["bids"]] or ([(r["bb"], r.get("bb_sz") or 0)] if r.get("bb") is not None else []),
                        r.get("asks") and [(p, s) for p, s in r["asks"]] or ([(r["ba"], r.get("ba_sz") or 0)] if r.get("ba") is not None else []))
        tick_one(rs, T, r["label"], r["start"], r["ts"], book, r.get("fair_p"))
    n_set = n_drop = 0
    for (label, start), rs in sorted(rounds.items(), key=lambda kv: kv[0][1]):
        T = 300 if label == "5m" else 900
        if start in labels:
            outcome, cid = labels[start]; source = "chain-corpus"
        else:
            outcome, cid = gamma_settle(label, start); source = "gamma"
            time.sleep(0.05)
        if outcome is None:
            n_drop += 1; continue
        close_round(w, label, T, start, rs, outcome, cid or rs["condition_id"], source)
        done.add((label, start))
        n_set += 1
    return len(rounds), n_set, n_drop, skipped


def run_replay(files, labels_path):
    os.makedirs(OUT, exist_ok=True)
    labels = load_labels(labels_path)
    done = existing_keys()
    csv_path = paths()[0]
    log(f"replay: {len(files)} file(s); labels loaded: {len(labels)}; already scored: {len(done)}; "
        f"variant={CFG.id} cfg={json.dumps(CFG.as_dict())} size={QUOTE_SIZE} cap={MAX_NET_SHARES} tick={TICK}")
    meta = dict(variant=CFG.id, cfg=CFG.as_dict(), code_hash=code_hash(), corpus_hash=corpus_hash(files),
                files=[os.path.basename(f) for f in sorted(files)], quote_size=QUOTE_SIZE, max_net=MAX_NET_SHARES,
                fill_prob=QUEUE_FILL_PROB, labels=os.path.basename(labels_path) if labels_path else None,
                started=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    json.dump(meta, open(os.path.join(OUT, "run_meta.json"), "w"), indent=1)
    new_csv = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    tot = [0, 0, 0, 0]
    with open(csv_path, "a", newline="") as cf:
        w = csv.DictWriter(cf, fieldnames=CSV_COLS)
        if new_csv:
            w.writeheader()
        for fp in sorted(files):
            n_r, n_s, n_d, n_k = replay_file(fp, labels, done, w)
            cf.flush()
            tot = [a + b for a, b in zip(tot, (n_r, n_s, n_d, n_k))]
            log(f"  {os.path.basename(fp)}: rounds {n_r} settled {n_s} dropped {n_d} (already-scored records skipped {n_k})")
    log(f"replay done: rounds {tot[0]}, settled {tot[1]}, dropped (no label yet) {tot[2]}")
    report()


# ---------- gate reports -------------------------------------------------------
def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _boot(rows, fn, n=2000, seed=7):
    """bootstrap over rounds; fn(list_of_rows) -> statistic. -> (lo, hi) 95%."""
    if not rows:
        return (None, None)
    rng = random.Random(seed); idx = list(range(len(rows))); out = []
    for _ in range(n):
        s = [rows[rng.choice(idx)] for _ in idx]
        out.append(fn(s))
    out.sort()
    return (out[int(0.025 * n)], out[int(0.975 * n) - 1])


def r_metrics(rows, pilot_capital):
    """the plan's R1-R6 on a set of rows (one row per settled round)."""
    fills = sum(int(r["n_fills"]) for r in rows)
    shares = sum(_f(r.get("shares")) for r in rows)
    cost = sum(_f(r.get("cost")) for r in rows)
    notional = sum(_f(r["notional"]) for r in rows)
    pnl = sum(_f(r["pnl_usd"]) for r in rows)
    won = sum(int(r.get("fills_won") or 0) for r in rows)
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += _f(r["pnl_usd"])

    def edge(s):
        sh = sum(_f(x.get("shares")) for x in s); return (sum(_f(x["pnl_usd"]) for x in s) / sh) if sh else 0.0

    def wr(s):
        f = sum(int(x["n_fills"]) for x in s); return (sum(int(x.get("fills_won") or 0) for x in s) / f) if f else 0.0

    def roc(s):
        c = sum(_f(x.get("cost")) for x in s); return (sum(_f(x["pnl_usd"]) for x in s) / c) if c else 0.0

    def ron(s):
        n_ = sum(_f(x["notional"]) for x in s); return (sum(_f(x["pnl_usd"]) for x in s) / n_) if n_ else 0.0

    e_ci = _boot(rows, edge); w_ci = _boot(rows, wr); c_ci = _boot(rows, roc); n_ci = _boot(rows, ron)
    a5b = sum(_f(r.get("adv5_buy_sum")) for r in rows); a5bn = sum(int(r.get("adv5_buy_n") or 0) for r in rows)
    a5s = sum(_f(r.get("adv5_sell_sum")) for r in rows); a5sn = sum(int(r.get("adv5_sell_n") or 0) for r in rows)
    adv5_buy = a5b / a5bn if a5bn else None; adv5_sell = a5s / a5sn if a5sn else None
    worst_day = min(by_day.values()) if by_day else 0.0
    be_meas = (cost / shares) if shares else None
    m = dict(rounds=len(rows), rounds_with_fills=sum(1 for r in rows if int(r["n_fills"])), fills=fills,
             shares=round(shares, 1), cost_usd=round(cost, 2), notional_usd=round(notional, 2), pnl_usd=round(pnl, 2),
             edge_per_share=round(edge(rows), 5), edge_ci95=[None if e_ci[0] is None else round(e_ci[0], 5), None if e_ci[1] is None else round(e_ci[1], 5)],
             fill_win_rate=round(wr(rows), 4), fill_win_ci95=[None if w_ci[0] is None else round(w_ci[0], 4), None if w_ci[1] is None else round(w_ci[1], 4)],
             break_even_plan=BREAK_EVEN_PLAN, break_even_measured=None if be_meas is None else round(be_meas, 4),
             adv5_buy=None if adv5_buy is None else round(adv5_buy, 5), adv5_sell=None if adv5_sell is None else round(adv5_sell, 5),
             return_on_cost=round(roc(rows), 4), return_on_cost_ci95=[None if c_ci[0] is None else round(c_ci[0], 4), None if c_ci[1] is None else round(c_ci[1], 4)],
             return_on_notional=round(ron(rows), 4), return_on_notional_ci95=[None if n_ci[0] is None else round(n_ci[0], 4), None if n_ci[1] is None else round(n_ci[1], 4)],
             worst_day_usd=round(worst_day, 2), worst_day_pct_of_pilot=round(100.0 * worst_day / pilot_capital, 2) if pilot_capital else None,
             days={k: round(v, 2) for k, v in sorted(by_day.items())},
             touch_ticks=sum(int(r.get("touch_bid") or 0) + int(r.get("touch_ask") or 0) for r in rows),
             improve_ticks=sum(int(r.get("improve_bid") or 0) + int(r.get("improve_ask") or 0) for r in rows))
    crit = dict(R1_rounds_ge_200=len(rows) >= 200,
                R2_edge_ci_low_gt_0=(e_ci[0] is not None and e_ci[0] > 0),
                R3_winrate_point_ge_50_and_ci_low_gt_breakeven=(m["fill_win_rate"] >= 0.5 and w_ci[0] is not None and w_ci[0] > BREAK_EVEN_PLAN),
                R4_adv5_ge_0_both_sides=(adv5_buy is not None and adv5_sell is not None and adv5_buy >= 0 and adv5_sell >= 0),
                R5_worst_day_gt_minus_2pct=(worst_day > -0.02 * pilot_capital),
                R6_return_on_cost_ci_low_gt_0=(c_ci[0] is not None and c_ci[0] > 0))
    m["criteria"] = crit; m["pass"] = all(crit.values())
    return m


def report(test_days=None, fit_days=None, pilot_capital=1000.0):
    csv_path, _, stats_path = paths()
    if not os.path.exists(csv_path):
        print("no sim_rounds.csv yet"); return None
    rows = list(csv.DictReader(open(csv_path, newline="")))
    # --- the old Phase-A gate on every row (kept so V0 can be compared with the 2026-10-10 result)
    pnl = [_f(r["pnl_usd"]) for r in rows]; notional = [_f(r["notional"]) for r in rows]
    fills = [int(r["n_fills"]) for r in rows]
    adv = [_f(r["adv_sel_edge"]) for r in rows if r["adv_sel_edge"] != ""]
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += _f(r["pnl_usd"])
    tot_pnl, tot_not = sum(pnl), sum(notional)
    ci = _boot(rows, lambda s: (sum(_f(x["pnl_usd"]) for x in s) / sum(_f(x["notional"]) for x in s)) if sum(_f(x["notional"]) for x in s) else 0.0)
    cert_hit = sum(int(r["model_cert_hit"]) for r in rows); cert_n = sum(int(r["model_cert_n"]) for r in rows)
    worst_day = min(by_day.values()) if by_day else 0.0
    gate = dict(rounds_ge_200=len(rows) >= 200, pnl_positive=tot_pnl > 0,
                beats_benchmark_ci=(ci[0] is not None and ci[0] > BENCH),
                adverse_edge_ge_0=(sum(adv) / len(adv) >= 0) if adv else False,
                no_day_blowup_20=worst_day > -20.0)
    stats = dict(variant=rows[0].get("variant") if rows else None, rounds=len(rows), rounds_with_fills=sum(1 for f in fills if f), fills=sum(fills),
                 pnl_usd=round(tot_pnl, 2), filled_notional_usd=round(tot_not, 2),
                 return_on_notional=round(tot_pnl / tot_not, 4) if tot_not else 0.0,
                 return_ci95=[round(ci[0], 4), round(ci[1], 4)] if ci[0] is not None else None,
                 benchmark=BENCH, mean_adverse_edge=round(sum(adv) / len(adv), 4) if adv else None,
                 model_cert=f"{cert_hit}/{cert_n}", worst_day_usd=round(worst_day, 2),
                 days={k: round(v, 2) for k, v in sorted(by_day.items())},
                 quote_size=QUOTE_SIZE, max_net=MAX_NET_SHARES, fill_prob=QUEUE_FILL_PROB,
                 gate=gate, gate_pass=all(gate.values()),
                 generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    json.dump(stats, open(stats_path, "w"), indent=1)
    # --- the plan's R-gate on the test days (and fit days for information only)
    meta = {}
    try:
        meta = json.load(open(os.path.join(OUT, "run_meta.json")))
    except Exception:
        pass
    all_days = sorted({r["day"] for r in rows})
    test_days = sorted(set(test_days)) if test_days else all_days
    fit_days = sorted(set(fit_days)) if fit_days else []
    test_rows = [r for r in rows if r["day"] in test_days]
    fit_rows = [r for r in rows if r["day"] in fit_days]
    res = dict(variant=meta.get("variant", stats["variant"]), cfg=meta.get("cfg"), code_hash=meta.get("code_hash"),
               code_hash_now=code_hash(), corpus_hash=meta.get("corpus_hash"), files=meta.get("files"),
               pilot_capital=pilot_capital, test_days=test_days, fit_days=fit_days,
               test=r_metrics(test_rows, pilot_capital), fit=r_metrics(fit_rows, pilot_capital) if fit_rows else None,
               phase_a_gate_all_rows=dict(gate=gate, gate_pass=all(gate.values()), return_on_notional=stats["return_on_notional"],
                                          mean_adverse_edge=stats["mean_adverse_edge"], rounds=len(rows)),
               generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), generated_ts=time.time())
    res["gate_pass"] = bool(res["test"]["pass"])
    json.dump(res, open(os.path.join(OUT, "gate_result.json"), "w"), indent=1)
    gp = os.path.join(OUT, "gate_pass.json")
    if res["gate_pass"]:
        json.dump(res, open(gp, "w"), indent=1)
    elif os.path.exists(gp):
        os.remove(gp)
    print(json.dumps({k: res[k] for k in ("variant", "code_hash", "corpus_hash", "test_days", "fit_days", "gate_pass")}, indent=1))
    print("TEST:", json.dumps({k: v for k, v in res["test"].items() if k != "days"}, indent=1))
    if res["fit"]:
        print("FIT :", json.dumps({k: res["fit"][k] for k in ("rounds", "fills", "edge_per_share", "fill_win_rate", "return_on_cost", "worst_day_usd")}))
    print("PHASE-A gate (all rows):", json.dumps(res["phase_a_gate_all_rows"]))
    return res


def main():
    global OUT, CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", nargs="+", metavar="JSONL")
    ap.add_argument("--labels", default=None, help="chain-verified corpus csv (start_time,condition_id,chain_outcome)")
    ap.add_argument("--cfg", default=None, help="VariantCfg JSON; omit for the baseline (V0)")
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--test-days", nargs="*", default=None)
    ap.add_argument("--fit-days", nargs="*", default=None)
    ap.add_argument("--pilot-capital", type=float, default=1000.0)
    a = ap.parse_args()
    OUT = os.path.abspath(a.out)
    if a.cfg:
        CFG = VariantCfg.from_dict(json.loads(a.cfg))
    if a.report:
        report(a.test_days, a.fit_days, a.pilot_capital); return
    if a.replay:
        run_replay(a.replay, a.labels); return
    ap.print_help()


if __name__ == "__main__":
    main()
