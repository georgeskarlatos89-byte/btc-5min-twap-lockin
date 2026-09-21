#!/usr/bin/env python3
"""Integrity check of the observer's rounds.csv (the arbiter). Read-only.
Checks: schema, value validity, alignment/ordering/duplicates, every yes/NO flag recomputed from
the stored numbers, plausibility, `settled` vs the trader's independent SCORE lines, `settled`
vs Polymarket's gamma API (official outcome, ALL rows), open-ref noise vs the trader's own bell
snapshot, and completeness (which rounds are MISSING)."""
import csv, json, re, time, urllib.request, math
from datetime import datetime, timezone

P = "rounds.csv"
raw = open(P, newline="").read().splitlines()
hdr = raw[0].split(",")
rows = list(csv.DictReader(open(P, newline="")))
T = {"5m": 300, "15m": 900}
issues = []


def utc(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


def side(x, o):
    return "Up" if x >= o else "Down"


print("=" * 78)
print("ROUNDS.CSV INTEGRITY CHECK  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"))
print("=" * 78)
print("header: %d columns -> %s" % (len(hdr), hdr))
print("rows: %d  (5m %d, 15m %d)" % (len(rows), sum(r["market"] == "5m" for r in rows), sum(r["market"] == "15m" for r in rows)))

# [1] schema
bad = [i for i, ln in enumerate(raw[1:], 2) if len(next(csv.reader([ln]))) != len(hdr)]
print("\n[1] every row has %d fields: %s" % (len(hdr), "OK" if not bad else "FAIL lines %s" % bad))

# [2] validity
num = ["open_ref_twap", "full_round_avg", "tail60_avg", "end_value"]
for r in rows:
    for c in num:
        try:
            v = float(r[c])
            assert math.isfinite(v) and v > 0
        except Exception:
            issues.append("row %s %s: %s=%r not a valid price" % (r["market"], r["round_start"], c, r[c]))
    if r["open_ref_spot"]:
        try:
            float(r["open_ref_spot"])
        except Exception:
            issues.append("row %s: open_ref_spot bad" % r["round_start"])
    if r["settled"] not in ("Up", "Down"):
        issues.append("row %s: settled=%r" % (r["round_start"], r["settled"]))
    for c in ["fa_twap", "t60_twap", "ev_twap"]:
        if not (r[c] in ("yes", "NO") or r[c].startswith("partial")):
            issues.append("row %s: %s=%r" % (r["round_start"], c, r[c]))
    if not re.fullmatch(r"\d{1,3}%", r["coverage"]):
        issues.append("row %s: coverage=%r" % (r["round_start"], r["coverage"]))
n2 = len(issues)
print("[2] prices numeric/finite, settled in {Up,Down}, flags valid, coverage %%: %s" % ("OK" if not n2 else "FAIL"))

# [3] alignment / order / duplicates
seen = set()
last = {}
for r in rows:
    k = (r["market"], r["round_start"])
    s = int(r["round_start"])
    t = T[r["market"]]
    if s % t:
        issues.append("%s: round_start not aligned to %ds" % (k, t))
    if k in seen:
        issues.append("%s: DUPLICATE row" % (k,))
    seen.add(k)
    if r["market"] in last and s <= last[r["market"]]:
        issues.append("%s: out of order" % (k,))
    last[r["market"]] = s
n3 = len(issues) - n2
print("[3] round_start aligned to 300/900 s, no duplicates, chronological per market: %s" % ("OK" if not n3 else "FAIL"))

# [4] recompute every flag from the numbers
logic_bad = 0
nflags = 0
for r in rows:
    o = float(r["open_ref_twap"])
    st = r["settled"]
    exp = {"fa_twap": side(float(r["full_round_avg"]), o),
           "t60_twap": side(float(r["tail60_avg"]), o),
           "ev_twap": side(float(r["end_value"]), o)}
    if r["open_ref_spot"]:
        os_ = float(r["open_ref_spot"])
        exp.update({"fa_spot": side(float(r["full_round_avg"]), os_),
                    "t60_spot": side(float(r["tail60_avg"]), os_),
                    "ev_spot": side(float(r["end_value"]), os_)})
    for c, want in exp.items():
        if r[c].startswith("partial") or r[c] == "":
            continue
        nflags += 1
        if (r[c] == "yes") != (want == st):
            logic_bad += 1
            issues.append("%s %s: %s=%s but numbers say %s vs settled %s" % (r["market"], r["round_start"], c, r[c], want, st))
print("[4] every yes/NO flag recomputed from the stored numbers: %s" % ("OK - %d flags consistent" % nflags if not logic_bad else "FAIL %d" % logic_bad))

# [5] plausibility
n5a = len(issues)
for r in rows:
    o = float(r["open_ref_twap"])
    for c in ["full_round_avg", "tail60_avg", "end_value"]:
        if abs(float(r[c]) - o) / o > 0.03:
            issues.append("%s: %s %+.2f%% from open (implausible)" % (r["round_start"], c, (float(r[c]) / o - 1) * 100))
    if r["open_ref_spot"] and abs(float(r["open_ref_spot"]) - o) / o * 1e4 > 100:
        issues.append("%s: spot-open vs twap-open > 100 bps" % r["round_start"])
part = [r for r in rows if r["fa_twap"].startswith("partial")]
print("[5] every price within 3%% of open, spot/twap opens within 100 bps: %s;  partial-coverage rows: %d" % ("OK" if len(issues) == n5a else "FAIL", len(part)))

# [6] settled vs trader SCORE lines (independent process)
sc = {}
for ln in open("trader.log", errors="replace"):
    m = re.search(r"SCORE (\S+) (\d+): .* settled (Up|Down)", ln)
    if m:
        sc[(m.group(1), m.group(2))] = m.group(3)
cmp_ = [(r, sc[(r["market"], r["round_start"])]) for r in rows if (r["market"], r["round_start"]) in sc]
mism = [(r["market"], r["round_start"]) for r, s in cmp_ if s != r["settled"]]
print("[6] settled vs the trader's own SCORE lines (separate process): %d rounds compared, %d mismatches %s" % (len(cmp_), len(mism), "OK" if not mism else "FAIL %s" % mism))


# [7] settled vs Polymarket gamma API, ALL rows
def gamma(label, start):
    for q in ("", "&closed=true"):
        try:
            u = "https://gamma-api.polymarket.com/markets?slug=btc-updown-%s-%s%s" % (label, start, q)
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15))
            if d:
                pr = [float(x) for x in json.loads(d[0]["outcomePrices"])]
                if pr[0] >= 0.99 or pr[0] <= 0.01:
                    return "Up" if pr[0] > 0.5 else "Down"
                return "unresolved(%.2f)" % pr[0]
        except Exception:
            pass
        time.sleep(0.15)
    return "n/a"


g_ok = g_bad = g_na = 0
gbad = []
for r in rows:
    g = gamma(r["market"], r["round_start"])
    time.sleep(0.12)
    if g in ("Up", "Down"):
        if g == r["settled"]:
            g_ok += 1
        else:
            g_bad += 1
            gbad.append((r["market"], r["round_start"], r["settled"], g))
    else:
        g_na += 1
print("[7] settled vs Polymarket gamma API (official outcome, ALL rows): %d match, %d MISMATCH, %d not checkable  %s" % (g_ok, g_bad, g_na, "OK" if not g_bad else "FAIL %s" % gbad))

# [8] open_ref noise vs trader bell snapshot
tr = {}
for ln in open("trader.log", errors="replace"):
    m = re.search(r"\] (5m|15m) (\d+): open ref ([\d.]+)", ln)
    if m:
        tr[(m.group(1), m.group(2))] = float(m.group(3))
d = sorted(abs(float(r["open_ref_twap"]) - tr[(r["market"], r["round_start"])]) / float(r["open_ref_twap"]) * 1e4
           for r in rows if (r["market"], r["round_start"]) in tr)
if d:
    print("[8] open_ref_twap vs the trader's own bell snapshot: %d rounds, median diff %.2f bps, max %.2f bps (two processes; first feed update within 3 s of the bell)" % (len(d), d[len(d) // 2], d[-1]))

# [9] completeness
print("\n[9] COMPLETENESS (accuracy is not completeness):")
for lab, t in T.items():
    ss = sorted(int(r["round_start"]) for r in rows if r["market"] == lab)
    if not ss:
        continue
    present = set(ss)
    expected = list(range(ss[0], ss[-1] + 1, t))
    missing = [s for s in expected if s not in present]
    print("   %s: window %s -> %s UTC = %d rounds, present %d, MISSING %d: %s" % (lab, utc(ss[0]), utc(ss[-1]), len(expected), len(ss), len(missing), ", ".join(utc(s)[11:] for s in missing)))

print("\nISSUES FOUND: %s" % ("NONE" if not issues else str(len(issues))))
for i in issues[:40]:
    print("   - " + i)
