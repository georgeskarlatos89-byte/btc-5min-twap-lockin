#!/usr/bin/env python3
"""
us_round_recorder.py — US-venue BTC Up/Down round recorder + BRTI-proxy (productized).

MEASURE-ONLY. Anonymous public websocket + public exchange market data. No keys, no orders,
no authenticated surface. One websocket connection, a handful of REST calls per minute.

Reference implementation: workspace app_lab/us_round_recorder.py. Changes (2026-09-27):
  * ONE persistent venue connection; rounds are subscribed on the 15-minute grid (the old
    code re-subscribed every 900 s from start-up, so it missed the start of every round)
  * the open reference is ONLY the proxy TWAP60 measured at the round's own start boundary;
    a round first seen mid-way is marked NO-OPEN-REF and excluded from validation
  * settlement: WS settlementPx first, then GetMarketSettlement as second witness
    (proto3 omits a zero: HTTP 200 without the field means 0 = Down)
  * raw frames go to a daily gzip stream (raw was ~600 MB/day); a compact per-second book
    state CSV feeds the mispricing monitors
  * stall watchdog, heartbeat file, named CSV columns everywhere

Oracle under audit (market rules text): "in the last minute before each of those two times,
60 BRTI prices are collected. The official value is the simple average, rounded to 2 decimal
places"; Up if end >= start. BRTI = CF Benchmarks composite of constituent order books.
The proxy uses top-of-book mids of 4 constituents (staleness > 30 s and > 25 % deviation
excluded). Its agreement with the venue's own settlement IS the measurement.

Outputs (us_measure/us_rounds/):
  frames-YYYYMMDD.jsonl.gz      every venue lite frame + local ns receive clock
  bookstate-YYYYMMDD.csv        1 row per second per live round (quotes, depth, proxy)
  brti-proxy-YYYYMMDD.csv       1 row per second: proxy price + per-exchange mids
  boundaries.csv                proxy TWAP60 at every 15-minute boundary
  rounds.csv                    one row per closed round, match = OK / DIVERGENCE / ...
"""
import asyncio, json, os, sys, time, urllib.error, urllib.request, uuid
from collections import deque
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import labcommon
from labcommon import GzDaily, append_csv, heartbeat, iso, day

OUT = os.path.join(HERE, "us_rounds"); os.makedirs(OUT, exist_ok=True)
KILL = os.path.join(HERE, "KILL")
WS = "wss://gateway-ws-markets.polymarket.us/v1/ws/subscriptions"
UA = {"User-Agent": "Mozilla/5.0 (measurement-only research)"}
HORIZONS = {900: "15m", 3600: "1h"}
STALL_S, SETTLE_POLL_EVERY, SETTLE_POLL_MAX = 60, 20, 30
EXCHANGES = ("coinbase", "kraken", "bitstamp", "gemini")

ROUND_COLS = ["round_slug", "horizon", "start_unix", "end_unix", "start_utc", "end_utc", "frames", "first_frame_utc",
              "seen_from_start", "last_up_px", "last_best_bid", "last_best_ask", "one_sided_at_close",
              "one_sided_seconds_last_minute", "proxy_twap60_open", "proxy_samples_open", "proxy_books_open",
              "proxy_twap60_close", "proxy_samples_close", "proxy_books_close", "proxy_gap_bps", "proxy_up",
              "settlement_px", "settlement_src", "settlement_delay_s", "http_settlement", "venue_up", "match"]
BOOK_COLS = ["ts_unix", "round_slug", "horizon", "secs_to_end", "state", "up_px", "last_trade_px", "best_bid", "best_ask",
             "bid_depth", "ask_depth", "one_sided", "shares_traded", "open_interest", "frame_age_s", "proxy_px",
             "proxy_open_twap60", "proxy_gap_bps_vs_open"]
PROXY_COLS = ["ts_unix", "proxy_px", "n_books"] + [f"mid_{e}" for e in EXCHANGES] + [f"age_{e}" for e in EXCHANGES]
BOUND_COLS = ["boundary_unix", "boundary_utc", "proxy_twap60", "samples", "min_books", "proxy_twap60_2dp"]

def log(*a): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z]", *a, flush=True)
def now_ns(): return time.time_ns()
def val(x):
    """{'value': '0.21', 'currency': 'USD'} -> 0.21 ; None stays None"""
    if x is None: return None
    try: return float(x["value"]) if isinstance(x, dict) else float(x)
    except Exception: return None

def slug_for(start, secs):
    return f"cpc-btc-updown-{HORIZONS[secs]}-{datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%d-%H%M')}z"

def rpc(path, body=None, host="gateway.polymarket.us", timeout=15):
    req = urllib.request.Request(f"https://{host}/{path}", data=json.dumps(body or {}).encode(),
                                 headers=dict(UA, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def http_get(url, timeout=8):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())

# ------------------------------------------------------------------ constituent books
class Books:
    def __init__(self): self.best = {}; self.err = {e: 0 for e in EXCHANGES}
    def put(self, ex, bid, ask):
        if bid and ask and 0 < bid < ask * 1.5: self.best[ex] = dict(bid=bid, ask=ask, ts=time.time())
    def mids(self):
        t = time.time(); out = {}
        for ex, b in self.best.items():
            if t - b["ts"] <= 30: out[ex] = ((b["bid"] + b["ask"]) / 2, t - b["ts"])     # staleness rule
        return out
    def proxy(self):
        m = self.mids()
        if not m: return None, 0, m
        vals = sorted(v[0] for v in m.values()); med = vals[len(vals) // 2]
        keep = [v for v in vals if abs(v / med - 1) <= 0.25] or vals                       # deviation rule
        return sum(keep) / len(keep), len(keep), m

BOOKS = Books()
SAMPLES = deque(maxlen=200)      # (ts, proxy_px, n_books)
TWAPS = {}                       # boundary_unix -> dict(twap, n, min_books)
STATS = dict(frames=0, frames_min=deque(maxlen=600), last_frame=0.0, ws_connects=0, ws_stalls=0, rounds_closed=0,
             divergences=0, started=time.time())

async def poll(name, fn, every=1.0):
    loop = asyncio.get_running_loop()
    while True:
        t0 = time.time()
        try:
            bid, ask = await loop.run_in_executor(None, fn)
            BOOKS.put(name, bid, ask); BOOKS.err[name] = 0
        except Exception as e:
            BOOKS.err[name] += 1
            if BOOKS.err[name] in (5, 60, 600): log(f"{name} poll failing x{BOOKS.err[name]}: {e.__class__.__name__}")
            if BOOKS.err[name] > 30: await asyncio.sleep(5)              # back off, never hammer
        await asyncio.sleep(max(0.05, every - (time.time() - t0)))

def q_coinbase():
    b = http_get("https://api.exchange.coinbase.com/products/BTC-USD/book?level=1")
    return float(b["bids"][0][0]), float(b["asks"][0][0])
def q_kraken():
    d = http_get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")["result"]; k = next(iter(d))
    return float(d[k]["b"][0]), float(d[k]["a"][0])

async def feed_bitstamp():
    import websockets
    while True:
        try:
            async with websockets.connect("wss://ws.bitstamp.net", open_timeout=10) as ws:
                await ws.send(json.dumps({"event": "bts:subscribe", "data": {"channel": "order_book_btcusd"}}))
                while True:
                    j = json.loads(await asyncio.wait_for(ws.recv(), 45))
                    d = j.get("data") or {}
                    if j.get("event") == "data" and d.get("bids") and d.get("asks"):
                        BOOKS.put("bitstamp", float(d["bids"][0][0]), float(d["asks"][0][0]))
        except Exception as e:
            log("bitstamp feed restart:", e.__class__.__name__); await asyncio.sleep(3)

async def feed_gemini():
    import websockets
    while True:
        try:
            async with websockets.connect("wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true", open_timeout=10) as ws:
                bid = ask = None
                while True:
                    j = json.loads(await asyncio.wait_for(ws.recv(), 45))
                    for ev in j.get("events", []):
                        if ev.get("type") != "change": continue
                        if ev.get("side") == "bid": bid = float(ev["price"]) if float(ev.get("remaining", 0) or 0) > 0 else bid
                        elif ev.get("side") == "ask": ask = float(ev["price"]) if float(ev.get("remaining", 0) or 0) > 0 else ask
                    if bid and ask: BOOKS.put("gemini", bid, ask)
        except Exception as e:
            log("gemini feed restart:", e.__class__.__name__); await asyncio.sleep(3)

# ------------------------------------------------------------------ rounds
class Round:
    def __init__(self, start, secs, seen_from_start):
        self.start, self.secs, self.end = start, secs, start + secs
        self.slug = slug_for(start, secs); self.frames = 0; self.first_frame = None
        self.seen_from_start = seen_from_start
        self.last = {}; self.last_frame_ts = 0.0
        self.settled = None; self.settle_recv = None; self.http_settle = None
        self.polls = 0; self.next_poll = 0.0; self.closed = False
        self.one_sided_last_min = 0

ROUNDS = {}          # slug -> Round
FRAMES = GzDaily(OUT, "frames", ".jsonl")

def ensure_rounds(t):
    """rounds that should be tracked now: the live 15m + 1h rounds."""
    fresh = []
    for secs in HORIZONS:
        start = int(t // secs) * secs
        slug = slug_for(start, secs)
        if slug not in ROUNDS:
            ROUNDS[slug] = Round(start, secs, seen_from_start=(t - start) <= 5)
            fresh.append(slug); log("tracking", slug, f"(joined {t - start:.0f}s into the round)")
    return fresh

def close_round(r):
    o, c = TWAPS.get(r.start), TWAPS.get(r.end)
    po, pc = (o or {}).get("twap"), (c or {}).get("twap")
    proxy_up = None if po is None or pc is None else (round(pc, 2) >= round(po, 2))          # 2dp, ties Up
    spx, src = r.settled if r.settled else (None, None)
    venue_up = None if spx is None else spx >= 0.5
    if venue_up is None: match = "NO-SETTLEMENT"
    elif po is None: match = "NO-OPEN-REF"
    elif pc is None: match = "NO-CLOSE-REF"
    elif (o["n"] < 45 or c["n"] < 45): match = "THIN-PROXY"
    else: match = "OK" if venue_up == proxy_up else "DIVERGENCE"
    row = dict(round_slug=r.slug, horizon=HORIZONS[r.secs], start_unix=r.start, end_unix=r.end, start_utc=iso(r.start), end_utc=iso(r.end),
               frames=r.frames, first_frame_utc=iso(r.first_frame) if r.first_frame else "", seen_from_start=int(bool(r.seen_from_start)),
               last_up_px=r.last.get("px"), last_best_bid=r.last.get("bid"), last_best_ask=r.last.get("ask"),
               one_sided_at_close=r.last.get("one_sided"), one_sided_seconds_last_minute=r.one_sided_last_min,
               proxy_twap60_open=None if po is None else round(po, 2), proxy_samples_open=(o or {}).get("n"), proxy_books_open=(o or {}).get("min_books"),
               proxy_twap60_close=None if pc is None else round(pc, 2), proxy_samples_close=(c or {}).get("n"), proxy_books_close=(c or {}).get("min_books"),
               proxy_gap_bps=None if po is None or pc is None else round((pc - po) / po * 1e4, 3),
               proxy_up="" if proxy_up is None else int(proxy_up), settlement_px=spx, settlement_src=src,
               settlement_delay_s=None if r.settle_recv is None else round(r.settle_recv - r.end, 2),
               http_settlement=r.http_settle, venue_up="" if venue_up is None else int(venue_up), match=match)
    append_csv(os.path.join(OUT, "rounds.csv"), ROUND_COLS, row)
    STATS["rounds_closed"] += 1; r.closed = True
    log("round closed:", r.slug, match, f"proxy {po and round(po,2)} -> {pc and round(pc,2)}", f"settle {spx} ({src})")
    if match == "DIVERGENCE":
        STATS["divergences"] += 1
        gap = row["proxy_gap_bps"]
        if gap is not None and abs(gap) >= 2.0:          # a razor round disagreeing is expected proxy noise, not news
            labcommon.alert(f"us_div:{r.slug}", f"⚠ US round {r.slug}: our BRTI proxy says {'Up' if proxy_up else 'Down'} "
                            f"(gap {gap:+.2f} bps, well clear of zero) but the venue settled {'Up' if venue_up else 'Down'}. "
                            f"Either the proxy is off or the settlement deserves a look. Row saved in rounds.csv.", "event", "us_recorder")

def http_settlement(slug):
    """second witness. 200 + field -> value; 200 without field -> 0 (proto3 default, Down); 404 -> not settled"""
    try:
        j = rpc("gateway.market.v1.MarketService/GetMarketSettlement", {"slug": slug})
        return float(j.get("settlement", 0))
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise

async def sampler():
    """1 Hz: proxy sample, boundary TWAPs, bookstate rows, round close-out, heartbeat."""
    loop = asyncio.get_running_loop(); seen_b = set(); last_hb = 0
    while True:
        t = time.time()
        px, n, mids = BOOKS.proxy()
        if px:
            SAMPLES.append((t, px, n))
            append_csv(os.path.join(OUT, f"brti-proxy-{day()}.csv"), PROXY_COLS,
                       dict(ts_unix=round(t, 3), proxy_px=round(px, 2), n_books=n,
                            **{f"mid_{e}": (round(mids[e][0], 2) if e in mids else None) for e in EXCHANGES},
                            **{f"age_{e}": (round(mids[e][1], 2) if e in mids else None) for e in EXCHANGES}))
        b = int(t // 900) * 900
        if b not in seen_b and t - b >= 1.0:
            seen_b.add(b)
            win = [(p, k) for (ts, p, k) in SAMPLES if b - 60 <= ts < b]
            if win:
                tw = sum(p for p, _ in win) / len(win)
                TWAPS[b] = dict(twap=tw, n=len(win), min_books=min(k for _, k in win))
                append_csv(os.path.join(OUT, "boundaries.csv"), BOUND_COLS,
                           dict(boundary_unix=b, boundary_utc=iso(b), proxy_twap60=round(tw, 4), samples=len(win),
                                min_books=TWAPS[b]["min_books"], proxy_twap60_2dp=round(tw, 2)))
                log(f"boundary {iso(b)}: proxy TWAP60 = {tw:.2f} (n={len(win)}, books>={TWAPS[b]['min_books']})")
            for k in [k for k in TWAPS if k < b - 3 * 3600]: TWAPS.pop(k, None)
        # per-second book state for every live round
        for r in list(ROUNDS.values()):
            if r.closed: continue
            if r.last and t < r.end + 5:
                o = TWAPS.get(r.start, {}).get("twap")
                one = r.last.get("one_sided")
                if one and 0 <= r.end - t <= 60: r.one_sided_last_min += 1
                append_csv(os.path.join(OUT, f"bookstate-{day()}.csv"), BOOK_COLS,
                           dict(ts_unix=int(t), round_slug=r.slug, horizon=HORIZONS[r.secs], secs_to_end=int(r.end - t), state=r.last.get("state"),
                                up_px=r.last.get("px"), last_trade_px=r.last.get("ltp"), best_bid=r.last.get("bid"), best_ask=r.last.get("ask"),
                                bid_depth=r.last.get("bd"), ask_depth=r.last.get("ad"), one_sided=int(bool(one)),
                                shares_traded=r.last.get("vol"), open_interest=r.last.get("oi"), frame_age_s=round(t - r.last_frame_ts, 1),
                                proxy_px=None if not px else round(px, 2), proxy_open_twap60=None if o is None else round(o, 2),
                                proxy_gap_bps_vs_open=None if (o is None or not px) else round((px - o) / o * 1e4, 3)))
            if t >= r.end:
                if r.settled is None and t >= r.next_poll and r.polls < SETTLE_POLL_MAX and t - r.end >= 5:
                    r.polls += 1; r.next_poll = t + SETTLE_POLL_EVERY
                    try:
                        v = await loop.run_in_executor(None, http_settlement, r.slug)
                        if v is not None: r.http_settle = v; r.settled = (v, "http"); r.settle_recv = time.time()
                    except Exception as e:
                        log("settlement poll:", e.__class__.__name__)
                if r.settled is not None and t - r.end >= 20 and r.http_settle is None and r.polls < 3:
                    r.polls += 1                                          # cross-check the WS value once over HTTP
                    try: r.http_settle = await loop.run_in_executor(None, http_settlement, r.slug)
                    except Exception: pass
                if (r.settled is not None and t - r.end >= 25) or r.polls >= SETTLE_POLL_MAX or t - r.end > 900:
                    close_round(r)
        for k in [k for k, r in ROUNDS.items() if r.closed and t - r.end > 1800]: ROUNDS.pop(k, None)
        if t - last_hb >= 30:
            last_hb = t
            STATS["frames_min"].append((t, STATS["frames"]))
            f60 = STATS["frames"] - next((c for (ts, c) in STATS["frames_min"] if t - ts <= 65), STATS["frames"])
            heartbeat("us_recorder", frames_total=STATS["frames"], frames_last_min=f60, last_frame_age_s=round(t - STATS["last_frame"], 1) if STATS["last_frame"] else None,
                      books=sorted(BOOKS.mids().keys()), proxy_px=None if not px else round(px, 2), rounds_tracked=[r.slug for r in ROUNDS.values() if not r.closed],
                      rounds_closed=STATS["rounds_closed"], divergences=STATS["divergences"], ws_connects=STATS["ws_connects"], ws_stalls=STATS["ws_stalls"],
                      uptime_s=int(t - STATS["started"]), disk_free_gb=round(labcommon.disk_free_gb(), 1))
        await asyncio.sleep(max(0.05, 1.0 - (time.time() - t)))

async def venue():
    import websockets
    backoff = 3
    while True:
        if os.path.exists(KILL): log("KILL present — venue recorder idle"); await asyncio.sleep(30); continue
        try:
            async with websockets.connect(WS, open_timeout=10, max_size=2 ** 22) as ws:
                STATS["ws_connects"] += 1; backoff = 3; subs = {}                   # slug -> requestId
                log("venue websocket connected (anonymous, market data lite)")
                last_any = time.time()
                while True:
                    t = time.time()
                    ensure_rounds(t)
                    want = [r.slug for r in ROUNDS.values() if not r.closed and t < r.end + 120]
                    for slug in want:
                        if slug not in subs:
                            rid = str(uuid.uuid4()); subs[slug] = rid
                            await ws.send(json.dumps({"subscribe": {"requestId": rid, "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA_LITE",
                                                                    "marketSlugs": [slug]}}))
                    for slug in [s for s in subs if s not in want]:
                        await ws.send(json.dumps({"unsubscribe": {"requestId": subs.pop(slug)}}))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        if time.time() - last_any > STALL_S:
                            STATS["ws_stalls"] += 1; log(f"venue silent for {STALL_S}s — reconnecting (stall watchdog)"); break
                        continue
                    recv = now_ns(); last_any = time.time()
                    try: j = json.loads(raw)
                    except Exception: continue
                    d = j.get("marketDataLite")
                    if not d: continue
                    r = ROUNDS.get(d.get("marketSlug"))
                    if not r: continue
                    FRAMES.write(json.dumps({"recv_ns": recv, "data": d}, separators=(",", ":")))
                    r.frames += 1; STATS["frames"] += 1; STATS["last_frame"] = r.last_frame_ts = recv / 1e9
                    if r.first_frame is None: r.first_frame = recv / 1e9
                    bid, ask = val(d.get("bestBid")), val(d.get("bestAsk"))
                    r.last = dict(px=val(d.get("currentPx")), ltp=val(d.get("lastTradePx")), bid=bid, ask=ask, bd=d.get("bidDepth"), ad=d.get("askDepth"),
                                  one_sided=(bid is None or ask is None), state=d.get("state"), vol=d.get("sharesTraded"), oi=d.get("openInterest"))
                    sp = val(d.get("settlementPx"))
                    if sp is not None and r.settled is None:
                        r.settled = (sp, "ws"); r.settle_recv = recv / 1e9
                        log(r.slug, f"settlementPx via WS: {sp} ({recv / 1e9 - r.end:+.2f}s after the boundary)")
        except Exception as e:
            log("venue session restart:", e.__class__.__name__, str(e)[:120])
        await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

async def main(test=False):
    log(f"US round recorder starting — out={OUT} (measure-only; anonymous public feeds)")
    tasks = [asyncio.create_task(poll("coinbase", q_coinbase)), asyncio.create_task(poll("kraken", q_kraken)),
             asyncio.create_task(feed_bitstamp()), asyncio.create_task(feed_gemini()),
             asyncio.create_task(sampler()), asyncio.create_task(venue())]
    if test:
        await asyncio.sleep(25)
        log("test books:", {k: (round(v[0], 2), round(v[1], 1)) for k, v in BOOKS.mids().items()}, "| frames:", STATS["frames"],
            "| rounds:", [r.slug for r in ROUNDS.values()])
        for t in tasks: t.cancel()
        FRAMES.close(); return
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in done:                                                   # a task must never end; if one does, crash loudly so systemd restarts
        log("FATAL task ended:", repr(t.exception() if not t.cancelled() else "cancelled"))
    FRAMES.close(); sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main(test="--test" in sys.argv))
