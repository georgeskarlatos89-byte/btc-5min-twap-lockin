#!/usr/bin/env python3
"""
nws_watch.py — NWS forecast-change watcher + weather bucket-sum scanner (productized).

MEASURE-ONLY. Public api.weather.gov + the anonymous Polymarket US market-data websocket.
No keys, no orders. ~3 NWS requests per minute, one websocket connection.

Reference: workspace app_lab/nws_watch.py. Changes (2026-09-27):
  * forecast high = the DAYTIME period whose local date is the market day (the old parser
    looked for a period literally named "Today" and returned nothing for 3 of 5 cities)
  * station code + grid come from the market's own rule text ("Central Park (KNYC)") and the
    NWS station record, not from hand-typed coordinates
  * ONE persistent websocket on all buckets, every frame kept with a ns receive clock, so the
    reaction lag is measured against the TRUE NWS updateTime (the old version polled every
    30 min and started its clock at detection time)
  * bucket_sums.csv has a proper header (old: 6 names, 7 values per row)
  * verdicts on EXECUTABLE ask-sums only; mid-sums are recorded, never trusted
  * observations give a running daily high: a bucket entirely below it cannot win

Outputs (us_measure/weather/):
  weather-frames-YYYYMMDD.jsonl.gz   every bucket quote frame + ns receive clock
  bucket_quotes-YYYYMMDD.csv         1 row per bucket per minute
  bucket_sums.csv                    1 row per city/day every 5 minutes
  nws_forecasts.csv                  every forecast state seen (update time, highs)
  observations.csv                   station observations + running high
  lag_table.csv                      1 row per forecast update: market reaction
  rules.csv                          station + rule text per city/day (audit trail)
"""
import asyncio, json, os, re, sys, time, urllib.request, uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import labcommon
from labcommon import GzDaily, append_csv, heartbeat, iso, day

OUT = os.path.join(HERE, "weather"); os.makedirs(OUT, exist_ok=True)
KILL = os.path.join(HERE, "KILL")
STATE_F = os.path.join(OUT, "state.json")
WS = "wss://gateway-ws-markets.polymarket.us/v1/ws/subscriptions"
UA = {"User-Agent": "polymarket-weather-research (measure-only, low rate)", "Accept": "application/geo+json"}
CITIES = {"nyc": ("nychigh", "America/New_York"), "mia": ("miahigh", "America/New_York"),
          "sfo": ("sfohigh", "America/Los_Angeles"), "lax": ("laxhigh", "America/Los_Angeles"),
          "mdw": ("mdwhigh", "America/Chicago")}
FORECAST_EVERY, OBS_EVERY, SUMS_EVERY, QUOTES_EVERY, DISCOVER_EVERY = 120, 600, 300, 60, 1800
BUY_ARB_BELOW, OVERROUND_ABOVE = 0.985, 1.05

SUM_COLS = ["ts_unix", "ts_utc", "city", "market_day", "n_buckets", "n_with_ask", "n_with_bid", "mid_sum", "ask_sum", "bid_sum",
            "verdict", "cheapest_full_set_cost", "oldest_quote_age_s"]
QUOTE_COLS = ["ts_unix", "city", "market_day", "bucket_slug", "bucket_label", "bucket_lo_f", "bucket_hi_f", "up_px", "best_bid", "best_ask",
              "bid_depth", "ask_depth", "state", "quote_age_s"]
FC_COLS = ["detected_unix", "detected_utc", "city", "station", "nws_update_time", "detection_delay_s", "market_day",
           "forecast_high_f", "previous_high_f", "high_changed", "tomorrow_high_f", "period_name", "short_forecast"]
OBS_COLS = ["ts_unix", "city", "station", "obs_time", "temp_f", "local_day", "running_high_f", "running_high_complete"]
LAG_COLS = ["city", "market_day", "nws_update_time", "detected_utc", "detection_delay_s", "previous_high_f", "new_high_f", "high_delta_f",
            "favourite_bucket_before", "favourite_px_before", "new_high_bucket", "new_high_bucket_px_before",
            "first_quote_change_after_update_s", "first_changed_bucket", "new_high_bucket_px_t10", "new_high_bucket_px_t60",
            "new_high_bucket_px_t300", "max_abs_px_move_300s", "frames_in_300s"]
RULE_COLS = ["ts_utc", "city", "market_day", "event_slug", "station", "n_buckets", "rule_text"]

def log(*a): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z]", *a, flush=True)
def val(x):
    if x is None: return None
    try: return float(x["value"]) if isinstance(x, dict) else float(x)
    except Exception: return None
def get_json(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())
def rpc(path, body):
    req = urllib.request.Request(f"https://gateway.polymarket.us/{path}", data=json.dumps(body).encode(),
                                 headers={"User-Agent": UA["User-Agent"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())
def load_state():
    try: return json.load(open(STATE_F))
    except Exception: return {}
def save_state(st):
    tmp = STATE_F + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE_F)

def local_day(city, offset=0, ts=None):
    tz = ZoneInfo(CITIES[city][1])
    return (datetime.fromtimestamp(ts or time.time(), tz) + timedelta(days=offset)).strftime("%Y-%m-%d")

def parse_bucket(title):
    """'64 or below' -> (None, 64); '65 to 66' -> (65, 66); '73 or above' -> (73, None)"""
    t = (title or "").lower()
    m = re.search(r"(-?\d+)\s+or\s+(below|less|lower)", t)
    if m: return None, int(m.group(1))
    m = re.search(r"(-?\d+)\s+or\s+(above|more|higher)", t)
    if m: return int(m.group(1)), None
    m = re.search(r"(-?\d+)\s*(?:to|-|–)\s*(-?\d+)", t)
    if m: return int(m.group(1)), int(m.group(2))
    return None, None

BUCKETS = {}        # slug -> dict(city, day, label, lo, hi)
QUOTES = {}         # slug -> dict(px,bid,ask,bd,ad,state,ts)
HISTORY = {}        # slug -> list[(ts, px, bid, ask)] (last 2 h)
STATS = dict(frames=0, last_frame=0.0, ws_connects=0, ws_stalls=0, forecast_events=0, nws_errors=0, started=time.time(), arb_flags=0)
FRAMES = GzDaily(OUT, "weather-frames", ".jsonl")

def discover(st):
    """today's + tomorrow's buckets per city from the venue's own event objects (1 request each)."""
    found = 0
    for city, (code, _) in CITIES.items():
        for off in (0, 1):
            d = local_day(city, off); slug = f"temp-{code}-{d}"
            try:
                ev = rpc("gateway.events.v1.EventsService/GetEventBySlug", {"slug": slug}).get("event", {})
            except Exception as e:
                if off == 0: log(f"discover {slug}: {e.__class__.__name__}")
                continue
            ms = ev.get("markets") or []
            for m in ms:
                lo, hi = parse_bucket(m.get("title"))
                BUCKETS[m["slug"]] = dict(city=city, day=d, label=m.get("title"), lo=lo, hi=hi); found += 1
            if ms:
                desc = (ms[0].get("description") or "").strip()
                stn = re.search(r"\(([A-Z]{4})\)", desc)
                key = f"rule:{city}:{d}"
                if stn and st.get("station", {}).get(city) != stn.group(1):
                    old = st.get("station", {}).get(city)
                    st.setdefault("station", {})[city] = stn.group(1); st.pop(f"grid:{city}", None)
                    if old: log(f"STATION CHANGE {city}: {old} -> {stn.group(1)}")
                if key not in st.get("rules_logged", []):
                    st.setdefault("rules_logged", []).append(key); st["rules_logged"] = st["rules_logged"][-40:]
                    append_csv(os.path.join(OUT, "rules.csv"), RULE_COLS,
                               dict(ts_utc=iso(), city=city, market_day=d, event_slug=slug, station=stn.group(1) if stn else "",
                                    n_buckets=len(ms), rule_text=re.sub(r"\s+", " ", desc)[:600]))
    today = {local_day(c, o) for c in CITIES for o in (0, 1)} | {local_day(c, -1) for c in CITIES}
    for s in [s for s, b in BUCKETS.items() if b["day"] not in today]: BUCKETS.pop(s, None); QUOTES.pop(s, None); HISTORY.pop(s, None)
    save_state(st)
    return found

def grid_for(city, st):
    """forecast URL for the market's OWN station (station -> coordinates -> NWS grid point)."""
    k = f"grid:{city}"
    if k in st: return st[k]
    stn = st.get("station", {}).get(city)
    if not stn: return None
    s = get_json(f"https://api.weather.gov/stations/{stn}")
    lon, lat = s["geometry"]["coordinates"]
    p = get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    st[k] = dict(forecast=p["properties"]["forecast"], lat=lat, lon=lon, station=stn); save_state(st)
    return st[k]

def forecast_state(city, st):
    g = grid_for(city, st)
    if not g: return None
    fc = get_json(g["forecast"])["properties"]
    highs = {}
    for p in fc.get("periods", []):
        if p.get("isDaytime") and p.get("temperature") is not None:
            d = p["startTime"][:10]                       # local date of the daytime period
            t = p["temperature"] if p.get("temperatureUnit", "F") == "F" else round(p["temperature"] * 9 / 5 + 32)
            highs.setdefault(d, dict(high=t, name=p.get("name"), short=p.get("shortForecast")))
    return dict(update=fc.get("updateTime") or fc.get("updated"), highs=highs)

def bucket_for(city, d, temp):
    for s, b in BUCKETS.items():
        if b["city"] == city and b["day"] == d and temp is not None:
            if (b["lo"] is None or temp >= b["lo"]) and (b["hi"] is None or temp <= b["hi"]): return s
    return None

def px_at(slug, ts):
    """last known px at/before ts"""
    best = None
    for (t, px, *_x) in HISTORY.get(slug, []):
        if t <= ts: best = px
        else: break
    return best

async def lag_followup(city, d, upd_iso, det_ts, old_high, new_high):
    """5 minutes after detection, measure how the buckets moved relative to the TRUE update time."""
    await asyncio.sleep(305)
    try: upd_ts = datetime.fromisoformat(upd_iso.replace("Z", "+00:00")).timestamp()
    except Exception: upd_ts = det_ts
    slugs = [s for s, b in BUCKETS.items() if b["city"] == city and b["day"] == d]
    before = {s: px_at(s, upd_ts) for s in slugs}
    fav = max((s for s in slugs if before[s] is not None), key=lambda s: before[s], default=None)
    nb = bucket_for(city, d, new_high)
    first, first_s, frames, maxmove = None, None, 0, 0.0
    for s in slugs:
        for (t, px, *_x) in HISTORY.get(s, []):
            if upd_ts < t <= det_ts + 300:
                frames += 1
                if px is not None and before[s] is not None:
                    mv = abs(px - before[s]); maxmove = max(maxmove, mv)
                    if mv >= 0.005 and (first is None or t < first): first, first_s = t, s
    append_csv(os.path.join(OUT, "lag_table.csv"), LAG_COLS,
               dict(city=city, market_day=d, nws_update_time=upd_iso, detected_utc=iso(det_ts), detection_delay_s=round(det_ts - upd_ts, 1),
                    previous_high_f=old_high, new_high_f=new_high, high_delta_f=None if (old_high is None or new_high is None) else new_high - old_high,
                    favourite_bucket_before=BUCKETS.get(fav, {}).get("label"), favourite_px_before=before.get(fav),
                    new_high_bucket=BUCKETS.get(nb, {}).get("label"), new_high_bucket_px_before=before.get(nb),
                    first_quote_change_after_update_s=None if first is None else round(first - upd_ts, 1),
                    first_changed_bucket=BUCKETS.get(first_s, {}).get("label"),
                    new_high_bucket_px_t10=px_at(nb, det_ts + 10) if nb else None, new_high_bucket_px_t60=px_at(nb, det_ts + 60) if nb else None,
                    new_high_bucket_px_t300=px_at(nb, det_ts + 300) if nb else None, max_abs_px_move_300s=round(maxmove, 4), frames_in_300s=frames))

async def nws_loop(st):
    loop = asyncio.get_running_loop()
    while True:
        for city in CITIES:
            try:
                fs = await loop.run_in_executor(None, forecast_state, city, st)
            except Exception as e:
                STATS["nws_errors"] += 1
                if STATS["nws_errors"] in (5, 50): log(f"NWS forecast {city} failing x{STATS['nws_errors']}: {e.__class__.__name__}")
                await asyncio.sleep(2); continue
            if not fs or not fs["update"]: continue
            d0, d1 = local_day(city, 0), local_day(city, 1)
            h0, h1 = fs["highs"].get(d0, {}), fs["highs"].get(d1, {})
            prev = st.get("forecast", {}).get(city, {})
            if fs["update"] != prev.get("update"):
                det = time.time()
                try: delay = round(det - datetime.fromisoformat(fs["update"].replace("Z", "+00:00")).timestamp(), 1)
                except Exception: delay = None
                old_high = prev.get("highs", {}).get(d0)
                changed = old_high is not None and h0.get("high") is not None and old_high != h0.get("high")
                append_csv(os.path.join(OUT, "nws_forecasts.csv"), FC_COLS,
                           dict(detected_unix=int(det), detected_utc=iso(det), city=city, station=st.get("station", {}).get(city),
                                nws_update_time=fs["update"], detection_delay_s=delay, market_day=d0, forecast_high_f=h0.get("high"),
                                previous_high_f=old_high, high_changed=int(changed), tomorrow_high_f=h1.get("high"),
                                period_name=h0.get("name") or h1.get("name"), short_forecast=(h0.get("short") or h1.get("short") or "")[:80]))
                if prev.get("update"):                     # not the first sighting after start-up
                    STATS["forecast_events"] += 1
                    asyncio.create_task(lag_followup(city, d0, fs["update"], det, old_high, h0.get("high")))
                st.setdefault("forecast", {})[city] = dict(update=fs["update"], highs={d0: h0.get("high"), d1: h1.get("high")})
                save_state(st)
            await asyncio.sleep(1.0)                       # spread the 5 cities, never burst
        await asyncio.sleep(FORECAST_EVERY)

async def obs_loop(st):
    loop = asyncio.get_running_loop()
    while True:
        for city in CITIES:
            stn = st.get("station", {}).get(city)
            if not stn: continue
            try:
                o = (await loop.run_in_executor(None, get_json, f"https://api.weather.gov/stations/{stn}/observations/latest"))["properties"]
                c = (o.get("temperature") or {}).get("value")
                if c is None: continue
                f = round(c * 9 / 5 + 32, 1); ot = o.get("timestamp")
                d = local_day(city, 0, datetime.fromisoformat(ot.replace("Z", "+00:00")).timestamp())
                rh = st.setdefault("running_high", {}).setdefault(city, {})
                if rh.get("day") != d:
                    tz = ZoneInfo(CITIES[city][1]); nowl = datetime.now(tz)
                    rh.clear(); rh.update(day=d, high=f, complete=(nowl.hour == 0 and (time.time() - STATS["started"]) > 0))
                if ot != rh.get("last_obs"):
                    rh["last_obs"] = ot; rh["high"] = max(rh.get("high", f), f)
                    append_csv(os.path.join(OUT, "observations.csv"), OBS_COLS,
                               dict(ts_unix=int(time.time()), city=city, station=stn, obs_time=ot, temp_f=f, local_day=d,
                                    running_high_f=rh["high"], running_high_complete=int(bool(rh.get("complete")))))
                    save_state(st)
            except Exception as e:
                STATS["nws_errors"] += 1
            await asyncio.sleep(1.0)
        await asyncio.sleep(OBS_EVERY)

def sums_pass(st):
    t = time.time()
    groups = {}
    for s, b in BUCKETS.items(): groups.setdefault((b["city"], b["day"]), []).append(s)
    for (city, d), slugs in sorted(groups.items()):
        if d < local_day(city, 0): continue
        q = [QUOTES.get(s) for s in slugs]
        have = [x for x in q if x]
        if not have: continue
        asks = [x["ask"] for x in have if x.get("ask") is not None]; bids = [x["bid"] for x in have if x.get("bid") is not None]
        mids = [x["px"] for x in have if x.get("px") is not None]
        n = len(slugs); full_ask = len(asks) == n
        ask_sum = round(sum(asks), 4) if asks else None
        if len(have) < n: verdict = "INCOMPLETE-QUOTES"
        elif not full_ask: verdict = "NO-FULL-ASK-SET"
        elif ask_sum < BUY_ARB_BELOW: verdict = "BUY-ARB"
        elif ask_sum > OVERROUND_ABOVE: verdict = "OVERROUND-ASK"
        else: verdict = "OK"
        append_csv(os.path.join(OUT, "bucket_sums.csv"), SUM_COLS,
                   dict(ts_unix=int(t), ts_utc=iso(t), city=city, market_day=d, n_buckets=n, n_with_ask=len(asks), n_with_bid=len(bids),
                        mid_sum=round(sum(mids), 4) if mids else None, ask_sum=ask_sum, bid_sum=round(sum(bids), 4) if bids else None,
                        verdict=verdict, cheapest_full_set_cost=ask_sum if full_ask else None,
                        oldest_quote_age_s=round(max(t - x["ts"] for x in have), 1)))
        if verdict == "BUY-ARB" and d == local_day(city, 0) and max(t - x["ts"] for x in have) < 120:
            STATS["arb_flags"] += 1
            labcommon.alert(f"wx_arb:{city}:{d}", f"🔎 Weather bucket set {city.upper()} {d}: the six asks sum to {ask_sum:.3f} (< {BUY_ARB_BELOW}). "
                            f"Buying every bucket would cost {ask_sum:.3f} for a guaranteed 1.00 before fees and depth. "
                            f"Measurement only, no order placed. Check depth in bucket_quotes.", "warn", "nws_watch")
        # hard floor: a bucket entirely below the running high cannot win
        rh = st.get("running_high", {}).get(city, {})
        if rh.get("day") == d and rh.get("high") is not None:
            for s in slugs:
                b, x = BUCKETS[s], QUOTES.get(s)
                if x and b["hi"] is not None and b["hi"] < math_floor(rh["high"]) and (x.get("bid") or 0) >= 0.05:
                    labcommon.alert(f"wx_floor:{s}", f"🔎 Weather {city.upper()} {d}: bucket '{b['label']}' still has a bid of {x['bid']:.2f} "
                                    f"although the station already recorded {rh['high']}F (a bucket below the running high cannot win). "
                                    f"Measurement only.", "warn", "nws_watch")

def math_floor(x):
    import math; return math.floor(x)

def quotes_pass():
    t = time.time()
    for s, b in sorted(BUCKETS.items()):
        x = QUOTES.get(s)
        if not x: continue
        append_csv(os.path.join(OUT, f"bucket_quotes-{day()}.csv"), QUOTE_COLS,
                   dict(ts_unix=int(t), city=b["city"], market_day=b["day"], bucket_slug=s, bucket_label=b["label"], bucket_lo_f=b["lo"],
                        bucket_hi_f=b["hi"], up_px=x.get("px"), best_bid=x.get("bid"), best_ask=x.get("ask"), bid_depth=x.get("bd"),
                        ask_depth=x.get("ad"), state=x.get("state"), quote_age_s=round(t - x["ts"], 1)))

async def housekeeping(st):
    loop = asyncio.get_running_loop(); last_q = last_s = last_d = last_hb = 0
    while True:
        t = time.time()
        if t - last_d >= DISCOVER_EVERY or not BUCKETS:
            last_d = t
            try: n = await loop.run_in_executor(None, discover, st); log(f"buckets known: {len(BUCKETS)} (stations {st.get('station')})")
            except Exception as e: log("discover failed:", e.__class__.__name__)
        if t - last_q >= QUOTES_EVERY: last_q = t; quotes_pass()
        if t - last_s >= SUMS_EVERY and QUOTES: last_s = t; sums_pass(st)
        for s, h in HISTORY.items():
            while h and t - h[0][0] > 7200: h.pop(0)
        if t - last_hb >= 30:
            last_hb = t
            heartbeat("nws_watch", buckets=len(BUCKETS), quoted=len(QUOTES), frames_total=STATS["frames"],
                      last_frame_age_s=round(t - STATS["last_frame"], 1) if STATS["last_frame"] else None,
                      forecast_events=STATS["forecast_events"], nws_errors=STATS["nws_errors"], ws_connects=STATS["ws_connects"],
                      stations=st.get("station"), highs={c: (st.get("forecast", {}).get(c, {}).get("highs")) for c in CITIES},
                      running_high={c: st.get("running_high", {}).get(c, {}).get("high") for c in CITIES},
                      arb_flags=STATS["arb_flags"], uptime_s=int(t - STATS["started"]))
        await asyncio.sleep(5)

async def venue():
    import websockets
    backoff = 3
    while True:
        if os.path.exists(KILL) or not BUCKETS: await asyncio.sleep(10); continue
        try:
            async with websockets.connect(WS, open_timeout=10, max_size=2 ** 22) as ws:
                STATS["ws_connects"] += 1; backoff = 3
                subscribed = set(); rid = None; last_any = time.time()
                while True:
                    want = set(BUCKETS)
                    if want != subscribed:
                        if rid: await ws.send(json.dumps({"unsubscribe": {"requestId": rid}}))
                        rid = str(uuid.uuid4()); subscribed = set(want)
                        await ws.send(json.dumps({"subscribe": {"requestId": rid, "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA_LITE",
                                                                "marketSlugs": sorted(want)}}))
                        log(f"subscribed to {len(want)} weather buckets")
                    try: raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        # weather books are slow; silence is normal. Reconnect only after a long one.
                        if time.time() - last_any > 1800:
                            STATS["ws_stalls"] += 1; log("weather feed silent 30 min — reconnecting"); break
                        continue
                    recv = time.time_ns(); last_any = time.time()
                    try: d = json.loads(raw).get("marketDataLite")
                    except Exception: continue
                    if not d or d.get("marketSlug") not in BUCKETS: continue
                    FRAMES.write(json.dumps({"recv_ns": recv, "data": d}, separators=(",", ":")))
                    STATS["frames"] += 1; STATS["last_frame"] = recv / 1e9
                    q = dict(px=val(d.get("currentPx")), bid=val(d.get("bestBid")), ask=val(d.get("bestAsk")), bd=d.get("bidDepth"),
                             ad=d.get("askDepth"), state=d.get("state"), ts=recv / 1e9)
                    s = d["marketSlug"]; QUOTES[s] = q
                    HISTORY.setdefault(s, []).append((q["ts"], q["px"], q["bid"], q["ask"]))
        except Exception as e:
            log("weather venue restart:", e.__class__.__name__, str(e)[:100])
        await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

async def main(test=False):
    st = load_state()
    log(f"NWS watcher starting — out={OUT} (measure-only)")
    n = discover(st); log(f"discovered {n} bucket markets; stations {st.get('station')}")
    tasks = [asyncio.create_task(venue()), asyncio.create_task(housekeeping(st)), asyncio.create_task(nws_loop(st)), asyncio.create_task(obs_loop(st))]
    if test:
        await asyncio.sleep(40)
        quotes_pass(); sums_pass(st)
        log("test: frames", STATS["frames"], "quoted buckets", len(QUOTES), "forecast", st.get("forecast"), "running_high", st.get("running_high"))
        for t in tasks: t.cancel()
        FRAMES.close(); return
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in done: log("FATAL task ended:", repr(t.exception() if not t.cancelled() else "cancelled"))
    FRAMES.close(); sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main(test="--test" in sys.argv))
