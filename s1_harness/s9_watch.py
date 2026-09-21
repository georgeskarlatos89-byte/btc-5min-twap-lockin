#!/usr/bin/env python3
"""
s9_watch.py — MULTI-WALLET S9 watcher (measure-only, NEVER places orders).

Watches (all public endpoints, no credentials):
  PRIMARY   gabigol   0x885278f0e304bc2d53f805af2ab779cb6011c569
            follow rule: 2nd+ taker BUY in a round-token, >=150s left in round,
            live best ask <= his price + 1c  -> CANDIDATE FOLLOW (logged, measured)
  SECONDARY 0xce25... 0xce25e214d5cfe4f459cf67f08df581885aae7fdc
            follow rule: 1st taker BUY, >=60s left, ask <= price + 1c -> CANDIDATE
  MONITOR   0x13e0... 0x13e0d447520ebe7f8eeaf7817211201b2c585204   (record only)
  G22       gabagool22 0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d
            any fill -> RETURN ALERT; 3rd+ BUY with >=150s left -> PYRAMID SIGNAL

Measures per detection: feed lag (fill_ts -> detect), book fetch ms, end-to-end.
Resolves round outcomes via /v2/resolutions and maintains cumulative stats against
the PRE-REGISTERED gates (S9-REPLAY-REPORT.md Part 3):
  INCUBATE: >=100 independent followed rounds AND availability >=40% AND EV > +2c/sh
  KILL (post-incubation): EV/share < +3c  (win-rate no longer covers price + 3c)

Outputs (s1_harness/s9_data/): s9_watch.log, s9_follows.csv, s9_latency.csv,
s9_watch_state.json.  Restart-safe (state persisted).  Kill: touch s1_harness/KILL.

Usage:
  python3 s1_harness/s9_watch.py                # run forever
  python3 s1_harness/s9_watch.py --minutes 10   # bounded run (tests / latency session)
  python3 s1_harness/s9_watch.py --backfill 6   # hours of history to classify at start
"""
import argparse, csv, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "s9_data")
os.makedirs(OUT, exist_ok=True)
KILL = os.path.join(HERE, "KILL")
LOG = os.path.join(OUT, "s9_watch.log")
FOLLOWS_CSV = os.path.join(OUT, "s9_follows.csv")
LATENCY_CSV = os.path.join(OUT, "s9_latency.csv")
STATE_JSON = os.path.join(OUT, "s9_watch_state.json")
AGREE_CSV = os.path.join(OUT, "s9_edge_agreement.csv")
HARNESS_LOG = os.path.join(HERE, "harness.log")
TRADER_LOG = os.path.join(HERE, "trader.log")

DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
RT = 0.07  # fee coefficient: fee = 0.07*q*(1-q)

WALLETS = {
    "0x885278f0e304bc2d53f805af2ab779cb6011c569": {
        "name": "gabigol", "role": "PRIMARY",
        "rule": {"min_sig_idx": 2, "min_t_left": 150, "max_sig_idx": None}},
    "0xce25e214d5cfe4f459cf67f08df581885aae7fdc": {
        "name": "ce25-unnamed", "role": "SECONDARY",
        "rule": {"min_sig_idx": 1, "min_t_left": 60, "max_sig_idx": 1}},
    "0x13e0d447520ebe7f8eeaf7817211201b2c585204": {
        "name": "13e0-longshot", "role": "MONITOR", "rule": None},
    "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d": {
        "name": "gabagool22", "role": "G22_RETURN",
        "rule": None, "pyramid": {"min_sig_idx": 3, "min_t_left": 150}},
}
GATES = {"min_rounds": 100, "min_avail": 0.40, "incubate_ev": 0.02, "kill_ev": 0.03}
POLL_S = 4.0          # per full cycle (all wallets)
FRESH_S = 120         # fills older than this are classified but not book-checked
LIVE_BOOK_TLEFT = 20  # don't book-check if <20s left (book irrelevant)

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

_last_req = [0.0]

def get(url, timeout=20, retries=6):
    """Global-throttled GET: min spacing between requests, 429-aware backoff
    (respects Retry-After; exponential otherwise)."""
    import urllib.error
    for i in range(retries):
        wait = 0.12 - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else min(30.0, 5.0 * (2 ** i))
                except ValueError:
                    delay = min(30.0, 5.0 * (2 ** i))
                time.sleep(delay)
                continue
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"rate-limited after {retries} retries: {url[:80]}")

def fee(q):
    return RT * q * (1 - q)

ROUND_RE = re.compile(r"-(5m|15m|1h|4h)-(\d+)$")
ROUND_T = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}

# --- edge-agreement parsing: harness/trader dry-signal lines and round-bell lines
SIG_H_RE = re.compile(r"⚡ SIGNAL (5m|15m) t-(\d+)s: would BUY (Up|Down) @ ([\d.]+)"
                      r" \| model p\(Up\)=([\d.]+) \| post-fee edge ([\d.]+)%\s+\(gap ([+-]?[\d.]+)bps")
SIG_T_RE = re.compile(r"DRY SIGNAL (5m|15m) t-(\d+)s: (Up|Down) @ ([\d.]+) edge ([\d.]+)% gap ([+-]?[\d.]+)bps")
BELL_RE = re.compile(r"(5m|15m) (?:round )?(\d{10}): (?:open refs? snapped|open ref)")

def round_meta(slug, fill_ts):
    m = ROUND_RE.search(slug or "")
    if not m:
        return None, None
    T = ROUND_T[m.group(1)]
    start = int(m.group(2))
    return start, T - (fill_ts - start)

class Watcher:
    def __init__(self, backfill_hours):
        self.state = {"last_ts": {}, "pending": [], "stats": {}, "seen": []}
        if os.path.exists(STATE_JSON):
            try:
                self.state = json.load(open(STATE_JSON))
                log(f"state loaded: last_ts={ {k: datetime.fromtimestamp(v, timezone.utc).strftime('%m-%d %H:%M') for k, v in self.state['last_ts'].items()} }")
            except Exception:
                pass
        now = int(time.time())
        for w, cfg in WALLETS.items():
            if w not in self.state["last_ts"]:
                self.state["last_ts"][w] = now - int(backfill_hours * 3600)
        self.seen = set(tuple(x) for x in self.state.get("seen", []))
        for k in ("log_offsets", "bells", "agree_done", "pending_agree",
                  "agree_stats", "cond_cache"):
            self.state.setdefault(k, {} if k != "agree_done" and k != "pending_agree" else [])
        self.new_fills = 0

    # ---------------- data access
    def user_fills(self, w, lo, hi, max_pages=3):
        rows, cursor, pages = [], None, 0
        base = f"{DATA}/v2/trades?" + urllib.parse.urlencode(
            {"user": w, "taker_only": "true", "limit": 500, "start": lo, "end": hi})
        while pages < max_pages:
            url = base + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
            d = get(url)
            rows += d.get("data") or []
            pages += 1
            cursor = (d.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
        return rows, cursor

    def earlier_buys(self, w, condition, start_ts, before_ts):
        """Count of his earlier taker BUYs in this round (signal index - 1)."""
        q = urllib.parse.urlencode({"user": w, "market": condition, "side": "BUY",
                                    "takerOnly": "true", "start": start_ts,
                                    "end": before_ts - 1, "limit": 1000, "offset": 0})
        try:
            d = get(f"{DATA}/trades?{q}")
            return len(d) if isinstance(d, list) else 0
        except Exception:
            return 0

    def best_ask(self, token_id):
        t0 = time.time()
        b = get(f"{CLOB}/book?token_id={token_id}")
        ms = (time.time() - t0) * 1000
        asks = b.get("asks") or []
        bid = b.get("bids") or []
        best_ask = float(asks[-1]["price"]) if asks else None
        best_bid = float(bid[-1]["price"]) if bid else None
        return best_bid, best_ask, ms

    # ---------------- processing
    def process_fill(self, w, cfg, f, now):
        ts, tok, cid = f["timestamp"], f["token_id"], f["condition_id"]
        key = (w, tok, ts, f.get("transaction_hash", ""))
        if key in self.seen:
            return
        self.seen.add(key)
        slug = f.get("slug") or ""
        # live co-signal: this fill is in a round the harness/trader flagged?
        if "-updown-" in slug and slug.startswith("btc-"):
            ms = ROUND_RE.search(slug)
            if ms:
                rstart = int(ms.group(2))
                for ag in self.state.get("pending_agree", []):
                    if ag["round_start"] == rstart:
                        same = (f.get("outcome") == ag["side"])
                        log(f"  CO-SIGNAL: {cfg['name']} filled {f.get('outcome')} in the "
                            f"flagged round ({ag['label']} {rstart}, signal side {ag['side']}) — "
                            f"{'SAME side' if same else 'OPPOSITE the signal'}")
                        break
        if f.get("side") != "BUY" or "-updown-" not in slug:
            return
        start, t_left = round_meta(f["slug"], ts)
        p = f["price"]
        live = (now - ts) <= FRESH_S
        # signal index (with backfill for correctness)
        rk = (w, cid, tok)
        if rk not in getattr(self, "_rt_idx", {}):
            self._rt_idx = getattr(self, "_rt_idx", {})
            n_prev = self.earlier_buys(w, cid, start or (ts - 15000), ts)
            self._rt_idx[rk] = n_prev
        self._rt_idx[rk] += 1
        sig_idx = self._rt_idx[rk]

        tag = f"{cfg['name']:14s}"
        if cfg["role"] == "G22_RETURN":
            log(f"*** G22 RETURN: {cfg['name']} filled {f['side']} {f['size']} @ {p} "
                f"[{f.get('outcome')}] {f['title'][:48]} (sig#{sig_idx}, {t_left}s left)")
            pyr = cfg.get("pyramid")
            if pyr and sig_idx >= pyr["min_sig_idx"] and (t_left or 0) >= pyr["min_t_left"]:
                log(f"*** G22 PYRAMID SIGNAL (sig#{sig_idx}, {t_left}s left, price {p}) — refined S9 trigger")
        else:
            log(f"fill {tag} sig#{sig_idx:<3d} t-{str(t_left):>6s}s  BUY {f['size']:>8.2f} @ {p:.3f} "
                f"[{str(f.get('outcome'))[:4]}] {f['slug'][:44]}")

        # live book check for fresh fills
        best_bid = best_ask = None
        book_ms = e2e_ms = None
        rule_pass = False
        if live and (t_left is None or t_left >= LIVE_BOOK_TLEFT):
            try:
                best_bid, best_ask, book_ms = self.best_ask(tok)
                e2e_ms = (now - ts) * 1000 + book_ms
            except Exception as e:
                log(f"  book fetch failed: {e}")
        avail = (best_ask is not None and best_ask <= p + 0.01)
        rule = cfg.get("rule")
        if rule and best_ask is not None and avail:
            ok_idx = (sig_idx >= rule["min_sig_idx"]
                      and (rule.get("max_sig_idx") is None or sig_idx <= rule["max_sig_idx"]))
            ok_t = (t_left is not None and t_left >= rule["min_t_left"])
            rule_pass = bool(ok_idx and ok_t)
            if rule_pass:
                q = best_ask
                log(f"  >>> CANDIDATE FOLLOW [{cfg['role']}] {cfg['name']}: ask {q:.3f} <= "
                    f"{p:.3f}+1c, sig#{sig_idx}, {t_left}s left, {f['slug'][:44]}")

        # latency record
        if live:
            new = not os.path.exists(LATENCY_CSV)
            with open(LATENCY_CSV, "a", newline="") as fh:
                wr = csv.writer(fh)
                if new:
                    wr.writerow(["wallet", "name", "fill_ts", "detected_at", "feed_lag_s",
                                 "book_ms", "e2e_ms", "t_left_s", "slug", "sig_idx",
                                 "his_price", "best_bid", "best_ask", "avail", "rule_pass"])
                wr.writerow([w, cfg["name"], ts, int(now), round(now - ts, 2),
                             round(book_ms, 1) if book_ms else "",
                             round(e2e_ms, 1) if e2e_ms else "",
                             t_left, f["slug"], sig_idx, p, best_bid, best_ask,
                             int(avail), int(rule_pass)])
        # follow record (candidate or measured-miss, for stats either way)
        if rule and best_ask is not None:
            new = not os.path.exists(FOLLOWS_CSV)
            with open(FOLLOWS_CSV, "a", newline="") as fh:
                wr = csv.writer(fh)
                if new:
                    wr.writerow(["wallet", "name", "role", "fill_ts", "slug", "condition_id",
                                 "token_id", "outcome_index", "sig_idx", "t_left", "his_price",
                                 "best_ask", "avail", "rule_pass", "round_end", "win", "pnl_ev"])
                wr.writerow([w, cfg["name"], cfg["role"], ts, f["slug"], cid, tok,
                             f.get("outcome_index"), sig_idx, t_left, p, best_ask,
                             int(avail), int(rule_pass),
                             (start + ROUND_T[ROUND_RE.search(f['slug']).group(1)])
                             if start else None, "", ""])
            if rule_pass:
                self.state["pending"].append(
                    {"wallet": w, "name": cfg["name"], "role": cfg["role"],
                     "condition_id": cid, "token_id": tok,
                     "outcome_index": f.get("outcome_index"),
                     "fill_ts": ts, "slug": f["slug"], "q": best_ask,
                     "round_end": (start + ROUND_T[ROUND_RE.search(f["slug"]).group(1)])
                                 if start else ts + 3600})

    def resolve_pending(self, now):
        still = []
        for pd_ in self.state["pending"]:
            if now < pd_["round_end"] + 240:
                still.append(pd_)
                continue
            if now > pd_["round_end"] + 3600:
                log(f"  resolution timeout: {pd_['slug'][:40]}")
                continue
            try:
                d = get(f"{DATA}/v2/resolutions?condition={pd_['condition_id']}").get("data") or []
                payouts = (d[0] or {}).get("payouts") or [] if d else []
                if not payouts:
                    still.append(pd_)
                    continue
                win_idx = next((i for i, v in enumerate(payouts) if (v or 0) > 0), None)
                win = (pd_["outcome_index"] == win_idx) if win_idx is not None else None
                q = pd_["q"]
                pnl = (((1 - q) if win else -q) - fee(q)) if win is not None else None
                if win is not None:
                    s = self.state["stats"].setdefault(
                        pd_["wallet"], {"follows": 0, "wins": 0, "ev_sum": 0.0,
                                        "rounds": [], "live_checks": 0, "avail": 0})
                    s["follows"] += 1
                    s["wins"] += int(win)
                    s["ev_sum"] += pnl
                    s["rounds"].append(pd_["condition_id"])
                    log(f"  OUTCOME [{pd_['name']}] {'WIN' if win else 'LOSS'} "
                        f"q={q:.3f} pnl={pnl:+.3f}/sh  {pd_['slug'][:40]} "
                        f"(cum: {s['follows']} follows, {len(set(s['rounds']))} rounds, "
                        f"EV {s['ev_sum']/s['follows']:+.3f})")
                # patch the follows CSV row
                self._patch_follow(pd_, win, pnl)
            except Exception as e:
                still.append(pd_)
        self.state["pending"] = still

    def _patch_follow(self, pd_, win, pnl):
        try:
            rows = list(csv.reader(open(FOLLOWS_CSV)))
            for r in rows[1:]:
                if r[0] == pd_["wallet"] and r[3] == str(pd_["fill_ts"]) and r[6] == pd_["token_id"]:
                    r[15], r[16] = ("" if win is None else str(win)), ("" if pnl is None else f"{pnl:.5f}")
            csv.writer(open(FOLLOWS_CSV, "w", newline="")).writerows(rows)
        except Exception:
            pass


    # ---------------- edge agreement: harness/trader dry signals x watched wallets
    def tail_signal_logs(self, live=True):
        """Incrementally read harness.log / trader.log; register dry signals for
        the edge-agreement cross-check. Live reads pin the round by wall clock
        (signals fire only in the last 45/90s of a round); the initial backfill
        read pins via TRUE bell lines (open-refs-snapped), never ROUND-RESULT
        lines (those report the previous round)."""
        for path, source in ((HARNESS_LOG, "harness"), (TRADER_LOG, "trader")):
            if not os.path.exists(path):
                continue
            off = self.state["log_offsets"].get(path, 0)
            first_read = (off == 0)
            try:
                with open(path, "r", errors="replace") as f:
                    f.seek(off)
                    new = f.read()
                    self.state["log_offsets"][path] = f.tell()
            except Exception:
                continue
            if not new:
                continue
            now = time.time()
            for line in new.splitlines():
                m = BELL_RE.search(line)
                if m:
                    self.state["bells"][m.group(1)] = int(m.group(2))
                    continue
                rx = SIG_H_RE if source == "harness" else SIG_T_RE
                m = rx.search(line)
                if not m:
                    continue
                label, tleft, side = m.group(1), int(m.group(2)), m.group(3)
                price = float(m.group(4))
                edge = float(m.group(6) if source == "harness" else m.group(5))
                gap = float(m.group(7) if source == "harness" else m.group(6))
                T = ROUND_T[label]
                start = None
                if not first_read:
                    # live: pin by wall clock; pick the candidate round whose
                    # implied t-left best matches the line (handles both the
                    # boundary case and stale reads after a poll hiccup)
                    cand = int(now // T) * T
                    cands = [c for c in (cand, cand - T, cand + T) if c <= now]
                    best = min(cands, key=lambda c: abs((T - (now - c)) - tleft),
                               default=None)
                    if best is not None and abs((T - (now - best)) - tleft) <= 60:
                        start = best
                else:
                    start = self.state["bells"].get(label)
                if start is None:
                    continue  # no reliable round context
                key = f"{source}:{label}:{start}"
                if key in self.state["agree_done"]:
                    continue
                self.state["agree_done"].append(key)
                if len(self.state["agree_done"]) > 500:
                    self.state["agree_done"] = self.state["agree_done"][-500:]
                self.state["pending_agree"].append({
                    "key": key, "source": source, "label": label, "round_start": start,
                    "side": side, "price": price, "edge_pct": edge, "gap_bps": gap,
                    "sig_ts": start + (T - tleft), "round_end": start + T})
                log(f"SIGNAL-SEEN [{source}] {label} round {start}: BUY {side} @ "
                    f"{price:.2f} edge {edge:.1f}% gap {gap:+.1f}bps -> will cross-check "
                    f"{len(WALLETS)} watched wallets after the round settles")

    def round_condition(self, label, start):
        cache = self.state.setdefault("cond_cache", {})
        key = f"{label}:{start}"
        if key in cache:
            return cache[key]
        cid = None
        try:
            m = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-{label}-{start}")
            if not m:  # closed rounds are hidden unless closed=true (changelog 2026-04-09)
                m = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-{label}-{start}&closed=true")
            cid = m[0]["conditionId"] if m else None
        except Exception:
            cid = None
        if len(cache) > 2000:
            cache.clear()
        cache[key] = cid
        return cid

    def resolve_agreements(self, now):
        """After a flagged round settles: did any watched wallet fill the same
        round? Same side / opposite side / absent, with sizes, prices, timing
        vs the signal, and the signal's own settled outcome."""
        still = []
        for ag in self.state["pending_agree"]:
            if now < ag["round_end"] + 330:
                still.append(ag)
                continue
            cid = self.round_condition(ag["label"], ag["round_start"])
            if not cid:
                if now < ag["round_end"] + 3600:
                    still.append(ag)
                    continue
                log(f"  agreement skip (no condition found): {ag['label']} {ag['round_start']}")
                continue
            try:
                d = get(f"{DATA}/v2/resolutions?condition={cid}").get("data") or []
                payouts = (d[0] or {}).get("payouts") or []
                up_won = (float(payouts[0]) > 0) if len(payouts) >= 2 else None
            except Exception:
                up_won = None
            parts = []
            for w, cfg in WALLETS.items():
                try:
                    q = urllib.parse.urlencode({"user": w, "market": cid,
                                                "takerOnly": "false", "limit": 1000, "offset": 0})
                    rows = get(f"{DATA}/trades?{q}")
                    rows = rows if isinstance(rows, list) else []
                except Exception:
                    rows = []
                same = [r for r in rows if r.get("outcome") == ag["side"]]
                opp = [r for r in rows if r.get("outcome") and r.get("outcome") != ag["side"]]
                n, sz = len(same), sum(r["size"] for r in same)
                avgp = (sum(r["price"] for r in same) / n) if n else None
                delta = (min(r["timestamp"] for r in same) - ag["sig_ts"]) if n else None
                st = self.state["agree_stats"].setdefault(
                    w, {"signals": 0, "same": 0, "opp": 0, "first_earlier": 0})
                st["signals"] += 1
                if n:
                    st["same"] += 1
                    if delta is not None and delta < 0:
                        st["first_earlier"] += 1
                if opp:
                    st["opp"] += 1
                win = None
                if up_won is not None:
                    win = (ag["side"] == "Up") == up_won
                new = not os.path.exists(AGREE_CSV)
                with open(AGREE_CSV, "a", newline="") as fh:
                    wr = csv.writer(fh)
                    if new:
                        wr.writerow(["source", "label", "round_start", "side", "signal_price",
                                     "edge_pct", "gap_bps", "wallet", "name", "role",
                                     "same_side_n", "same_side_size", "same_avg_price",
                                     "opposite_n", "first_delta_s", "settled", "signal_won"])
                    wr.writerow([ag["source"], ag["label"], ag["round_start"], ag["side"],
                                 ag["price"], ag["edge_pct"], ag["gap_bps"], w, cfg["name"],
                                 cfg["role"], n, round(sz, 2),
                                 round(avgp, 3) if avgp is not None else "",
                                 len(opp), delta if delta is not None else "",
                                 "" if up_won is None else ("Up" if up_won else "Down"),
                                 "" if win is None else int(win)])
                if n:
                    d_txt = f"{-delta}s before signal" if (delta is not None and delta < 0) \
                            else (f"+{delta}s after signal" if delta is not None else "")
                    parts.append(f"{cfg['name']}:SAME-SIDE({n} fills, {d_txt})")
                elif opp:
                    parts.append(f"{cfg['name']}:OPPOSITE-ONLY")
            win_txt = ""
            if up_won is not None:
                won = (ag["side"] == "Up") == up_won
                win_txt = f" | signal {'WON' if won else 'LOST'}"
            log(f"EDGE-AGREE [{ag['source']}] {ag['label']} round {ag['round_start']} "
                f"BUY {ag['side']} @ {ag['price']:.2f}{win_txt} | "
                + ("; ".join(parts) if parts else "no watched wallet filled this round"))
            time.sleep(0.05)
        self.state["pending_agree"] = still

    def save(self):
        self.state["seen"] = [list(x) for x in list(self.seen)[-20000:]]
        json.dump(self.state, open(STATE_JSON, "w"))

    def status_line(self):
        now = int(time.time())
        try:
            st = get(f"{DATA}/v2/status").get("data") or {}
            serving = (st.get("serving") or {})
            lag = serving.get("lag_seconds")
        except Exception:
            lag = None
        parts = [f"feed_lag={lag}s" if lag is not None else "feed_lag=?"]
        for w, cfg in WALLETS.items():
            s = self.state["stats"].get(w)
            lt = self.state["last_ts"].get(w, 0)
            age = int((time.time() - lt) if lt else -1)
            base = f"{cfg['name']}:last={age}s"
            if s and s["follows"]:
                n, r = s["follows"], len(set(s["rounds"]))
                parts.append(f"{base} follows={n} rounds={r} "
                             f"win={100*s['wins']/n:.0f}% EV={s['ev_sum']/n:+.3f}")
            else:
                parts.append(base)
        ag = self.state.get("agree_stats") or {}
        if any(v.get("signals") for v in ag.values()):
            tot = {WALLETS[w].get("name", w[:8]): f"{v['same']}/{v['signals']}"
                   for w, v in ag.items() if v.get("signals")}
            parts.append("edge-agree(same/signals)=" + json.dumps(tot))
        pend = len(self.state.get("pending_agree") or [])
        if pend:
            parts.append(f"pending_agree={pend}")
        log("STATUS " + " | ".join(parts))
        # gate evaluation per rule-wallet
        for w, cfg in WALLETS.items():
            if not cfg.get("rule"):
                continue
            s = self.state["stats"].get(w)
            if not s or not s["follows"]:
                continue
            n, r = s["follows"], len(set(s["rounds"]))
            ev = s["ev_sum"] / n
            lat = self._avail_rate(w)
            if r >= GATES["min_rounds"] and lat >= GATES["min_avail"] and ev > GATES["incubate_ev"]:
                verdict = "INCUBATION GATES MET (hold: measure-only mode)"
            elif r >= GATES["min_rounds"] and ev < GATES["kill_ev"]:
                verdict = "KILL CRITERION (EV below +3c floor)"
            else:
                verdict = (f"accumulating ({r}/{GATES['min_rounds']} rounds, "
                           f"avail={100*lat:.0f}%, EV={ev:+.3f})")
            log(f"  GATES [{cfg['name']}] {verdict}")

    def _avail_rate(self, w):
        try:
            rows = list(csv.DictReader(open(LATENCY_CSV)))
            mine = [r for r in rows if r["wallet"] == w and r["avail"] != ""]
            return sum(int(r["avail"]) for r in mine) / max(1, len(mine))
        except Exception:
            return 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0, help="0 = run forever")
    ap.add_argument("--backfill", type=int, default=6, help="hours of history at start")
    ap.add_argument("--poll", type=float, default=POLL_S)
    args = ap.parse_args()

    w = Watcher(args.backfill)
    log(f"s9_watch start — {len(WALLETS)} wallets, poll {args.poll}s, "
        f"gates {GATES}. DRY MEASURE-ONLY (never places orders).")
    t_end = time.time() + args.minutes * 60 if args.minutes else None
    cycle, t_status, t_save = 0, 0.0, 0.0
    while True:
        if os.path.exists(KILL):
            log("KILL file present — exiting.")
            break
        now = int(time.time())
        try:
            for wall, cfg in WALLETS.items():
                lo = w.state["last_ts"].get(wall, now - 3600)
                rows, cursor = w.user_fills(wall, lo + 1, now + 3600)
                for f in rows:
                    w.process_fill(wall, cfg, f, int(time.time()))
                if rows:
                    mx = max(r["timestamp"] for r in rows)
                    if mx > w.state["last_ts"].get(wall, 0):
                        w.state["last_ts"][wall] = mx
                if cursor:
                    # more history than 3 pages: bump last_ts to avoid re-walk loops
                    w.state["last_ts"][wall] = max(w.state["last_ts"].get(wall, 0), now - 60)
            w.resolve_pending(now)
            w.tail_signal_logs(live=True)
            w.resolve_agreements(now)
        except Exception as e:
            log(f"cycle error: {e}")
        cycle += 1
        if time.time() - t_status > 300:
            w.status_line()
            t_status = time.time()
        if time.time() - t_save > 60:
            w.save()
            t_save = time.time()
        if t_end and time.time() > t_end:
            log(f"--minutes {args.minutes} elapsed — exiting.")
            w.save()
            break
        time.sleep(args.poll)

if __name__ == "__main__":
    main()
