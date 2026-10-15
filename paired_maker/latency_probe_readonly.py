#!/usr/bin/env python3
"""latency_probe_readonly.py - STEP 1a: measure every leg of the order loop WITHOUT placing an order.

  python3 latency_probe_readonly.py --env ~/obm/.env [--n 30] [--out ~/pm/results]

Measures, from this machine:
  sign_ms        building + signing an order locally (create_order never touches the network)
  auth_get_ms    an authenticated (L2 HMAC) GET /data/orders round trip, same auth path as POST /order,
                 cold (first call) and then on the SDK's persistent connection
  feed_lag_ms    market websocket server timestamp -> receipt
  event->resp    book event server timestamp -> authenticated GET response received
What it cannot measure: the matching engine's own time to accept an order. That one number needs a
real post-only order (latency_probe.py, owner-run). Secrets never printed. No orders, no cancels.
Writes <out>/latency_probe_readonly_<utc>.json and .md
"""
import argparse, asyncio, json, os, statistics, time, urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

UA = {"User-Agent": "Mozilla/5.0 pm-latency-probe-readonly"}
CLOB_HOST = "https://clob.polymarket.com"
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


def now_ms():
    return time.time() * 1000.0


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def summ(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return {"n": 0}
    return {"n": len(xs), "median": round(statistics.median(xs), 1), "p10": round(pct(xs, 0.10), 1),
            "p90": round(pct(xs, 0.90), 1), "min": round(min(xs), 1), "max": round(max(xs), 1)}


def current_market():
    now = int(time.time()); s = now - now % 300
    for st in (s, s + 300):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{st}", headers=UA)
        try:
            d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception as e:
            print("lookup", st, e); continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]
            outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            return dict(start=st, slug=d[0]["slug"], condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")])
    raise SystemExit("no current market found")


def make_client():
    """same signing path as the live bot: py_clob_client_v2, sig type 3, funder = proxy."""
    from py_clob_client_v2 import ClobClient
    pk = env("POLY_PRIVATE_KEY"); funder = env("POLY_FUNDER") or env("POLYMARKET_PROXY_ADDRESS")
    sig = int(env("POLY_SIGNATURE_TYPE", "3"))
    if not pk or not funder:
        raise SystemExit("missing credentials in the env file (names only: POLY_PRIVATE_KEY, POLY_FUNDER)")
    t0 = now_ms()
    creds = ClobClient(host=CLOB_HOST, key=pk, chain_id=137).create_or_derive_api_key()
    t1 = now_ms()
    return ClobClient(host=CLOB_HOST, key=pk, chain_id=137, creds=creds, signature_type=sig, funder=funder), sig, funder, t1 - t0


def auth_get(c, cid):
    """authenticated GET on the open-orders endpoint (L2 HMAC headers, same auth path as POST /order)."""
    fn = getattr(c, "get_open_orders", None) or getattr(c, "get_orders", None)
    if fn is None:
        raise SystemExit("client has no open-orders method: " + ", ".join(m for m in dir(c) if "order" in m.lower()))
    params = None
    for modname in ("py_clob_client_v2", "py_clob_client_v2.clob_types"):
        try:
            mod = __import__(modname, fromlist=["OpenOrderParams"]); params = mod.OpenOrderParams(market=cid); break
        except Exception:
            continue
    t0 = now_ms()
    r = fn(params) if params is not None else fn()
    return now_ms() - t0, (len(r) if isinstance(r, list) else None)


async def phase_event(c, mkt, n, log):
    import websockets
    out = []
    async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None) as ws:
        await ws.send(json.dumps({"assets_ids": [mkt["up"], mkt["down"]], "type": "market"}))
        last = 0.0
        while len(out) < n:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                log("ws idle 10 s"); continue
            rx = now_ms()
            msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
            for m in msgs:
                if m.get("event_type") not in ("price_change", "book") or m.get("timestamp") is None: continue
                if rx - last < 1000: continue
                last = rx
                ts = float(m["timestamp"])
                ms, _ = auth_get(c, mkt["condition_id"])
                rec = dict(feed_lag_ms=rx - ts, auth_get_ms=ms, event_to_resp_ms=now_ms() - ts)
                out.append(rec)
                log(f"  E #{len(out)} feed lag {rec['feed_lag_ms']:.0f} ms, auth GET {ms:.0f} ms, event->response {rec['event_to_resp_ms']:.0f} ms")
                if len(out) >= n: break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True); ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))
    a = ap.parse_args()
    load_dotenv(os.path.expanduser(a.env), override=True)
    os.makedirs(a.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ"); lines = []

    def log(s):
        print(s, flush=True); lines.append(s)

    from py_clob_client_v2 import OrderArgs
    from py_clob_client_v2.order_builder.constants import BUY
    mkt = current_market(); log(f"market {mkt['slug']} up ...{mkt['up'][-8:]}")
    c, sig, funder, derive_ms = make_client()
    log(f"client ready sig_type={sig} funder=...{funder[-6:]} derive_api_key {derive_ms:.0f} ms")
    t0 = now_ms(); c.get_tick_size(mkt["up"]); t1 = now_ms(); c.get_neg_risk(mkt["up"]); t2 = now_ms()
    log(f"SDK pre-checks (first call only, cached after): tick_size {t1 - t0:.0f} ms, neg_risk {t2 - t1:.0f} ms")
    # local signing only (no network)
    S = []
    for i in range(a.n):
        t = now_ms(); c.create_order(OrderArgs(token_id=mkt["up"], price=0.30, size=5, side=BUY)); S.append(now_ms() - t)
    log(f"sign only: {summ(S)}")
    # authenticated GET: cold then warm
    cold, _ = auth_get(c, mkt["condition_id"]); log(f"auth GET cold (first): {cold:.0f} ms")
    G = []
    for i in range(a.n):
        ms, _ = auth_get(c, mkt["condition_id"]); G.append(ms); time.sleep(0.2)
    log(f"auth GET warm: {summ(G)}")
    E = asyncio.run(phase_event(c, mkt, a.n, log))
    res = dict(stamp=stamp, market=mkt["slug"], sig_type=sig, derive_api_key_ms=round(derive_ms), precheck_tick_ms=round(t1 - t0), precheck_negrisk_ms=round(t2 - t1),
               sign_ms=summ(S), auth_get_cold_ms=round(cold), auth_get_warm_ms=summ(G),
               feed_lag_ms=summ([r["feed_lag_ms"] for r in E]), event_auth_get_ms=summ([r["auth_get_ms"] for r in E]),
               event_to_resp_ms=summ([r["event_to_resp_ms"] for r in E]))
    jp = os.path.join(a.out, f"latency_probe_readonly_{stamp}.json"); json.dump(res, open(jp, "w"), indent=1)
    md = [f"# Latency probe (read-only) {stamp} - {mkt['slug']}", "", "| leg | median ms | p10 | p90 | n |", "|---|---|---|---|---|"]
    for k in ("sign_ms", "auth_get_warm_ms", "feed_lag_ms", "event_auth_get_ms", "event_to_resp_ms"):
        v = res[k]; md.append(f"| {k} | {v.get('median', '-')} | {v.get('p10', '-')} | {v.get('p90', '-')} | {v.get('n', 0)} |")
    md += ["", f"- auth GET cold (first call): {res['auth_get_cold_ms']} ms; derive api key {res['derive_api_key_ms']} ms; "
               f"SDK pre-checks tick {res['precheck_tick_ms']} ms, neg_risk {res['precheck_negrisk_ms']} ms (once per token)",
           "- event_to_resp = book event server timestamp -> authenticated response received = the reaction time floor from this machine",
           "", "## log", ""] + [f"    {l}" for l in lines]
    mp = os.path.join(a.out, f"latency_probe_readonly_{stamp}.md"); open(mp, "w").write("\n".join(md) + "\n")
    log(f"saved {jp} and {mp}")


if __name__ == "__main__":
    main()
