#!/usr/bin/env python3
"""book_reader.py - the "inspect" tool as a program: what the Polymarket page sees, printed once a second.

  python3 book_reader.py --snapshot                 one full order book of the current BTC 5m round (REST), then exit
  python3 book_reader.py --live [--csv DIR]         follow the current round(s): one line per side per second
  ... | python3 book_reader.py --replay START tokens.json   same lines from recorded rows (BME csv, filtered or not)

Each line: elapsed s | side | best bid | best ask | spread | size at top 5 bid levels | top5 bid size vs top5 ask size
(imbalance) | taker sells / buys hitting this side's bids in the last 10 s | chainlink spot | price to beat | Up chance.
Read-only, no keys. Live mode uses the same websockets the web page uses (CLOB market feed + RTDS chainlink).
"""
import argparse, asyncio, collections, datetime, json, os, sys, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 book-reader (read-only)"}
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_RTDS = "wss://ws-live-data.polymarket.com"


def gamma_market(start):
    for q in ("", "&closed=true"):
        req = urllib.request.Request(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{start}{q}", headers=UA)
        try: d = json.load(urllib.request.urlopen(req, timeout=10))
        except Exception: continue
        if d and d[0].get("markets"):
            m = d[0]["markets"][0]; outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
            return dict(start=start, condition_id=m["conditionId"], up=ids[outs.index("Up")], down=ids[outs.index("Down")], question=m.get("question"))
    return None


class Book:
    def __init__(s, name):
        s.name = name; s.bids = {}; s.asks = {}; s.tape = collections.deque()      # (t, 'SELL'/'BUY', price, size) taker side on this token

    def top(s):
        # recorded rows carry the exchange's best bid/ask; levels the recorder never zeroed are pruned against them
        bb = getattr(s, "bb", None); ba = getattr(s, "ba", None)
        if bb is not None:
            for p in [p for p in s.bids if p > bb + 1e-9]: s.bids.pop(p, None)
        if ba is not None:
            for p in [p for p in s.asks if p < ba - 1e-9]: s.asks.pop(p, None)
        if bb is None: bb = max(s.bids) if s.bids else None
        if ba is None: ba = min(s.asks) if s.asks else None
        return bb, ba

    def line(s, t, e, spot, target, up_chance):
        bb, ba = s.top()
        bl = sorted(s.bids, reverse=True)[:5]; al = sorted(s.asks)[:5]
        sb = sum(s.bids[p] for p in bl); sa = sum(s.asks[p] for p in al)
        while s.tape and s.tape[0][0] < t - 10: s.tape.popleft()
        sells = sum(x[3] for x in s.tape if x[1] == "SELL"); buys = sum(x[3] for x in s.tape if x[1] == "BUY")
        imb = sb / (sb + sa) if sb + sa else None
        return (f"{e:6.1f}s {s.name:4} bb {bb if bb is not None else '-':>5} ba {ba if ba is not None else '-':>5} spr {f'{ba - bb:.2f}' if (bb is not None and ba is not None) else '-':>5} | "
                f"bid sizes {' '.join(f'{p:.2f}:{s.bids[p]:.0f}' for p in bl):<60} | imb {f'{imb:.2f}' if imb is not None else '-':>4} | "
                f"10s taker sells {sells:7.1f} buys {buys:7.1f} | spot {spot if spot else '-'} target {target if target else '-'} | Up {f'{up_chance:.0%}' if up_chance is not None else '-'}")

    def csv(s, t, e):
        bb, ba = s.top(); bl = sorted(s.bids, reverse=True)[:5]; al = sorted(s.asks)[:5]
        return [f"{t:.3f}", f"{e:.1f}", s.name, bb, ba, sum(s.bids[p] for p in bl), sum(s.asks[p] for p in al)] + [f"{p:.2f}:{s.bids[p]:.0f}" for p in bl]


def apply_book_msg(books, m):
    et = m.get("event_type")
    if et == "book":
        b = books.get(m["asset_id"])
        if b is None: return
        b.bids = {float(x["price"]): float(x["size"]) for x in m.get("bids") or []}
        b.asks = {float(x["price"]): float(x["size"]) for x in m.get("asks") or []}
    elif et == "price_change":
        for ch in m.get("price_changes") or []:
            b = books.get(ch["asset_id"])
            if b is None: continue
            L = b.bids if ch["side"] == "BUY" else b.asks; p = float(ch["price"]); sz = float(ch["size"])
            if sz <= 0: L.pop(p, None)
            else: L[p] = sz
    elif et == "last_trade_price":
        b = books.get(m["asset_id"])
        if b is not None: b.tape.append((time.time(), m.get("side"), float(m["price"]), float(m.get("size") or 0)))


def snapshot():
    st = int(time.time()) // 300 * 300; mk = gamma_market(st)
    if not mk: print("no market for", st); return
    print(mk["question"], "| start", datetime.datetime.fromtimestamp(st, datetime.timezone.utc).strftime("%H:%M:%SZ"),
          "| now", datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%SZ"), "(REST lists: best bid is the LAST bid, best ask the LAST ask)")
    for name in ("up", "down"):
        d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://clob.polymarket.com/book?token_id={mk[name]}", headers=UA), timeout=10))
        bids = sorted(((float(x["price"]), float(x["size"])) for x in d["bids"]), reverse=True); asks = sorted((float(x["price"]), float(x["size"])) for x in d["asks"])
        print(f"\n== {name.upper()}  best bid {bids[0][0] if bids else '-'}  best ask {asks[0][0] if asks else '-'}")
        print("  bids (price:shares):", " ".join(f"{p:.2f}:{s:.0f}" for p, s in bids[:10]))
        print("  asks (price:shares):", " ".join(f"{p:.2f}:{s:.0f}" for p, s in asks[:10]))


async def live(csv_dir):
    import websockets
    spot = {"v": None, "open": None}
    async def rtds():
        while True:
            try:
                async with websockets.connect(WS_RTDS, ping_interval=None) as ws:
                    await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                        {"topic": "crypto_prices_chainlink", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))},
                        {"topic": "crypto_prices_twap_sixty", "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))}]}))
                    async def ping():
                        while True: await asyncio.sleep(5); await ws.send("PING")
                    pt = asyncio.create_task(ping())
                    try:
                        async for raw in ws:
                            try: m = json.loads(raw)
                            except Exception: continue
                            if m.get("type") != "update": continue
                            pl = m.get("payload") or {}
                            if pl.get("symbol") != "btc/usd": continue
                            v = pl.get("full_accuracy_value"); v = float(v) / 1e18 if v else float(pl.get("value") or 0)
                            ts = float(pl.get("timestamp") or 0) / 1000.0
                            if m.get("topic") == "crypto_prices_chainlink": spot["v"] = round(v, 2)
                            elif m.get("topic") == "crypto_prices_twap_sixty":
                                st = int(ts) // 300 * 300
                                if spot.get("open_st") != st and ts - st <= 3: spot["open"] = round(v, 2); spot["open_st"] = st
                    finally: pt.cancel()
            except Exception as e:
                print("rtds error", type(e).__name__, str(e)[:80], file=sys.stderr); await asyncio.sleep(1)
    asyncio.create_task(rtds())
    while True:
        st = int(time.time()) // 300 * 300; mk = gamma_market(st)
        if not mk: await asyncio.sleep(2); continue
        books = {mk["up"]: Book("Up"), mk["down"]: Book("Down")}
        print(f"\n### {mk['question']}  start {datetime.datetime.fromtimestamp(st, datetime.timezone.utc).strftime('%H:%M:%SZ')}")
        fh = None
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True); fh = open(os.path.join(csv_dir, f"round_{st}.csv"), "w")
            fh.write("t,elapsed,side,bb,ba,top5_bid_sh,top5_ask_sh,bid_levels...\n")
        try:
            async with websockets.connect(WS_MARKET, ping_interval=None, max_size=None) as ws:
                await ws.send(json.dumps({"assets_ids": list(books), "type": "market"}))
                nxt = time.time() + 1
                while time.time() < st + 300:
                    try: raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, nxt - time.time()))
                    except asyncio.TimeoutError: raw = None
                    if raw:
                        msgs = json.loads(raw); msgs = msgs if isinstance(msgs, list) else [msgs]
                        for m in msgs: apply_book_msg(books, m)
                    if time.time() >= nxt:
                        t = time.time(); e = t - st; nxt += 1
                        up = books[mk["up"]]; ubb, uba = up.top()
                        chance = (ubb + uba) / 2 if (ubb is not None and uba is not None) else None
                        for b in books.values():
                            print(b.line(t, e, spot["v"], spot["open"] if spot.get("open_st") == st else None, chance))
                            if fh: fh.write(",".join(str(x) for x in b.csv(t, e)) + "\n")
                        if fh: fh.flush()
        except Exception as e:
            print("market feed error", type(e).__name__, str(e)[:80], file=sys.stderr); await asyncio.sleep(1)
        finally:
            if fh: fh.close()


def replay(start, tokens_path):
    tokens = json.load(open(tokens_path)); names = {a: tk["outcome"] for a, tk in tokens.items() if tk["start"] == start}
    if not names: print("no tokens for start", start); return
    books = {a: Book(n) for a, n in names.items()}; nxt = start + 1.0; spot = None; opn = None; spot_q = collections.deque()
    for line in sys.stdin:
        r = line.rstrip("\n").split(",")
        if len(r) < 9: continue
        t = int(r[0]) / 1000.0; k = r[1]
        if k == "spot_tick": spot_q.append((t + 1.44, float(r[5]))); continue
        if k == "twap_tick":
            if opn is None and 0 <= t - start <= 3: opn = float(r[5])
            continue
        if r[3] not in books: continue
        b = books[r[3]]
        while spot_q and spot_q[0][0] <= t: spot = spot_q.popleft()[1]
        if k == "price_change":
            L = b.bids if r[4] == "BUY" else b.asks; p = round(float(r[5]), 2); sz = float(r[6])
            if sz <= 0: L.pop(p, None)
            else: L[p] = sz
        if k in ("price_change", "bba"):
            if r[7] and float(r[7]) > 0.005: b.bb = float(r[7])
            if r[8] and float(r[8]) > 0.005: b.ba = float(r[8])
        elif k == "trade": b.tape.append((t, r[4], float(r[5]), float(r[6])))
        while t >= nxt and nxt <= start + 300:
            e = nxt - start; up = next(x for x in books.values() if x.name == "Up"); ubb, uba = up.top()
            chance = (ubb + uba) / 2 if (ubb is not None and uba is not None) else None
            for bk in books.values(): print(bk.line(nxt, e, spot, opn, chance))
            nxt += 1
        if t > start + 301: break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true"); ap.add_argument("--live", action="store_true"); ap.add_argument("--csv", default=None)
    ap.add_argument("--replay", type=int, default=None); ap.add_argument("tokens", nargs="?")
    a = ap.parse_args()
    if a.snapshot: snapshot()
    elif a.live: asyncio.run(live(a.csv))
    elif a.replay: replay(a.replay, a.tokens)
    else: ap.print_help()


if __name__ == "__main__":
    main()
