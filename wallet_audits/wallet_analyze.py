#!/usr/bin/env python3
"""wallet_analyze.py - audit a wallet's pulled activity (wallet_pull.py output) with focus on crypto Up/Down markets.
  python3 wallet_analyze.py ~/wallets/norm1e69 --out ~/wallets/norm1e69/AUDIT.md
Reconstructs per-market cash flow (buys out, sells + redeems + merges in), flags taker fills by joining the
taker-only /trades feed, parses the 5-minute round start from the slug, and reports tactic statistics."""
import argparse, collections, csv, datetime, os, re, statistics

ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--out", required=True); a = ap.parse_args()


def kind(title):
    t = title or ""
    for name, key in (("BTC 5m", "Bitcoin Up or Down"), ("ETH 5m", "Ethereum Up or Down"), ("SOL 5m", "Solana Up or Down"), ("XRP 5m", "XRP Up or Down")):
        if key in t: return name
    if "Up or Down" in t: return "other Up/Down"
    if t.startswith("LoL") or "esports" in t.lower(): return "esports"
    return "other"


rows = list(csv.DictReader(open(os.path.join(a.dir, "activity.csv"))))
def tkey(ts, asset, side, size, price):
    return (int(float(ts)), asset, side, round(float(size or 0), 4), round(float(price or 0), 4))


takers = set()
tp = os.path.join(a.dir, "trades_taker.csv")
if os.path.exists(tp):
    for r in csv.DictReader(open(tp)):
        takers.add(tkey(r["timestamp"], r["asset"], r["side"], r["size"], r["price"]))
types = collections.Counter(r["type"] for r in rows)
slug_t = re.compile(r"-(\d{10})$")

M = collections.defaultdict(lambda: dict(buy=0.0, sell=0.0, redeem=0.0, merge=0.0, split=0.0, fills=0, taker=0, taker_usd=0.0, usd=0.0,
                                         sh={}, cost={}, first=None, last=None, kind=None, title=None, start=None, late=[], prices=[]))
daily = collections.defaultdict(float); rebates = collections.defaultdict(float)
for r in rows:
    ty = r["type"]; ts = int(float(r["timestamp"])); usd = float(r["usdcSize"] or 0)
    if ty in ("TAKER_REBATE", "MAKER_REBATE"):
        rebates[ty] += usd; continue
    key = r["conditionId"] or r["title"]; m = M[key]
    if m["kind"] is None:
        m["kind"] = kind(r["title"]); m["title"] = r["title"]
        g = slug_t.search(r["slug"] or "")
        if g and "updown" in (r["slug"] or ""): m["start"] = int(g.group(1))
    m["first"] = ts if m["first"] is None else min(m["first"], ts); m["last"] = ts if m["last"] is None else max(m["last"], ts)
    if ty == "TRADE":
        side = r["side"]; out = r["outcome"]; sz = float(r["size"] or 0); px = float(r["price"] or 0)
        m["fills"] += 1; m["usd"] += usd
        if tkey(r["timestamp"], r["asset"], side, r["size"], r["price"]) in takers:
            m["taker"] += 1; m["taker_usd"] += usd
        if side == "BUY":
            m["buy"] += usd; m["sh"][out] = m["sh"].get(out, 0.0) + sz; m["cost"][out] = m["cost"].get(out, 0.0) + usd; m["prices"].append(px)
            if m["start"]: m["late"].append(ts - m["start"])
        else:
            m["sell"] += usd; m["sh"][out] = m["sh"].get(out, 0.0) - sz
    elif ty == "REDEEM": m["redeem"] += usd
    elif ty == "MERGE": m["merge"] += usd
    elif ty == "SPLIT": m["split"] += usd
for key, m in M.items():
    m["pnl"] = m["sell"] + m["redeem"] + m["merge"] - m["buy"] - m["split"]
    daily[datetime.datetime.fromtimestamp(m["last"], datetime.timezone.utc).strftime("%Y-%m-%d")] += m["pnl"]

L = []
L.append(f"# Wallet audit from the public activity feed - {len(rows):,} activity rows, {len(M):,} markets, {rows[0]['timestamp'] and datetime.datetime.fromtimestamp(int(float(rows[0]['timestamp'])), datetime.timezone.utc):%Y-%m-%d} .. {datetime.datetime.fromtimestamp(int(float(rows[-1]['timestamp'])), datetime.timezone.utc):%Y-%m-%d}")
L.append(""); L.append("Row types: " + ", ".join(f"{k} {v:,}" for k, v in types.most_common()))
L.append(f"Rebate rows in the feed: taker ${rebates['TAKER_REBATE']:,.0f}, maker ${rebates['MAKER_REBATE']:,.0f}")
L.append(f"Taker-only feed rows: {len(takers):,}")
L.append("")
L.append("## By market type (cash flow = sells + redeems + merges - buys - splits; before fees, before rebates)")
L.append(""); L.append("| type | markets | fills | fills/market | money in (buys) | profit | return on buys | taker share of fills | taker share of $ | markets won |")
L.append("|---|---|---|---|---|---|---|---|---|---|")
byk = collections.defaultdict(list)
for m in M.values(): byk[m["kind"]].append(m)
for k in sorted(byk, key=lambda k: -sum(x["buy"] for x in byk[k])):
    xs = byk[k]; fills = sum(x["fills"] for x in xs); buy = sum(x["buy"] for x in xs); pnl = sum(x["pnl"] for x in xs)
    tk = sum(x["taker"] for x in xs); tku = sum(x["taker_usd"] for x in xs); usd = sum(x["usd"] for x in xs)
    L.append(f"| {k} | {len(xs):,} | {fills:,} | {fills / len(xs):.1f} | ${buy:,.0f} | ${pnl:,.0f} | {100 * pnl / buy if buy else 0:+.2f}% | {100 * tk / fills if fills else 0:.1f}% | {100 * tku / usd if usd else 0:.1f}% | {100 * sum(1 for x in xs if x['pnl'] > 0) / len(xs):.1f}% |")
L.append("")

crypto = [m for m in M.values() if m["kind"].endswith("5m") and m["start"]]
if crypto:
    L.append(f"## The crypto 5-minute tactic ({len(crypto):,} rounds with a parsable start time)"); L.append("")
    both = [m for m in crypto if len([o for o, s in m["sh"].items()]) >= 2 and all(m["cost"].get(o, 0) > 0 for o in ("Up", "Down"))]
    L.append(f"- rounds where it bought BOTH Up and Down: {len(both):,} ({100 * len(both) / len(crypto):.1f}%)")
    # average buy price per side across all crypto rounds
    px_up = [p for m in crypto for p in m["prices"]]
    L.append(f"- buy price per share: median {statistics.median(px_up):.2f}, 10th pct {sorted(px_up)[len(px_up) // 10]:.2f}, 90th pct {sorted(px_up)[9 * len(px_up) // 10]:.2f} ({len(px_up):,} buy fills)")
    hist = collections.Counter(round(p, 2) for p in px_up)
    L.append("- most common buy prices: " + ", ".join(f"{p:.2f} ({100 * c / len(px_up):.1f}%)" for p, c in hist.most_common(8)))
    late = [x for m in crypto for x in m["late"] if 0 <= x <= 400]
    if late:
        L.append(f"- when in the round it buys (seconds after the round start): median {statistics.median(late):.0f} s, 10th pct {sorted(late)[len(late) // 10]:.0f} s, 90th pct {sorted(late)[9 * len(late) // 10]:.0f} s; share of buys in the last 60 s: {100 * sum(1 for x in late if x >= 240) / len(late):.1f}%; after the round ended: {100 * sum(1 for m in crypto for x in m['late'] if x > 300) / max(1, sum(len(m['late']) for m in crypto)):.1f}%")
    mx = [m["buy"] for m in crypto]
    L.append(f"- money per round (buys): median ${statistics.median(mx):,.0f}, 90th pct ${sorted(mx)[9 * len(mx) // 10]:,.0f}, max ${max(mx):,.0f}")
    fl = [m["fills"] for m in crypto]
    L.append(f"- fills per round: median {statistics.median(fl):.0f}, mean {statistics.mean(fl):.1f}, max {max(fl)}")
    tk = sum(m["taker"] for m in crypto); tf = sum(m["fills"] for m in crypto)
    L.append(f"- taker fills: {tk:,} of {tf:,} = {100 * tk / tf:.1f}% of fills, {100 * sum(m['taker_usd'] for m in crypto) / max(1e-9, sum(m['usd'] for m in crypto)):.1f}% of dollars")
    pcs = []; lefts = []
    for m in both:
        su, sd = m["sh"].get("Up", 0), m["sh"].get("Down", 0)
        au = m["cost"]["Up"] / su if su > 0 else None; ad = m["cost"]["Down"] / sd if sd > 0 else None
        if au and ad: pcs.append(au + ad)
        if su + sd > 0: lefts.append(abs(su - sd) / (su + sd))
    if pcs: L.append(f"- in both-sides rounds: median pair cost (avg Up + avg Down buy price, net shares) {statistics.median(pcs):.3f}, 90th pct {sorted(pcs)[9 * len(pcs) // 10]:.3f}; rounds with pair cost < 1.00: {100 * sum(1 for x in pcs if x < 1) / len(pcs):.1f}%; median unpaired share of the round's shares {100 * statistics.median(lefts):.1f}%")
    # per-round outcome buckets
    pos = [m for m in crypto if m["pnl"] > 0]; neg = [m for m in crypto if m["pnl"] < 0]
    L.append(f"- rounds won {len(pos):,} ({100 * len(pos) / len(crypto):.1f}%), lost {len(neg):,}; average win ${statistics.mean([m['pnl'] for m in pos]) if pos else 0:,.2f}, average loss ${statistics.mean([m['pnl'] for m in neg]) if neg else 0:,.2f}")
    L.append(f"- profit per round: mean ${statistics.mean([m['pnl'] for m in crypto]):,.2f}, median ${statistics.median([m['pnl'] for m in crypto]):,.2f}")
    # by taker share bucket
    L.append(""); L.append("| round's taker share of fills | rounds | money in | profit | return |"); L.append("|---|---|---|---|---|")
    for lab, lo_, hi_ in (("0% (pure maker)", -1, 0.0), ("1-50%", 0.0, 0.5), ("51-99%", 0.5, 0.999), ("100% (pure taker)", 0.999, 2)):
        xs = [m for m in crypto if m["fills"] and lo_ < m["taker"] / m["fills"] <= hi_]
        if xs: L.append(f"| {lab} | {len(xs):,} | ${sum(x['buy'] for x in xs):,.0f} | ${sum(x['pnl'] for x in xs):,.0f} | {100 * sum(x['pnl'] for x in xs) / max(1e-9, sum(x['buy'] for x in xs)):+.2f}% |")
    L.append("")
    L.append("## Top 12 crypto rounds by profit (to compare with the screenshot)"); L.append("")
    L.append("| round | buys $ | profit | Up shares bought | Down shares bought | avg Up | avg Down | fills | taker fills |"); L.append("|---|---|---|---|---|---|---|---|---|")
    for m in sorted(crypto, key=lambda m: -m["pnl"])[:12]:
        L.append(f"| {m['title']} | ${m['buy']:,.0f} | ${m['pnl']:,.0f} | {m['sh'].get('Up', 0) + 0:,.0f} | {m['sh'].get('Down', 0) + 0:,.0f} | {m['cost'].get('Up', 0) / m['sh']['Up'] if m['sh'].get('Up') else 0:.2f} | {m['cost'].get('Down', 0) / m['sh']['Down'] if m['sh'].get('Down') else 0:.2f} | {m['fills']} | {m['taker']} |")
    L.append(""); L.append("## Worst 6 crypto rounds"); L.append("")
    for m in sorted(crypto, key=lambda m: m["pnl"])[:6]:
        L.append(f"- {m['title']}: buys ${m['buy']:,.0f}, profit ${m['pnl']:,.0f}, Up {m['sh'].get('Up', 0):,.0f} sh, Down {m['sh'].get('Down', 0):,.0f} sh, fills {m['fills']}, taker {m['taker']}")
    L.append("")
    # concentration
    srt = sorted(crypto, key=lambda m: -m["pnl"]); tot = sum(m["pnl"] for m in crypto)
    for n in (10, 100, 1000):
        L.append(f"- top {n} rounds = ${sum(m['pnl'] for m in srt[:n]):,.0f} of ${tot:,.0f} total crypto profit ({100 * sum(m['pnl'] for m in srt[:n]) / tot if tot else 0:.1f}%)")
    L.append("")
L.append("## Daily profit from the feed (before fees and rebates), last 15 days"); L.append("")
for d in sorted(daily)[-15:]: L.append(f"- {d}: ${daily[d]:,.0f}")
L.append(""); L.append(f"Total from the feed: ${sum(daily.values()):,.0f} (compare with the official trade_pnl)")
open(a.out, "w").write("\n".join(L)); print("\n".join(L))
