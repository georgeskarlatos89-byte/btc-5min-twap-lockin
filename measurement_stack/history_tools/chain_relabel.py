#!/usr/bin/env python3
"""
chain_relabel.py — FULL-corpus on-chain relabel of a gamma history corpus.

WHY: gamma's outcomePrices (what history_backfill_gamma.py records) can serve a
stale side for hours after settlement; /api/past-results `outcome` is derived
and wrong on ~28-35% of sub-2bp rounds. The CTF payout vector on Polygon is the
money truth. A 500-round random sample verified 500/500; this script does the
entire corpus so zero doubt remains. Full runbook: instructions.md

WHAT IT DOES
  - reads history/<corpus>.csv (needs the condition_id column)
  - for every row: payoutNumerators(conditionId, 0|1) via batched JSON-RPC on
    0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 (Polygon); index 0 = "Up"
  - checkpoints every result to <corpus>.chainlog.jsonl (append-only) so an
    interrupted run RESUMES where it left off (RPC_ERR rows are retried)
  - writes <corpus>.relabeled.csv (adds chain_outcome + label_changed columns),
    <corpus>.chain_changes.csv (only rows whose label flipped), and
    <corpus>.relabel_stats.json

USAGE (on the VPS)
  python3 chain_relabel.py --csv history/BTC_fiveminute_long.csv --limit 1000  # test first
  python3 chain_relabel.py --csv history/BTC_fiveminute_long.csv --workers 4   # full ~76k

Stdlib only. Expect ~2-3.5h for 76k rounds at 4 workers on public RPCs.
"""
import argparse, csv, json, os, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
SEL = "0x0504c814"   # payoutNumerators(bytes32,uint256)
# add/remove endpoints freely; a dead host just costs one failed attempt/round
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org",
        "https://rpc-mainnet.matic.quiknode.pro",
        "https://1rpc.io/matic"]

def post(u, payload, t=6):
    req = urllib.request.Request(u, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=t) as r:
        j = json.loads(r.read().decode())
    if isinstance(j, dict) and "error" in j: raise RuntimeError(str(j["error"]))
    return j

def chain_outcome(cid, tries=4):
    """winner per the on-chain payout vector; 'pending' if not yet resolved."""
    if not cid: return "NO_ID"
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": CTF, "data": SEL + cid[2:] + hex(i)[2:].rjust(64, "0")}, "latest"]} for i in (0, 1)]
    for t in range(tries):
        try:
            r = post(RPCS[t % len(RPCS)], batch)
            tot = [int(x["result"], 16) for x in sorted(r, key=lambda y: y["id"])]
            if tot[0] == tot[1] == 0: return "pending"
            return "up" if tot[0] > tot[1] else "down"
        except Exception:
            time.sleep(0.25 * (t + 1))
    return "RPC_ERR"

def load_checkpoint(path):
    ckpt = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("chain_outcome") not in (None, "RPC_ERR"):   # retry errors on resume
                        ckpt[d["condition_id"]] = d["chain_outcome"]
                except Exception:
                    pass
    return ckpt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--workers", type=int, default=4, help="parallel rounds in flight (keep <=6 on public RPCs)")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N pending rows (test mode)")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv)))
    ckpt_path = a.csv.replace(".csv", ".chainlog.jsonl")
    ckpt = load_checkpoint(ckpt_path)
    base_done = len(ckpt)
    pending = [r for r in rows if r.get("condition_id") and r["condition_id"] not in ckpt]
    if a.limit: pending = pending[:a.limit]
    total = base_done + len(pending)
    print(f"rows={len(rows)} already_done={base_done} pending={len(pending)} workers={a.workers}", flush=True)

    lock = threading.Lock()
    ckf = open(ckpt_path, "a")
    state = dict(done=base_done, new=0, errs=0, pend=0)
    t0 = time.time()

    def report():
        el = time.time() - t0
        rate = max(state["new"] / max(el, 1e-9), 1e-9)
        remain = total - state["done"]
        print(f"  {state['done']}/{total} (+{state['new']} this run) errs={state['errs']} "
              f"unresolved={state['pend']} rate={rate:.1f}/s eta={remain/rate/60:.0f}min", flush=True)

    if pending:
        ex = ThreadPoolExecutor(max_workers=a.workers)
        futures, it = {}, iter(pending)
        def feed(n):
            for _ in range(n):
                try: r = next(it)
                except StopIteration: return
                futures[ex.submit(chain_outcome, r["condition_id"])] = r
        feed(a.workers * 8)
        while futures:
            done_set, _ = wait(list(futures), return_when=FIRST_COMPLETED)
            for f in done_set:
                r = futures.pop(f)
                co = f.result()
                with lock:
                    ckf.write(json.dumps(dict(condition_id=r["condition_id"], start_time=r["start_time"],
                                              chain_outcome=co)) + "\n")
                    ckf.flush()
                    ckpt[r["condition_id"]] = co
                    state["done"] += 1; state["new"] += 1
                    state["errs"] += co == "RPC_ERR"; state["pend"] += co == "pending"
                    if state["new"] % 250 == 0: report()
                feed(1)
        report()
        ex.shutdown()
    else:
        print("nothing pending; writing outputs from checkpoint")
    ckf.close()

    # ---- finalize: relabeled csv + changes + stats -------------------------
    ckpt = load_checkpoint(ckpt_path)   # authoritative merged view
    outp = a.csv.replace(".csv", ".relabeled.csv")
    chp = a.csv.replace(".csv", ".chain_changes.csv")
    fields = list(rows[0].keys()) + ["chain_outcome", "label_changed"]
    n_changed = n_verified = 0
    changed_rows = []
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows:
            co = ckpt.get(r.get("condition_id", ""), "")
            r2 = dict(r); r2["chain_outcome"] = co
            flip = co in ("up", "down") and r["outcome"] not in ("", co)
            r2["label_changed"] = "yes" if flip else ""
            if co in ("up", "down"): n_verified += 1
            if flip:
                n_changed += 1; changed_rows.append(r2)
            w.writerow(r2)
    with open(chp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(changed_rows)
    stats = dict(source_csv=a.csv, rows=len(rows), chain_verified=n_verified,
                 labels_changed=n_changed,
                 change_rate=round(n_changed / max(n_verified, 1), 5),
                 checkpoint=ckpt_path, relabeled=outp, changes=chp,
                 finished_utc=datetime.now(timezone.utc).isoformat())
    json.dump(stats, open(a.csv.replace(".csv", ".relabel_stats.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))

if __name__ == "__main__":
    main()
