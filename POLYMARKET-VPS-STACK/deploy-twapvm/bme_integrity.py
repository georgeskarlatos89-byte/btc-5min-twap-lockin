#!/usr/bin/env python3
"""
bme_integrity.py - daily data-integrity check of the BME recording, cross-referenced against
Polymarket's own endpoints so a silent failure cannot hide. READ-ONLY. One Telegram line.

Checks on one UTC day of files (default: yesterday; --today for the running, partial day):
  [1] every events row parses; per-type field validity (prices in (0,1), sizes >= 0, BUY/SELL,
      hex hashes, BTC ticks plausible); counts per event type; time order within each gzip member
  [2] round coverage: which 5m/15m rounds have book-state rows, and gaps > 60 s inside a round
  [3] RTDS cross-check: the twap_tick values we recorded at each bell / round end vs the S1
      observer's rounds.csv (a SEPARATE process on the same feed) - must agree within ~1 bps
  [4] token cross-check: the tokens the recorder subscribed to (bme.log) vs gamma's clobTokenIds
      for that round slug (closed=true fallback)
  [5] tape cross-check: our trade prints per round vs the official data-api tape for that market
      (sampled rounds; approximate - reported as a ratio, flagged only if we clearly missed prints)
  [6] outcomes: every round of the day resolvable via gamma (what bme_score needs)
Writes bme_integrity_<day>.json + .txt next to the data. Exit 0 = OK, 1 = issues.
"""
import argparse, csv, glob, gzip, json, os, re, sys, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
BME_DIR = os.path.join(HERE, "..", "polymarket-stack", "4-BME-BOOK-MOVEMENT-ENGINE")
OUT = os.path.normpath(os.path.join(BME_DIR, "s9_data", "bme"))
LOG = os.path.join(OUT, "bme.log")
ROUNDS_CSV = os.path.expanduser("~/s1_harness/rounds.csv")
S1_DIR = os.path.expanduser("~/s1_harness")
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
T = {"5m": 300, "15m": 900}
_last = [0.0]


def get(url, retries=4):
    for i in range(retries):
        w = 0.15 - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == retries - 1: return None
            time.sleep(1.5 * (i + 1))


def read_gz_rows(path):
    """rows of a (possibly multi-member, possibly truncated-tail) gzip csv; yields (member_idx, row)."""
    truncated = False
    try:
        with gzip.open(path, "rt", newline="") as f:
            for r in csv.reader(f):
                if r: yield r
    except (EOFError, OSError):
        truncated = True
    read_gz_rows.truncated = truncated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYYMMDD (default: yesterday UTC)")
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--sample-rounds", type=int, default=12)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    day = a.day or (datetime.now(timezone.utc) - (timedelta(days=0) if a.today else timedelta(days=1))).strftime("%Y%m%d")
    ev_path = os.path.join(OUT, f"events_{day}.csv.gz")
    st_path = os.path.join(OUT, f"bookstate_1s_{day}.csv")
    issues, R = [], {"day": day, "generated_utc": datetime.now(timezone.utc).isoformat()}
    if not os.path.exists(ev_path):
        print(f"no events file for {day}"); sys.exit(1)

    # ---------- [1] events: parse + validity + counts + order
    counts = Counter(); bad = Counter(); last_ts = None; back = 0; twap = []; spot = []; prints = defaultdict(int)
    hexre = re.compile(r"^[0-9a-fA-F]{0,16}$")
    n = 0
    for r in read_gz_rows(ev_path):
        if r[0] == "ts_ms": continue
        n += 1
        if len(r) != 11: bad["fields"] += 1; continue
        try: ts = int(r[0])
        except Exception: bad["ts"] += 1; continue
        if last_ts is not None and ts < last_ts - 5000: back += 1   # >5 s backwards = member boundary or clock issue
        last_ts = ts
        et = r[1]; counts[et] += 1
        try:
            if et in ("price_change", "trade", "bba"):
                p = float(r[5]) if r[5] else None
                if p is not None and not (0 < p < 1): bad["price_range"] += 1
                if et == "price_change" and r[4] not in ("BUY", "SELL"): bad["side"] += 1
                if et == "price_change" and not hexre.match(r[9]): bad["hash"] += 1
                if r[6] and float(r[6]) < 0: bad["size"] += 1
                if et == "trade": prints[(r[3], ts // 1000)] += 1
            elif et in ("spot_tick", "twap_tick"):
                v = float(r[5])
                if not (10_000 < v < 500_000): bad["tick_value"] += 1
                (twap if et == "twap_tick" else spot).append((ts / 1000.0, v))
                if et == "spot_tick" and not re.match(r"^pretick_pulls_200ms=\d+$", r[10]): bad["pretick_field"] += 1
        except Exception:
            bad["parse"] += 1
    R["events"] = {"rows": n, "by_type": dict(counts), "invalid": dict(bad), "backward_time_jumps": back,
                   "truncated_open_member": getattr(read_gz_rows, "truncated", False)}
    if sum(bad.values()) > max(50, n * 0.001): issues.append(f"events: {sum(bad.values())} invalid rows {dict(bad)}")
    if n == 0: issues.append("events: file has no rows")
    for et in ("price_change", "trade", "spot_tick", "twap_tick"):
        if counts.get(et, 0) == 0: issues.append(f"events: no {et} rows at all")

    # ---------- [2] round coverage + gaps from bookstate
    cover = defaultdict(list)
    if os.path.exists(st_path):
        for r in csv.reader(open(st_path, newline="")):
            if not r or r[0] == "ts_ms": continue
            try:
                ts, series, idx = int(r[0]), r[1], int(r[2])
                start = ts // 1000 // T[series] * T[series]
                cover[(series, start)].append(ts)
            except Exception:
                pass
    gaps = 0; rounds = sorted(cover)
    for k, tss in cover.items():
        tss.sort()
        gaps += sum(1 for a_, b_ in zip(tss, tss[1:]) if b_ - a_ > 60_000)
    R["coverage"] = {"rounds_with_bookstate": len(rounds), "5m": sum(1 for k in rounds if k[0] == "5m"),
                     "15m": sum(1 for k in rounds if k[0] == "15m"), "gaps_over_60s": gaps}
    if gaps > max(5, len(rounds) * 0.05): issues.append(f"coverage: {gaps} book-state gaps > 60 s")

    # ---------- [3] RTDS cross-check vs the observer's rounds.csv
    obs = {}
    if os.path.exists(ROUNDS_CSV):
        for r in csv.DictReader(open(ROUNDS_CSV, newline="")):
            obs[(r["market"], int(r["round_start"]))] = (float(r["open_ref_twap"]), float(r["end_value"]))
    twap.sort()
    m_ok = m_bad = m_n = 0; worst = 0.0
    for (series, start), (o_obs, e_obs) in obs.items():
        if datetime.fromtimestamp(start, timezone.utc).strftime("%Y%m%d") != day: continue
        at_open = [v for t_, v in twap if start <= t_ <= start + 3]
        at_end = [v for t_, v in twap if start + T[series] - 3 <= t_ <= start + T[series] + 2]
        if not at_open or not at_end: continue
        m_n += 1
        d1 = abs(at_open[0] - o_obs) / o_obs * 1e4; d2 = abs(at_end[-1] - e_obs) / e_obs * 1e4
        worst = max(worst, d1, d2)
        if d1 <= 1.5 and d2 <= 1.5: m_ok += 1
        else: m_bad += 1
    R["rtds_vs_observer"] = {"rounds_compared": m_n, "agree_within_1.5bps": m_ok, "disagree": m_bad, "worst_bps": round(worst, 2)}
    if m_n and m_bad > max(2, m_n * 0.1): issues.append(f"RTDS: our twap ticks disagree with the observer on {m_bad}/{m_n} rounds (worst {worst:.1f} bps)")

    # ---------- [4] tokens subscribed vs gamma
    subs = {}
    if os.path.exists(LOG):
        for ln in open(LOG, errors="replace"):
            m = re.search(r"\] (5m|15m) round (\d+): subscribed (\S{14})…/(\S{14})…", ln)
            if m and datetime.fromtimestamp(int(m.group(2)), timezone.utc).strftime("%Y%m%d") == day:
                subs[(m.group(1), int(m.group(2)))] = (m.group(3), m.group(4))
    t_ok = t_bad = t_na = 0; cids = {}
    for (series, start), (a16, b16) in sorted(subs.items()):
        d = get(f"{GAMMA}/markets?slug=btc-updown-{series}-{start}") or get(f"{GAMMA}/markets?slug=btc-updown-{series}-{start}&closed=true")
        if not d: t_na += 1; continue
        try:
            toks = json.loads(d[0]["clobTokenIds"]); cids[(series, start)] = d[0].get("conditionId")
            if toks[0][:14] == a16 and toks[1][:14] == b16: t_ok += 1
            else: t_bad += 1
        except Exception:
            t_na += 1
    R["tokens_vs_gamma"] = {"rounds": len(subs), "match": t_ok, "MISMATCH": t_bad, "not_checkable": t_na}
    if t_bad: issues.append(f"tokens: {t_bad} rounds subscribed to tokens that do not match gamma")

    # ---------- [5] trade prints vs the official tape (sampled)
    ratios = []
    sample = [k for k in sorted(cids) if k[0] == "5m"][:: max(1, len(cids) // max(1, a.sample_rounds))][: a.sample_rounds]
    for k in sample:
        cid = cids.get(k)
        if not cid: continue
        rows = get(f"{DATA}/trades?" + urllib.parse.urlencode({"market": cid, "takerOnly": "false", "limit": 1000, "offset": 0}))
        if not isinstance(rows, list): continue
        start = k[1]; api_n = sum(1 for r in rows if start <= int(r.get("timestamp", 0)) < start + T["5m"])
        ours = sum(v for (tok, sec), v in prints.items() if start <= sec < start + T["5m"])
        if api_n >= 20: ratios.append(ours / api_n)
    R["prints_vs_tape"] = {"rounds_sampled": len(ratios), "ratio_ours_over_api_median": round(sorted(ratios)[len(ratios) // 2], 2) if ratios else None,
                           "note": "channel prints vs data-api fills are not 1:1 (batch fills); flag only if clearly low"}
    if ratios and sorted(ratios)[len(ratios) // 2] < 0.5: issues.append("tape: our trade prints are < 50% of the official tape - prints being missed")

    # ---------- [6] outcomes resolvable
    res_ok = res_no = 0
    for (series, start) in rounds:
        if start + T[series] + 600 > time.time(): continue
        d = get(f"{GAMMA}/events?slug=btc-updown-{series}-{start}") or get(f"{GAMMA}/events?slug=btc-updown-{series}-{start}&closed=true")
        try:
            pr = [float(x) for x in json.loads(d[0]["markets"][0]["outcomePrices"])]
            res_ok += 1 if (pr[0] >= 0.99 or pr[0] <= 0.01) else 0; res_no += 0 if (pr[0] >= 0.99 or pr[0] <= 0.01) else 1
        except Exception:
            res_no += 1
    R["outcomes"] = {"resolved": res_ok, "unresolved_or_missing": res_no}
    if res_ok + res_no and res_no > max(3, (res_ok + res_no) * 0.05): issues.append(f"outcomes: {res_no} rounds not resolvable via gamma")

    R["issues"] = issues
    base = os.path.join(OUT, f"bme_integrity_{day}")
    json.dump(R, open(base + ".json", "w"), indent=1)
    txt = (f"BME data check {day}{' (partial, running day)' if a.today else ''}: events {n:,} rows ({counts.get('price_change',0):,} book moves, "
           f"{counts.get('trade',0):,} prints, {counts.get('twap_tick',0):,} twap ticks), invalid {sum(bad.values())} | rounds {len(rounds)} "
           f"({R['coverage']['5m']}×5m, {R['coverage']['15m']}×15m), gaps>60s {gaps} | RTDS vs observer {m_ok}/{m_n} agree (worst {worst:.1f} bps) | "
           f"tokens vs gamma {t_ok}/{len(subs)} | prints/tape {R['prints_vs_tape']['ratio_ours_over_api_median']} | outcomes {res_ok}/{res_ok+res_no}"
           + (f"\n⚠ ISSUES: " + "; ".join(issues) if issues else "\n✅ no issues"))
    open(base + ".txt", "w").write(txt + "\n"); print(txt)
    if not a.no_telegram:
        sys.path.insert(0, S1_DIR)
        try:
            import s1_monitor as m
            m.tg_send(f"🔷 {m.STRAT_DESC['BME']}\n{'🚨' if issues else '✅'} {txt}")
        except Exception as e:
            print(f"telegram send failed: {e!r}")
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
