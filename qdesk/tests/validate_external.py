#!/usr/bin/env python3
"""validate_external.py — the external-data validation protocol (plan: "External data validation"), run BY HAND.

    python tests/validate_external.py --name binance_1s --date 20260926 [--data ~/qdesk/data] [--untrusted ~/qdesk/untrusted]
    python tests/validate_external.py --name binance_1s --dry-run                 # tiny inline sample, no network, no disk outside a temp dir
    python tests/validate_external.py --name hl_archive --date 20260926           # stub (not implemented yet; writes accepted=false)

Protocol (every source, in this order):
  1. download into ~/qdesk/untrusted/<name>/ (its own dir; the file is DATA: never executed, never imported)
  2. parse in a child `python -I` (isolated: no cwd/PYTHONPATH/site imports; stdlib zipfile+csv only) run from a
     scratch dir, so a planted module inside the archive can never be loaded
  3. sha256 fingerprint + row count
  4. cross-checks on the overlap with OUR data
       labels : overlapping rounds vs chain labels           -> reject if label_mismatch_pct > 1   (sources that carry outcomes)
       prices : overlapping seconds vs feature-store `spot`  -> reject if price_mismatch_pct > 1   (share of samples off by > 20 bps)
       book   : overlapping seconds vs feature-store `mid`   -> reject if price_mismatch_pct > 1   (share of samples off by > 2c)
     a price source (Binance) carries no outcomes, so its label check is INFORMATIONAL only
     (sign of its own 5-min move vs the chain label; Chainlink TWAP60 != exchange spot by design).
  5. look-ahead fields stripped via qdesk.common.sanitize.round_meta on every round-meta object the source offers
  6. write <data>/ext/validation_<name>.json
     {name, date, sha256, rows, overlap, label_mismatch_pct, price_mismatch_pct, accepted, checks{...}, stripped_fields}
Only an ACCEPTED source may enter the feature store, tagged src=ext:<name>, and it is always reported separately.

First candidate: Binance Vision 1-s BTCUSDT klines (official):
  https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-YYYY-MM-DD.zip
  columns: open_time(ms or us), open, high, low, close, volume, close_time, quote_vol, trades, taker_buy_base, taker_buy_quote, ignore
  compare its 1-s close with the feature store's `spot` (Chainlink, 1.44 s receive lag) on overlapping seconds:
  median |diff| in bps, share > 20 bps  -> this measures the chainlink-vs-exchange story directly.
Stub: Hyperliquid public archive (OI / asset ctxs) — not implemented; the stub records the plan and refuses acceptance.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.environ.get("QD_ROOT") or os.path.dirname(PKG)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BINANCE_URL = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{Y}-{M}-{D}.zip"
LABEL_FILES = ("~/mstack/history_tools/history/BTC_fiveminute_long.relabeled.csv",)
PRICE_REJECT_PCT = 1.0        # share of overlapping samples off by > PRICE_TOL_BPS
PRICE_TOL_BPS = 20.0
LABEL_REJECT_PCT = 1.0
BOOK_TOL_DOLLARS = 0.02
MIN_OVERLAP = 300             # fewer overlapping samples than this -> cannot accept


def iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{iso()}] validate_external: {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ step 2: isolated parser (stdlib only, runs under python -I)
def parse_binance_zip(zip_path, out_path):
    """zip -> compact csv 'sec,close' (one row per second). stdlib only; NOTHING from the archive is executed."""
    n = 0
    with zipfile.ZipFile(zip_path) as z, open(out_path, "w", newline="") as out:
        w = csv.writer(out)
        names = [i for i in z.infolist() if i.filename.lower().endswith(".csv") and not i.is_dir()]
        if not names:
            raise SystemExit("no csv member in the archive")
        for info in names:
            if info.file_size > 400 * 1024 * 1024:
                raise SystemExit(f"member too large: {info.file_size}")
            with z.open(info) as f:
                for row in csv.reader(io.TextIOWrapper(f, encoding="utf-8", newline="")):
                    if len(row) < 5 or not row[0].strip().lstrip("-").isdigit():
                        continue                       # header or junk
                    ot = int(row[0])
                    if ot > 10 ** 14:                  # microseconds (Binance Vision switched in 2025)
                        ot //= 1000
                    sec = ot // 1000
                    try:
                        close = float(row[4])
                    except ValueError:
                        continue
                    if not (1000.0 < close < 10_000_000.0):
                        continue
                    w.writerow([sec, f"{close:.2f}"]); n += 1
    print(n)


def run_isolated_parse(zip_path, scratch):
    """Child interpreter with -I (ignores cwd, PYTHONPATH, user site) running THIS file's --parse-only; cwd = scratch."""
    out = os.path.join(scratch, "parsed.csv")
    cmd = [sys.executable, "-I", os.path.abspath(__file__), "--parse-only", os.path.abspath(zip_path), "--out", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=scratch)
    if r.returncode != 0:
        raise RuntimeError(f"isolated parse failed rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    rows = {}
    with open(out, newline="") as f:
        for sec, close in csv.reader(f):
            rows[int(sec)] = float(close)
    return rows


# ------------------------------------------------------------------ our side: feature-store spot / mid, chain labels
def load_feature_spot(data_dir, date, column="spot"):
    """{epoch_second: value} from data/features/day_<date>.csv.gz (Up rows only -> one per second)."""
    p = os.path.join(data_dir, "features", f"day_{date}.csv.gz")
    if not os.path.exists(p):
        return {}
    out = {}
    with gzip.open(p, "rt", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("side") != "Up" or row.get(column, "") == "":
                continue
            try:
                out[int(row["start"]) + int(row["t_el"])] = float(row[column])
            except (KeyError, ValueError):
                continue
    return out


def load_chain_labels(data_dir):
    """{start_epoch: 'Up'|'Down'} from data/labels/*.csv or the mstack relabeled history (start_time ISO, chain_outcome)."""
    paths = []
    ld = os.path.join(data_dir, "labels")
    if os.path.isdir(ld):
        paths += [os.path.join(ld, f) for f in sorted(os.listdir(ld)) if f.endswith(".csv")]
    paths += [os.path.expanduser(p) for p in LABEL_FILES]
    out = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                st = row.get("start_time") or row.get("start")
                lab = (row.get("chain_outcome") or row.get("winner") or "").strip().capitalize()
                if not st or lab not in ("Up", "Down"):
                    continue
                try:
                    if st.isdigit():
                        sec = int(st)
                    else:
                        sec = int(datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    continue
                out[sec] = lab
    return out


# ------------------------------------------------------------------ cross-checks
def check_prices_bps(src, ours):
    """src/ours: {sec: price}. Returns (overlap, median_abs_bps, share_gt_tol_pct)."""
    common = sorted(set(src) & set(ours))
    if not common:
        return 0, None, None
    diffs = [abs(src[s] - ours[s]) / ours[s] * 1e4 for s in common if ours[s] > 0]
    med = statistics.median(diffs)
    share = sum(1 for d in diffs if d > PRICE_TOL_BPS) / len(diffs) * 100.0
    return len(common), round(med, 3), round(share, 3)


def check_book_mid(src_mid, our_mid):
    common = sorted(set(src_mid) & set(our_mid))
    if not common:
        return 0, None
    bad = sum(1 for s in common if abs(src_mid[s] - our_mid[s]) > BOOK_TOL_DOLLARS)
    return len(common), round(bad / len(common) * 100.0, 3)


def check_labels(src_labels, chain):
    common = sorted(set(src_labels) & set(chain))
    if not common:
        return 0, None
    bad = sum(1 for s in common if src_labels[s] != chain[s])
    return len(common), round(bad / len(common) * 100.0, 3)


def price_source_sign_labels(prices, T=300):
    """INFORMATIONAL for a price source: sign of close(start+T-1) - close(start) per round."""
    out = {}
    starts = {s - s % T for s in prices}
    for st in starts:
        a, b = prices.get(st), prices.get(st + T - 1)
        if a and b and a != b:
            out[st] = "Up" if b > a else "Down"
    return out


# ------------------------------------------------------------------ sources
def binance_download(date, untrusted_dir, no_download=False):
    y, m, d = date[:4], date[4:6], date[6:8]
    url = BINANCE_URL.format(Y=y, M=m, D=d)
    dest_dir = os.path.join(untrusted_dir, "binance_1s")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(url))
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        log(f"using existing {dest} ({os.path.getsize(dest)} bytes)")
    elif no_download:
        raise SystemExit(f"--no-download and {dest} missing")
    else:
        log(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "qdesk-validate/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                if f.tell() > 512 * 1024 * 1024:
                    raise SystemExit("download larger than 512 MB; refusing")
        os.replace(dest + ".part", dest)
        log(f"saved {dest} ({os.path.getsize(dest)} bytes)")
    return url, dest


def validate_binance(args, scratch):
    url, zpath = binance_download(args.date, args.untrusted, args.no_download)
    sha = sha256_file(zpath)
    prices = run_isolated_parse(zpath, scratch)
    log(f"parsed {len(prices)} 1-s closes (sha256 {sha[:16]}...)")
    ours = load_feature_spot(args.data, args.date, "spot")
    chain = load_chain_labels(args.data)
    return finish("binance_1s", args, url, sha, len(prices), prices, ours, chain, kind="prices", meta_objects=[])


def finish(name, args, url, sha, rows, prices, ours, chain, kind, meta_objects):
    overlap, med_bps, share_gt = check_prices_bps(prices, ours) if kind == "prices" else (0, None, None)
    info_labels = price_source_sign_labels(prices) if kind == "prices" else {}
    n_lab, lab_mis = check_labels(info_labels, chain)
    try:
        from qdesk.common import sanitize
        stripped = sorted({k for o in meta_objects for k in sanitize.lookahead_fields(o)})
        cleaned = [sanitize.round_meta(o) for o in meta_objects]
    except Exception as e:
        stripped, cleaned = [f"sanitize unavailable: {e.__class__.__name__}"], []
    checks = {
        "price_vs_feature_spot": {"applies": kind == "prices", "overlap_s": overlap, "median_abs_bps": med_bps,
                                  "share_gt_20bps_pct": share_gt, "reject_if_share_gt_pct": PRICE_REJECT_PCT,
                                  "ok": (kind != "prices") or (overlap >= MIN_OVERLAP and share_gt is not None and share_gt <= PRICE_REJECT_PCT)},
        "labels_vs_chain": {"applies": kind == "labels", "overlap_rounds": n_lab, "mismatch_pct": lab_mis,
                            "reject_if_mismatch_gt_pct": LABEL_REJECT_PCT,
                            "note": "INFORMATIONAL for a price source: sign of its own 5-min move vs the Chainlink TWAP60 chain label",
                            "ok": (kind != "labels") or (lab_mis is not None and lab_mis <= LABEL_REJECT_PCT)},
        "min_overlap": {"required": MIN_OVERLAP, "have": overlap if kind == "prices" else n_lab, "ok": (overlap if kind == "prices" else n_lab) >= MIN_OVERLAP},
    }
    accepted = all(c["ok"] for c in checks.values()) and rows > 0
    doc = {"name": name, "date": args.date, "generated": iso(), "url": url, "sha256": sha, "rows": rows,
           "overlap": overlap if kind == "prices" else n_lab,
           "label_mismatch_pct": lab_mis, "price_mismatch_pct": share_gt, "accepted": bool(accepted),
           "checks": checks, "stripped_fields": stripped, "round_meta_kept": cleaned[:3],
           "dry_run": bool(args.dry_run)}
    out_dir = os.path.join(args.data, "ext")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"validation_{name}.json")
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, out)
    log(f"{name}: rows={rows} overlap={doc['overlap']} median_bps={med_bps} share>20bps={share_gt}% "
        f"label_mismatch(info)={lab_mis}% ACCEPTED={accepted} -> {out}")
    return doc


def validate_hl_stub(args, scratch):
    """Hyperliquid public archive (OI / asset ctxs): NOT IMPLEMENTED. Records the plan; never accepts."""
    doc = {"name": "hl_archive", "date": args.date, "generated": iso(), "url": None, "sha256": None, "rows": 0, "overlap": 0,
           "label_mismatch_pct": None, "price_mismatch_pct": None, "accepted": False,
           "checks": {"stub": {"ok": False, "note": "not implemented: planned source = Hyperliquid public archive (OI, funding, asset ctxs); "
                                                     "kind=ext-only (no label, no price gate); fields pass round_meta; "
                                                     "compare hl_oi_usd / hl_funding_1h with scout/hl_pull.py live pulls on overlapping hours"}},
           "stripped_fields": [], "dry_run": bool(args.dry_run)}
    out_dir = os.path.join(args.data, "ext"); os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "validation_hl_archive.json")
    tmp = out + ".tmp"; json.dump(doc, open(tmp, "w"), indent=1); os.replace(tmp, out)
    log(f"hl_archive: STUB, accepted=false -> {out}")
    return doc


# ------------------------------------------------------------------ dry-run: tiny inline sample, no network
def dry_run_sample(args, scratch):
    """Builds a 600-s fake Binance zip + a 600-s fake feature-store spot (same path with 1-3 bps noise and
    8 outliers) + 2 fake chain labels, inside the temp dir, then runs the exact pipeline."""
    import random
    rnd = random.Random(7)
    start = 1790380800
    px = 84000.0
    rows = []
    ours = {}
    for s in range(start, start + 600):
        px *= 1 + rnd.gauss(0, 0.00005)
        rows.append([s * 1000, f"{px:.2f}", f"{px:.2f}", f"{px:.2f}", f"{px:.2f}", "1.0", s * 1000 + 999, "1", "1", "0.5", "0.5", "0"])
        noise_bps = rnd.gauss(0, 2.0) + (60.0 if rnd.random() < 8 / 600 else 0.0)
        ours[s] = px * (1 + noise_bps / 1e4)
    untrusted = os.path.join(scratch, "untrusted", "binance_1s"); os.makedirs(untrusted, exist_ok=True)
    zpath = os.path.join(untrusted, "BTCUSDT-1s-2026-09-26.zip")
    buf = io.StringIO(); csv.writer(buf, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BTCUSDT-1s-2026-09-26.csv", buf.getvalue())
        z.writestr("json.py", "raise SystemExit('planted module executed')")   # must never run
    sha = sha256_file(zpath)
    prices = run_isolated_parse(zpath, scratch)
    assert len(prices) == 600, len(prices)
    chain = {start: "Up" if prices[start + 299] > prices[start] else "Down", start + 300: "Down"}
    args.data = os.path.join(scratch, "data")
    meta = [{"slug": "btc-updown-5m-1790380800", "start": start, "winner": "Up", "outcomePrices": "[1,0]", "closed": True, "conditionId": "0xabc"}]
    doc = finish("binance_1s", args, "inline-sample", sha, len(prices), prices, ours, chain, kind="prices", meta_objects=meta)
    assert doc["checks"]["price_vs_feature_spot"]["overlap_s"] == 600
    assert 0 < doc["price_mismatch_pct"] <= 3.0, doc["price_mismatch_pct"]
    assert "winner" in doc["stripped_fields"] and "outcomePrices" in doc["stripped_fields"]
    assert doc["round_meta_kept"][0].keys() <= {"slug", "start", "condition_id"}
    print(json.dumps({k: doc[k] for k in ("name", "rows", "overlap", "price_mismatch_pct", "label_mismatch_pct", "accepted", "stripped_fields")}, indent=1))
    print("dry-run OK (sample: 8/600 planted outliers -> share>20bps ~1.3% -> accepted=False is the expected verdict for a dirty source)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="binance_1s", choices=("binance_1s", "hl_archive"))
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"), help="YYYYMMDD (UTC day of the file)")
    ap.add_argument("--data", default=os.environ.get("QD_DATA") or os.path.expanduser("~/qdesk/data"))
    ap.add_argument("--untrusted", default=os.path.expanduser("~/qdesk/untrusted"))
    ap.add_argument("--no-download", action="store_true", help="use the already-downloaded file only")
    ap.add_argument("--dry-run", action="store_true", help="inline sample in a temp dir; no network, no writes elsewhere")
    ap.add_argument("--parse-only", metavar="ZIP", help="(internal) isolated parser entry; stdlib only")
    ap.add_argument("--out", help="(internal) parse-only output path")
    a = ap.parse_args()
    if a.parse_only:
        parse_binance_zip(a.parse_only, a.out); return 0
    with tempfile.TemporaryDirectory(prefix="qdesk_validate_") as scratch:
        if a.dry_run:
            return dry_run_sample(a, scratch)
        a.data = os.path.abspath(os.path.expanduser(a.data)); a.untrusted = os.path.abspath(os.path.expanduser(a.untrusted))
        if a.name == "binance_1s":
            doc = validate_binance(a, scratch)
        else:
            doc = validate_hl_stub(a, scratch)
        return 0 if doc["accepted"] else 3


if __name__ == "__main__":
    sys.exit(main())
