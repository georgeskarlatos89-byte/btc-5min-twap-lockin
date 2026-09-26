#!/usr/bin/env python3
"""
S46 — COIN-FLIP HARVESTER (S6) + LIQUIDATION CASCADE CONTINUATION (S4) — PAIRED HARNESS
DRY by default. Incubation bot, gated by code (P5).

PAIRING THESIS:
  S6 farms quiet tape: entertainment takers pay max fee (1.75% at 50c) for a fair coin.
  Quote BOTH Up/Down at 0.49 for first 45s. Pair = 0.98 cost -> 1.00 payout (+2c).
  Single leg held to resolution to measure adverse selection (kill if >8pp over 300 legs).

  S4 harvests vol tape: liquidations are FORCED market orders, cascade 1-3 min.
  Multi-exchange aggregate all_liq_10m has 1s median lag (usable intra-round) — the ONLY
  liq feed allowed intra-round per Moon Dev knowledge base 5b. binance_liq_10m median
  163s stale = BANNED intra-round. When cascade >= $1.5M/10s + spot confirms direction,
  Polymarket book reprices slower than cascade unfolds. Buy continuation.

  Natural pairing: S6 pull trigger IS S4 entry trigger. S6 pull protects S6 from adverse
  selection, S4 monetizes the same forced flow. S6 dies in vol spikes, S4 dies in dead
  tape — uncorrelated groups A (fee farm) + C (fast flow).

STRATEGY SOURCES: BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD -> S6 + S4
MOON DEV KNOWLEDGE: s3_feefarm/docs/MOONDEV 6+ KNOWLEDGE.MD Part 5b + Multi-Exchange Liqs.md
  - all_liquidations/10m.json: 1s median lag, 30s update, 270-278 rows -> usable
  - binance_liquidations/10m.json: 163s median lag -> BANNED intra-round
  - totals.json: 6 windows, 20s update -> context for P_model sizing
  - imbalance/1h.json: 90s cadence -> background only, pull trigger (c)
  - Feed-health flat-switch M3/M4/M7: stale/empty/401 -> flat immediately (S4+S9 rule)

S6 MECHANICS (encoded):
  - BUY Up @ 0.49 and BUY Down @ 0.49, 20 shares each (~$10/side, incubation)
  - Window: t+0 -> 45s (QUOTE_END_S=45, QUOTE_END_FRAC=0.15)
  - Band: mid in [0.45,0.55] at placement AND at every fill (Part-4 gate, fails loudly)
  - Inventory: single leg HELD to resolution (EXIT_MODE="hold") — the experiment
  - Kill: conditional asymmetry >8pp over 300 legs

S4 MECHANICS (encoded):
  - Markets: 15m ONLY (5m explicitly excluded — detect+fill+resolve in 300s leaves no runway)
  - Trigger: all_liq_10m one-sided cascade >= $1.5M/10s AND spot confirms direction
    - SELL cascade (longs liquidated) = forced sells = down pressure -> continuation Down
    - BUY cascade (shorts liquidated) = forced buys = up pressure -> Up
  - Spot confirm: spot move direction matches cascade, |move| >= 2 bps in last 10s
    (pull trigger (b) is 6 bps/10s magnitude-only; S4 needs directional)
  - Entry: taker FOK BUY continuation side if ask <= P_model - fee - 0.02
    - P_model from measured post-cascade drift (event study). Initial conservative prior:
      1.5M->0.60, 2.5M->0.65, 4M->0.70, 8M->0.75 + spot confirmation bonus
      To be refined by s46_backtest.py event study using harvested data
  - Exit: hold to resolution (S1 handoff if TWAP gap locks in, else hold)
  - Window: t+30s -> 80% elapsed (15m = 30s..720s) — avoid round-open noise and late lock-in
  - Size: 20 shares (~$12 at 0.60), max 1 S4 position per 15m round
  - Kill: <55% post-fee win rate over 100 triggers, or mean-reverting regime

S8 ROUTER (encoded):
  - Quiet: 00-12 UTC + 16-18 UTC + weekends all day -> S6 enabled, S4 enabled (cascade override)
  - Active: US 13:30-21:00 UTC -> S6 disabled (thin edge), S4 enabled, S1/S2 would be enabled
  - Vol override: trailing 5m sigma >1.5x baseline (7 bps) -> S6 flat, S4 armed
  - Macro blackout: ±1 round around scheduled releases = no directional taker (S4 blocked)
    — for v1 we implement a manual calendar file check, default open

RISK (Part-3 global, encoded, none in willpower P5):
  - Net cap $30 per 5m bucket across BOTH S6+S4 and BOTH series (same underlying)
  - Daily stop $20 LIVE only -> KILL + flat (DRY P&L is simulated, never kills)
  - Feed stale >60s / empty / 401 -> flat (cannot pull on what cannot see)
  - DRY unless .env says LIVE_TRADING=1 + keys present + every gate passes

Outputs: s46_harvester.log, s46_fills.csv (S6), s46_s4_fills.csv (S4 taker),
         s46_pulls.csv, s46_cascades.csv (event study), s46_stats.json
Usage: python3 s46_harvester.py [--minutes 30] [--preflight]
"""
import argparse, asyncio, csv, json, math, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
KILL = os.path.join(HERE, "KILL")
LOG = os.path.join(HERE, "s46_harvester.log")
FILLS_CSV = os.path.join(HERE, "s46_fills.csv")
S4_FILLS_CSV = os.path.join(HERE, "s46_s4_fills.csv")
PULLS_CSV = os.path.join(HERE, "s46_pulls.csv")
CASCADES_CSV = os.path.join(HERE, "s46_cascades.csv")
STATS = os.path.join(HERE, "s46_stats.json")

# ---- S6 constants ----
S6_QUOTE_PRICE = 0.49
S6_QUOTE_SHARES = 20
S6_QUOTE_START_S = 0
S6_QUOTE_END_S = 45
S6_QUOTE_END_FRAC = 0.15
S6_BAND = (0.45, 0.55)
S6_PAIR_MAX_COST = 0.97

# ---- S4 constants ----
S4_ENABLED = True
S4_MARKETS = ["15m"]  # 5m explicitly excluded per S4 mechanics
S4_CASCADE_USD = 1_500_000.0
S4_WINDOW_START_S = 30
S4_WINDOW_END_FRAC = 0.80  # 80% of 15m = 720s
S4_SPOT_CONFIRM_BPS = 2.0
S4_ASK_BAND = (0.30, 0.85)  # taker band wider than maker, but still gated
S4_SIZE = 20
S4_FEE_MARGIN = 0.02
# P_model prior — conservative, to be refined by event study
S4_P_MODEL = [
    (8_000_000, 0.75),
    (4_000_000, 0.70),
    (2_500_000, 0.65),
    (1_500_000, 0.60),
]

# ---- shared pull triggers ----
LIQ_URL = "https://api.moondev.com/api/all_liquidations/10m.json"
LIQ_TOTALS_URL = "https://api.moondev.com/api/all_liquidations/totals.json"
LIQ_POLL_S = 2.0
LIQ_WINDOW_S = 10
IMB_URL = "https://api.moondev.com/api/imbalance/1h.json"
IMB_POLL_S = 90.0
IMB_EXTREME = 0.85
SPOT_MOVE_BPS = 6.0
SPOT_WINDOW_S = 10.0
REQUOTE_LOCK_S = 60.0
FEED_STALE_S = 60.0
VOL_MULT = 1.5
SIGMA_BASE_BPS = 7.0

# ---- risk ----
NET_CAP_5M = 30.0
DAILY_STOP = 20.0

# ---- session gate S8 ----
QUIET_UTC = [(0, 12), (16, 18)]
WEEKEND_ALL_DAY = True

# ---- market plumbing ----
RTDS = "wss://ws-live-data.polymarket.com"
SYMBOL = "btc/usd"
DATA = "https://data-api.polymarket.com"
ROUNDS = [(300, "5m"), (900, "15m")]

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def fee(p): return 0.07 * p * (1-p)

def http_json(url, headers=None):
    h = headers or {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        for ln in open(ENV_PATH):
            ln=ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k,v = ln.split("=",1)
                env[k.strip()] = v.strip()
    return env

def csv_append(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)

def quiet_session(now=None):
    dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    if WEEKEND_ALL_DAY and dt.weekday() >= 5:
        return True, "weekend"
    h = dt.hour
    for a,b in QUIET_UTC:
        if a <= h < b:
            return True, f"quiet {a}-{b} UTC"
    return False, f"active session {h} UTC"

def p_model_for_cascade(usd, spot_bps_abs):
    # find base by size
    base = 0.60
    for thresh, p in S4_P_MODEL:
        if usd >= thresh:
            base = p
            break
    # spot confirmation bonus
    if spot_bps_abs >= 6:
        base += 0.03
    if spot_bps_abs >= 10:
        base += 0.02
    return min(base, 0.80)

class T:
    def __init__(self):
        self.samples = deque(maxlen=3600)  # (ts, spot)
        self.twap_samples = deque(maxlen=3600)  # (ts, twap_sixty)
        self.last_update = 0.0
        self.twap_last_update = 0.0
        self.rounds = {}  # (label,start) -> round state
        self.pulled_until = 0.0
        self.pull_reason = ""
        self.liq_ok_t = 0.0
        self.liq_shape_logged = False
        self.liq_trig_ts = 0.0
        self.totals_ok_t = 0.0
        self.hb_t = 0.0
        self.day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.stats = json.load(open(STATS)) if os.path.exists(STATS) else {
            "day": self.day, "day_pnl": 0.0,
            "s6": {"n":0,"quotes":0,"fills":0,"pairs":0,"wins":0,"pnl":0.0,"gross":0.0,"adverse":0.0,"side_legs":{"Up":0,"Down":0},"side_leg_wins":{"Up":0,"Down":0}},
            "s4": {"n":0,"triggers":0,"fills":0,"wins":0,"pnl":0.0,"gross":0.0,"adverse":0.0,"by_size":{"1.5M":0,"2.5M":0,"4M":0,"8M":0}},
            "pulls": {}, "cascades":0, "tick_ts":0
        }
        self.client = None
        self.open_refs = {}  # (label,start) -> open spot ref

t = T()

def sigma5m_bps():
    vals = [v for _, v in list(t.samples)[-600:]]
    if len(vals) < 60:
        return 0.0
    d = [b-a for a,b in zip(vals, vals[1:])]
    m = sum(d)/len(d)
    s1 = math.sqrt(sum((x-m)**2 for x in d)/len(d))
    return (s1 * math.sqrt(300)) / (vals[-1] or 8e4) * 1e4

def spot_move_bps(window_s=10.0):
    now = time.time()
    win = [(ts,v) for ts,v in t.samples if now - ts <= window_s]
    if len(win) < 2:
        return 0.0, 0.0
    start_p = win[0][1]
    end_p = win[-1][1]
    bps = (end_p - start_p) / start_p * 1e4 if start_p else 0.0
    return bps, end_p

def gates(now):
    reasons=[]
    if os.path.exists(KILL):
        reasons.append("KILL file present")
    if time.time() - t.last_update > 10:
        reasons.append("feed stale")
    if now < t.pulled_until:
        reasons.append(f"pulled: {t.pull_reason}")
    if time.time() - t.liq_ok_t > FEED_STALE_S:
        reasons.append("moondev liq feed stale/flat-switch M3/M4/M7")
    if t.stats.get("day_pnl",0.0) <= -DAILY_STOP and load_env().get("LIVE_TRADING")=="1":
        reasons.append(f"daily stop {t.stats['day_pnl']:.2f}")
    return reasons

def s6_session_gate(now):
    ok, why = quiet_session(now)
    # S6 only in quiet OR weekend, and vol not high
    if not ok:
        return False, f"session: {why} (S6 quiet-only)"
    if sigma5m_bps() > VOL_MULT * SIGMA_BASE_BPS:
        return False, f"vol override: sigma {sigma5m_bps():.1f}bps > {VOL_MULT}x base (S6 flat, S4 armed)"
    return True, why

def s4_session_gate(now):
    # S4 enabled always except feed stale, but log session
    if time.time() - t.liq_ok_t > FEED_STALE_S:
        return False, "moondev liq feed stale"
    # Macro blackout placeholder — check file if exists
    return True, "S4 armed (15m only)"

def stats_gates():
    reasons=[]
    env=load_env()
    if env.get("LIVE_TRADING")!="1":
        reasons.append("LIVE_TRADING!=1")
    if not env.get("POLY_PRIVATE_KEY") or not env.get("POLY_FUNDER"):
        reasons.append("no key/funder in .env")
    return reasons

def band_ok(mid, band):
    return band[0] <= mid <= band[1]

def admissible(mid, band, now):
    r = gates(now)
    if not band_ok(mid, band):
        r.append(f"mid {mid:.2f} outside band {band}")
    return (len(r)==0, r)

# ---- execution plumbing ----
def client_for(env):
    if t.client:
        return t.client
    from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType
    host="https://clob.polymarket.com"
    pk,funder = env["POLY_PRIVATE_KEY"], env["POLY_FUNDER"]
    sig_type=int(env.get("POLY_SIGNATURE_TYPE","3"))
    creds=ClobClient(host=host, key=pk, chain_id=137).create_or_derive_api_key()
    c=ClobClient(host=host, key=pk, chain_id=137, creds=creds, signature_type=sig_type, funder=funder)
    try:
        c.update_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    except Exception as ex:
        log(f"balance-allowance sync skipped: {ex!r}")
    t.client=c
    return c

def post_gtc(token_id, price, size, side):
    env=load_env()
    c=client_for(env)
    from py_clob_client_v2 import OrderArgs, OrderType
    from py_clob_client_v2.order_builder.constants import BUY, SELL
    args=OrderArgs(token_id=token_id, price=price, size=size, side=BUY if side=="BUY" else SELL)
    return c.create_and_post_order(args, order_type=OrderType.GTC)

def post_fok(token_id, price, size, side):
    env=load_env()
    c=client_for(env)
    from py_clob_client_v2 import OrderArgs, OrderType
    from py_clob_client_v2.order_builder.constants import BUY, SELL
    args=OrderArgs(token_id=token_id, price=price, size=size, side=BUY if side=="BUY" else SELL)
    return c.create_and_post_order(args, order_type=OrderType.FOK)

def order_id_of(resp):
    if isinstance(resp, dict):
        return resp.get("orderID") or resp.get("orderId") or resp.get("id")
    return resp

def cancel_order(oid):
    env=load_env()
    c=client_for(env)
    oid=order_id_of(oid)
    if not oid:
        log("cancel skipped: no order id"); return
    try:
        c.cancel_orders([oid])
    except Exception as ex:
        log(f"cancel failed {oid}: {ex!r}")

# ---- feeds parsing ----
def parse_liq_rows(payload):
    rows = payload if isinstance(payload, list) else (payload.get("liquidations") or payload.get("data") or payload.get("rows") or [])
    out=[]
    for r in rows:
        if not isinstance(r, dict): continue
        ts = r.get("timestamp") or r.get("ts") or r.get("time") or r.get("created_at") or 0
        if isinstance(ts, str):
            try:
                # ISO8601
                dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
                ts = dt.timestamp()
            except Exception:
                try: ts=float(ts)
                except: ts=0
        if ts > 1e12: ts/=1000.0
        usd = r.get("usd") or r.get("usd_size") or r.get("notional") or r.get("value") or r.get("size_usd") or 0
        try: usd=float(usd)
        except: usd=0.0
        side=str(r.get("side") or r.get("direction") or r.get("type") or "").lower()
        sym=str(r.get("symbol") or r.get("coin") or r.get("pair") or "").upper()
        # 2026-09-26 (S46 record Part #1): Binance COIN-margined symbols (BTCUSD_PERP, BTC_<yymmdd>) come with
        # quantity in CONTRACTS (100 USD face each) but value = price x contracts -> ~800x overstated
        # ($2.07M real showed as $1.72B and topped the event study). Use contracts x 100 for those.
        try:
            _q=float(r.get("quantity") or 0)
        except Exception:
            _q=0.0
        if re.search(r"(USD_PERP|_\d{6})$", sym) and _q>0:
            usd=_q*100.0
        out.append(dict(ts=ts, usd=usd, side=side, sym=sym))
    return out

def scan_cascade(rows):
    """(a) one-sided cascade >= $1.5M in any 10s window on BTC. Returns (reason, newest_ts, side, usd)"""
    btc=[r for r in rows if (not r["sym"]) or ("BTC" in r["sym"] or "XBT" in r["sym"] or "BITCOIN" in r["sym"])]
    btc=[r for r in btc if r["ts"]>0 and r["usd"]>0]
    btc.sort(key=lambda r: r["ts"])
    now=time.time()
    btc=[r for r in btc if now - r["ts"] <= 620]
    j=0
    hit=None
    for i in range(len(btc)):
        while btc[i]["ts"] - btc[j]["ts"] > LIQ_WINDOW_S:
            j+=1
        sell=sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("long","sell","liquidated_long","l"))
        buy=sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("short","buy","liquidated_short","s"))
        if sell >= S4_CASCADE_USD and buy < sell/3:
            hit=(f"cascade SELL/longs ${sell/1e6:.2f}M/{LIQ_WINDOW_S}s", btc[i]["ts"], "SELL", sell)
        elif buy >= S4_CASCADE_USD and sell < buy/3:
            hit=(f"cascade BUY/shorts ${buy/1e6:.2f}M/{LIQ_WINDOW_S}s", btc[i]["ts"], "BUY", buy)
    return hit

def parse_imbalance(rows):
    if isinstance(rows, dict):
        bc=(rows.get("by_coin") or {})
        r=bc.get("BTC") or bc.get("BTCUSDT") or bc.get("XBT")
        if r and r.get("buy_volume_usd") is not None and r.get("sell_volume_usd") is not None:
            b,s=float(r["buy_volume_usd"]), float(r["sell_volume_usd"])
            return b/(b+s) if (b+s) else 0.5
        return None
    for r in rows:
        sym=str(r.get("symbol") or r.get("coin") or r.get("pair") or "").upper()
        if "BTC" in sym or "XBT" in sym:
            v=r.get("imbalance") or r.get("imb") or r.get("ratio") or r.get("buy_ratio")
            if v is None and r.get("buy") is not None and r.get("sell") is not None:
                b,s=float(r["buy"]), float(r["sell"])
                v=b/(b+s) if (b+s) else 0.5
            try: return float(v)
            except: return None
    return None

async def rtds():
    import websockets
    backoff=2
    while True:
        try:
            async with websockets.connect(RTDS, max_size=None) as ws:
                log("RTDS connected; subscribing chainlink-spot + twap-sixty")
                await ws.send(json.dumps({"action":"subscribe","subscriptions":[
                    {"topic":"crypto_prices_chainlink","type":"update","filters":json.dumps({"symbol":SYMBOL}, separators=(",",":"))},
                    {"topic":"crypto_prices_twap_sixty","type":"update","filters":json.dumps({"symbol":SYMBOL}, separators=(",",":"))},
                ]}))
                backoff=2
                async def hb():
                    while True:
                        await asyncio.sleep(5)
                        try: await ws.send("PING")
                        except: break
                h=asyncio.create_task(hb())
                try:
                    while True:
                        try:
                            raw=await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            log("RTDS silent 60s — reconnecting (stall watchdog)"); break
                        try: msg=json.loads(raw)
                        except: continue
                        if msg.get("type")!="update": continue
                        p=msg.get("payload",{})
                        if p.get("symbol")!=SYMBOL: continue
                        topic=msg.get("topic","")
                        try:
                            val=float(p["full_accuracy_value"])/1e18
                        except: continue
                        ts_ms=p.get("timestamp",0)
                        obs=ts_ms/1000.0 if ts_ms>1e12 else (ts_ms/1000.0 if ts_ms>1e9 else time.time())
                        now=time.time()
                        if "chainlink" in topic:
                            t.samples.append((now, val))
                            t.last_update=now
                            # spot move pull trigger (b)
                            win=[v for ts2,v in t.samples if now-ts2 <= SPOT_WINDOW_S]
                            if len(win)>=2:
                                move_bps=abs(win[-1]-win[0])/win[0]*1e4 if win[0] else 0
                                if move_bps >= SPOT_MOVE_BPS:
                                    do_pull(f"SPOT_MOVE {move_bps:.1f}bps/{SPOT_WINDOW_S:.0f}s")
                        elif "twap_sixty" in topic:
                            t.twap_samples.append((now, val))
                            t.twap_last_update=now
                            # snapshot open ref for rounds starting now
                            for Tsec,label in [(300,"5m"),(900,"15m")]:
                                start=int(now // Tsec)*Tsec
                                key=(label,start)
                                if key not in t.open_refs and (now-start) <=3:
                                    t.open_refs[key]=val
                finally:
                    h.cancel()
        except Exception as ex:
            log(f"RTDS problem {ex!r}, backoff {backoff}s"); await asyncio.sleep(backoff); backoff=min(backoff*2,30)

async def moondev_loops():
    last_imb=0.0
    last_totals=0.0
    while True:
        env=load_env()
        key=env.get("MOONDEV_API_KEY","")
        hdr={"User-Agent":"Mozilla/5.0","X-API-Key":key} if key else {"User-Agent":"Mozilla/5.0"}
        # (a) liq cascade fast
        try:
            req=urllib.request.Request(LIQ_URL, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                payload=json.loads(r.read().decode())
            rows=parse_liq_rows(payload)
            if not t.liq_shape_logged and rows:
                t.liq_shape_logged=True
                log(f"liq feed shape: {len(rows)} rows, sample={rows[0]}")
            if rows:
                t.liq_ok_t=time.time()
            trig=scan_cascade(rows)
            if trig and trig[1] > t.liq_trig_ts:
                # trig = (reason, ts, side, usd)
                t.liq_trig_ts=trig[1]
                # log cascade for event study
                side=trig[2]; usd=trig[3]
                spot_bps, spot_px = spot_move_bps(10.0)
                # spot dir confirmation for S4
                spot_confirms = (side=="BUY" and spot_bps >= S4_SPOT_CONFIRM_BPS) or (side=="SELL" and spot_bps <= -S4_SPOT_CONFIRM_BPS)
                # For pull we fire regardless, for S4 we need confirmation
                csv_append(CASCADES_CSV, ["ts","reason","side","usd","spot_bps","spot_px","spot_confirms","totals_5m","totals_15m"],
                           [int(time.time()), trig[0], side, f"{usd:.0f}", f"{spot_bps:.2f}", f"{spot_px:.2f}", int(spot_confirms), "", ""])
                t.stats["cascades"]=t.stats.get("cascades",0)+1
                log(f"CASCADE DETECTED {trig[0]} side={side} usd=${usd/1e6:.2f}M spot_bps={spot_bps:+.1f} confirms={spot_confirms}")
                do_pull("LIQ "+trig[0])
                # attempt S4 entry (will check spot confirmation internally)
                attempt_s4_entry(side, usd, spot_bps)
            elif trig:
                # same cascade already acted on, ignore
                pass
        except urllib.error.HTTPError as ex:
            if ex.code in (401,403):
                if int(time.time())%300 < LIQ_POLL_S:
                    log(f"moondev liq {ex.code} — key expired/missing M7 — FLAT")
            elif ex.code==429:
                if int(time.time())%300 < LIQ_POLL_S:
                    log(f"moondev liq 429 — rate limited, backoff 30s")
                await asyncio.sleep(30)
            else:
                log(f"moondev liq HTTP {ex.code}")
        except Exception as ex:
            log(f"moondev liq error {ex!r}")

        # totals.json for context (20s cadence)
        now=time.time()
        if now - last_totals >= 20:
            last_totals=now
            try:
                req=urllib.request.Request(LIQ_TOTALS_URL, headers=hdr)
                with urllib.request.urlopen(req, timeout=10) as r:
                    data=json.loads(r.read().decode())
                if data:
                    t.totals_ok_t=time.time()
                    # optional: log 5m/15m totals for P_model context
                    wins=data.get("windows",{})
                    # no action, just health
            except Exception as ex:
                # non-critical
                pass

        # (c) imbalance 90s
        if now - last_imb >= IMB_POLL_S:
            last_imb=now
            try:
                req=urllib.request.Request(IMB_URL, headers=hdr)
                with urllib.request.urlopen(req, timeout=10) as r:
                    v=parse_imbalance(json.loads(r.read().decode()))
                if v is not None and (v >= IMB_EXTREME or v <= 1-IMB_EXTREME):
                    do_pull(f"IMB_EXTREME {v:.2f}")
            except Exception as ex:
                log(f"moondev imbalance error {ex!r}")

        await asyncio.sleep(LIQ_POLL_S)

def do_pull(reason):
    now=time.time()
    if now < t.pulled_until and reason.split()[0] in t.pull_reason:
        return
    t.pulled_until=now+REQUOTE_LOCK_S
    t.pull_reason=reason
    t.stats["pulls"][reason.split()[0]]=t.stats["pulls"].get(reason.split()[0],0)+1
    csv_append(PULLS_CSV, ["ts","reason","active_rounds"],
               [int(now), reason, sum(1 for r in t.rounds.values() for q in r.get("s6_quotes",{}).values() if q["phase"]=="RESTING" and q.get("placed"))])
    log(f"PULL BOTH S6 — {reason}")
    for key, rs in t.rounds.items():
        for side in ("Up","Down"):
            q=rs.get("s6_quotes",{}).get(side)
            if q and q["phase"]=="RESTING":
                q["phase"]="PULLED"
                if q.get("oid"):
                    cancel_order(q["oid"])
    save_stats()

# ---- round helpers ----
def mid_for(rs, side):
    bk=rs.get("book",{}).get(side)
    if not bk: return None
    b,a=bk
    if b is None or a is None: return None
    return (b+a)/2

def round_tokens(label, start):
    try:
        ev=http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")
        m=ev[0]["markets"][0]
        toks=json.loads(m["clobTokenIds"])
        return {"Up":toks[0],"Down":toks[1],"cond":m.get("conditionId")}
    except Exception:
        return None

def gamma_resolution(label, start):
    for q in ("&closed=true",""):
        try:
            ev=http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}{q}")
            if not ev: continue
            m=ev[0]["markets"][0]
            pr=[float(x) for x in json.loads(m["outcomePrices"])]
            if m.get("closed") or not m.get("acceptingOrders"):
                return "Up" if pr[0]>0.5 else "Down"
            if pr[0]>=0.9995 or pr[0]<=0.0005:
                return "Up" if pr[0]>=0.9995 else "Down"
        except:
            pass
    return None

def net_directional_5m(now):
    bucket=(int(now)//300)*300
    net=0.0
    for (label,start), rs in t.rounds.items():
        if start//300 != bucket//300:
            continue
        # S6 pairs hedged
        s6=rs.get("s6_quotes",{})
        up=s6.get("Up"); dn=s6.get("Down")
        if up and dn and up["phase"]=="FILLED" and dn["phase"]=="FILLED":
            continue
        # count S6 naked legs
        for side in ("Up","Down"):
            q=s6.get(side)
            if q and q["phase"]=="FILLED":
                net+=q["filled"]*S6_QUOTE_PRICE*(1 if side=="Up" else -1)
        # S4 taker positions (naked directional)
        s4=rs.get("s4_fill")
        if s4 and not s4.get("exited"):
            side=s4["side"]
            # taker price approx ask, use filled * price
            net+=s4["filled"]*s4["price"]*(1 if side=="Up" else -1)
    return net

def record_s6_fill(rs, side, ts, px, sz, now, live=False):
    mid=mid_for(rs, side) or 0.50
    ok, reasons = admissible(mid, S6_BAND, now)
    q=rs.get("s6_quotes",{}).get(side)
    if not q or q["phase"]!="RESTING" or not q.get("placed"):
        return
    if ts < rs["start"]+S6_QUOTE_START_S or ts > rs["start"]+min(S6_QUOTE_END_S, S6_QUOTE_END_FRAC*rs["T"]):
        return
    if ts < q.get("place_ts",0):
        return
    if not ok:
        log(f"❌ S6 FILL REJECTED {rs['label']} {side} @{px:.2f} mid={mid:.2f} — {'; '.join(reasons)}")
        return
    take=min(sz, q["rest"])
    if take<=0: return
    delta=take*S6_QUOTE_PRICE*(1 if side=="Up" else -1)
    if abs(net_directional_5m(now)+delta) > NET_CAP_5M:
        log(f"❌ S6 FILL REJECTED net cap ${NET_CAP_5M:.0f}/5m {rs['label']} {side}")
        return
    q["rest"]-=take
    q["filled"]+=take
    if q["fill_ts"] is None:
        q["fill_ts"]=ts
    q["phase"]="FILLED"
    t.stats["s6"]["fills"]+=1
    csv_append(FILLS_CSV, ["ts","series","round","side","event","price","shares","live"],
               [int(ts), rs["label"], rs["start"], side, "FILL", f"{px:.2f}", f"{take:.0f}", int(live or q.get("live"))])
    log(f"S6 FILL {rs['label']} {side} {take:.0f}sh @ {px:.2f} ({'LIVE' if live or q.get('live') else 'DRY'}) — hold to resolution")
    up, dn = rs["s6_quotes"]["Up"], rs["s6_quotes"]["Down"]
    if up["phase"]=="FILLED" and dn["phase"]=="FILLED" and 2*S6_QUOTE_PRICE < S6_PAIR_MAX_COST:
        # pair completed — cancel any breakeven offers and hold both
        for s2,q2 in (("Up",up),("Down",dn)):
            if q2.get("offered") and not q2.get("exited"):
                q2["offered"]=False
                if q2.get("exit_oid"):
                    cancel_order(q2["exit_oid"])
                log(f"S6 PAIR COMPLETED {rs['label']} — breakeven on {s2} cancelled, hold")
    save_stats()

def attempt_s4_entry(cascade_side, cascade_usd, spot_bps):
    """
    S4 — LIQUIDATION CASCADE CONTINUATION
    cascade_side: BUY = shorts rekt = Up, SELL = longs rekt = Down
    """
    if not S4_ENABLED:
        return
    now=time.time()
    # spot confirmation gate
    if cascade_side=="BUY" and spot_bps < S4_SPOT_CONFIRM_BPS:
        log(f"S4 SKIP cascade BUY but spot_bps {spot_bps:+.1f} < {S4_SPOT_CONFIRM_BPS} (no confirmation)")
        return
    if cascade_side=="SELL" and spot_bps > -S4_SPOT_CONFIRM_BPS:
        log(f"S4 SKIP cascade SELL but spot_bps {spot_bps:+.1f} > -{S4_SPOT_CONFIRM_BPS}")
        return

    cont_side = "Up" if cascade_side=="BUY" else "Down"
    p_model = p_model_for_cascade(cascade_usd, abs(spot_bps))

    # find active 15m round in S4 window
    for (label,start), rs in list(t.rounds.items()):
        if label not in S4_MARKETS:
            continue
        e = now - start
        if e < S4_WINDOW_START_S or e > S4_WINDOW_END_FRAC * rs["T"]:
            continue
        if rs.get("s4_fill") is not None:
            continue  # one S4 per round
        if not rs.get("tokens"):
            continue
        # gates
        ok, why = s4_session_gate(now)
        if not ok:
            log(f"S4 SKIP {label} {start}: {why}")
            continue
        hard = gates(now)
        if hard:
            log(f"S4 SKIP {label} {start}: hard gates {hard}")
            continue
        # book
        bk = rs.get("book",{}).get(cont_side)
        if not bk or bk[1] is None:
            log(f"S4 SKIP {label} {start}: no book for {cont_side}")
            continue
        ask = bk[1]
        mid = mid_for(rs, cont_side)
        if mid is None:
            continue
        # band gate for S4
        ok_band, reasons = admissible(mid, S4_ASK_BAND, now)
        if not ok_band:
            log(f"S4 SKIP {label} {start} {cont_side} mid={mid:.2f} — {'; '.join(reasons)}")
            continue
        if ask < S4_ASK_BAND[0] or ask > S4_ASK_BAND[1]:
            log(f"S4 SKIP {label} {start} {cont_side} ask {ask:.2f} outside {S4_ASK_BAND}")
            continue
        fee_val = fee(ask)
        edge = p_model - ask - fee_val - S4_FEE_MARGIN
        if edge <= 0:
            log(f"S4 SKIP {label} {start} {cont_side} ask={ask:.2f} p_model={p_model:.2f} fee={fee_val:.4f} edge={edge:+.3f} <=0 (book already repriced)")
            continue
        # net cap
        delta = S4_SIZE * ask * (1 if cont_side=="Up" else -1)
        if abs(net_directional_5m(now)+delta) > NET_CAP_5M:
            log(f"S4 SKIP {label} {start}: net cap ${NET_CAP_5M} would breach")
            continue

        # passed — attempt taker entry
        live_hard = gates(now)
        live_stat = stats_gates()
        forced = load_env().get("FORCE_LIVE")=="1"
        live_ok = (not live_hard) and (forced or not live_stat)

        t.stats["s4"]["triggers"]+=1
        # size bucket for stats
        if cascade_usd >= 8_000_000: bucket="8M"
        elif cascade_usd >=4_000_000: bucket="4M"
        elif cascade_usd >=2_500_000: bucket="2.5M"
        else: bucket="1.5M"
        t.stats["s4"]["by_size"][bucket]=t.stats["s4"]["by_size"].get(bucket,0)+1

        log(f"S4 TRIGGER {label} {start} cascade={cascade_side} ${cascade_usd/1e6:.2f}M spot={spot_bps:+.1f}bps cont={cont_side} ask={ask:.2f} p_model={p_model:.2f} fee={fee_val:.4f} edge={edge:+.3f} {'LIVE' if live_ok else 'DRY'}")

        if live_ok:
            try:
                resp = post_fok(rs["tokens"][cont_side], ask, S4_SIZE, "BUY")
                oid = order_id_of(resp)
                log(f"S4 FILL (LIVE) {label} {cont_side} {S4_SIZE}sh @ {ask:.2f} -> {resp}")
                rs["s4_fill"] = dict(side=cont_side, price=ask, filled=S4_SIZE, ts=now, oid=oid, live=True, p_model=p_model, cascade_usd=cascade_usd, spot_bps=spot_bps, edge=edge)
                t.stats["s4"]["fills"]+=1
                csv_append(S4_FILLS_CSV, ["ts","series","round","side","cascade_side","cascade_usd","spot_bps","ask","p_model","fee","edge","shares","live"],
                           [int(now), label, start, cont_side, cascade_side, f"{cascade_usd:.0f}", f"{spot_bps:.2f}", f"{ask:.2f}", f"{p_model:.3f}", f"{fee_val:.4f}", f"{edge:.3f}", S4_SIZE, 1])
            except Exception as ex:
                log(f"S4 LIVE entry failed {label} {cont_side}: {ex!r}")
                # fall through to DRY simulation for stats continuity
                rs["s4_fill"] = dict(side=cont_side, price=ask, filled=S4_SIZE, ts=now, oid=None, live=False, p_model=p_model, cascade_usd=cascade_usd, spot_bps=spot_bps, edge=edge)
                t.stats["s4"]["fills"]+=1
                csv_append(S4_FILLS_CSV, ["ts","series","round","side","cascade_side","cascade_usd","spot_bps","ask","p_model","fee","edge","shares","live"],
                           [int(now), label, start, cont_side, cascade_side, f"{cascade_usd:.0f}", f"{spot_bps:.2f}", f"{ask:.2f}", f"{p_model:.3f}", f"{fee_val:.4f}", f"{edge:.3f}", S4_SIZE, 0])
        else:
            log(f"S4 FILL (DRY) {label} {cont_side} {S4_SIZE}sh @ {ask:.2f} p_model={p_model:.2f} edge={edge:+.3f}")
            rs["s4_fill"] = dict(side=cont_side, price=ask, filled=S4_SIZE, ts=now, oid=None, live=False, p_model=p_model, cascade_usd=cascade_usd, spot_bps=spot_bps, edge=edge)
            t.stats["s4"]["fills"]+=1
            csv_append(S4_FILLS_CSV, ["ts","series","round","side","cascade_side","cascade_usd","spot_bps","ask","p_model","fee","edge","shares","live"],
                       [int(now), label, start, cont_side, cascade_side, f"{cascade_usd:.0f}", f"{spot_bps:.2f}", f"{ask:.2f}", f"{p_model:.3f}", f"{fee_val:.4f}", f"{edge:.3f}", S4_SIZE, 0])

        save_stats()
        # only one S4 per cascade event
        break

def place_s6_quotes(rs, now, live):
    # S6 session gate
    ok_sess, why_sess = s6_session_gate(now)
    if not ok_sess:
        rs["skip_reason"]=why_sess
        if not rs.get("skip_logged"):
            rs["skip_logged"]=True
            log(f"SKIP S6 {rs['label']} {rs['start']}: {why_sess}")
        return
    global_gates=gates(now)
    if global_gates:
        rs["skip_reason"]="; ".join(global_gates)
        if not rs.get("skip_logged"):
            rs["skip_logged"]=True
            log(f"SKIP S6 {rs['label']} {rs['start']}: {rs['skip_reason']}")
        return
    mid=mid_for(rs,"Up") or mid_for(rs,"Down") or 0.50
    if not band_ok(mid, S6_BAND):
        rs["skip_reason"]=f"mid {mid:.2f} outside band"
        if not rs.get("skip_logged"):
            rs["skip_logged"]=True
            log(f"SKIP S6 {rs['label']} {rs['start']}: {rs['skip_reason']}")
        return
    if now > rs["start"]+min(S6_QUOTE_END_S, S6_QUOTE_END_FRAC*rs["T"]):
        return
    for side in ("Up","Down"):
        q=rs["s6_quotes"][side]
        if q["phase"]!="RESTING" or q["placed"]:
            continue
        q["placed"]=True
        q["place_ts"]=now
        t.stats["s6"]["quotes"]+=1
        if live:
            try:
                resp=post_gtc(rs["tokens"][side], S6_QUOTE_PRICE, S6_QUOTE_SHARES, "BUY")
                q["oid"]=order_id_of(resp)
                q["live"]=True
                log(f"S6 QUOTE (LIVE) {rs['label']} BUY {side} {S6_QUOTE_SHARES}sh @ {S6_QUOTE_PRICE} -> {resp}")
            except Exception as ex:
                log(f"S6 QUOTE FAILED -> dry: {ex!r}")
                log(f"S6 QUOTE (DRY) {rs['label']} BUY {side} {S6_QUOTE_SHARES}sh @ {S6_QUOTE_PRICE}")
        else:
            log(f"S6 QUOTE (DRY) {rs['label']} BUY {side} {S6_QUOTE_SHARES}sh @ {S6_QUOTE_PRICE}")
    save_stats()

async def tape_loop():
    while True:
        try:
            now=time.time()
            for (label,start), rs in list(t.rounds.items()):
                if rs["T"] and now > rs["start"]+rs["T"]+30:
                    continue
                if not rs.get("tokens") or not any(q.get("placed") for q in rs.get("s6_quotes",{}).values()):
                    # still need tape for S6 fills detection only if we quoted
                    if not rs.get("s6_quotes"):
                        continue
                try:
                    rows=http_json(f"{DATA}/trades?market={rs['tokens']['cond']}&limit=200")
                except:
                    continue
                for r in rows or []:
                    ts=int(r.get("timestamp",0))
                    if ts <= rs.get("tape_t", rs["start"]):
                        continue
                    out=r.get("outcome") or ("Up" if r.get("outcomeIndex")==0 else "Down")
                    px=float(r["price"]); sz=float(r["size"])
                    if r.get("side")=="SELL" and px <= S6_QUOTE_PRICE:
                        record_s6_fill(rs, out, ts, S6_QUOTE_PRICE, sz, now)
                if rows:
                    rs["tape_t"]=max(int(r["timestamp"]) for r in rows)
        except Exception as ex:
            log(f"tape error {ex!r}")
        await asyncio.sleep(1.5)

def settle_round(rs, now):
    settled=gamma_resolution(rs["label"], rs["start"])
    if not settled:
        return False
    # S6 settlement
    s6=rs.get("s6_quotes",{})
    up=s6.get("Up"); dn=s6.get("Down")
    paired=False
    pnl=0.0
    note=""
    if up and dn and up["phase"]=="FILLED" and dn["phase"]=="FILLED" and not up.get("exited") and not dn.get("exited"):
        pay=min(up["filled"], dn["filled"])
        pnl+=pay*(1.0 - 2*S6_QUOTE_PRICE)
        t.stats["s6"]["pairs"]+=1
        t.stats["s6"]["gross"]+=pnl
        note+=f"S6 pair {pay:.0f}sh +${pnl:.2f} "
        paired=True
    else:
        for side in ("Up","Down"):
            q=s6.get(side)
            if q and q["phase"] in ("STUCK","FILLED") and not q.get("exited"):
                win=(side==settled)
                leg_pnl=q["filled"]*(1.0 - S6_QUOTE_PRICE if win else -S6_QUOTE_PRICE)
                pnl+=leg_pnl
                t.stats["s6"].setdefault("side_legs",{"Up":0,"Down":0})
                t.stats["s6"].setdefault("side_leg_wins",{"Up":0,"Down":0})
                t.stats["s6"]["side_legs"][side]+=1
                if win:
                    t.stats["s6"]["wins"]+=1
                    t.stats["s6"]["side_leg_wins"][side]+=1
                else:
                    t.stats["s6"]["adverse"]+=q["filled"]*S6_QUOTE_PRICE
                note+=f"S6 HOLD-{side}->{'WIN' if win else 'LOSS'} "
    # S4 settlement
    s4=rs.get("s4_fill"); s4_pnl=0.0
    if s4 and not s4.get("exited"):
        win=(s4["side"]==settled)
        # taker fee on entry, no exit fee because hold to resolution
        entry_cost=s4["filled"]*s4["price"]
        # if win, payout = filled *1, pnl = filled*(1-price) - fee*filled
        # fee is on notional? official: fee = shares *0.07*p*(1-p)
        # we already accounted fee in edge, but for pnl compute accurately
        fee_val=fee(s4["price"])*s4["filled"]
        if win:
            s4_pnl=s4["filled"]*(1.0 - s4["price"]) - fee_val
        else:
            s4_pnl=-s4["filled"]*s4["price"] - fee_val
        pnl+=s4_pnl
        t.stats["s4"]["n"]+=1
        if win:
            t.stats["s4"]["wins"]+=1
            t.stats["s4"]["gross"]+=s4_pnl
        else:
            t.stats["s4"]["adverse"]+= -s4_pnl if s4_pnl<0 else 0
        t.stats["s4"]["pnl"]=t.stats["s4"].get("pnl",0.0)+s4_pnl
        note+=f"S4 {s4['side']} {'WIN' if win else 'LOSS'} pnl ${s4_pnl:+.2f} (p_model {s4['p_model']:.2f} edge {s4['edge']:+.3f}) "
        # log S4 outcome to csv
        csv_append(S4_FILLS_CSV, ["ts","series","round","side","cascade_side","cascade_usd","spot_bps","ask","p_model","fee","edge","shares","live","outcome","settled"],
                   [int(now), rs["label"], rs["start"], s4["side"], s4.get("cascade_side",""), f"{s4.get('cascade_usd',0):.0f}", f"{s4.get('spot_bps',0):.2f}", f"{s4['price']:.2f}", f"{s4['p_model']:.3f}", f"{fee_val/s4['filled']:.4f}", f"{s4['edge']:.3f}", s4["filled"], int(s4.get("live",0)), "WIN" if win else "LOSS", settled])

    t.stats["day_pnl"]=t.stats.get("day_pnl",0.0)+pnl
    # 2026-09-26 (S46 record Part #1): `pnl` here is S6 + S4 combined, so when both traded the same
    # 15m round the S4 result leaked into the S6 P&L counter. Keep the two ledgers separate.
    s6_pnl = pnl - (s4_pnl if (s4 and not s4.get("exited")) else 0.0)
    t.stats["s6"]["pnl"]=t.stats["s6"].get("pnl",0.0)+ (s6_pnl if paired or any(q and q["phase"]=="FILLED" for q in s6.values()) else 0)
    # day reset handled in tick_loop
    log(f"ROUND-RESULT {rs['label']} {rs['start']}: settled={settled} pnl ${pnl:+.2f} day ${t.stats['day_pnl']:+.2f} | {note}")
    save_stats()
    return True

def save_stats():
    try:
        with open(STATS,"w") as f:
            json.dump(t.stats,f,indent=2)
    except Exception as ex:
        log(f"save_stats failed {ex!r}")

async def tick_loop():
    while True:
        try:
            if time.time() - t.hb_t >=60:
                t.hb_t=time.time()
                t.stats["tick_ts"]=int(t.hb_t)
                save_stats()
            now=time.time()
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if day != t.stats.get("day"):
                t.stats["day"]=day
                t.stats["day_pnl"]=0.0
            live_hard=gates(now)
            live_stat=stats_gates()
            forced=load_env().get("FORCE_LIVE")=="1"
            live_ok=(not live_hard) and (forced or not live_stat)

            for Tsec,label in ROUNDS:
                start=int(now // Tsec)*Tsec
                key=(label,start)
                rs=t.rounds.setdefault(key, dict(
                    label=label, T=Tsec, start=start, tokens=None, book={},
                    s6_quotes={s: dict(phase="RESTING", placed=False, rest=S6_QUOTE_SHARES, filled=0.0, fill_ts=None, place_ts=None, oid=None, live=False, offered=False, exited=False) for s in ("Up","Down")},
                    s4_fill=None,
                    tape_t=start, settled=False, skip_reason="", skip_logged=False
                ))
                e=now-start
                if rs["tokens"] is None and e>=0:
                    rs["tokens"]=round_tokens(label,start)
                if rs["tokens"]:
                    for side in ("Up","Down"):
                        try:
                            bk=http_json(f"https://clob.polymarket.com/book?token_id={rs['tokens'][side]}")
                            bu=max((float(x["price"]) for x in bk.get("bids",[])), default=None)
                            au=min((float(x["price"]) for x in bk.get("asks",[])), default=None)
                            rs["book"][side]=(bu,au)
                        except:
                            pass
                    # band-exit pull for S6
                    if any(q["phase"]=="RESTING" and q["placed"] for q in rs["s6_quotes"].values()):
                        mid=mid_for(rs,"Up") or mid_for(rs,"Down") or 0.50
                        if not band_ok(mid, S6_BAND):
                            do_pull(f"BAND_EXIT mid={mid:.2f} {rs['label']}")

                # S6 quote window 0-45s
                if S6_QUOTE_START_S <= e <= min(S6_QUOTE_END_S, S6_QUOTE_END_FRAC*Tsec) and not any(q["placed"] for q in rs["s6_quotes"].values()):
                    place_s6_quotes(rs, now, live_ok)

                # close S6 resting quotes at window end
                if e >= min(S6_QUOTE_END_S, S6_QUOTE_END_FRAC*Tsec):
                    for side in ("Up","Down"):
                        q=rs["s6_quotes"][side]
                        if q["phase"]=="RESTING" and q["placed"]:
                            q["phase"]="PULLED"
                            if q.get("oid"):
                                cancel_order(q["oid"])
                                log(f"S6 CLOSE {rs['label']} {side} quote cancelled at window end")

                # (settlement moved below, 2026-09-26 S46 record Part #1: `e` here is the CURRENT round's
                #  elapsed time, always < Tsec, so `e > Tsec+45` could never be true -> no round ever
                #  settled, n stayed 0 and no S6 leg / S4 outcome was ever counted. Same bug as S3/S6.)

            # settle FINISHED rounds: retry every 30 s until gamma reports the outcome
            for key, rs in list(t.rounds.items()):
                if rs["settled"] or not rs.get("tokens"): continue
                if now - rs["start"] > rs["T"] + 45 and now >= rs.get("settle_next", 0):
                    rs["settle_next"] = now + 30
                    rs["settled"] = settle_round(rs, now)

            # GC old rounds
            for key in [k for k,r in t.rounds.items() if now - k[1] > 1500]:
                del t.rounds[key]

        except Exception as ex:
            log(f"tick error {ex!r}")
        await asyncio.sleep(2.0)

def preflight():
    print("="*72)
    print("S46 PREFLIGHT — S6 coin-flip + S4 cascade continuation (15m only)")
    print(time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    env=load_env()
    now=time.time()
    for Tsec,label in ROUNDS:
        start=int(now // Tsec)*Tsec
        toks=round_tokens(label,start)
        print(f"{label}: tokens {'OK' if toks else 'MISSING'} for {start}")
        # book check
        if toks:
            try:
                bk=http_json(f"https://clob.polymarket.com/book?token_id={toks['Up']}")
                best_bid=max((float(x["price"]) for x in bk.get("bids",[])), default=None)
                best_ask=min((float(x["price"]) for x in bk.get("asks",[])), default=None)
                print(f"  book Up bid={best_bid} ask={best_ask}")
            except Exception as ex:
                print(f"  book fetch failed {ex!r}")
    ok,why=quiet_session(now)
    print(f"session gate: {'QUIET' if ok else 'ACTIVE'} — {why} (S6 quiet-only, S4 always armed)")
    print(f"S6 band: {S6_BAND} price {S6_QUOTE_PRICE} x {S6_QUOTE_SHARES}sh window 0-{S6_QUOTE_END_S}s hold-mode")
    print(f"S4 band: {S4_ASK_BAND} markets {S4_MARKETS} cascade ${S4_CASCADE_USD/1e6:.1f}M/10s spot_confirm {S4_SPOT_CONFIRM_BPS}bps")
    print(f"S4 P_model prior: {S4_P_MODEL} + spot bonus, edge > {S4_FEE_MARGIN}")
    print(f"moondev key present: {'yes' if env.get('MOONDEV_API_KEY') else 'NO (liq/imb pulls dead -> FLAT via M7)'}")
    print(f"live keys present: {'yes' if env.get('POLY_PRIVATE_KEY') and env.get('POLY_FUNDER') else 'no (DRY only)'}")
    print(f"LIVE_TRADING: {env.get('LIVE_TRADING','0')} FORCE_LIVE: {env.get('FORCE_LIVE','0')}")
    print(f"KILL file: {'PRESENT — FLAT' if os.path.exists(KILL) else 'absent'}")
    r=gates(now)
    print(f"hard gates: {'MET — may quote' if not r else 'NOT MET -> FLAT: '+'; '.join(r)}")
    print(f"s6 session gate: {'PASS' if s6_session_gate(now)[0] else 'BLOCKED'} — {s6_session_gate(now)[1]}")
    print(f"s4 session gate: {'PASS' if s4_session_gate(now)[0] else 'BLOCKED'} — {s4_session_gate(now)[1]}")
    print(f"RTDS: {RTDS} topics chainlink + twap_sixty")
    print(f"LIQ feed: {LIQ_URL} (1s median lag, ONLY intra-round feed)")
    print(f"BANNED intra-round: binance_liquidations/10m.json (163s median lag)")
    print("="*72)

async def main_async(minutes=None):
    log(f"S46 START S6 {S6_QUOTE_PRICE}x{S6_QUOTE_SHARES} 0-{S6_QUOTE_END_S}s band {S6_BAND} + S4 {S4_MARKETS} cascade ${S4_CASCADE_USD/1e6:.1f}M spot_confirm {S4_SPOT_CONFIRM_BPS}bps P_model {S4_P_MODEL} — DRY unless LIVE_TRADING=1")
    tasks=[asyncio.create_task(rtds()), asyncio.create_task(moondev_loops()), asyncio.create_task(tape_loop()), asyncio.create_task(tick_loop())]
    if minutes:
        await asyncio.sleep(minutes*60)
        for task in tasks:
            task.cancel()
    else:
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=None)
    ap.add_argument("--preflight", action="store_true")
    args=ap.parse_args()
    if args.preflight:
        preflight()
        sys.exit(0)
    try:
        asyncio.run(main_async(args.minutes))
    except KeyboardInterrupt:
        log("interrupted — flat")
