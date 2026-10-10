#!/usr/bin/env python3
"""
bme_to_replay.py - Step 0 data reuse: build replay files from data ALREADY on twapvm

  book  : BME bookstate_1s_YYYYMMDD.csv[.gz]  (series 5m, token_idx 0 = Up token;
          best_bid/best_ask per second, only seconds where both sides exist)
  prices: S2 sessions ticks.jsonl (topics crypto_prices_chainlink = spot,
          crypto_prices_twap_sixty = TWAP60; one tick per second each)
  fair  : fair_model.py (same code as the recorder and the bot)

Writes replay/YYYY-MM-DD.jsonl in the recorder's schema (one line per second per 5m round).
Read-only on every input. Usage:
  bme_to_replay.py --day 2026-10-09 [--bme DIR] [--s2 DIR] [--out DIR]
"""
import argparse, csv, glob, gzip, json, os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fair_model import FairModel

HOME = os.path.expanduser("~")
BME_DIR = os.path.join(HOME, "POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme")
S2_DIR = os.path.join(HOME, "s2_openprint/data/sessions")
T = 300


def open_any(p):
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p)


def load_book(bme_dir, day):
    """second -> (bb, ba) for the Up token of the 5m series."""
    tag = day.replace("-", "")
    paths = [p for p in (os.path.join(bme_dir, f"bookstate_1s_{tag}.csv"), os.path.join(bme_dir, f"bookstate_1s_{tag}.csv.gz")) if os.path.exists(p)]
    if not paths:
        raise SystemExit(f"no bookstate file for {day} in {bme_dir}")
    book = {}
    with open_any(paths[0]) as f:
        for r in csv.DictReader(f):
            if r["series"] != "5m" or r["token_idx"] != "0":
                continue
            try:
                book[int(r["ts_ms"]) // 1000] = (float(r["best_bid"]), float(r["best_ask"]),
                                                 float(r.get("bid_depth_top10") or 0), float(r.get("ask_depth_top10") or 0))
            except ValueError:
                continue
    return book


def load_ticks(s2_dir, day):
    """spot and twap ticks (observed ts, value) covering the day plus the hour before it."""
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t_lo, t_hi = (d0 - timedelta(hours=1)).timestamp(), (d0 + timedelta(days=1)).timestamp()
    spot, twap = [], []
    for sess in sorted(glob.glob(os.path.join(s2_dir, "*"))):
        name = os.path.basename(sess)
        try:
            st = datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if st < t_lo - 3600 or st > t_hi:
            continue
        p = os.path.join(sess, "ticks.jsonl")
        if not os.path.exists(p):
            continue
        for ln in open(p):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts = r.get("observed") or r.get("received")
            if ts is None or not (t_lo <= ts <= t_hi):
                continue
            (spot if r["topic"] == "crypto_prices_chainlink" else twap).append((float(ts), float(r["value"])))
    spot.sort(); twap.sort()
    return spot, twap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--bme", default=BME_DIR)
    ap.add_argument("--s2", default=S2_DIR)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "replay"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    book = load_book(a.bme, a.day)
    spot, twap = load_ticks(a.s2, a.day)
    print(f"{a.day}: book seconds {len(book)}, spot ticks {len(spot)}, twap ticks {len(twap)}", flush=True)
    d0 = datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_lo, day_hi = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())
    model = FairModel()
    si = ti = 0
    o_twap = {}
    n_rows = n_rounds = n_late = 0
    outp = os.path.join(a.out, f"{a.day}.jsonl")
    with open(outp, "w") as f:
        for sec in range(day_lo - 3600, day_hi):
            # feed every tick observed up to this second
            while si < len(spot) and spot[si][0] < sec + 1:
                model.on_spot(spot[si][0], spot[si][1]); si += 1
            while ti < len(twap) and twap[ti][0] < sec + 1:
                model.on_twap(twap[ti][0], twap[ti][1]); ti += 1
            if sec < day_lo:
                continue
            start = (sec // T) * T
            if start not in o_twap:
                # open reference = TWAP60 observed within 3 s after the boundary
                if model.twap_ts is not None and start <= model.twap_ts <= start + 3:
                    o_twap[start] = model.twap; n_rounds += 1
                elif sec - start > 3:
                    o_twap[start] = None; n_late += 1
                else:
                    continue
            o = o_twap.get(start)
            if o is None:
                continue
            t_rem = start + T - sec
            p = model.p_up(o, t_rem, sec)
            b = book.get(sec)
            spot_v = model.spot[-1][1] if model.spot else None
            rec = dict(ts=float(sec), label="5m", start=start, t_el=sec - start, t_rem=t_rem,
                       twap=model.twap, spot=spot_v, o_twap=o,
                       gap_bps=round((model.twap - o) / o * 1e4, 2) if model.twap else None,
                       fair_p=round(p, 4) if p is not None else None,
                       sigma1=round(model._sigma, 4) if model._sigma else None,
                       bb=b[0] if b else None, ba=b[1] if b else None,
                       bb_sz=b[2] if b else None, ba_sz=b[3] if b else None,
                       mid=round((b[0] + b[1]) / 2, 4) if b else None, spread=round(b[1] - b[0], 4) if b else None,
                       bids=[[b[0], b[2]]] if b else None, asks=[[b[1], b[3]]] if b else None, source="bme+s2")
            f.write(json.dumps(rec, separators=(",", ":")) + "\n"); n_rows += 1
    print(f"{a.day}: wrote {n_rows} rows for {n_rounds} rounds ({n_late} rounds skipped: no TWAP tick within 3 s of open) -> {outp}", flush=True)


if __name__ == "__main__":
    main()
