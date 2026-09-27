#!/usr/bin/env python3
"""
backtest7d.py — 7-day backtest engine: chain-verified labels vs public-API labels.

READ-ONLY. No keys, no orders. Public endpoints + files already recorded on this box.

WHAT IT DOES (each stage caches under cache/, so a re-run is cheap and resumable)
  1  round grid for the last N days (BTC 5m + 15m) with condition_id + gamma outcome
  2  CHAIN labels: CTF payoutNumerators(conditionId, 0|1) on Polygon (index 0 = Up)
  3  PUBLIC labels: polymarket.com/api/past-results (open, close, outcome) — 7-day window
  4  OBSERVER rows: s1_harness/rounds.csv (our own open/end TWAP values + the gamma label
     production saw at settle time)
  5  merged label table, one row per round
  6  settlement-rule audit vs chain, by boundary-gap bucket
  6b boundary-tick study on our own 1 Hz RTDS recordings (s2_openprint ticks): which tick
     reproduces the chain outcome, and which tick past-results' open/close actually are
  7  strategy signals as ACTUALLY generated in production (S1 trader SCORE ledger, S1 harness
     rule rows); other strategies are listed with their trade counts
  8  scoring under every label source with the standing risk rules
     ($10/trade, 1/round, <=3/hour, $20 daily stop per UTC day), fee = 0.07*p*(1-p)/share
  9  CSVs with named columns + REPORT.md, named "backtest from <today> to <earliest>"

USAGE
  python3 backtest7d.py                 # full run, 7 days
  python3 backtest7d.py --days 7 --no-fetch   # re-score from cache only
"""
import argparse, csv, glob, json, math, os, re, sys, threading, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUTBASE = os.path.join(HERE, "out")
HOME = os.path.expanduser("~")
DEF_ROUNDS = os.path.join(HOME, "s1_harness", "rounds.csv")
DEF_TRADER_LOG = os.path.join(HOME, "s1_harness", "trader.log")
DEF_S2_SESSIONS = os.path.join(HOME, "s2_openprint", "data", "sessions")

GAMMA = "https://gamma-api.polymarket.com"
CTF, SEL = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045", "0x0504c814"   # payoutNumerators(bytes32,uint256)
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org",
        "https://rpc-mainnet.matic.quiknode.pro", "https://1rpc.io/matic"]
UA = {"User-Agent": "Mozilla/5.0 (research; read-only backtest)"}
T_OF = {"5m": 300, "15m": 900}
PR_VARIANT = {"5m": "fiveminute", "15m": "fifteen"}
TRADE_USD, DAILY_STOP, MAX_PER_HOUR, FEE_RATE = 10.0, 20.0, 3, 0.07
SIGNAL_LEAD = {"5m": 45, "15m": 90}          # S1 decision point: seconds before the round end
GAP_BUCKETS = [("<1bp", 0, 1), ("1-2bp", 1, 2), ("2-4bp", 2, 4), ("4-8bp", 4, 8), (">8bp", 8, 1e9)]

def log(*a): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z]", *a, flush=True)
def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def day_of(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
def parse_iso(s): return int(datetime.fromisoformat(s.replace(".000Z", "Z").replace("Z", "+00:00")).timestamp())

def hj(url, tries=3, timeout=20, sleep=0.8):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == tries - 1: raise
            time.sleep(sleep * (i + 1))

def load_jsonl(path):
    out = []
    if os.path.exists(path):
        for ln in open(path):
            try: out.append(json.loads(ln))
            except Exception: pass
    return out

def jl(x): return json.loads(x) if isinstance(x, str) else x

# ------------------------------------------------------------------ stage 1: gamma grid
def gamma_outcome(m):
    """('up'|'down'|'', closed_bool) from a gamma market object; '' = not a clean 1/0 yet."""
    try:
        op, oc = jl(m["outcomePrices"]), jl(m["outcomes"])
        if sorted(float(x) for x in op) == [0.0, 1.0]:
            return oc[[float(x) for x in op].index(1.0)].lower(), bool(m.get("closed"))
    except Exception:
        pass
    return "", bool(m.get("closed"))

def series_id(label):
    now = int(time.time()); T = T_OF[label]
    for k in (2, 3, 4, 6):
        ev = hj(f"{GAMMA}/events?slug=btc-updown-{label}-{(now // T) * T - k * T}")
        if ev and ev[0].get("series"): return str(ev[0]["series"][0]["id"])
    raise RuntimeError(f"cannot resolve gamma series id for {label}")

def stage_gamma(label, t_from, t_to, fetch=True):
    path = os.path.join(CACHE, f"gamma_{label}.jsonl")
    have = {r["start"]: r for r in load_jsonl(path)}
    if not fetch: return have
    T = T_OF[label]; sid = series_id(label); new = 0
    d = datetime.fromtimestamp(t_from, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(t_to, timezone.utc)
    with open(path, "a") as f:
        while d <= end:
            nxt, off = d + timedelta(days=1), 0
            while True:
                q = urllib.parse.urlencode(dict(series_id=sid, limit=100, offset=off, order="id", ascending="true",
                                                start_date_min=d.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                start_date_max=nxt.strftime("%Y-%m-%dT%H:%M:%SZ")))
                res = hj(f"{GAMMA}/events?{q}")
                for ev in res:
                    try: ts = int(ev.get("slug", "").rsplit("-", 1)[1])
                    except Exception: continue
                    if ts < t_from or ts + T > t_to: continue
                    m = (ev.get("markets") or [{}])[0]
                    oc, closed = gamma_outcome(m)
                    rec = dict(start=ts, market=label, condition_id=m.get("conditionId", ""), gamma_outcome=oc,
                               gamma_closed=closed, closed_time=ev.get("closedTime", ""), fetched=int(time.time()))
                    old = have.get(ts)
                    if old is None or (not old.get("gamma_outcome") and oc) or not old.get("condition_id"):
                        have[ts] = rec; f.write(json.dumps(rec) + "\n"); new += 1
                if len(res) < 100 or off > 1000: break
                off += 100; time.sleep(0.15)
            d = nxt; time.sleep(0.15)
        # grid holes: the listing can lag; ask for the round by slug (closed=true first)
        holes = [s for s in range(((t_from + T - 1) // T) * T, t_to - T + 1, T) if s not in have or not have[s].get("condition_id")]
        for s in holes[:400]:
            for q in ("&closed=true", ""):
                try:
                    ev = hj(f"{GAMMA}/events?slug=btc-updown-{label}-{s}{q}")
                    if ev:
                        m = ev[0]["markets"][0]; oc, closed = gamma_outcome(m)
                        rec = dict(start=s, market=label, condition_id=m.get("conditionId", ""), gamma_outcome=oc,
                                   gamma_closed=closed, closed_time=ev[0].get("closedTime", ""), fetched=int(time.time()))
                        have[s] = rec; f.write(json.dumps(rec) + "\n"); new += 1; break
                except Exception:
                    pass
            time.sleep(0.1)
    log(f"gamma {label}: {len(have)} rounds cached (+{new} this run), series {sid}")
    return have

# ------------------------------------------------------------------ stage 2: chain
def rpc_post(u, payload, t=8):
    req = urllib.request.Request(u, data=json.dumps(payload).encode(),
                                 headers=dict(UA, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=t) as r:
        j = json.loads(r.read().decode())
    if isinstance(j, dict) and "error" in j: raise RuntimeError(str(j["error"]))
    return j

def chain_outcome(cid, tries=4):
    if not cid: return "NO_ID"
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": CTF, "data": SEL + cid[2:] + hex(i)[2:].rjust(64, "0")}, "latest"]} for i in (0, 1)]
    for t in range(tries):
        try:
            r = rpc_post(RPCS[t % len(RPCS)], batch)
            tot = [int(x["result"], 16) for x in sorted(r, key=lambda y: y["id"])]
            if tot[0] == tot[1] == 0: return "pending"
            if tot[0] == tot[1]: return "split"
            return "up" if tot[0] > tot[1] else "down"
        except Exception:
            time.sleep(0.3 * (t + 1))
    return "RPC_ERR"

def stage_chain(cids, fetch=True, workers=4):
    path = os.path.join(CACHE, "chain.jsonl")
    have = {r["condition_id"]: r["chain_outcome"] for r in load_jsonl(path)
            if r.get("chain_outcome") in ("up", "down", "split")}
    todo = [c for c in cids if c and c not in have]
    if not fetch or not todo:
        log(f"chain: {len(have)} cached, {len(todo)} not fetched"); return have
    lock, n, errs, t0 = threading.Lock(), 0, 0, time.time()
    with open(path, "a") as f, ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(chain_outcome, c): c for c in todo}
        for fu in as_completed(futs):
            c, co = futs[fu], fu.result()
            with lock:
                n += 1; errs += co in ("RPC_ERR",)
                f.write(json.dumps(dict(condition_id=c, chain_outcome=co, ts=int(time.time()))) + "\n"); f.flush()
                if co in ("up", "down", "split"): have[c] = co
                if n % 500 == 0: log(f"  chain {n}/{len(todo)} errs={errs} {n / max(time.time() - t0, 1e-9):.1f}/s")
    log(f"chain: {len(have)} verified (+{n} this run, errs={errs}, {time.time() - t0:.0f}s)")
    return have

# ------------------------------------------------------------------ stage 3: past-results
def stage_pastresults(label, t_from, t_to, fetch=True):
    path = os.path.join(CACHE, f"pastresults_{label}.jsonl")
    have = {r["start"]: r for r in load_jsonl(path)}
    if not fetch: return have
    T = T_OF[label]; x = (t_to // T) * T; empty = 0; new = 0; pages = 0
    newest_needed = max([s for s in have] or [0])
    with open(path, "a") as f:
        while x > t_from and empty < 3:
            u = (f"https://polymarket.com/api/past-results?symbol=BTC&variant={PR_VARIANT[label]}"
                 f"&assetType=crypto&currentEventStartTime={iso(x)}")
            try: page = hj(u)["data"]["results"]
            except Exception as e:
                log(f"  past-results warn {iso(x)}: {e!r}"[:160]); page = []
            pages += 1; fresh = 0
            for r in page:
                s = parse_iso(r["startTime"])
                if s not in have:
                    rec = dict(start=s, market=label, pr_open=r["openPrice"], pr_close=r["closePrice"],
                               pr_outcome=str(r.get("outcome", "")).lower(), fetched=int(time.time()))
                    have[s] = rec; f.write(json.dumps(rec) + "\n"); fresh += 1; new += 1
            empty = empty + 1 if not page else 0
            x = min(parse_iso(r["startTime"]) for r in page) if page else x - 4 * T
            # everything older than this is already cached -> stop paging early
            if not fresh and page and x <= newest_needed and all((s in have) for s in range(max(t_from, x - 40 * T) // T * T, x, T)):
                break
            time.sleep(0.12)
    log(f"past-results {label}: {len(have)} rounds cached (+{new}, {pages} pages)")
    return have

# ------------------------------------------------------------------ stage 4: observer
def stage_observer(path):
    out = {}
    if not os.path.exists(path): log(f"observer rounds.csv not found: {path}"); return out
    for r in csv.DictReader(open(path, newline="")):
        try: s = int(r["round_start"])
        except Exception: continue
        cov = str(r.get("coverage", ""))
        try: covn = float(cov.rstrip("%"))
        except Exception: covn = None
        def fl(k):
            try: return float(r[k])
            except Exception: return None
        out[(r["market"], s)] = dict(obs_open_twap=fl("open_ref_twap"), obs_open_spot=fl("open_ref_spot"),
                                     obs_full_avg=fl("full_round_avg"), obs_tail60=fl("tail60_avg"), obs_end=fl("end_value"),
                                     obs_settled=str(r.get("settled", "")).lower(), obs_coverage=covn,
                                     h_side=r.get("signal_side", ""), h_ask=fl("book_ask"), h_edge=fl("signal_edge"),
                                     h_model_p=fl("model_p"))
    log(f"observer: {len(out)} rounds from {path}")
    return out

# ------------------------------------------------------------------ stage 5: merge
def sign_up(a, b): return None if a is None or b is None else ("up" if a >= b else "down")   # ties resolve Up
def bps(a, b): return None if a is None or not b else (a - b) / b * 1e4

def build_labels(t_from, t_to, a):
    rows = []
    obs = stage_observer(a.rounds)
    for label in ("5m", "15m"):
        g = stage_gamma(label, t_from, t_to, a.fetch)
        pr = stage_pastresults(label, t_from, t_to, a.fetch)
        T = T_OF[label]
        for s in range(((t_from + T - 1) // T) * T, t_to - T + 1, T):
            gg, pp, oo = g.get(s, {}), pr.get(s, {}), obs.get((label, s), {})
            rows.append(dict(market=label, round_start_unix=s, round_start_utc=iso(s), round_end_utc=iso(s + T),
                             condition_id=gg.get("condition_id", ""), gamma_outcome=gg.get("gamma_outcome", ""),
                             pastresults_outcome=pp.get("pr_outcome", ""), pr_open=pp.get("pr_open"), pr_close=pp.get("pr_close"),
                             pr_gap_bps=bps(pp.get("pr_close"), pp.get("pr_open")),
                             observer_gamma_at_settle=oo.get("obs_settled", ""), obs_coverage_pct=oo.get("obs_coverage"),
                             obs_open_twap=oo.get("obs_open_twap"), obs_open_spot=oo.get("obs_open_spot"),
                             obs_end_value=oo.get("obs_end"), obs_full_round_avg=oo.get("obs_full_avg"),
                             obs_tail60_avg=oo.get("obs_tail60"),
                             obs_end_gap_bps=bps(oo.get("obs_end"), oo.get("obs_open_twap")),
                             obs_fullavg_gap_bps=bps(oo.get("obs_full_avg"), oo.get("obs_open_twap")),
                             rule_end_value=sign_up(oo.get("obs_end"), oo.get("obs_open_twap")) or "",
                             rule_full_avg=sign_up(oo.get("obs_full_avg"), oo.get("obs_open_twap")) or "",
                             rule_tail60=sign_up(oo.get("obs_tail60"), oo.get("obs_open_twap")) or "",
                             rule_end_vs_spot_open=sign_up(oo.get("obs_end"), oo.get("obs_open_spot")) or "",
                             h_side=oo.get("h_side", ""), h_ask=oo.get("h_ask"), h_edge=oo.get("h_edge")))
    chain = stage_chain([r["condition_id"] for r in rows], a.fetch)
    for r in rows:
        r["chain_outcome"] = chain.get(r["condition_id"], "")
        srcs = {k: r[k] for k in ("gamma_outcome", "pastresults_outcome", "observer_gamma_at_settle") if r[k]}
        r["public_disagrees_with_chain"] = ";".join(k for k, v in srcs.items() if r["chain_outcome"] in ("up", "down") and v != r["chain_outcome"])
    return rows

# ------------------------------------------------------------------ stage 6: rule audit
def bucket_of(g):
    if g is None: return None
    g = abs(g)
    for name, lo, hi in GAP_BUCKETS:
        if lo <= g < hi: return name

def rule_audit(rows):
    out = []
    SOURCES = [("pastresults_outcome", "public: past-results outcome", "pr_gap_bps"),
               ("gamma_outcome", "public: gamma outcomePrices (fetched now)", "pr_gap_bps"),
               ("observer_gamma_at_settle", "production: gamma label seen at settle time", "obs_end_gap_bps"),
               ("rule_end_value", "rule: TWAP60 end value >= open TWAP (our ticks)", "obs_end_gap_bps"),
               ("rule_full_avg", "rule: full-round TWAP average >= open TWAP (our ticks)", "obs_fullavg_gap_bps"),
               ("rule_tail60", "rule: last-60s average >= open TWAP (our ticks)", "obs_end_gap_bps"),
               ("rule_end_vs_spot_open", "rule: TWAP60 end value >= open SPOT (our ticks)", "obs_end_gap_bps")]
    for market in ("5m", "15m", "both"):
        R = [r for r in rows if r["chain_outcome"] in ("up", "down") and (market == "both" or r["market"] == market)]
        for col, name, gapcol in SOURCES:
            RR = [r for r in R if r[col] in ("up", "down")]
            if col.startswith("rule_") or col == "observer_gamma_at_settle":
                RR = [r for r in RR if (r["obs_coverage_pct"] or 0) >= 95]        # full-coverage rounds only
            for b in [x[0] for x in GAP_BUCKETS] + ["ALL"]:
                S = [r for r in RR if b == "ALL" or bucket_of(r[gapcol]) == b]
                if not S: continue
                wrong = sum(r[col] != r["chain_outcome"] for r in S)
                out.append(dict(market=market, label_source=name, gap_measured_on=gapcol, gap_bucket=b, rounds=len(S),
                                wrong_vs_chain=wrong, wrong_pct=round(100 * wrong / len(S), 2),
                                accuracy_pct=round(100 - 100 * wrong / len(S), 2)))
    return out

# ------------------------------------------------------------------ stage 6b: boundary ticks
def load_ticks(sess_dir, t_from):
    """{topic: {observed_second: value}} from the S2 recorder (1 Hz, source timestamps are whole seconds)."""
    tw, sp = {}, {}
    for d in sorted(glob.glob(os.path.join(sess_dir, "*")) + glob.glob(os.path.join(os.path.dirname(sess_dir), "preflight"))):
        p = os.path.join(d, "ticks.jsonl")
        if not os.path.exists(p): continue
        for ln in open(p):
            try: r = json.loads(ln)
            except Exception: continue
            o = int(round(r["observed"]))
            if o < t_from - 900: continue
            (tw if r["topic"] == "crypto_prices_twap_sixty" else sp)[o] = r["value"]
    return tw, sp

def tick_study(rows, a, t_from):
    tw, sp = load_ticks(a.s2_sessions, t_from)
    if not tw: log("tick study: no S2 ticks found"); return [], [], {}
    OFFS = [-3, -2, -1, 0, 1, 2, 3]
    acc = defaultdict(lambda: [0, 0]); match_open = Counter(); match_close = Counter(); detail = []; n_round = 0
    for r in rows:
        if r["chain_outcome"] not in ("up", "down"): continue
        s, e = r["round_start_unix"], r["round_start_unix"] + T_OF[r["market"]]
        if not all((s + k) in tw and (e + k) in tw for k in OFFS): continue
        n_round += 1
        b = bucket_of(bps(tw[e], tw[s]))
        for ko in OFFS:
            for ke in OFFS:
                ok = ("up" if tw[e + ke] >= tw[s + ko] else "down") == r["chain_outcome"]
                for key in ((r["market"], "twap60", ko, ke, "ALL"), (r["market"], "twap60", ko, ke, b)):
                    acc[key][0] += ok; acc[key][1] += 1
        for ke in OFFS:                                   # rival: TWAP end vs SPOT open
            if s in sp:
                ok = ("up" if tw[e + ke] >= sp[s] else "down") == r["chain_outcome"]
                acc[(r["market"], "twap_end_vs_spot_open", 0, ke, "ALL")][0] += ok
                acc[(r["market"], "twap_end_vs_spot_open", 0, ke, "ALL")][1] += 1
        # which of our ticks IS past-results' open / close?
        if r["pr_open"] and r["pr_close"]:
            for nm, val, t0, cnt in (("open", r["pr_open"], s, match_open), ("close", r["pr_close"], e, match_close)):
                cands = [(abs(tw[t0 + k] - val), f"twap60@{k:+d}s") for k in OFFS if (t0 + k) in tw] + \
                        [(abs(sp[t0 + k] - val), f"spot@{k:+d}s") for k in OFFS if (t0 + k) in sp]
                best = min(cands)
                cnt[best[1] if best[0] < 0.005 else "no tick within $0.005"] += 1
        detail.append(dict(market=r["market"], round_start_utc=r["round_start_utc"], chain_outcome=r["chain_outcome"],
                           pastresults_outcome=r["pastresults_outcome"], pr_open=r["pr_open"], pr_close=r["pr_close"],
                           our_twap_at_start=tw[s], our_twap_at_end=tw[e], our_spot_at_start=sp.get(s),
                           end_minus_open_bps=round(bps(tw[e], tw[s]), 4),
                           rule_end_value_exact_tick="up" if tw[e] >= tw[s] else "down"))
    tab = [dict(market=k[0], rule=k[1], open_tick_offset_s=k[2], end_tick_offset_s=k[3], gap_bucket=k[4], rounds=v[1],
                correct_vs_chain=v[0], accuracy_pct=round(100 * v[0] / v[1], 2))
           for k, v in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][4], -kv[1][0] / kv[1][1])) if v[1]]
    log(f"tick study: {n_round} rounds with complete 1 Hz boundary ticks")
    return tab, detail, dict(open=dict(match_open), close=dict(match_close), rounds=n_round)

# ------------------------------------------------------------------ stage 7: signals
SCORE_RE = re.compile(r"SCORE (5m|15m) (\d{9,11}): (Up|Down) @ ([0-9.]+) settled (Up|Down) -> (WIN|LOSS)")
SIG_RE = re.compile(r"DRY SIGNAL (5m|15m) t-(\d+)s: (Up|Down) @ ([0-9.]+) edge ([0-9.]+)% gap ([+-][0-9.]+)bps")

def signals_s1_trader(path, t_from, t_to):
    sigs, pend = [], {}
    if not os.path.exists(path): log(f"trader log not found: {path}"); return sigs
    for ln in open(path, errors="replace"):
        m = SIG_RE.search(ln)
        if m: pend[(m.group(1), m.group(3), m.group(4))] = (float(m.group(5)), float(m.group(6)), int(m.group(2))); continue
        m = SCORE_RE.search(ln)
        if not m: continue
        mk, s, side, px = m.group(1), int(m.group(2)), m.group(3).lower(), float(m.group(4))
        if not (t_from <= s < t_to): continue
        edge, gap, lead = pend.pop((mk, m.group(3), m.group(4)), (None, None, SIGNAL_LEAD[mk]))
        sigs.append(dict(strategy="S1 trader (as deployed, DRY)", market=mk, round_start_unix=s, side=side, entry_price=px,
                         decision_unix=s + T_OF[mk] - lead, edge_pct_at_signal=edge, gap_bps_at_signal=gap,
                         label_seen_by_bot=m.group(5).lower()))
    seen, out = set(), []
    for x in sigs:                                               # one trade per round
        k = (x["market"], x["round_start_unix"])
        if k not in seen: seen.add(k); out.append(x)
    return out

def signals_s1_harness(rows):
    out = []
    for r in rows:
        if r["h_side"] in ("Up", "Down") and r["h_ask"]:
            out.append(dict(strategy="S1 harness rule (observer rows)", market=r["market"], round_start_unix=r["round_start_unix"],
                            side=r["h_side"].lower(), entry_price=r["h_ask"],
                            decision_unix=r["round_start_unix"] + T_OF[r["market"]] - SIGNAL_LEAD[r["market"]],
                            edge_pct_at_signal=None if r["h_edge"] is None else round(100 * r["h_edge"], 2),
                            gap_bps_at_signal=r["obs_fullavg_gap_bps"], label_seen_by_bot=r["observer_gamma_at_settle"]))
    return out

# ------------------------------------------------------------------ stage 8: scoring
def session_of(ts):
    d = datetime.fromtimestamp(ts, timezone.utc); h = d.hour + d.minute / 60
    if d.weekday() >= 5: return "WEEKEND"
    if h < 12: return "ASIA 00:00-12:00"
    if 13.5 <= h < 21: return "US 13:30-21:00"
    return "OFF-HOURS"

def trade_pnl(price, win):
    shares = TRADE_USD / price
    fee = shares * FEE_RATE * price * (1 - price)
    gross = shares * (1 - price) if win else -TRADE_USD
    return gross, fee, gross - fee

LABEL_SETS = [("chain", "chain_outcome", "(a) chain-verified"), ("pastresults", "pastresults_outcome", "(b1) public past-results outcome"),
              ("gamma", "gamma_outcome", "(b2) public gamma outcome"), ("bot", None, "(c) label the bot itself used")]

def score(signals, by_round):
    scored, summary, sessions = [], [], []
    for strat in sorted({s["strategy"] for s in signals}):
        S = sorted([s for s in signals if s["strategy"] == strat], key=lambda x: x["decision_unix"])
        for key, col, nice in LABEL_SETS:
            day_pnl, hour_marks, stopped_days = defaultdict(float), [], set()
            agg = dict(n=0, wins=0, gross=0.0, fees=0.0, net=0.0, skipped_stop=0, skipped_rate=0, unlabeled=0,
                       all_n=0, all_wins=0, all_net=0.0)
            sess = defaultdict(lambda: dict(n=0, wins=0, net=0.0))
            for s in S:
                r = by_round.get((s["market"], s["round_start_unix"]), {})
                lab = s["label_seen_by_bot"] if col is None else r.get(col, "")
                if lab not in ("up", "down"): agg["unlabeled"] += 1; continue
                d = day_of(s["decision_unix"])
                hour_marks = [t for t in hour_marks if s["decision_unix"] - t < 3600]
                taken, why = True, ""
                if d in stopped_days: taken, why = False, "daily stop"; agg["skipped_stop"] += 1
                elif len(hour_marks) >= MAX_PER_HOUR: taken, why = False, "3 per hour cap"; agg["skipped_rate"] += 1
                win = lab == s["side"]
                gross, fee, net = trade_pnl(s["entry_price"], win)
                agg["all_n"] += 1; agg["all_wins"] += win; agg["all_net"] += net
                if taken:
                    hour_marks.append(s["decision_unix"]); day_pnl[d] += net
                    agg["n"] += 1; agg["wins"] += win; agg["gross"] += gross; agg["fees"] += fee; agg["net"] += net
                    k = session_of(s["decision_unix"]); sess[k]["n"] += 1; sess[k]["wins"] += win; sess[k]["net"] += net
                    if day_pnl[d] <= -DAILY_STOP: stopped_days.add(d)
                if key == "chain" or True:
                    scored.append(dict(strategy=strat, label_source=nice, market=s["market"], round_start_utc=iso(s["round_start_unix"]),
                                       decision_utc=iso(s["decision_unix"]), session=session_of(s["decision_unix"]), side=s["side"],
                                       entry_price=s["entry_price"], edge_pct_at_signal=s["edge_pct_at_signal"],
                                       gap_bps_at_signal=s["gap_bps_at_signal"], label=lab, win=int(win), trade_taken=int(taken),
                                       skip_reason=why, gross_usd=round(gross, 4), fee_usd=round(fee, 4), net_usd=round(net, 4),
                                       boundary_gap_bps=r.get("pr_gap_bps") if r.get("pr_gap_bps") is not None else r.get("obs_end_gap_bps")))
            n = agg["n"]
            summary.append(dict(strategy=strat, label_source=nice, signals=len(S), trades_taken=n, wins=agg["wins"],
                                win_rate_pct=round(100 * agg["wins"] / n, 2) if n else "", gross_usd=round(agg["gross"], 2),
                                fees_usd=round(agg["fees"], 2), net_usd_fee_adjusted=round(agg["net"], 2),
                                net_per_trade_usd=round(agg["net"] / n, 4) if n else "",
                                avg_entry_price=round(sum(s["entry_price"] for s in S) / len(S), 4) if S else "",
                                breakeven_win_rate_pct=round(100 * sum(s["entry_price"] + FEE_RATE * s["entry_price"] * (1 - s["entry_price"]) for s in S) / len(S), 2) if S else "",
                                all_signals_no_caps=agg["all_n"], all_signals_wins=agg["all_wins"],
                                all_signals_win_rate_pct=round(100 * agg["all_wins"] / agg["all_n"], 2) if agg["all_n"] else "",
                                all_signals_net_usd=round(agg["all_net"], 2),
                                days_stopped_by_20usd_rule=len(stopped_days), skipped_by_daily_stop=agg["skipped_stop"],
                                skipped_by_hourly_cap=agg["skipped_rate"], signals_without_label=agg["unlabeled"]))
            for k, v in sorted(sess.items()):
                sessions.append(dict(strategy=strat, label_source=nice, session=k, trades=v["n"], wins=v["wins"],
                                     win_rate_pct=round(100 * v["wins"] / v["n"], 2) if v["n"] else "", net_usd_fee_adjusted=round(v["net"], 2)))
    return scored, summary, sessions

# ------------------------------------------------------------------ stage 9: output
def wcsv(path, rows, cols=None):
    cols = cols or (list(rows[0].keys()) if rows else ["empty"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})

def md_table(rows, cols, heads=None):
    if not rows: return "_(no rows)_\n"
    h = heads or cols
    s = "| " + " | ".join(h) + " |\n|" + "|".join("---" for _ in h) + "|\n"
    for r in rows: s += "| " + " | ".join(str("" if r.get(c) is None else r.get(c)) for c in cols) + " |\n"
    return s

def other_strategies():
    out = []
    for name, p, keys in (("S3 fee-farm maker (DRY)", "s3_feefarm/s3_stats.json", ("n", "quotes", "fills", "pairs", "pnl")),
                          ("S46 cascade + coin-flip (DRY)", "s46_coinflip_cascade/s46_stats.json", None),
                          ("S10 vol-event box (DRY)", "s10_volbox/s10_stats.json", ("n", "maker_quotes", "maker_fills", "taker_pairs", "pairs_completed", "pnl"))):
        try:
            s = json.load(open(os.path.join(HOME, p)))
            if keys is None:
                d = {f"s6_{k}": s["s6"].get(k) for k in ("quotes", "fills", "pairs")}; d.update({f"s4_{k}": s["s4"].get(k) for k in ("triggers", "fills")})
            else: d = {k: s.get(k) for k in keys}
            held = (s.get("pairs", 0) if keys else s["s6"].get("pairs", 0) + s["s4"].get("fills", 0))
            out.append(dict(strategy=name, ledger=json.dumps(d), positions_held_to_resolution=held or 0,
                            label_dependent="no trades held to resolution, so no label can change its result" if not held else "yes"))
        except Exception as e:
            out.append(dict(strategy=name, ledger=f"not readable: {e!r}"[:80], positions_held_to_resolution="", label_dependent=""))
    n_dec = n_cand = 0
    for p in glob.glob(os.path.join(DEF_S2_SESSIONS, "*", "decisions.jsonl")):
        for ln in open(p):
            n_dec += 1; n_cand += '"candidate"' in ln
    out.append(dict(strategy="S2 open-print displacement (recorder)", ledger=json.dumps(dict(decisions=n_dec, candidates=n_cand)),
                    positions_held_to_resolution=n_cand, label_dependent="no candidates produced" if not n_cand else "yes"))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-fetch", dest="fetch", action="store_false")
    ap.add_argument("--rounds", default=DEF_ROUNDS); ap.add_argument("--trader-log", default=DEF_TRADER_LOG)
    ap.add_argument("--s2-sessions", default=DEF_S2_SESSIONS)
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    now = int(time.time()); t_to = (now - 900) // 900 * 900; t_from = t_to - a.days * 86400
    tag = f"backtest from {day_of(t_to)} to {day_of(t_from)}"
    out = os.path.join(OUTBASE, tag); os.makedirs(out, exist_ok=True)
    P = lambda name: os.path.join(out, f"{tag} - {name}")
    log(f"{tag}: window {iso(t_from)} .. {iso(t_to)}")

    rows = build_labels(t_from, t_to, a)
    by_round = {(r["market"], r["round_start_unix"]): r for r in rows}
    LCOLS = ["market", "round_start_unix", "round_start_utc", "round_end_utc", "condition_id", "chain_outcome", "gamma_outcome",
             "pastresults_outcome", "observer_gamma_at_settle", "public_disagrees_with_chain", "pr_open", "pr_close", "pr_gap_bps",
             "obs_coverage_pct", "obs_open_twap", "obs_open_spot", "obs_end_value", "obs_full_round_avg", "obs_tail60_avg",
             "obs_end_gap_bps", "obs_fullavg_gap_bps", "rule_end_value", "rule_full_avg", "rule_tail60", "rule_end_vs_spot_open"]
    wcsv(P("labels per round.csv"), rows, LCOLS)
    audit = rule_audit(rows); wcsv(P("label and rule accuracy vs chain by gap.csv"), audit)
    ttab, tdet, tmatch = tick_study(rows, a, t_from)
    wcsv(P("boundary tick study - accuracy by tick offset.csv"), ttab); wcsv(P("boundary tick study - rounds.csv"), tdet)
    disag = [r for r in rows if r["public_disagrees_with_chain"]]
    wcsv(P("label disagreement cases (public vs chain).csv"), disag, LCOLS)

    # Only the trader ledger is scored. The observer rows (signals_s1_harness) store the UP token ask
    # at round END (values like 0.001 and 1.00), not our side's price at decision time: scoring them
    # produced a fictitious +$67,243 in the first run. They are counted, never priced.
    signals = signals_s1_trader(a.trader_log, t_from, t_to)
    harness_rows = signals_s1_harness(rows)
    scored, summary, sessions = score(signals, by_round)
    wcsv(P("signals scored.csv"), scored); wcsv(P("summary by strategy and label source.csv"), summary)
    wcsv(P("session split.csv"), sessions)
    others = other_strategies()
    others.insert(0, dict(strategy="S1 harness rule (observer rows)", ledger=json.dumps(dict(rows_with_a_signal=len(harness_rows))),
                          positions_held_to_resolution="", label_dependent="not scored: the stored ask is the Up token ask at round end, not an entry price"))
    wcsv(P("other strategies - trade counts.csv"), others)
    # signals whose label differs between chain and a public source
    flips = []
    for s in signals:
        r = by_round.get((s["market"], s["round_start_unix"]), {})
        if r.get("chain_outcome") in ("up", "down"):
            for col, nm in (("pastresults_outcome", "past-results"), ("gamma_outcome", "gamma"), (None, "label the bot used")):
                lab = s["label_seen_by_bot"] if col is None else r.get(col, "")
                if lab in ("up", "down") and lab != r["chain_outcome"]:
                    g, _, n_true = trade_pnl(s["entry_price"], r["chain_outcome"] == s["side"]); _, _, n_pub = trade_pnl(s["entry_price"], lab == s["side"])
                    flips.append(dict(strategy=s["strategy"], market=s["market"], round_start_utc=iso(s["round_start_unix"]), side=s["side"],
                                      entry_price=s["entry_price"], chain_outcome=r["chain_outcome"], wrong_source=nm, wrong_label=lab,
                                      pr_gap_bps=r.get("pr_gap_bps"), obs_end_gap_bps=r.get("obs_end_gap_bps"),
                                      net_usd_true=round(n_true, 2), net_usd_if_public_label=round(n_pub, 2),
                                      pnl_error_usd=round(n_pub - n_true, 2)))
    wcsv(P("signals mis-scored by a public label.csv"), flips)

    # ---------------- REPORT
    cov = Counter();
    for r in rows:
        cov["rounds"] += 1; cov["chain"] += r["chain_outcome"] in ("up", "down"); cov["gamma"] += r["gamma_outcome"] in ("up", "down")
        cov["pr"] += r["pastresults_outcome"] in ("up", "down"); cov["obs"] += bool(r["observer_gamma_at_settle"])
        cov["pending"] += r["chain_outcome"] == "pending"; cov["noid"] += not r["condition_id"]
    A = lambda src, m="both": [x for x in audit if x["label_source"].startswith(src) and x["market"] == m]
    L = [f"# {tag} — S1 and label accuracy\n",
         f"Generated {iso(now)} on `{os.uname().nodename}`. Window {iso(t_from)} → {iso(t_to)} (UTC), BTC 5m + 15m on polymarket.com.",
         "Read-only: no keys, no orders. Ground truth = Polygon CTF `payoutNumerators` (index 0 = Up).\n",
         "## 1. Coverage\n",
         md_table([dict(k="rounds on the grid", v=cov["rounds"]), dict(k="chain-verified (clean 1/0 payout)", v=cov["chain"]),
                   dict(k="chain pending / no condition id", v=f"{cov['pending']} / {cov['noid']}"),
                   dict(k="gamma outcome available", v=cov["gamma"]), dict(k="past-results outcome available", v=cov["pr"]),
                   dict(k="observed by our own S1 harness", v=cov["obs"])], ["k", "v"], ["item", "count"]),
         "\n## 2. How wrong is each label source? (vs chain, both markets)\n",
         "Gap = distance between the round's boundary values in basis points. Small gap = razor round.\n"]
    for src in ("public: past-results", "public: gamma", "production: gamma label"):
        L += [f"**{A(src)[0]['label_source'] if A(src) else src}**\n", md_table(A(src), ["gap_bucket", "rounds", "wrong_vs_chain", "wrong_pct"]), ""]
    L += ["## 3. Which settlement rule reproduces the chain? (our own recorded values, full-coverage rounds)\n"]
    for src in ("rule: TWAP60 end value >= open TWAP", "rule: full-round", "rule: last-60s", "rule: TWAP60 end value >= open SPOT"):
        if A(src): L += [f"**{A(src)[0]['label_source']}**\n", md_table(A(src), ["gap_bucket", "rounds", "wrong_vs_chain", "accuracy_pct"]), ""]
    L += ["## 4. Boundary-tick study (1 Hz ticks recorded by the S2 collector)\n",
          f"Rounds with complete ticks at both boundaries: **{tmatch.get('rounds', 0)}**.\n",
          "Which of our ticks equals past-results' `openPrice` / `closePrice` (within half a cent):\n",
          md_table([dict(k=k, o=tmatch.get("open", {}).get(k, 0), c=tmatch.get("close", {}).get(k, 0))
                    for k in sorted(set(tmatch.get("open", {})) | set(tmatch.get("close", {})))], ["k", "o", "c"],
                   ["our tick", "matches openPrice", "matches closePrice"]),
          "\nAccuracy vs chain of `TWAP60(end + b) >= TWAP60(start + a)` — best 8 offset pairs per market (all gaps):\n"]
    for m in ("5m", "15m"):
        best = sorted([x for x in ttab if x["market"] == m and x["rule"] == "twap60" and x["gap_bucket"] == "ALL"], key=lambda x: -x["accuracy_pct"])[:8]
        exact = [x for x in ttab if x["market"] == m and x["rule"] == "twap60" and x["gap_bucket"] == "ALL" and x["open_tick_offset_s"] == 0 and x["end_tick_offset_s"] == 0]
        L += [f"**{m}** (exact boundary ticks 0/0: {exact[0]['correct_vs_chain']}/{exact[0]['rounds']} = {exact[0]['accuracy_pct']} %)\n" if exact else f"**{m}**\n",
              md_table(best, ["open_tick_offset_s", "end_tick_offset_s", "rounds", "correct_vs_chain", "accuracy_pct"]), ""]
    L += ["## 5. Strategy results under each label source\n",
          f"Rules applied: ${TRADE_USD:.0f} per trade at the recorded ask, one trade per round, at most {MAX_PER_HOUR} per hour, "
          f"${DAILY_STOP:.0f} daily stop per UTC day, taker fee {FEE_RATE}·p·(1−p) per share. Entries are the asks the bot actually saw.\n",
          md_table(summary, ["strategy", "label_source", "signals", "trades_taken", "wins", "win_rate_pct", "breakeven_win_rate_pct",
                             "fees_usd", "net_usd_fee_adjusted", "net_per_trade_usd", "days_stopped_by_20usd_rule"]),
          "", "Same signals with NO risk caps (every signal taken, fees included):", "",
          md_table(summary, ["strategy", "label_source", "all_signals_no_caps", "all_signals_wins", "all_signals_win_rate_pct", "all_signals_net_usd"]),
          "\n### Accuracy delta (a) chain vs (b) public\n"]
    for strat in sorted({s["strategy"] for s in summary}):
        g = {s["label_source"][:4]: s for s in summary if s["strategy"] == strat}
        a_, rows_d = g.get("(a) "), []
        for k, nm in (("(b1)", "past-results"), ("(b2)", "gamma"), ("(c) ", "bot's own label")):
            b_ = g.get(k)
            if a_ and b_ and a_["trades_taken"] and b_["trades_taken"]:
                rows_d.append(dict(vs=nm, wr=round(b_["win_rate_pct"] - a_["win_rate_pct"], 2), pnl=round(b_["net_usd_fee_adjusted"] - a_["net_usd_fee_adjusted"], 2),
                                   n=sum(1 for f in flips if f["strategy"] == strat and f["wrong_source"].startswith(nm[:5]))))
        L += [f"**{strat}**\n", md_table(rows_d, ["vs", "wr", "pnl", "n"], ["public source", "win-rate error (pp)", "net P&L error (USD)", "signals mis-labeled"]), ""]
    L += ["### Specific boundary-round cases where a label disagreed with the chain on one of our signals\n",
          md_table(flips[:60], ["strategy", "market", "round_start_utc", "side", "entry_price", "chain_outcome", "wrong_source", "wrong_label",
                                "pr_gap_bps", "net_usd_true", "net_usd_if_public_label"]),
          f"\n({len(flips)} rows in total, full list in the CSV.)\n",
          "## 6. Per-session split (chain labels)\n",
          md_table([s for s in sessions if s["label_source"].startswith("(a)")], ["strategy", "session", "trades", "wins", "win_rate_pct", "net_usd_fee_adjusted"]),
          "\nSessions: ASIA 00:00–12:00 UTC, US 13:30–21:00 UTC, OFF-HOURS the rest of weekdays, WEEKEND Saturday and Sunday (same blocks the S8 router uses).\n",
          "## 7. Other repo strategies\n", md_table(others, ["strategy", "ledger", "positions_held_to_resolution", "label_dependent"]),
          "\n## 8. Files\n"] + [f"- `{os.path.basename(p)}`" for p in sorted(glob.glob(os.path.join(out, "*.csv")))]
    open(P("REPORT.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(dict(tag=tag, generated=now, window=[t_from, t_to], coverage=dict(cov), summary=summary, tick_match=tmatch,
                   public_disagreements=len(disag), signals_mislabeled=len(flips)), open(os.path.join(out, "summary.json"), "w"), indent=1)
    json.dump(dict(tag=tag, out=out, generated=now), open(os.path.join(OUTBASE, "latest.json"), "w"))
    log(f"done -> {out}")
    print(open(P("REPORT.md"), encoding="utf-8").read())

if __name__ == "__main__":
    main()
