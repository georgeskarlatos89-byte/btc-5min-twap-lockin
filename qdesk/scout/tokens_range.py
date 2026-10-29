#!/usr/bin/env python3
"""tokens_range.py - token map for every BTC 5m or 15m round in a date range (generalised pm_tokens.py).

  python3 tokens_range.py --variant 5m --from 20260926 --to 20261014 --labels chain.csv --out tokens_btc5m_range.json
  python3 tokens_range.py --variant 15m --from 20260926 --to 20261014 --out tokens_btc15m_range.json [--dry-run]

Output (same format as ~/pm/tokens_btc5m_range.json):
  asset -> {"start": epoch, "outcome": "Up"|"Down", "other": other asset, "winner": "Up"|"Down", "condition_id": ..}
plus <out>_assets.txt (one asset per line). Winner = chain label (relabeled CSV: start_time ISO, chain_outcome)
when given, else Gamma (closed market with outcomePrices 1/0); disagreements are counted and printed.
Gamma slug btc-updown-5m-<start> (step 300) / btc-updown-15m-<start> (step 900). Slug lookups only (no listing
call); if a listing call is ever added it must use order=endDate&ascending=true, never createdAt.
UA header on every request, 5 retries with backoff, 0.2 s pacing. Read-only, no keys.
"""
import argparse
import json
import os
import sys
import concurrent.futures as cf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

STEP = {"5m": 300, "15m": 900}
GAMMA = "https://gamma-api.polymarket.com"


def gamma_event(variant, start):
    """One round's market from Gamma -> (tokens {Up,Down}, gamma winner|None, condition_id, closed) or None."""
    for q in ("&closed=true", ""):
        ok, ev, _ = U.http_json(f"{GAMMA}/events?slug=btc-updown-{variant}-{start}{q}")
        if ok and ev and ev[0].get("markets"):
            m = ev[0]["markets"][0]
            try:
                outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"])
                pr = [float(x) for x in json.loads(m["outcomePrices"])]
            except Exception:
                return None
            w = outs[pr.index(1.0)] if (m.get("closed") and sorted(pr) == [0.0, 1.0]) else None
            return dict(zip(outs, ids)), w, m.get("conditionId"), bool(m.get("closed"))
    return None


# copied from C:\g\tlfix\paired_maker\pm_tokens.py (one), generalised to the variant
def one(variant, s):
    r = gamma_event(variant, s)
    if not r:
        return s, None, None, None
    t, w, cid, _ = r
    return s, t, w, cid


def build(variant, d0, d1, labels_path=None, workers=4, dry_run=False, log=U.log):
    """Token map for days d0..d1 inclusive (YYYYMMDD). Returns (tok, stats)."""
    step = STEP[variant]
    t0 = U.parse_day(d0); t1 = U.parse_day(d1) + 86400
    starts = list(range(t0, t1, step))
    lab = U.load_labels(labels_path)
    stats = dict(variant=variant, rounds=0, starts=len(starts), missing=0, no_winner=0, disagree=0,
                 chain_verified=0, labels_loaded=len(lab))
    tok = {}
    if dry_run:
        log(f"dry-run: would look up {len(starts)} {variant} slugs; labels loaded {len(lab)}")
        return tok, stats
    with cf.ThreadPoolExecutor(workers) as ex:
        for s, t, w, cid in ex.map(lambda s: one(variant, s), starts):
            if not t or "Up" not in t or "Down" not in t:
                stats["missing"] += 1; continue
            lw = lab.get(s)
            if lw and w and lw != w:
                stats["disagree"] += 1
            if lw:
                stats["chain_verified"] += 1
            win = lw or w
            if not win:
                stats["no_winner"] += 1; continue
            tok[t["Up"]] = {"start": s, "outcome": "Up", "other": t["Down"], "winner": win, "condition_id": cid}
            tok[t["Down"]] = {"start": s, "outcome": "Down", "other": t["Up"], "winner": win, "condition_id": cid}
    stats["rounds"] = len(tok) // 2
    return tok, stats


def write_map(out, tok):
    U.save_json(out, tok)
    ap = out[:-5] + "_assets.txt" if out.endswith(".json") else out + "_assets.txt"
    tmp = ap + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(tok.keys()) + ("\n" if tok else ""))
    os.replace(tmp, ap)
    return ap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=("5m", "15m"), default="5m")
    ap.add_argument("--from", dest="d0", required=True); ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--labels", default=None); ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--date", default=None, help="unused (uniform CLI)"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tok, st = build(a.variant, a.d0, a.d1, a.labels, a.workers, a.dry_run)
    if not a.dry_run:
        write_map(a.out, tok)
    print("rounds %d of %d, missing markets %d, no winner %d, gamma/chain disagreements %d, chain-verified %d, labels loaded %d"
          % (st["rounds"], st["starts"], st["missing"], st["no_winner"], st["disagree"], st["chain_verified"], st["labels_loaded"]))


if __name__ == "__main__":
    main()
