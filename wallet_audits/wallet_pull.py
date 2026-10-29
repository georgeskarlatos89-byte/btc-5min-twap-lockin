#!/usr/bin/env python3
"""wallet_pull.py - pull a wallet's full public activity (and taker-only trades) from the Polymarket data API into CSV.
The activity feed caps offset at 5,000, so we page by TIME WINDOWS (start/end unix) and offset inside each window.
  python3 wallet_pull.py 0x41e2... --since 2026-08-07 --out ~/wallets/norm1e69
Read-only, public data, no keys."""
import argparse, csv, datetime, json, os, sys, time, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 wallet-audit (read-only)"}
BASE = "https://data-api.polymarket.com"
COLS = ["timestamp", "type", "side", "outcome", "size", "price", "usdcSize", "title", "slug", "conditionId", "asset", "transactionHash"]


def get(url, tries=6):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception as e:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("gave up on " + url)


def pull(endpoint, w, t0, t1, out_path, log, win=3600):
    n = 0; wins = 0
    with open(out_path, "w", newline="") as fh:
        wr = csv.writer(fh); wr.writerow(COLS)
        s = t0
        while s < t1:
            e = min(s + win, t1); off = 0; got = 0
            while True:
                q = urllib.parse.urlencode(dict(user=w, limit=500, offset=off, start=s, end=e, sortBy="TIMESTAMP", sortDirection="ASC"))
                d = get(f"{BASE}/{endpoint}?{q}")
                if not isinstance(d, list): log(f"{endpoint} window {s}: {d}"); break
                for r in d: wr.writerow([r.get(c) for c in COLS])
                got += len(d); off += len(d)
                if len(d) < 500: break
                if off >= 5000: log(f"{endpoint} window {s}-{e}: hit 5000 cap, rows lost"); break
            n += got; wins += 1
            if wins % 50 == 0: log(f"{endpoint}: {wins} windows, {n} rows, at {datetime.datetime.fromtimestamp(e, datetime.timezone.utc):%Y-%m-%d %H:%M}Z")
            fh.flush(); s = e
    log(f"{endpoint} DONE rows {n}")
    return n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("wallet"); ap.add_argument("--since", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--win", type=int, default=3600); ap.add_argument("--skip-trades", action="store_true")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    logf = open(os.path.join(a.out, "pull.log"), "a")
    def log(m):
        line = f"[{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}] {m}"; print(line); logf.write(line + "\n"); logf.flush()
    t0 = int(datetime.datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()); t1 = int(time.time())
    log(f"wallet {a.wallet} since {a.since} windows {a.win}s")
    pull("activity", a.wallet, t0, t1, os.path.join(a.out, "activity.csv"), log, a.win)
    if not a.skip_trades:
        pull("trades", a.wallet, t0, t1, os.path.join(a.out, "trades_taker.csv"), log, a.win)
    open(os.path.join(a.out, "PULL-DONE"), "w").write("ok\n")


if __name__ == "__main__":
    main()
