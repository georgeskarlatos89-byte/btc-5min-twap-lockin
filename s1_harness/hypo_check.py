import csv
rows = [r for r in csv.DictReader(open("rounds.csv")) if r["fa_twap"] in ("yes", "NO")]
H = ["fa_twap", "t60_twap", "ev_twap", "fa_spot", "t60_spot", "ev_spot"]
o = lambda r: float(r["open_ref_twap"])
gap = lambda r: (float(r["full_round_avg"]) - o(r)) / o(r) * 1e4
egap = lambda r: (float(r["end_value"]) - o(r)) / o(r) * 1e4
n5 = sum(r["market"] == "5m" for r in rows); n15 = len(rows) - n5
print("full-coverage rounds: %d  (5m %d, 15m %d)" % (len(rows), n5, n15))
print("\n== match rate vs OFFICIAL settlement, per hypothesis (all rounds | rounds with |avg gap|>=4bps) ==")
big = [r for r in rows if abs(gap(r)) >= 4]
for h in H:
    y = sum(r[h] == "yes" for r in rows if r[h]); n = sum(1 for r in rows if r[h])
    yb = sum(r[h] == "yes" for r in big if r[h]); nb = sum(1 for r in big if r[h])
    print("  %-9s %3d/%-3d = %5.1f%%   | >=4bps %2d/%-2d = %5.1f%%" % (h, y, n, 100 * y / max(1, n), yb, nb, 100 * yb / max(1, nb)))
dis = [r for r in rows if r["fa_twap"] != r["ev_twap"]]
print("\n== THE DECIDING ROUNDS: full-round-avg and end-value DISAGREE (%d rounds) ==" % len(dis))
print("  mkt round        avg-gap  end-gap  settled  full-avg  end-value")
fa = ev = 0
for r in dis:
    fa += r["fa_twap"] == "yes"; ev += r["ev_twap"] == "yes"
    print("  %-3s %s %+7.1f %+8.1f   %-4s     %-3s       %s" % (r["market"], r["round_start"], gap(r), egap(r), r["settled"], r["fa_twap"], r["ev_twap"]))
print("  -> settlement matched FULL-ROUND-AVG in %d/%d, END-VALUE in %d/%d" % (fa, len(dis), ev, len(dis)))
agr = [r for r in rows if r["fa_twap"] == r["ev_twap"]]
print("\n== rounds where both hypotheses say the same side: %d/%d matched (right or wrong together) ==" % (sum(r["fa_twap"] == "yes" for r in agr), len(agr)))
print("\n== end-value hypothesis by |end-gap| bucket ==")
for lo, hi in ((0, 1), (1, 4), (4, 1e9)):
    b = [r for r in rows if lo <= abs(egap(r)) < hi]
    print("  |end-gap| %s-%s bps: ev_twap %d/%d" % (lo, "inf" if hi > 1e8 else hi, sum(r["ev_twap"] == "yes" for r in b), len(b)))
print("\n== full-round-avg hypothesis by |avg-gap| bucket (the S1 thesis) ==")
for lo, hi in ((0, 1), (1, 4), (4, 1e9)):
    b = [r for r in rows if lo <= abs(gap(r)) < hi]
    print("  |avg-gap| %s-%s bps: fa_twap %d/%d" % (lo, "inf" if hi > 1e8 else hi, sum(r["fa_twap"] == "yes" for r in b), len(b)))
