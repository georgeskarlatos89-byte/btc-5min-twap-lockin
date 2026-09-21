#!/usr/bin/env python3
"""
S1 INCUBATION TRADER — $10 real-money incubation, gated by code (P5).

DEFAULT: DRY RUN. It goes live ONLY if ALL of these hold at the same time:
  1. s1_harness/.env exists with LIVE_TRADING=1, a POLY_PRIVATE_KEY and a
     POLY_FUNDER (the Polymarket proxy address that actually holds the pUSD)
     (created by the human, chmod 600, NEVER written by this program);
  2. ARBITER GATE: >= ARB_MIN full-coverage rounds in rounds.csv whose
     fa_twap column is 'yes' (true-feed semantics still validated);
  3. DRY GATE: this trader's own dry signals: n >= SIG_MIN at win-rate >= 90%;
  4. no KILL file present; feed fresh; within risk caps.
If any gate closes mid-run, it drops back to dry immediately.

Entry rules (backtest-calibrated):
  * locked-TWAP gap >= 4 bps (the measured 100% zone);
  * last 45s (5m) / 90s (15m);
  * our side's ask in [0.55, 0.97]  -> the LAGGING book; a converged opposite
    book (our ask <= 0.05) is our error, not an edge: never traded;
  * post-fee edge (official 0.07*p*(1-p)) > 2c.

Risk: $10/trade; 1 trade/round; max 3/hour; daily loss cap $20 -> KILL;
stale feed (>10s without a stream update) -> no trade.
"""
import asyncio, json, math, os, time, csv, urllib.request
from datetime import datetime, timezone
from collections import deque

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
KILL = os.path.join(HERE, "KILL")
ROUNDS_CSV = os.path.join(HERE, "rounds.csv")
STATS = os.path.join(HERE, "trader_stats.json")
LOG = os.path.join(HERE, "trader.log")

RTDS = "wss://ws-live-data.polymarket.com"
SYMBOL = "btc/usd"
ROUNDS = [(300, "5m", 45), (900, "15m", 90)]
GAP_MIN = 4.0
EDGE_MIN = 0.02
ASK_BAND = (0.55, 0.97)
TRADE_USD = 10.0
MAX_PER_HOUR = 3
DAILY_STOP = 20.0
ARB_MIN = 50
SIG_MIN = 20
SIG_WR = 0.90

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def fee(p): return 0.07 * p * (1 - p)
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

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

def arbiter_count():
    n = 0
    if os.path.exists(ROUNDS_CSV):
        for row in csv.DictReader(open(ROUNDS_CSV)):
            if row.get("fa_twap") == "yes":
                n += 1
    return n

class T:
    def __init__(self):
        self.holding = None; self.last_ts = None
        self.samples = deque(maxlen=600)
        self.last_update = 0.0
        self.rounds = {}
        self.stats = json.load(open(STATS)) if os.path.exists(STATS) else {"n": 0, "wins": 0, "pnl": 0.0, "day": "", "day_pnl": 0.0, "hour_marks": []}
        self.client = None

t = T()

def gates():
    """HARD gates — never bypassable: key, KILL, fresh feed."""
    env = load_env()
    reasons = []
    if not env.get("POLY_PRIVATE_KEY"): reasons.append("no key in .env")
    # Without the funder the client signs as the bare EOA (an EMPTY wallet) - hard block.
    if not env.get("POLY_FUNDER"): reasons.append("no POLY_FUNDER (proxy) in .env")
    # 2026-09-21 (living record Part #15): this model's settlement premise is FALSIFIED - the
    # venue settles on the END value of the 60s TWAP feed vs its open (live arbiter: end-value
    # 11/11 vs full-round-avg 0/11 on distinguishing rounds; venue changelog agrees). The
    # marker file makes "never go live" a HARD gate that FORCE_LIVE cannot bypass. Reversible
    # only by deliberately deleting the marker (the monitor alerts if that happens).
    if os.path.exists(os.path.join(HERE, "S1_LIVE_BLOCKED")):
        reasons.append("S1 live BLOCKED (settlement premise falsified - see S1_LIVE_BLOCKED)")
    if os.path.exists(KILL): reasons.append("KILL file present")
    if time.time() - t.last_update > 10: reasons.append("feed stale")
    return reasons

def stats_gates():
    """STATISTICAL gates — bypassable only by explicit FORCE_LIVE=1."""
    reasons = []
    env = load_env()
    if env.get("LIVE_TRADING") != "1": reasons.append("LIVE_TRADING!=1")
    a = arbiter_count()
    if a < ARB_MIN: reasons.append(f"arbiter {a}/{ARB_MIN}")
    s = t.stats
    if s["n"] < SIG_MIN or (s["n"] and s["wins"] / s["n"] < SIG_WR):
        reasons.append(f"dry {s['n']} sigs wr={s['wins']/max(1,s['n']):.0%}")
    return reasons

def client_for(env):
    """Proxy-wallet signing path, copied from the live btc5bot (account 2):
    py_clob_client_v2 + signature_type=3 (POLY_1271) + funder=<proxy holding the pUSD>.
    The original bundle built a bare-EOA v1 client, which signs for an EMPTY wallet
    (the collateral sits at the proxy, not the signer). Execution plumbing only -
    the strategy (gates, entry rules, sizing) is untouched."""
    if t.client: return t.client
    from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType
    host = "https://clob.polymarket.com"
    pk, funder = env["POLY_PRIVATE_KEY"], env["POLY_FUNDER"]
    sig_type = int(env.get("POLY_SIGNATURE_TYPE", "3"))
    # L1: derive (or create) the API creds bound to this signer's key
    creds = ClobClient(host=host, key=pk, chain_id=137).create_or_derive_api_key()
    # L2: the trading client that signs THROUGH the proxy
    c = ClobClient(host=host, key=pk, chain_id=137, creds=creds,
                   signature_type=sig_type, funder=funder)
    # Sync the proxy's on-chain pUSD into the CLOB ledger (best-effort, as the live bot does)
    try:
        c.update_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    except Exception as ex:
        log(f"balance-allowance sync skipped: {ex!r}")
    t.client = c
    return c

def place(side, token_id, price, now):
    """$10 FOK buy at `price`. Returns fill info or error string."""
    env = load_env()
    c = client_for(env)
    from py_clob_client_v2 import OrderArgs, OrderType
    from py_clob_client_v2.order_builder.constants import BUY   # the "BUY" string
    # NOTE: OrderArgs.side is a str ("BUY"/"SELL"). py_clob_client_v2.Side is an
    # IntEnum (BUY=0) meant for MarketOrderArgsV2 - passing it here would be wrong.
    size = max(5, int(TRADE_USD / price))
    args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
    # v2 API: order_type is a keyword (v1 took options={"order_type": ...}).
    # options=None -> the client resolves tick_size / neg_risk from the CLOB itself.
    resp = c.create_and_post_order(args, order_type=OrderType.FOK)
    return resp

async def rtds():
    backoff = 2
    while True:
        try:
            async with websockets.connect(RTDS, max_size=None) as ws:
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                    {"topic": "crypto_prices_twap_sixty", "type": "update",
                     "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":"))}]}))
                backoff = 2
                async def hb():
                    while True:
                        await asyncio.sleep(5); await ws.send("PING")
                h = asyncio.create_task(hb())
                try:
                    while True:
                        # stall watchdog (Part #23): a zombie socket froze the observer for 85 min;
                        # the trader's "feed stale" gate blocks trading but would never reconnect.
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            log("RTDS silent for 60s — reconnecting (stall watchdog)")
                            break
                        try: msg = json.loads(raw)
                        except Exception: continue
                        if msg.get("type") != "update": continue
                        p = msg.get("payload", {})
                        if p.get("symbol") != SYMBOL or msg.get("topic") != "crypto_prices_twap_sixty": continue
                        val = float(p["full_accuracy_value"]) / 1e18
                        obs = p["timestamp"] / 1000.0
                        now = time.time()
                        for (Tsec, label, win) in ROUNDS:
                            start = int(now // Tsec) * Tsec
                            rs = t.rounds.setdefault((label, start), {"acc": 0.0, "el": 0.0, "lt": None, "o": None, "traded": False, "sig": None})
                            if rs["o"] is None and (now - start) <= 3:
                                rs["o"] = val
                                log(f"{label} {start}: open ref {val:.2f}")
                            if rs["lt"] is None: rs["lt"] = max(obs, start)
                            t0 = max(rs["lt"], start); t1 = min(obs, start + Tsec)
                            if t1 > t0 and t.holding is not None:
                                rs["acc"] += t.holding * (t1 - t0); rs["el"] += (t1 - t0)
                            rs["lt"] = obs
                        t.holding = val; t.last_update = now
                        t.samples.append((obs, val))
                finally:
                    h.cancel()
        except Exception as ex:
            log(f"RTDS problem {ex!r}, backoff {backoff}s"); await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)

def sigma1():
    vals = [v for _, v in list(t.samples)[-120:]]
    if len(vals) >= 30:
        d = [b - a for a, b in zip(vals, vals[1:])]
        m = sum(d) / len(d)
        return max(math.sqrt(sum((x - m) ** 2 for x in d) / len(d)), (t.holding or 8e4) * 4.3e-6)
    return (t.holding or 8e4) * 4.3e-6

async def tick():
    last_roll = {}
    warned = False
    while True:
        now = time.time()
        hard = gates()
        stat = stats_gates()
        forced = load_env().get("FORCE_LIVE") == "1"
        live_ok = (not hard) and (forced or not stat)
        if forced and not hard and not warned:
            warned = True
            log(f"⚠ FORCE_LIVE active: statistical gates bypassed by operator ({'; '.join(stat) or 'gates were green'})")
        for (Tsec, label, win) in ROUNDS:
            start = int(now // Tsec) * Tsec
            rs = t.rounds.get((label, start))
            if not rs or rs["o"] is None or rs["el"] <= 0 or rs["traded"]:
                continue
            e, r = rs["el"], Tsec - rs["el"]
            if r > win or r <= 1:
                continue
            acc = rs["acc"] / e
            gap = (acc - rs["o"]) / rs["o"] * 1e4
            if abs(gap) < GAP_MIN:
                continue
            f_req = (Tsec * rs["o"] - e * acc) / r
            sf = 1.5 * sigma1() * math.sqrt(r / 3.0)
            p = ncdf((t.holding - f_req) / sf)
            side = "Up" if p > 0.5 else "Down"
            # book
            try:
                if "tok" not in rs:
                    m = http_json(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-{label}-{start}")[0]
                    rs["tok"] = json.loads(m["clobTokenIds"])
                bk = http_json(f"https://clob.polymarket.com/book?token_id={rs['tok'][0]}")
                bu = max(float(x["price"]) for x in bk["bids"]); au = min(float(x["price"]) for x in bk["asks"])
            except Exception:
                continue
            our_ask = au if side == "Up" else round(1 - bu, 2)
            our_p = p if side == "Up" else 1 - p
            edge = our_p - our_ask - fee(our_ask)
            if edge <= EDGE_MIN or not (ASK_BAND[0] <= our_ask <= ASK_BAND[1]):
                continue
            # ---- signal! ----
            rs["traded"] = True   # one shot per round
            rs["sig"] = dict(side=side, price=our_ask, start=start, label=label)
            if not live_ok:
                log(f"DRY SIGNAL {label} t-{int(r)}s: {side} @ {our_ask:.2f} edge {edge:.1%} gap {gap:+.1f}bps (blocked: {'; '.join(hard + (stat if not forced else [])) or 'none'})")
            else:
                try:
                    resp = place(side, rs["tok"][0 if side == "Up" else 1], our_ask, now)
                    log(f"LIVE ORDER {label}: {side} {int(TRADE_USD/our_ask)}sh @ {our_ask:.2f} -> {resp}")
                    rs["sig"]["live"] = True
                except Exception as ex:
                    log(f"LIVE ORDER FAILED -> dry: {ex!r}")
            t.stats["hour_marks"] = [x for x in t.stats["hour_marks"] if now - x < 3600]
        # roll + settle dry signals
        for (label, start) in list(t.rounds):
            Tsec = 300 if label == "5m" else 900
            if now >= start + Tsec + 45 and not t.rounds[(label, start)].get("checked"):
                rs = t.rounds[(label, start)]; rs["checked"] = True
                sig = rs.get("sig")
                if not sig: continue
                try:
                    # closed=true FIRST: it turns final ~100 s before the plain /events response,
                    # which lags (measured live, Part #22). Plain query as the fallback.
                    ev = (http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}&closed=true")
                          or http_json(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{label}-{start}"))
                    m = ev[0]["markets"][0]
                    pr = [float(x) for x in json.loads(m["outcomePrices"])]
                    if not (m.get("closed") or not m.get("acceptingOrders")):
                        rs["checked"] = False; continue
                    settled = "Up" if pr[0] > 0.5 else "Down"
                except Exception:
                    rs["checked"] = False; continue
                win_ = settled == sig["side"]
                pnl = (1 - sig["price"]) if win_ else -sig["price"]
                t.stats["n"] += 1; t.stats["wins"] += win_
                t.stats["pnl"] = round(t.stats["pnl"] + pnl * TRADE_USD, 2)
                json.dump(t.stats, open(STATS, "w"))
                log(f"SCORE {label} {start}: {sig['side']} @ {sig['price']:.2f} settled {settled} -> {'WIN' if win_ else 'LOSS'} | dry n={t.stats['n']} wr={t.stats['wins']/t.stats['n']:.0%}")
                # 2026-09-21 fix (risk plumbing; constants untouched): the $20 stop is a REAL-MONEY
                # kill switch. In DRY mode there is no loss to stop - and writing KILL on paper
                # losses (cumulative, never reset) also killed the unrelated S9 watcher (687-restart
                # loop, ~1,600 Telegram pings). It now arms only when LIVE_TRADING=1.
                if (load_env().get("LIVE_TRADING") == "1" and t.stats["pnl"] <= -DAILY_STOP
                        and not os.path.exists(KILL)):
                    open(KILL, "w").write(f"daily stop {datetime.now(timezone.utc).isoformat()}\n")
                    log("DAILY STOP hit -> KILL written, live disabled")
        await asyncio.sleep(1)

async def main():
    log(f"trader starting | arbiter={arbiter_count()}/{ARB_MIN} | dry n={t.stats['n']} | live={load_env().get('LIVE_TRADING','0')}")
    await asyncio.gather(rtds(), tick())

if __name__ == "__main__":
    asyncio.run(main())
