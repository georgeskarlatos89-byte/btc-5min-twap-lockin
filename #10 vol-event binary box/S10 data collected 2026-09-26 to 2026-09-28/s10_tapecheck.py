"""For every S10 maker-quote window: did the public tape print a trade that could have filled a resting 0.45 bid?
Route A (the engine's own rule): a taker SELL of token X at price <= 0.45 -> fills our bid on X.
Route B (complementary match, which the engine never looked at): a taker BUY of the OTHER token at price >= 0.55
        -> Polymarket can match it against a resting bid on X at 1 - price <= 0.45.
Read-only: gamma (market id) + data-api /trades (taker-side public tape)."""
import re, json, sys, time, csv, datetime, calendar, collections, urllib.request
LOG, OUTDIR = sys.argv[1], sys.argv[2]
BID = 0.45

def get(u, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as r:
                return json.load(r)
        except Exception:
            if i + 1 == tries: raise
            time.sleep(1.5)

# ---- 1) windows from the log
day = datetime.date(2026, 9, 26); prev = -1; rows = []
for l in open(LOG, encoding="utf-8", errors="replace"):
    m = re.match(r"^\[(\d\d):(\d\d):(\d\d)Z\] (.*)$", l.rstrip("\n"))
    if not m: continue
    s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    if s < prev - 3600: day += datetime.timedelta(days=1)
    prev = s; rows.append((calendar.timegm(day.timetuple()) + s, m[4]))
T = {"5m": 300, "15m": 900}; FR = {"5m": 0.40, "15m": 0.50}
wins = []
for i, (ts, b) in enumerate(rows):
    m = re.match(r"MAKER \(DRY\) (5m|15m) BUY Up ", b)
    if not m: continue
    lab = m[1]; cur = ts // T[lab] * T[lab]
    pre = (cur + T[lab] - ts) <= 30                      # last 30 s of a round -> quote belongs to the NEXT round (pre-arm)
    st = cur + T[lab] if pre else cur
    end, why = None, "window end"
    for ts2, b2 in rows[i + 1:]:
        if ts2 - ts > 1000: break
        if b2.startswith("PULL MAKERS") or "BOX DISARMED" in b2 or "KILL file present" in b2:
            end, why = ts2, re.sub(r"[-+]?\$?\d+(\.\d+)?", "#", b2)[:34]; break
    wend = int(st + FR[lab] * T[lab])
    if end is None or end > wend: end, why = wend, "window end"
    wins.append(dict(series=lab, start=int(st), placed=int(ts), ended=int(end), why=why, preopen=pre))

# ---- 2) tape per round
cache = {}
def tape(lab, st):
    k = (lab, st)
    if k in cache: return cache[k]
    slug = "btc-updown-%s-%d" % (lab, st)
    ev = None
    for q in ("&closed=true", ""):
        try:
            ev = get("https://gamma-api.polymarket.com/events?slug=" + slug + q)
            if ev: break
        except Exception:
            pass
    if not ev:
        cache[k] = (None, None, "no gamma event"); return cache[k]
    mk = ev[0]["markets"][0]; cond = mk["conditionId"]; out = []; off = 0
    while True:
        t = get("https://data-api.polymarket.com/trades?market=%s&limit=1000&offset=%d" % (cond, off))
        out += t or []
        if not t or len(t) < 1000 or off >= 9000: break
        off += 1000; time.sleep(0.15)
    time.sleep(0.12)
    cache[k] = (cond, out, mk.get("outcomePrices")); return cache[k]

def routes(L):
    a = [x for x in L if x["side"] == "SELL" and float(x["price"]) <= BID + 1e-9]
    b = [x for x in L if x["side"] == "BUY" and float(x["price"]) >= 1 - BID - 1e-9]
    return a, b

res = []; t0 = time.time()
for n, w in enumerate(wins):
    cond, tr, extra = tape(w["series"], w["start"])
    r = dict(w); r["cond"] = cond
    if tr is None:
        r.update(error=extra); res.append(r); continue
    inw = [x for x in tr if w["placed"] <= int(x["timestamp"]) <= w["ended"]]
    tol = [x for x in tr if w["placed"] - 2 <= int(x["timestamp"]) <= w["ended"] + 2]
    a, b = routes(inw); a2, b2 = routes(tol)
    sells = [float(x["price"]) for x in inw if x["side"] == "SELL"]; buys = [float(x["price"]) for x in inw if x["side"] == "BUY"]
    fillable = {x["outcome"] for x in a} | {("Down" if x["outcome"] == "Up" else "Up") for x in b}
    r.update(round_trades=len(tr), first_trade_ts=min((int(x["timestamp"]) for x in tr), default=None), trades_in_window=len(inw),
             routeA_sell_le_045=len(a), routeA_shares=round(sum(float(x["size"]) for x in a), 1), routeA_sides="/".join(sorted({x["outcome"] for x in a})),
             routeB_buy_ge_055=len(b), routeB_shares=round(sum(float(x["size"]) for x in b), 1), routeB_bought="/".join(sorted({x["outcome"] for x in b})),
             routeA_tol2s=len(a2), routeB_tol2s=len(b2),
             min_sell_px=min(sells, default=None), max_buy_px=max(buys, default=None),
             our_sides_fillable="/".join(sorted(fillable)), both_sides_fillable=len(fillable) == 2)
    res.append(r)
    if n % 25 == 0: print("  ..%d/%d windows, %d rounds fetched, %.0fs" % (n, len(wins), len(cache), time.time() - t0), flush=True)

json.dump(res, open(OUTDIR + "/s10_tapecheck_windows.json", "w"), indent=0)
cols = ["series", "start", "placed", "ended", "why", "preopen", "round_trades", "first_trade_ts", "trades_in_window", "routeA_sell_le_045", "routeA_shares",
        "routeA_sides", "routeB_buy_ge_055", "routeB_shares", "routeB_bought", "routeA_tol2s", "routeB_tol2s", "min_sell_px", "max_buy_px",
        "our_sides_fillable", "both_sides_fillable", "error"]
with open(OUTDIR + "/s10_tapecheck_windows.csv", "w", newline="") as f:
    w_ = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w_.writeheader()
    for r in res: w_.writerow(r)

# ---- 3) summary
ok = [r for r in res if "error" not in r]
S = []
def P(*a): S.append(" ".join(str(x) for x in a))
utc = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
P("S10 tape check - did any trade print that could have filled a resting 0.45 bid while a quote was up?")
P("generated", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), "| source: data-api /trades (taker side), gamma for market ids")
P("quote windows (one per Up+Down quote pair):", len(res), "| tape fetched for", len(ok), "| errors", len(res) - len(ok))
P("first window", utc(min(r["placed"] for r in ok)), "| last window", utc(max(r["placed"] for r in ok)))
P("by series:", dict(collections.Counter(r["series"] for r in ok)), "| placed BEFORE the round opened (pre-arm zone):", sum(r["preopen"] for r in ok))
L = sorted(r["ended"] - r["placed"] for r in ok)
P("window length seconds: median", L[len(L) // 2], "p25", L[len(L) // 4], "p75", L[3 * len(L) // 4], "max", L[-1])
P("windows with ZERO trades of any kind in them:", sum(r["trades_in_window"] == 0 for r in ok), "of", len(ok))
P("total trades inside all windows:", sum(r["trades_in_window"] for r in ok))
P("")
P("ROUTE A (engine's rule: taker SELL <= 0.45): windows with at least one such print:", sum(r["routeA_sell_le_045"] > 0 for r in ok),
  "| prints", sum(r["routeA_sell_le_045"] for r in ok), "| shares", round(sum(r["routeA_shares"] for r in ok), 1))
P("ROUTE B (taker BUY of the other token >= 0.55): windows with at least one:", sum(r["routeB_buy_ge_055"] > 0 for r in ok),
  "| prints", sum(r["routeB_buy_ge_055"] for r in ok), "| shares", round(sum(r["routeB_shares"] for r in ok), 1))
P("either route:", sum((r["routeA_sell_le_045"] + r["routeB_buy_ge_055"]) > 0 for r in ok), "windows | BOTH of our sides fillable in the same window:",
  sum(bool(r["both_sides_fillable"]) for r in ok))
P("with a +-2 s tolerance on the window edges: route A windows", sum(r["routeA_tol2s"] > 0 for r in ok), "| route B windows", sum(r["routeB_tol2s"] > 0 for r in ok))
ms = [r["min_sell_px"] for r in ok if r["min_sell_px"] is not None]
if ms: P("lowest taker SELL price seen inside a window: min", min(ms), "median", sorted(ms)[len(ms) // 2], "| windows that had any SELL:", len(ms))
mb = [r["max_buy_px"] for r in ok if r["max_buy_px"] is not None]
if mb: P("highest taker BUY price seen inside a window: max", max(mb), "median", sorted(mb)[len(mb) // 2], "| windows that had any BUY:", len(mb))
P("")
for pre in (True, False):
    g = [r for r in ok if r["preopen"] == pre]
    if g:
        P(("PRE-OPEN quotes" if pre else "IN-ROUND quotes"), "n", len(g), "| zero-trade windows", sum(r["trades_in_window"] == 0 for r in g),
          "| trades in windows", sum(r["trades_in_window"] for r in g), "| route A windows", sum(r["routeA_sell_le_045"] > 0 for r in g),
          "| route B windows", sum(r["routeB_buy_ge_055"] > 0 for r in g), "| both sides", sum(bool(r["both_sides_fillable"]) for r in g),
          "| median length", sorted(r["ended"] - r["placed"] for r in g)[len(g) // 2], "s")
P("how windows ended:", dict(collections.Counter(r["why"] for r in ok).most_common(6)))
hits = [r for r in ok if r["routeA_sell_le_045"] + r["routeB_buy_ge_055"] > 0]
P(""); P("windows with a fillable print (first 30):")
for r in hits[:30]:
    P(" ", r["series"], r["start"], utc(r["placed"]).strftime("%m-%d %H:%M:%S"), "len %ds" % (r["ended"] - r["placed"]), "pre-open" if r["preopen"] else "in-round",
      "A:%dx %ssh %s" % (r["routeA_sell_le_045"], r["routeA_shares"], r["routeA_sides"]),
      "B:%dx %ssh bought %s" % (r["routeB_buy_ge_055"], r["routeB_shares"], r["routeB_bought"]), "| our fillable side(s):", r["our_sides_fillable"], "| ended by", r["why"])
open(OUTDIR + "/s10_tapecheck_summary.txt", "w", encoding="utf-8").write("\n".join(S) + "\n")
print("\n".join(S))
