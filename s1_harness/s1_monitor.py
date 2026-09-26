#!/usr/bin/env python3
"""
S1 MONITOR - Telegram alerts + data-quality checks for the S1 TWAP Lock-In strategy.

READ-ONLY: never touches trader/harness state, never places orders, never restarts anything.
It runs as its own service so trader.py stays byte-identical (strategy untouched).

URGENT alerts only (since 2026-09-21, living record Part #22), each once:
  * LIVE ORDER / LIVE ORDER FAILED / DAILY STOP / FORCE_LIVE (real money paths)
  * DATA ERRORS: the END-VALUE settlement rule contradicted at >=4 bps (the true rule -
    a contradiction means our recording or understanding broke), trader-vs-observer settlement
    mismatch, implausible prices, duplicate/misaligned rounds, partial coverage, lost rounds,
    no new rounds, feed storms, strategy constants changed on disk, BME/disk faults
  * service down / restarted, .env appeared, LIVE_TRADING / FORCE_LIVE / KILL /
    S1_LIVE_BLOCKED changed, gabagool22 RETURN, S9 gate-verdict changes
DIGESTED into the hourly status instead of pinging: S1 dry signals + scores (S1 is falsified
and hard-blocked - its signals are research data, not events), S9 EDGE-AGREE / OUTCOME /
CO-SIGNAL, S9 resolution gaps. Plus one daily summary at 07:00 UTC.
Warnings are de-duplicated for 6 h; verdicts are sent once ever; global 10-per-10-min breaker.

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
SERVICES = ["s1-harness", "s1-trader", "s9-watcher", "bme-capture", "s3-maker", "s6-harvester", "s2-collect"]

# ---- S3 fee-farm maker (s3_feefarm/s3_maker.py, 2026-09-24) --------------------------------
# Urgent: anything LIVE (quote/fill/exit placed with real money), DAILY STOP / KILL, a fill that
# violated the band gate (FILL REJECTED = the Part-4 bug class), the Moon Dev key rejected
# (401/403 -> the strategy goes FLAT), error storms. Digested hourly: quotes, dry fills, pairs,
# pulls by trigger, day P&L, moondev feed health. Baseline (M5, backtest): 7.52 +/- 2.12
# rounds-with-fills per hour, min 2 / max 12 - alerts may not be tighter than that.
# 2026-09-24: S3 + S6 share one code base, so ONE generic checker (check_maker) runs per MAKERS
# entry. Added after the S3 zombie (tick loop died 00:07Z, service "active", 0 quotes for 6 h):
# heartbeat (stats tick_ts) and "0 quotes in a quiet hour" with the SKIP reasons.
S3_DIR = "/home/ubuntupolymarket3/s3_feefarm"
S3_LOG, S3_STATS, S3_ENV, S3_KILL = (os.path.join(S3_DIR, n) for n in ("s3_maker.log", "s3_stats.json", ".env", "KILL"))
S3_LIQ_URL = "https://api.moondev.com/api/all_liquidations/10m.json"
S3_FILLS_H_MIN, S3_FILLS_H_MAX = 2, 12          # observed min/max of the backtest baseline
S3_ERRS_PER_H_MAX = 10
S3_QUIET_UTC = [(0, 12), (16, 18)]              # mirrors s3_maker.py QUIET_UTC (+ weekends)
S3_TS_RE = re.compile(r"^\[[^\]]+\]\s*")
MAKER_HB_DEAD_S = 5 * 60
# ---- S2 open-print displacement recorder (s2_openprint/capture.py, 2026-09-26) -------------------
# Read-only: no keys, no orders. 60-min sessions under data/sessions/<UTC>-<pid>, restarted by systemd.
# Checks: service; newest session's ticks.jsonl must keep growing (a connected socket with no ticks
# is the failure mode the handoff guide warns about); samples/decisions per hour by skip reason;
# health.jsonl errors; sessions started per hour (restart storms); disk via the existing check.
S2_DIR = "/home/ubuntupolymarket3/s2_openprint"
S2_SESSIONS = os.path.join(S2_DIR, "data", "sessions")
S2_TICK_STALE_S = 3 * 60                    # ticks arrive ~1/s per feed; 3 min silence = feed problem
S2_ERRS_PER_H_MAX = 20                      # each 60-min session restart logs a stop record; timeouts add up
S2_SESSIONS_PER_H_MAX = 3                   # one per hour expected (+1 for a restart)                        # tick_ts older than this while active = dead main loop
MAKERS = {
    "S3": dict(svc="s3-maker", dir=S3_DIR, log="s3_maker.log", stats="s3_stats.json", shares=50,
               fills_band=(S3_FILLS_H_MIN, S3_FILLS_H_MAX), baseline="mean 7.52 ± 2.12, backtest 24 h"),
    "S6": dict(svc="s6-harvester", dir="/home/ubuntupolymarket3/s6_coinflip", log="s6_harvester.log",
               stats="s6_stats.json", shares=20, fills_band=None, baseline=None),   # no backtest yet -> no band
}

# ---- BME order-book recorder (POLYMARKET-VPS-STACK, 2026-09-21) --------------------------
# Keyless, measure-only, but DISK-HEAVY (~5.8 GB/day raw, ~2 GB/day after the midnight gzip).
# Checks: today's events file must keep growing while the service is active; reconnect storms;
# disk free % (a full disk would take every service on this box down); the disk-guard log.
BME_DIR = "/home/ubuntupolymarket3/POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE"
BME_OUT = os.path.join(BME_DIR, "s9_data", "bme")
BME_LOG = os.path.join(BME_OUT, "bme.log")
BME_GUARD_LOG = "/home/ubuntupolymarket3/POLYMARKET-VPS-STACK/bme-diskguard.log"
BME_STALL_S, BME_RECONNECTS_PER_H_MAX = 10 * 60, 12
DISK_WARN_PCT, DISK_CRIT_PCT = 25.0, 10.0
BME_STATE_MIN_ROWS_5MIN = 60          # per token; ~300 expected (1 row/s when the book is two-sided)
BME_HASHES_STALE_S = 15 * 60          # hashes_*.csv is rewritten every 5 min by the recorder

# ---- plain-English one-liners for every message that names a strategy (user request) ----------
STRAT_DESC = {
    "S1":  "S1 TWAP Lock-In: observer + DRY trader. Records every BTC 5m/15m round and tests which settlement rule Polymarket really uses (verdict: end-value, not the average). Trader is hard-blocked from live.",
    "S9":  "S9 Whale Coattails: watches 4 pro wallets and measures whether copying them would pay (measure-only; so far it does not).",
    "BME": "BME Book-Movement Engine: records every order-book move on the BTC 5m/15m markets (~1,000/s) to score 5 pre-registered signals after 7 full days.",
    "S3":  "S3 Fee-Farm Two-Sided Maker: rests a buy at 0.45 on BOTH sides of each BTC 5m/15m round during quiet hours, keeps completed pairs (0.90 -> 1.00), cuts stray legs after 20 s, and pulls its quotes on liquidation cascades / spot jumps (Moon Dev feed). DRY: fills are simulated from the public tape.",
    "S2":  "S2 Open-Print Displacement (research recorder, NO orders): 10 s after each BTC 5m/15m round opens it records the Chainlink spot vs the open, the TWAP feed, and the order book on the displaced side, then applies the strategy gates (5/8 bps move, ask <= 0.60, 2 s freshness) and logs a hypothetical candidate or the skip reason. Outcomes are joined later for replay.",
    "S6":  "S6 Coin-Flip Harvester: in quiet hours rests a buy at 0.49 on BOTH sides of each BTC 5m/15m round for the first 45 s only, keeps any completed pair (0.98 -> 1.00) and deliberately HOLDS a lone leg to the end of the round to measure whether whoever filled it knew something (kill test: 300 held legs, either side more than 8 points from a 50% win rate). DRY: fills simulated from the public tape, no real orders.",
}
HOURLY_STATUS = True                  # one combined status message at the top of every hour
LOOP_S, DEDUP_S, SUMMARY_UTC_HOUR = 30, 6 * 3600, 7
# Global circuit-breaker (added 2026-09-21 after a 1,600-ping storm): at most STORM_MAX alerts
# per STORM_WIN seconds; beyond that ONE "muted" message, silence for STORM_MUTE, then ONE summary.
STORM_MAX, STORM_WIN, STORM_MUTE = 10, 600, 1800
NO_ROWS_ALERT_S = 30 * 60          # a 5m round should land every ~6-7 min
TRADER_SILENT_ALERT_S = 12 * 60    # trader prints "open ref" every 5 min when tracking
PARTIAL_MAX_FRAC, PARTIAL_WINDOW = 0.30, 20
LOST_ROUNDS_PER_H_MAX = 3
RECONNECTS_PER_H_MAX = 6

# ---- S9 watcher (s9_watch.py, measure-only) ------------------------------------------
# Forwarded: resolved follow OUTCOMEs, gabagool22 RETURN / PYRAMID, EDGE-AGREE + CO-SIGNAL
# (did the pros trade our signal's round?), gate-verdict CHANGES, KILL exit, (re)start.
# Never forwarded: per-fill lines, CANDIDATE FOLLOW (each becomes an OUTCOME), SIGNAL-SEEN
# (duplicate of the trader's DRY SIGNAL we already send), STATUS lines (daily summary only).
S9_LOG = os.path.join(HERE, "s9_data", "s9_watch.log")
S9_STALL_S, S9_FEED_LAG_MAX, S9_ERRS_PER_H_MAX = 15 * 60, 30.0, 10
S9_FORWARD = ("OUTCOME [", "*** G22", "EDGE-AGREE", "CO-SIGNAL", "KILL file present", "s9_watch start")
S9_TS_RE = re.compile(r"^\[[^\]]+\]\s*")
S9_FEEDLAG_RE = re.compile(r"feed_lag=([\d.]+)s")
S9_GATES_RE = re.compile(r"GATES \[([^\]]+)\] (INCUBATION GATES MET|KILL CRITERION|accumulating)")

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
    elif cls == "warn":
        if now() - st["sent"].get(key, 0) < DEDUP_S: return
        st["sent"][key] = now()
    # ---- global circuit-breaker: no bug anywhere may ever flood the thread again
    st["sent_ts"] = [t for t in st.get("sent_ts", []) if t > now() - STORM_WIN]
    if cls == "once":
        # milestones / verdicts fire once EVER per key -> inherently bounded -> bypass the mute.
        # (2026-09-21: the SETTLEMENT RULE verdict was swallowed by a CO-SIGNAL-triggered mute
        # and marked consumed - never mark a once-key consumed unless it was actually sent)
        st["once"].append(key); st["sent_ts"].append(now())
        tg_send(f"🔷 {STRAT}\n{text}"); mlog(f"ALERT[{cls}:{key}] {text.replace(chr(10), ' | ')}"); return
    if now() < st.get("mute_until", 0):
        st["muted_n"] = st.get("muted_n", 0) + 1; st["muted_last"] = text[:160]
        mlog(f"MUTED[{cls}:{key}] {text.replace(chr(10), ' | ')[:160]}"); return
    if len(st["sent_ts"]) >= STORM_MAX:
        st["mute_until"] = now() + STORM_MUTE; st["muted_n"] = 1; st["muted_last"] = text[:160]
        tg_send(f"🔷 {STRAT}\n🔇 ALERT STORM: {STORM_MAX} alerts in {STORM_WIN // 60} min - muting everything for "
                f"{STORM_MUTE // 60} min, one summary follows. Something is looping; check the box.\nlast: {text[:160]}")
        mlog(f"STORM MUTE engaged; dropped [{cls}:{key}]"); return
    st["sent_ts"].append(now())
    tg_send(f"🔷 {STRAT}\n{text}"); mlog(f"ALERT[{cls}:{key}] {text.replace(chr(10), ' | ')}")

def storm_tick(st):
    """When a mute window ends, send exactly one summary of what was suppressed."""
    if st.get("mute_until") and now() >= st["mute_until"]:
        n, last = st.get("muted_n", 0), st.get("muted_last", "")
        st["mute_until"] = 0; st["muted_n"] = 0; st["sent_ts"] = []
        tg_send(f"🔷 {STRAT}\n🔈 mute ended: {n} alert(s) were suppressed. Last one: {last}")
        mlog(f"STORM MUTE ended; {n} suppressed")

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
    # "arbiter X/50" removed 2026-09-21 (Part #22): it counts full-avg agreement, a false green
    # since the premise was falsified. Show the dry ledger and the hard block instead.
    arb, s, wr = gate_status()
    return (f"S1 dry ledger n={s.get('n',0)} wr={wr:.0%} pnl ${s.get('pnl',0):+.2f} | "
            f"live {'BLOCKED' if os.path.exists(P('S1_LIVE_BLOCKED')) else 'NOT BLOCKED'}")

def s1_digest(st):
    """S1 dry signals/scores since the last hourly status (then reset)."""
    d = st.get("s1_hour", {}); st["s1_hour"] = {}
    n, w, l = d.get("sig", 0), d.get("win", 0), d.get("loss", 0)
    return f"last hour: {n} dry signal(s), scored {w} win / {l} loss" if (n or w or l) else "last hour: no dry signals"

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
        m = SCORE_RE.search(ln)
        if m:   # still cross-checked against the observer; counted for the hourly digest
            st["scores_checked"].append([m.group(1), m.group(2), m.group(5)])
            h = st.setdefault("s1_hour", {}); k = "win" if m.group(6) == "WIN" else "loss"; h[k] = h.get(k, 0) + 1
            continue
        if "DRY SIGNAL" in ln:
            h = st.setdefault("s1_hour", {}); h["sig"] = h.get("sig", 0) + 1
            continue
        if "trader starting" in ln:
            continue   # restarts are covered by the service checks
        if any(k in ln for k in FORWARD):
            icon = "🟢" if "LIVE ORDER" in ln and "FAILED" not in ln else "🚨" if ("FAILED" in ln or "DAILY STOP" in ln or "FORCE_LIVE" in ln) else "🔔"
            alert(st, f"fwd:{ln}", f"{icon} {body}\n{gates_line()}", "event")
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
            why = "late join" if "late join" in ln else "feed stall" if "feed stall" in ln else "resolution not final"
            if n > LOST_ROUNDS_PER_H_MAX:
                alert(st, "lost_rounds", f"⚠ observer lost {n} rounds in the last hour ({why}) - fewer rounds recorded than expected")
        elif "stall watchdog" in ln:
            alert(st, "feed_stall_reconnect", f"⚠ observer feed went silent and was reconnected by the stall watchdog: {TS_RE.sub('', ln)[:120]}")
        elif "open refs snapped" in ln:
            # 2026-09-21 (Part #23): a frozen feed shows up as the SAME open ref at consecutive bells
            # (85859.47 for 85 min). Three identical in a row = frozen -> one alert, deduped.
            m = re.search(r"twap=([\d.]+)", ln)
            if m:
                v = st.setdefault("open_refs", []); v.append(m.group(1)); del v[:-3]
                if len(v) == 3 and len(set(v)) == 1:
                    alert(st, "feed_frozen", f"🚨 observer TWAP feed FROZEN: open ref {v[0]} at 3 consecutive bells - socket zombie (watchdog should reconnect within 60 s; if this repeats, restart s1-harness)")
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
        if fa.startswith("partial"):
            # a partial-coverage row legitimately carries nan averages (the observer now skips
            # rows below 5 % coverage, Part #23); partial rate is checked separately below
            continue
        if o is None or a is None or e is None: probs.append("non-numeric price field")
        else:
            if sp and abs(sp - o) / o * 1e4 > 100: probs.append(f"twap-open vs spot-open differ {abs(sp-o)/o*1e4:.0f} bps")
            for nm, v in (("full_round_avg", a), ("end_value", e), ("tail60_avg", t60)):
                if v is not None and abs(v - o) / o > 0.03: probs.append(f"{nm} {v:.2f} is {abs(v-o)/o:.1%} from open {o:.2f}")
        if probs:
            alert(st, f"row:{lab}:{start}", f"🚨 DATA ERROR in rounds.csv row {lab} {start}:\n" + "\n".join(probs))
            continue
        # 2026-09-21 (Part #22): the full-round-average premise is FALSIFIED (verdict sent once), so
        # its contradictions are expected, not news. What matters now is the TRUE rule: the venue
        # settles on the END value of the 60 s TWAP vs the open (so far 100% at >= 4 bps). If that
        # ever fails with a clear gap, our recording or our understanding of the venue broke.
        ev = r.get("ev_twap", "")
        eg = (e - o) / o * 1e4 if (o and e is not None) else None
        if eg is not None and abs(eg) >= GAP_MIN and ev == "NO":
            alert(st, f"evrule:{lab}:{start}", f"🚨 SETTLEMENT DATA CHECK FAILED: {lab} {start} end-value gap {eg:+.1f} bps said "
                  f"{'Up' if e >= o else 'Down'} but the venue settled {settled}. The end-value rule has held on every clear "
                  f"round so far - check the observer's feed/recording.", "event")
    st["rows_seen"] = len(rs)
    # THE settlement-rule test (2026-09-21): the arbiter count (fa_twap=yes) is mostly coincidental
    # agreement with the end-value rule. Only rounds where the two hypotheses DISAGREE can decide
    # which one the venue follows - score the premise there. (Live: end-value 11/11, full-avg 0/11.)
    full = [r for r in rs if r.get("fa_twap") in ("yes", "NO") and r.get("ev_twap") in ("yes", "NO")]
    dis = [r for r in full if r["fa_twap"] != r["ev_twap"]]
    if full:
        fa_all = sum(r["fa_twap"] == "yes" for r in full); ev_all = sum(r["ev_twap"] == "yes" for r in full)
        fa_w = sum(r["fa_twap"] == "yes" for r in dis); ev_w = sum(r["ev_twap"] == "yes" for r in dis)
        st["hypo_line"] = (f"settlement rule: full-avg {fa_all}/{len(full)} | end-value {ev_all}/{len(full)} | "
                           f"on {len(dis)} disagreement rounds: full-avg {fa_w}, end-value {ev_w}")
        if len(dis) >= 5 and fa_w / len(dis) < 0.3:
            alert(st, "hypo_falsified", f"🛑 SETTLEMENT RULE: on {len(dis)} rounds where full-round-avg and end-value "
                  f"disagree, the venue followed END-VALUE {ev_w}x and full-avg {fa_w}x. The S1 premise (full-round "
                  f"average) is falsified; 'arbiter met' is coincidental agreement. Do NOT go live on S1.", "once")
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

def check_s9_log(st):
    st.setdefault("s9_verdict", {}); st.setdefault("s9_outcomes", [])
    st.setdefault("s9_last_status_t", now()); st.setdefault("s9_status", "")
    for ln in tail_new(S9_LOG, "s9_off", st):
        body = S9_TS_RE.sub("", ln)
        if body.startswith("STATUS"):
            st["s9_last_status_t"] = now(); st["s9_status"] = body[7:][:400]
            m = S9_FEEDLAG_RE.search(body)
            # sustained only: 2 consecutive STATUS lines (~10 min) above the limit. One 44 s spike
            # in 40 readings (median 2 s) pinged once on 2026-09-21 - a blip, not a fault.
            hi = bool(m) and float(m.group(1)) > S9_FEED_LAG_MAX
            if hi and st.get("s9_lag_hi"):
                alert(st, "s9_feedlag", f"⚠ S9 data-api feed lag {m.group(1)}s (> {S9_FEED_LAG_MAX:.0f}s for ~10 min) - copy-follow timing unreliable")
            st["s9_lag_hi"] = hi
            continue
        m = S9_GATES_RE.search(body)
        if m:
            name, verdict = m.group(1), m.group(2)
            prev = st["s9_verdict"].get(name)
            if prev != verdict and (prev is not None or verdict != "accumulating"):
                icon = "🟢" if "MET" in verdict else "🛑" if "KILL" in verdict else "🔔"
                alert(st, f"s9_gate:{name}:{verdict}", f"{icon} S9 gate [{name}]: {body[body.find(']') + 2:][:300]}", "event")
            st["s9_verdict"][name] = verdict
            continue
        if "cycle error" in body or "book fetch failed" in body:
            if hour_count(st, "s9_errs", 1) > S9_ERRS_PER_H_MAX:
                alert(st, "s9_err_storm", f"⚠ S9 watcher: >{S9_ERRS_PER_H_MAX} errors in the last hour, last: {body[:200]}")
            continue
        if "resolution timeout" in body or "agreement skip" in body:
            # single gaps go to the digest; only a burst is worth a ping
            h = st.setdefault("s9_hour", {}); h["gaps"] = h.get("gaps", 0) + 1
            if hour_count(st, "s9_gaps", 1) > 5:
                alert(st, "s9_res_timeout", f"⚠ S9 watcher: >5 unresolvable rounds in the last hour (data gap), last: {body[:160]}")
            continue
        if any(k in body for k in S9_FORWARD):
            # PER-ITEM lines are never forwarded (2026-09-21, twice): OUTCOME is one line per
            # resolved follow candidate (a scalper = many per round) and CO-SIGNAL one per fill.
            # They are COUNTED here and reported in the daily summary.
            if "OUTCOME [" in body:
                st["s9_outcomes"].append(now()); continue
            if "CO-SIGNAL" in body:
                continue
            # EDGE-AGREE is research data about a falsified model's signals -> hourly digest
            if "EDGE-AGREE" in body:
                h = st.setdefault("s9_hour", {}); h["agree"] = h.get("agree", 0) + 1
                if "signal WON" in body: h["won"] = h.get("won", 0) + 1
                elif "signal LOST" in body: h["lost"] = h.get("lost", 0) + 1
                continue
            icon = "🚨" if "G22" in body else "🛑" if "KILL" in body else "🔔"
            # lifecycle lines (start / KILL exit) repeat on every restart -> de-duplicated 6 h,
            # never "always send" (2026-09-21: 1,447 of these went out during a restart loop)
            if "s9_watch start" in body or "KILL file present" in body:
                alert(st, "s9_lifecycle:" + ("kill" if "KILL" in body else "start"), f"{icon} S9 {body[:300]}", "warn")
                continue
            alert(st, f"s9fwd:{ln}", f"{icon} S9 {body[:600]}", "event")
    st["s9_outcomes"] = [t for t in st["s9_outcomes"] if now() - t < 86400]
    if svc("s9-watcher", "ActiveState") == "active" and now() - st["s9_last_status_t"] > S9_STALL_S:
        alert(st, "s9_stalled", f"⚠ S9 watcher has not printed a STATUS line for {int((now() - st['s9_last_status_t']) / 60)} min (stalled?)")

def disk_free():
    try:
        import shutil
        u = shutil.disk_usage("/")
        return u.free / u.total * 100.0, u.free / 2 ** 30, u.total / 2 ** 30
    except Exception:
        return None, None, None

def bme_today_file():
    # streamed gzip since Part #17 (the raw .csv no longer exists)
    return os.path.join(BME_OUT, "events_" + utc().strftime("%Y%m%d") + ".csv.gz")

def check_bme(st):
    st.setdefault("bme_size", -1); st.setdefault("bme_grow_t", now()); st.setdefault("bme_guard_size", None)
    active = svc("bme-capture", "ActiveState") == "active"
    # 1. disk (matters for every service on the box, not just BME)
    pct, free_gb, total_gb = disk_free()
    if pct is not None:
        st["disk_line"] = f"disk free {free_gb:.1f} of {total_gb:.0f} GB ({pct:.0f}%)"
        if pct < DISK_CRIT_PCT:
            alert(st, "disk_crit", f"🚨 DISK {pct:.0f}% free ({free_gb:.1f} GB) - the guard stops BME at 2 GB; resize the disk (GCP > Disks) or prune s9_data/bme/*.gz")
        elif pct < DISK_WARN_PCT:
            alert(st, "disk_warn", f"⚠ disk {pct:.0f}% free ({free_gb:.1f} GB). BME writes ~5.8 GB/day raw; resize the disk before it matters")
    # 2. the guard fired?
    try: gsz = os.path.getsize(BME_GUARD_LOG)
    except Exception: gsz = 0
    if st["bme_guard_size"] is not None and gsz > st["bme_guard_size"]:
        try: last = open(BME_GUARD_LOG).read().strip().splitlines()[-1]
        except Exception: last = ""
        alert(st, "bme_guard", f"🛑 BME disk guard STOPPED the recorder: {last[:200]}", "event")
    st["bme_guard_size"] = gsz
    # 3. capture alive = today's events file keeps growing
    if active:
        try: sz = os.path.getsize(bme_today_file())
        except Exception: sz = -1
        if sz != st["bme_size"]:
            st["bme_size"] = sz; st["bme_grow_t"] = now()
        elif now() - st["bme_grow_t"] > BME_STALL_S:
            alert(st, "bme_stalled", f"⚠ BME recorder active but today's events file has not grown for {int((now()-st['bme_grow_t'])/60)} min")
    # 4. reconnect storms (only lines since the last check)
    for ln in tail_new(BME_LOG, "bme_off", st):
        if "WS problem" in ln or "RTDS problem" in ln:
            if hour_count(st, "bme_reconnects", 1) > BME_RECONNECTS_PER_H_MAX:
                alert(st, "bme_reconnect_storm", f"⚠ BME feed unstable: >{BME_RECONNECTS_PER_H_MAX} reconnects in the last hour (last: {TS_RE.sub('', ln)[:120]})")
        elif "Traceback" in ln:
            alert(st, "bme_traceback:" + ln[-60:], f"⚠ BME recorder error: {ln[:200]}")
    # 5. LIVE data validation on the small raw files (the big events file is validated daily by
    #    bme_integrity.py against the endpoints). bookstate_1s: one row per second per token.
    if active:
        stf = os.path.join(BME_OUT, "bookstate_1s_" + utc().strftime("%Y%m%d") + ".csv")
        st.setdefault("bme_rows5", {}); st.setdefault("bme_rows5_t", now()); st.setdefault("bme_rows_hour", 0)
        bad = locked = total = 0
        for ln in tail_new(stf, "bmest_off", st):
            f = ln.split(",")
            if f[0] == "ts_ms": continue
            total += 1
            try:
                ts, series, idx = int(f[0]), f[1], int(f[2]); bb, ba, mid = float(f[4]), float(f[5]), float(f[6])
                bd, ad, nb, na = float(f[7]), float(f[8]), int(f[9]), int(f[10])
                ok = (series in ("5m", "15m") and idx in (0, 1) and 0 < bb < 1 and 0 < ba < 1 and abs(mid - (bb + ba) / 2) < 1e-3
                      and bd >= 0 and ad >= 0 and nb >= 0 and na >= 0 and len(f) == 11)
                # bid >= ask is a LOCKED/CROSSED book - a real momentary market state (seen live
                # 2026-09-21 08:31:47, bid=ask=0.56 for 1 s), not a data error. Counted separately.
                if ok and bb >= ba: locked += 1
            except Exception:
                ok = False
            if not ok: bad += 1
            else:
                k = f"{series}:{idx}"; st["bme_rows5"][k] = st["bme_rows5"].get(k, 0) + 1; st["bme_rows_hour"] += 1
        if bad: alert(st, "bme_state_bad", f"🚨 BME DATA ERROR: {bad} unparseable bookstate rows (fields/prices) in the last check")
        if total >= 200 and locked / total > 0.01:
            alert(st, "bme_locked_books", f"⚠ BME: {locked}/{total} book-state rows show a locked/crossed book (bid >= ask) - stale L2 after reconnects?")
        if now() - st["bme_rows5_t"] >= 300:
            low = {k: v for k, v in st["bme_rows5"].items() if v < BME_STATE_MIN_ROWS_5MIN}
            if len(st["bme_rows5"]) < 4 or low:
                alert(st, "bme_state_gap", f"⚠ BME book-state gap: rows in the last 5 min per token = {st['bme_rows5'] or '{}'} (expect ~300 each for 4 tokens)")
            st["bme_rows5"] = {}; st["bme_rows5_t"] = now()
        hf = os.path.join(BME_OUT, "hashes_" + utc().strftime("%Y%m%d") + ".csv")
        try: age = now() - os.path.getmtime(hf)
        except Exception: age = None
        if age is not None and age > BME_HASHES_STALE_S:
            alert(st, "bme_hashes_stale", f"⚠ BME hashes file not refreshed for {int(age/60)} min (recorder loop stuck?)")

def s3_quiet_now():
    d = utc()
    return d.weekday() >= 5 or any(a <= d.hour < b for a, b in S3_QUIET_UTC)

def mk_path(tag, what):
    c = MAKERS[tag]; return os.path.join(c["dir"], c[what] if what in ("log", "stats") else what)

def mk_probe_key(st, tag):
    """Once per hour per maker: is the Moon Dev key still accepted? (lifetime unknown - the README
    says ~1 week). 401/403 -> the maker goes FLAT, so it must be an alert, not a surprise."""
    p = tag.lower()
    if now() - st.get(f"{p}_probe_t", 0) < 3600: return
    st[f"{p}_probe_t"] = now()
    key = read_env(mk_path(tag, ".env")).get("MOONDEV_API_KEY", "")
    if not key:
        st[f"{p}_key"] = "no key in .env"; alert(st, f"{p}_nokey", f"⚠ {tag}: no MOONDEV_API_KEY in its .env - liq/imbalance pulls dead, strategy flat"); return
    try:
        r = urllib.request.urlopen(urllib.request.Request(S3_LIQ_URL, headers={"User-Agent": "Mozilla/5.0", "X-API-Key": key}), timeout=15)
        d = json.loads(r.read().decode()); rows = d.get("liquidations") if isinstance(d, dict) else d
        n = len(rows) if isinstance(rows, list) else 0
        st[f"{p}_key"] = f"moondev key OK ({n} liq rows in 10 min)"
        if n == 0: alert(st, f"{p}_liq_empty", f"⚠ {tag}: Moon Dev liq feed returned 0 rows (200-but-empty = feed unhealthy; maker treats it as stale -> flat)")
    except urllib.error.HTTPError as e:
        st[f"{p}_key"] = f"moondev HTTP {e.code}"
        if e.code in (401, 403): alert(st, f"{p}_key_dead", f"🚨 {tag}: Moon Dev API key REJECTED (HTTP {e.code}) - expired or revoked. The maker is FLAT until a new key is put in its .env")
        else: alert(st, f"{p}_key_http", f"⚠ {tag}: Moon Dev liq feed HTTP {e.code} on the hourly probe")
    except Exception as e:
        st[f"{p}_key"] = f"moondev probe error {e.__class__.__name__}"; alert(st, f"{p}_probe_err", f"⚠ {tag}: Moon Dev probe failed: {e!r}"[:200])

def mk_skip_key(body):
    """'SKIP 5m 1790000000: feed stale; pulled: LIQ ...' -> 'feed stale' (first gate reason, trimmed)."""
    r = body.split(":", 1)[1].strip() if ":" in body else body
    r = r.split(";")[0].strip()
    r = re.sub(r"\$?-?\d+(\.\d+)?", "#", r)                 # numbers -> # so reasons group
    return r[:40]

def check_maker(st, tag):
    p = tag.lower(); cfg = MAKERS[tag]
    st.setdefault(f"{p}_hour", {}); h = st[f"{p}_hour"]
    LOGP, ENVP, KILLP, STATSP = mk_path(tag, "log"), mk_path(tag, ".env"), mk_path(tag, "KILL"), mk_path(tag, "stats")
    for ln in tail_new(LOGP, f"{p}_off", st):
        body = S3_TS_RE.sub("", ln)
        # --- urgent, each once
        if "(LIVE)" in body or "LIVE)" in body and ("FILL" in body or "INV-EXIT" in body):
            alert(st, f"{p}live:{ln}", f"🟢 {tag} REAL MONEY: {body[:300]}", "event"); continue
        if "DAILY STOP" in body:
            alert(st, f"{p}stop:{ln}", f"🚨 {tag} {body[:200]}", "event"); continue
        if "FILL REJECTED" in body:
            alert(st, f"{p}_fill_rejected", f"🚨 {tag} gate violation (Part-4 class): {body[:220]}"); h["rejected"] = h.get("rejected", 0) + 1; continue
        if "key expired/missing" in body or ("moondev liq 40" in body):
            alert(st, f"{p}_key_dead", f"🚨 {tag}: {body[:200]} (maker FLAT)"); continue
        if "moondev liq 429" in body:
            alert(st, f"{p}_liq_429", f"⚠ {tag}: Moon Dev rate-limited the liq feed: {body[:160]}"); continue
        if "moondev" in body and ("error" in body or "HTTP" in body) or "tape error" in body or "tick error" in body or "Traceback" in body:
            h["errors"] = h.get("errors", 0) + 1
            if "tick error" in body:                      # main-loop exception (guarded since 09-24) - always worth one ping
                alert(st, f"{p}_tick_err", f"⚠ {tag} main-loop exception (loop survived): {body[:200]}")
            if hour_count(st, f"{p}_errs", 1) > S3_ERRS_PER_H_MAX:
                alert(st, f"{p}_err_storm", f"⚠ {tag}: >{S3_ERRS_PER_H_MAX} feed/API errors in the last hour, last: {body[:180]}")
            continue
        if "RTDS silent" in body or "RTDS problem" in body:
            if hour_count(st, f"{p}_rtds", 1) > RECONNECTS_PER_H_MAX:
                alert(st, f"{p}_rtds_storm", f"⚠ {tag} spot feed unstable: >{RECONNECTS_PER_H_MAX} reconnects in the last hour")
            continue
        # --- digest counters
        if body.startswith("QUOTE"): h["quotes"] = h.get("quotes", 0) + 1
        elif body.startswith("SKIP "):
            h["skips"] = h.get("skips", 0) + 1; k = mk_skip_key(body)
            h.setdefault("skip_by", {})[k] = h.get("skip_by", {}).get(k, 0) + 1
        elif body.startswith("FILL "):
            h["fills"] = h.get("fills", 0) + 1
            rk = " ".join(body.split()[1:3])                      # "5m <round>" - JSON-safe list, not a set
            if rk not in h.setdefault("fill_rounds", []): h["fill_rounds"].append(rk)
        elif body.startswith("PULL BOTH"):
            trig = body.split("—")[-1].strip().split()[0] if "—" in body else "?"
            h["pulls"] = h.get("pulls", 0) + 1; h.setdefault("pull_by", {})[trig] = h.get("pull_by", {}).get(trig, 0) + 1
        elif body.startswith("INV-EXIT"): h["cuts"] = h.get("cuts", 0) + 1
        elif body.startswith("SINGLE LEG HOLD"): h["holds"] = h.get("holds", 0) + 1
        elif body.startswith("ROUND-RESULT"):
            h["rounds"] = h.get("rounds", 0) + 1
            if "paired=1" in body: h["pairs"] = h.get("pairs", 0) + 1
            h["hold_win"] = h.get("hold_win", 0) + body.count("->WIN"); h["hold_loss"] = h.get("hold_loss", 0) + body.count("->LOSS")
        elif "liq feed shape" in body: st[f"{p}_shape"] = body[:200]
    mk_probe_key(st, tag)
    active = svc(cfg["svc"], "ActiveState") == "active"
    # --- heartbeat: the main loop writes stats["tick_ts"] every 60 s (since 2026-09-24)
    if active:
        try:
            s = json.load(open(STATSP)); hb = s.get("tick_ts")
            age = now() - hb if hb else now() - os.path.getmtime(STATSP)
        except Exception: age = None
        if age is not None and age > MAKER_HB_DEAD_S:
            alert(st, f"{p}_dead_loop", f"🚨 {tag} main loop DEAD: no heartbeat for {int(age/60)} min while the service is 'active' (zombie: no quotes, no settlements). Fix: sudo systemctl restart {cfg['svc']}.service")
    # --- hourly quiet-hour checks (M5 band for S3; zero-quote check for both)
    if active and s3_quiet_now() and now() - st.get(f"{p}_band_t", now()) > 3600:
        n = len(h.get("fill_rounds", []))
        if cfg["fills_band"] and (n < cfg["fills_band"][0] or n > cfg["fills_band"][1]):
            alert(st, f"{p}_fill_band", f"⚠ {tag} fill model check: {n} rounds-with-fills in the last hour, outside the backtest baseline band {cfg['fills_band'][0]}-{cfg['fills_band'][1]} ({cfg['baseline']})")
        if h.get("quotes", 0) == 0:
            top = ", ".join(f"{k}×{v}" for k, v in sorted((h.get("skip_by") or {}).items(), key=lambda kv: -kv[1])[:3]) or "no SKIP lines either (loop dead?)"
            alert(st, f"{p}_no_quotes", f"⚠ {tag} placed 0 quotes in the last quiet hour (it should quote every round). Gate reasons: {top}")
    if f"{p}_band_t" not in st: st[f"{p}_band_t"] = now()
    # live flag on the maker's own .env
    live = read_env(ENVP).get("LIVE_TRADING") == "1"
    if st.get(f"{p}_live_seen") is not None and live != st[f"{p}_live_seen"]:
        alert(st, f"{p}_live_change", f"🚨 {tag} LIVE_TRADING=1 - it WILL rest real orders ({cfg['shares']} sh/side) when its gates pass. Intended?" if live else f"🔔 {tag} LIVE_TRADING back to 0 - DRY", "event")
    st[f"{p}_live_seen"] = live
    kill = os.path.exists(KILLP)
    if st.get(f"{p}_kill_seen") is not None and kill != st[f"{p}_kill_seen"]:
        alert(st, f"{p}_kill_change", f"🛑 {tag} KILL file present - FLAT" if kill else f"🔔 {tag} KILL file removed", "event")
    st[f"{p}_kill_seen"] = kill

def mk_digest(st, tag, reset=True):
    p = tag.lower(); cfg = MAKERS[tag]; h = st.get(f"{p}_hour", {})
    try: s = json.load(open(mk_path(tag, "stats")))
    except Exception: s = {}
    pulls = ", ".join(f"{k}×{v}" for k, v in (h.get("pull_by") or {}).items()) or "none"
    skips = ", ".join(f"{k}×{v}" for k, v in sorted((h.get("skip_by") or {}).items(), key=lambda kv: -kv[1])[:3]) or "none"
    hb = s.get("tick_ts"); hb_s = f"heartbeat {int((now()-hb)/60)} min ago" if hb else "no heartbeat yet"
    extra = f"{h.get('cuts',0)} cuts"
    if tag == "S6":
        lg, lw = s.get("side_legs") or {}, s.get("side_leg_wins") or {}
        extra = (f"{h.get('holds',0)} legs held (settled {h.get('hold_win',0)} won / {h.get('hold_loss',0)} lost) | kill-test sample: "
                 f"Up {lg.get('Up',0)} legs ({lw.get('Up',0)} won), Down {lg.get('Down',0)} legs ({lw.get('Down',0)} won) of 300 needed")
    line = (f"last hour: {h.get('quotes',0)} quotes, {h.get('fills',0)} dry fills in {len(h.get('fill_rounds', []))} rounds, "
            f"{h.get('pairs',0)} pairs, {extra}, pulls {pulls}, skipped rounds {h.get('skips',0)} ({skips}), errors {h.get('errors',0)} | "
            f"session {'quiet (quoting allowed)' if s3_quiet_now() else 'active (flat by rule)'} | {hb_s} | "
            f"all-time: rounds {s.get('n',0)} fills {s.get('fills',0)} pairs {s.get('pairs',0)} pnl ${s.get('pnl',0):+.2f} day ${s.get('day_pnl',0):+.2f} "
            f"adverse ${s.get('adverse',0):.2f} / gross ${s.get('gross',0):.2f} | {st.get(f'{p}_key','key not probed yet')} | "
            f"LIVE={'YES' if read_env(mk_path(tag, '.env')).get('LIVE_TRADING') == '1' else 'no (DRY)'} KILL={'yes' if os.path.exists(mk_path(tag, 'KILL')) else 'no'}")
    if reset: st[f"{p}_hour"] = {"fill_rounds": []}; st[f"{p}_band_t"] = now()
    return line

def s2_newest_session():
    try:
        ds = [os.path.join(S2_SESSIONS, d) for d in os.listdir(S2_SESSIONS)]
        ds = [d for d in ds if os.path.isdir(d)]
        return max(ds, key=os.path.getmtime) if ds else None
    except Exception: return None

def s2_read_new(st, sess, name):
    """New complete lines of <sess>/<name>.jsonl since last look; offsets are per session dir."""
    key = f"s2_off:{os.path.basename(sess)}:{name}"
    p = os.path.join(sess, name + ".jsonl")
    if not os.path.exists(p): return []
    return tail_new(p, key, st)

def check_s2(st):
    st.setdefault("s2_hour", {}); h = st["s2_hour"]
    active = svc("s2-collect", "ActiveState") == "active"
    sess = s2_newest_session()
    if sess and st.get("s2_sess") != sess:
        if st.get("s2_sess") is not None:
            hour_count(st, "s2_sessions", 1); h["sessions"] = h.get("sessions", 0) + 1
            if hour_count(st, "s2_sessions") > S2_SESSIONS_PER_H_MAX:
                alert(st, "s2_session_storm", f"⚠ S2: {hour_count(st,'s2_sessions')} collection sessions started in the last hour (one per hour expected) - the collector is restarting; check journalctl -u s2-collect")
        st["s2_sess"] = sess
        # forget offsets of older sessions (keep state small)
        for k in [k for k in st if k.startswith("s2_off:") and os.path.basename(sess) not in k]: st.pop(k, None)
    if not sess:
        if active: alert(st, "s2_no_session", "⚠ S2 service active but no session directory exists yet under data/sessions")
        return
    # ticks: count per feed this hour, freshness
    for ln in s2_read_new(st, sess, "ticks"):
        try: r = json.loads(ln)
        except Exception: continue
        t = r.get("topic", "?"); h.setdefault("ticks", {})[t] = h.get("ticks", {}).get(t, 0) + 1
        st["s2_last_tick"] = max(st.get("s2_last_tick", 0), float(r.get("received", 0)))
        lag = float(r.get("received", 0)) - float(r.get("observed", 0)); h["lag_sum"] = h.get("lag_sum", 0.0) + lag; h["lag_n"] = h.get("lag_n", 0) + 1
    if active and st.get("s2_last_tick") and now() - st["s2_last_tick"] > S2_TICK_STALE_S:
        alert(st, "s2_ticks_stale", f"🚨 S2: no RTDS ticks for {int((now()-st['s2_last_tick'])/60)} min while the collector is active (socket alive, no data = the handoff guide's failure mode). Session {os.path.basename(sess)}")
    for ln in s2_read_new(st, sess, "samples"): h["samples"] = h.get("samples", 0) + 1
    for ln in s2_read_new(st, sess, "decisions"):
        try: d = json.loads(ln)
        except Exception: continue
        if d.get("action") == "candidate":
            h["candidates"] = h.get("candidates", 0) + 1
            alert(st, f"s2cand:{d.get('slug')}", f"🔎 S2 hypothetical candidate (research only, NO order): {d.get('slug')} {d.get('side')} displacement {float(d.get('displacement_bps',0)):+.1f} bps, model fair {float(d.get('model_fair',0)):.3f}, assumed cost {float(d.get('assumed_cost_per_share',0)):.4f}", "event")
        else:
            k = d.get("reason", "?"); h.setdefault("skip_by", {})[k] = h.get("skip_by", {}).get(k, 0) + 1
    for ln in s2_read_new(st, sess, "health"):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("event") == "capture_stopped": h["stops"] = h.get("stops", 0) + 1; continue
        if r.get("skip"): k = "health:" + r["skip"]; h.setdefault("skip_by", {})[k] = h.get("skip_by", {}).get(k, 0) + 1; continue
        if r.get("error") is not None:
            h["errors"] = h.get("errors", 0) + 1; st["s2_last_err"] = str(r.get("error"))[:120]
            if hour_count(st, "s2_errs", 1) > S2_ERRS_PER_H_MAX:
                alert(st, "s2_err_storm", f"⚠ S2: >{S2_ERRS_PER_H_MAX} health errors in the last hour, last: {st['s2_last_err']}")
    kill = os.path.exists(os.path.join(S2_DIR, "KILL"))
    if st.get("s2_kill_seen") is not None and kill != st["s2_kill_seen"]:
        alert(st, "s2_kill_change", "🛑 S2 KILL file present - collection stops (no orders exist)" if kill else "🔔 S2 KILL file removed", "event")
    st["s2_kill_seen"] = kill

def s2_digest(st, reset=True):
    h = st.get("s2_hour", {}); sess = st.get("s2_sess")
    ticks = h.get("ticks", {}); lag = (h.get("lag_sum", 0.0) / h["lag_n"]) if h.get("lag_n") else None
    skips = ", ".join(f"{k}×{v}" for k, v in sorted((h.get("skip_by") or {}).items(), key=lambda kv: -kv[1])[:4]) or "none"
    age = f"{int(now()-st['s2_last_tick'])}s ago" if st.get("s2_last_tick") else "never"
    try:
        n_sess = len([d for d in os.listdir(S2_SESSIONS) if os.path.isdir(os.path.join(S2_SESSIONS, d))])
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(os.path.join(S2_DIR, "data")) for f in fs) / 2**20
    except Exception: n_sess, size = 0, 0.0
    line = (f"last hour: ticks spot {ticks.get('crypto_prices_chainlink',0)} / twap60 {ticks.get('crypto_prices_twap_sixty',0)} "
            f"(last tick {age}{f', feed lag {lag:.2f}s' if lag is not None else ''}), {h.get('samples',0)} samples, "
            f"{h.get('candidates',0)} hypothetical candidates, skips {skips}, errors {h.get('errors',0)}, session restarts {h.get('sessions',0)} | "
            f"sessions on disk {n_sess}, data {size:.0f} MB | session {os.path.basename(sess) if sess else 'none'} | KILL={'yes' if os.path.exists(os.path.join(S2_DIR,'KILL')) else 'no'}")
    if reset: st["s2_hour"] = {}
    return line

def hourly_status(st):
    """ONE combined status message at the top of every hour - bounded, bypasses the breaker."""
    if not HOURLY_STATUS: return
    h = utc().strftime("%Y-%m-%d %H")
    if utc().minute != 0 or st.get("last_hourly") == h: return
    st["last_hourly"] = h
    arb, s, wr = gate_status()
    pct, free_gb, total_gb = disk_free()
    try: gz = os.path.getsize(bme_today_file())
    except Exception: gz = 0
    d_gz = (gz - st.get("bme_gz_hour", gz)) / 2 ** 20; st["bme_gz_hour"] = gz
    n_out = sum(1 for t in st.get("s9_outcomes", []) if now() - t < 3600)
    svcs = ", ".join(f"{n} {'✅' if svc(n,'ActiveState') == 'active' else '❌ ' + svc(n,'ActiveState')}" for n in SERVICES)
    rows_h = st.get("bme_rows_hour", 0); st["bme_rows_hour"] = 0
    s9h = st.get("s9_hour", {}); st["s9_hour"] = {}
    # compact S9 STATUS: per-wallet follows/win/EV + the edge-agreement tally, no "last=" noise
    s9s = st.get("s9_status") or "no STATUS yet"
    s9s = re.sub(r"\s*\|?\s*[\w-]+:last=-?\d+s(?=\s*\||\s*$)", "", s9s)          # wallets with nothing but last=
    s9s = re.sub(r":last=-?\d+s", ":", s9s)
    s9_d = (f"last hour: {n_out} follow outcomes, {s9h.get('agree',0)} signal cross-checks "
            f"({s9h.get('won',0)} won / {s9h.get('lost',0)} lost), {s9h.get('gaps',0)} data gaps")
    msg = (f"🔷 hourly status {h}:00 UTC\n"
           f"{svcs}\n"
           f"— {STRAT_DESC['S1']}\n   {s1_digest(st)} | rounds recorded {len(rows_cache)} | {st.get('hypo_line', 'settlement tally pending')} | {gates_line()}\n"
           f"— {STRAT_DESC['S9']}\n   {s9_d} | {s9s[:300]}\n"
           f"— {STRAT_DESC['BME']}\n   last hour: +{d_gz:.0f} MB gz, {rows_h} book-state rows, reconnects {hour_count(st,'bme_reconnects')} | today's file {gz/2**20:.0f} MB | "
           f"{st.get('disk_line','disk ?')} | 7-day gate: day {max(1, len([f for f in os.listdir(BME_OUT) if f.startswith('events_') and f.endswith('.gz')]) if os.path.isdir(BME_OUT) else 0)} of 7\n"
           f"— {STRAT_DESC['S3']}\n   {mk_digest(st, 'S3')}\n"
           f"— {STRAT_DESC['S6']}\n   {mk_digest(st, 'S6')}\n"
           f"— {STRAT_DESC['S2']}\n   {s2_digest(st)}")
    tg_send(msg); mlog("hourly status sent")

def check_gates(st):
    # Promotion milestones / READY removed 2026-09-21 (Part #22): S1 is falsified and hard-blocked,
    # the arbiter count is a false green, and "dry 20/20" would announce a gate that can never
    # open. The dry ledger stays visible in the hourly status. Only the data sanity check remains.
    arb, s, wr = gate_status()
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
            # keyed per SERVICE (6 h dedup), not per restart number - a restart loop must produce
            # ONE alert, not one per restart (2026-09-21: 185 of these went out)
            alert(st, f"restart:{name}", f"⚠ {name}.service restarted by systemd ({nres - prev} since last check, "
                  f"#{nres} total) - check {name}.service.log", "warn")
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
    # S1_LIVE_BLOCKED marker (Part #15): hard live-block because the settlement premise is falsified
    blk = os.path.exists(P("S1_LIVE_BLOCKED"))
    if st.get("blk_seen") is not None and blk != st["blk_seen"]:
        alert(st, "blk_change", "🔒 S1_LIVE_BLOCKED marker present - trader hard-blocked from live" if blk else
              "🚨 S1_LIVE_BLOCKED marker REMOVED - trader can go live if LIVE_TRADING=1 and gates pass. Intended?", "event")
    st["blk_seen"] = blk

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
            f"{st.get('hypo_line', '')}\n"
            f"{gates_line()}\n"
            f"services: " + ", ".join(f"{n} {svc(n,'ActiveState')} (restarts {svc(n,'NRestarts')})" for n in SERVICES) + "\n"
            f"feed reconnects/h now: trader {hour_count(st,'trader_rtds')} observer {hour_count(st,'harness_rtds')}\n"
            f"S9: {len(st.get('s9_outcomes', []))} follow outcomes in 24h | {st.get('s9_status') or '(no STATUS line yet)'}\n"
            f"BME: {svc('bme-capture','ActiveState')} | today's events file (gz) {max(0, st.get('bme_size', 0)) / 2**20:.0f} MB | {st.get('disk_line', '')}\n"
            f"S3: {mk_digest(st, 'S3', reset=False)}\n"
            f"S6: {mk_digest(st, 'S6', reset=False)}\n"
            f"S2: {s2_digest(st, reset=False)}\n"
            f"LIVE_TRADING={read_env(ENV).get('LIVE_TRADING','0')} KILL={'yes' if os.path.exists(KILL) else 'no'} "
            f"S1_LIVE_BLOCKED={'yes' if os.path.exists(P('S1_LIVE_BLOCKED')) else 'NO'}")

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
    # S9 log: never replay history on first sight (the seeded sandbox log would spam old lines)
    if "s9_off" not in st and os.path.exists(S9_LOG):
        st["s9_off"] = os.path.getsize(S9_LOG); st["s9_last_status_t"] = now()
    for tag in MAKERS:                       # never replay a maker's log history on first sight
        p = tag.lower()
        if f"{p}_off" not in st and os.path.exists(mk_path(tag, "log")):
            st[f"{p}_off"] = os.path.getsize(mk_path(tag, "log"))
        st.setdefault(f"{p}_hour", {"fill_rounds": []})
    env = read_env(ENV)
    st["env_seen"] = os.path.exists(ENV); st["live_seen"] = env.get("LIVE_TRADING") == "1"
    st["force_seen"] = env.get("FORCE_LIVE") == "1"; st["kill_seen"] = os.path.exists(KILL)
    save_state(st)
    # "monitor online" at most once per 6 h (three went out in 20 min during deploys, 2026-09-21)
    alert(st, "monitor_online", f"🔔 monitor online on {os.uname().nodename}\n{gates_line()}\n"
          f"services: " + ", ".join(f"{n} {svc(n,'ActiveState')}" for n in SERVICES) +
          f"\nrounds.csv rows: {len(rows_cache)} | .env: {'present' if st['env_seen'] else 'absent (DRY)'}", "warn")
    mlog("monitor started")
    errs = 0
    while True:
        try:
            storm_tick(st)
            check_trader_log(st); check_harness_log(st); check_rounds(st); check_s9_log(st); check_bme(st); check_maker(st, "S3"); check_maker(st, "S6"); check_s2(st)
            check_gates(st); check_services(st); check_env(st); daily_summary(st); hourly_status(st)
            save_state(st); errs = 0
        except Exception as e:
            errs += 1; mlog(f"monitor internal error #{errs}: {e!r}")
            if errs in (3, 20): alert(st, f"monitor_err:{errs}", f"⚠ monitor internal error x{errs}: {e!r}"[:300])
        time.sleep(LOOP_S)

if __name__ == "__main__":
    main()
