#!/usr/bin/env python3
"""inspect_report.py - aggregate inspect_lab day files into one plain-language results file.

  python3 inspect_report.py --days inspect_out/day_*.json --out inspect_out/INSPECT-RESULTS.md

Tables: 2a fill probability by offset and post time; 2b timed both-sides posts (pair view, bootstrap CI, gate);
2c odd/even and round-number prices + crowding; 2d flow-filtered continuous tactic (gate); 2e exits; 2f rebates
are a column everywhere. Gate = profit 95% CI low > 0 after rebate AND pair completion >= 95% (same as the plan).
"""
import argparse, collections, glob, json, random, statistics


def boot_ci(vals, n=2000, seed=7):
    if not vals: return (0.0, 0.0)
    rnd = random.Random(seed); m = len(vals); out = []
    for _ in range(n): out.append(sum(vals[rnd.randrange(m)] for _ in range(m)))
    out.sort(); return out[int(0.025 * n)], out[int(0.975 * n)]


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def f(x, d=2):
    return "-" if x is None else f"{x:,.{d}f}"


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", nargs="+", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    files = sorted(set(sum([glob.glob(p) for p in a.days], [])))
    timed, crowd, exits = [], [], []; flow = collections.defaultdict(list); flow_days = collections.defaultdict(dict)
    for fp in files:
        d = json.load(open(fp)); day = fp.split("day_")[-1][:8]
        timed += d["timed"]; crowd += d["crowd"]; exits += d["exits"]
        for v, rows in d["flow"].items():
            flow[v] += rows; flow_days[v][day] = sum(r["profit"] + r["rebate"] for r in rows)
    lat = d["lat"]
    rounds = len({x["start"] for x in timed})
    L = [f"# v2-open-maker-inspect-order-book - RESULTS - {len(files)} recorded days ({files[0].split('day_')[-1][:8]} .. {files[-1].split('day_')[-1][:8]}), {rounds:,} rounds", "",
         f"Order latency {lat} s (measured). Size 20 shares per order. Taker fee 0.07*p*(1-p) per share; maker rebate = 20% of that, credited "
         "per maker-filled share (0.35% of notional at 50c). Queue rules as in the paired-maker replay. Edge/share = profit at settlement per filled share.", ""]

    # ---- 2a fill probability ----
    L += ["## 2a Will a taker trade at my price? One 20-share bid, held to the end of the round", "",
          "| cents below best bid | posted at (elapsed s) | orders | filled (any) | avg shares filled | median s to first fill | median queue ahead (sh) | edge/share | edge/share + rebate | profit per order |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    by = collections.defaultdict(list)
    for x in timed: by[(x["o"], x["e0"])].append(x)
    for (o, e0) in sorted(by):
        xs = by[(o, e0)]; n = len(xs); fl = [x for x in xs if x["filled"] > 0]; sh = sum(x["filled"] for x in xs)
        pr = sum(x["profit"] for x in xs); rb = sum(x["rebate"] for x in xs)
        L.append(f"| {o} | {e0} ({300 - e0} s left) | {n:,} | {pct(len(fl) / n if n else None)} | {f(sh / n if n else None)} | {f(med([x['tfill'] for x in fl]), 1)} | "
                 f"{f(med([x.get('qa0') for x in xs]), 0)} | {f(100 * pr / sh if sh else None, 2)}c | {f(100 * (pr + rb) / sh if sh else None, 2)}c | ${f(pr / n if n else None, 3)} |")
    L.append("")

    # ---- 2b timed posts, pair view ----
    L += ["## 2b Posting both sides at a fixed time (the notebook times) - pair view", "",
          "| cents below bb | posted at | rounds | both sides filled | pair completion | money in | profit | +rebate | return on cost | profit+rebate 95% CI | gate |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    gate_rows = []
    for o in sorted({x["o"] for x in timed}):
        for e0 in sorted({x["e0"] for x in timed}):
            R = collections.defaultdict(dict)
            for x in timed:
                if x["o"] == o and x["e0"] == e0: R[x["start"]][x["outcome"]] = x
            prof, cost, reb, fill, pairs, both = [], 0.0, 0.0, 0.0, 0.0, 0
            for st, d in R.items():
                u = d.get("Up"); dn = d.get("Down")
                if not u or not dn: continue
                prof.append(u["profit"] + dn["profit"] + u["rebate"] + dn["rebate"]); cost += u["cost"] + dn["cost"]; reb += u["rebate"] + dn["rebate"]
                fill += u["filled"] + dn["filled"]; pairs += min(u["filled"], dn["filled"]); both += int(u["filled"] > 0 and dn["filled"] > 0)
            if not prof: continue
            lo, hi = boot_ci(prof); p = sum(prof) - reb; pc = 2 * pairs / fill if fill else None
            g = (lo > 0) and (pc is not None and pc >= 0.95)
            L.append(f"| {o} | {e0} ({300 - e0} s left) | {len(prof):,} | {pct(both / len(prof))} | {pct(pc)} | ${f(cost, 0)} | ${f(p, 0)} | ${f(reb, 0)} | {f(100 * p / cost if cost else None, 2)}% | [{f(lo, 0)}, {f(hi, 0)}] | {'PASS' if g else 'FAIL'} |")
            gate_rows.append(g)
    L.append("")

    # ---- 2c parity ----
    L += ["## 2c Odd, even and round-number prices", "", "Our bid price parity (the price we post, 0 or 1 cent below the best bid):", "",
          "| cents below bb | price parity | orders | filled (any) | median queue ahead | edge/share | edge/share + rebate |", "|---|---|---|---|---|---|---|"]
    for o in (0, 1):
        for lab, key in (("even cent", lambda x: x["odd"] == 0 and not x["r5"]), ("odd cent", lambda x: x["odd"] == 1 and not x["r5"]), ("round (x0/x5)", lambda x: x["r5"] == 1)):
            xs = [x for x in timed if x["o"] == o and key(x)]; n = len(xs)
            if not n: continue
            sh = sum(x["filled"] for x in xs); pr = sum(x["profit"] for x in xs); rb = sum(x["rebate"] for x in xs)
            L.append(f"| {o} | {lab} | {n:,} | {pct(sum(x['filled'] > 0 for x in xs) / n)} | {f(med([x.get('qa0') for x in xs]), 0)} | {f(100 * pr / sh if sh else None)}c | {f(100 * (pr + rb) / sh if sh else None)}c |")
    L += ["", "Crowding: average shares resting at the best bid and the next three cents below it, by the parity of the best-bid price:", "",
          "| best bid parity | snapshots | at bb | bb-1c | bb-2c | bb-3c | at ask | ask+1c |", "|---|---|---|---|---|---|---|---|"]
    for lab, key in (("even cent", lambda x: x["odd"] == 0 and not x["r5"]), ("odd cent", lambda x: x["odd"] == 1 and not x["r5"]), ("round (x0/x5)", lambda x: x["r5"] == 1)):
        xs = [x for x in crowd if key(x)]; n = len(xs)
        if not n: continue
        sb = [statistics.mean(x["sb"][i] for x in xs) for i in range(4)]
        sa_ = [x["sa"] for x in xs if x["sa"]]; sa = [statistics.mean(x[i] for x in sa_) for i in range(2)] if sa_ else [None, None]
        L.append(f"| {lab} | {n:,} | {f(sb[0], 0)} | {f(sb[1], 0)} | {f(sb[2], 0)} | {f(sb[3], 0)} | {f(sa[0], 0)} | {f(sa[1], 0)} |")
    L.append("")

    # ---- 2d flow ----
    L += ["## 2d Read the flow, then place: continuous join-the-best-bid tactic with filters", "",
          "base = quote both sides at the best bid all window (60-240 s). sweep = pull the quote for 5 s when >= 30 shares hit our side's bids "
          "within 1 s or the best bid drops 2 cents within 1 s. imb = quote only while the top-3 bid size >= top-3 ask size on that side. both = sweep + imb.", "",
          "| variant | rounds | fills | money in | profit | +rebate | return on cost | profit+rebate 95% CI | pair completion | days +/- | worst day | gate |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in ("base", "sweep", "imb", "both"):
        rows = flow.get(v, [])
        if not rows: continue
        cost = sum(r["cost"] for r in rows); p = sum(r["profit"] for r in rows); rb = sum(r["rebate"] for r in rows)
        fills = sum(r["fills"] for r in rows); sh = sum(r["up_sh"] + r["dn_sh"] for r in rows); pairs = sum(r["pairs"] for r in rows)
        lo, hi = boot_ci([r["profit"] + r["rebate"] for r in rows]); pc = 2 * pairs / sh if sh else None
        days = flow_days[v]; pos = sum(1 for x in days.values() if x > 0)
        g = lo > 0 and pc is not None and pc >= 0.95
        L.append(f"| {v} | {len(rows):,} | {fills:,} | ${f(cost, 0)} | ${f(p, 0)} | ${f(rb, 0)} | {f(100 * p / cost if cost else None)}% | [{f(lo, 0)}, {f(hi, 0)}] | {pct(pc)} | {pos}/{len(days)} | ${f(min(days.values()), 0)} | {'PASS' if g else 'FAIL'} |")
        gate_rows.append(g)
    L.append("")

    # ---- 2e exits ----
    L += ["## 2e After a fill: sell at once, pair at once, or hold?", "",
          "For every fill of the base tactic (above): A = sell the shares at once at the best bid as a taker (fee paid, assumes the best-bid size takes all of it), "
          "B = buy the other side at its ask as a taker if the pair then costs <= 0.97 (else not available), C = hold to settlement.", ""]
    sh = sum(x["f"] for x in exits)
    if sh:
        A = [x for x in exits if x["A"] is not None]; B = [x for x in exits if x["B"] is not None]
        shA = sum(x["f"] for x in A); shB = sum(x["f"] for x in B)
        L += ["| exit | fills covered | shares | total profit | per share |", "|---|---|---|---|---|",
              f"| A sell at once (taker) | {len(A):,} | {f(shA, 0)} | ${f(sum(x['A'] for x in A), 0)} | {f(100 * sum(x['A'] for x in A) / shA if shA else None)}c |",
              f"| C hold, same fills as A | {len(A):,} | {f(shA, 0)} | ${f(sum(x['C'] for x in A), 0)} | {f(100 * sum(x['C'] for x in A) / shA if shA else None)}c |",
              f"| B pair at once (taker), when available | {len(B):,} | {f(shB, 0)} | ${f(sum(x['B'] for x in B), 0)} | {f(100 * sum(x['B'] for x in B) / shB if shB else None)}c |",
              f"| C hold, same fills as B | {len(B):,} | {f(shB, 0)} | ${f(sum(x['C'] for x in B), 0)} | {f(100 * sum(x['C'] for x in B) / shB if shB else None)}c |",
              f"| C hold, all fills | {len(exits):,} | {f(sh, 0)} | ${f(sum(x['C'] for x in exits), 0)} | {f(100 * sum(x['C'] for x in exits) / sh)}c |", ""]
    L += ["## Verdict", "", f"Rules tested with a gate: {len(gate_rows)}; PASS: {sum(gate_rows)}; FAIL: {len(gate_rows) - sum(gate_rows)}.",
          "Gate = profit 95% CI low > 0 after rebate AND pair completion >= 95%, at the measured latency.", ""]
    open(a.out, "w").write("\n".join(L)); print("\n".join(L))


if __name__ == "__main__":
    main()
