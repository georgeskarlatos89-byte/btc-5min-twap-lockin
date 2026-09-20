#!/usr/bin/env python3
"""
S1 MONITOR - Telegram alerts + data-quality checks for the S1 TWAP Lock-In strategy.

READ-ONLY: never touches trader/harness state, never places orders, never restarts anything.
It runs as its own service so trader.py stays byte-identical (strategy untouched).

Sends ONLY what matters, each once:
  * every DRY SIGNAL / SCORE / LIVE ORDER / LIVE ORDER FAILED / DAILY STOP / FORCE_LIVE line
  * gate milestones (arbiter 10/25/50, dry 5/10/20) and "READY: both gates met"
  * DATA ERRORS: core hypothesis contradicted at >=4 bps, trader-vs-harness settlement
    mismatch, implausible prices, duplicate/misaligned rounds, too many partial-coverage
    rounds, rounds being lost to slow resolution, no new rounds, feed reconnect storms,
    strategy constants changed on disk
  * service down / restarted, .env appeared, LIVE_TRADING or FORCE_LIVE flipped, KILL written
  * one daily summary at 07:00 UTC
Anything else (open refs, stream updates, ROUND-RESULT lines) is deliberately NOT sent.
Warnings are de-duplicated for 6 h; milestones are sent once ever.

Config: telegram.env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_THREAD_ID) with .env
overriding. Without a token it still runs every check and prints them to its own log.
"""
import csv, json, math, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)
TRADER_LOG, HARNESS_LOG, ROUNDS_CSV = P("trader.log"), P("harness.log"), P("rounds.csv")
STATS, KILL, ENV, TG_ENV, STATE, TRADER_PY = P("trader_stats.json"), P("KILL"), P(".env"), P("telegram.env"), P("monitor_state.json"), P("trader.py")

STRAT = "S1 TWAP Lock-In"
# Mirrors trader.py - verified against the file on disk at startup (alert if they differ).
EXPECT = {"ARB_MIN": "50", "SIG_MIN": "20", "SIG_WR": "0.90", "GAP_MIN": "4.0",
          "EDGE_MIN": "0.02", "TRADE_USD": "10.0", "DAILY_STOP": "20.0", "MAX_PER_HOUR": "3"}
ARB_MIN, SIG_MIN, SIG_WR, GAP_MIN = 50, 20, 0.90, 4.0
SERVICES = ["s1-harness", "s1-trader"]
LOOP_S, DEDUP_S, SUMMARY_UTC_HOUR = 30, 6 * 3600, 7
NO_ROWS_ALERT_S = 30 * 60          # a 5m round should land every ~6-7 min
TRADER_SILENT_ALERT_S = 12 * 60    # trader prints "open ref" every 5 min when tracking
PARTIAL_MAX_FRAC, PARTIAL_WINDOW = 0.30, 20
LOST_ROUNDS_PER_H_MAX = 3
RECONNECTS_PER_H_MAX = 6

FORWARD = ("DRY SIGNAL", "SCORE ", "LIVE ORDER", "DAILY STOP", "FORCE_LIVE", "trader starting",
           "balance-allowance sync skipped")
SCORE_RE = re.compile(r"SCORE (\S+) (\d+): (Up|Down) @ ([\d.]+) settled (Up|Down) -> (WIN|LOSS)")
TS_RE = re.compile(r"^\[\d\d:\d\d:\d\d Z?\]\s*|^\[\d\d:\d\d:\d\dZ\]\s*")

def now(): return time.time()
def utc(): return datetime.now(timezone.utc)
def mlog(msg):
    print(f"[{utc().strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)

# ---------------------------------------------------------------- config / state
def read_env(path):
    env = {}
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); env[k.strip()] = v.strip()
    return env

def tg_config():
    cfg = read_env(TG_ENV)
    cfg.update({k: v for k, v in read_env(ENV).items() if k.startswith("TELEGRAM_") and v})
    return cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID"), cfg.get("TELEGRAM_THREAD_ID")

def load_state():
    try: return json.load(open(STATE))
    except Exception:
        return {"trader_off": 0, "harness_off": 0, "sent": {}, "once": [], "rows_seen": 0,
                "restarts": {}, "last_summary_day": "", "hour": {}, "env_seen": None,
                "live_seen": None, "force_seen": None, "kill_seen": None, "scores_checked": []}

def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)

# ---------------------------------------------------------------- telegram
def tg_send(text):
    token, chat, thread = tg_config()
    if not token or not chat:
        mlog("TELEGRAM (no config) >> " + text.replace("\n", " | ")); return False
    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}
    if thread: body["message_thread_id"] = thread
    data = urllib.parse.urlencode(body).encode()
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data), timeout=10)
            if r.status == 200: return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try: wait = int(json.load(e).get("parameters", {}).get("retry_after", 5))
                except Exception: wait = 5
                time.sleep(min(wait, 30)); continue
            mlog(f"telegram HTTP {e.code}: {e.read()[:120]!r}"); return False
        except Exception as e:
            mlog(f"telegram error {e!r}"); time.sleep(3)
    return False

def alert(st, key, text, cls="warn"):
    """cls: 'event' = always send; 'warn' = dedup 6h per key; 'once' = once ever."""
    if cls == "once":
        if key in st["once"]: return
        st["once"].append(key)
    elif cls == "warn":
        if now() - st["sent"].get(key, 0) < DEDUP_S: return
        st["sent"][key] = now()
    tg_send(f"🔷 {STRAT}\n{text}"); mlog(f"ALERT[{cls}:{key}] {text.replace(chr(10), ' | ')}")

# ---------------------------------------------------------------- readers
def tail_new(path, off_key, st):
    """Return new complete lines since the stored offset; handles truncation."""
    if not os.path.exists(path): return []
    size = os.path.getsize(path); off = st.get(off_key, 0)
    if size < off: off = 0                      # truncated / rotated
    with open(path, "rb") as f:
        f.seek(off); chunk = f.read()
    if not chunk: return []
    if not chunk.endswith(b"\n"):               # keep a partial last line for next time
        chunk = chunk[:chunk.rfind(b"\n") + 1]
    st[off_key] = off + len(chunk)
    return chunk.decode("utf-8", "replace").splitlines()

def rounds():
    rows = []
    if os.path.exists(ROUNDS_CSV) and os.path.getsize(ROUNDS_CSV):
        for i, r in enumerate(csv.DictReader(open(ROUNDS_CSV, newline=""))):
            r["_i"] = i; rows.append(r)
    return rows

def fnum(x):
    try: v = float(x); return v if math.isfinite(v) else None
    except Exception: return None

def gap_bps(r):
    o, a = fnum(r.get("open_ref_twap")), fnum(r.get("full_round_avg"))
    return None if not o or a is None else (a - o) / o * 1e4

def gate_status():
    rs = rows_cache
    arb = sum(1 for r in rs if r.get("fa_twap") == "yes")
    try: s = json.load(open(STATS))
    except Exception: s = {"n": 0, "wins": 0, "pnl": 0.0}
    wr = s["wins"] / s["n"] if s.get("n") else 0.0
    return arb, s, wr

def gates_line():
    arb, s, wr = gate_status()
    return f"gates: arbiter {arb}/{ARB_MIN} | dry n={s.get('n',0)}/{SIG_MIN} wr={wr:.0%} (need ≥{SIG_WR:.0%}) | dry pnl ${s.get('pnl',0):+.2f}"

def svc(name, prop):
    try: return subprocess.run(["systemctl", "show", "-p", prop, "--value", f"{name}.service"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception: return "?"

def hour_count(st, key, inc=0):
    h = st["hour"]; cut = now() - 3600
    lst = [t for t in h.get(key, []) if t > cut] + [now()] * inc
    h[key] = lst; return len(lst)

# ---------------------------------------------------------------- checks
rows_cache = []

def check_constants(st):
    try: src = open(TRADER_PY).read()
    except Exception as e: alert(st, "trader_py_missing", f"🚨 cannot read trader.py: {e}"); return
    bad = []
    for k, v in EXPECT.items():
        m = re.search(rf"^{k}\s*=\s*([^\n#]+)", src, re.M)
        got = m.group(1).strip() if m else "MISSING"
        if got != v: bad.append(f"{k}={got} (expected {v})")
    if bad: alert(st, "constants:" + "|".join(bad), "🚨 STRATEGY CONSTANTS CHANGED on disk:\n" + "\n".join(bad), "warn")

def check_trader_log(st):
    for ln in tail_new(TRADER_LOG, "trader_off", st):
        body = TS_RE.sub("", ln)
        if "RTDS problem" in ln:
            if hour_count(st, "trader_rtds", 1) > RECONNECTS_PER_H_MAX:
                alert(st, "trader_rtds_storm", f"⚠ trader feed unstable: >{RECONNECTS_PER_H_MAX} RTDS reconnects in the last hour")
            continue
        if any(k in ln for k in FORWARD):
            icon = "🟢" if "LIVE ORDER" in ln and "FAILED" not in ln else "🚨" if ("FAILED" in ln or "DAILY STOP" in ln or "FORCE_LIVE" in ln) else "📈" if "SCORE" in ln else "🔔"
            extra = "\n" + gates_line() if ("SCORE" in ln or "DRY SIGNAL" in ln or "trader starting" in ln) else ""
            alert(st, f"fwd:{ln}", f"{icon} {body}{extra}", "event")
            m = SCORE_RE.search(ln)
            if m: st["scores_checked"].append([m.group(1), m.group(2), m.group(5)])
        if "open ref" in ln: st["last_open_ref"] = now()
    # trader silent?
    if svc("s1-trader", "ActiveState") == "active" and now() - st.get("last_open_ref", now()) > TRADER_SILENT_ALERT_S:
        alert(st, "trader_silent", f"⚠ trader has not snapped a round open for {int((now()-st['last_open_ref'])/60)} min (feed or clock problem?)")

def check_harness_log(st):
    for ln in tail_new(HARNESS_LOG, "harness_off", st):
        if "RTDS problem" in ln:
            if hour_count(st, "harness_rtds", 1) > RECONNECTS_PER_H_MAX:
                alert(st, "harness_rtds_storm", f"⚠ observer feed unstable: >{RECONNECTS_PER_H_MAX} RTDS reconnects in the last hour")
        elif "row skipped" in ln or "skipping row" in ln:
            n = hour_count(st, "lost_rounds", 1)
            if n > LOST_ROUNDS_PER_H_MAX:
                alert(st, "lost_rounds", f"⚠ observer lost {n} rounds in the last hour ({'late join' if 'late join' in ln else 'resolution not final'}) - arbiter will take longer than planned")
        elif "Traceback" in ln or "Error" in ln and "RTDS" not in ln:
            alert(st, "harness_err:" + ln[-80:], f"⚠ observer error: {TS_RE.sub('', ln)[:300]}")

def check_rounds(st):
    global rows_cache
    try: rs = rounds()
    except Exception as e:
        alert(st, "rounds_parse", f"🚨 DATA ERROR: rounds.csv cannot be parsed: {e!r}"); return
    rows_cache = rs
    new = rs[st.get("rows_seen", 0):]
    seen = set()
    for r in rs:
        k = (r.get("market"), r.get("round_start"))
        if k in seen and r in new: alert(st, f"dup:{k}", f"🚨 DATA ERROR: duplicate round row {k}")
        seen.add(k)
    for r in new:
        lab, start = r.get("market"), r.get("round_start")
        T = 300 if lab == "5m" else 900 if lab == "15m" else None
        o, sp, a, e, t60 = (fnum(r.get(c)) for c in ("open_ref_twap", "open_ref_spot", "full_round_avg", "end_value", "tail60_avg"))
        fa, settled = r.get("fa_twap", ""), r.get("settled")
        probs = []
        if T is None or not start or not start.isdigit() or int(start) % T: probs.append(f"misaligned round_start {lab} {start}")
        if settled not in ("Up", "Down"): probs.append(f"settled={settled!r}")
        if not (fa in ("yes", "NO") or fa.startswith("partial")): probs.append(f"fa_twap={fa!r}")
        if o is None or a is None or e is None: probs.append("non-numeric price field")
        else:
            if sp and abs(sp - o) / o * 1e4 > 100: probs.append(f"twap-open vs spot-open differ {abs(sp-o)/o*1e4:.0f} bps")
            for nm, v in (("full_round_avg", a), ("end_value", e), ("tail60_avg", t60)):
                if v is not None and abs(v - o) / o > 0.03: probs.append(f"{nm} {v:.2f} is {abs(v-o)/o:.1%} from open {o:.2f}")
        if probs:
            alert(st, f"row:{lab}:{start}", f"🚨 DATA ERROR in rounds.csv row {lab} {start}:\n" + "\n".join(probs))
            continue
        g = gap_bps(r)
        # THE money check: the report says >=4 bps is 100% - any contradiction is news
        if g is not None and abs(g) >= GAP_MIN and fa == "NO":
            alert(st, f"hypo:{lab}:{start}", f"🚨 CORE HYPOTHESIS CONTRADICTED: {lab} {start} gap {g:+.1f} bps, full-round-avg said "
                  f"{'Up' if a >= o else 'Down'} but settled {settled}. (Backtest: 70/70 at >4 bps.)", "event")
    st["rows_seen"] = len(rs)
    # partial-coverage rate (observer/feed gaps)
    last = rs[-PARTIAL_WINDOW:]
    if len(last) >= 10:
        part = sum(1 for r in last if str(r.get("fa_twap", "")).startswith("partial"))
        if part / len(last) > PARTIAL_MAX_FRAC:
            alert(st, "partial_rate", f"⚠ {part}/{len(last)} of the last rounds have partial coverage - observer is missing feed time; those rounds do not count toward the arbiter")
    # liveness
    if rs and svc("s1-harness", "ActiveState") == "active":
        T = 300 if rs[-1].get("market") == "5m" else 900
        age = now() - (int(rs[-1]["round_start"]) + T)
        if age > NO_ROWS_ALERT_S:
            alert(st, "no_rows", f"⚠ no new round written for {int(age/60)} min although the observer service is active (rounds.csv frozen at {len(rs)} rows)")
    # trader vs harness settlement cross-check (two independent code paths, same source)
    by = {(r.get("market"), r.get("round_start")): r.get("settled") for r in rs}
    keep = []
    for lab, start, s_tr in st["scores_checked"]:
        s_h = by.get((lab, start))
        if s_h is None:
            if now() - int(start) < 3600: keep.append([lab, start, s_tr])   # harness row may still come
        elif s_h != s_tr:
            alert(st, f"mismatch:{lab}:{start}", f"🚨 DATA ERROR: trader scored {lab} {start} as settled {s_tr} but the observer recorded {s_h}")
    st["scores_checked"] = keep

def check_gates(st):
    arb, s, wr = gate_status()
    for m in (10, 25, ARB_MIN):
        if arb >= m: alert(st, f"arb{m}", f"🏁 arbiter reached {arb}/{ARB_MIN}" + (" - ARBITER GATE MET" if m == ARB_MIN else ""), "once")
    for m in (5, 10, SIG_MIN):
        if s.get("n", 0) >= m: alert(st, f"sig{m}", f"🏁 dry signals reached {s['n']}/{SIG_MIN} at wr={wr:.0%}" + (" - count met" + (", win-rate met" if wr >= SIG_WR else f", win-rate NOT met (need ≥{SIG_WR:.0%})") if m == SIG_MIN else ""), "once")
    if arb >= ARB_MIN and s.get("n", 0) >= SIG_MIN and wr >= SIG_WR:
        alert(st, "ready", f"🟢 READY - both promotion gates are MET.\n{gates_line()}\nTrader stays DRY until you set LIVE_TRADING=1 in .env yourself.", "once")
    if s.get("n", 0) and s.get("wins", 0) > s["n"]:
        alert(st, "stats_bad", f"🚨 DATA ERROR: trader_stats.json wins {s['wins']} > n {s['n']}")

def check_services(st):
    for name in SERVICES:
        state_, nres = svc(name, "ActiveState"), svc(name, "NRestarts")
        if state_ != "active":
            alert(st, f"down:{name}", f"🚨 {name}.service is {state_ or 'unknown'} (sub={svc(name,'SubState')})")
        try: nres = int(nres)
        except Exception: nres = 0
        prev = st["restarts"].get(name)
        if prev is not None and nres > prev:
            alert(st, f"restart:{name}:{nres}", f"⚠ {name}.service restarted by systemd (restart #{nres}) - check s1-{name.split('-')[1]}.service.log", "event")
        st["restarts"][name] = nres

def check_env(st):
    env = read_env(ENV)
    has = os.path.exists(ENV)
    if st["env_seen"] is not None and has != st["env_seen"]:
        alert(st, "env_change", ("🔔 .env appeared - keys present: " + ", ".join(sorted(k for k in env if env[k] and not k.startswith('TELEGRAM')))) if has else "🔔 .env removed - trader is DRY", "event")
    st["env_seen"] = has
    live = env.get("LIVE_TRADING") == "1"; force = env.get("FORCE_LIVE") == "1"; kill = os.path.exists(KILL)
    if st["live_seen"] is not None and live != st["live_seen"]:
        alert(st, "live_change", "🟢 LIVE_TRADING=1 - trader WILL place real $10 orders once its gates pass" if live else "🔔 LIVE_TRADING back to 0 - DRY", "event")
    if st["force_seen"] is not None and force != st["force_seen"]:
        alert(st, "force_change", "🚨 FORCE_LIVE=1 set - statistical gates BYPASSED (not recommended)" if force else "🔔 FORCE_LIVE cleared", "event")
    if st["kill_seen"] is not None and kill != st["kill_seen"]:
        alert(st, "kill_change", ("🚨 KILL file present - live disabled: " + open(KILL).read().strip()[:80]) if kill else "🔔 KILL file removed", "event")
    st["live_seen"], st["force_seen"], st["kill_seen"] = live, force, kill

def daily_summary(st):
    d = utc()
    if d.hour != SUMMARY_UTC_HOUR or st["last_summary_day"] == d.strftime("%Y-%m-%d"): return
    st["last_summary_day"] = d.strftime("%Y-%m-%d")
    rs = rows_cache; cut = now() - 86400
    day = [r for r in rs if r.get("round_start", "0").isdigit() and int(r["round_start"]) > cut]
    full = [r for r in rs if r.get("fa_twap") in ("yes", "NO")]
    big = [r for r in full if (g := gap_bps(r)) is not None and abs(g) >= GAP_MIN]
    yes = lambda L: sum(1 for r in L if r.get("fa_twap") == "yes")
    part = sum(1 for r in day if str(r.get("fa_twap", "")).startswith("partial"))
    arb, s, wr = gate_status()
    tg_send(f"🔷 {STRAT} - daily summary {d.strftime('%Y-%m-%d')}\n"
            f"rounds last 24h: {len(day)} ({part} partial)\n"
            f"arbiter: {arb}/{ARB_MIN} | full-avg rule holds {yes(full)}/{len(full)} overall, {yes(big)}/{len(big)} at ≥{GAP_MIN:.0f} bps\n"
            f"dry signals: n={s.get('n',0)}/{SIG_MIN} wr={wr:.0%} pnl ${s.get('pnl',0):+.2f}\n"
            f"services: " + ", ".join(f"{n} {svc(n,'ActiveState')} (restarts {svc(n,'NRestarts')})" for n in SERVICES) + "\n"
            f"feed reconnects/h now: trader {hour_count(st,'trader_rtds')} observer {hour_count(st,'harness_rtds')}\n"
            f"LIVE_TRADING={read_env(ENV).get('LIVE_TRADING','0')} KILL={'yes' if os.path.exists(KILL) else 'no'}")

# ---------------------------------------------------------------- main
def main():
    st = load_state()
    check_constants(st); check_rounds(st)
    # prime offsets on first ever run so we don't replay the whole history
    if "last_open_ref" not in st:
        st["last_open_ref"] = now()
        for p, k in ((TRADER_LOG, "trader_off"), (HARNESS_LOG, "harness_off")):
            if os.path.exists(p): st[k] = os.path.getsize(p)
    for name in SERVICES:
        try: st["restarts"].setdefault(name, int(svc(name, "NRestarts")))
        except Exception: pass
    env = read_env(ENV)
    st["env_seen"] = os.path.exists(ENV); st["live_seen"] = env.get("LIVE_TRADING") == "1"
    st["force_seen"] = env.get("FORCE_LIVE") == "1"; st["kill_seen"] = os.path.exists(KILL)
    save_state(st)
    tg_send(f"🔷 {STRAT} - monitor online on {os.uname().nodename}\n{gates_line()}\n"
            f"services: " + ", ".join(f"{n} {svc(n,'ActiveState')}" for n in SERVICES) +
            f"\nrounds.csv rows: {len(rows_cache)} | .env: {'present' if st['env_seen'] else 'absent (DRY)'}")
    mlog("monitor started")
    errs = 0
    while True:
        try:
            check_trader_log(st); check_harness_log(st); check_rounds(st)
            check_gates(st); check_services(st); check_env(st); daily_summary(st)
            save_state(st); errs = 0
        except Exception as e:
            errs += 1; mlog(f"monitor internal error #{errs}: {e!r}")
            if errs in (3, 20): alert(st, f"monitor_err:{errs}", f"⚠ monitor internal error x{errs}: {e!r}"[:300])
        time.sleep(LOOP_S)

if __name__ == "__main__":
    main()
