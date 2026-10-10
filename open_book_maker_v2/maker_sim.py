#!/usr/bin/env python3
"""
maker_sim.py (v2) - maker quoting simulator (paper mode; never places orders)

  --replay F [F2 ...]      replay recorder JSONL files (book_recorder.py or bme_to_replay.py)
                           through the SAME strategy/fill code as the bot.
  --labels CSV             chain-verified labels (start_time,condition_id,chain_outcome) used
                           to settle replayed rounds without touching gamma. Rounds not in the
                           file fall back to gamma events (closed + outcomePrices).
  --report                 print the Phase-A gate evaluation from sim_rounds.csv and exit.

Outputs: sim_rounds.csv (one row per settled round, with condition_id + outcome so
chain_verify.py can audit it), sim_fills.jsonl, maker_stats.json, maker_sim.log.

v2 fixes: post-only quoting, fill model that cannot fill on an unchanged book, quotes are
consumed when filled, venue tick prices, sizes from .env (same as the bot), rounds already
in sim_rounds.csv are skipped on re-replay, stats recomputed from the CSV, gate report with
a bootstrap confidence interval against the -5.5% public passive-MM benchmark.
"""
import argparse, csv, json, os, random, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from make_strategy import (Book, Resting, decide_quotes, detect_fills, RoundAccount,
                           QUOTE_SIZE, MAX_NET_SHARES, QUEUE_FILL_PROB, HALF_SPREAD, TICK)

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "sim_rounds.csv")
FILLS_PATH = os.path.join(HERE, "sim_fills.jsonl")
STATS_PATH = os.path.join(HERE, "maker_stats.json")
CSV_COLS = ["mode", "label", "round_start", "day", "n_quotes", "n_fills", "buys", "sells",
            "net_shares", "notional", "avg_buy", "avg_sell", "fair_mean_at_fill",
            "settled", "outcome", "condition_id", "label_source", "pnl_usd", "adv_sel_edge",
            "model_cert_hit", "model_cert_n"]
BENCH = -0.055   # public passive-MM benchmark (marv): return on filled notional


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(HERE, "maker_sim.log"), "a") as f:
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
                cert_hit=0, cert_n=0, fill_fairs=[], condition_id=None, last_fair=None)


def tick_one(rs, T, label, start, now, book, fair):
    """one engine tick: detect fills on the quote that was resting, then decide the new quote."""
    acct = rs["acct"]
    t_rem = start + T - now
    if t_rem <= 0:
        return
    for (side, price, size) in detect_fills(rs["prev_book"], book, rs["resting"], rs["rng"]):
        acct.add(now, side, price, size, fair)
        rs["fill_fairs"].append((fair, side))
        log(f"FILL {label} {start} t={now - start:.0f}s {side} {size:.0f}@{price:.2f} fair={fair if fair is None else round(fair, 3)}")
    quote = decide_quotes(fair, t_rem, label, acct.net_shares, book) if book is not None else None
    rs["resting"].update(quote)
    if quote is not None:
        acct.n_quotes += 1
    rs["prev_book"] = book
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
    if not os.path.exists(CSV_PATH):
        return set()
    return {(r["label"], int(r["round_start"])) for r in csv.DictReader(open(CSV_PATH, newline=""))}


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
    row = dict(mode="replay", label=label, round_start=start,
               day=datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d"),
               n_quotes=acct.n_quotes, n_fills=len(acct.fills), buys=len(buys), sells=len(sells),
               net_shares=round(acct.net_shares, 1), notional=round(acct.notional, 2),
               avg_buy=round(sum(f[2] for f in buys) / len(buys), 4) if buys else "",
               avg_sell=round(sum(f[2] for f in sells) / len(sells), 4) if sells else "",
               fair_mean_at_fill=round(sum(fairs) / len(fairs), 4) if fairs else "",
               settled="Up" if up else "Down", outcome=outcome, condition_id=cid or "",
               label_source=source, pnl_usd=f"{pnl:.4f}", adv_sel_edge=f"{adv:.4f}" if adv is not None else "",
               model_cert_hit=rs["cert_hit"], model_cert_n=rs["cert_n"])
    w.writerow(row)
    with open(FILLS_PATH, "a") as f:
        for (t, side, price, size, fair) in acct.fills:
            f.write(json.dumps({"label": label, "start": start, "t": t, "side": side, "price": price,
                                "size": size, "fair": fair, "outcome": outcome}) + "\n")
    return pnl, len(acct.fills)


# ---------- replay ------------------------------------------------------------
def run_replay(files, labels_path):
    labels = load_labels(labels_path)
    done = existing_keys()
    recs = []
    for fp in files:
        with open(fp) as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    recs.sort(key=lambda r: (r["label"], r["start"], r["ts"]))
    log(f"replay: {len(recs)} records from {len(files)} file(s); labels loaded: {len(labels)}; "
        f"already scored: {len(done)}; size={QUOTE_SIZE} cap={MAX_NET_SHARES} half_spread={HALF_SPREAD} tick={TICK}")
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
            book = Book(r.get("bids") and [(p, s) for p, s in r["bids"]] or ([(r["bb"], 0)] if r.get("bb") is not None else []),
                        r.get("asks") and [(p, s) for p, s in r["asks"]] or ([(r["ba"], 0)] if r.get("ba") is not None else []))
        tick_one(rs, T, r["label"], r["start"], r["ts"], book, r.get("fair_p"))
    log(f"replay: {len(rounds)} new round(s), {skipped} records of already-scored rounds skipped")
    new_csv = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="") as cf:
        w = csv.DictWriter(cf, fieldnames=CSV_COLS)
        if new_csv:
            w.writeheader()
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
            n_set += 1
    log(f"replay done: settled {n_set}, dropped (no label yet) {n_drop}")
    report()


# ---------- gate report -------------------------------------------------------
def report():
    if not os.path.exists(CSV_PATH):
        print("no sim_rounds.csv yet"); return
    rows = list(csv.DictReader(open(CSV_PATH, newline="")))
    pnl = [float(r["pnl_usd"]) for r in rows]
    notional = [float(r["notional"] or 0) for r in rows]
    fills = [int(r["n_fills"]) for r in rows]
    adv = [float(r["adv_sel_edge"]) for r in rows if r["adv_sel_edge"] != ""]
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += float(r["pnl_usd"])
    tot_pnl, tot_not = sum(pnl), sum(notional)
    ret = tot_pnl / tot_not if tot_not else 0.0
    # bootstrap over rounds: return on notional
    rng = random.Random(7); boots = []
    if rows:
        idx = list(range(len(rows)))
        for _ in range(2000):
            s = [rng.choice(idx) for _ in idx]
            n_ = sum(notional[i] for i in s)
            boots.append(sum(pnl[i] for i in s) / n_ if n_ else 0.0)
        boots.sort()
    ci = (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]) if boots else (None, None)
    cert_hit = sum(int(r["model_cert_hit"]) for r in rows); cert_n = sum(int(r["model_cert_n"]) for r in rows)
    worst_day = min(by_day.values()) if by_day else 0.0
    gate = dict(rounds_ge_200=len(rows) >= 200,
                pnl_positive=tot_pnl > 0,
                beats_benchmark_ci=(ci[0] is not None and ci[0] > BENCH),
                adverse_edge_ge_0=(sum(adv) / len(adv) >= 0) if adv else False,
                no_day_blowup_20=worst_day > -20.0)
    stats = dict(rounds=len(rows), rounds_with_fills=sum(1 for f in fills if f), fills=sum(fills),
                 pnl_usd=round(tot_pnl, 2), filled_notional_usd=round(tot_not, 2),
                 return_on_notional=round(ret, 4), return_ci95=[round(ci[0], 4), round(ci[1], 4)] if ci[0] is not None else None,
                 benchmark=BENCH, mean_adverse_edge=round(sum(adv) / len(adv), 4) if adv else None,
                 model_cert=f"{cert_hit}/{cert_n}", worst_day_usd=round(worst_day, 2),
                 days={k: round(v, 2) for k, v in sorted(by_day.items())},
                 quote_size=QUOTE_SIZE, max_net=MAX_NET_SHARES, fill_prob=QUEUE_FILL_PROB,
                 gate=gate, gate_pass=all(gate.values()),
                 generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    json.dump(stats, open(STATS_PATH, "w"), indent=1)
    print(json.dumps(stats, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", nargs="+", metavar="JSONL")
    ap.add_argument("--labels", default=None, help="chain-verified corpus csv (start_time,condition_id,chain_outcome)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report(); return
    if a.replay:
        run_replay(a.replay, a.labels); return
    ap.print_help()


if __name__ == "__main__":
    main()
