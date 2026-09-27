#!/usr/bin/env python3
"""
regime_watch.py — Regime-Change Sentinel for Polymarket (Moon Dev RBI pattern)

The venue edits its own physics: fees, tick sizes, taker delay (seconds_delay),
rewards config, settlement docs. This daemon diffs everything it can see and
raises a loud alert the moment ANY structural parameter changes, so we react
before the PnL teaches us.

WHAT IT WATCHES
  docs-full     docs.polymarket.com/llms-full.txt   (plain-text mirror of ALL
                docs: API reference, fee structure, changelogs — one hash)
  docs-toc      llms.txt table of contents (catches added/removed pages)
  sentinel-*    current live BTC up/down rounds (5m / 15m / 1h):
                feesEnabled, feeType, feeSchedule, makerBaseFee, takerBaseFee,
                tick size, negRisk, rewards min size / max spread (gamma)
                PLUS CLOB market detail: seconds_delay (the speed bump!),
                minimum_order_size, minimum_tick_size, maker/taker_base_fee,
                rewards rates (clob)
  fee-rate-*    CLOB /fee-rate per token for the live rounds (effective fee)
  us-legal-*    polymarketexchange.com/files/legal/latest/{rulebook,
                participant-agreement, risk-disclosure-statement} (PDF hashes)
  us-incentives docs.polymarket.us/incentives/user-programs (Mintlify text)
  us-gateway-*  US venue ConnectRPC gateway, unauthenticated:
                configs = the live operational config store (feature flags,
                payment kill switches); series = product series list
  us-venue-5m   STANDING GATE: scans US series/events/configs for any
                sentinel  5m-style crypto round listing; on detection alerts
                "full oracle re-audit required" (US crypto resolves via
                Binance 1m candles, NOT the .com Chainlink TWAP stream)

BEHAVIOR
  - state in regime_state.json, snapshots kept in snapshots/<source>/ (last 4)
  - changes appended to regime_watch.log as  REGIME-CHANGE ...  lines
  - optional Telegram alert: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  - errors are retried with backoff; a source blind for 5 straight cycles
    logs WATCHER-BLIND once (never crashes the daemon)
  - SIGTERM/SIGINT flushes state and exits clean (systemd-friendly)

USAGE
  python3 regime_watch.py --baseline    # first run: record state, no alerts
  python3 regime_watch.py --once        # single check cycle (good for cron/tests)
  python3 regime_watch.py               # loop forever, INTERVAL seconds (default 600)

NO SECRETS. Public endpoints only. Cheap: ~6 requests per cycle.
"""
import os, sys, json, time, signal, hashlib, difflib, argparse, re, base64
import urllib.request, urllib.parse
from datetime import datetime, timezone

# 2026-09-27 productized for twapvm (measurement stack): alerts go through the stack's
# rate-limited sender (thread-aware, global hourly cap, dedup) instead of a bare GET;
# sources are split into CRITICAL (Telegram) and INFO (log + daily report only).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import labcommon
CRITICAL = ("sentinel-", "us-weather-rules", "us-venue-5m-sentinel", "us-legal-")
CHANGES_F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "changes.jsonl")

HERE     = os.path.dirname(os.path.abspath(__file__))
STATE_F  = os.path.join(HERE, "regime_state.json")
LOG_F    = os.path.join(HERE, "regime_watch.log")
SNAP_DIR = os.path.join(HERE, "snapshots")
INTERVAL = int(os.environ.get("RW_INTERVAL", "600"))
UA       = {"User-Agent": "regime-watch/1.0 (private research)"}
SENTINEL_LABELS = ["5m", "15m"]     # hourly Up/Down was discontinued on .com (audit 2026-09-26)
MAX_SNAPSHOTS   = 4

TMO = 15
def http_get(url, timeout=TMO):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def http_json(url):
    return json.loads(http_get(url).decode())

def h(x): return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()[:16]

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_F, "a") as f: f.write(line + "\n")
    except Exception:
        pass

def telegram(msg, key="regime", cls="event"):
    try:
        labcommon.alert(key, msg[:3500], cls, source="regime_watch")
    except Exception as e:
        log(f"WARN alert failed: {e}")

def load_state():
    try:
        with open(STATE_F) as f: return json.load(f)
    except Exception:
        return {}

def save_state(st):
    tmp = STATE_F + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=1)
    os.replace(tmp, STATE_F)

def save_snapshot(source, text):
    d = os.path.join(SNAP_DIR, source)
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, f"{time.time_ns() // 1_000_000}.txt")
    with open(fn, "w") as f: f.write(text)
    olds = sorted(os.listdir(d))
    for old in olds[:-MAX_SNAPSHOTS]:
        try: os.remove(os.path.join(d, old))
        except Exception: pass
    return fn

def diff_summary(old_text, new_text, maxlines=40):
    d = list(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(),
                                  lineterm="", n=0))
    kept = [l for l in d if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    return "\n".join(kept[:maxlines]) or f"(content changed; {len(old_text)}B -> {len(new_text)}B)"

# ---------------- source collectors: each returns (text_to_hash, display_text) --------

def collect_docs_full():
    t = http_get("https://docs.polymarket.com/llms-full.txt").decode(errors="replace")
    norm = "\n".join(l.rstrip() for l in t.splitlines() if l.strip())
    return h(norm), norm

def collect_docs_toc():
    t = http_get("https://docs.polymarket.com/llms.txt").decode(errors="replace")
    norm = "\n".join(l.rstrip() for l in t.splitlines() if l.strip())
    return h(norm), norm

# ---------------- US venue sources (added 2026-09-27, from the mobile-app audit) -----
# The regulated US venue (polymarket.us / polymarketexchange.com) runs its own
# ConnectRPC gateway. All of these respond UNAUTHENTICATED (probed 2026-09-27).

US_LEGAL = {
    "us-legal-rulebook":    "https://www.polymarketexchange.com/files/legal/latest/rulebook",
    "us-legal-participant": "https://www.polymarketexchange.com/files/legal/latest/participant-agreement",
    "us-legal-risk":        "https://www.polymarketexchange.com/files/legal/latest/risk-disclosure-statement",
}

def collect_us_legal(url):
    b = http_get(url, timeout=30)                      # served as PDF
    text = (f"PDF {len(b)} bytes sha256={hashlib.sha256(b).hexdigest()}\n"
            f"(base64 of full document follows; decode to diff manually)\n"
            + base64.b64encode(b).decode())
    return h(b), text

def collect_us_incentives():
    t = http_get("https://docs.polymarket.us/incentives/user-programs", timeout=30).decode(errors="replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    norm = "\n".join(" ".join(l.split()) for l in t.splitlines() if l.strip())
    return h(norm), norm

def rpc_us(path, body=None, host="gateway.polymarket.us"):
    req = urllib.request.Request(f"https://{host}/" + path,
                                 data=json.dumps(body or {}).encode(),
                                 headers=dict(UA, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def collect_us_configs():
    # the venue's live operational config store: feature flags + payment kill switches
    cfg = rpc_us("gateway.config.v1.ConfigService/GetConfigs")
    norm = json.dumps({c["key"]: c.get("value") for c in cfg.get("configs", [])},
                      sort_keys=True, separators=(",", ":"))
    return h(norm), json.dumps({c["key"]: c.get("value") for c in cfg.get("configs", [])},
                               sort_keys=True, indent=1)

def collect_us_series():
    # product series of the US venue — a new crypto/5m-style series appears here first
    s = rpc_us("gateway.series.v1.SeriesService/GetSeries")
    rows = [{k: x.get(k) for k in ("id", "slug", "title", "active")} for x in s.get("series", [])]
    norm = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return h(norm), json.dumps(rows, sort_keys=True, indent=1)

def collect_us_weather_rules():
    """CANARY: hash the US-venue weather market DESCRIPTIONS (which embed the
    resolution station, e.g. 'Central Park (KNYC)', and the source, 'NWS
    Climatological Report'). If the venue silently swaps stations or sources,
    every priced bucket is suddenly mispriced against the new rule -> alert."""
    import urllib.request as _u
    stations = []
    for city in ("nychigh", "miahigh", "sfohigh", "laxhigh", "mdwhigh"):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = f"temp-{city}-{day}"
        try:
            req = _u.Request("https://gateway.polymarket.us/gateway.events.v1.EventsService/GetEventBySlug",
                             data=json.dumps({"slug": slug}).encode(),
                             headers=dict(UA, **{"Content-Type": "application/json"}))
            with _u.urlopen(req, timeout=20) as r:
                ev = json.loads(r.read().decode()).get("event", {})
            descs = sorted({(m.get("description") or "").strip()
                            for m in (ev.get("markets") or [])})
            # strip date + °F thresholds + city name so only the RULE text
            # (station code, resolution source) drives the hash — bucket
            # boundaries drift with seasons and would false-alarm otherwise.
            # The deduplicated rule text keeps KNYC etc. exactly intact.
            norm_descs = sorted({re.sub(r"\d+", "", d)
                                 .replace("New York City", "").replace("Miami", "")
                                 .replace("San Francisco", "").replace("Los Angeles", "")
                                 .replace("Chicago", "")
                                 for d in descs})
            norm_descs = list(dict.fromkeys(norm_descs))  # thresholds collapse to one rule
            stations.append({"city": city, "descs": norm_descs})
        except Exception as e:
            # a city that cannot be read must count as a FAILED cycle (watcher blind), never as a
            # rule change: the old code hashed the error text + date and would have alerted daily.
            raise RuntimeError(f"weather rule fetch failed for {city}: {str(e)[:60]}")
        if not norm_descs:
            raise RuntimeError(f"weather event {slug} returned no market descriptions")
    norm = json.dumps(stations, sort_keys=True, separators=(",", ":"))
    return h(norm), json.dumps(stations, sort_keys=True, indent=1)

_US_5M_PATTERNS = ("updown", "up-or-down", "up or down", "fiveminute", "five-minute",
                   "btc-updown", "chainlink", "twap")
_us_5m_alerted = {"sent": False}

def collect_us_5m_sentinel():
    """STANDING GATE: detect US-venue updown/short-horizon crypto rounds.
    DISCOVERY (2026-09-27): 15m + 1h 'BTC Up or Down' rounds ALREADY LIVE on the
    US venue — but they are INVISIBLE to GetSeries/GetEvents (search-only
    products), so this sentinel ALSO probes unified-search on unified-api.
    Oracle: CF Benchmarks BRTI TWAP60 rounded 2dp, ties settle Up (NOT Binance,
    NOT Chainlink — the app's touch-market Binance note does not apply here)."""
    hits = []
    for x in rpc_us("gateway.series.v1.SeriesService/GetSeries").get("series", []):
        blob = f"{x.get('slug','')} {x.get('title','')}".lower()
        if any(p in blob for p in _US_5M_PATTERNS): hits.append(("series", x.get("slug")))
    for x in rpc_us("gateway.events.v1.EventsService/GetEvents", {}).get("events", [])[:500]:
        blob = f"{x.get('slug','')} {x.get('title','')}".lower()
        if any(p in blob for p in _US_5M_PATTERNS): hits.append(("event", x.get("slug")))
    # unified-search: the ONLY place updown rounds are discoverable
    try:
        bag = rpc_us("unified.domain.search.v1.SearchUnifiedService/Search",
                     {"query": "BTC Up or Down"},
                     host="unified-api.polymarket.us").get("bag", {})
        for x in (bag.get("events") or []):
            slug = (x.get("slug") or {}).get("value", "")
            if any(p in slug.lower() for p in _US_5M_PATTERNS):
                hits.append(("search-event", slug))
        for x in (bag.get("markets") or []):
            slug = (x.get("slug") or {}).get("value", "")
            if any(p in slug.lower() for p in _US_5M_PATTERNS):
                hits.append(("search-market", slug))
    except Exception as e:
        raise RuntimeError(f"unified-search failed: {str(e)[:80]}")      # blind, not a change
    for x in rpc_us("gateway.config.v1.ConfigService/GetConfigs").get("configs", []):
        blob = json.dumps(x).lower()
        if "updown" in blob or "up-or-down" in blob or "fiveminute" in blob:
            hits.append(("config", x.get("key")))
    if hits:
        # normalize: strip round timestamps so the hash only changes when a NEW
        # round TYPE appears (e.g. 5m added), not every 15-min round rollover
        pats = set()
        for src, slug in hits:
            pats.add(re.sub(r"-\d{4}-\d{2}-\d{2}(-\d{4}z?)?$", "", str(slug).lower()))
        text = "DETECTED " + json.dumps(sorted(pats))
        # no direct alert here: the baseline already contains 15m + 1h, and the generic
        # REGIME-CHANGE path fires exactly when the SET of round types changes (e.g. 5m appears).
    else:
        text = "NONE-DETECTED"
    return h(text), text

def resolve_sentinel(label):
    """Current live round for a btc-updown label, or None."""
    T = {"5m": 300, "15m": 900, "1h": 3600, "60m": 3600}[label]
    start = int(time.time() // T) * T
    slug = f"btc-updown-{label}-{start}"
    ev = http_json(f"https://gamma-api.polymarket.com/events?slug={slug}")
    if not ev:
        return None
    m = ev[0]["markets"][0]
    cid = m["conditionId"]
    toks = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
    # structural fields ONLY — ids/prices/volume change every round by design
    struct = {
        "label": label,
        "feesEnabled": m.get("feesEnabled"),
        "feeType": m.get("feeType"),
        "feeSchedule": m.get("feeSchedule"),
        "makerBaseFee": m.get("makerBaseFee"),
        "takerBaseFee": m.get("takerBaseFee"),
        # 2026-09-27: gamma orderPriceMinTickSize is DYNAMIC per round (0.001 once the price is near
        # 0 or 1, 0.01 otherwise), so it flipped the hash at every rollover (soak test 12:15:58Z, and
        # the false alarm in the workspace log). Not hashed; the CLOB minimum_tick_size below is the
        # base tick and is what a rule change would move.
        "negRisk": m.get("negRisk"),
        "rewardsMinSize": m.get("rewardsMinSize"),
        "rewardsMaxSpread": m.get("rewardsMaxSpread"),
        "holdingRewardsEnabled": m.get("holdingRewardsEnabled"),
    }
    try:  # CLOB detail: the physics (seconds_delay = taker speed bump)
        cm = http_json(f"https://clob.polymarket.com/markets/{cid}")
        struct.update({
            "seconds_delay": cm.get("seconds_delay"),
            "itode": cm.get("itode"),          # 250ms taker delay flag (docs: selected up/down markets)
            "minimum_order_size": cm.get("minimum_order_size"),
            "minimum_tick_size": cm.get("minimum_tick_size"),
            "clob_maker_base_fee": cm.get("maker_base_fee"),
            "clob_taker_base_fee": cm.get("taker_base_fee"),
            "clob_rewards": cm.get("rewards"),
            "is_50_50_outcome": cm.get("is_50_50_outcome"),
        })
    except Exception as e:
        # a transient CLOB failure used to be written INTO the hashed text, which turned one
        # timeout into a "critical" rule-change alert. A failed read is a blind cycle, not a change.
        raise RuntimeError(f"clob market detail failed: {str(e)[:80]}")
    try:  # effective per-token fee (what the docs warn against hardcoding)
        fr = http_json(f"https://clob.polymarket.com/fee-rate?token_id={toks[0]}")
        struct["fee_rate"] = fr
    except Exception as e:
        raise RuntimeError(f"clob fee-rate failed: {str(e)[:80]}")
    blob = json.dumps(struct, sort_keys=True, default=str)
    shown = blob + " | not hashed (dynamic per round): gamma tick size now = " + str(m.get("orderPriceMinTickSize"))
    return h(blob), shown

def all_sources():
    yield "docs-full", collect_docs_full
    yield "docs-toc", collect_docs_toc
    for name, url in US_LEGAL.items():
        yield name, lambda u=url: collect_us_legal(u)
    yield "us-incentives-docs", collect_us_incentives
    yield "us-gateway-configs", collect_us_configs
    yield "us-gateway-series", collect_us_series
    yield "us-weather-rules", collect_us_weather_rules
    yield "us-venue-5m-sentinel", collect_us_5m_sentinel
    for label in SENTINEL_LABELS:
        yield f"sentinel-{label}", lambda lb=label: resolve_sentinel(lb)

# ---------------- main -----------------------------------------------------------------

STOP = {"flag": False}
def _sig(*_): STOP["flag"] = True; log("stop signal received, shutting down")

def cycle(state, baseline):
    changes = []
    for name, fn in all_sources():
        if state.get(name, {}).get("disabled"):
            continue
        try:
            res = fn()
        except Exception as e:
            prev = state.get(name, {})
            prev["fails"] = prev.get("fails", 0) + 1
            if prev["fails"] == 5:
                log(f"WATCHER-BLIND source={name} after 5 consecutive errors ({e})")
                telegram(f"⚠ regime watcher BLIND on [{name}] after 5 failed cycles: {str(e)[:200]}. "
                         f"A no-change result from this source cannot be trusted until it recovers.",
                         key=f"blind:{name}", cls="warn")
            state[name] = prev
            continue
        if res is None:  # e.g. a sentinel label that simply doesn't exist
            prev = state.get(name, {})
            if "hash" not in prev:
                prev["nores"] = prev.get("nores", 0) + 1
                if prev["nores"] == 8:
                    prev["disabled"] = True
                    log(f"source {name} never resolved (label may not exist) — disabled")
                state[name] = prev
            continue
        digest, text = res
        prev = state.get(name, {})
        prev["fails"] = 0
        if baseline or "hash" not in prev:
            prev.update({"hash": digest, "baseline_ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            if not baseline: log(f"baseline learned source={name} hash={digest}")
        elif digest != prev["hash"]:
            old_hash = prev["hash"]
            old_snap_dir = os.path.join(SNAP_DIR, name)
            old_text = ""
            if prev.get("last_snapshot") and os.path.exists(prev["last_snapshot"]):
                try: old_text = open(prev["last_snapshot"]).read()
                except Exception: pass
            summary = diff_summary(old_text, text) if old_text else "(no prior snapshot)"
            prev["hash"] = digest
            prev["last_change"] = time.strftime("%Y-%m-%d %H:%M:%S")
            prev["changes"] = prev.get("changes", 0) + 1
            log(f"REGIME-CHANGE source={name} ({prev['changes']}th change)\n{summary}")
            crit = name.startswith(CRITICAL)
            try:
                with open(CHANGES_F, "a") as cf:
                    cf.write(json.dumps(dict(ts=int(time.time()), utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                             source=name, critical=crit, old_hash=old_hash, new_hash=digest,
                                             summary=summary[:1500])) + "\n")
            except Exception:
                pass
            if crit:
                if name == "us-venue-5m-sentinel":
                    hint = "US venue round types changed: full oracle re-audit (CF Benchmarks BRTI TWAP60, 2dp, ties Up) before any assumption transfers."
                elif name == "us-weather-rules":
                    hint = "Weather resolution rule text changed (station or source). Every priced bucket must be re-checked against the new rule."
                elif name.startswith("us-legal-"):
                    hint = "A US exchange legal document changed (rulebook / participant agreement / risk disclosure)."
                else:
                    hint = "Venue physics changed (fees / tick / delay / rewards). Every DRY strategy cost model depends on these."
                telegram(f"🚨 REGIME-CHANGE [{name}]\n{hint}\n{summary[:900]}", key=f"regime:{name}:{digest}", cls="event")
            changes.append(name)
        prev["last_ok"] = time.strftime("%Y-%m-%d %H:%M:%S")
        prev["last_snapshot"] = save_snapshot(name, text)
        state[name] = prev
    save_state(state)
    live = {k: v for k, v in state.items() if isinstance(v, dict) and not v.get("disabled")}
    labcommon.heartbeat("regime_watch", sources_ok=sum(1 for v in live.values() if v.get("fails", 0) == 0 and "hash" in v),
                        sources_total=len(live), blind=[k for k, v in live.items() if v.get("fails", 0) >= 5],
                        changes_this_cycle=changes)
    return changes

def status(state):
    log("--- watcher status ---")
    for name, s in sorted(state.items()):
        log(f"  {name:<18} hash={s.get('hash','-')} changes={s.get('changes',0)} last_ok={s.get('last_ok','-')}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _sig); signal.signal(signal.SIGINT, _sig)
    state = load_state()
    if a.status:
        status(state); return
    if a.baseline:
        cycle(state, baseline=True); status(state)
        log(f"baseline written to {STATE_F}")
        return
    if a.once:
        ch = cycle(state, baseline=False)
        log(f"cycle done — {'no regime changes' if not ch else 'CHANGES: ' + ','.join(ch)}")
        return
    log(f"regime watch started: interval={INTERVAL}s sources={[n for n,_ in all_sources()]}")
    if not state:
        cycle(state, baseline=True)
        log("first run: baseline recorded, subsequent cycles will alert on change")
    while not STOP["flag"]:
        try:
            cycle(state, baseline=False)
        except Exception as e:
            log(f"WARN cycle crashed (kept alive): {e}")
        for _ in range(INTERVAL):
            if STOP["flag"]: break
            time.sleep(1)
    save_state(state)

if __name__ == "__main__":
    main()
