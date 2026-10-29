#!/usr/bin/env python3
"""bme_to_features.py - one recorded BME day -> the per-second feature store file (INTERFACES.md section 2).

  zcat events_YYYYMMDD.csv.gz | mawk <filter, see build_days.sh> \
      | python3 bme_to_features.py tokens_btc5m_range.json --day YYYYMMDD --out data/features/day_YYYYMMDD.csv.gz
  python3 bme_to_features.py tokens.json --in tests/fixtures/bme_sample_20260926_first_hour.csv.gz --day 20260926 --out /tmp/x.csv.gz

Input rows: BME CSV (header optional) ts_ms,event_type,market,asset,side,price,size,best_bid,best_ask,hash16,extra.
Rows are produced for every 5m round of the token map whose start falls inside --day (and 15m rounds when --tokens15
is given). A FeatureState is created for a round when its first asset event arrives or 90 s before its start
(whichever comes first) so the book and the spot history exist at t_el = 0; spot/twap ticks are fed to every live
state. Rows are emitted once events up to s + 1.2 have been seen; the incomplete tail at end-of-stream is dropped
(counted on stderr). Streams; memory = the live rounds only. Writes with tmp + os.replace.
"""
import argparse
import csv
import gzip
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.features.features import FEATURE_COLUMNS, FeatureState, row_to_list, T_OF  # noqa: E402

PRE_CREATE_S = 90.0      # create a round's state this long before its start (spot history for spot_vol_30s)
DROP_AFTER_S = 5.0       # drop a state this long after its end (all rows flushed by then)


def load_rounds(path, market):
    """tokens map (asset -> {start, outcome, other, winner, ...}) -> {start: {"Up": asset, "Down": asset, "winner": w}}"""
    with open(path, "r", encoding="utf-8") as f:
        tok = json.load(f)
    rounds = {}
    for asset, tk in tok.items():
        st = int(tk["start"])
        r = rounds.setdefault(st, {"winner": tk.get("winner")})
        r[tk["outcome"]] = str(asset)
    out = {}
    T = T_OF[market]
    for st, r in rounds.items():
        if "Up" in r and "Down" in r and st % T == 0:
            out[st] = r
    return out


def day_bounds(day):
    import datetime as dt
    d0 = dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    t0 = int(d0.timestamp())
    return t0, t0 + 86400


def open_input(path):
    if path is None or path == "-":
        return io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace", newline="")
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def parse_book(r):
    """BME `book` row -> (bids, asks) from the JSON in the extra column (which may itself contain commas or CSV
    quoting); None when it cannot be parsed."""
    if len(r) <= 10:
        return None
    raw = ",".join(r[10:]).strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1].replace('""', '"')
    try:
        j = json.loads(raw)
        bids = [(float(x["price"]), float(x["size"])) for x in j.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in j.get("asks", [])]
    except Exception:
        return None
    return bids, asks


def blackout_loader():
    try:
        from qdesk.scout import macro  # agent B's module; optional here
    except Exception:
        return None
    if hasattr(macro, "is_blackout"):
        return macro.is_blackout                     # 0/1
    return getattr(macro, "check_macro_blackout", None)   # (bool, event, secs): features.py unwraps the tuple


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens", nargs="?", default=None, help="5m tokens map json (asset -> start/outcome/other/winner)")
    ap.add_argument("--tokens", dest="tokens_opt", default=None, help="same as the positional tokens argument")
    ap.add_argument("--tokens15", default=None, help="15m tokens map json (optional)")
    ap.add_argument("--day", default=None, help="YYYYMMDD: rounds whose start falls inside this UTC day (default --date)")
    ap.add_argument("--date", default=None, help="alias of --day (common CLI convention)")
    ap.add_argument("--out", default=None, help="output csv.gz (default $QD_DATA/features/day_<day>.csv.gz)")
    ap.add_argument("--in", dest="inp", default=None, help="input file (csv or csv.gz); default stdin")
    ap.add_argument("--spot-lag", type=float, default=1.44)
    ap.add_argument("--src", default="bme")
    ap.add_argument("--no-blackout", action="store_true", help="do not call scout.macro (macro_blackout = 0)")
    ap.add_argument("--dry-run", action="store_true", help="parse and build, write nothing")
    a = ap.parse_args()
    a.day = a.day or a.date
    a.tokens = a.tokens_opt or a.tokens
    data_dir = os.environ.get("QD_DATA", os.path.expanduser("~/qdesk/data"))
    if not a.day:
        ap.error("--day (or --date) is required")
    if not a.tokens:
        cand = os.path.join(data_dir, "tokens", "tokens_btc5m_range.json")
        if not os.path.exists(cand):
            ap.error("tokens map required (positional or --tokens)")
        a.tokens = cand
    if not a.out:
        a.out = os.path.join(data_dir, "features", f"day_{a.day}.csv.gz")

    t_start = time.time()
    d0, d1 = day_bounds(a.day)
    rounds = {"5m": load_rounds(a.tokens, "5m")}
    if a.tokens15:
        rounds["15m"] = load_rounds(a.tokens15, "15m")
    # (market, start) in time order, restricted to the day; asset -> (market, start)
    sched = []
    asset_round = {}
    for mk, rr in rounds.items():
        for st, r in rr.items():
            if d0 <= st < d1:
                sched.append((st, mk))
                asset_round[r["Up"]] = (mk, st)
                asset_round[r["Down"]] = (mk, st)
    sched.sort()
    blk = None if a.no_blackout else blackout_loader()
    print(f"[bme_to_features] day {a.day}: {len([s for s in sched if s[1] == '5m'])} 5m rounds, "
          f"{len([s for s in sched if s[1] == '15m'])} 15m rounds in the token maps; blackout_fn={'yes' if blk else 'none'}", file=sys.stderr)

    live = {}            # (market, start) -> FeatureState
    sched_i = 0
    n_in = 0
    n_rows = 0
    n_used = 0
    secs_per_round = {}  # (market, start) -> seconds emitted (both sides / 2)
    last_t = None

    out_path = a.out
    tmp_path = out_path + ".tmp"
    if not a.dry_run:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        fh = gzip.open(tmp_path, "wt", encoding="utf-8", newline="", compresslevel=6)
    else:
        fh = io.StringIO()
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(FEATURE_COLUMNS)

    def make_state(key):
        mk, st = key
        r = rounds[mk][st]
        fs = FeatureState(mk, st, r["Up"], r["Down"], spot_lag=a.spot_lag, blackout_fn=blk, src=a.src)
        live[key] = fs
        return fs

    def flush_all(t_now):
        nonlocal n_rows
        dead = []
        for key, fs in live.items():
            rows = fs.flush(t_now)
            if rows:
                for r in rows:
                    w.writerow(row_to_list(r))
                n_rows += len(rows)
                secs_per_round[key] = secs_per_round.get(key, 0) + len(rows) / 2.0
            if t_now > fs.end + DROP_AFTER_S:
                dead.append(key)
        for key in dead:
            live.pop(key, None)

    fin = open_input(a.inp)
    try:
        for line in fin:
            n_in += 1
            if n_in == 1 and line.startswith("ts_ms"):
                continue
            r = line.rstrip("\r\n").split(",")
            if len(r) < 9:
                continue
            try:
                t = int(r[0]) / 1000.0
            except ValueError:
                continue
            k = r[1]
            # create states whose pre-window has begun
            while sched_i < len(sched) and sched[sched_i][0] - PRE_CREATE_S <= t:
                key = (sched[sched_i][1], sched[sched_i][0])
                if key not in live:
                    make_state(key)
                sched_i += 1
            if k == "spot_tick":
                try:
                    v = float(r[5])
                except ValueError:
                    continue
                for fs in live.values():
                    fs.on_spot(t, v)
                continue
            if k == "twap_tick":
                try:
                    v = float(r[5])
                except ValueError:
                    continue
                for fs in live.values():
                    fs.on_twap(t, v)
                continue
            asset = r[3]
            key = asset_round.get(asset)
            if key is None:
                continue
            fs = live.get(key)
            if fs is None:
                if t > key[1] + T_OF[key[0]] + DROP_AFTER_S:
                    continue                   # late straggler of a finished round
                fs = make_state(key)
            n_used += 1
            if k == "price_change":
                fs.on_price_change(asset, r[4], r[5], r[6] or 0.0, r[7] or None, r[8] or None, t)
            elif k == "bba":
                fs.on_bba(asset, r[7] or None, r[8] or None, t)
            elif k == "trade":
                fs.on_trade(asset, r[4], r[5], r[6] or 0.0, t)
            elif k == "book":
                bk = parse_book(r)
                if bk is None:
                    continue                   # unparseable snapshot: never wipe the levels
                fs.on_book(asset, bk[0], bk[1], t)
            else:
                continue
            # the other live states see the clock move (resolves their look-ahead helpers; no book change)
            for key2, fs2 in live.items():
                if key2 != key:
                    fs2.on_clock(t)
            last_t = t
            if (n_used & 1023) == 0:
                flush_all(t)
        if last_t is not None:
            flush_all(last_t)
    except EOFError as e:
        print(f"[bme_to_features] WARNING truncated gzip input after {n_in} rows: {e}", file=sys.stderr)
        if last_t is not None:
            flush_all(last_t)
    finally:
        fin.close()
    incomplete = sum(len(fs.pending) for fs in live.values())
    if not a.dry_run:
        fh.close()
        os.replace(tmp_path, out_path)
    el = time.time() - t_start
    print(f"[bme_to_features] day {a.day}: input rows {n_in}, used {n_used}, feature rows {n_rows} "
          f"({n_rows // 2} seconds), rounds with rows {len(secs_per_round)}, incomplete seconds dropped {incomplete}, "
          f"{el:.1f} s -> {out_path if not a.dry_run else '(dry-run)'}", file=sys.stderr)
    for key in sorted(secs_per_round):
        print(f"  {key[0]} {key[1]} seconds {int(secs_per_round[key])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
