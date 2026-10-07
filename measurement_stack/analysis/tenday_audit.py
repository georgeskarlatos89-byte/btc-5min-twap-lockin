"""Read-only 10-day audit of twapvm: per-day activity for every strategy, PDFs, alerts, key."""
import collections, csv, glob, gzip, hashlib, json, os, re, subprocess, time, urllib.error, urllib.request
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
SINCE = datetime(2026, 9, 27, 14, 31, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc)
DAYS = [(SINCE + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((NOW - SINCE).days + 2)]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e: return f"ERR {e!r}"
def dstr(ts): return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
def fl(x):
    try: return float(x)
    except Exception: return None
def jl(p):
    try: return json.load(open(p))
    except Exception: return {}
out = {}
def section(name): print(f"\n{'=' * 8} {name} {'=' * 8}")

print("UTC now:", NOW.strftime("%Y-%m-%d %H:%M:%S"), "| audit window from", SINCE.strftime("%Y-%m-%d %H:%M"), "| days:", DAYS[0], "..", DAYS[-1])
section("services now")
UNITS = ["s1-harness", "s1-trader", "s1-monitor", "s9-watcher", "bme-capture", "s2-collect", "s3-maker", "s46-harvester", "s5-collector", "s8-router", "s10-box",
         "ms-us-recorder", "ms-nws-watch", "ms-regime-watch"]
for u in UNITS:
    p = dict(l.split("=", 1) for l in sh(f"systemctl show {u}.service -p ActiveState -p SubState -p NRestarts -p ActiveEnterTimestamp -p UnitFileState").splitlines() if "=" in l)
    print(f"  {u:16} {p.get('ActiveState', '?'):9} restarts={p.get('NRestarts', '?'):4} since {p.get('ActiveEnterTimestamp', '')[4:23]}  enabled={p.get('UnitFileState', '')}")
print("  other units mentioning our names:", sh("systemctl list-units --type=service --all --no-legend | awk '{print $1}' | grep -vE '^(" + "|".join(UNITS) + ")\\.service$' | grep -iE 's[0-9]+-|ms-|bme|paper|ui-|trader|weather' | tr '\\n' ' '"))
print("  timers:", sh("systemctl list-timers --all --no-legend | awk '{print $NF}' | grep -E 'ms-|bme|s5' | tr '\\n' ' '"))
print("  failed:", sh("systemctl --failed --no-legend | awk '{print $2}' | tr '\\n' ' '") or "none")
print("  disk:", sh("df -h / | awk 'END{print $4\" free of \"$2\" (\"$5\" used)\"}'"), "| uptime:", sh("uptime -p"), "| load:", sh("cut -d' ' -f1-3 /proc/loadavg"))

# ---------------- helper: day attribution for logs with HH:MM:SS only (walk backwards from the newest line)
def day_lines(path, max_bytes=60_000_000):
    """returns list of (day, line) for the whole tail of the log, walking back day rollovers from today"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - max_bytes)); lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []
    out, prev, day = [], None, datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date()
    for ln in reversed(lines):
        m = re.match(r"\[?(?:(\d{4}-\d\d-\d\d)[ T])?(\d\d):(\d\d):(\d\d)", ln)
        if not m: continue
        if m.group(1): d = m.group(1)
        else:
            t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
            if prev is not None and t > prev + 300: day = day - timedelta(days=1)
            prev = t; d = day.strftime("%Y-%m-%d")
        if d < DAYS[0]: break
        out.append((d, ln))
    return list(reversed(out))

def per_day(lines, patterns):
    c = {d: collections.Counter() for d in DAYS}
    for d, ln in lines:
        if d not in c: continue
        for k, rx in patterns.items():
            if re.search(rx, ln): c[d][k] += 1
    return c

def show(c, keys):
    print("  day        " + "  ".join(f"{k:>12}" for k in keys))
    for d in DAYS:
        if any(c[d].get(k) for k in keys): print(f"  {d} " + "  ".join(f"{c[d].get(k, 0):>12}" for k in keys))

section("S1 observer (rounds.csv) + trader (dry signals)")
R = list(csv.DictReader(open(f"{HOME}/s1_harness/rounds.csv")))
byd = collections.defaultdict(lambda: collections.Counter())
for r in R:
    if not r["round_start"].isdigit(): continue
    d = dstr(r["round_start"])
    if d < DAYS[0]: continue
    byd[d]["rounds"] += 1; byd[d]["full"] += str(r.get("coverage", "")).rstrip("%").replace(".", "", 1).isdigit() and float(r["coverage"].rstrip("%")) >= 95
    byd[d]["ev_yes"] += r.get("ev_twap") == "yes"; byd[d]["fa_yes"] += r.get("fa_twap") == "yes"; byd[d]["signal"] += bool(r.get("signal_side"))
print("  day         rounds  full-coverage  end-value-rule-ok  full-avg-rule-ok  harness-signals")
for d in DAYS:
    if byd[d]["rounds"]: print(f"  {d}  {byd[d]['rounds']:6}  {byd[d]['full']:13}  {byd[d]['ev_yes']:17}  {byd[d]['fa_yes']:16}  {byd[d]['signal']:15}")
tl = day_lines(f"{HOME}/s1_harness/trader.log")
c = per_day(tl, {"dry_signals": r"DRY SIGNAL", "wins": r"-> WIN", "losses": r"-> LOSS", "rtds_reconnect": r"RTDS (problem|silent)", "live_order": r"LIVE ORDER"})
show(c, ["dry_signals", "wins", "losses", "rtds_reconnect", "live_order"])
print("  trader_stats:", jl(f"{HOME}/s1_harness/trader_stats.json"), "| S1_LIVE_BLOCKED present:", os.path.exists(f"{HOME}/s1_harness/S1_LIVE_BLOCKED"))

section("S3 fee-farm maker")
c = per_day(day_lines(f"{HOME}/s3_feefarm/s3_maker.log"), {"quotes": r"\] QUOTE", "fills": r"\] FILL ", "pairs": r"PAIR COMPLETED", "pulls": r"PULL BOTH", "skips": r"\] SKIP", "settled": r"ROUND-RESULT",
                                                        "inv_exit": r"INV-EXIT", "errors": r"error|Traceback", "s8_blocked": r"S8 gate", "starts": r"maker starting"})
show(c, ["quotes", "fills", "pairs", "pulls", "skips", "settled", "inv_exit", "s8_blocked", "errors", "starts"])
print("  stats now:", {k: v for k, v in jl(f"{HOME}/s3_feefarm/s3_stats.json").items() if k != "pulls"})
fills = list(csv.DictReader(open(f"{HOME}/s3_feefarm/s3_fills.csv"))) if os.path.exists(f"{HOME}/s3_feefarm/s3_fills.csv") else []
print("  fills ledger rows since the window:", [(dstr(r["ts"]), r["series"], r["side"], r["event"], r["price"], r["shares"]) for r in fills if dstr(r["ts"]) >= DAYS[0]][:20])

section("S46 (S6 coin-flip maker + S4 cascade taker)")
c = per_day(day_lines(f"{HOME}/s46_coinflip_cascade/s46_harvester.log"), {"s6_quotes": r"S6 QUOTE", "s6_fills": r"S6 FILL|\] FILL S6", "legs_held": r"SINGLE LEG HOLD", "pulls": r"PULL BOTH", "settled": r"ROUND-RESULT",
                                                                           "cascades": r"CASCADE|cascade", "s4_triggers": r"S4 TRIGGER|S4 trigger|S4 ENTRY", "s4_fills": r"S4 FILL", "errors": r"error|Traceback", "starts": r"starting"})
show(c, ["s6_quotes", "s6_fills", "legs_held", "pulls", "settled", "cascades", "s4_triggers", "s4_fills", "errors", "starts"])
s46 = jl(f"{HOME}/s46_coinflip_cascade/s46_stats.json"); print("  stats now: s6", s46.get("s6"), "| s4", {k: v for k, v in (s46.get("s4") or {}).items()}, "| cascades", s46.get("cascades"))

section("S10 vol-event box")
c = per_day(day_lines(f"{HOME}/s10_volbox/s10_box.log"), {"armed": r"BOX ARMED", "disarmed": r"DISARM", "quotes": r"QUOTE", "fills": r"\] FILL|MAKER FILL", "pair_arb": r"PAIR-ARB|pair-arb", "chase": r"CHASE", "unwind": r"UNWIND", "settled": r"ROUND-RESULT", "errors": r"error|Traceback"})
show(c, ["armed", "disarmed", "quotes", "fills", "pair_arb", "chase", "unwind", "settled", "errors"])
print("  stats now:", {k: v for k, v in jl(f"{HOME}/s10_volbox/s10_stats.json").items() if k in ("n", "maker_quotes", "maker_fills", "taker_pairs", "pairs_completed", "pairs_won", "pnl", "arms", "kill_test", "sigma_baseline_bps")})

section("S2 open-print recorder")
dec = collections.defaultdict(collections.Counter); sess = collections.Counter(); ticks = collections.Counter()
for s in sorted(glob.glob(f"{HOME}/s2_openprint/data/sessions/*")):
    nm = os.path.basename(s); d = f"{nm[:4]}-{nm[4:6]}-{nm[6:8]}"
    if d < DAYS[0]: continue
    sess[d] += 1
    for ln in open(os.path.join(s, "decisions.jsonl")) if os.path.exists(os.path.join(s, "decisions.jsonl")) else []:
        try: j = json.loads(ln); dec[d][j.get("reason", j.get("action"))] += 1
        except Exception: pass
    p = os.path.join(s, "ticks.jsonl")
    if os.path.exists(p): ticks[d] += sum(1 for _ in open(p))
for d in DAYS:
    if sess[d]: print(f"  {d}  sessions {sess[d]:2}  ticks {ticks[d]:7}  decisions {dict(dec[d])}")

section("S5 wick-fade recorder (data files)")
for p in sorted(glob.glob(f"{HOME}/s5_wickfade/data/*.jsonl*")):
    d = os.path.basename(p)[:10]
    if d >= DAYS[0]: print(f"  {d}  {os.path.getsize(p) / 2**30:.2f} GB  {os.path.basename(p)}")
print("  archive/other:", sh(f"ls {HOME}/s5_wickfade/data | grep -vE '^2026' | tr '\\n' ' '"), "| total", sh(f"du -sh {HOME}/s5_wickfade/data | cut -f1"))

section("S8 session router")
for d, ln in day_lines(f"{HOME}/s8_router/s8_router.log"):
    if "TRANSITION" in ln: print("  ", d, ln[ln.find("]") + 1:][:140])
s8 = jl(f"{HOME}/s8_router/s8_state.json"); print("  state now:", {k: s8.get(k) for k in ("utc_time", "effective_regime", "realized_sigma_5m_bps", "allowed_strategies")})

section("S9 whale watcher")
c = per_day(day_lines(f"{HOME}/s1_harness/s9_data/s9_watch.log", 20_000_000), {"follows": r"FOLLOW|follow", "outcomes": r"OUTCOME", "status": r"STATUS", "gaps": r"gap|GAP", "errors": r"error|Traceback"})
show(c, ["follows", "outcomes", "status", "gaps", "errors"])
st_lines = [ln for d, ln in day_lines(f"{HOME}/s1_harness/s9_data/s9_watch.log", 20_000_000) if "STATUS" in ln]
print("  last STATUS:", (st_lines[-1][:300] if st_lines else "none"))

section("BME book recorder")
for p in sorted(glob.glob(f"{HOME}/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/events_*.csv*")):
    d = re.search(r"(\d{8})", p).group(1); d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if d >= DAYS[0]:
        integ = f"{HOME}/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/bme_integrity_{d.replace('-', '')}.txt"
        it = open(integ).read().strip().replace("\n", " ")[:160] if os.path.exists(integ) else "no integrity report"
        print(f"  {d}  {os.path.getsize(p) / 2**30:.2f} GB  | {it}")
print("  score 7d report:", sh(f"ls -la --time-style=+%m-%dT%H:%M {HOME}/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/bme_score_report_7d.* 2>&1 | awk '{{print $6, $7}}' | tr '\\n' ' '"))
print("  score job unit:", sh("systemctl show bme-score-7d.service -p ActiveState -p Result -p ExecMainStatus --value | tr '\\n' ' '"), "| log tail:", sh(f"tail -n 2 {HOME}/mstack/bme_scoring/score_7d.log | cut -c1-160 | tr '\\n' ' | '"))
print("  BME folder size:", sh(f"du -sh {HOME}/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme | cut -f1"))

section("measurement stack: daily reports, status PDFs, US rounds, weather, regime, alerts")
print("  daily reports:", sh(f"ls {HOME}/mstack/reports/ | grep DAILY | tr '\\n' ' '"))
pdfs = sorted(glob.glob(f"{HOME}/mstack/reports/status/STATUS-*.pdf")); cnt = collections.Counter(os.path.basename(p)[7:17] for p in pdfs)
print("  status PDFs per day:", dict(sorted(cnt.items())), "| total", len(pdfs), "| newest", os.path.basename(pdfs[-1]) if pdfs else "none")
hb = jl(f"{HOME}/mstack/state/hb_status_pdf.json"); print("  last PDF job:", {k: hb.get(k) for k in ("utc", "sent", "detail", "errors", "alerts")})
print("  pdf errors file:", sh(f"tail -n 3 {HOME}/mstack/state/status_pdf.err 2>/dev/null | cut -c1-160 | tr '\\n' ' | '") or "empty")
for d in DAYS:
    p = f"{HOME}/mstack/reports/DAILY-REPORT-{d}.md"
    if os.path.exists(p):
        t = open(p, encoding="utf-8", errors="replace").read()
        m1 = re.search(r"Rounds closed in the last 24 h: ([^\n]+)", t); m2 = re.search(r"Validation so far: \*\*([^*]+)\*\*[^.]*\. [^V]*Verdict: \*\*([^*]+)\*\*", t)
        m3 = re.search(r"NWS forecast updates detected in the last 24 h: (\d+) \(high changed on (\d+)\)\. Reaction-lag rows: (\d+)", t)
        m4 = re.search(r"Telegram alerts sent in the last 24 h: (\d+)", t); m5 = re.search(r"Sub-report errors: ([^\n]+)", t)
        print(f"  {d}: US rounds {m1.group(1) if m1 else '?'} | validation {m2.group(1) if m2 else '?'} -> {m2.group(2) if m2 else '?'} | weather updates {m3.group(1) if m3 else '?'} (high changed {m3.group(2) if m3 else '?'}), lag rows {m3.group(3) if m3 else '?'} | alerts {m4.group(1) if m4 else '?'}" + (f" | ERRORS: {m5.group(1)[:120]}" if m5 else ""))
val = jl(f"{HOME}/mstack/us_measure/us_rounds/validation_summary.json"); print("  US proxy validation now:", {k: val.get(k) for k in ("comparisons", "agree", "agree_pct", "clear_rounds_2bp", "clear_rounds_agree", "two_witness_disagree", "verdict")})
us = list(csv.DictReader(open(f"{HOME}/mstack/us_measure/us_rounds/rounds.csv"))) if os.path.exists(f"{HOME}/mstack/us_measure/us_rounds/rounds.csv") else []
ud = collections.defaultdict(collections.Counter)
for r in us: ud[dstr(r["end_unix"])][r["match"]] += 1
for d in DAYS:
    if ud[d]: print(f"    US rounds {d}: {dict(ud[d])}")
wx = list(csv.DictReader(open(f"{HOME}/mstack/us_measure/weather/lag_table.csv"))) if os.path.exists(f"{HOME}/mstack/us_measure/weather/lag_table.csv") else []
print(f"  weather lag rows: {len(wx)}; with a changed high: {sum(1 for r in wx if r.get('high_delta_f') not in ('', '0', None))}")
for r in [r for r in wx if r.get("high_delta_f") not in ("", "0", None)][-8:]:
    print(f"    {r['city']} {r['nws_update_time'][:16]} high {r['previous_high_f']}->{r['new_high_f']} first quote change after {r['first_quote_change_after_update_s']} s, max move {r['max_abs_px_move_300s']}")
sums = list(csv.DictReader(open(f"{HOME}/mstack/us_measure/weather/bucket_sums.csv"))) if os.path.exists(f"{HOME}/mstack/us_measure/weather/bucket_sums.csv") else []
vd = collections.Counter(r["verdict"] for r in sums); print("  bucket-sum verdicts all time:", dict(vd))
print("  regime changes:")
for ln in open(f"{HOME}/mstack/regime_watch/changes.jsonl") if os.path.exists(f"{HOME}/mstack/regime_watch/changes.jsonl") else []:
    j = json.loads(ln); print(f"    {j['utc']} {j['source']} {'CRITICAL' if j['critical'] else 'info'}: {j['summary'][:150].replace(chr(10), ' ')}")
ac = collections.Counter()
for ln in open(f"{HOME}/mstack/state/alerts.log", errors="replace") if os.path.exists(f"{HOME}/mstack/state/alerts.log") else []:
    m = re.match(r"\[(\d{4}-\d\d-\d\d)T[^\]]+\] (SENT|MUTED|DEDUP|TELEGRAM)", ln)
    if m and m.group(1) >= DAYS[0]: ac[(m.group(1), m.group(2))] += 1
print("  stack alerts per day:", {d: {k: v for (dd, k), v in ac.items() if dd == d} for d in DAYS if any(dd == d for (dd, k) in ac)})
print("  stack SENT alerts:")
for ln in open(f"{HOME}/mstack/state/alerts.log", errors="replace") if os.path.exists(f"{HOME}/mstack/state/alerts.log") else []:
    if " SENT[" in ln and ln[1:11] >= DAYS[0] and "report:" not in ln: print("    " + ln.strip()[:200])

section("fleet monitor alerts per day (s1-monitor)")
mc = collections.defaultdict(collections.Counter); samples = collections.defaultdict(dict)
for ln in open(f"{HOME}/s1_harness/s1-monitor.service.log", errors="replace"):
    m = re.match(r"\[(\d{4}-\d\d-\d\d) [^\]]+\] (ALERT|MUTED|STORM)\[(\w+):([^\]:]+)", ln)
    if m and m.group(1) >= DAYS[0]:
        mc[m.group(1)][m.group(4)] += 1; samples[m.group(1)].setdefault(m.group(4), ln.strip()[:170])
for d in DAYS:
    if mc[d]:
        print(f"  {d}: {sum(mc[d].values())} alerts -> {dict(mc[d].most_common(8))}")
        for k, v in samples[d].items():
            if k not in ("s10arm",): print("      " + v)
print("  hourly status sent per day:", dict(collections.Counter(ln[1:11] for ln in open(f"{HOME}/s1_harness/s1-monitor.service.log", errors="replace") if "hourly status sent" in ln and ln[1:11] >= DAYS[0])))
print("  monitor internal errors:", sh(f"grep -c 'internal error' {HOME}/s1_harness/s1-monitor.service.log"))

section("Moon Dev key")
keys = {}
for f in glob.glob(f"{HOME}/*/.env") + glob.glob(f"{HOME}/*/*/.env"):
    try: txt = open(f).read()
    except Exception: continue
    m = re.search(r"^MOONDEV_API_KEY\s*=\s*(.+)$", txt, re.M)
    if m: keys.setdefault(m.group(1).strip().strip('"'), []).append(f.replace(HOME, "~"))
for k, fs in keys.items():
    fp = hashlib.sha256(k.encode()).hexdigest()[:10]
    for u in ("all_liquidations/10m.json", "imbalance/1h.json", "poly/whales"):
        t = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request("https://api.moondev.com/api/" + u, headers={"X-API-Key": k, "User-Agent": "Mozilla/5.0"}), timeout=20)
            b = r.read(); json.loads(b); print(f"  key {fp} ({len(fs)} files)  {u:28} 200  {len(b):>7} B  {(time.time() - t) * 1000:.0f} ms")
        except urllib.error.HTTPError as e: print(f"  key {fp}  {u:28} HTTP {e.code} {e.read()[:100]!r}")
        except Exception as e: print(f"  key {fp}  {u:28} ERR {e!r}"[:140])
mdc = {d: collections.Counter() for d in DAYS}
for name, path in (("S3", "s3_feefarm/s3_maker.log"), ("S46", "s46_coinflip_cascade/s46_harvester.log"), ("S10", "s10_volbox/s10_box.log")):
    for d, ln in day_lines(f"{HOME}/{path}"):
        if "moondev" in ln.lower() and re.search(r"40[13]|expired|REJECT", ln): mdc[d][name + "_auth"] += 1
        elif "moondev" in ln.lower() and "429" in ln: mdc[d][name + "_429"] += 1
        elif "moondev" in ln.lower() and re.search(r"error|HTTP", ln, re.I): mdc[d][name + "_other"] += 1
print("  Moon Dev errors per day (auth / rate-limit / other):")
for d in DAYS:
    if mdc[d]: print(f"    {d}: {dict(mdc[d])}")
print("  monitor key probes:", {k: v for k, v in jl(f"{HOME}/s1_harness/monitor_state.json").items() if k.endswith("_key") and isinstance(v, str)})
