#!/usr/bin/env python3
"""
S10 VOL-EVENT BINARY BOX ("own the whipsaw, don't guess it") — incubation bot, DRY by default.

Strategy source: BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD -> S10. Pairs naturally with S6:
S6 harvests the coin-flip open in QUIET tape (low vol, no event); S10 harvests WHIPSAW rounds
when the calendar or a vol spike says "this round contains an event" and refuses to carry a
naked directional leg across an event — the opposite of S6's hold-to-resolution rule.

Markets: 15m primarily (releases land inside one 15m candle); 5m monitored for secondary
whipsaw fills. Style: both-sides maker AND pair taker. Edge class: convexity in event rounds.

Causal edge: in event rounds probability mass migrates to the extremes while the book still
shows 50+/-1c. Two harvests:
  (a) PAIR ARBITRAGE (taker)  — buy Up + Down whenever combined ask < $0.97 POST-FEE.
                                Panic on one side, nobody requoting the other — whipsaws.
  (b) MAKER STRADDLE (maker) — quote Up @0.45 and Down @0.45 around the event; whipsaw fills
                                both legs, $0.90 of cost pays $1.00.
Losers pay: panic takers on both sides of the same whipsaw — they pay twice, once each dir.

Calendar gate (S8 feeds it): rounds containing 8:30/10:00 ET data or 14:00 ET FOMC -> BOX
mode armed from round-open. Also arms for CPI/NFP/PPI/FOMC_MINUTES/JOLTS/GDP/PCE/RETAIL etc.
Vol gate: trailing-1h realized sigma > 1.8x baseline arms BOX mode even off-calendar
(cascade regimes).

LEG-RISK RULE (strict — opposite of S6): if only one leg fills and the other hasn't filled
within 60s, OFFER the missing leg aggressively up to combined cost $0.97; beyond that,
UNWIND the lone leg at model fair. NEVER carry a naked event position (that's S4/S5's job).

RISK: $10/leg incubation (QUOTE_SHARES=20sh at 0.45 ~ $9 maker; taker leg sized to the same
notional per pair). Max $30 net directional across both markets/leg-states per 5-min window
(global Part-3 cap shared with S3/S6). Daily stop $20 (LIVE only) -> KILL + flat. Moon Dev
feed stale/empty/401 -> flat. DRY unless .env says LIVE_TRADING=1, keys present and every
gate passes. DRY fills are queue-optimistic simulations from the public tape and prove
plumbing, never edge.

Outputs: s10_box.log, s10_fills.csv, s10_pulls.csv, s10_pairs.csv, s10_stats.json.
Usage:   python3 s10_box.py [--minutes 30] [--preflight] [--calibrate]
"""
import argparse, asyncio, csv, json, math, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
KILL = os.path.join(HERE, "KILL")
LOG = os.path.join(HERE, "s10_box.log")
FILLS_CSV = os.path.join(HERE, "s10_fills.csv")
PULLS_CSV = os.path.join(HERE, "s10_pulls.csv")
PAIRS_CSV = os.path.join(HERE, "s10_pairs.csv")
STATS = os.path.join(HERE, "s10_stats.json")

# ---- strategy constants (S10 mechanics) ----
MAKER_BID         = 0.45    # (b) straddle: quote both sides at 0.45  -> pair cost 0.90 pays 1.00
PAIR_ASK_CAP      = 0.97    # (a) pair-arb cap: combined ask < $0.97 post-fee
QUOTE_SHARES      = 20      # ~$10/leg incubation size
LEG_TIMEOUT_S     = 60.0    # if one leg fills and the other hasn't within 60s -> chase/unwind
CHASE_MAX_COST    = 0.97    # chase the missing leg up to combined cost $0.97
QUOTE_START_S     = 0       # armed from round-open (calendar rounds are known pre-open)
QUOTE_END_FRAC_15 = 0.50    # quote through 50% of a 15m event round (whipsaws land early and late)
QUOTE_END_FRAC_5  = 0.40    # 5m secondary window tighter
BAND              = (0.40, 0.60)   # wide band around 50c for the whipsaw zone; outside this we are already resolved
SIGMA_BASE_BPS    = 7.0     # baseline 5m vol in bps (same as S6/S3)
VOL_GATE_MULT     = 1.8     # trailing-1h realized sigma > 1.8x baseline -> BOX mode armed
SIGMA_WINDOW_S    = 3600    # 1h trailing for vol gate
# ---- pull triggers (shared with S6 but REARMED in vol mode) ----
LIQ_URL           = "https://api.moondev.com/api/all_liquidations/10m.json"
LIQ_POLL_S        = 2.0
LIQ_CASCADE_USD   = 1_500_000.0
LIQ_WINDOW_S      = 10
SPOT_MOVE_BPS     = 6.0
SPOT_WINDOW_S     = 10.0
FEED_STALE_S      = 60.0
IMB_URL           = "https://api.moondev.com/api/imbalance/1h.json"
IMB_POLL_S        = 90.0
IMB_EXTREME       = 0.85
# ---- risk (Part-3 global rules) ----
NET_CAP_5M        = 30.0
DAILY_STOP        = 20.0
# ---- calendar gate (major US macro releases that move BTC, all times ET = America/New_York) ----
# (minute-of-day-in-ET -> name). Releases land inside one 15m candle; we arm the round that
# CONTAINS the print (i.e. start = print // 900 * 900 ET), plus guard for 5m round too.
# Times are local wall-clock (handles EST/EDT via America/New_York zone when available; if
# zoneinfo is missing we fall back to ET = UTC-5 which is wrong in EDT — prints a warning).
MACRO_RELEASES_ET = [
    (8*60+30,  "CPI / NFP / PPI / Retail / Durables / GDP-adv / PCE"),   # 8:30 ET bucket
    (10*60+0,  "ISM / Consumer Conf / New Homes"),                       # 10:00 ET
    (14*60+0,  "FOMC decision / FOMC minutes"),                           # 14:00 ET
]
CALENDAR_ARM_LEAD_S = 30     # arm 30s BEFORE the round start (maker quotes get in the book early)
CALENDAR_ARM_TAIL_S = 120    # keep box-mode armed 120s into the round (whipsaw aftershocks)
# ---- market plumbing ----
RTDS = "wss://ws-live-data.polymarket.com"
SYMBOL = "btc/usd"
DATA = "https://data-api.polymarket.com"
ROUNDS = [(900, "15m"), (300, "5m")]   # 15m PRIMARY, 5m secondary per the spec
EST_FAIR = 0.50   # model fair for the unwinds — in a binary around an event the unconditional fair is 0.50

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def fee(p):
    """Polymarket taker fee: 0.07 * p * (1-p). Makers pay 0."""
    return 0.07 * p * (1 - p)

def post_fee_cost(ask_up, ask_dn):
    """Cost to buy both legs at taker including fee."""
    return (ask_up + fee(ask_up)) + (ask_dn + fee(ask_dn))

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        for ln in open(ENV_PATH):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def csv_append(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)

def get_et_offset_minutes(utc_dt):
    """Return UTC offset in minutes for America/New_York at the given UTC moment.
    EST = UTC-5 (300 min), EDT = UTC-4 (240 min). Falls back to EST if zoneinfo missing
    (logs once); calendar gate will be off by 1h in EDT in that case."""
    try:
        from zoneinfo import ZoneInfo
        et = utc_dt.astimezone(ZoneInfo("America/New_York"))
        # utc-offset in minutes (negative west of Greenwich)
        off = int(et.utcoffset().total_seconds() // 60)
        return -off, False   # return "minutes to SUBTRACT from UTC to get ET wallclock"
    except Exception:
        # EST fallback = UTC-5
        return 300, True

def et_minute_of_day(now=None):
    """Return (minute_in_et_wallclock_day, is_fallback)."""
    dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    off_min, fb = get_et_offset_minutes(dt)
    et_dt = dt - timedelta(minutes=off_min)
    return et_dt.hour * 60 + et_dt.minute, fb

def et_seconds_of_day(now=None):
    """(seconds into the ET wall-clock day as float, ET weekday 0=Mon, is_fallback)."""
    dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    off_min, fb = get_et_offset_minutes(dt)
    et_dt = dt - timedelta(minutes=off_min)
    return et_dt.hour * 3600 + et_dt.minute * 60 + et_dt.second + et_dt.microsecond / 1e6, et_dt.weekday(), fb

def calendar_armed(now=None):
    """Return (armed: bool, reason: str). Armed from CALENDAR_ARM_LEAD_S before the 15m round
    that CONTAINS a macro print until CALENDAR_ARM_TAIL_S after that round ends.
    2026-09-26 (S10 record Part #1): the shipped version worked in whole ET minutes, so the
    documented 30 s pre-arm could never trigger (-0.5 min < -1 min) and it armed on weekends,
    when no US release exists. Now seconds-precise and weekdays only."""
    now = now or time.time()
    et_sec, wd, fb = et_seconds_of_day(now)
    if wd >= 5:
        return False, ""
    for print_min, name in MACRO_RELEASES_ET:
        rd_start = (print_min // 15) * 15 * 60            # 15m round containing the print, in ET seconds
        if rd_start - CALENDAR_ARM_LEAD_S <= et_sec <= rd_start + 900 + CALENDAR_ARM_TAIL_S:
            reason = f"calendar {name} @{print_min//60:02d}:{print_min%60:02d} ET"
            if fb: reason += " (EST-fallback: verify tzdata)"
            return True, reason
    return False, ""

def sigma_bps(window_s=SIGMA_WINDOW_S):
    """Realized sigma over `window_s` from 1s spot diffs (scaled to bps annualized per 5m bar)."""
    vals = [v for _, v in list(t.samples) if time.time() - _ <= window_s]
    if len(vals) < 60:
        return 0.0
    d = [b - a for a, b in zip(vals, vals[1:])]
    m = sum(d) / len(d)
    s1 = math.sqrt(sum((x - m) ** 2 for x in d) / len(d))
    # sigma over 5m horizon in bps of spot
    return (s1 * math.sqrt(300)) / (vals[-1] or 8e4) * 1e4

class T:
    def __init__(self):
        self.samples = deque(maxlen=7200)     # (ts, spot) — 2h worth of 1s ticks
        self.last_update = 0.0
        self.rounds = {}                      # (label, start) -> round state
        self.armed_until = 0.0                # when box mode disarms
        self.arm_reason = ""
        self.arm_source = ""                  # "calendar" | "vol" | ""
        self.liq_ok_t = 0.0
        self.liq_shape_logged = False
        self.liq_trig_ts = 0.0
        self.hb_t = 0.0
        self.tz_fallback_logged = False
        self.day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.day_pnl = 0.0
        self.sigma_calib = []                 # running 1h sigma samples for today's baseline
        self.sigma_calib_t = 0.0
        self.stats = json.load(open(STATS)) if os.path.exists(STATS) else {
            "n": 0, "maker_quotes": 0, "maker_fills": 0, "taker_pairs": 0,
            "pairs_completed": 0, "pairs_won": 0, "pnl": 0.0, "gross": 0.0,
            "unwind_loss": 0.0, "chases": 0, "arms": {"calendar": 0, "vol": 0},
            "pulls": {}, "day": self.day, "day_pnl": 0.0,
            "sigma_baseline_bps": SIGMA_BASE_BPS, "sigma_peak_bps": 0.0,
            "kill_test": {"events_armed": 0, "events_two_leg": 0, "events_unwind_loss": 0.0,
                          "events_pair_gain": 0.0}}
        self.client = None

t = T()

# ---------------- gates ----------------

def hard_gates(now):
    """HARD gates — never bypassable. Returns list of reasons (empty = all clear)."""
    reasons = []
    if os.path.exists(KILL):
        reasons.append("KILL file present")
    if time.time() - t.last_update > 10:
        reasons.append("feed stale")
    if time.time() - t.liq_ok_t > FEED_STALE_S:
        reasons.append("moondev liq feed stale/flat-switch")
    # Unlike S6, S10 only fires when BOX mode is armed. Outside arm windows we are FLAT.
    if now > t.armed_until:
        reasons.append("box mode disarmed (no event / no vol)")
    if t.stats.get("day_pnl", 0.0) <= -DAILY_STOP and load_env().get("LIVE_TRADING") == "1":
        reasons.append(f"daily stop {t.stats['day_pnl']:.2f}")
    return reasons

def stats_gates():
    reasons = []
    env = load_env()
    if env.get("LIVE_TRADING") != "1":
        reasons.append("LIVE_TRADING!=1")
    if not env.get("POLY_PRIVATE_KEY") or not env.get("POLY_FUNDER"):
        reasons.append("no key/funder in .env")
    return reasons

def box_mode_ok(mid):
    return BAND[0] <= mid <= BAND[1]

def admissible(mid, now):
    """Part-4 gate: runs BEFORE any fill is recorded. Returns (ok, reasons)."""
    r = hard_gates(now)
    if not box_mode_ok(mid):
        r.append(f"mid {mid:.2f} outside box band {BAND}")
    return (len(r) == 0, r)

# ---------------- execution plumbing (mirrors s6_harvester.py) ----------------

def client_for(env):
    if t.client: return t.client
    from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType
    host = "https://clob.polymarket.com"
    pk, funder = env["POLY_PRIVATE_KEY"], env["POLY_FUNDER"]
    sig_type = int(env.get("POLY_SIGNATURE_TYPE", "3"))
    creds = ClobClient(host=host, key=pk, chain_id=137).create_or_derive_api_key()
    c = ClobClient(host=host, key=pk, chain_id=137, creds=creds,
                   signature_type=sig_type, funder=funder)
    try:
        c.update_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    except Exception as ex:
        log(f"balance-allowance sync skipped: {ex!r}")
    t.client = c
    return c

def post_gtc(token_id, price, size, side):
    env = load_env()
    c = client_for(env)
    from py_clob_client_v2 import OrderArgs, OrderType
    from py_clob_client_v2.order_builder.constants import BUY, SELL
    args = OrderArgs(token_id=token_id, price=price, size=size,
                     side=BUY if side == "BUY" else SELL)
    return c.create_and_post_order(args, order_type=OrderType.GTC)

def post_fok(token_id, price, size, side):
    env = load_env()
    c = client_for(env)
    from py_clob_client_v2 import OrderArgs, OrderType
    from py_clob_client_v2.order_builder.constants import BUY, SELL
    args = OrderArgs(token_id=token_id, price=price, size=size,
                     side=BUY if side == "BUY" else SELL)
    return c.create_and_post_order(args, order_type=OrderType.FOK)

def order_id_of(resp):
    if isinstance(resp, dict):
        return resp.get("orderID") or resp.get("orderId") or resp.get("id")
    return resp

def cancel_order(oid):
    env = load_env()
    c = client_for(env)
    oid = order_id_of(oid)
    if not oid:
        log("cancel skipped: no order id"); return
    try:
        c.cancel_orders([oid])
    except Exception as ex:
        log(f"cancel failed {oid}: {ex!r}")

# ---------------- feeds ----------------

async def rtds():
    """Chainlink spot via RTDS — vol gate + spot-move pull trigger. Stall watchdog."""
    import websockets
    backoff = 2
    while True:
        try:
            async with websockets.connect(RTDS, max_size=None) as ws:
                log("RTDS connected; subscribing chainlink-spot")
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                    {"topic": "crypto_prices_chainlink", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))}]}))
                backoff = 2
                async def hb():
                    while True:
                        await asyncio.sleep(5); await ws.send("PING")
                h = asyncio.create_task(hb())
                try:
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            log("RTDS silent for 60s — reconnecting (stall watchdog)")
                            break
                        try: msg = json.loads(raw)
                        except Exception: continue
                        if msg.get("type") != "update": continue
                        p = msg.get("payload", {})
                        if p.get("symbol") != SYMBOL: continue
                        val = float(p["full_accuracy_value"]) / 1e18
                        now = time.time()
                        t.samples.append((now, val))
                        t.last_update = now
                        # vol gate check every tick (cheap)
                        check_vol_gate(now)
                        # spot-move pull (same as S6 — yank maker straddle on a jump)
                        win = [v for ts, v in t.samples if now - ts <= SPOT_WINDOW_S]
                        if len(win) >= 2:
                            move_bps = abs(win[-1] - win[0]) / win[0] * 1e4
                            if move_bps >= SPOT_MOVE_BPS:
                                do_pull(f"SPOT_MOVE {move_bps:.1f}bps/{SPOT_WINDOW_S:.0f}s")
                finally:
                    h.cancel()
        except Exception as ex:
            log(f"RTDS problem {ex!r}, backoff {backoff}s"); await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)

def parse_liq_rows(payload):
    rows = payload if isinstance(payload, list) else (payload.get("liquidations") or payload.get("data") or payload.get("rows") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict): continue
        ts = r.get("timestamp") or r.get("ts") or r.get("time") or r.get("created_at") or 0
        if isinstance(ts, str):
            try: ts = float(ts)
            except Exception: ts = 0
        if ts > 1e12: ts /= 1000.0
        usd = r.get("usd") or r.get("usd_size") or r.get("notional") or r.get("value") or r.get("size_usd") or 0
        try: usd = float(usd)
        except Exception: usd = 0.0
        side = str(r.get("side") or r.get("direction") or r.get("type") or "").lower()
        sym = str(r.get("symbol") or r.get("coin") or r.get("pair") or "").upper()
        out.append(dict(ts=ts, usd=usd, side=side, sym=sym))
    return out

def scan_cascade(rows):
    btc = [r for r in rows if (not r["sym"]) or ("BTC" in r["sym"] or "XBT" in r["sym"] or "BITCOIN" in r["sym"])]
    btc = [r for r in btc if r["ts"] > 0 and r["usd"] > 0]
    btc.sort(key=lambda r: r["ts"])
    now = time.time()
    btc = [r for r in btc if now - r["ts"] <= 620]
    j = 0
    hit = None
    for i in range(len(btc)):
        while btc[i]["ts"] - btc[j]["ts"] > LIQ_WINDOW_S:
            j += 1
        sell = sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("long", "sell", "liquidated_long", "l"))
        buy  = sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("short", "buy", "liquidated_short", "s"))
        if sell >= LIQ_CASCADE_USD and buy < sell / 3:
            hit = (f"cascade SELL/longs ${sell/1e6:.2f}M/{LIQ_WINDOW_S}s", btc[i]["ts"])
        elif buy >= LIQ_CASCADE_USD and sell < buy / 3:
            hit = (f"cascade BUY/shorts ${buy/1e6:.2f}M/{LIQ_WINDOW_S}s", btc[i]["ts"])
    return hit

def parse_imbalance(rows):
    if isinstance(rows, dict):
        bc = (rows.get("by_coin") or {})
        r = bc.get("BTC") or bc.get("BTCUSDT") or bc.get("XBT")
        if r and r.get("buy_volume_usd") is not None and r.get("sell_volume_usd") is not None:
            b, s = float(r["buy_volume_usd"]), float(r["sell_volume_usd"])
            return b / (b + s) if (b + s) else 0.5
        return None
    for r in rows:
        sym = str(r.get("symbol") or r.get("coin") or r.get("pair") or "").upper()
        if "BTC" in sym or "XBT" in sym:
            v = r.get("imbalance") or r.get("imb") or r.get("ratio") or r.get("buy_ratio")
            if v is None and r.get("buy") is not None and r.get("sell") is not None:
                b, s = float(r["buy"]), float(r["sell"])
                v = b / (b + s) if (b + s) else 0.5
            try: return float(v)
            except Exception: return None
    return None

async def moondev_loops():
    last_imb = 0.0
    while True:
        env = load_env()
        key = env.get("MOONDEV_API_KEY", "")
        hdr = {"User-Agent": "Mozilla/5.0", "X-API-Key": key} if key else {"User-Agent": "Mozilla/5.0"}
        try:
            req = urllib.request.Request(LIQ_URL, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read().decode())
            rows = parse_liq_rows(payload)
            if not t.liq_shape_logged and rows:
                t.liq_shape_logged = True
                log(f"liq feed shape: {len(rows)} rows, sample={rows[0]}")
            if rows:
                t.liq_ok_t = time.time()
            trig = scan_cascade(rows)
            if trig and trig[1] > t.liq_trig_ts:
                t.liq_trig_ts = trig[1]
                # A cascade during an armed round pulls maker quotes (whipsaw already moved);
                # outside an armed round it is ITSELF a vol-regime signal (arm box mode).
                if time.time() < t.armed_until:
                    do_pull("LIQ " + trig[0])
                else:
                    arm_box("vol", f"LIQ cascade arms vol-box: {trig[0]}",
                            hold_s=max(CALENDAR_ARM_TAIL_S, 300))
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                if int(time.time()) % 300 < LIQ_POLL_S:
                    log(f"moondev liq {ex.code} — key expired/missing (M7) — FLAT")
            elif ex.code == 429:
                if int(time.time()) % 300 < LIQ_POLL_S:
                    log(f"moondev liq 429 — rate limited, backing off 30s")
                await asyncio.sleep(30)
            else:
                log(f"moondev liq HTTP {ex.code}")
        except Exception as ex:
            log(f"moondev liq error {ex!r}")
        now = time.time()
        if now - last_imb >= IMB_POLL_S:
            last_imb = now
            try:
                req = urllib.request.Request(IMB_URL, headers=hdr)
                with urllib.request.urlopen(req, timeout=10) as r:
                    v = parse_imbalance(json.loads(r.read().decode()))
                if v is not None and (v >= IMB_EXTREME or v <= 1 - IMB_EXTREME):
                    if now < t.armed_until:
                        do_pull(f"IMB_EXTREME {v:.2f}")
                    else:
                        arm_box("vol", f"IMB_EXTREME {v:.2f} arms vol-box", hold_s=300)
            except Exception as ex:
                log(f"moondev imbalance error {ex!r}")
        await asyncio.sleep(LIQ_POLL_S)

# ---------------- box mode + pulls ----------------

def arm_box(source, reason, hold_s=CALENDAR_ARM_TAIL_S + 900):
    """Arm (or re-arm) box mode for hold_s seconds. `source` is 'calendar' or 'vol'."""
    now = time.time()
    new_end = now + hold_s
    # Calendar beats vol for logging; vol extensions stack.
    if new_end > t.armed_until or t.arm_source != source:
        t.armed_until = max(t.armed_until, new_end)
        t.arm_reason = reason
        if t.arm_source != "calendar":     # calendar arms stick for the whole round
            t.arm_source = source
        t.stats["arms"][source] = t.stats["arms"].get(source, 0) + 1
        log(f"🔵 BOX ARMED ({source}) — {reason} | hold {hold_s:.0f}s | armed until "
            f"{datetime.fromtimestamp(t.armed_until, timezone.utc).strftime('%H:%M:%S')}Z")
        save_stats()

def disarm(why=""):
    # 2026-09-26: was `if t.armed_until > time.time()` - both call sites run AFTER expiry, so the
    # DISARMED line never appeared. Log whenever we leave an armed state.
    if t.armed_until or t.arm_source:
        log(f"⬜ BOX DISARMED — {why}")
    t.armed_until = 0
    t.arm_reason = ""
    t.arm_source = ""

def check_vol_gate(now):
    """Trailing-1h sigma > VOL_GATE_MULT x baseline -> arm box mode (cascade regime).
    Also accumulates baseline calibration samples during quiet periods."""
    s = sigma_bps(SIGMA_WINDOW_S)
    if s > 0:
        if s > t.stats.get("sigma_peak_bps", 0.0):
            t.stats["sigma_peak_bps"] = s
        # calibrate baseline during disarmed / non-event periods (Why-now rule: Saturday etc.)
        if now - t.sigma_calib_t > 300:
            t.sigma_calib.append(s)
            if len(t.sigma_calib) > 288:           # last 24h of 5-min samples
                t.sigma_calib = t.sigma_calib[-288:]
            t.sigma_calib_t = now
            # running median baseline — robust to spikes
            if len(t.sigma_calib) >= 6:
                sorted_s = sorted(t.sigma_calib)
                t.stats["sigma_baseline_bps"] = round(sorted_s[len(sorted_s)//2], 2)
        baseline = t.stats.get("sigma_baseline_bps", SIGMA_BASE_BPS)
        if s > VOL_GATE_MULT * baseline and now > t.armed_until:
            arm_box("vol", f"sigma1h={s:.1f}bps > {VOL_GATE_MULT}x baseline {baseline:.1f}bps",
                    hold_s=600)

def do_pull(reason):
    """Cancel resting maker quotes. Does NOT touch taker pair-arb logic (that's a separate
    aggressive action — it only fires when combined ask < cap, never blindly on pull)."""
    now = time.time()
    csv_append(PULLS_CSV, ["ts", "reason", "active_quotes"],
               [int(now), reason,
                sum(1 for r in t.rounds.values()
                    for q in r["quotes"].values()
                    if q["phase"] == "MAKER_RESTING" and q.get("placed"))])
    log(f"PULL MAKERS — {reason}")
    for key, rs in t.rounds.items():
        for side in ("Up", "Down"):
            q = rs["quotes"].get(side)
            if q and q["phase"] == "MAKER_RESTING":
                q["phase"] = "PULLED"
                if q.get("oid"):
                    cancel_order(q["oid"])
    save_stats()

# ---------------- round lifecycle ----------------

def mid_for(rs, side):
    bk = rs.get("book", {}).get(side)
    if not bk: return None
    b, a = bk
    return (b + a) / 2 if (b is not None and a is not None) else (b or a)

def best_ask(rs, side):
    bk = rs.get("book", {}).get(side)
    if not bk: return None
    return bk[1]

def best_bid(rs, side):
    bk = rs.get("book", {}).get(side)
    if not bk: return None
    return bk[0]

def round_tokens(label, start):
    try:
        ev = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}")
        m = ev[0]["markets"][0]
        toks = json.loads(m["clobTokenIds"])
        return {"Up": toks[0], "Down": toks[1], "cond": m.get("conditionId")}
    except Exception:
        return None

def gamma_resolution(label, start):
    for q in ("&closed=true", ""):
        try:
            ev = http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}{q}")
            if not ev: continue
            m = ev[0]["markets"][0]
            pr = [float(x) for x in json.loads(m["outcomePrices"])]
            if m.get("closed") or not m.get("acceptingOrders"):
                return "Up" if pr[0] > 0.5 else "Down"
            if pr[0] >= 0.9995 or pr[0] <= 0.0005:
                return "Up" if pr[0] >= 0.9995 else "Down"
        except Exception:
            pass
    return None

def net_directional_5m(now):
    """Signed naked directional notional per 5m bucket across BOTH series.
    A completed pair is riskless (Up+Down pays $1 regardless) and nets to zero.
    Maker-pending and taker-pending legs count as naked until paired."""
    bucket = (int(now) // 300) * 300
    net = 0.0
    for (label, start), rs in t.rounds.items():
        if start // 300 != bucket // 300:
            continue
        up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
        paired = (up["phase"] in ("PAIRED", "SETTLED") and dn["phase"] in ("PAIRED", "SETTLED")
                  and abs(up["filled"] - dn["filled"]) < 1e-6)
        if paired:
            continue
        for side, q in (("Up", up), ("Down", dn)):
            if q["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED") and not q.get("exited"):
                net += q["filled"] * q.get("avg_px", MAKER_BID) * (1 if side == "Up" else -1)
    return net

def record_maker_fill(rs, side, ts, px, sz, now, live=False):
    """Maker straddle fill. Part-4 gate first. Leg-risk timer starts here."""
    mid = mid_for(rs, side) or 0.50
    ok, reasons = admissible(mid, now)
    q = rs["quotes"].get(side)
    if not q or q["phase"] != "MAKER_RESTING" or not q.get("placed"):
        return
    if ts < q.get("place_ts", 0):
        return                                    # print before the quote existed = phantom (Part-4 class)
    if not ok:
        log(f"❌ MAKER FILL REJECTED (gate) {rs['label']} {side} @{px:.2f} mid={mid:.2f} — {'; '.join(reasons)}")
        return
    take = min(sz, q["rest"])
    if take <= 0:
        return
    delta = take * px * (1 if side == "Up" else -1)
    if abs(net_directional_5m(now) + delta) > NET_CAP_5M:
        log(f"❌ MAKER FILL REJECTED (net cap ${NET_CAP_5M:.0f}/5m) {rs['label']} {side}")
        return
    q["rest"] -= take
    q["filled"] += take
    q["avg_px"] = ((q["avg_px"] * (q["filled"] - take)) + px * take) / q["filled"] if q["filled"] else px
    if q["fill_ts"] is None:
        q["fill_ts"] = ts
    q["phase"] = "MAKER_FILLED"
    t.stats["maker_fills"] += 1
    csv_append(FILLS_CSV,
               ["ts","series","round","side","event","price","shares","live","arm_source"],
               [int(ts), rs["label"], rs["start"], side, "MAKER_FILL", f"{px:.2f}",
                f"{take:.0f}", int(live or q.get("live")), t.arm_source])
    log(f"MAKER FILL {rs['label']} {side} {take:.0f}sh @ {px:.2f} ({'LIVE' if live or q.get('live') else 'DRY'}) "
        f"— 60s leg-risk timer armed")
    check_pair_completion(rs, now)
    save_stats()

def do_pair_arbitrage(rs, now, live=False):
    """(a) Pair arbitrage: if combined ask post-fee < PAIR_ASK_CAP, taker BOTH sides via FOK.
    Sizes to QUOTE_SHARES (same $10/leg incubation as maker leg)."""
    if rs.get("pair_arb_done"):
        return
    a_up = best_ask(rs, "Up")
    a_dn = best_ask(rs, "Down")
    if a_up is None or a_dn is None:
        return
    combined = post_fee_cost(a_up, a_dn)
    if combined >= PAIR_ASK_CAP:
        return
    # Size: min of book liquidity available up to our ask price, capped at QUOTE_SHARES
    sz = QUOTE_SHARES
    # 2026-09-26: a FOK for sz needs sz at the top level on BOTH sides; DRY used to assume it.
    a_sz = rs.get("ask_size", {})
    if min(a_sz.get("Up", 0.0), a_sz.get("Down", 0.0)) < sz:
        if not rs.get("thin_logged"):
            rs["thin_logged"] = True
            log(f"PAIR-ARB THIN {rs['label']}: combined ${combined:.4f} < cap but top-of-book size Up {a_sz.get('Up',0):.0f} / Down {a_sz.get('Down',0):.0f} < {sz}sh — not fired")
        return
    mid = mid_for(rs, "Up") or 0.50
    ok, reasons = admissible(mid, now)
    if not ok:
        return
    if abs(net_directional_5m(now)) > NET_CAP_5M:
        return
    rs["pair_arb_done"] = True    # fire once per round per price level; tape will re-arm on new prints
    expected_pnl = sz * (1.0 - combined)
    log(f"🎯 PAIR-ARB FIRE {rs['label']}: ask Up={a_up:.3f} Down={a_dn:.3f} "
        f"combined post-fee=${combined:.4f} (cap ${PAIR_ASK_CAP}) sz={sz:.0f}sh expect +${expected_pnl:.2f}")
    t.stats["taker_pairs"] += 1
    if live:
        oids = []
        for side, ask in (("Up", a_up), ("Down", a_dn)):
            try:
                resp = post_fok(rs["tokens"][side], ask, sz, "BUY")
                oid = order_id_of(resp)
                oids.append((side, oid))
                log(f"PAIR-ARB TAKER (LIVE) {rs['label']} BUY {side} {sz}sh @ {ask:.3f} -> {resp}")
            except Exception as ex:
                log(f"PAIR-ARB FOK FAILED {side}: {ex!r}")
                # try to cancel / unwind whatever did fill
        # If both FOKs filled -> immediately paired (riskless)
        if len(oids) == 2 and all(o[1] for o in oids):
            rs["quotes"]["Up"].update(dict(phase="PAIRED", filled=sz, avg_px=a_up, fill_ts=now, paired_ts=now))
            rs["quotes"]["Down"].update(dict(phase="PAIRED", filled=sz, avg_px=a_dn, fill_ts=now, paired_ts=now))
            t.stats["pairs_completed"] += 1
            t.stats["gross"] += expected_pnl
            t.stats["pnl"] += expected_pnl
            t.stats["day_pnl"] += expected_pnl
            csv_append(PAIRS_CSV, ["ts","series","round","kind","cost","pnl","shares"],
                       [int(now), rs["label"], rs["start"], "taker_arb", f"{combined:.4f}",
                        f"{expected_pnl:.2f}", f"{sz:.0f}"])
        else:
            # partial fill -> apply leg-risk rule
            log(f"⚠️ PAIR-ARB partial — entering leg-risk unwind flow")
            enter_leg_risk(rs, now)
    else:
        # DRY: assume both FOKs filled at top-of-book (optimistic, for plumbing only)
        rs["quotes"]["Up"].update(dict(phase="PAIRED", filled=sz, avg_px=a_up, fill_ts=now, paired_ts=now))
        rs["quotes"]["Down"].update(dict(phase="PAIRED", filled=sz, avg_px=a_dn, fill_ts=now, paired_ts=now))
        t.stats["pairs_completed"] += 1
        t.stats["gross"] += expected_pnl
        t.stats["pnl"] += expected_pnl
        t.stats["day_pnl"] += expected_pnl
        csv_append(PAIRS_CSV, ["ts","series","round","kind","cost","pnl","shares"],
                   [int(now), rs["label"], rs["start"], "taker_arb_dry", f"{combined:.4f}",
                    f"{expected_pnl:.2f}", f"{sz:.0f}"])
    save_stats()

def check_pair_completion(rs, now):
    """If Up and Down both filled (maker + any chases), mark PAIRED."""
    up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
    up_filled = up["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED", "PAIRED") and up["filled"] > 0
    dn_filled = dn["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED", "PAIRED") and dn["filled"] > 0
    if up["phase"] == "PAIRED" and dn["phase"] == "PAIRED":
        return
    if up_filled and dn_filled:
        matched = min(up["filled"], dn["filled"])
        cost = matched * (up["avg_px"] + dn["avg_px"])
        pnl = matched * (1.0 - (up["avg_px"] + dn["avg_px"]))   # maker pays 0 fee
        up["phase"] = "PAIRED"
        dn["phase"] = "PAIRED"
        up["paired_ts"] = dn["paired_ts"] = now
        t.stats["pairs_completed"] += 1
        t.stats["gross"] += pnl
        t.stats["pnl"] += pnl
        t.stats["day_pnl"] += pnl
        log(f"✅ PAIR COMPLETE {rs['label']}: cost ${(up['avg_px']+dn['avg_px']):.3f} x {matched:.0f}sh = ${cost:.2f}, pnl ${pnl:+.2f}")
        csv_append(PAIRS_CSV, ["ts","series","round","kind","cost","pnl","shares"],
                   [int(now), rs["label"], rs["start"], "maker_straddle",
                    f"{(up['avg_px']+dn['avg_px']):.4f}", f"{pnl:.2f}", f"{matched:.0f}"])
        # cancel any outstanding chase offers
        for s, q in (("Up", up), ("Down", dn)):
            if q.get("chase_oid"):
                cancel_order(q["chase_oid"])
        save_stats()

def enter_leg_risk(rs, now):
    """One leg filled, the other hasn't. Start the 60s clock; when it expires, chase the
    missing leg aggressively up to combined $0.97, else unwind the lone leg at model fair."""
    up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
    if up.get("exited") or dn.get("exited"):
        return
    up_filled = up["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED") and up["filled"] > 0
    dn_filled = dn["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED") and dn["filled"] > 0
    # If both already filled -> pair it
    if up_filled and dn_filled:
        check_pair_completion(rs, now); return
    if not (up_filled or dn_filled):
        return
    # Identify filled leg and missing leg
    if up_filled and not dn_filled:
        filled_side, miss_side = "Up", "Down"
    elif dn_filled and not up_filled:
        filled_side, miss_side = "Down", "Up"
    else:
        return
    fq, mq = rs["quotes"][filled_side], rs["quotes"][miss_side]
    # Start leg-risk clock on first fill
    if not fq.get("leg_risk_ts"):
        fq["leg_risk_ts"] = now
        log(f"⏱️ LEG-RISK START {rs['label']}: {filled_side} filled @ {fq['avg_px']:.3f}, "
            f"waiting up to {LEG_TIMEOUT_S:.0f}s for {miss_side}")
        return
    elapsed = now - fq["leg_risk_ts"]
    if elapsed < LEG_TIMEOUT_S:
        return
    # 60s elapsed without the other leg — chase aggressively
    if not fq.get("chased"):
        fq["chased"] = True
        t.stats["chases"] += 1
        # max we can pay for the missing leg: CHASE_MAX_COST - filled_price
        miss_max_px = CHASE_MAX_COST - fq["avg_px"]
        miss_ask = best_ask(rs, miss_side)
        # 2026-09-26: the chase is a taker order, so its cost is ask + fee (the shipped code compared
        # and booked the raw ask -> pair gains overstated by ~1.7c/sh, which feeds kill rule #2).
        miss_cost = (miss_ask + fee(miss_ask)) if miss_ask is not None else None
        if miss_cost is not None and miss_cost <= miss_max_px:
            # taker the missing leg at best ask (FOK)
            sz = fq["filled"]
            comb_cost = fq["avg_px"] + miss_cost
            log(f"🏃 CHASE FILL {rs['label']} {miss_side} @ {miss_ask:.3f} (+fee -> {miss_cost:.4f}; combined ${comb_cost:.3f} ≤ ${CHASE_MAX_COST})")
            mq.update(dict(phase="CHASE_FILLED", filled=sz, avg_px=miss_cost, fill_ts=now, chased=True))
            t.stats["maker_fills"] += 1
            csv_append(FILLS_CSV,
                       ["ts","series","round","side","event","price","shares","live","arm_source"],
                       [int(now), rs["label"], rs["start"], miss_side, "CHASE_FILL", f"{miss_ask:.3f}",
                        f"{sz:.0f}", int(mq.get("live")), t.arm_source])
            check_pair_completion(rs, now)
            return
        # Cannot chase under $0.97 combined -> UNWIND the lone leg at model fair
        unwind_lone_leg(rs, filled_side, fq, now)

def unwind_lone_leg(rs, side, q, now):
    """Unwind a naked event position. Taker-sell at best bid; if best bid doesn't exist,
    post a GTC at EST_FAIR (0.50) — never carry naked event risk (S10 rule)."""
    if q.get("exited"):
        return
    bid = best_bid(rs, side)
    if bid is None:
        bid = EST_FAIR     # model fair
    fee_cost = fee(bid) if bid < 1.0 else 0.0
    pnl = q["filled"] * ((bid - q["avg_px"]) - fee_cost)
    q["exited"] = True
    q["exit_px"] = bid
    q["exit_pnl"] = pnl
    q["phase"] = "EXITED"
    t.stats["unwind_loss"] += (-pnl if pnl < 0 else 0.0)
    t.stats["pnl"] += pnl
    t.stats["day_pnl"] += pnl
    # Track kill-test: lone-leg unwind losses vs pair gains across events
    if pnl < 0:
        t.stats["kill_test"]["events_unwind_loss"] += -pnl
    log(f"🚪 UNWIND {rs['label']} {side} {q['filled']:.0f}sh: sell @ {bid:.3f} (entry {q['avg_px']:.3f}) pnl ${pnl:+.2f} — naked event position exited")
    csv_append(FILLS_CSV,
               ["ts","series","round","side","event","price","shares","live","arm_source"],
               [int(now), rs["label"], rs["start"], side, "UNWIND_SELL", f"{bid:.3f}",
                f"{q['filled']:.0f}", int(q.get("live")), t.arm_source])
    # cancel the maker quote on the other side (if any still resting)
    other = "Down" if side == "Up" else "Up"
    oq = rs["quotes"][other]
    if oq.get("phase") == "MAKER_RESTING" and oq.get("oid"):
        oq["phase"] = "PULLED"
        cancel_order(oq["oid"])
        log(f"pulling maker quote on {other} after unwind")
    # daily stop check
    if t.stats["day_pnl"] <= -DAILY_STOP and load_env().get("LIVE_TRADING") == "1":
        open(KILL, "w").write(f"daily stop {t.stats['day_pnl']:.2f} at {datetime.now(timezone.utc).isoformat()}\n")
        log(f"🛑 DAILY STOP ${t.stats['day_pnl']:.2f} — KILL written, flat")
    elif t.stats["day_pnl"] <= -DAILY_STOP and not t.stats.get("dry_stop_logged") == t.day:
        t.stats["dry_stop_logged"] = t.day
        log(f"DRY daily stop level reached (${t.stats['day_pnl']:.2f}) — simulated P&L, no KILL written")
    save_stats()

def place_maker_straddle(rs, now, live):
    """(b) Maker straddle: post BUY Up @ MAKER_BID + BUY Down @ MAKER_BID."""
    reasons = hard_gates(now)
    if reasons:
        rs["skip_reason"] = "; ".join(reasons)
        if not rs.get("skip_logged"):
            rs["skip_logged"] = True
            log(f"SKIP {rs['label']} {rs['start']}: {rs['skip_reason']}")
        return
    mid = mid_for(rs, "Up") or mid_for(rs, "Down") or 0.50
    if not box_mode_ok(mid):
        rs["skip_reason"] = f"mid {mid:.2f} outside box band"
        if not rs.get("skip_logged"):
            rs["skip_logged"] = True
            log(f"SKIP {rs['label']} {rs['start']}: {rs['skip_reason']}")
        return
    window_end = rs["start"] + (QUOTE_END_FRAC_15 if rs["T"] == 900 else QUOTE_END_FRAC_5) * rs["T"]
    if now > window_end:
        return
    for side in ("Up", "Down"):
        q = rs["quotes"][side]
        if q["phase"] != "MAKER_RESTING" or q["placed"]:
            continue
        q["placed"] = True
        q["place_ts"] = now
        t.stats["maker_quotes"] = t.stats.get("maker_quotes", 0) + 1
        if live:
            try:
                resp = post_gtc(rs["tokens"][side], MAKER_BID, QUOTE_SHARES, "BUY")
                q["oid"] = order_id_of(resp)
                q["live"] = True
                log(f"MAKER (LIVE) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {MAKER_BID} -> {resp}")
            except Exception as ex:
                log(f"MAKER QUOTE FAILED -> dry: {ex!r}")
                log(f"MAKER (DRY) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {MAKER_BID}")
        else:
            log(f"MAKER (DRY) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {MAKER_BID}")
    save_stats()

async def tape_loop():
    """Public prints -> fill detection (DRY+LIVE); also runs pair-arb sniff every tick."""
    while True:
        try:
            now = time.time()
            armed = now < t.armed_until
            for (label, start), rs in list(t.rounds.items()):
                if rs["T"] and now > rs["start"] + rs["T"] + 120:
                    continue
                if not rs.get("tokens") or not rs["quotes"]["Up"].get("placed"):
                    continue
                # pair-arb sniff (a) only while armed
                if armed and rs["tokens"]:
                    do_pair_arbitrage(rs, now,
                                      live=(not stats_gates()) or load_env().get("FORCE_LIVE") == "1")
                # public tape scan for maker-fill simulation
                rows = http_json(f"{DATA}/trades?market={rs['tokens']['cond']}&limit=200")
                for r in rows or []:
                    ts = int(r.get("timestamp", 0))
                    if ts <= rs.get("tape_t", rs["start"]):
                        continue
                    out = r.get("outcome") or ("Up" if r.get("outcomeIndex") == 0 else "Down")
                    px, sz = float(r["price"]), float(r["size"])
                    # maker fill: someone sells at or below our bid
                    if r.get("side") == "SELL" and px <= MAKER_BID:
                        record_maker_fill(rs, out, ts, MAKER_BID, sz, now)
                if rows:
                    rs["tape_t"] = max(int(r["timestamp"]) for r in rows)
                # leg-risk rule runs continuously on this round
                enter_leg_risk(rs, now)
        except Exception as ex:
            log(f"tape error {ex!r}")
        await asyncio.sleep(1.5)

def settle_round(rs, now):
    settled = gamma_resolution(rs["label"], rs["start"])
    if not settled:
        return False
    up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
    paired = (up["phase"] == "PAIRED" and dn["phase"] == "PAIRED")
    exited_both = (up.get("exited") and dn.get("exited"))
    pnl = 0.0
    note = ""
    two_leg = False
    if paired:
        two_leg = True
        matched = min(up["filled"], dn["filled"])
        p = matched * (1.0 - (up["avg_px"] + dn["avg_px"]))
        pnl += p
        note = f"pair {matched:.0f}sh cost ${(up['avg_px']+dn['avg_px']):.3f} +${p:.2f}"
        t.stats["pairs_won"] += 1   # pairs always pay $1
        t.stats["kill_test"]["events_pair_gain"] += p
    for side, q in (("Up", up), ("Down", dn)):
        if q["phase"] in ("MAKER_FILLED", "TAKER_FILLED", "CHASE_FILLED") and not q.get("exited"):
            # Shouldn't happen (leg-risk should unwind), but catch residual
            win = (side == settled)
            p = q["filled"] * (1.0 - q["avg_px"] if win else -q["avg_px"])
            pnl += p
            note += f" RESIDUAL-{side}->{'WIN' if win else 'LOSS'} "
    # kill-test: this was an armed event. 2026-09-26: only the 15m round counts as an EVENT
    # (one release = one 15m round); the 5m rounds inside it are secondary and would have counted
    # 3-4 "events" per release, reaching the 20-event verdict after ~5 releases.
    if rs.get("was_armed_event"):
        if rs["T"] == 900:
            t.stats["kill_test"]["events_armed"] += 1
            if two_leg:
                t.stats["kill_test"]["events_two_leg"] += 1
        else:
            t.stats["kill_test"]["rounds_5m_armed"] = t.stats["kill_test"].get("rounds_5m_armed", 0) + 1
            if two_leg:
                t.stats["kill_test"]["rounds_5m_two_leg"] = t.stats["kill_test"].get("rounds_5m_two_leg", 0) + 1
    t.stats["day_pnl"] = t.stats.get("day_pnl", 0.0) + pnl
    t.stats["pnl"] = t.stats.get("pnl", 0.0) + pnl
    t.stats["n"] = t.stats.get("n", 0) + 1
    log(f"ROUND-RESULT {rs['label']} {rs['start']}: settled={settled} two-leg={int(two_leg)} "
        f"pnl ${pnl:+.2f} day ${t.stats['day_pnl']:+.2f} | {note}")
    save_stats()
    return True

def save_stats():
    with open(STATS, "w") as f:
        json.dump(t.stats, f, indent=1)

async def tick_loop():
    while True:
        try:
            if time.time() - t.hb_t >= 60:
                t.hb_t = time.time()
                t.stats["tick_ts"] = int(t.hb_t)
                save_stats()
            now = time.time()
            # 2026-09-26: SKIP logs once per round, so a KILL file dropped mid-round left no trace in the
            # log (the guide expects to see it). Log the KILL state transitions explicitly.
            k = os.path.exists(KILL)
            if k != getattr(t, "kill_seen", None):
                if getattr(t, "kill_seen", None) is not None or k:
                    log("🛑 KILL file present — FLAT (hard gate)" if k else "🔔 KILL file removed — gates re-evaluated")
                t.kill_seen = k
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if day != t.stats.get("day"):
                t.stats["day"] = day
                t.stats["day_pnl"] = 0.0
            # Calendar gate check (runs every tick — cheap)
            cal_armed, cal_reason = calendar_armed(now)
            if cal_armed and t.arm_source != "calendar":
                arm_box("calendar", cal_reason,
                        hold_s=900 + CALENDAR_ARM_LEAD_S + CALENDAR_ARM_TAIL_S)
            elif not cal_armed and t.arm_source == "calendar" and now > t.armed_until:
                disarm("calendar window passed")
            # Vol gate runs from RTDS callbacks (check_vol_gate) for efficiency, but we
            # do a slow cadence check here as a belt-and-braces.
            if now % 30 < 2:
                check_vol_gate(now)
            # Disarm cleanly once hold_s expires (if not re-armed by a fresh cascade)
            if now > t.armed_until and (t.armed_until != 0):
                disarm("hold_s expired without new signal")
            live_hard = hard_gates(now)
            live_stat = stats_gates()
            forced = load_env().get("FORCE_LIVE") == "1"
            live_ok = (not live_hard) and (forced or not live_stat)
            for Tsec, label in ROUNDS:
              # 2026-09-26: also create the NEXT round CALENDAR_ARM_LEAD_S early, so the documented
              # 30 s pre-arm quotes can exist (before, `e` was never negative and pre-arm was dead code).
              cur = int(now // Tsec) * Tsec
              for start in ((cur, cur + Tsec) if (cur + Tsec - now) <= CALENDAR_ARM_LEAD_S else (cur,)):
                key = (label, start)
                rs = t.rounds.setdefault(key, dict(
                    label=label, T=Tsec, start=start, tokens=None, book={},
                    quotes={s: dict(phase="MAKER_RESTING", placed=False, rest=QUOTE_SHARES,
                                    filled=0.0, avg_px=MAKER_BID, fill_ts=None,
                                    leg_risk_ts=None, place_ts=None, oid=None,
                                    live=False, exited=False, chased=False,
                                    paired_ts=None, chase_oid=None)
                            for s in ("Up", "Down")},
                    tape_t=start, settled=False, skip_reason="",
                    pair_arb_done=False, was_armed_event=False))
                e = now - start
                if rs["tokens"] is None and e >= -CALENDAR_ARM_LEAD_S:
                    rs["tokens"] = round_tokens(label, start)
                # tag rounds that were entered under box mode
                if now < t.armed_until:
                    rs["was_armed_event"] = True
                if rs["tokens"]:
                    for side in ("Up", "Down"):
                        try:
                            bk = http_json(f"https://clob.polymarket.com/book?token_id={rs['tokens'][side]}")
                            bu = max((float(x["price"]) for x in bk.get("bids", [])), default=None)
                            au = min((float(x["price"]) for x in bk.get("asks", [])), default=None)
                            rs["book"][side] = (bu, au)
                            rs.setdefault("ask_size", {})[side] = sum(float(x["size"]) for x in bk.get("asks", []) if float(x["price"]) == au) if au is not None else 0.0
                        except Exception:
                            pass
                    mid = mid_for(rs, "Up") or mid_for(rs, "Down") or 0.50
                    if any(q["phase"] == "MAKER_RESTING" and q["placed"] for q in rs["quotes"].values()):
                        if not box_mode_ok(mid):
                            do_pull(f"BAND_EXIT mid={mid:.2f} {rs['label']}")
                # place maker straddle once the window opens
                window_end_frac = QUOTE_END_FRAC_15 if Tsec == 900 else QUOTE_END_FRAC_5
                if (-CALENDAR_ARM_LEAD_S <= e <= window_end_frac * Tsec
                        and not any(q["placed"] for q in rs["quotes"].values())
                        and now < t.armed_until):
                    place_maker_straddle(rs, now, live_ok)
                elif 0 <= e <= window_end_frac * Tsec and now >= t.armed_until and not rs.get("skip_logged"):
                    # 2026-09-26: disarmed rounds were silent (the straddle call is gated on armed), so the
                    # log could not show that the box was alive but flat. One line per round, like S3/S6.
                    rs["skip_logged"] = True; rs["skip_reason"] = "box mode disarmed (no event / no vol)"
                    log(f"SKIP {rs['label']} {rs['start']}: {rs['skip_reason']}")
                # window closed: cancel any resting maker quotes
                if e >= window_end_frac * Tsec:
                    for side in ("Up", "Down"):
                        q = rs["quotes"][side]
                        if q["phase"] == "MAKER_RESTING" and q["placed"]:
                            q["phase"] = "PULLED"
                            if q.get("oid"):
                                cancel_order(q["oid"])
                                log(f"CLOSE {rs['label']} {side} maker quote cancelled at window end")
            # settle FINISHED rounds from the round table, retry every 30 s until gamma reports
            # (2026-09-26: the shipped check ran on the CURRENT round only, so `e > Tsec + 60` was
            #  never true and no round could ever settle -> kill-test counters stayed at 0 forever)
            for key, rs in list(t.rounds.items()):
                if rs["settled"] or not rs.get("tokens"): continue
                if now - rs["start"] > rs["T"] + 60 and now >= rs.get("settle_next", 0):
                    rs["settle_next"] = now + 30
                    rs["settled"] = settle_round(rs, now)
            # GC old rounds
            for key in [k for k, r in t.rounds.items() if now - k[1] > 2400]:
                del t.rounds[key]
        except Exception as ex:
            log(f"tick error {ex!r}")
        await asyncio.sleep(2.0)

def calibrate_baseline(minutes=60):
    """Calibrate the vol-gate baseline by sitting on the feed for `minutes` and printing
    1h-realized sigma percentiles. This is the Saturday / release-free baseline run
    (Why-now grounding in the playbook)."""
    log(f"=== S10 VOL-GATE CALIBRATION MODE — sitting {minutes}min, collecting spot samples ===")
    async def _run():
        rtds_task = asyncio.create_task(rtds())
        end = time.time() + minutes * 60
        try:
            while time.time() < end:
                await asyncio.sleep(5)
                s1h = sigma_bps(3600)
                s5m = sigma_bps(300)
                print(f"  [calib +{(time.time()-(end-minutes*60)):.0f}s] "
                      f"n={len(t.samples):4d}  sigma1h={s1h:5.1f}bps  sigma5m={s5m:5.1f}bps  "
                      f"gate fires at >{VOL_GATE_MULT}x baseline", flush=True)
        finally:
            rtds_task.cancel()
        # final summary
        if len(t.samples) > 60:
            vals = [v for _, v in t.samples]
            diffs = [b-a for a, b in zip(vals, vals[1:])]
            s1 = math.sqrt(sum((x - sum(diffs)/len(diffs))**2 for x in diffs)/len(diffs))
            s5m_bps = (s1*math.sqrt(300)) / vals[-1] * 1e4
            s1h_bps = s5m_bps * math.sqrt(12)   # 1h = 12 x 5m bars (2026-09-26: was divided)
            print()
            print(f"Baseline sigma_5m  = {s5m_bps:.2f} bps")
            print(f"Baseline sigma_1h  = {s1h_bps:.2f} bps (scaled)")
            print(f"Vol gate threshold = {VOL_GATE_MULT * s5m_bps:.2f} bps (1.8x 5m baseline)")
            print(f"SIGMA_BASE_BPS suggested value: {s5m_bps:.1f}")
    asyncio.run(_run())

def preflight():
    print("=" * 72)
    print("S10 VOL-EVENT BINARY BOX — preflight (read-only, no orders)  "
          + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    env = load_env()
    now = time.time()
    for Tsec, label in ROUNDS:
        start = int(now // Tsec) * Tsec
        toks = round_tokens(label, start)
        print(f"{label}: tokens {'OK' if toks else 'MISSING'} for current round {start}")
    cal_armed, cal_reason = calendar_armed(now)
    print(f"calendar gate: {'ARMED — ' + cal_reason if cal_armed else 'clear (no release in current 15m round)'}")
    s = sigma_bps(SIGMA_WINDOW_S)
    baseline = t.stats.get("sigma_baseline_bps", SIGMA_BASE_BPS)
    print(f"vol gate: sigma_1h={s:.1f}bps baseline={baseline:.1f}bps threshold={VOL_GATE_MULT*baseline:.1f}bps "
          f"({'ARMED' if s > VOL_GATE_MULT*baseline else 'clear'})")
    print(f"box band: {BAND} (fills outside FAIL LOUDLY per Part-4)")
    print(f"pair-arb cap: ${PAIR_ASK_CAP} post-fee; maker straddle @ {MAKER_BID} both sides")
    print(f"leg-risk rule: {LEG_TIMEOUT_S:.0f}s — chase up to ${CHASE_MAX_COST} combined, else unwind")
    print(f"moondev key present: {'yes' if env.get('MOONDEV_API_KEY') else 'NO (liq/imb pulls dead -> FLAT via stale-switch)'}")
    print(f"live keys present:   {'yes' if env.get('POLY_PRIVATE_KEY') and env.get('POLY_FUNDER') else 'no (DRY only)'}")
    print(f"LIVE_TRADING: {env.get('LIVE_TRADING', '0')}  FORCE_LIVE: {env.get('FORCE_LIVE', '0')}")
    print(f"KILL file: {'PRESENT — FLAT' if os.path.exists(KILL) else 'absent'}")
    et_min, fb = et_minute_of_day(now)
    print(f"ET wallclock: {et_min//60:02d}:{et_min%60:02d}{' (EST fallback — install tzdata for EDT accuracy)' if fb else ''}")
    r = hard_gates(now)
    print(f"hard gates: {'MET — box may fire if armed' if not r else 'NOT MET -> ' + '; '.join(r)}")
    # Kill-test status
    kt = t.stats.get("kill_test", {})
    n_evt = kt.get("events_armed", 0)
    n_two = kt.get("events_two_leg", 0)
    print(f"kill-test: armed_events={n_evt} two_leg_rate={(n_two/n_evt*100 if n_evt else 0):.0f}% "
          f"(kill <30%) | unwind_loss=${kt.get('events_unwind_loss',0):.2f} "
          f"pair_gain=${kt.get('events_pair_gain',0):.2f}")
    print("=" * 72)

async def main_async(minutes=None):
    log(f"S10 vol-event binary box starting ({'BOUNDED '+str(minutes)+'min' if minutes else 'forever'}) "
        f"— maker straddle @ {MAKER_BID} x {QUOTE_SHARES}sh, pair-arb cap ${PAIR_ASK_CAP} post-fee, "
        f"leg-risk timeout {LEG_TIMEOUT_S:.0f}s chase-to-${CHASE_MAX_COST}-else-unwind, "
        f"vol gate {VOL_GATE_MULT}x baseline, band {BAND}")
    # Do an initial calendar check at startup
    cal_armed, cal_reason = calendar_armed()
    if cal_armed:
        arm_box("calendar", cal_reason)
    tasks = [asyncio.create_task(rtds()),
             asyncio.create_task(moondev_loops()),
             asyncio.create_task(tape_loop()),
             asyncio.create_task(tick_loop())]
    if minutes:
        await asyncio.sleep(minutes * 60)
        for task in tasks:
            task.cancel()
    else:
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=None)
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--calibrate", action="store_true",
                    help="Sit on RTDS for --minutes and report vol baseline (Saturday/release-free calibration run)")
    args = ap.parse_args()
    if args.preflight:
        preflight()
        sys.exit(0)
    if args.calibrate:
        calibrate_baseline(args.minutes or 60)
        sys.exit(0)
    try:
        asyncio.run(main_async(args.minutes))
    except KeyboardInterrupt:
        log("interrupted — flat")
