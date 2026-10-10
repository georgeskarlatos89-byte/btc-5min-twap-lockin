#!/usr/bin/env python3
"""
chain_verify.py - the ONLY authoritative outcome source for auto-resolved crypto rounds:
the CTF payout vector on Polygon.

  payoutNumerators(conditionId, idx) on 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
  idx 0 = "Up", idx 1 = "Down"; winner = index with payout 1 (both 0 => not resolved yet)

FOURTH WITNESS: the house Data API v2 resolution record (status resolved, not disputed,
no extended review) is pulled per 20 ids and cross-checked.

v2 changes: accepts the simulator's own CSV (sim_rounds.csv / bot_rounds.csv): the label
column may be `outcome` (up/down) OR `settled` (Up/Down); rows without a condition_id are
reported as NO-CONDITION-ID instead of crashing; summary prints the agreement per label source.

Usage:
  chain_verify.py --csv sim_rounds.csv [--sample N] [--seed S] [--no-house]
Writes <csv>.chain.csv adding chain_outcome + house_* columns.
"""
import argparse, csv, json, random, time, urllib.request

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
SEL = "0x0504c814"   # payoutNumerators(bytes32,uint256)
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org", "https://1rpc.io/matic"]
HOUSE_V2 = "https://data-api.polymarket.com/v2/resolutions?condition="


def post(u, payload, t=8):
    req = urllib.request.Request(u, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=t) as r:
        j = json.loads(r.read().decode())
    if isinstance(j, dict) and "error" in j:
        raise RuntimeError(str(j["error"]))
    return j


def chain_outcome(cid, tries=3):
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": CTF, "data": SEL + cid[2:] + hex(i)[2:].rjust(64, "0")}, "latest"]} for i in (0, 1)]
    for t in range(tries):
        try:
            r = post(RPCS[t % len(RPCS)], batch)
            tot = [int(x["result"], 16) for x in sorted(r, key=lambda y: y["id"])]
            if tot[0] == tot[1] == 0:
                return "pending"
            return "up" if tot[0] > tot[1] else "down"
        except Exception:
            time.sleep(0.3)
    return "RPC_ERR"


def house_records(cids, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(HOUSE_V2 + ",".join(cids), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return {d["condition_id"]: d for d in json.loads(r.read().decode())["data"]}
        except Exception:
            time.sleep(0.4)
    return {}


def house_cols(rec):
    if rec is None:
        return {"house_status": "", "house_disputed": "", "house_extended": "", "house_tx": "",
                "cross_verdict": "HOUSE-MISSING"}
    dis, ext = bool(rec.get("was_disputed")), bool(rec.get("extended_review"))
    st = rec.get("status")
    verdict = ("OK" if st == "resolved" and not dis and not ext
               else ("DISPUTED" if dis else ("EXTENDED_REVIEW" if ext else "NOT-RESOLVED:" + str(st))))
    return {"house_status": st, "house_disputed": dis, "house_extended": ext,
            "house_tx": rec.get("transaction_hash", ""), "cross_verdict": verdict}


def label_of(row):
    """the CSV's own label, normalised to up/down/'' (accepts outcome or settled columns)."""
    v = (row.get("outcome") or row.get("settled") or row.get("chain_label") or "").strip().lower()
    return v if v in ("up", "down") else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--sample", type=int, default=0, help="verify a random sample of N rows (0=all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-house", action="store_true")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv, newline="")))
    if not rows:
        print("empty csv"); return
    idxs = list(range(len(rows)))
    if a.sample:
        random.seed(a.seed); idxs = sorted(random.sample(idxs, min(a.sample, len(idxs))))
    HOUSE = {}
    if not a.no_house:
        cids = sorted({rows[k]["condition_id"] for k in idxs if rows[k].get("condition_id")})
        for i in range(0, len(cids), 20):
            HOUSE.update(house_records(cids[i:i + 20])); time.sleep(0.25)
    out_rows = []
    m_ = {"match": 0, "differ": 0, "pending": 0, "err": 0, "no_condition_id": 0, "no_label": 0, "house_flags": 0}
    t00 = time.time()
    for i, k in enumerate(idxs):
        r = dict(rows[k])
        cid = (r.get("condition_id") or "").strip()
        if not cid:
            r["chain_outcome"] = "NO-CONDITION-ID"; m_["no_condition_id"] += 1
            r.update({"house_status": "", "house_disputed": "", "house_extended": "", "house_tx": "", "cross_verdict": "SKIPPED"})
            out_rows.append(r); continue
        r["chain_outcome"] = chain_outcome(cid)
        if a.no_house:
            r.update({"house_status": "", "house_disputed": "", "house_extended": "", "house_tx": "", "cross_verdict": "SKIPPED"})
        else:
            hc = house_cols(HOUSE.get(cid)); r.update(hc)
            if hc["cross_verdict"] != "OK":
                m_["house_flags"] += 1
        out_rows.append(r)
        co, lab = r["chain_outcome"], label_of(r)
        if co in ("up", "down"):
            if not lab: m_["no_label"] += 1
            elif co == lab: m_["match"] += 1
            else: m_["differ"] += 1
        elif co == "pending": m_["pending"] += 1
        else: m_["err"] += 1
        if i % 25 == 0:
            print(f"  {i}/{len(idxs)} match={m_['match']} differ={m_['differ']} pending={m_['pending']} "
                  f"err={m_['err']} house_flags={m_['house_flags']} {time.time() - t00:.0f}s", flush=True)
        time.sleep(0.04)
    outp = a.csv[:-4] + ".chain.csv" if a.csv.endswith(".csv") else a.csv + ".chain.csv"
    cols = list(out_rows[0].keys())
    for r in out_rows:
        for c in r:
            if c not in cols: cols.append(c)
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(out_rows)
    for r in [r for r in out_rows if r.get("cross_verdict") not in ("OK", "SKIPPED")][:10]:
        print("  HOUSE-FLAG:", r.get("condition_id"), r.get("cross_verdict"), r.get("house_tx"))
    for r in [r for r in out_rows if r["chain_outcome"] in ("up", "down") and label_of(r) and label_of(r) != r["chain_outcome"]][:10]:
        print("  LABEL-DIFFERS:", r.get("condition_id"), "csv says", label_of(r), "chain says", r["chain_outcome"])
    print(json.dumps(dict(checked=len(out_rows), **m_, seconds=round(time.time() - t00), saved=outp), indent=1))


if __name__ == "__main__":
    main()
