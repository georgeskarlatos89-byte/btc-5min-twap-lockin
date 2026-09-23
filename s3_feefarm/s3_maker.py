#!/usr/bin/env python3
"""
S3 FEE-FARM TWO-SIDED MAKER — incubation bot, gated by code (P5).

Strategy source: BTC-5m-15m-POLYMARKET-10-STRATEGIES.MD -> S3, tuned by the B step
(s3_backtest_report.md — "run 25 versions in an afternoon"). "Get paid by the 1.75%
the gamblers pay." Quote a BUY at **0.45** on BOTH the Up and the Down token of
every BTC 5m/15m round, 50 shares each (liquidity-rewards minimum), from t+30s
until 60% of the round has elapsed. Makers pay 0%; a completed pair costs 0.90 and
pays 1.00. NOTE: the doc's 0.49/0.49 + sit-at-breakeven version is KILLED by the
backtest (rank 31/32, net -$781/24h); the surviving variant is bid 0.45 + cut
naked legs at 20s (positive in both 12h halves). Method over parameters (P1/P4).

PULL BOTH QUOTES INSTANTLY when any of:
  (a) Moon Dev `all_liq_10m` prints a one-sided cascade >= $1.5M in any 10s window
      (the 1s-median-lag multi-exchange feed — the ONLY liq feed allowed intra-round;
      `binance_liq_10m` is 163s stale and is BANNED here, see knowledge base 5b);
  (b) spot moves >= 6 bps within 10s (RTDS chainlink spot stream);
  (c) orderflow imbalance flips extreme (Moon Dev imbalance, 90s background cadence).

INVENTORY RULE (never "hope" a position — P5): one side filled and the other not
within 20s -> EXIT_MODE decides: "cut20" (the B-step winner) takes the bid at
fill+20s immediately; "offer49" (doc-literal) rests a breakeven offer AT COST until
quote end then takes the bid; "chase" rests at cost 15s more then cuts. Pair
completed -> hold to resolution (0.45x2 = 0.90 cost -> +$0.10/sh).

PART-4 RULE (encoded, not intended): the live-band gate (mid in [0.45, 0.55]) and
every other gate execute BEFORE any fill is ever recorded. A fill logged outside
the band is a bug and FAILS LOUDLY. DRY runs the exact same gates as LIVE.

SESSION GATE (S8 not built yet — carried here, P5): quiet-regime hours only
(Asia 00:00-12:00 UTC + dead US lunch 16:00-18:00 UTC; all-day weekends). A
realized-vol override (trailing 5m sigma > 1.5x the 7bps baseline) flattens.

RISK: 50sh/side (~$24.5 at 0.49); max $30 NET directional per 5-min window across
both series; daily stop $20 -> KILL file + flatten; feed-health flat-switch: if the
Moon Dev liq feed goes stale/empty/401 the strategy goes flat (you cannot pull on
what you cannot see). DRY unless .env says LIVE_TRADING=1 and every gate passes.

Outputs: s3_maker.log, s3_fills.csv, s3_pulls.csv, s3_stats.json.
Usage:  python3 s3_feefarm/s3_maker.py [--minutes 30] [--preflight]
"""
import argparse, asyncio, csv, json, math, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
KILL = os.path.join(HERE, "KILL")
LOG = os.path.join(HERE, "s3_maker.log")
FILLS_CSV = os.path.join(HERE, "s3_fills.csv")
PULLS_CSV = os.path.join(HERE, "s3_pulls.csv")
STATS = os.path.join(HERE, "s3_stats.json")

# ---- strategy constants (S3 mechanics + B-step survivor; mirrors s3_backtest.py) ----
QUOTE_PRICE    = 0.45    # B-step survivor (doc said 0.49: that version ranks 31/32)
QUOTE_SHARES   = 50      # >=50sh = liquidity-rewards min. Doc also says "$10/side" — set 20 for that.
QUOTE_START_S  = 30
QUOTE_END_FRAC = 0.60
BAND = (0.45, 0.55)       # the 45-55 band — the live gate, BEFORE any fill (Part-4)
INV_GRACE_S = 20
EXIT_MODE     = "cut20"   # "cut20" (winner) | "offer49" (doc-literal) | "chase"
BREAKEVEN     = QUOTE_PRICE  # offer-at-COST (never a fixed 0.49 — free-money bug guard)
# ---- pull triggers ----
LIQ_URL       = "https://api.moondev.com/api/all_liquidations/10m.json"  # all_liq_10m: MEDIAN 1s lag
LIQ_POLL_S    = 2.0       # cascade detection must feel "instant" vs the 10s window
LIQ_CASCADE_USD = 1_500_000.0
LIQ_WINDOW_S  = 10
IMB_URL       = "https://api.moondev.com/api/imbalance/1h.json"          # ~90s cadence feed
IMB_POLL_S    = 90.0
IMB_EXTREME   = 0.85
SPOT_MOVE_BPS = 6.0
SPOT_WINDOW_S = 10.0
REQUOTE_LOCK_S = 60.0     # after a pull: no new quotes for 60s
FEED_STALE_S  = 60.0      # Moon Dev liq feed stale -> FLAT (M3/M4/M7 spirit)
VOL_MULT      = 1.5       # trailing 5m sigma > 1.5x baseline -> flat (S8 override, encoded here)
SIGMA_BASE_BPS = 7.0
# ---- risk (Part-3 global rules) ----
NET_CAP_5M    = 30.0      # max $30 net directional across BOTH markets per 5m window
DAILY_STOP    = 20.0
# ---- session gate (S8 pending) ----
QUIET_UTC = [(0, 12), (16, 18)]   # Asia block + dead US lunch
WEEKEND_ALL_DAY = True
# ---- market plumbing (copied from s1_harness/trader.py — execution plumbing only) ----
RTDS = "wss://ws-live-data.polymarket.com"
SYMBOL = "btc/usd"
DATA = "https://data-api.polymarket.com"
ROUNDS = [(300, "5m"), (900, "15m")]

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def fee(p): return 0.07 * p * (1 - p)          # taker only; makers pay 0

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

def quiet_session(now=None):
    dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    if WEEKEND_ALL_DAY and dt.weekday() >= 5:
        return True, "weekend"
    h = dt.hour
    for a, b in QUIET_UTC:
        if a <= h < b:
            return True, f"quiet {a}-{b} UTC"
    return False, f"active session {h} UTC"

class T:
    def __init__(self):
        self.samples = deque(maxlen=3600)      # (ts, spot)
        self.last_update = 0.0
        self.rounds = {}                       # (label, start) -> round state
        self.pulled_until = 0.0                # global requote lock
        self.pull_reason = ""
        self.liq_ok_t = 0.0                    # last healthy liq poll
        self.liq_shape_logged = False
        self.day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.day_pnl = 0.0
        self.stats = json.load(open(STATS)) if os.path.exists(STATS) else {
            "n": 0, "quotes": 0, "fills": 0, "pairs": 0, "wins": 0,
            "pnl": 0.0, "gross": 0.0, "adverse": 0.0,
            "pulls": {}, "day": self.day, "day_pnl": 0.0}
        self.client = None

t = T()

# ---------------- gates (every one of them runs BEFORE a fill is recorded) ----------------

def gates(now):
    """HARD gates — never bypassable. Returns list of reasons (empty = all clear)."""
    reasons = []
    if os.path.exists(KILL):
        reasons.append("KILL file present")
    if time.time() - t.last_update > 10:
        reasons.append("feed stale")
    if now < t.pulled_until:
        reasons.append(f"pulled: {t.pull_reason}")
    ok, why = quiet_session(now)
    if not ok:
        reasons.append(f"session: {why}")
    if time.time() - t.liq_ok_t > FEED_STALE_S:
        reasons.append("moondev liq feed stale/flat-switch")
    if sigma5m_bps() > VOL_MULT * SIGMA_BASE_BPS:
        reasons.append(f"vol override: sigma {sigma5m_bps():.1f}bps > {VOL_MULT}x base")
    if t.stats.get("day_pnl", 0.0) <= -DAILY_STOP:
        reasons.append(f"daily stop {t.stats['day_pnl']:.2f}")
    return reasons

def stats_gates():
    """STATISTICAL gates — bypassable only by explicit FORCE_LIVE=1."""
    reasons = []
    env = load_env()
    if env.get("LIVE_TRADING") != "1":
        reasons.append("LIVE_TRADING!=1")
    if not env.get("POLY_PRIVATE_KEY") or not env.get("POLY_FUNDER"):
        reasons.append("no key/funder in .env")
    return reasons

def band_ok(mid):
    return BAND[0] <= mid <= BAND[1]

def admissible(mid, now):
    """THE Part-4 gate. Call BEFORE recording any fill. Returns (ok, reasons).
    A fill recorded with ok=False is the +5.71pp bug — fail loudly."""
    r = gates(now)
    if not band_ok(mid):
        r.append(f"mid {mid:.2f} outside band {BAND}")
    return (len(r) == 0, r)

# ---------------- execution plumbing (copied from trader.py — strategy untouched elsewhere)

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
    """The CLOB POST /order reply is {success, errorMsg, orderID, ...}; keep only the id."""
    if isinstance(resp, dict):
        return resp.get("orderID") or resp.get("orderId") or resp.get("id")
    return resp

def cancel_order(oid):
    # 2026-09-24 (live-path fix, verified against the v2 client on the fleet box):
    # ClobClient has NO .cancel(order_id=) - it has cancel_orders(order_hashes: list). As shipped
    # every cancel would have failed and a resting GTC quote could never be pulled.
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

def sigma5m_bps():
    """Realized 5m sigma from 1s spot diffs (scaled), knowledge-base style."""
    vals = [v for _, v in list(t.samples)[-600:]]
    if len(vals) < 60:
        return 0.0
    d = [b - a for a, b in zip(vals, vals[1:])]
    m = sum(d) / len(d)
    s1 = math.sqrt(sum((x - m) ** 2 for x in d) / len(d))
    return (s1 * math.sqrt(300)) / (vals[-1] or 8e4) * 1e4

async def rtds():
    """Chainlink spot via RTDS — pull trigger (b) + vol override. Stall watchdog (Part #23)."""
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
                        # pull trigger (b): spot >= 6 bps within 10s
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
    """Defensive row parser — log the first raw row so the shape is verifiable live."""
    # 2026-09-24 (S3 record Part #1): the live feed nests rows under "liquidations" (verified with
    # the key: {'window', 'exchanges', ..., 'liquidations': list[180]}). Without this key the
    # parser returned [] -> "200-but-empty" -> the strategy would have stayed flat forever.
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
    """(a) one-sided cascade >= $1.5M in any 10s window on BTC. -> side or None.
    Long liquidations = forced sells (down pressure); shorts = forced buys (up)."""
    btc = [r for r in rows if (not r["sym"]) or ("BTC" in r["sym"] or "XBT" in r["sym"] or "BITCOIN" in r["sym"])]
    btc = [r for r in btc if r["ts"] > 0 and r["usd"] > 0]
    btc.sort(key=lambda r: r["ts"])
    now = time.time()
    btc = [r for r in btc if now - r["ts"] <= 620]           # the 10m rolling window
    j = 0
    for i in range(len(btc)):
        while btc[i]["ts"] - btc[j]["ts"] > LIQ_WINDOW_S:
            j += 1
        sell = sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("long", "sell", "liquidated_long", "l"))
        buy  = sum(r["usd"] for r in btc[j:i+1] if r["side"] in ("short", "buy", "liquidated_short", "s"))
        if sell >= LIQ_CASCADE_USD and buy < sell / 3:
            return f"cascade SELL/longs ${sell/1e6:.2f}M/{LIQ_WINDOW_S}s"
        if buy >= LIQ_CASCADE_USD and sell < buy / 3:
            return f"cascade BUY/shorts ${buy/1e6:.2f}M/{LIQ_WINDOW_S}s"
    return None

def parse_imbalance(rows):
    # 2026-09-24: the live /imbalance/1h.json is a DICT {updated_at, time_window, overall,
    # by_coin:{BTC:{buy_volume_usd, sell_volume_usd, ...}}}, not a list of rows - iterating it
    # raised AttributeError every 90 s and trigger (c) could never fire. Buy share in [0,1]:
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
    """Pull triggers (a) + (c) + feed-health flat-switch (M3/M4/M7 spirit)."""
    last_imb = 0.0
    imb_state = {"v": None}
    while True:
        env = load_env()
        key = env.get("MOONDEV_API_KEY", "")
        hdr = {"User-Agent": "Mozilla/5.0", "X-API-Key": key} if key else {"User-Agent": "Mozilla/5.0"}
        # --- (a) liq cascade, fast cadence ---
        try:
            req = urllib.request.Request(LIQ_URL, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read().decode())
            rows = parse_liq_rows(payload)
            if not t.liq_shape_logged and rows:
                t.liq_shape_logged = True
                log(f"liq feed shape: {len(rows)} rows, sample={rows[0]}")
            if rows:                      # M3: 200-but-empty = unhealthy
                t.liq_ok_t = time.time()
            trig = scan_cascade(rows)
            if trig:
                do_pull("LIQ " + trig)
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                if int(time.time()) % 300 < LIQ_POLL_S:
                    log(f"moondev liq {ex.code} — key expired/missing (M7) — FLAT")
            elif ex.code == 429:
                if int(time.time()) % 300 < LIQ_POLL_S:
                    log(f"moondev liq 429 — rate limited (no key?), backing off 30s")
                await asyncio.sleep(30)
            else:
                log(f"moondev liq HTTP {ex.code}")
        except Exception as ex:
            log(f"moondev liq error {ex!r}")
        # --- (c) imbalance, 90s background cadence ---
        now = time.time()
        if now - last_imb >= IMB_POLL_S:
            last_imb = now
            try:
                req = urllib.request.Request(IMB_URL, headers=hdr)
                with urllib.request.urlopen(req, timeout=10) as r:
                    v = parse_imbalance(json.loads(r.read().decode()))
                imb_state["v"] = v
                if v is not None and (v >= IMB_EXTREME or v <= 1 - IMB_EXTREME):
                    do_pull(f"IMB_EXTREME {v:.2f}")
            except Exception as ex:
                log(f"moondev imbalance error {ex!r}")
        await asyncio.sleep(LIQ_POLL_S)

def do_pull(reason):
    """Pull BOTH quotes instantly + lock requotes (S3 pull rule)."""
    now = time.time()
    if now < t.pulled_until and reason.split()[0] in t.pull_reason:
        return                                     # same trigger still firing
    t.pulled_until = now + REQUOTE_LOCK_S
    t.pull_reason = reason
    t.stats["pulls"][reason.split()[0]] = t.stats["pulls"].get(reason.split()[0], 0) + 1
    csv_append(PULLS_CSV, ["ts", "reason", "active_rounds"],
               [int(now), reason, len([1 for r in t.rounds.values() if r["live_quotes"]])])
    log(f"PULL BOTH — {reason}")
    for key, rs in t.rounds.items():
        for side in ("Up", "Down"):
            q = rs["quotes"].get(side)
            if q and q["phase"] == "RESTING":
                q["phase"] = "PULLED"
                if q.get("oid"):
                    cancel_order(q["oid"])
    save_stats()

# ---------------- round lifecycle ----------------

def mid_for(rs, side):
    bk = rs.get("book", {}).get(side)
    if not bk: return None
    b, a = bk
    return (b + a) / 2

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
    """Signed naked directional notional per 5m bucket across BOTH series (Part-3 cap).
    A completed pair is hedged (Up + Down = $1.00 no matter what) and nets to zero."""
    bucket = (int(now) // 300) * 300
    net = 0.0
    for (label, start), rs in t.rounds.items():
        if start // 300 != bucket // 300:
            continue
        up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
        if up["phase"] == "FILLED" and dn["phase"] == "FILLED":
            continue                                    # pair: riskless, nets out
        for side in ("Up", "Down"):
            q = rs["quotes"].get(side)
            if q and q["phase"] == "FILLED":
                net += q["filled"] * QUOTE_PRICE * (1 if side == "Up" else -1)
    return net

def record_fill(rs, side, ts, px, sz, now, live=False):
    """EVERY fill goes through admissible() BEFORE it exists. Part-4, no exceptions."""
    mid = mid_for(rs, side) or 0.50
    ok, reasons = admissible(mid, now)
    q = rs["quotes"].get(side)
    if not q or q["phase"] != "RESTING" or not q.get("placed"):
        return
    if ts < rs["start"] + QUOTE_START_S or ts > rs["start"] + QUOTE_END_FRAC * rs["T"]:
        return                                    # outside quote window: not our fill
    if ts < q.get("place_ts", 0):
        return                                    # fill before the quote existed = phantom
    if not ok:
        log(f"❌ FILL REJECTED (gate) {rs['label']} {side} @{px:.2f} mid={mid:.2f} — {'; '.join(reasons)}")
        return
    take = min(sz, q["rest"])
    if take <= 0:
        return
    delta = take * QUOTE_PRICE * (1 if side == "Up" else -1)
    if abs(net_directional_5m(now) + delta) > NET_CAP_5M:
        log(f"❌ FILL REJECTED (net cap ${NET_CAP_5M:.0f}/5m) {rs['label']} {side}")
        return
    q["rest"] -= take
    q["filled"] += take
    if q["fill_ts"] is None:
        q["fill_ts"] = ts
    q["phase"] = "FILLED"
    t.stats["fills"] += 1
    csv_append(FILLS_CSV, ["ts", "series", "round", "side", "event", "price", "shares", "live"],
               [int(ts), rs["label"], rs["start"], side, "FILL", f"{px:.2f}", f"{take:.0f}", int(live or q.get("live"))])
    log(f"FILL {rs['label']} {side} {take:.0f}sh @ {px:.2f} ({'LIVE' if live or q.get('live') else 'DRY'}) — inventory rule armed")
    # pair completed late -> cancel a pending breakeven offer and hold BOTH to resolution
    up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
    if up["phase"] == "FILLED" and dn["phase"] == "FILLED":
        for s2, q2 in (("Up", up), ("Down", dn)):
            if q2.get("offered") and not q2.get("exited"):
                q2["offered"] = False
                if q2.get("exit_oid"):
                    cancel_order(q2["exit_oid"])
                log(f"PAIR COMPLETED {rs['label']} — breakeven offer on {s2} cancelled, hold to resolution")
    save_stats()

def inventory_exit(rs, side, now):
    """Quote end still naked: taker exit at best bid (fee on top). Never hold naked hope."""
    q = rs["quotes"][side]
    if q["phase"] not in ("FILLED",) or q.get("exited"):
        return
    bk = rs.get("book", {}).get(side)
    bid = bk[0] if bk else None
    if bid is None:
        log(f"STUCK {rs['label']} {side}: no book — holding to resolution (cannot invent an exit)")
        q["phase"] = "STUCK"
        return
    px = max(0.01, bid)
    pnl_usd = q["filled"] * ((px - QUOTE_PRICE) - fee(px))
    q["exited"] = True
    q["phase"] = "EXITED"
    t.stats["day"] = t.day
    t.stats["day_pnl"] = t.stats.get("day_pnl", 0.0) + pnl_usd
    t.stats["pnl"] = t.stats.get("pnl", 0.0) + pnl_usd
    if pnl_usd < 0:
        t.stats["adverse"] = t.stats.get("adverse", 0.0) - pnl_usd
        if px <= 0.44:
            t.stats["adverse_ev"] = t.stats.get("adverse_ev", 0) + 1
    else:
        t.stats["gross"] = t.stats.get("gross", 0.0) + pnl_usd
    csv_append(FILLS_CSV, ["ts", "series", "round", "side", "event", "price", "shares", "live"],
               [int(now), rs["label"], rs["start"], side, "TAKER_EXIT", f"{px:.2f}", f"{q['filled']:.0f}", int(q.get("live"))])
    log(f"INV-EXIT {rs['label']} {side} {q['filled']:.0f}sh @ {px:.2f} pnl ${pnl_usd:+.2f}")
    if q.get("oid"):
        cancel_order(q["oid"])
    # 2026-09-24: the $20 stop is a REAL-MONEY switch; in DRY the P&L is simulated and a KILL
    # would silently end the incubation run (the S1 record Part #13 lesson). Armed only when live.
    if t.stats["day_pnl"] <= -DAILY_STOP and load_env().get("LIVE_TRADING") == "1":
        open(KILL, "w").write(f"daily stop {t.stats['day_pnl']:.2f} at {datetime.now(timezone.utc).isoformat()}\n")
        log(f"🛑 DAILY STOP ${t.stats['day_pnl']:.2f} — KILL written, flat")
    elif t.stats["day_pnl"] <= -DAILY_STOP and not t.stats.get("dry_stop_logged") == t.day:
        t.stats["dry_stop_logged"] = t.day
        log(f"DRY daily stop level reached (${t.stats['day_pnl']:.2f}) — simulated P&L, no KILL written")
    save_stats()

def breakeven_offer(rs, side, now):
    q = rs["quotes"][side]
    if q["phase"] != "FILLED" or q.get("offered") or q.get("exited"):
        return
    q["offered"] = True
    if q.get("live"):
        try:
            resp = post_gtc(rs["tokens"][side], BREAKEVEN, q["filled"], "SELL")
            q["exit_oid"] = order_id_of(resp)
            log(f"BREAKEVEN OFFER (LIVE) {rs['label']} {side} sell {q['filled']:.0f}sh @ {BREAKEVEN} (at cost) -> {resp}")
        except Exception as ex:
            log(f"breakeven offer failed -> will taker-exit at quote end: {ex!r}")
    else:
        log(f"BREAKEVEN OFFER (DRY) {rs['label']} {side} sell {q['filled']:.0f}sh @ {BREAKEVEN} (at cost)")

def place_quotes(rs, now, live):
    global_gates = gates(now)
    if global_gates:
        rs["skip_reason"] = "; ".join(global_gates)
        return
    mid = mid_for(rs, "Up") or mid_for(rs, "Down") or 0.50
    if not band_ok(mid):
        rs["skip_reason"] = f"mid {mid:.2f} outside band"
        return
    if now > rs["start"] + QUOTE_END_FRAC * rs["T"]:
        return
    for side in ("Up", "Down"):
        q = rs["quotes"][side]
        if q["phase"] != "RESTING" or q["placed"]:
            continue
        q["placed"] = True
        q["place_ts"] = now
        t.stats["quotes"] = t.stats.get("quotes", 0) + 1
        if live:
            try:
                resp = post_gtc(rs["tokens"][side], QUOTE_PRICE, QUOTE_SHARES, "BUY")
                q["oid"] = order_id_of(resp)
                q["live"] = True
                log(f"QUOTE (LIVE) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {QUOTE_PRICE} -> {resp}")
            except Exception as ex:
                log(f"QUOTE FAILED -> dry: {ex!r}")
                log(f"QUOTE (DRY) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {QUOTE_PRICE}")
        else:
            log(f"QUOTE (DRY) {rs['label']} BUY {side} {QUOTE_SHARES}sh @ {QUOTE_PRICE}")
    save_stats()

async def tape_loop():
    """Public prints -> fill detection (both DRY and LIVE — prints are ground truth)."""
    while True:
        try:
            now = time.time()
            for (label, start), rs in list(t.rounds.items()):
                if rs["T"] and now > rs["start"] + rs["T"] + 30:
                    continue
                if not rs.get("tokens") or not rs["quotes"]["Up"]["placed"]:
                    continue
                rows = http_json(f"{DATA}/trades?market={rs['tokens']['cond']}&limit=200")
                for r in rows or []:
                    ts = int(r.get("timestamp", 0))
                    if ts <= rs.get("tape_t", rs["start"]):
                        continue
                    out = r.get("outcome") or ("Up" if r.get("outcomeIndex") == 0 else "Down")
                    px, sz = float(r["price"]), float(r["size"])
                    if r.get("side") == "SELL" and px <= QUOTE_PRICE:
                        record_fill(rs, out, ts, QUOTE_PRICE, sz, now)
                if rows:
                    rs["tape_t"] = max(int(r["timestamp"]) for r in rows)
                # inventory rule after 20s unpaired (EXIT_MODE decides — never hope)
                up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
                both_f = up["phase"] == "FILLED" and dn["phase"] == "FILLED"
                for side, q in (("Up", up), ("Down", dn)):
                    if (q["phase"] == "FILLED" and q["fill_ts"] and not both_f and
                            now - q["fill_ts"] >= INV_GRACE_S):
                        if EXIT_MODE == "cut20":
                            inventory_exit(rs, side, now)       # cut now at the bid
                        elif EXIT_MODE == "chase":
                            if now - q["fill_ts"] >= INV_GRACE_S + 15:
                                inventory_exit(rs, side, now)
                            else:
                                breakeven_offer(rs, side, now)  # brief rest at cost
                        else:  # offer49: sit at cost offer; tick_loop dumps at quote end
                            breakeven_offer(rs, side, now)
        except Exception as ex:
            log(f"tape error {ex!r}")
        await asyncio.sleep(1.5)

def settle_round(rs, now):
    settled = gamma_resolution(rs["label"], rs["start"])
    if not settled:
        return False
    up, dn = rs["quotes"]["Up"], rs["quotes"]["Down"]
    paired = up["phase"] == "FILLED" and dn["phase"] == "FILLED" and not up.get("exited") and not dn.get("exited")
    note = ""
    pnl = 0.0
    if paired:
        pay = min(up["filled"], dn["filled"])
        pnl = pay * (1.0 - 2 * QUOTE_PRICE)
        t.stats["pairs"] = t.stats.get("pairs", 0) + 1
        t.stats["gross"] = t.stats.get("gross", 0.0) + pnl
        note = f"pair {pay:.0f}sh +${pnl:.2f}"
    else:
        for side, q in (("Up", up), ("Down", dn)):
            if q["phase"] == "STUCK":
                win = (side == settled)
                pnl += q["filled"] * (1.0 - QUOTE_PRICE if win else -QUOTE_PRICE)
                if win: t.stats["wins"] = t.stats.get("wins", 0) + 1
                else:   t.stats["adverse"] = t.stats.get("adverse", 0.0) + q["filled"] * QUOTE_PRICE
                note += f" STUCK-{side}->{'WIN' if win else 'LOSS'} "
    t.stats["day_pnl"] = t.stats.get("day_pnl", 0.0) + pnl
    t.stats["pnl"] = t.stats.get("pnl", 0.0) + pnl
    t.stats["n"] = t.stats.get("n", 0) + 1
    log(f"ROUND-RESULT {rs['label']} {rs['start']}: settled={settled} paired={int(paired)} "
        f"pnl ${pnl:+.2f} day ${t.stats['day_pnl']:+.2f} | {note}")
    save_stats()
    return True

def save_stats():
    with open(STATS, "w") as f:
        json.dump(t.stats, f, indent=1)

async def tick_loop():
    while True:
        now = time.time()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != t.stats.get("day"):
            t.stats["day"] = day
            t.stats["day_pnl"] = 0.0
        live_hard = gates(now)
        live_stat = stats_gates()
        forced = load_env().get("FORCE_LIVE") == "1"
        live_ok = (not live_hard) and (forced or not live_stat)
        for Tsec, label in ROUNDS:
            start = int(now // Tsec) * Tsec
            key = (label, start)
            rs = t.rounds.setdefault(key, dict(
                label=label, T=Tsec, start=start, tokens=None, book={},
                quotes={s: dict(phase="RESTING", placed=False, rest=QUOTE_SHARES,
                                filled=0.0, fill_ts=None, place_ts=None, oid=None, live=False,
                                offered=False, exited=False) for s in ("Up", "Down")},
                tape_t=start, settled=False, skip_reason=""))
            e = now - start
            if rs["tokens"] is None and e >= 0:
                rs["tokens"] = round_tokens(label, start)
            if rs["tokens"]:
                for side in ("Up", "Down"):
                    try:
                        bk = http_json(f"https://clob.polymarket.com/book?token_id={rs['tokens'][side]}")
                        bu = max((float(x["price"]) for x in bk.get("bids", [])), default=None)
                        au = min((float(x["price"]) for x in bk.get("asks", [])), default=None)
                        rs["book"][side] = (bu, au)
                    except Exception:
                        pass
                # band-exit pull: quotes live ONLY inside the 45-55 band (S3: "quote the
                # 45-55 band") — mid leaving the band pulls both, same as a cascade.
                if any(q["phase"] == "RESTING" and q["placed"] for q in rs["quotes"].values()):
                    mid = mid_for(rs, "Up") or mid_for(rs, "Down") or 0.50
                    if not band_ok(mid):
                        do_pull(f"BAND_EXIT mid={mid:.2f} {rs['label']}")
            # quote at t+30s (mechanics: "from t+30s until 60% of round elapsed")
            if QUOTE_START_S <= e <= QUOTE_END_FRAC * Tsec and not any(q["placed"] for q in rs["quotes"].values()):
                place_quotes(rs, now, live_ok)
            # quote end: cancel resting quotes (LIVE GTC must never linger) + taker-exit naked legs
            if e >= QUOTE_END_FRAC * Tsec:
                paired = (rs["quotes"]["Up"]["phase"] == "FILLED" and rs["quotes"]["Down"]["phase"] == "FILLED")
                for side in ("Up", "Down"):
                    q = rs["quotes"][side]
                    if q["phase"] == "RESTING" and q["placed"]:
                        q["phase"] = "PULLED"                    # window closed
                        if q.get("oid"):
                            cancel_order(q["oid"])
                            log(f"CLOSE {rs['label']} {side} quote cancelled at window end")
                    if q["phase"] == "FILLED" and not q.get("exited") and not paired:
                        inventory_exit(rs, side, now)
            # settle
            if e > Tsec + 45 and not rs["settled"]:
                rs["settled"] = settle_round(rs, now)
        # GC old rounds
        for key in [k for k, r in t.rounds.items() if now - k[1] > 1500]:
            del t.rounds[key]
        await asyncio.sleep(2.0)

def preflight():
    print("=" * 72)
    print("S3 PREFLIGHT (read-only, no orders)  " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    env = load_env()
    now = time.time()
    for Tsec, label in ROUNDS:
        start = int(now // Tsec) * Tsec
        toks = round_tokens(label, start)
        print(f"{label}: tokens {'OK' if toks else 'MISSING'} for {start}")
    ok, why = quiet_session(now)
    print(f"session gate: {'QUIET (quotes allowed)' if ok else 'ACTIVE (flat)'} — {why}")
    print(f"band gate: quotes only while mid in {BAND} (Part-4: fills outside band FAIL LOUDLY)")
    print(f"moondev key present: {'yes' if env.get('MOONDEV_API_KEY') else 'NO (liq/imb pulls dead -> FLAT via stale-switch)'}")
    print(f"live keys present:   {'yes' if env.get('POLY_PRIVATE_KEY') and env.get('POLY_FUNDER') else 'no (DRY only)'}")
    print(f"LIVE_TRADING: {env.get('LIVE_TRADING', '0')}  FORCE_LIVE: {env.get('FORCE_LIVE', '0')}")
    print(f"KILL file: {'PRESENT — FLAT' if os.path.exists(KILL) else 'absent'}")
    r = gates(now)
    print(f"hard gates: {'MET — trader may quote' if not r else 'NOT MET -> FLAT: ' + '; '.join(r)}")
    print("=" * 72)

async def main_async(minutes=None):
    log(f"S3 fee-farm maker starting ({'BOUNDED ' + str(minutes) + 'min' if minutes else 'forever'}) "
        f"— quotes {QUOTE_PRICE} x {QUOTE_SHARES}sh both sides, window +{QUOTE_START_S}s..{QUOTE_END_FRAC:.0%}, "
        f"band {BAND}, exit_mode {EXIT_MODE}, pulls: LIQ $1.5M/10s + SPOT {SPOT_MOVE_BPS}bps/{SPOT_WINDOW_S:.0f}s + IMB {IMB_EXTREME}")
    tasks = [asyncio.create_task(rtds()), asyncio.create_task(moondev_loops()),
             asyncio.create_task(tape_loop()), asyncio.create_task(tick_loop())]
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
    args = ap.parse_args()
    if args.preflight:
        preflight()
        sys.exit(0)
    try:
        asyncio.run(main_async(args.minutes))
    except KeyboardInterrupt:
        log("interrupted — flat")
