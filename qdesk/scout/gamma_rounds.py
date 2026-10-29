#!/usr/bin/env python3
"""gamma_rounds.py - yesterday's BTC 5m and 15m rounds with winner, merged into the live token maps.

  python3 gamma_rounds.py [--date YYYYMMDD] [--dry-run]      (--date = the day the scout runs; rounds of the day before)

Writes data/tokens/tokens_btc5m_live.json and tokens_btc15m_live.json (asset -> round record, same format as
tokens_range.py) merged by asset (idempotent: re-running updates the same keys), plus the _assets.txt files.
Chain labels (QD_LABELS_5M / QD_LABELS_15M or the VM relabeled CSVs) override Gamma when present.
run(date, dry_run) returns {"5m": {n, up_rate, chain_verified, gamma_disagree, missing}, "15m": {...}}.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U
from qdesk.scout import tokens_range as TR


def live_map_path(variant):
    return U.data_path("tokens", f"tokens_btc{variant}_live.json")


def load_live(variant):
    return U.load_json(live_map_path(variant), {}) or {}


def rounds_of_day(tok, day):
    """Round records (one per start) of a token map restricted to a YYYYMMDD day."""
    t0 = U.parse_day(day); t1 = t0 + 86400
    seen = {}
    for a, r in tok.items():
        s = int(r.get("start", 0))
        if t0 <= s < t1 and s not in seen:
            seen[s] = r
    return seen


def day_stats(tok, day, chain_verified=0, disagree=0, missing=0):
    rs = rounds_of_day(tok, day)
    ups = sum(1 for r in rs.values() if r.get("winner") == "Up")
    return {"n": len(rs), "up_rate": round(ups / len(rs), 4) if rs else None,
            "chain_verified": int(chain_verified), "gamma_disagree": int(disagree), "missing": int(missing)}


def run(date=None, dry_run=False, log=U.log):
    day = U.yesterday(date)
    out = {}
    for variant in ("5m", "15m"):
        existing = load_live(variant)
        if dry_run:
            out[variant] = day_stats(existing, day)
            log(f"gamma_rounds dry-run {variant}: {out[variant]['n']} rounds already in the live map for {day}")
            continue
        tok, st = TR.build(variant, day, day, U.default_labels_path(variant), workers=4, log=log)
        existing.update(tok)
        TR.write_map(live_map_path(variant), existing)
        out[variant] = day_stats(existing, day, st["chain_verified"], st["disagree"], st["missing"])
        log(f"gamma_rounds {variant} {day}: {st['rounds']} rounds, missing {st['missing']}, chain-verified {st['chain_verified']}, "
            f"disagree {st['disagree']}, labels {st['labels_loaded']} -> {live_map_path(variant)}")
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(run(a.date, a.dry_run))


if __name__ == "__main__":
    main()
