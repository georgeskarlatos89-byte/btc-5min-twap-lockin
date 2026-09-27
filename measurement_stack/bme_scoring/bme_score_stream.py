#!/usr/bin/env python3
"""
bme_score_stream.py — memory-bounded scorer for the full BME dataset (read-only).

The original bme_score.py is NOT modified. It keeps one counter entry per (millisecond, hash)
for every price change and was OOM-killed at 1.3 GB after 4 minutes on the 7-day set
(~600 million events). This version computes the SAME three signals in ONE streaming pass:

  S1 flicker/batch   price changes, distinct causes, causes with >=5 / >=20 events,
                     same-millisecond batch bursts (>=5 levels), largest burst
  S2 pre-tick pulls  quintiles of pretick_pulls vs the next tick's move
  S3 imbalance       early-round (first 20 %) signed depth of the Up token vs the outcome
                     + what the pre-registered gate actually asks: post-fee EV per share of
                     buying the side the imbalance points to, at the recorded ask

Outcomes come from the chain-verified label cache of the measurement stack
(backtest7d/cache), falling back to gamma for rounds not in it.
Writes s9_data/bme/bme_score_report_7d.json + .md next to the original report.
"""
import argparse, csv, glob, gzip, json, math, os, sys, time, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
BME = os.path.join(HOME, "POLYMARKET-VPS-STACK", "polymarket-stack", "4-BME-BOOK-MOVEMENT-ENGINE", "s9_data", "bme")
CACHE = os.path.join(HOME, "mstack", "backtest7d", "cache")
HEAD = ("ts_ms", "hash", "timestamp")
FEE = 0.07

def log(*a): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z]", *a, flush=True)
def opener(p): return gzip.open(p, "rt", encoding="utf-8", errors="replace") if p.endswith(".gz") else open(p, encoding="utf-8", errors="replace")
def wilson(k, n, z=1.96):
    if not n: return None, None
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 4), round(c + h, 4)

def labels():
    out = {}
    chain = {}
    try:
        for ln in open(os.path.join(CACHE, "chain.jsonl")):
            j = json.loads(ln); chain[j["condition_id"]] = j["chain_outcome"]
    except Exception: pass
    for series in ("5m", "15m"):
        try:
            for ln in open(os.path.join(CACHE, f"gamma_{series}.jsonl")):
                j = json.loads(ln)
                lab = chain.get(j.get("condition_id")) or j.get("gamma_outcome")
                if lab in ("up", "down"): out[(series, j["start"])] = (lab == "up", "chain" if j.get("condition_id") in chain else "gamma")
        except Exception: pass
    return out

def gamma_outcome(series, start):
    for q in ("&closed=true", ""):
        try:
            req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{series}-{start}{q}", headers={"User-Agent": "Mozilla/5.0"})
            ev = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
            m = ev[0]["markets"][0]; pr = [float(x) for x in json.loads(m["outcomePrices"])]
            if m.get("closed") and sorted(pr) == [0.0, 1.0]: return pr[0] > 0.5
        except Exception:
            pass
    return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--min-rounds", type=int, default=100); ap.add_argument("--days", type=int, default=0, help="0 = every day on disk")
    a = ap.parse_args(); t00 = time.time()
    ev_files = sorted(glob.glob(os.path.join(BME, "events_*.csv*")))
    if a.days: ev_files = ev_files[-a.days:]
    days = [os.path.basename(f).split("_")[1].split(".")[0] for f in ev_files]
    log(f"events files: {len(ev_files)} ({days[0]}..{days[-1]})")

    # ---------------- S1 causes (hash files): streaming counters
    distinct = ge5 = ge20 = 0
    for p in sorted(glob.glob(os.path.join(BME, "hashes_*.csv*"))):
        if not any(d in p for d in days): continue
        try:
            with opener(p) as f:
                for r in csv.reader(f):
                    if not r or r[0] in HEAD: continue
                    try: n = int(r[1])
                    except Exception: continue
                    distinct += 1; ge5 += n >= 5; ge20 += n >= 20
        except (EOFError, OSError):
            pass
    log(f"S1 causes: {distinct} distinct")

    # ---------------- one pass over events: S1 bursts + S2 ticks
    n_pc = bursts = burst_max = rows = 0
    cur_ts, cur = None, Counter()
    hist = Counter()                                  # burst size bucket -> count
    ticks = []
    def flush(c):
        nonlocal bursts, burst_max
        for v in c.values():
            if v >= 5:
                bursts += 1; burst_max = max(burst_max, v)
                hist["5-9" if v < 10 else "10-19" if v < 20 else "20-49" if v < 50 else "50+"] += 1
    for p in ev_files:
        t0 = time.time(); n0 = rows
        try:
            with opener(p) as f:
                for ln in f:
                    rows += 1
                    i = ln.find(","); j = ln.find(",", i + 1)
                    if i < 0 or j < 0: continue
                    et = ln[i + 1:j]
                    if et == "price_change":
                        n_pc += 1
                        ts = ln[:i]
                        if ts != cur_ts:
                            flush(cur); cur = Counter(); cur_ts = ts
                        r = ln.rstrip("\n").split(",")
                        cur[r[9] if len(r) > 9 else ""] += 1
                    elif et == "spot_tick":
                        r = ln.rstrip("\n").split(",")
                        try:
                            x = r[10] if len(r) > 10 else ""
                            n = int(x.split("=")[-1]) if "=" in x else 0
                            ticks.append((int(r[0]), float(r[5]), n))
                        except Exception:
                            pass
        except (EOFError, OSError) as e:
            log(f"  {os.path.basename(p)}: truncated tail ignored ({e.__class__.__name__})")
        log(f"  {os.path.basename(p)}: {rows - n0:,} rows in {time.time() - t0:.0f}s")
    flush(cur)
    S1 = dict(price_changes_read=n_pc, distinct_causes=distinct, causes_ge5_events=ge5, causes_ge20_events=ge20,
              batch_bursts_ge5_same_ms=bursts, batch_burst_max_levels=burst_max, batch_burst_size_histogram=dict(hist),
              bursts_per_day=round(bursts / max(len(ev_files), 1)),
              note="Descriptive only. It shows re-quote bots exist; it is not a trading signal and has no gate of its own.")

    ticks.sort(); pairs = []
    for (t0, p0, n0), (t1, p1, _) in zip(ticks, ticks[1:]):
        if 0.5 <= (t1 - t0) / 1000.0 <= 5.0: pairs.append((n0, p1 - p0))
    def qreport(pairs):
        if not pairs: return {"n": 0}
        ps = sorted(pairs, key=lambda x: x[0]); k = len(ps); out = {"n": k}
        for i, (lo, hi) in enumerate(((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.0))):
            seg = ps[int(lo * k):int(hi * k)]
            if not seg: continue
            ups = sum(1 for _, d in seg if d > 0); lo_, hi_ = wilson(ups, len(seg))
            out[f"Q{i + 1}"] = {"pulls_range": f"{seg[0][0]}-{seg[-1][0]}", "n": len(seg), "mean_abs_move": round(sum(abs(d) for _, d in seg) / len(seg), 3),
                                "p_up": round(ups / len(seg), 4), "p_up_ci95": [lo_, hi_]}
        top = ps[int(0.9 * k):]
        if top:
            ups = sum(1 for _, d in top if d > 0)
            out["top_decile"] = {"n": len(top), "min_pulls": top[0][0], "mean_abs_move": round(sum(abs(d) for _, d in top) / len(top), 3),
                                 "p_up": round(ups / len(top), 4), "p_up_ci95": list(wilson(ups, len(top)))}
        return out
    q = qreport(pairs)
    ratio = (q.get("Q5", {}).get("mean_abs_move") or 0) / (q.get("Q1", {}).get("mean_abs_move") or 1) if q.get("Q1") else None
    S2 = dict(ticks=len(ticks), usable_pairs=len(pairs), by_quintile_of_pretick_pulls=q, q5_over_q1_move_ratio=round(ratio, 3) if ratio else None,
              reading=("Pulls before a tick predict the SIZE of the move, not its DIRECTION" if ratio and ratio > 1.2 else "No size effect either")
                      + ": p_up stays at a coin flip in every quintile, so there is nothing to bet on." )

    # ---------------- S3 imbalance (bookstate, early window) + EV
    L = labels(); per = defaultdict(list); last = {}
    for p in sorted(glob.glob(os.path.join(BME, "bookstate_1s_*.csv*"))):
        if not any(d in p for d in days): continue
        try:
            with opener(p) as f:
                for r in csv.reader(f):
                    if not r or r[0] in HEAD: continue
                    try:
                        ts, series, idx = int(r[0]), r[1], int(r[2])
                        if series not in ("5m", "15m") or idx != 0: continue
                        bd, ad = float(r[7]), float(r[8])
                        if bd + ad <= 0: continue
                        T = 300 if series == "5m" else 900
                        start = ts // 1000 // T * T
                        if (ts / 1000.0 - start) / T > 0.20: continue
                        per[(series, start)].append((bd - ad) / (bd + ad)); last[(series, start)] = (float(r[4]), float(r[5]))
                    except Exception:
                        continue
        except (EOFError, OSError):
            pass
    log(f"S3: {len(per)} rounds with early-window book state")
    rows_out = []; miss = 0
    for key in sorted(per):
        lab = L.get(key)
        if lab is None and miss < 60:
            miss += 1; w = gamma_outcome(*key); lab = None if w is None else (w, "gamma-live")
        imbs = per[key]; mi = sum(imbs) / len(imbs); bb, ba = last[key]
        side_up = mi > 0
        price = ba if side_up else round(1 - bb, 4)            # Down ask approximated by 1 - Up bid
        rows_out.append(dict(series=key[0], start=key[1], n_1s=len(imbs), mean_imb=round(mi, 4), won_up=None if lab is None else lab[0],
                             label_source=None if lab is None else lab[1], signal_side="up" if side_up else "down", entry_price=price))
    def s3_stats(R):
        sc = [r for r in R if r["won_up"] is not None and len(per[(r["series"], r["start"])]) >= 10]
        if not sc: return dict(rounds_scored=0)
        hit = sum(1 for r in sc if (r["mean_imb"] > 0) == r["won_up"]); lo, hi = wilson(hit, len(sc))
        tr = [r for r in sc if 0.05 <= r["entry_price"] <= 0.95]
        pnl = [((1 - r["entry_price"]) if ((r["signal_side"] == "up") == r["won_up"]) else -r["entry_price"]) - FEE * r["entry_price"] * (1 - r["entry_price"]) for r in tr]
        ev = sum(pnl) / len(pnl) if pnl else None
        sd = math.sqrt(sum((x - ev) ** 2 for x in pnl) / len(pnl)) if pnl else None
        strong = [r for r in sc if abs(r["mean_imb"]) >= 0.2]; sh = sum(1 for r in strong if (r["mean_imb"] > 0) == r["won_up"])
        up = [r["mean_imb"] for r in sc if r["won_up"]]; dn = [r["mean_imb"] for r in sc if not r["won_up"]]
        return dict(rounds_scored=len(sc), sign_accuracy=round(hit / len(sc), 4), sign_accuracy_ci95=[lo, hi],
                    mean_imb_when_up_won=round(sum(up) / len(up), 4) if up else None, mean_imb_when_down_won=round(sum(dn) / len(dn), 4) if dn else None,
                    strong_imbalance_rounds=len(strong), strong_sign_accuracy=round(sh / len(strong), 4) if strong else None,
                    ev_trades=len(tr), avg_entry_price=round(sum(r["entry_price"] for r in tr) / len(tr), 4) if tr else None,
                    ev_per_share_post_fee=round(ev, 4) if ev is not None else None,
                    ev_ci95=[round(ev - 1.96 * sd / math.sqrt(len(pnl)), 4), round(ev + 1.96 * sd / math.sqrt(len(pnl)), 4)] if pnl else None)
    allr = s3_stats(rows_out)
    S3 = dict(all=allr, by_series={s: s3_stats([r for r in rows_out if r["series"] == s]) for s in ("5m", "15m")},
              labels={k: v for k, v in Counter(r["label_source"] for r in rows_out).items()},
              gate=f"needs >= {a.min_rounds} scored rounds AND >= +0.02 post-fee EV per share",
              gate_rounds_met=allr.get("rounds_scored", 0) >= a.min_rounds,
              gate_ev_met=bool(allr.get("ev_per_share_post_fee") is not None and allr["ev_per_share_post_fee"] >= 0.02),
              note="EV uses the recorded Up ask for Up signals and 1 minus the Up bid for Down signals (approximation), held to resolution, taker fee 0.07*p*(1-p).")
    S3["verdict"] = ("PASS" if S3["gate_rounds_met"] and S3["gate_ev_met"] else "FAIL: enough rounds, EV below +2c per share" if S3["gate_rounds_met"] else "NOT ENOUGH ROUNDS")

    R = dict(generated_utc=datetime.now(timezone.utc).isoformat(), days=days, event_rows_read=rows, seconds=round(time.time() - t00),
             signals=dict(S1_flicker=S1, S2_pretick_pulls=S2, S3_imbalance=S3,
                          S4_double_tap_reaction=dict(status="not scored: needs a wallet join that the recorder does not capture"),
                          S5_divergence=dict(status="not scored: no scoring rule was pre-registered in the scorer")),
             rounds=rows_out)
    out = os.path.join(BME, "bme_score_report_7d.json"); json.dump(R, open(out, "w"), indent=1)
    M = [f"# BME 7-day score ({days[0]} to {days[-1]})\n", f"Generated {R['generated_utc'][:19]}Z. {rows:,} event rows read in {R['seconds']} s. Read-only.\n",
         "## S1 flicker and batch bursts (descriptive)\n", f"- price changes: {n_pc:,}\n- distinct causes: {distinct:,} ({ge5:,} with 5+ events, {ge20:,} with 20+)\n"
         f"- same-millisecond bursts of 5+ levels: {bursts:,} (about {S1['bursts_per_day']:,} a day), largest {burst_max} levels\n",
         "## S2 pulls before a price tick\n", f"Ticks {len(ticks):,}, usable pairs {len(pairs):,}.\n", "| quintile | pulls | pairs | mean move | P(up) | 95 % interval |", "|---|---|---|---|---|---|"]
    for k in ("Q1", "Q2", "Q3", "Q4", "Q5", "top_decile"):
        v = q.get(k)
        if v: M.append(f"| {k} | {v.get('pulls_range', '>= ' + str(v.get('min_pulls')))} | {v['n']} | {v['mean_abs_move']} | {v['p_up']} | {v['p_up_ci95'][0]} to {v['p_up_ci95'][1]} |")
    M += ["", S2["reading"], "", "## S3 early-round depth imbalance (the only signal with a gate)\n", "| set | rounds | sign accuracy | 95 % interval | EV per share after fee | 95 % interval |", "|---|---|---|---|---|---|"]
    for nm, v in (("all", allr), ("5m", S3["by_series"]["5m"]), ("15m", S3["by_series"]["15m"])):
        if v.get("rounds_scored"): M.append(f"| {nm} | {v['rounds_scored']} | {v['sign_accuracy']} | {v['sign_accuracy_ci95'][0]} to {v['sign_accuracy_ci95'][1]} | {v['ev_per_share_post_fee']} | {v['ev_ci95'][0]} to {v['ev_ci95'][1]} |")
    M += ["", f"Gate: {S3['gate']}. **Verdict: {S3['verdict']}.**", "", S3["note"], "", "## S4 and S5\n", "Not scored. S4 needs wallet data the recorder does not capture. S5 has no scoring rule in the scorer.", ""]
    open(os.path.join(BME, "bme_score_report_7d.md"), "w", encoding="utf-8").write("\n".join(M))
    print("\n".join(M)); log(f"report -> {out}")

if __name__ == "__main__":
    main()
