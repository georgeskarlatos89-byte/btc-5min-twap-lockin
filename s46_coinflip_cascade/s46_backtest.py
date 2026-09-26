#!/usr/bin/env python3
"""
S46 BACKTEST — Event Study for S4 Liquidation Cascade Continuation + S6 Asymmetry Check

Two parts:

1) S4 EVENT STUDY:
   Uses Moon Dev harvested data if available (data/all_liquidations/10m.json snapshots),
   otherwise simulates using Polymarket price history + synthetic cascade injection.
   Goal: measure average continuation 60s/180s after cascade onset, hit-rate by cascade size.

   Required inputs for real study:
   - ~/Moondev API/data/all_liquidations/10m/<date>/*.json.gz  (harvested since 2026-09-07)
   - Kraken 1m or 1s spot for BTC (or Polymarket RTDS spot logs)
   - Polymarket Gamma resolutions for 15m rounds
   - CLOB prices-history for intra-round book repricing speed

   Outputs: s46_event_study.csv, s46_event_study_report.md

2) S6 ASYMMETRY SIMULATION:
   Uses CLOB prices-history touches to estimate fill probability at 0.49 at bell.
   Question: do 0.49 bids fill both sides symmetrically or mainly by informed flow?

Usage:
  python3 s46_backtest.py --event-study --minutes 10080   # 7 days of 10m snapshots
  python3 s46_backtest.py --s6-sim --rounds 500
"""

import argparse, csv, json, math, os, glob, gzip, time, re
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "s46_event_study_report.md")
CSV_OUT = os.path.join(HERE, "s46_event_study.csv")

# Moon Dev harvest paths (VPS)
HARVEST_ROOTS = [
    os.path.expanduser("~/Moondev API/data/all_liquidations/10m"),
    os.path.expanduser("~/Moondev API/data/all_liq_10m"),
    "/home/user/s3_feefarm/docs/moondev_docs",  # fallback dummy
]

def parse_liq_rows(payload):
    rows = payload if isinstance(payload, list) else (payload.get("liquidations") or payload.get("data") or payload.get("rows") or [])
    out=[]
    for r in rows:
        if not isinstance(r, dict): continue
        ts = r.get("timestamp") or r.get("ts") or r.get("time") or 0
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
                ts = dt.timestamp()
            except:
                try: ts=float(ts)
                except: ts=0
        if ts>1e12: ts/=1000.0
        # 2026-09-26: the harvested rows carry the notional under "value" (verified on the fleet-box files:
        # {exchange, symbol, side, value, price, quantity, timestamp}); without it every row parsed as $0
        # and the study found zero cascades. Same key list as the live harvester.
        usd = r.get("usd") or r.get("usd_size") or r.get("notional") or r.get("value") or r.get("size_usd") or 0
        try: usd=float(usd)
        except: usd=0.0
        side=str(r.get("side") or r.get("direction") or "").lower()
        sym=str(r.get("symbol") or r.get("coin") or "").upper()
        # 2026-09-26 (S46 record Part #1): Binance COIN-margined symbols (BTCUSD_PERP, BTC_<yymmdd>) come with
        # quantity in CONTRACTS (100 USD face each) but value = price x contracts -> ~800x overstated
        # ($2.07M real showed as $1.72B and topped the event study). Use contracts x 100 for those.
        try:
            _q=float(r.get("quantity") or 0)
        except Exception:
            _q=0.0
        if re.search(r"(USD_PERP|_\d{6})$", sym) and _q>0:
            usd=_q*100.0
        out.append(dict(ts=ts, usd=usd, side=side, sym=sym))
    return out

def scan_cascades_in_snapshot(rows, cascade_usd=1_500_000, window_s=10):
    btc=[r for r in rows if (not r["sym"]) or ("BTC" in r["sym"] or "XBT" in r["sym"])]
    btc=[r for r in btc if r["ts"]>0 and r["usd"]>0]
    btc.sort(key=lambda r: r["ts"])
    if not btc:
        return []
    # sliding window 10s
    j=0
    hits=[]
    for i in range(len(btc)):
        while btc[i]["ts"] - btc[j]["ts"] > window_s:
            j+=1
        window=btc[j:i+1]
        sell=sum(r["usd"] for r in window if r["side"] in ("long","sell","liquidated_long","l"))
        buy=sum(r["usd"] for r in window if r["side"] in ("short","buy","liquidated_short","s"))
        if sell >= cascade_usd and buy < sell/3:
            hits.append(dict(ts=btc[i]["ts"], side="SELL", usd=sell, buy=buy, sell=sell, n=len(window)))
        elif buy >= cascade_usd and sell < buy/3:
            hits.append(dict(ts=btc[i]["ts"], side="BUY", usd=buy, buy=buy, sell=sell, n=len(window)))
    # dedupe: keep latest per 60s
    deduped=[]
    last_ts=0
    for h in sorted(hits, key=lambda x: x["ts"]):
        if h["ts"] - last_ts > 60:
            deduped.append(h)
            last_ts=h["ts"]
    return deduped

def load_harvested_snapshots():
    """Find all 10m liquidation snapshots from harvest dir"""
    files=[]
    for root in HARVEST_ROOTS:
        if os.path.exists(root):
            files.extend(glob.glob(os.path.join(root, "**", "*.json"), recursive=True))
            files.extend(glob.glob(os.path.join(root, "**", "*.json.gz"), recursive=True))
    return files

def event_study():
    files=load_harvested_snapshots()
    print(f"Found {len(files)} harvested files")
    if not files:
        print("No harvested data — running synthetic demo with 500 fake cascades")
        # synthetic demo
        rows=[]
        import random
        now=time.time()
        for i in range(500):
            usd = random.choice([1.6e6, 2.8e6, 4.5e6, 9e6])
            side = random.choice(["BUY","SELL"])
            # simulate continuation: larger cascades more likely to continue
            base_p = 0.55 + min(0.20, usd/30e6)
            win = random.random() < base_p
            rows.append(dict(ts=now- random.randint(0, 7*24*3600), side=side, usd=usd, cont_60s=win, cont_180s=win, outcome=side))
        # write report
        with open(CSV_OUT,"w",newline="") as f:
            w=csv.DictWriter(f, fieldnames=["ts","side","usd","cont_60s","cont_180s","outcome"])
            w.writeheader()
            w.writerows(rows)
        # aggregate
        agg=defaultdict(list)
        for r in rows:
            agg[r["side"]].append(r)
        with open(REPORT,"w") as out:
            out.write("# S4 Event Study — Synthetic Demo (no harvest data found)\n\n")
            out.write("No Moon Dev harvest data found in ~/Moondev API/. This is a synthetic demo to show the report shape.\n")
            out.write("Replace with real harvested data for actual P_model calibration.\n\n")
            for side in ["BUY","SELL"]:
                lst=agg[side]
                if not lst: continue
                win60=sum(1 for x in lst if x["cont_60s"])/len(lst)
                out.write(f"## {side} cascades: n={len(lst)} win60={win60:.1%}\n")
                for thresh in [1.5e6,2.5e6,4e6,8e6]:
                    sub=[x for x in lst if x["usd"]>=thresh]
                    if sub:
                        w60=sum(1 for x in sub if x["cont_60s"])/len(sub)
                        out.write(f"- >=${thresh/1e6:.1f}M: n={len(sub)} win60={w60:.1%}\n")
            out.write("\n### Recommended P_model prior (to be refined with real data)\n")
            out.write("- 1.5M: 0.60, 2.5M: 0.65, 4M: 0.70, 8M: 0.75 + spot bonus\n")
        print(f"Wrote {REPORT} and {CSV_OUT}")
        return

    # real path: iterate files
    all_hits=[]
    # 2026-09-26 (S46 record Part #1): the harvest has ~1,370 files/day (60 s cadence) and each file
    # is a rolling 10-min window, so (a) the old [:2000] cap silently analysed ~1.5 days of 20 and
    # (b) the same cascade appeared in ~10 consecutive snapshots and was counted ~10x. Now: all
    # files, and a GLOBAL dedupe (same side within 60 s = one event) after the scan.
    files=sorted(files)
    for fp in files:
        try:
            if fp.endswith(".gz"):
                with gzip.open(fp,"rt") as f:
                    payload=json.load(f)
            else:
                with open(fp) as f:
                    payload=json.load(f)
            rows=parse_liq_rows(payload)
            hits=scan_cascades_in_snapshot(rows)
            all_hits.extend(hits)
        except Exception as ex:
            # print(f"skip {fp}: {ex}")
            continue

    raw_hits=len(all_hits)
    dd=[]; last={}
    for h in sorted(all_hits, key=lambda x: x["ts"]):
        if h["ts"] - last.get(h["side"], 0) > 60:
            dd.append(h); last[h["side"]]=h["ts"]
        elif dd and dd[-1]["side"]==h["side"] and h["usd"]>dd[-1]["usd"]:
            dd[-1]=h                                  # keep the biggest print of the same event
    all_hits=dd
    print(f"Total cascade events found: {len(all_hits)} (from {raw_hits} raw window hits across {len(files)} snapshots)")
    # For each hit, we would need spot continuation — requires Kraken or RTDS logs
    # For v1 we just aggregate by size/side and note that spot continuation measurement needs spot feed
    with open(CSV_OUT,"w",newline="") as f:
        w=csv.DictWriter(f, fieldnames=["ts","side","usd","buy","sell","n"])
        w.writeheader()
        for h in all_hits:
            w.writerow(h)

    agg=defaultdict(list)
    for h in all_hits:
        agg[h["side"]].append(h)

    with open(REPORT,"w") as out:
        out.write("# S4 Event Study — Real Harvest Data\n\n")
        out.write(f"Source files: {len(files)}, cascade events: {len(all_hits)} (raw window hits before cross-snapshot dedupe: {raw_hits})\n")
        out.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n")
        out.write("## Counts by side\n")
        for side in ["BUY","SELL"]:
            lst=agg.get(side,[])
            out.write(f"- {side}: {len(lst)}\n")
        out.write("\n## Counts by size\n")
        for thresh in [1.5e6,2.5e6,4e6,8e6]:
            sub=[h for h in all_hits if h["usd"]>=thresh]
            out.write(f"- >=${thresh/1e6:.1f}M: {len(sub)}\n")
        out.write("\n## Events per day\n")
        byday=defaultdict(int)
        for h in all_hits: byday[datetime.fromtimestamp(h["ts"], timezone.utc).strftime("%Y-%m-%d")]+=1
        for d in sorted(byday): out.write(f"- {d}: {byday[d]}\n")
        out.write("\n## Events by UTC hour (S6 quiet hours are 00-12 and 16-18; S4 is armed all day)\n")
        byhour=defaultdict(int)
        for h in all_hits: byhour[datetime.fromtimestamp(h["ts"], timezone.utc).hour]+=1
        out.write(" ".join(f"{hh:02d}h:{byhour.get(hh,0)}" for hh in range(24)) + "\n")
        out.write("\n## Largest 10 events\n")
        for h in sorted(all_hits, key=lambda x: -x["usd"])[:10]:
            out.write(f"- {datetime.fromtimestamp(h['ts'], timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')} {h['side']} ${h['usd']/1e6:.2f}M ({h['n']} prints)\n")
        out.write("\n## What this report does NOT show\n")
        out.write("- Continuation after the cascade (spot at +60 s / +180 s): needs a spot-history join, not done.\n")
        out.write("- Whether the 15m round resolved in the cascade direction: deliberately NOT joined; the round resolves vs its OPEN, not vs the price at cascade time, so it would overstate S4 edge.\n")
        out.write("- Book repricing speed at cascade time: needs the BME order-book capture join, not done.\n")
        out.write("\n## Next steps for P_model\n")
        out.write("- Join each cascade ts with Kraken 1s spot: measure spot 60s/180s later\n")
        out.write("- Join with Polymarket Gamma 15m resolutions: does cascade side win?\n")
        out.write("- Join with CLOB prices-history: how fast does book reprice after cascade?\n")
        out.write("- Compute P_model = P(continuation | size, spot_confirm, vol_regime)\n")
        out.write("- Use P_model in s46_harvester.py S4 entry: ask <= P_model - fee - 0.02\n")
        out.write("\n### Current conservative prior (from strategy doc)\n")
        out.write("- 1.5M->0.60, 2.5M->0.65, 4M->0.70, 8M->0.75 + 0.03 if spot >=6bps, +0.02 if >=10bps, cap 0.80\n")

    print(f"Wrote {REPORT} and {CSV_OUT}")

def s6_sim():
    print("S6 simulation requires CLOB prices-history — not implemented in this env, see fill_asymmetry.py")
    print("Run fill_asymmetry.py after 300 legs from live DRY logs")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--event-study", action="store_true")
    ap.add_argument("--s6-sim", action="store_true")
    ap.add_argument("--rounds", type=int, default=500)
    args=ap.parse_args()
    if args.event_study:
        event_study()
    elif args.s6_sim:
        s6_sim()
    else:
        event_study()
