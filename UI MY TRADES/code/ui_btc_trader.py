#!/usr/bin/env python3
"""
ui_btc_trader.py - PAPER trader that watches the real Polymarket BTC 5-minute page and places
one HYPOTHETICAL bet on every round (288 a day). No wallet, no keys, no orders.

What it does each round
  1. Switches the page to the live round ("Go to live market", or a full load if that fails).
  2. Reads what a person would see: Price To Beat, Current Price, Up/Down cents, the countdown.
  3. At fixed countdown checkpoints (240 ... 15 s left) it takes a snapshot: the UI numbers plus
     the real order book (what a buy would actually cost).
  4. Places exactly one paper bet per round at one checkpoint, records my_choice = Up/Down.
  5. After the round, joins the OFFICIAL result (gamma) and the result the UI shows, books the
     P&L, updates the pattern table, and sizes the next bets from what it has learned.

How it learns (see HOW-IT-WORKS.md for the plain version)
  * Every snapshot of every round goes into a table keyed by (seconds left, gap size): how often
    did the side that was ahead at that moment actually win, and what did it cost to buy?
  * First 150 settled rounds = LEARN: random entry checkpoint, fixed stake, mostly the leader.
  * Afterwards = ADAPT: enter at the first checkpoint whose cell shows >= 2 cents edge per share
    after the fee (and >= 30 observations); stake = half-Kelly, clamped 1%..6% of the bankroll.
    No cell with an edge -> forced minimum bet at 75 s left, because every round is traded.
  * Stakes are halved after 5 losses in a row and while the bankroll is under 60% of the start.

If the page is late or down, the same numbers are taken from the UI's own data endpoints and
the public feed, and the row is marked data_source=api_fallback. If the whole process was down,
the rounds it could not see are written as MISSED. Nothing is ever silently skipped.
"""
import argparse, asyncio, json, math, os, random, re, sys, time
from uicommon import (VERSION, BTC_DIR, STATE_DIR, SITE, GAMMA, UIBrowser, Telegram, make_logger, now, iso,
                      utc_day, save_json, load_json, write_csv, append_csv, read_csv, http_json, ahttp_json,
                      arr, fee_per_share, best_bid_ask, gamma_market, official_winner)

T = 300
START_BANKROLL = 200.0
FEE_RATE = 0.07                                   # crypto taker fee rate (bundled fee doc)
ENTRY_SLOTS = [240, 210, 180, 150, 120, 90, 75]   # seconds LEFT where a bet may be placed
SHADOW_SLOTS = [60, 45, 30, 15]                   # observed for the pattern table; late entry only if needed
ALL_SLOTS = ENTRY_SLOTS + SHADOW_SLOTS
FORCE_SLOT = 75                                   # books are mostly two-sided until about here
SLOT_GRACE_S = 12                                 # a checkpoint reached later than this is skipped
LATE_ENTRY_MIN_SLOT = 30                          # a late forced bet is allowed down to here, never at 15 s
ASK_MIN, ASK_MAX = 0.02, 0.98                     # outside this a "bet" is a lottery ticket or a sure thing: not bought
GAP_EDGES = [0.5, 1.0, 2.0, 4.0, 8.0]             # |gap| buckets in basis points
LEARN_ROUNDS = 150
PRIOR_W = 30                                      # weight of "the market price is right" prior
MIN_EDGE, MIN_CELL_N = 0.02, 30
BASE_FRAC, MIN_FRAC, MAX_FRAC = 0.025, 0.01, 0.06
MIN_SHARES = 5.0                                  # venue minimum order size
UI_STALE_DRIFT_S = 6.0                            # |UI countdown - real clock| above this = UI is late
UI_STALE_PRICE_USD = 8.0                          # no countdown readable: UI price vs feed above this = late
BROWSER_MAX_AGE_S = 2 * 3600                      # was 6 h: the page grew to 2.2 GB and the memory cap throttled it (2026-09-28)
RTDS = "wss://ws-live-data.polymarket.com"

STATE_PATH = os.path.join(STATE_DIR, "btc_state.json")
LIVE_PATH = os.path.join(STATE_DIR, "btc_live.json")     # what the dashboard shows as "now"
SHOT_PATH = os.path.join(STATE_DIR, "btc_page.jpg")      # picture of the page the trader is reading
LIVE_EVERY_S, SHOT_EVERY_S = 1.0, 5.0
ALL_CSV = os.path.join(BTC_DIR, "btc_trades_all.csv")
SNAP_CSV = os.path.join(BTC_DIR, "btc_round_snapshots.csv")
TABLE_CSV = os.path.join(BTC_DIR, "btc_pattern_table.csv")
DAILY_CSV = os.path.join(BTC_DIR, "btc_daily_summary.csv")

COLS = ["round_start_unix", "round_start_utc", "round_end_utc", "slug", "ui_url", "ui_title_range", "status",
        "price_to_beat_ui", "price_to_beat_captured_utc", "price_to_beat_ui_countdown_s", "price_to_beat_api_full",
        "entry_slot_s_remaining", "decision_utc", "decision_clock_remaining_s", "decision_ui_countdown_s",
        "ui_clock_drift_s", "ui_current_price", "gap_usd", "gap_bps", "gap_bucket", "ui_up_cents", "ui_down_cents",
        "api_twap60", "ui_price_minus_twap", "book_up_bid", "book_up_ask", "book_down_bid", "book_down_ask",
        "data_source", "phase", "my_choice", "choice_reason", "leader_side", "market_favourite_side",
        "page_leader_agrees_with_market", "exec_price_source",
        "entry_price_exec", "entry_price_ui", "fee_per_share", "cost_per_share", "est_win_prob",
        "est_edge_per_share", "bet_usd", "shares", "bankroll_before",
        "outcome_official", "outcome_ui", "outcomes_agree", "close_price_ui_api", "win", "pnl_usd",
        "pnl_if_ui_price_usd", "bankroll_after", "settled_utc",
        "loss_streak_before", "ui_read_latency_ms", "browser_launches", "code_version", "notes"]
SNAP_COLS = ["round_start_unix", "slug", "slot_s_remaining", "snap_utc", "clock_remaining_s", "ui_countdown_s",
             "ui_clock_drift_s", "ui_current_price", "price_to_beat", "gap_bps", "gap_bucket", "ui_up_cents",
             "ui_down_cents", "api_twap60", "api_spot", "book_up_bid", "book_up_ask", "book_down_bid",
             "book_down_ask", "leader_side", "market_favourite_side", "page_leader_agrees_with_market", "data_source",
             "ui_read_latency_ms", "outcome_official", "leader_won"]
TABLE_COLS = ["slot_s_remaining", "gap_bucket_bps", "page_vs_market", "observations", "leader_wins", "leader_win_rate",
              "avg_leader_ask", "avg_underdog_ask", "edge_buy_leader", "edge_buy_underdog", "verdict"]
DAILY_COLS = ["utc_day", "rounds_in_day_so_far", "rounds_recorded", "bets_settled", "bets_open", "missed",
              "no_liquidity", "wins", "losses", "win_rate", "staked_usd", "pnl_usd", "bankroll_end",
              "ui_rows", "api_fallback_rows", "ui_vs_official_disagreements"]

# The read must be cheap: the first version scanned the whole document (innerText + every element)
# on each call and took 4 s on a busy 2-core VM. Now the four elements are found once with a text
# walker near the top of the page and kept in window.__uiT until the page replaces them.
READ_JS = r"""() => {
  const here = location.pathname.split('/').pop();
  if (!window.__uiT || window.__uiT.slug !== here) window.__uiT = {slug: here};   // new round: forget every element
  const W = window.__uiT;
  // after an in-place switch the previous round's panel stays in the document, hidden, with its old
  // numbers (the page "showed" 84526.23 for three rounds). Only elements that are actually drawn count.
  const seen = e => e && e.isConnected && e.getClientRects().length > 0;
  const ok = seen;
  const find = (re) => { const it = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n, c = 0;
    while ((n = it.nextNode()) && c++ < 8000) { if (re.test((n.nodeValue || '').trim()) && seen(n.parentElement)) return n.parentElement } return null };
  if (!ok(W.ptb)) W.ptb = find(/^Price To Beat$/i);
  if (!ok(W.mins)) W.mins = find(/^mins$/i);
  if (!ok(W.secs)) W.secs = find(/^secs$/i);
  if (!ok(W.rng)) W.rng = find(/^[A-Z][a-z]+ \d{1,2}, \d{1,2}(:\d{2})?.{0,20}ET$/);
  const digit = (col) => { let best = null, bv = 1e9;
    for (const s of col.children) { const m = /translateY\((-?[\d.]+)(px|%)\)/.exec((s.style && s.style.transform) || '');
      const v = m ? Math.abs(parseFloat(m[1])) : 0; if (v < bv) { bv = v; best = (s.textContent || '').trim() } } return best };
  const unit = (l) => { if (!ok(l)) return null; const box = l.parentElement; if (!box) return null;
    const cols = box.querySelectorAll('div.tabular-nums');
    if (cols.length) { const v = parseInt([...cols].map(digit).join(''), 10); return isNaN(v) ? null : v }
    const m = /(\d+)/.exec(box.textContent || ''); return m ? parseInt(m[1], 10) : null };
  let ptb = null;
  if (ok(W.ptb)) { let p = W.ptb.parentElement;
    for (let k = 0; k < 3 && p && !ptb; k++) { const t = p.textContent || ''; if (t.length < 200) { const m = /\$[\d,]+(\.\d+)?/.exec(t); if (m) ptb = m[0] } p = p.parentElement } }
  const spans = [...document.querySelectorAll('span.pk-animated-label__text')].filter(seen).map(e => e.textContent.trim());
  const price = spans.find(s => /^\$[\d,]+\.\d{2}$/.test(s) && s.replace(/\D/g, '').length >= 6) || null;
  const side = (sp) => { let p = sp.parentElement; for (let k = 0; k < 6 && p; k++) { const t = (p.textContent || '').trim();
      if (/^(Buy\s*)?Up/i.test(t)) return 'up'; if (/^(Buy\s*)?Down/i.test(t)) return 'down'; p = p.parentElement } return null };
  let up = null, down = null; const loose = [];
  for (const e of document.querySelectorAll('span.pk-animated-label__text')) { if (!seen(e)) continue; const t = e.textContent.trim(); if (!/¢$/.test(t)) continue;
    const w = side(e); if (w === 'up') { if (!up) up = t } else if (w === 'down') { if (!down) down = t } else loose.push(t) }
  if (!up && !down && loose.length >= 2) { up = loose[0]; down = loose[1] }     // old page layout: first label is Up
  return { slug: location.pathname.split('/').pop(), range: ok(W.rng) ? W.rng.textContent.trim() : null, ptb, price,
           up, down, labels_mapped: (up || down) ? (loose.length >= 2 && !document.querySelector('x') ? 'by_position_or_text' : 'by_text') : 'none',
           mins: unit(W.mins), secs: unit(W.secs),
           dialog: document.querySelectorAll('[role=dialog]').length };
}"""
LIVEBTN_JS = ("() => [...document.querySelectorAll('button,a')].some(e => /go to live market/i.test(e.textContent||''))")
GOLIVE_JS = ("() => { const b = [...document.querySelectorAll('button,a')].find(e => /go to live market/i.test(e.textContent||''));"
             " if (b) { b.click(); return true } return false }")


def money(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except Exception:
        return None

def cents(x):
    try:
        return round(float(str(x).replace("¢", "").strip()) / 100.0, 4)
    except Exception:
        return None

def gap_bucket(abs_bps):
    lo = 0.0
    for e in GAP_EDGES:
        if abs_bps < e:
            return f"{lo:g}-{e:g}"
        lo = e
    return f"{GAP_EDGES[-1]:g}+"

def r4(x):
    return None if x is None else round(x, 4)

def r2(x):
    return None if x is None else round(x, 2)


class Feed:
    """Public Chainlink feed (the same one the page uses). Fallback + cross-check only."""
    def __init__(self, log):
        self.log = log
        self.spot = self.twap = None
        self.spot_t = self.twap_t = 0.0
        self.bell_twap = {}                                      # round_start -> first TWAP60 print at/after the bell

    def fresh(self, max_age=8):
        return self.twap is not None and now() - self.twap_t <= max_age

    async def run(self):
        import websockets
        backoff = 2
        while True:
            try:
                async with websockets.connect(RTDS, max_size=2**20) as ws:
                    # the server matches the filter string exactly: compact JSON or no ticks at all
                    await ws.send(json.dumps({"action": "subscribe", "subscriptions": [
                        {"topic": t, "type": "update", "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))}
                        for t in ("crypto_prices_chainlink", "crypto_prices_twap_sixty")]}))
                    self.log("feed connected"); backoff = 2
                    async def hb():
                        while True:
                            await asyncio.sleep(5); await ws.send("PING")
                    h = asyncio.create_task(hb())
                    try:
                        while True:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), 60)
                            except asyncio.TimeoutError:
                                self.log("feed silent 60 s - reconnecting"); break
                            try:
                                m = json.loads(raw)
                            except Exception:
                                continue
                            if not isinstance(m, dict) or m.get("type") != "update":
                                continue
                            p = m.get("payload") or {}
                            if p.get("symbol") != "btc/usd":
                                continue
                            try:
                                v = float(p["full_accuracy_value"]) / 1e18 if "full_accuracy_value" in p else float(p["value"])
                                ts = float(p["timestamp"]) / 1000.0
                            except Exception:
                                continue
                            if m.get("topic") == "crypto_prices_chainlink":
                                self.spot, self.spot_t = v, now()
                            else:
                                self.twap, self.twap_t = v, now()
                                st = int(ts // T) * T
                                if st not in self.bell_twap and ts - st <= 3:
                                    self.bell_twap[st] = v
                                    for k in [k for k in self.bell_twap if k < st - 3600]:
                                        self.bell_twap.pop(k, None)
                    finally:
                        h.cancel()
            except Exception as e:
                self.log(f"feed problem {e.__class__.__name__}: {str(e)[:80]}; retry in {backoff}s")
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)


class Trader:
    def __init__(self, seed=None):
        self.log = make_logger("ui_btc_trader")
        self.rng = random.Random(seed)
        self.st = load_json(STATE_PATH, None) or {
            "version": VERSION, "created_utc": iso(), "start_bankroll": START_BANKROLL, "cash": START_BANKROLL,
            "open": {}, "cells": {}, "settled_count": 0, "loss_streak": 0, "last_round": 0,
            "ui_bad_rounds": 0, "day_rows": {}, "last_summary_t": 0}
        self.feed = Feed(self.log)
        self.ui = UIBrowser(self.log)
        self.tg = Telegram("UI paper trader - BTC 5m", os.path.join(STATE_DIR, "btc_telegram.json"))
        self.ui_ready_for = None                                # round start the page currently shows
        self.ui_task = None
        self.tokens = {}                                        # round start -> {"Up": id, "Down": id}
        self.last_latency = None

    # ---------------------------------------------------------------- accounting
    def equity(self):
        return self.st["cash"] + sum(o.get("bet_usd") or 0.0 for o in self.st["open"].values())

    def save(self):
        save_json(STATE_PATH, self.st)

    def put_row(self, row):
        """Insert/replace the round's row in its day file (live view, rewritten atomically)."""
        day = row["round_start_utc"][:10]
        rows = self.st["day_rows"].setdefault(day, {})
        rows[str(row["round_start_unix"])] = row
        for d in sorted(self.st["day_rows"])[:-3]:              # keep 3 days in state
            self.st["day_rows"].pop(d, None)
        write_csv(os.path.join(BTC_DIR, f"btc_trades_{day}.csv"), COLS,
                  [rows[k] for k in sorted(rows, key=int)])

    def finalize_row(self, row):
        self.put_row(row)
        append_csv(ALL_CSV, COLS, row)
        self.write_daily()

    def write_daily(self):
        rows = read_csv(ALL_CSV)
        days = {}
        for r in rows:
            days.setdefault(r["round_start_utc"][:10], []).append(r)
        out = []
        for d in sorted(days):
            L = days[d]
            settled = [r for r in L if r["status"] == "SETTLED"]
            wins = sum(1 for r in settled if r["win"] == "1")
            pnl = sum(float(r["pnl_usd"] or 0) for r in settled)
            last_bank = next((r["bankroll_after"] for r in reversed(L) if r.get("bankroll_after")), "")
            today = d == utc_day()
            so_far = (int(now()) % 86400) // T if today else 288
            openn = sum(1 for o in self.st["open"].values() if o["round_start_utc"][:10] == d)
            out.append({"utc_day": d, "rounds_in_day_so_far": so_far, "rounds_recorded": len(L) + openn,
                        "bets_settled": len(settled), "bets_open": openn,
                        "missed": sum(1 for r in L if r["status"] == "MISSED"),
                        "no_liquidity": sum(1 for r in L if r["status"] == "NO_LIQUIDITY"),
                        "wins": wins, "losses": len(settled) - wins,
                        "win_rate": r4(wins / len(settled)) if settled else "",
                        "staked_usd": r2(sum(float(r["bet_usd"] or 0) for r in settled)), "pnl_usd": r2(pnl),
                        "bankroll_end": last_bank,
                        "ui_rows": sum(1 for r in L if r["data_source"] == "ui"),
                        "api_fallback_rows": sum(1 for r in L if r["data_source"].startswith("api_fallback")),
                        "ui_vs_official_disagreements": sum(1 for r in L if r["outcomes_agree"] == "0")})
        write_csv(DAILY_CSV, DAILY_COLS, out)

    def write_table(self):
        out = []
        for slot in ALL_SLOTS:
            lo = 0.0
            for b, ag in [(gap_bucket(x), a) for x in (0.0, 0.5, 1.0, 2.0, 4.0, 8.0) for a in ("A", "D", "U")]:
                c = self.st["cells"].get(f"{slot}|{b}|{ag}")
                if not c or not c["n"]:
                    continue
                wr = c["wins"] / c["n"]
                aL = c["sum_ask_l"] / c["n_ask_l"] if c["n_ask_l"] else None
                aD = c["sum_ask_d"] / c["n_ask_d"] if c["n_ask_d"] else None
                eL = wr - (aL + fee_per_share(FEE_RATE, aL)) if aL else None
                eD = (1 - wr) - (aD + fee_per_share(FEE_RATE, aD)) if aD else None
                best = max([x for x in (eL, eD) if x is not None], default=None)
                verdict = ("too few observations" if c["n"] < MIN_CELL_N else
                           "no edge after fee" if best is None or best < MIN_EDGE else
                           ("buy the leader" if best == eL else "buy the underdog"))
                out.append({"slot_s_remaining": slot, "gap_bucket_bps": b,
                            "page_vs_market": {"A": "page leader = market favourite", "D": "page leader is NOT the market favourite",
                                               "U": "no book to compare"}[ag], "observations": c["n"],
                            "leader_wins": c["wins"], "leader_win_rate": r4(wr), "avg_leader_ask": r4(aL),
                            "avg_underdog_ask": r4(aD), "edge_buy_leader": r4(eL), "edge_buy_underdog": r4(eD),
                            "verdict": verdict})
        write_csv(TABLE_CSV, TABLE_COLS, out)

    # ---------------------------------------------------------------- startup backfill
    def backfill_missed(self, current_start):
        last = self.st.get("last_round") or 0
        if not last:
            return
        first = max(last + T, current_start - 2 * 86400)
        n = 0
        for s in range(first, current_start, T):
            if str(s) in self.st["open"]:
                continue
            row = self.blank_row(s)
            row.update(status="MISSED", data_source="none", my_choice="", bet_usd=0, shares=0,
                       bankroll_before=r2(self.equity()), bankroll_after=r2(self.equity()),
                       notes="trader was not running during this round (restart or outage); no bet was possible")
            self.finalize_row(row); n += 1
        if n:
            self.log(f"backfilled {n} MISSED round(s) from the downtime")
            self.tg.send("btc_missed", f"⚠ BTC paper trader was down: {n} round(s) recorded as MISSED "
                                        f"({iso(first)} to {iso(current_start - T)}).")

    def blank_row(self, start):
        slug = f"btc-updown-5m-{start}"
        return {"round_start_unix": start, "round_start_utc": iso(start), "round_end_utc": iso(start + T),
                "slug": slug, "ui_url": f"{SITE}/event/{slug}", "code_version": VERSION,
                "browser_launches": self.ui.launches, "loss_streak_before": self.st["loss_streak"]}

    # ---------------------------------------------------------------- UI
    async def switch_ui(self, start):
        """Bring the page to round `start`. Never raises. Sets self.ui_ready_for when the slug matches."""
        slug = f"btc-updown-5m-{start}"
        current = lambda: now() < start + T - 20                # never work on the page for a round that is ending
        try:
            if self.ui.page is None or now() - self.ui.started > BROWSER_MAX_AGE_S:
                await self.ui.start()
            d, _ = await self.ui.js(READ_JS)
            if d and d.get("slug") == slug:
                self.ui_ready_for = start; return
            on_prev = bool(d) and d.get("slug") == f"btc-updown-5m-{start - T}"
            btn = False
            for _ in range(10 if on_prev else 1):               # only worth waiting when the page shows the round that just ended
                btn, _ = await self.ui.js(LIVEBTN_JS, 3)
                if btn:
                    break
                await asyncio.sleep(1.0)
            if btn:
                await self.ui.js(GOLIVE_JS, 4)
                for i in range(20):
                    await asyncio.sleep(1.0)
                    d, _ = await self.ui.js(READ_JS)
                    if d and d.get("slug") == slug:
                        self.ui_ready_for = start
                        self.log(f"UI switched in place by 'Go to live market' at +{now() - start:.0f}s"); return
                    if i in (6, 13):
                        await self.ui.js(GOLIVE_JS, 4)          # the click is sometimes swallowed while the page is busy
                self.log("in-place switch did not happen within 20 s; loading the round's address")
            for attempt in (1, 2):
                if not current():
                    return
                took = await self.ui.goto(f"{SITE}/event/{slug}")
                if took is not None:
                    for _ in range(12):
                        await asyncio.sleep(1.0)
                        await self.ui.dismiss_notice()
                        d, _ = await self.ui.js(READ_JS)
                        if d and d.get("slug") == slug and (d.get("ptb") or d.get("up")):
                            self.ui_ready_for = start
                            self.log(f"UI loaded by full page load ({took:.0f}s) at +{now() - start:.0f}s"); return
                if not current():
                    return
                self.log(f"UI load attempt {attempt} failed; restarting the browser")
                await self.ui.start()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log(f"switch_ui error {e.__class__.__name__}: {str(e)[:100]}")

    async def read_ui(self, start, timeout_s=5.5):
        """One reading of the page for round `start`, or None when the page is not usable."""
        if self.ui_ready_for != start:
            return None
        d, lat = await self.ui.js(READ_JS, timeout_s)
        if not d or d.get("slug") != f"btc-updown-5m-{start}":
            return None
        if d.get("dialog"):
            asyncio.create_task(self.ui.dismiss_notice())       # never make a checkpoint wait for the notice
        self.last_latency = lat
        cd = None
        if d.get("mins") is not None and d.get("secs") is not None and 0 <= d["secs"] < 60 and 0 <= d["mins"] <= 15:
            cd = d["mins"] * 60 + d["secs"]
        return {"range": d.get("range"), "ptb": money(d.get("ptb")), "price": money(d.get("price")),
                "up": cents(d.get("up")), "down": cents(d.get("down")), "countdown": cd,
                "latency_ms": int((lat or 0) * 1000), "read_t": now()}

    async def get_tokens(self, start):
        if start in self.tokens:
            return self.tokens[start]
        try:
            m = await ahttp_json(f"{GAMMA}/markets?slug=btc-updown-5m-{start}", timeout=6, retries=2)
            if m:
                t = dict(zip(arr(m[0]["outcomes"]), arr(m[0]["clobTokenIds"])))
                if "Up" in t and "Down" in t:
                    self.tokens[start] = t
                    for k in [k for k in self.tokens if k < start - 3600]:
                        self.tokens.pop(k, None)
                    return t
        except Exception as e:
            self.log(f"token lookup failed for {start}: {e.__class__.__name__}")
        return None

    async def books(self, start):
        t = await self.get_tokens(start)
        if not t:
            self.log(f"books: no market ids for round {start} yet (gamma lookup failed)")
            return {}
        async def one(side):
            for attempt in (1, 2):
                try:
                    return side, await asyncio.wait_for(asyncio.to_thread(best_bid_ask, t[side], 5), 6.5)
                except Exception as e:
                    err = f"{e.__class__.__name__}: {str(e)[:100]}"
            self.log(f"books: {side} book unreadable for round {start} ({err})")
            self.book_fails = getattr(self, "book_fails", 0) + 1
            if self.book_fails in (8, 60):                      # 4 checkpoints in a row, then again after ~half an hour
                self.tg.send("btc_books_down", f"⚠ the order book cannot be read ({err}). Bets continue at the price the page "
                                               f"shows and are marked page_label(book_unreadable). Failed reads in a row: {self.book_fails}")
            return side, None
        res = dict(await asyncio.gather(one("Up"), one("Down")))
        if any(res.values()):
            self.book_fails = 0
        out = {}
        for side, v in res.items():
            if v:
                out[side] = {"bid": v[0], "bid_size": v[1], "ask": v[2], "ask_size": v[3]}
        return out

    async def api_open_price(self, start):
        """The UI's own endpoint for the Price To Beat (full precision)."""
        try:
            # without the two twap options this endpoint returns the SPOT open (84504.99) while the page
            # displays the 60-second-average open (84526.23): verified 2026-09-27 on round 1790523300
            d = await ahttp_json(f"{SITE}/api/crypto/crypto-price?symbol=BTC&eventStartTime={iso(start)}"
                                 f"&variant=fiveminute&endDate={iso(start + T)}&twapEnabled=true&twapLookbackSeconds=60",
                                 timeout=6, retries=2)
            v = d.get("openPrice")
            return float(v) if v else None
        except Exception:
            return None

    async def accept_ptb(self, rnd, ui):
        """The Price To Beat shown by the page is accepted for a round only when it is really that round's.
        2026-09-27: right after an in-place switch the page showed the PREVIOUS round's value (84573.67 twice)."""
        if rnd.get("ptb_ui"):
            return True
        v = ui.get("ptb")
        if not v:
            return False
        if not rnd.get("ptb_api"):
            rnd["ptb_api"] = await self.api_open_price(rnd["start"])
        api = rnd.get("ptb_api")
        ok = (abs(v - api) <= 1.0) if api is not None else (v != getattr(self, "prev_ptb", None))
        if not ok:
            if not rnd.get("_stale_ptb_logged"):
                rnd["_stale_ptb_logged"] = True
                self.log(f"page still shows an old price to beat ({v}); waiting for this round's"
                         + (f" (the page's own data says {api:.2f})" if api is not None else ""))
            return False
        rnd.update(ptb_ui=v, ptb_t=iso(ui["read_t"], ms=True), ptb_cd=ui["countdown"], range=ui["range"])
        self.prev_ptb = v
        self.log(f"price to beat {v} seen at +{ui['read_t'] - rnd['start']:.0f}s (ui countdown {ui['countdown']})"
                 + (f", page data {api:.2f}" if api is not None else ", not cross-checked"))
        return True

    # ---------------------------------------------------------------- snapshot + decision
    async def snapshot(self, rnd, slot):
        start = rnd["start"]; end = start + T
        ui, bk = await asyncio.gather(self.read_ui(start), self.books(start))
        t = now(); rem = end - t
        src, note = "ui", ""
        ptb = cur = upc = dnc = cd = drift = lat = None
        if ui:
            ptb, cur, upc, dnc, cd, lat = ui["ptb"], ui["price"], ui["up"], ui["down"], ui["countdown"], ui["latency_ms"]
            if cd is not None:
                drift = round(cd - (end - ui["read_t"]), 1)
            if not await self.accept_ptb(rnd, ui):
                ptb = None                                      # stale or missing: this checkpoint falls back
            else:
                ptb = rnd["ptb_ui"]
            late = (drift is not None and abs(drift) > UI_STALE_DRIFT_S) or \
                   (drift is None and cur and self.feed.fresh() and abs(cur - self.feed.twap) > UI_STALE_PRICE_USD)
            if not (ptb and cur):
                src, note = "api_fallback(ui_incomplete)", "page showed no price or no price-to-beat at this checkpoint"
            elif late:
                src, note = "api_fallback(ui_late)", f"page was behind the clock (countdown drift {drift}s)"
        else:
            src, note = "api_fallback(ui_unavailable)", "page not ready for this round at this checkpoint"
        if src != "ui":
            if not rnd.get("ptb_api"):
                rnd["ptb_api"] = await self.api_open_price(start)
            ptb_f = rnd.get("ptb_api") or self.feed.bell_twap.get(start)
            cur_f = self.feed.twap if self.feed.fresh() else None
            if ptb_f and cur_f:
                ptb, cur = ptb_f, cur_f
            else:
                ptb = cur = None
                note += "; no fallback price either (feed down)"
        up, dn = bk.get("Up") or {}, bk.get("Down") or {}
        gap = (cur - ptb) if (cur is not None and ptb) else None
        bps = (gap / ptb * 1e4) if gap is not None else None
        mid = lambda b: ((b.get("bid") + b.get("ask")) / 2 if (b.get("bid") and b.get("ask")) else (b.get("ask") or b.get("bid")))
        mu, md = mid(up), mid(dn)
        if mu is None and md is not None: mu = 1 - md
        if md is None and mu is not None: md = 1 - mu
        fav = None if mu is None else ("Up" if mu >= md else "Down")
        lead = None if gap is None else ("Up" if gap >= 0 else "Down")
        agree = "U" if (fav is None or lead is None) else ("A" if fav == lead else "D")
        s = {"fav": fav, "agree": agree,
             "slot": slot, "t": t, "rem": round(rem, 1), "cd": cd, "drift": drift, "cur": cur, "ptb": ptb, "gap": gap,
             "bps": bps, "bucket": gap_bucket(abs(bps)) if bps is not None else None, "ui_up": upc, "ui_down": dnc,
             "twap": self.feed.twap if self.feed.fresh() else None, "spot": self.feed.spot,
             "up_bid": up.get("bid"), "up_ask": up.get("ask"), "up_ask_size": up.get("ask_size"),
             "dn_bid": dn.get("bid"), "dn_ask": dn.get("ask"), "dn_ask_size": dn.get("ask_size"),
             "leader": lead, "src": src, "lat": lat, "note": note}
        rnd["snaps"].append(s)
        return s

    def plan(self):
        learn = self.st["settled_count"] < LEARN_ROUNDS
        explore = learn or self.rng.random() < 0.10
        return {"phase": "LEARN" if learn else "ADAPT", "explore": explore,
                "slot": self.rng.choice(ENTRY_SLOTS), "side_mode": "leader" if self.rng.random() < 0.75 else "underdog"}

    def decide(self, rnd, s):
        """Returns a bet dict or None. Exactly one bet per round."""
        slot = s["slot"]; plan = rnd["plan"]
        forced = slot <= FORCE_SLOT
        if s["leader"] is None:
            return None
        L = s["leader"]; D = "Down" if L == "Up" else "Up"
        ask = {"Up": s["up_ask"], "Down": s["dn_ask"]}; bid = {"Up": s["up_bid"], "Down": s["dn_bid"]}
        size = {"Up": s["up_ask_size"], "Down": s["dn_ask_size"]}
        ui_px = {"Up": s["ui_up"], "Down": s["ui_down"]}
        px_src = "order_book_ask"
        if not any(bid.values()) and not any(ask.values()) and forced and any(ui_px.values()):
            # the order book could not be read at all: use what the page shows rather than skip the round
            ask = dict(ui_px); px_src = "page_label(book_unreadable)"
        if ask[L] and bid[L]:
            prior = (ask[L] + bid[L]) / 2
        else:
            prior = ask[L] or ui_px[L] or (1 - ask[D] if ask[D] else 0.5)
        prior = min(0.98, max(0.02, prior))
        c = self.st["cells"].get(f"{slot}|{s['bucket']}|{s.get('agree', 'U')}") or {"n": 0, "wins": 0}
        p_hat = (c["wins"] + PRIOR_W * prior) / (c["n"] + PRIOR_W)
        cost = lambda a: a + fee_per_share(FEE_RATE, a)
        ev = {L: (p_hat - cost(ask[L])) if ask[L] else None, D: ((1 - p_hat) - cost(ask[D])) if ask[D] else None}
        avail = [x for x in (L, D) if ask[x] and ASK_MIN <= ask[x] <= ASK_MAX]
        if not avail or slot < LATE_ENTRY_MIN_SLOT:
            if slot == SHADOW_SLOTS[-1]:
                return {"none": True, "reason": f"nothing buyable between {ASK_MIN:.2f} and {ASK_MAX:.2f} at any checkpoint reached"}
            return None
        deciding = max(ask[x] for x in avail) >= 0.90 or len(avail) == 1
        if plan["explore"]:
            early = slot > plan["slot"] and deciding               # waiting longer would leave nothing to buy
            if slot != plan["slot"] and not forced and not early:
                return None
            want = L if plan["side_mode"] == "leader" else D
            side = want if want in avail else avail[0]
            frac = BASE_FRAC
            why = (f"{plan['phase']}: exploring entry at {plan['slot']}s left, buying the "
                   f"{'leader' if side == L else 'underdog'}"
                   + ("" if slot == plan["slot"] else
                      f" (entered early at {slot}s: the round was being decided)" if slot > plan["slot"] else
                      f" (planned checkpoint was missed, entered at {slot}s)")
                   + ("" if side == want else " (planned side had no ask)"))
        else:
            best = max(avail, key=lambda x: ev[x])
            if ev[best] >= MIN_EDGE and c["n"] >= MIN_CELL_N:
                side = best
                kelly = ev[best] / max(1e-6, 1 - cost(ask[best]))
                frac = min(MAX_FRAC, max(MIN_FRAC, 0.5 * kelly))
                why = (f"ADAPT: cell ({slot}s left, gap {s['bucket']} bps, {c['n']} rounds seen) shows "
                       f"{ev[best] * 100:+.1f}c edge buying the {'leader' if side == L else 'underdog'}; half-Kelly stake")
            elif forced or deciding:
                side, frac = best, MIN_FRAC
                why = ("ADAPT: no checkpoint showed an edge of 2c or more; minimum bet "
                       + ("now, because the round is being decided" if not forced else "at the last entry checkpoint")
                       + " (every round is traded)")
            else:
                return None
        scale = []
        if self.st["loss_streak"] >= 5:
            frac *= 0.5; scale.append(f"halved after {self.st['loss_streak']} losses in a row")
        eq = self.equity()
        if eq < 0.6 * self.st["start_bankroll"]:
            frac *= 0.5; scale.append("halved: bankroll under 60% of start")
        px = ask[side]; f = fee_per_share(FEE_RATE, px); cps = px + f
        stake = eq * frac; shares = stake / cps
        if shares < MIN_SHARES:
            shares = MIN_SHARES; scale.append("raised to the 5-share minimum")
        if size[side] and shares > size[side]:
            shares = max(MIN_SHARES, size[side]); scale.append("capped by the size at the best ask")
        stake = shares * cps
        if stake > self.st["cash"]:
            return {"none": True, "reason": f"not enough cash for the minimum bet (cash ${self.st['cash']:.2f})"}
        return {"px_src": px_src, "side": side, "px": px, "fee": f, "cps": cps, "stake": stake, "shares": shares, "p_hat": p_hat if side == L else 1 - p_hat,
                "ev": ev[side], "ui_px": ui_px[side], "why": why + ("; " + "; ".join(scale) if scale else ""),
                "phase": plan["phase"] + ("/explore" if plan["explore"] and plan["phase"] == "ADAPT" else "")}

    def book_bet(self, rnd, s, b):
        start = rnd["start"]
        row = self.blank_row(start)
        row.update({
            "ui_title_range": rnd.get("range"), "status": "OPEN",
            "price_to_beat_ui": rnd.get("ptb_ui"), "price_to_beat_captured_utc": rnd.get("ptb_t"),
            "price_to_beat_ui_countdown_s": rnd.get("ptb_cd"), "price_to_beat_api_full": rnd.get("ptb_api"),
            "entry_slot_s_remaining": s["slot"], "decision_utc": iso(s["t"], ms=True),
            "decision_clock_remaining_s": s["rem"], "decision_ui_countdown_s": s["cd"], "ui_clock_drift_s": s["drift"],
            "ui_current_price": r2(s["cur"]), "gap_usd": r2(s["gap"]), "gap_bps": r4(s["bps"]), "gap_bucket": s["bucket"],
            "ui_up_cents": s["ui_up"], "ui_down_cents": s["ui_down"], "api_twap60": r2(s["twap"]),
            "ui_price_minus_twap": r2(s["cur"] - s["twap"]) if (s["cur"] and s["twap"]) else None,
            "book_up_bid": s["up_bid"], "book_up_ask": s["up_ask"], "book_down_bid": s["dn_bid"], "book_down_ask": s["dn_ask"],
            "data_source": s["src"], "leader_side": s["leader"], "market_favourite_side": s.get("fav"),
            "page_leader_agrees_with_market": {"A": "yes", "D": "no"}.get(s.get("agree"), ""),
            "ui_read_latency_ms": s["lat"], "bankroll_before": r2(self.equity()), "notes": s["note"]})
        if b.get("none"):
            row.update(status="NO_LIQUIDITY", my_choice="", bet_usd=0, shares=0, choice_reason=b["reason"],
                       phase=rnd["plan"]["phase"])
        else:
            row.update({"exec_price_source": b["px_src"],
                        "phase": b["phase"], "my_choice": b["side"].upper(), "choice_reason": b["why"],
                        "entry_price_exec": r4(b["px"]), "entry_price_ui": b["ui_px"], "fee_per_share": r4(b["fee"]),
                        "cost_per_share": r4(b["cps"]), "est_win_prob": r4(b["p_hat"]), "est_edge_per_share": r4(b["ev"]),
                        "bet_usd": r2(b["stake"]), "shares": r4(b["shares"])})
            self.st["cash"] -= b["stake"]
        rnd["row"] = row
        self.st["open"][str(start)] = row
        self.put_row(row); self.save()
        self.log(f"BET {row['slug']} my_choice={row['my_choice'] or '-'} ${row['bet_usd']} at {s['slot']}s left "
                 f"(ui countdown {s['cd']}, source {s['src']}) gap {s['bps'] if s['bps'] is None else round(s['bps'], 2)} bps "
                 f"px {row.get('entry_price_exec')} | {row['choice_reason']}")

    # ---------------------------------------------------------------- live view for the dashboard
    async def live_tick(self, rnd, done):
        """Publish the current view. Never raises, never delays a checkpoint, never feeds a decision."""
        try:
            start = rnd["start"]; end = start + T; t = now(); rem = end - t
            ahead = [sl for sl in ALL_SLOTS if sl not in done and sl < rem]
            if ahead and rem - max(ahead) < 2.5:                # a checkpoint is about to fire: stay out of its way
                return
            if t - getattr(self, "_live_t", 0) < LIVE_EVERY_S:
                return
            self._live_t = t
            ui = await self.read_ui(start, 2.0)
            if ui and ui.get("ptb") and not rnd.get("ptb_ui"):
                await self.accept_ptb(rnd, ui)
            last = rnd["snaps"][-1] if rnd["snaps"] else {}
            row = rnd.get("row") or {}
            cur = (ui or {}).get("price"); ptb = rnd.get("ptb_ui")
            live = {
                "written_utc": iso(now(), ms=True), "written_unix": round(now(), 2), "version": VERSION,
                "round_start_unix": start, "round_start_utc": iso(start), "round_end_utc": iso(end),
                "slug": f"btc-updown-5m-{start}", "ui_url": f"{SITE}/event/btc-updown-5m-{start}",
                "clock_remaining_s": round(end - now(), 1), "page_ready": self.ui_ready_for == start,
                "page": None if not ui else {
                    "title_range": ui.get("range"), "price_to_beat": ptb, "price_to_beat_shown": ui.get("ptb"),
                    "current_price": cur, "up_cents": ui.get("up"), "down_cents": ui.get("down"),
                    "countdown_s": ui.get("countdown"), "read_latency_ms": ui.get("latency_ms"),
                    "behind_clock_s": (None if ui.get("countdown") is None else round(ui["countdown"] - (end - ui["read_t"]), 1)),
                    "gap_usd": (None if not (cur and ptb) else round(cur - ptb, 2)),
                    "gap_bps": (None if not (cur and ptb) else round((cur - ptb) / ptb * 1e4, 2))},
                "feed": {"twap60": r2(self.feed.twap) if self.feed.fresh() else None, "spot": r2(self.feed.spot)},
                "book": {"up_bid": last.get("up_bid"), "up_ask": last.get("up_ask"), "down_bid": last.get("dn_bid"),
                         "down_ask": last.get("dn_ask"), "as_of_slot_s": last.get("slot"),
                         "market_favourite": last.get("fav")},
                "plan": {"phase": rnd["plan"]["phase"], "exploring": rnd["plan"]["explore"],
                         "planned_slot_s": rnd["plan"]["slot"] if rnd["plan"]["explore"] else None},
                "checkpoints_done": sorted(done, reverse=True),
                "checkpoints_left": sorted([sl for sl in ALL_SLOTS if sl not in done], reverse=True),
                "bet": None if not row else {k: row.get(k) for k in (
                    "status", "my_choice", "bet_usd", "shares", "entry_price_exec", "entry_price_ui", "entry_slot_s_remaining",
                    "decision_utc", "choice_reason", "est_win_prob", "est_edge_per_share", "data_source", "exec_price_source")},
                "bankroll": r2(self.equity()), "cash": r2(self.st["cash"]), "start_bankroll": self.st["start_bankroll"],
                "settled_count": self.st["settled_count"], "learn_rounds": LEARN_ROUNDS, "loss_streak": self.st["loss_streak"],
                "open_rounds": len(self.st["open"]), "browser_launches": self.ui.launches,
                "browser_age_min": round((now() - self.ui.started) / 60, 1) if self.ui.started else None}
            save_json(LIVE_PATH, live)
            if self.ui_ready_for == start and self.ui.page is not None and t - getattr(self, "_shot_t", 0) >= SHOT_EVERY_S:
                self._shot_t = t
                tmp = SHOT_PATH + ".tmp.jpg"
                await asyncio.wait_for(self.ui.page.screenshot(path=tmp, type="jpeg", quality=55,
                                       clip={"x": 0, "y": 0, "width": 1440, "height": 900}), 3)
                os.replace(tmp, SHOT_PATH)
        except Exception as e:
            if now() - getattr(self, "_live_err_t", 0) > 600:    # at most one line per 10 min
                self._live_err_t = now()
                self.log(f"live view not written ({e.__class__.__name__}); trading is not affected")

    # ---------------------------------------------------------------- one round
    async def run_round(self, start):
        end = start + T
        rnd = {"start": start, "snaps": [], "plan": self.plan(), "row": None}
        self.rounds[start] = rnd
        # 2026-09-27 (first live run): the previous round's switch task was still running, failed on its
        # old slug and restarted the browser under the new round. One switch task at a time.
        if self.ui_task and not self.ui_task.done():
            self.ui_task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(self.ui_task, return_exceptions=True), 5)
            except Exception:
                pass
        self.ui_task = asyncio.create_task(self.switch_ui(start))
        asyncio.create_task(self.get_tokens(start))
        done = set()
        while now() < end - 2:
            rem = end - now()
            due = [sl for sl in ALL_SLOTS if sl not in done and rem <= sl]
            for sl in due:
                done.add(sl)
                if rem < sl - SLOT_GRACE_S:
                    continue                                    # reached too late (started mid-round / stall)
                try:
                    s = await asyncio.wait_for(self.snapshot(rnd, sl), 13.5)   # checkpoints are 15 s apart at the closest
                except Exception as e:
                    self.log(f"snapshot {sl}s failed: {e.__class__.__name__}"); continue
                if rnd["row"] is None:
                    b = self.decide(rnd, s)
                    if b:
                        self.book_bet(rnd, s, b)
            if 60 in done and (start + T) not in self.tokens and not rnd.get("_next_ids"):
                rnd["_next_ids"] = True                         # gamma lists the next round before it opens
                asyncio.create_task(self.get_tokens(start + T))
            if not due and not rnd.get("ptb_ui") and self.ui_ready_for == start:
                ui = await self.read_ui(start)                  # catch the Price To Beat as soon as it appears
                if ui and ui["ptb"]:
                    await self.accept_ptb(rnd, ui)
            if not due:
                await self.live_tick(rnd, done)
            await asyncio.sleep(0.5)
        if rnd["row"] is None:                                  # nothing could be decided at any checkpoint
            last = rnd["snaps"][-1] if rnd["snaps"] else {"slot": None, "t": now(), "rem": 0, "cd": None, "drift": None,
                    "cur": None, "ptb": None, "gap": None, "bps": None, "bucket": None, "ui_up": None, "ui_down": None,
                    "twap": None, "spot": None, "up_bid": None, "up_ask": None, "dn_bid": None, "dn_ask": None,
                    "leader": None, "src": "none", "lat": None, "note": ""}
            why = ("no checkpoint was reached (trader started mid-round)" if not rnd["snaps"] else
                   "no usable price at any checkpoint (page and fallback feed both unavailable)")
            self.book_bet(rnd, last, {"none": True, "reason": why})
            rnd["row"]["status"] = "MISSED" if not rnd["snaps"] else "NO_LIQUIDITY"
            self.put_row(rnd["row"])
        # UI health bookkeeping
        if any(s["src"] == "ui" for s in rnd["snaps"]):
            self.st["ui_bad_rounds"] = 0
        elif rnd["snaps"]:
            self.st["ui_bad_rounds"] += 1
            if self.st["ui_bad_rounds"] in (3, 12):
                self.tg.send("btc_ui_down", f"⚠ the page has been unreadable for {self.st['ui_bad_rounds']} rounds in a row; "
                                            f"bets continue from the fallback feed and are marked api_fallback. Browser is being restarted.")
            if self.st["ui_bad_rounds"] % 3 == 0:
                self.ui_ready_for = None
                try:
                    await self.ui.start()
                except Exception as e:
                    self.log(f"browser restart failed: {e.__class__.__name__}")
        rnd["row"]["_snaps"] = rnd["snaps"]
        self.st["open"][str(start)] = rnd["row"]
        self.st["last_round"] = start
        self.save()

    # ---------------------------------------------------------------- settlement
    async def ui_outcome(self, start):
        """What the page's 'Past' strip shows for the round (its own endpoint). Known to differ from the
        official result on thin rounds, which is exactly why both are recorded."""
        slug = f"btc-updown-5m-{start}"
        out = close = None
        try:
            d = await ahttp_json(f"{SITE}/api/past-results?includeOutcomesBySlug=true&outcomesOnly=true"
                                 f"&pastEventSlugs={slug}", timeout=8, retries=2)
            out = ((d.get("data") or {}).get("outcomesBySlug") or {}).get(slug)
        except Exception:
            pass
        try:
            d = await ahttp_json(f"{SITE}/api/past-results?symbol=BTC&variant=fiveminute&assetType=crypto"
                                 f"&currentEventStartTime={iso(start + 2 * T)}", timeout=8, retries=1)
            for r in (d.get("data") or {}).get("results", []):
                if str(r.get("startTime", ""))[:19] == iso(start)[:19]:
                    close = r.get("closePrice"); out = out or r.get("outcome")
        except Exception:
            pass
        return (out.capitalize() if out else None), close

    async def settle_loop(self):
        while True:
            try:
                for k in sorted(self.st["open"], key=int):
                    row = self.st["open"][k]; start = int(k)
                    age = now() - (start + T)
                    if age < 40:
                        continue
                    tries = row.get("_tries", 0)
                    if age > 900 and tries % 10:                # slow down after 15 min: every ~5 min
                        row["_tries"] = tries + 1; continue
                    row["_tries"] = tries + 1
                    ev = await asyncio.to_thread(gamma_market, row["slug"])
                    win = official_winner(ev["markets"][0]) if ev else None
                    if win is None:
                        if age > 86400:
                            row.update(status="UNRESOLVED", notes=(row.get("notes") or "") + "; no official result after 24 h")
                            if row.get("bet_usd"):
                                self.st["cash"] += row["bet_usd"]     # stake returned, P&L 0
                            self.finish(k, row)
                        continue
                    uo, close = await self.ui_outcome(start)
                    self.apply_result(k, row, win, uo, close)
            except Exception as e:
                self.log(f"settle loop error {e.__class__.__name__}: {str(e)[:100]}")
            await asyncio.sleep(20)

    def apply_result(self, k, row, win, uo, close):
        snaps = row.get("_snaps") or []
        for s in snaps:                                         # pattern table: every checkpoint of every round
            if s.get("leader") and s.get("bucket"):
                c = self.st["cells"].setdefault(f"{s['slot']}|{s['bucket']}|{s.get('agree', 'U')}",
                        {"n": 0, "wins": 0, "sum_ask_l": 0.0, "n_ask_l": 0, "sum_ask_d": 0.0, "n_ask_d": 0})
                c["n"] += 1; c["wins"] += int(s["leader"] == win)
                aL = s["up_ask"] if s["leader"] == "Up" else s["dn_ask"]
                aD = s["dn_ask"] if s["leader"] == "Up" else s["up_ask"]
                if aL: c["sum_ask_l"] += aL; c["n_ask_l"] += 1
                if aD: c["sum_ask_d"] += aD; c["n_ask_d"] += 1
            append_csv(SNAP_CSV, SNAP_COLS, {
                "round_start_unix": row["round_start_unix"], "slug": row["slug"], "slot_s_remaining": s["slot"],
                "snap_utc": iso(s["t"], ms=True), "clock_remaining_s": s["rem"], "ui_countdown_s": s["cd"],
                "ui_clock_drift_s": s["drift"], "ui_current_price": r2(s["cur"]), "price_to_beat": r2(s["ptb"]),
                "gap_bps": r4(s["bps"]), "gap_bucket": s["bucket"], "ui_up_cents": s["ui_up"], "ui_down_cents": s["ui_down"],
                "api_twap60": r2(s["twap"]), "api_spot": r2(s["spot"]), "book_up_bid": s["up_bid"], "book_up_ask": s["up_ask"],
                "book_down_bid": s["dn_bid"], "book_down_ask": s["dn_ask"], "leader_side": s["leader"],
                "market_favourite_side": s.get("fav"),
                "page_leader_agrees_with_market": {"A": "yes", "D": "no"}.get(s.get("agree"), ""),
                "data_source": s["src"], "ui_read_latency_ms": s["lat"], "outcome_official": win,
                "leader_won": "" if not s.get("leader") else int(s["leader"] == win)})
        row.update(outcome_official=win.upper(), outcome_ui=(uo or "").upper() or None,
                   outcomes_agree=("" if not uo else int(uo == win)), close_price_ui_api=close, settled_utc=iso())
        if row["status"] == "OPEN" and row.get("bet_usd"):
            won = row["my_choice"] == win.upper()
            payout = row["shares"] if won else 0.0
            pnl = payout - row["bet_usd"]
            self.st["cash"] += payout
            ui_px = row.get("entry_price_ui")
            pnl_ui = None
            if ui_px:
                cps_ui = ui_px + fee_per_share(FEE_RATE, ui_px)
                sh_ui = row["bet_usd"] / cps_ui
                pnl_ui = (sh_ui if won else 0.0) - row["bet_usd"]
            self.st["loss_streak"] = 0 if won else self.st["loss_streak"] + 1
            self.st["settled_count"] += 1
            row.update(status="SETTLED", win=int(won), pnl_usd=r2(pnl), pnl_if_ui_price_usd=r2(pnl_ui))
            self.log(f"SETTLED {row['slug']} my_choice={row['my_choice']} official={win.upper()} ui={row['outcome_ui']} "
                     f"{'WIN' if won else 'LOSS'} pnl ${pnl:+.2f}")
        self.finish(k, row)

    def finish(self, k, row):
        self.st["open"].pop(k, None)
        row.pop("_snaps", None); row.pop("_tries", None)
        row["bankroll_after"] = r2(self.equity())
        self.finalize_row(row)
        self.write_table()
        self.save()
        if self.st["settled_count"] == LEARN_ROUNDS and row["status"] == "SETTLED":
            self.tg.send("btc_adapt", f"learning phase finished ({LEARN_ROUNDS} settled rounds). From now on stakes follow the pattern table.")

    async def summary_loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                if now() - self.st.get("last_summary_t", 0) < 6 * 3600:
                    continue
                rows = [r for r in read_csv(ALL_CSV) if now() - int(r["round_start_unix"]) < 6 * 3600]
                if len(rows) < 6:
                    continue
                st = [r for r in rows if r["status"] == "SETTLED"]
                w = sum(1 for r in st if r["win"] == "1"); pnl = sum(float(r["pnl_usd"] or 0) for r in st)
                fb = sum(1 for r in rows if r["data_source"].startswith("api_fallback"))
                self.st["last_summary_t"] = now(); self.save()
                self.tg.send(f"btc_sum_{int(now() // 21600)}",
                    "PAPER bets on every BTC 5-minute round, read from the live Polymarket page. No real money.\n"
                    f"last 6 h: {len(rows)} rounds recorded, {len(st)} settled, {w} won / {len(st) - w} lost, "
                    f"P&L ${pnl:+.2f}\nbankroll ${self.equity():.2f} (start ${self.st['start_bankroll']:.0f}) | "
                    f"phase {'LEARN' if self.st['settled_count'] < LEARN_ROUNDS else 'ADAPT'} "
                    f"({self.st['settled_count']} settled in total)\n"
                    f"read from the page: {len(rows) - fb} rounds, from the fallback feed: {fb} | "
                    f"missed: {sum(1 for r in rows if r['status'] == 'MISSED')}", dedup=False)
            except Exception as e:
                self.log(f"summary error {e.__class__.__name__}")

    # ---------------------------------------------------------------- main
    async def main(self, minutes=None):
        self.rounds = {}
        stop = now() + minutes * 60 if minutes else None
        self.log(f"UI BTC paper trader v{VERSION} starting; bankroll ${self.equity():.2f}, "
                 f"{self.st['settled_count']} rounds settled so far, {len(self.st['open'])} open")
        tasks = [asyncio.create_task(self.feed.run()), asyncio.create_task(self.settle_loop()),
                 asyncio.create_task(self.summary_loop())]
        cur = int(now() // T) * T
        self.backfill_missed(cur)
        if str(cur) in self.st["open"]:
            # restarted in the middle of a round that already has its bet: keep it, do not bet again
            self.rounds[cur] = {"start": cur, "resumed": True}
            self.log(f"round {cur} already has a row from before the restart; not betting on it again")
        try:
            await self.ui.start()
        except Exception as e:
            self.log(f"browser did not start ({e.__class__.__name__}); continuing on the fallback feed")
        try:
            while stop is None or now() < stop:
                start = int(now() // T) * T
                if start in self.rounds:                        # round already handled: wait for the bell
                    await asyncio.sleep(min(1.0, max(0.05, start + T - now())))
                    continue
                try:
                    await self.run_round(start)
                except Exception as e:
                    self.log(f"round {start} error {e.__class__.__name__}: {str(e)[:160]}")
                    await asyncio.sleep(1)
                for k in [k for k in self.rounds if k < start - 1800]:
                    self.rounds.pop(k, None)
            if stop:                                            # bounded run: give settlement time to finish
                self.log("bounded run over; waiting for open rounds to settle (max 8 min)")
                t_end = now() + 480
                while self.st["open"] and now() < t_end:
                    await asyncio.sleep(5)
        finally:
            for t in tasks:
                t.cancel()
            await self.ui.stop()
            self.save()
            self.log(f"stopped; bankroll ${self.equity():.2f}, open rounds {len(self.st['open'])}")


def selftest():
    """Offline checks of sizing, decision and settlement arithmetic (no network, no browser)."""
    import tempfile, uicommon
    global STATE_PATH, ALL_CSV, SNAP_CSV, TABLE_CSV, DAILY_CSV, BTC_DIR
    d = tempfile.mkdtemp(); BTC_DIR = d
    STATE_PATH, ALL_CSV, SNAP_CSV, TABLE_CSV, DAILY_CSV = (os.path.join(d, n) for n in
        ("st.json", "all.csv", "snap.csv", "table.csv", "daily.csv"))
    t = Trader(seed=7); t.tg.send = lambda *a, **k: False
    t.log = print                                               # a self-test must not write into the live log file
    assert gap_bucket(0.2) == "0-0.5" and gap_bucket(3) == "2-4" and gap_bucket(50) == "8+"
    assert money("$84,984.95") == 84984.95 and cents("47¢") == 0.47 and cents("0.3¢") == 0.003
    start = 1790518500
    def snap(slot, cur, ptb, ua, da, src="ui"):
        g = cur - ptb
        fav = None if (ua is None and da is None) else ("Up" if (ua if ua is not None else 1 - da) >= (da if da is not None else 1 - ua) else "Down")
        lead = "Up" if g >= 0 else "Down"
        return {"fav": fav, "agree": "U" if fav is None else ("A" if fav == lead else "D"),
                "slot": slot, "t": now(), "rem": slot, "cd": slot, "drift": 0.0, "cur": cur, "ptb": ptb, "gap": g,
                "bps": g / ptb * 1e4, "bucket": gap_bucket(abs(g / ptb * 1e4)), "ui_up": 0.6, "ui_down": 0.41, "twap": cur,
                "spot": cur, "up_bid": ua - 0.01 if ua else None, "up_ask": ua, "up_ask_size": 500, "dn_bid": da - 0.01 if da else None,
                "dn_ask": da, "dn_ask_size": 500, "leader": "Up" if g >= 0 else "Down", "src": src, "lat": 300, "note": ""}
    # 1) LEARN: bet only at the planned checkpoint, base stake, 5-share minimum respected
    rnd = {"start": start, "snaps": [], "plan": {"phase": "LEARN", "explore": True, "slot": 180, "side_mode": "leader"}, "row": None}
    assert t.decide(rnd, snap(240, 85010, 85000, 0.60, 0.41)) is None
    b = t.decide(rnd, snap(180, 85010, 85000, 0.60, 0.41))
    assert b["side"] == "Up" and abs(b["stake"] - 200 * BASE_FRAC) < 1e-6, b
    assert abs(b["cps"] - (0.60 + 0.07 * 0.6 * 0.4)) < 1e-9 and abs(b["shares"] - 5.0 / b["cps"]) < 1e-9
    s = snap(180, 85010, 85000, 0.60, 0.41); rnd["snaps"].append(s); t.book_bet(rnd, s, b)
    assert abs(t.st["cash"] - 195.0) < 1e-6 and abs(t.equity() - 200.0) < 0.01
    row = t.st["open"][str(start)]; row["_snaps"] = rnd["snaps"]
    t.apply_result(str(start), row, "Up", "Up", 85020.0)
    sh = 5.0 / b["cps"]
    assert abs(t.equity() - (200 - 5 + sh)) < 0.01, t.equity()
    r = read_csv(ALL_CSV)[0]
    assert r["status"] == "SETTLED" and r["my_choice"] == "UP" and r["win"] == "1" and r["outcome_official"] == "UP", r
    assert abs(float(r["pnl_usd"]) - round(sh - 5, 2)) < 0.011
    c = t.st["cells"]["180|1-2|A"]; assert c["n"] == 1 and c["wins"] == 1
    # 2) a loss, then the forced bet when the planned checkpoint was missed
    start2 = start + 300
    rnd = {"start": start2, "snaps": [], "plan": {"phase": "LEARN", "explore": True, "slot": 240, "side_mode": "underdog"}, "row": None}
    b = t.decide(rnd, snap(75, 84990, 85000, 0.30, 0.71))      # leader = Down, underdog = Up
    assert b and b["side"] == "Up" and "missed" in b["why"], b
    s = snap(75, 84990, 85000, 0.30, 0.71); rnd["snaps"].append(s); eq0 = t.equity(); t.book_bet(rnd, s, b)
    row = t.st["open"][str(start2)]; row["_snaps"] = rnd["snaps"]; t.apply_result(str(start2), row, "Down", "Up", 84980.0)
    r = read_csv(ALL_CSV)[1]
    assert r["win"] == "0" and r["outcomes_agree"] == "0" and abs(t.equity() - (eq0 - b["stake"])) < 0.01 and t.st["loss_streak"] == 1
    # 3) ADAPT: a cell with a real edge is bought with a half-Kelly stake; no edge -> wait, then forced minimum
    t.st["settled_count"] = LEARN_ROUNDS
    t.st["cells"]["120|2-4|A"] = {"n": 200, "wins": 190, "sum_ask_l": 160.0, "n_ask_l": 200, "sum_ask_d": 44.0, "n_ask_d": 200}
    rnd = {"start": start2 + 300, "snaps": [], "plan": {"phase": "ADAPT", "explore": False, "slot": 0, "side_mode": "leader"}, "row": None}
    b = t.decide(rnd, snap(120, 85025.5, 85000, 0.80, 0.21))
    assert b and b["side"] == "Up" and b["ev"] > MIN_EDGE and MIN_FRAC * t.equity() <= b["stake"] <= MAX_FRAC * t.equity() + 1e-6, b
    assert t.decide(rnd, snap(150, 85001, 85000, 0.52, 0.49)) is None
    b = t.decide(rnd, snap(75, 85001, 85000, 0.52, 0.49))
    assert b and "last entry checkpoint" in b["why"] and b["shares"] >= MIN_SHARES, b
    # 4) one-sided book: the side with an ask is taken; no ask anywhere -> no bet is invented
    b = t.decide({"plan": {"phase": "LEARN", "explore": True, "slot": 90, "side_mode": "leader"}}, snap(90, 85100, 85000, None, 0.02))
    assert b and b["side"] == "Down" and "no ask" in b["why"], b
    P = {"plan": {"phase": "LEARN", "explore": True, "slot": 90, "side_mode": "leader"}}
    assert t.decide(P, snap(90, 85100, 85000, 0.999, 0.001)) is None            # lottery ticket / sure thing refused
    assert t.decide(P, snap(45, 85100, 85000, 0.90, 0.11))["side"] == "Up"      # late entry allowed at 45 s
    assert t.decide(P, snap(15, 85100, 85000, 0.90, 0.11)).get("none")          # never at 15 s
    assert t.decide({"plan": {"phase": "LEARN", "explore": True, "slot": 90, "side_mode": "leader"}}, snap(90, 85100, 85000, None, None)) is None
    b = t.decide({"plan": {"phase": "LEARN", "explore": True, "slot": 90, "side_mode": "leader"}}, snap(15, 85100, 85000, None, None))
    assert b and b.get("none"), b
    # 4b) early entry when the round is being decided; page price when the book is unreadable at the last checkpoint
    P2 = {"plan": {"phase": "LEARN", "explore": True, "slot": 90, "side_mode": "leader"}}
    b = t.decide(P2, snap(180, 85060, 85000, 0.93, 0.08)); assert b and "entered early" in b["why"] and b["px_src"] == "order_book_ask", b
    assert t.decide(P2, snap(180, 85010, 85000, 0.60, 0.41)) is None
    b = t.decide(P2, snap(75, 85010, 85000, None, None)); assert b and b["px_src"].startswith("page_label") and b["px"] == 0.6, b
    sD = snap(240, 85010, 85000, 0.18, 0.84); assert sD["agree"] == "D" and sD["fav"] == "Down" and sD["leader"] == "Up"
    # 5) loss-streak scaling and the missed-round backfill
    t.st["loss_streak"] = 5
    b5 = t.decide({"plan": {"phase": "LEARN", "explore": True, "slot": 180, "side_mode": "leader"}}, snap(180, 85010, 85000, 0.10, 0.91))
    assert "halved" in b5["why"], b5
    t.st["last_round"] = start2; n0 = len(read_csv(ALL_CSV)); t.backfill_missed(start2 + 4 * 300)
    rows = read_csv(ALL_CSV); assert len(rows) == n0 + 3 and rows[-1]["status"] == "MISSED", (n0, len(rows))
    assert os.path.exists(TABLE_CSV) and os.path.exists(DAILY_CSV) and os.path.exists(SNAP_CSV)
    print("SELFTEST OK - rows:", len(rows), "| equity", round(t.equity(), 2), "| daily:", read_csv(DAILY_CSV)[0])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=None, help="bounded run (verification)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    try:
        asyncio.run(Trader().main(a.minutes))
    except KeyboardInterrupt:
        pass
