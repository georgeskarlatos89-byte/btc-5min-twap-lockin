"""Read-only: replay S3's own volatility number and the S8 daemon's number over the same minutes."""
import glob, json, math, os, time, urllib.request
from datetime import datetime, timezone
HOME = os.path.expanduser("~")
T0 = datetime(2026, 9, 27, 13, 20, tzinfo=timezone.utc).timestamp()
T1 = datetime(2026, 9, 27, 14, 40, tzinfo=timezone.utc).timestamp()

# ---- Chainlink spot, 1 Hz, as recorded by the S2 collector (the same feed S3 listens to)
spot = {}
for p in sorted(glob.glob(f"{HOME}/s2_openprint/data/sessions/*/ticks.jsonl")):
    if os.path.getmtime(p) < T0 - 7200: continue
    for ln in open(p):
        try: r = json.loads(ln)
        except Exception: continue
        if r["topic"] == "crypto_prices_chainlink" and T0 - 900 <= r["received"] <= T1:
            spot[round(r["received"], 1)] = r["value"]
ts = sorted(spot); vals = [spot[t] for t in ts]
def s3_sigma(now):
    """exactly s3_maker.sigma5m_bps: last 600 samples, std of 1-step differences * sqrt(300)"""
    v = [x for t, x in zip(ts, vals) if t <= now][-600:]
    if len(v) < 60: return None
    d = [b - a for a, b in zip(v, v[1:])]; m = sum(d) / len(d)
    return math.sqrt(sum((x - m) ** 2 for x in d) / len(d)) * math.sqrt(300) / v[-1] * 1e4

# ---- Kraken 1-minute closes, as the S8 daemon fetches them
rows = json.loads(urllib.request.urlopen(urllib.request.Request(
    "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1", headers={"User-Agent": "read-only-audit"}), timeout=15).read().decode())["result"]["XXBTZUSD"]
k = [(float(r[0]), float(r[4])) for r in rows]
def s8_sigma(now):
    """exactly s8_router.VolatilityEstimator: trailing 1 h, 5-minute returns from 1-minute closes, sample std"""
    pts = [(t, p) for t, p in k if now - 3600 <= t <= now]
    rets = []
    for i, (tc, pc) in enumerate(pts):
        tg = tc - 300
        if tg < pts[0][0] or i == 0: continue
        bp = min(pts[:i], key=lambda x: abs(x[0] - tg))
        if abs(bp[0] - tg) <= 30 and bp[1] > 0: rets.append((pc - bp[1]) / bp[1] * 1e4)
    if len(rets) < 5: return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

print(f"Chainlink ticks loaded: {len(ts)}  Kraken 1m bars: {len(k)} ({datetime.fromtimestamp(k[0][0], timezone.utc):%H:%M}..{datetime.fromtimestamp(k[-1][0], timezone.utc):%H:%M})")
print("threshold for the volatility override in both programs: 10.5 bps\n")
print("UTC    S3's own number   S8 daemon's number   S3 decides   S8 daemon decides")
n = dis = 0; mx3 = 0
t = T0
while t <= T1:
    a, b = s3_sigma(t), s8_sigma(t)
    if a is not None and b is not None:
        n += 1; mx3 = max(mx3, a)
        d3, d8 = ("BLOCK" if a > 10.5 else "quote"), ("BLOCK" if b > 10.5 else "quote")
        dis += d3 != d8
        if int(t) % 300 == 0 or d3 != d8 and int(t) % 120 == 0:
            print(f"{datetime.fromtimestamp(t, timezone.utc):%H:%M}  {a:10.2f} bps   {b:12.2f} bps      {d3:6}       {d8}")
    t += 60
print(f"\nminutes compared {n}, minutes where the two disagree {dis}, highest S3 number in the period {mx3:.2f} bps")

print("\n=== which strategies consult the S8 router at all ===")
for name, path in (("S1 trader", "s1_harness/trader.py"), ("S2 recorder", "s2_openprint/capture.py"), ("S3 maker", "s3_feefarm/s3_maker.py"),
                   ("S46 cascade + coin-flip", "s46_coinflip_cascade/s46_harvester.py"), ("S5 recorder", "s5_wickfade/collect.py"), ("S10 box", "s10_volbox/s10_box.py")):
    p = os.path.join(HOME, path)
    try: src = open(p, errors="replace").read()
    except Exception: print(f"  {name:26} file not found"); continue
    imp = "import s8_router" in src
    calls = [ln.strip()[:120] for ln in src.splitlines() if "s8_router." in ln and not ln.strip().startswith("#")]
    reads_state = "s8_state.json" in src
    print(f"  {name:26} imports router: {'YES' if imp else 'no ':3}  reads the daemon's state file: {'YES' if reads_state else 'no'}  calls: {len(calls)}")
    for c in calls[:3]: print(f"        {c}")
