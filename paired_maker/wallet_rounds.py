#!/usr/bin/env python3
"""wallet_rounds.py - reconstruct what a wallet did in given BTC 5m rounds from the public activity feed.
  python3 wallet_rounds.py --env ~/obm/.env "October 10, 12:20PM" "October 15, 8:30AM"
Read-only. Uses POLY_FUNDER (the public proxy address) from the env file; prints no secrets."""
import argparse, datetime, json, os, urllib.request
from dotenv import load_dotenv

UA = {"User-Agent": "Mozilla/5.0 wallet-rounds (read-only)"}


def ts(t):
    return datetime.datetime.utcfromtimestamp(t).strftime("%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--env", required=True); ap.add_argument("keys", nargs="+"); ap.add_argument("--max", type=int, default=3000)
    a = ap.parse_args(); load_dotenv(os.path.expanduser(a.env))
    w = os.environ["POLY_FUNDER"]; print("wallet (proxy, public):", w)
    rows = []; off = 0
    while off < a.max:
        u = f"https://data-api.polymarket.com/activity?user={w}&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC"
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        if not d: break
        rows += d; off += 500
        if len(d) < 500: break
    print("activity rows", len(rows), "oldest", ts(min(r["timestamp"] for r in rows)) if rows else None)
    for key in a.keys:
        sel = [r for r in rows if key in (r.get("title") or "")]
        if not sel:
            print("\nno rows for", key); continue
        print("\n==", sel[0]["title"])
        paid = 0.0; redeemed = 0.0; shares = {}
        for r in sorted(sel, key=lambda r: r["timestamp"]):
            typ = r["type"]; side = r.get("side") or ""; out = r.get("outcome") or ""; sz = float(r.get("size") or 0); px = float(r.get("price") or 0); usd = float(r.get("usdcSize") or 0)
            print(f"  {ts(r['timestamp'])}Z {typ:7} {side:4} {out:5} size {sz:7.2f} price {px:.2f} usdc {usd:8.2f} tx {str(r.get('transactionHash'))[:12]}")
            if typ == "TRADE" and side == "BUY":
                paid += usd; shares[out] = shares.get(out, 0.0) + sz
            if typ == "REDEEM":
                redeemed += usd
        print(f"  shares bought {shares} | paid {paid:.2f} | redeemed {redeemed:.2f} | net for this round {redeemed - paid:+.2f}")


if __name__ == "__main__":
    main()
