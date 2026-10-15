#!/usr/bin/env python3
"""latency_probe.py - STEP 1b (owner-authorized 2026-10-15): measure the REAL order loop with tiny orders.

  python3 latency_probe.py --env ~/obm/.env [--n 20] [--out ~/pm/results]

On the current BTC 5-minute round it posts post-only BUY orders on the Up token at 0.01 (5 shares =
$0.05 at risk per order), measures every leg, and cancels each order right away. Phases:
  A  sign + POST /order (SDK as shipped, one persistent HTTP/2 connection) + DELETE
  B  pre-signed order: POST only (isolates signing from the network)
  C  event-triggered: wait for the next top-of-book change on the market websocket, then post the
     pre-signed order; reports server timestamp -> order acknowledged = the real reaction time
Safety: price 0.01 fixed, BUY only, post_only, hard cap on order count (MAX_ORDERS), only our own
order ids are cancelled (never cancel_all), balance read before and after, every leftover re-cancelled
and reported. Secrets never printed. Writes <out>/latency_probe_<utc>.json and .md
"""
import argparse, asyncio, json, os, statistics, time, urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

UA = {"User-Agent": "Mozilla/5.0 pm-latency-probe"}
CLOB_HOST = "https://clob.polymarket.com"
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PRICE = 0.01
SIZE = 5.0
MAX_ORDERS = 80


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
    if now - s > 270: s += 300                      # too close to the end: use the next round
    for st in (s, s + 300):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{st}", headers=UA)
        try:
            d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception as e:
            print("lookup", st, e); continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]; outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            return dict(start=st, slug=d[0]["slug"], condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")],
                        tick=m.get("orderPriceMinTickSize"), min_size=m.get("orderMinSize"))
    raise SystemExit("no current market found")


def make_client():
    from py_clob_client_v2 import ClobClient
    pk = env("POLY_PRIVATE_KEY"); funder = env("POLY_FUNDER") or env("POLYMARKET_PROXY_ADDRESS")
    sig = int(env("POLY_SIGNATURE_TYPE", "3"))
    if not pk or not funder:
        raise SystemExit("missing credentials in the env file (names only: POLY_PRIVATE_KEY, POLY_FUNDER)")
    t0 = now_ms()
    creds = ClobClient(host=CLOB_HOST, key=pk, chain_id=137).create_or_derive_api_key()
    t1 = now_ms()
    return ClobClient(host=CLOB_HOST, key=pk, chain_id=137, creds=creds, signature_type=sig, funder=funder), sig, funder, t1 - t0


def balance(c):
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        r = c.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return round(float(r.get("balance", 0)) / 1e6, 4)
    except Exception as e:
        return f"err {type(e).__name__}"


class Probe:
    def __init__(self, c, token, log):
        from py_clob_client_v2 import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY
        self.c = c; self.token = token; self.log = log; self.OrderArgs = OrderArgs; self.OrderType = OrderType; self.BUY = BUY
        self.posted = []; self.n_orders = 0

    def sign(self, price=PRICE):
        t0 = now_ms()
        o = self.c.create_order(self.OrderArgs(token_id=self.token, price=price, size=SIZE, side=self.BUY))
        return o, now_ms() - t0

    def post(self, order):
        if self.n_orders >= MAX_ORDERS: raise SystemExit("order cap reached")
        self.n_orders += 1
        t0 = now_ms()
        resp = self.c.post_order(order, order_type=self.OrderType.GTC, post_only=True)
        dt = now_ms() - t0
        if isinstance(resp, dict) and resp.get("success") and resp.get("orderID"):
            self.posted.append(resp["orderID"]); return resp["orderID"], dt, resp.get("status"), None
        return None, dt, None, str(resp)[:200]

    def cancel(self, oid):
        from py_clob_client_v2.clob_types import OrderPayload
        t0 = now_ms()
        r = self.c.cancel_order(OrderPayload(orderID=oid))
        dt = now_ms() - t0
        if isinstance(r, dict) and oid in (r.get("canceled") or []):
            self.posted.remove(oid); return True, dt, None
        nc = (r.get("not_canceled") or {}) if isinstance(r, dict) else {}
        return False, dt, nc.get(oid, str(r)[:160])

    def one(self, pre=None):
        rec = dict(sign_ms=None, post_ms=None, cancel_ms=None, order_id=None, posted=False, cancelled=False, status=None, err=None, t_ack_ms=None)
        try:
            if pre is None:
                order, rec["sign_ms"] = self.sign()
            else:
                order, rec["sign_ms"] = pre
            oid, rec["post_ms"], rec["status"], err = self.post(order)
            rec["t_ack_ms"] = now_ms()
            if not oid:
                rec["err"] = f"post: {err}"; return rec
            rec["posted"] = True; rec["order_id"] = oid
            ok, rec["cancel_ms"], err = self.cancel(oid)
            rec["cancelled"] = ok
            if not ok: rec["err"] = f"cancel: {err}"
        except Exception as e:
            rec["err"] = f"{type(e).__name__}: {str(e)[:200]}"
        return rec


async def phase_c(pr, mkt, n, log):
    import websockets
    out = []
    async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None) as ws:
        await ws.send(json.dumps({"assets_ids": [mkt["up"], mkt["down"]], "type": "market"}))
        last = 0.0; bb = {}; ba = {}
        pre = pr.sign()
        t_stop = now_ms() + 120000
        async for raw in ws:
            rx = now_ms()
            msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
            trig = None
            for m in msgs:
                et = m.get("event_type")
                if et == "price_change":
                    for ch in m.get("price_changes") or []:
                        a = ch["asset_id"]
                        if ch.get("best_bid") and float(ch["best_bid"]) != bb.get(a): bb[a] = float(ch["best_bid"]); trig = m
                        if ch.get("best_ask") and float(ch["best_ask"]) != ba.get(a): ba[a] = float(ch["best_ask"]); trig = m
                elif et == "book":
                    trig = trig or m
            if now_ms() > t_stop: break
            if trig is None or rx - last < 1500: continue
            last = rx
            ts = float(trig.get("timestamp") or rx)
            rec = pr.one(pre=pre)
            rec.update(feed_lag_ms=rx - ts, event_type=trig["event_type"],
                       server_ts_to_ack_ms=(rec["t_ack_ms"] - ts) if rec["posted"] else None)
            out.append(rec)
            log(f"  C #{len(out)} feed lag {rec['feed_lag_ms']:.0f} ms, post {rec['post_ms'] and round(rec['post_ms'])} ms, "
                f"server->ack {rec['server_ts_to_ack_ms'] and round(rec['server_ts_to_ack_ms'])} ms, cancel {rec['cancel_ms'] and round(rec['cancel_ms'])} ms, "
                f"cancelled={rec['cancelled']} {rec['err'] or ''}")
            pre = pr.sign()
            if len(out) >= n or (len(out) >= 3 and all(r["err"] and not r["posted"] for r in out[-3:])): break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True); ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"))
    a = ap.parse_args()
    load_dotenv(os.path.expanduser(a.env), override=True)
    os.makedirs(a.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ"); lines = []

    def log(s):
        print(s, flush=True); lines.append(s)

    if 3 * a.n > MAX_ORDERS: raise SystemExit(f"n too big: {3 * a.n} orders > cap {MAX_ORDERS}")
    mkt = current_market(); log(f"market {mkt['slug']} tick {mkt['tick']} min_size {mkt['min_size']} up ...{mkt['up'][-8:]}")
    c, sig, funder, derive_ms = make_client(); log(f"client ready sig_type={sig} funder=...{funder[-6:]} derive {derive_ms:.0f} ms")
    bal0 = balance(c); log(f"balance before: {bal0}")
    t0 = now_ms(); c.get_tick_size(mkt["up"]); t1 = now_ms(); c.get_neg_risk(mkt["up"]); t2 = now_ms()
    log(f"SDK pre-checks (first call only): tick_size {t1 - t0:.0f} ms, neg_risk {t2 - t1:.0f} ms")
    # safety: bid 0.01 only on the side that is currently the LIKELY WINNER (its ask is far above 0.01, so the
    # bid can never become marketable); the first run matched one 5-share 0.01 bid on the dying side.
    books = {}
    for name in ("up", "down"):
        try:
            ob = c.get_order_book(mkt[name]); asks = [float(x.price) for x in (ob.asks or [])]; bids = [float(x.price) for x in (ob.bids or [])]
            books[name] = (max(bids) if bids else 0.0, min(asks) if asks else 1.0)
        except Exception as e:
            books[name] = (0.0, 1.0); log(f"book read failed {name}: {e}")
    side_name = max(books, key=lambda k: books[k][0]); bb_, ba_ = books[side_name]
    log(f"books up bid/ask {books['up']} down bid/ask {books['down']} -> probing {side_name} (bid {bb_} ask {ba_})")
    if ba_ < 0.10:
        raise SystemExit("best ask under 0.10 on both sides, a 0.01 bid could become marketable: not probing now")
    pr = Probe(c, mkt[side_name], log)
    res = dict(stamp=stamp, market=mkt["slug"], size=SIZE, price=PRICE, sig_type=sig, phases={})
    # phase A
    log(f"phase A: sign + POST + DELETE, {a.n} orders"); A = []
    for i in range(a.n):
        r = pr.one(); A.append(r)
        log(f"  A #{i + 1} sign {r['sign_ms'] and round(r['sign_ms'], 1)} ms post {r['post_ms'] and round(r['post_ms'])} ms ({r['status']}) cancel {r['cancel_ms'] and round(r['cancel_ms'])} ms cancelled={r['cancelled']} {r['err'] or ''}")
        if i >= 2 and all(x["err"] and not x["posted"] for x in A[-3:]): log("  three consecutive failures, stopping phase A"); break
        time.sleep(0.5)
    res["phases"]["A_sign_post_cancel"] = dict(sign_ms=summ([r["sign_ms"] for r in A]), post_ms=summ([r["post_ms"] for r in A if r["posted"]]),
                                              cancel_ms=summ([r["cancel_ms"] for r in A if r["cancelled"]]), orders=A)
    # phase B
    log(f"phase B: pre-signed, POST only timed, {a.n} orders"); B = []
    if any(r["posted"] for r in A):
        for i in range(a.n):
            r = pr.one(pre=pr.sign()); B.append(r)
            log(f"  B #{i + 1} post {r['post_ms'] and round(r['post_ms'])} ms cancel {r['cancel_ms'] and round(r['cancel_ms'])} ms cancelled={r['cancelled']} {r['err'] or ''}")
            if i >= 2 and all(x["err"] and not x["posted"] for x in B[-3:]): log("  three consecutive failures, stopping phase B"); break
            time.sleep(0.5)
    res["phases"]["B_presigned_post"] = dict(post_ms=summ([r["post_ms"] for r in B if r["posted"]]), cancel_ms=summ([r["cancel_ms"] for r in B if r["cancelled"]]), orders=B)
    # phase C
    log(f"phase C: top-of-book change -> pre-signed POST, {a.n} orders"); C = []
    if any(r["posted"] for r in A):
        C = asyncio.run(phase_c(pr, mkt, a.n, log))
    res["phases"]["C_event_triggered"] = dict(feed_lag_ms=summ([r["feed_lag_ms"] for r in C]), post_ms=summ([r["post_ms"] for r in C if r["posted"]]),
                                              server_ts_to_ack_ms=summ([r["server_ts_to_ack_ms"] for r in C if r["posted"]]),
                                              cancel_ms=summ([r["cancel_ms"] for r in C if r["cancelled"]]), orders=C)
    # safety: leftovers
    left = list(pr.posted)
    for oid in left:
        try:
            ok, dt, err = pr.cancel(oid); log(f"  re-cancel {oid[:10]}...: {'ok' if ok else err}")
            if not ok:
                try: log(f"  order state: {json.dumps(c.get_order(oid))[:200]}")
                except Exception as e: log(f"  get_order failed: {e}")
        except Exception as e:
            log(f"  re-cancel failed {oid[:10]}...: {e}")
    bal1 = balance(c); log(f"balance after: {bal1} (before {bal0})")
    allr = A + B + C
    res.update(balance_before=bal0, balance_after=bal1, leftover_order_ids=left, orders_posted=sum(1 for r in allr if r["posted"]),
               orders_cancelled=sum(1 for r in allr if r["cancelled"]), fills_suspected=[r["order_id"] for r in allr if r["posted"] and not r["cancelled"]])
    jp = os.path.join(a.out, f"latency_probe_{stamp}.json"); json.dump(res, open(jp, "w"), indent=1)
    md = [f"# Latency probe (LIVE, tiny) {stamp} - {mkt['slug']} (size {SIZE:g} @ {PRICE}, post-only BUY, each cancelled)", "",
          f"orders posted {res['orders_posted']}, cancelled {res['orders_cancelled']}, leftovers {len(left)}, balance {bal0} -> {bal1}", "",
          "| phase | n | sign ms | POST /order ms median / p90 | DELETE ms median | feed lag ms | server ts -> ack ms median / p90 |", "|---|---|---|---|---|---|---|"]
    for k, v in res["phases"].items():
        p = v.get("post_ms", {}); cnl = v.get("cancel_ms", {}); fl = v.get("feed_lag_ms", {}); sa = v.get("server_ts_to_ack_ms", {}); sg = v.get("sign_ms", {})
        md.append(f"| {k} | {p.get('n', 0)} | {sg.get('median', '-')} | {p.get('median', '-')} / {p.get('p90', '-')} | {cnl.get('median', '-')} | {fl.get('median', '-')} | {sa.get('median', '-')} / {sa.get('p90', '-')} |")
    md += ["", "## log", ""] + [f"    {l}" for l in lines]
    mp = os.path.join(a.out, f"latency_probe_{stamp}.md"); open(mp, "w").write("\n".join(md) + "\n")
    log(f"saved {jp} and {mp}")


if __name__ == "__main__":
    main()
