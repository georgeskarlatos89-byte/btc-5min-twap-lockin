#!/usr/bin/env python3
"""pm_tokens.py - token map for every BTC 5m round in a date range (for the replay, step 3).

  python3 pm_tokens.py --from 2026-09-26 --to 2026-10-14 --out tokens_btc5m_range.json [--labels chain.csv]

asset -> {"start": round start epoch, "outcome": "Up"/"Down", "other": other asset, "winner": "Up"/"Down"}
Winner from Gamma (closed market, outcomePrices 1/0); when a chain label file is given its outcome is
preferred and disagreements are counted. Read-only, no keys.
"""
import argparse, csv, json, time, urllib.request, concurrent.futures as cf
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 pm-tokens (read-only)", "Accept": "application/json"}


def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(1 + i)
    return None


def one(s):
    for q in ("&closed=true", ""):
        ev = get("https://gamma-api.polymarket.com/events?slug=btc-updown-5m-%d%s" % (s, q))
        if ev and ev[0].get("markets"):
            m = ev[0]["markets"][0]
            outs = json.loads(m["outcomes"]); ids = json.loads(m["clobTokenIds"]); pr = [float(x) for x in json.loads(m["outcomePrices"])]
            w = outs[pr.index(1.0)] if (m.get("closed") and sorted(pr) == [0.0, 1.0]) else None
            return s, dict(zip(outs, ids)), w, m.get("conditionId")
    return s, None, None, None


def load_labels(path):
    lab = {}
    if not path: return lab
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            st = r.get("start_time") or r.get("start") or r.get("round_start") or r.get("start_ts") or r.get("epoch")
            oc = (r.get("chain_outcome") or r.get("outcome") or r.get("label") or "").strip().lower()
            if not st or oc not in ("up", "down"):
                continue
            try:
                if "T" in st:
                    st = int(datetime.strptime(st.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                else:
                    st = int(float(st))
                lab[st] = oc.capitalize()
            except ValueError:
                pass
    return lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True); ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--labels", default=None)
    a = ap.parse_args()
    t0 = int(datetime.strptime(a.d0, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.strptime(a.d1, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86400
    starts = list(range(t0, t1, 300))
    lab = load_labels(a.labels)
    tok = {}; miss = 0; disagree = 0; nolabel = 0
    with cf.ThreadPoolExecutor(6) as ex:
        for s, t, w, cid in ex.map(one, starts):
            if not t:
                miss += 1; continue
            lw = lab.get(s)
            if lw and w and lw != w: disagree += 1
            win = lw or w
            if not win:
                nolabel += 1; continue
            tok[t["Up"]] = {"start": s, "outcome": "Up", "other": t["Down"], "winner": win, "condition_id": cid}
            tok[t["Down"]] = {"start": s, "outcome": "Down", "other": t["Up"], "winner": win, "condition_id": cid}
    json.dump(tok, open(a.out, "w"))
    open(a.out.replace(".json", "_assets.txt"), "w").write("\n".join(tok.keys()) + "\n")
    print("rounds %d of %d, missing markets %d, no winner %d, gamma/chain disagreements %d, labels loaded %d"
          % (len(tok) // 2, len(starts), miss, nolabel, disagree, len(lab)))


if __name__ == "__main__":
    main()
