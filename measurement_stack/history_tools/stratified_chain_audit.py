#!/usr/bin/env python3
"""stratified_chain_audit.py — who tells the truth, and when?
Self-contained, per-round logging, batch JSON-RPC, incremental jsonl."""
import csv, json, random, time, urllib.request, datetime

CTF, SEL = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045", "0x0504c814"
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://rpc-mainnet.matic.quiknode.pro", "https://1rpc.io/matic"]

def post(u, payload, t=8):
    req = urllib.request.Request(u, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=t))

def hj(u, t=8, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=t))
        except Exception:
            if i == tries - 1: raise
            time.sleep(0.5)

def chain_outcome(cid):
    batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
              "params": [{"to": CTF, "data": SEL + cid[2:] + hex(i)[2:].rjust(64, "0")}, "latest"]} for i in (0, 1)]
    for rpc_i in range(len(RPCS)):
        try:
            r = post(RPCS[rpc_i], batch)
            tot = [int(x["result"], 16) for x in sorted(r, key=lambda y: y["id"])]
            if tot[0] == tot[1] == 0: return "pending"
            return "up" if tot[0] > tot[1] else "down"
        except Exception:
            continue
    return "RPC_ERR"

norm = lambda s: s.replace(".000Z", "Z")
pr = list(csv.DictReader(open("history/BTC_fiveminute.csv")))
long_rows = list(csv.DictReader(open("history/BTC_fiveminute_long.csv")))
long_by = {norm(r["start_time"]): r["outcome"] for r in long_rows}

buckets = {"<1bp": lambda g: g < 1, "1-2bp": lambda g: 1 <= g < 2,
           "2-4bp": lambda g: 2 <= g < 4, "4-8bp": lambda g: 4 <= g < 8,
           ">8bp": lambda g: g >= 8}
random.seed(11)
sample = []
for name, cond in buckets.items():
    pool = [r for r in pr if cond(abs(float(r["tail_gap_bps"]))) and norm(r["start_time"]) in long_by]
    sample += [(name, r) for r in random.sample(pool, min(40, len(pool)))]
print(f"sample: {len(sample)} rounds across {len(buckets)} gap buckets", flush=True)

out = open("history/chain_audit_stratified.jsonl", "w")
report = {}
for i, (name, r) in enumerate(sample):
    st = norm(r["start_time"])
    ts = int(datetime.datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp())
    t0 = time.time()
    try:
        m = hj(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{ts}")[0]["markets"][0]
        cid = m["conditionId"]
        g_fresh = "up" if m["outcomePrices"][0] == "1" else "down"
        co = chain_outcome(cid)
    except Exception as e:
        co, g_fresh = "FETCH_ERR", "?"
    rec = dict(bucket=name, start=st, gap_bps=r["tail_gap_bps"], pr=r["outcome"],
               gamma_long=long_by[st], gamma_fresh=g_fresh, chain=co)
    out.write(json.dumps(rec) + "\n"); out.flush()
    d = report.setdefault(name, dict(n=0, pr_wrong=0, long_wrong=0, fresh_wrong=0, other=0))
    d["n"] += 1
    if co not in ("up", "down"): d["other"] += 1
    else:
        if co != r["outcome"]: d["pr_wrong"] += 1
        if co != long_by[st]: d["long_wrong"] += 1
        if co != g_fresh: d["fresh_wrong"] += 1
    print(f"[{i+1:3d}/{len(sample)}] {st} gap={r['tail_gap_bps']:>7} pr={r['outcome']:4} "
          f"long={long_by[st]:4} fresh={g_fresh:4} chain={co:7} {time.time()-t0:.2f}s", flush=True)
    time.sleep(0.08)
out.close()
print(json.dumps(report, indent=1))
json.dump(report, open("history/chain_audit_stratified.json", "w"), indent=1)
