#!/usr/bin/env python3
"""
ui_weather_trader.py - PAPER trader for Polymarket "Highest temperature in <city>" markets, read
from the real event page. RANDOM picks on purpose: it is the control group for the BTC trader.
No wallet, no keys, no orders.

How it works
  * 8 cities spread over the clock: Hong Kong, Shanghai, Tokyo, Seoul, London, Paris, NYC, Miami.
  * Every city has one event per day with 11 temperature brackets. For every open event
    (today, tomorrow, the day after) the trader plans 3 bets at random times.
  * At a planned time it opens the event page, reads every bracket's "Buy Yes / Buy No" price as
    shown, keeps the brackets that are still undecided (Yes price 5c..85c), picks ONE at random,
    and picks Yes (60%) or No (40%) at random. my_choice = YES/NO on that bracket.
  * The cost is the real best ask in the order book plus the weather taker fee (0.05*p*(1-p)).
  * Stake starts at 2% of a separate $200 bankroll. After 20 settled bets in a price band the
    stake for that band is scaled: x1.5 if the band made money, x0.5 if it lost more than 20%.
    Halved after 5 losses in a row and while the bankroll is under 60% of the start.
  * Result = official resolution (gamma, market closed with exact 0/1 prices).

The browser is opened only for a visit and closed afterwards, so it does not sit in memory.
If the page cannot be read, the prices come from the public API and the row says api_fallback.
"""
import argparse, asyncio, json, os, random, re, sys, time
from datetime import datetime, timedelta, timezone
from uicommon import (VERSION, WX_DIR, STATE_DIR, SITE, GAMMA, UIBrowser, Telegram, make_logger, now, iso, utc_day,
                      save_json, load_json, write_csv, append_csv, read_csv, http_json, ahttp_json, arr,
                      fee_per_share, best_bid_ask, gamma_market, official_winner)

CITIES = {"hong-kong": "Hong Kong", "shanghai": "Shanghai", "tokyo": "Tokyo", "seoul": "Seoul",
          "london": "London", "paris": "Paris", "nyc": "NYC", "miami": "Miami"}
START_BANKROLL = 200.0
FEE_RATE = 0.05                                   # weather taker fee rate (bundled fee doc)
BETS_PER_EVENT = 3
PRICE_MIN, PRICE_MAX = 0.05, 0.85                 # a bracket is "still undecided" inside this Yes-price band
P_YES = 0.60
BASE_FRAC, MIN_FRAC, MAX_FRAC = 0.02, 0.01, 0.04
MIN_SHARES = 5.0
BAND_EDGES = [0.2, 0.4, 0.6, 0.8]
BAND_MIN_N = 20
LOOP_S = 60
DISCOVER_S = 15 * 60
# hours ahead of UTC (summer time where it applies; an hour of error does not matter for this use)
UTC_OFFSET = {"hong-kong": 8, "shanghai": 8, "tokyo": 9, "seoul": 9, "london": 1, "paris": 2, "nyc": -4, "miami": -4}
DECIDED_LOCAL_HOUR = 14                           # the day's high is usually in by mid-afternoon local time
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]

STATE_PATH = os.path.join(STATE_DIR, "weather_state.json")
ALL_CSV = os.path.join(WX_DIR, "weather_trades_all.csv")
OPEN_CSV = os.path.join(WX_DIR, "weather_trades_open.csv")
PAGE_CSV = os.path.join(WX_DIR, "weather_page_snapshots.csv")
BAND_CSV = os.path.join(WX_DIR, "weather_price_band_table.csv")
DAILY_CSV = os.path.join(WX_DIR, "weather_daily_summary.csv")

COLS = ["bet_id", "placed_utc", "city", "event_date", "event_slug", "ui_url", "ui_title", "status",
        "bracket_label", "brackets_on_page", "brackets_undecided", "random_pick_index", "my_choice", "choice_reason",
        "ui_yes_cents", "ui_no_cents", "ui_percent", "ui_bracket_volume", "book_bid", "book_ask",
        "entry_price_exec", "entry_price_ui", "fee_per_share", "cost_per_share", "price_band", "stake_rule",
        "bet_usd", "shares", "bankroll_before", "data_source",
        "outcome_official", "winning_bracket", "win", "pnl_usd", "pnl_if_ui_price_usd", "bankroll_after", "settled_utc",
        "resolution_source", "loss_streak_before", "ui_read_latency_ms", "page_load_s", "code_version", "notes"]
PAGE_COLS = ["visit_utc", "event_slug", "city", "event_date", "bracket_label", "ui_yes_cents", "ui_no_cents",
             "ui_percent", "ui_bracket_volume", "data_source"]
BAND_COLS = ["price_band", "bets_settled", "wins", "win_rate", "avg_cost_per_share", "staked_usd", "pnl_usd",
             "roi", "stake_multiplier"]
DAILY_COLS = ["utc_day", "bets_placed", "bets_settled", "wins", "losses", "win_rate", "staked_usd", "pnl_usd",
              "bankroll_end", "no_open_bracket", "ui_rows", "api_fallback_rows"]

READ_JS = r"""() => {
  const lines = (document.body ? document.body.innerText : '').split('\n').map(s => s.trim()).filter(Boolean);
  const i0 = lines.findIndex(l => /^Highest temperature/i.test(l));
  return { slug: location.pathname.split('/').pop(), title: i0 >= 0 ? lines[i0] : null,
           lines: lines.slice(Math.max(0, i0), Math.max(0, i0) + 160),
           dialog: document.querySelectorAll('[role=dialog]').length };
}"""


def cents(x):
    try:
        return round(float(str(x).replace("¢", "").replace("<", "").strip()) / 100.0, 4)
    except Exception:
        return None

def norm(label):
    return re.sub(r"[^a-z0-9]", "", (label or "").lower().replace("°", ""))

def band(p):
    lo = 0.0
    for e in BAND_EDGES:
        if p < e:
            return f"{lo:.1f}-{e:.1f}"
        lo = e
    return "0.8-1.0"

def r2(x):
    return None if x is None else round(x, 2)

def r4(x):
    return None if x is None else round(x, 4)

def event_slug(city, d):
    return f"highest-temperature-in-{city}-on-{MONTHS[d.month - 1]}-{d.day}-{d.year}"

def parse_brackets(lines):
    """Rows of the page: label | '$X Vol.' | 'NN%' | Buy Yes | 40c | Buy No | 63c."""
    out = []
    for i, l in enumerate(lines):
        if l == "Buy Yes" and i + 3 < len(lines) and lines[i + 2] == "Buy No" and i >= 2:
            pct, vol, label = lines[i - 1], lines[i - 2], (lines[i - 3] if i >= 3 else None)
            if not vol.endswith("Vol."):
                label, vol = vol, None
            if not label or not re.search(r"\d", label):
                continue
            out.append({"label": label, "yes": cents(lines[i + 1]), "no": cents(lines[i + 3]), "pct": pct, "vol": vol})
    return out


class WeatherTrader:
    def __init__(self, seed=None):
        self.log = make_logger("ui_weather_trader")
        self.rng = random.Random(seed)
        self.st = load_json(STATE_PATH, None) or {
            "version": VERSION, "created_utc": iso(), "start_bankroll": START_BANKROLL, "cash": START_BANKROLL,
            "events": {}, "open": {}, "bands": {}, "loss_streak": 0, "settled_count": 0, "next_id": 1,
            "last_discover": 0, "last_summary_t": 0, "ui_fail_streak": 0}
        self.tg = Telegram("UI paper trader - weather", os.path.join(STATE_DIR, "weather_telegram.json"))
        self.ui = UIBrowser(self.log, tz="UTC")

    def equity(self):
        return self.st["cash"] + sum(o.get("bet_usd") or 0.0 for o in self.st["open"].values())

    def save(self):
        save_json(STATE_PATH, self.st)

    # ---------------------------------------------------------------- discovery + planning
    async def discover(self, force_now=0):
        today = datetime.now(timezone.utc).date()
        added = 0
        for city in CITIES:
            for k in range(0, 3):
                d = today + timedelta(days=k)
                slug = event_slug(city, d)
                if slug in self.st["events"]:
                    continue
                try:
                    ev = await ahttp_json(f"{GAMMA}/events?slug={slug}", timeout=8, retries=2)
                except Exception as e:
                    self.log(f"discover {slug}: {e.__class__.__name__}"); continue
                if not ev or ev[0].get("closed") or not ev[0].get("markets"):
                    continue
                e = ev[0]
                mk = []
                for m in e["markets"]:
                    outs, toks = arr(m.get("outcomes")), arr(m.get("clobTokenIds"))
                    if len(outs) == 2 and len(toks) == 2:
                        t = dict(zip(outs, toks))
                        mk.append({"label": m.get("groupItemTitle") or m.get("question"), "key": norm(m.get("groupItemTitle")),
                                   "yes": t.get("Yes"), "no": t.get("No"), "cid": m.get("conditionId"),
                                   "closed": bool(m.get("closed"))})
                if not mk:
                    continue
                t0 = now()
                day0 = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
                # last sensible moment = 14:00 local on the event's day (after that the brackets are decided:
                # the first run wasted 6 visits on finished days, all NO_OPEN_BRACKET)
                t_last = day0 - UTC_OFFSET.get(city, 0) * 3600 + DECIDED_LOCAL_HOUR * 3600
                if t_last < t0 + 900:
                    self.st["events"][slug] = {"city": city, "date": d.isoformat(), "title": e.get("title"), "markets": [],
                                               "plan": [], "done": 0, "registered_utc": iso(),
                                               "skipped": "local day already past 14:00 when first seen"}
                    continue
                t1 = min(t0 + 30 * 3600, t_last)
                times = sorted(self.rng.uniform(t0 + 60, t1) for _ in range(BETS_PER_EVENT))
                self.st["events"][slug] = {"city": city, "date": d.isoformat(), "title": e.get("title"), "markets": mk,
                                           "plan": times, "done": 0, "resolution_source": e.get("resolutionSource"),
                                           "registered_utc": iso()}
                added += 1
        if force_now:                                           # verification: pull some planned bets forward
            todo = [s for s, e in self.st["events"].items() if e["plan"]]
            self.rng.shuffle(todo)
            for s in todo[:force_now]:
                self.st["events"][s]["plan"][0] = now()
                self.st["events"][s]["forced"] = True
        for s in [s for s, e in self.st["events"].items()      # forget events finished more than 5 days ago
                  if not e["plan"] and e["date"] < (today - timedelta(days=5)).isoformat()
                  and not any(o["event_slug"] == s for o in self.st["open"].values())]:
            self.st["events"].pop(s, None)
        self.st["last_discover"] = now(); self.save()
        if added:
            self.log(f"registered {added} new event(s); {sum(len(e['plan']) for e in self.st['events'].values())} bets planned")

    # ---------------------------------------------------------------- page visit
    async def visit(self, slug):
        """Returns (brackets, title, latency_ms, load_s, source)."""
        took = await self.ui.goto(f"{SITE}/event/{slug}", timeout_s=60)
        if took is None:                                        # one more try on a fresh browser (2 of 3 visits timed out under load)
            try:
                await self.ui.start()
                took = await self.ui.goto(f"{SITE}/event/{slug}", timeout_s=60)
            except Exception:
                took = None
        if took is not None:
            for _ in range(14):
                await asyncio.sleep(1.0)
                await self.ui.dismiss_notice()
                d, lat = await self.ui.js(READ_JS, 6)
                if d and d.get("slug") == slug:
                    br = parse_brackets(d.get("lines") or [])
                    if len(br) >= 3:
                        self.st["ui_fail_streak"] = 0
                        return br, d.get("title"), int((lat or 0) * 1000), round(took, 1), "ui"
        self.st["ui_fail_streak"] = self.st.get("ui_fail_streak", 0) + 1
        self.log(f"page unreadable for {slug}; using the public API prices")
        try:
            ev = await ahttp_json(f"{GAMMA}/events?slug={slug}", timeout=8, retries=2)
            br = []
            for m in ev[0]["markets"]:
                pr = [float(x) for x in arr(m.get("outcomePrices"))]
                br.append({"label": m.get("groupItemTitle"), "yes": pr[0] if pr else None,
                           "no": pr[1] if len(pr) > 1 else None, "pct": None, "vol": m.get("volume")})
            return br, ev[0].get("title"), None, None, "api_fallback(ui_unavailable)"
        except Exception:
            return [], None, None, None, "none"

    def stake_frac(self, b):
        c = self.st["bands"].get(b)
        mult, rule = 1.0, "base 2% of bankroll"
        if c and c["n"] >= BAND_MIN_N and c["staked"] > 0:
            roi = c["pnl"] / c["staked"]
            if roi > 0:
                mult, rule = 1.5, f"x1.5: price band {b} is up {roi * 100:+.0f}% over {c['n']} bets"
            elif roi < -0.20:
                mult, rule = 0.5, f"x0.5: price band {b} is down {roi * 100:+.0f}% over {c['n']} bets"
            else:
                rule = f"x1.0: price band {b} is flat ({roi * 100:+.0f}% over {c['n']} bets)"
        frac = BASE_FRAC * mult
        if self.st["loss_streak"] >= 5:
            frac *= 0.5; rule += f"; halved after {self.st['loss_streak']} losses in a row"
        if self.equity() < 0.6 * self.st["start_bankroll"]:
            frac *= 0.5; rule += "; halved: bankroll under 60% of start"
        return min(MAX_FRAC, max(MIN_FRAC / 2, frac)), mult, rule

    async def place(self, slug, forced=False):
        e = self.st["events"][slug]
        br, title, lat, load_s, src = await self.visit(slug)
        t = now()
        for b in br:
            append_csv(PAGE_CSV, PAGE_COLS, {"visit_utc": iso(t), "event_slug": slug, "city": CITIES[e["city"]],
                       "event_date": e["date"], "bracket_label": b["label"], "ui_yes_cents": b["yes"],
                       "ui_no_cents": b["no"], "ui_percent": b["pct"], "ui_bracket_volume": b["vol"], "data_source": src})
        bid = self.st["next_id"]; self.st["next_id"] += 1
        row = {"bet_id": f"W{bid:05d}", "placed_utc": iso(t, ms=True), "city": CITIES[e["city"]], "event_date": e["date"],
               "event_slug": slug, "ui_url": f"{SITE}/event/{slug}", "ui_title": title or e.get("title"),
               "brackets_on_page": len(br), "data_source": src, "bankroll_before": r2(self.equity()),
               "resolution_source": e.get("resolution_source"), "loss_streak_before": self.st["loss_streak"],
               "ui_read_latency_ms": lat, "page_load_s": load_s, "code_version": VERSION,
               "notes": "verification run: planned time pulled forward" if forced else ""}
        live = [b for b in br if b["yes"] is not None and PRICE_MIN <= b["yes"] <= PRICE_MAX]
        row["brackets_undecided"] = len(live)
        if not live:
            row.update(status="NO_OPEN_BRACKET", bet_usd=0, shares=0, my_choice="",
                       choice_reason=("page and API both unreadable" if not br else
                                      f"no bracket priced between {PRICE_MIN:.0%} and {PRICE_MAX:.0%}: the day is already decided"),
                       bankroll_after=r2(self.equity()))
            append_csv(ALL_CSV, COLS, row); self.write_daily(); self.save()
            self.log(f"{row['bet_id']} {slug}: NO_OPEN_BRACKET ({len(br)} brackets on page)")
            return
        idx = self.rng.randrange(len(live)); b = live[idx]
        side = "Yes" if self.rng.random() < P_YES else "No"
        mk = next((m for m in e["markets"] if m["key"] == norm(b["label"])), None)
        bidp = ask = ask_size = None
        note = row["notes"]
        if mk:
            book_ok = False
            async def book(tok):
                last = None
                for attempt in (1, 2, 3):
                    try:
                        return await asyncio.wait_for(asyncio.to_thread(best_bid_ask, tok, 8), 12)
                    except Exception as ex:
                        last = ex
                        await asyncio.sleep(0.5)
                self.log(f"order book unreadable for {slug} '{b['label']}': {last.__class__.__name__}: {str(last)[:120]}")
                return None
            r = await book(mk["yes" if side == "Yes" else "no"])
            if r:
                bidp, _, ask, ask_size, _ = r; book_ok = True
            else:
                note += "; order book unreachable"
            if book_ok and ask is None and side == "No":        # the book really has nothing offered on No
                r = await book(mk["yes"])
                if r and r[2] is not None:
                    side = "Yes"; bidp, _, ask, ask_size, _ = r; note += "; No had no ask, switched to Yes"
        else:
            note += "; bracket label on the page not found in the API list"
        ui_px = b["yes"] if side == "Yes" else b["no"]
        px = ask if ask else ui_px
        if not ask:
            note += "; no ask in the book, the page price was used as the fill (optimistic)"
        if not px or px >= 0.995:
            row.update(status="NO_LIQUIDITY", bet_usd=0, shares=0, my_choice="", bracket_label=b["label"],
                       choice_reason="picked bracket had no price to buy at", bankroll_after=r2(self.equity()), notes=note)
            append_csv(ALL_CSV, COLS, row); self.write_daily(); self.save(); return
        pb = band(px); frac, mult, rule = self.stake_frac(pb)
        f = fee_per_share(FEE_RATE, px); cps = px + f
        stake = self.equity() * frac; shares = stake / cps
        if shares < MIN_SHARES:
            shares = MIN_SHARES; rule += "; raised to the 5-share minimum"
        if ask_size and shares > ask_size:
            shares = max(MIN_SHARES, ask_size); rule += "; capped by the size at the best ask"
        stake = shares * cps
        if stake > self.st["cash"]:
            row.update(status="NO_CASH", bet_usd=0, shares=0, my_choice="", bracket_label=b["label"],
                       choice_reason=f"not enough cash (${self.st['cash']:.2f})", bankroll_after=r2(self.equity()), notes=note)
            append_csv(ALL_CSV, COLS, row); self.save(); return
        row.update({"status": "OPEN", "bracket_label": b["label"], "random_pick_index": f"{idx + 1} of {len(live)}",
                    "my_choice": side.upper(),
                    "choice_reason": f"random: bracket {idx + 1} of {len(live)} undecided, side {side} (Yes {P_YES:.0%} / No {1 - P_YES:.0%})",
                    "ui_yes_cents": b["yes"], "ui_no_cents": b["no"], "ui_percent": b["pct"], "ui_bracket_volume": b["vol"],
                    "book_bid": bidp, "book_ask": ask, "entry_price_exec": r4(px), "entry_price_ui": ui_px,
                    "fee_per_share": r4(f), "cost_per_share": r4(cps), "price_band": pb, "stake_rule": rule,
                    "bet_usd": r2(stake), "shares": r4(shares), "notes": note.strip("; "),
                    "_cid": mk["cid"] if mk else None, "_key": norm(b["label"])})
        self.st["cash"] -= stake
        self.st["open"][row["bet_id"]] = row
        self.write_open(); self.save()
        self.log(f"BET {row['bet_id']} {CITIES[e['city']]} {e['date']} bracket '{b['label']}' my_choice={side.upper()} "
                 f"${stake:.2f} at {px:.3f} (page shows {ui_px}) source {src}")

    # ---------------------------------------------------------------- settlement
    async def settle(self):
        by_event = {}
        for k, o in self.st["open"].items():
            by_event.setdefault(o["event_slug"], []).append(k)
        for slug, keys in by_event.items():
            ev = await asyncio.to_thread(gamma_market, slug)
            if not ev:
                continue
            winners = [m.get("groupItemTitle") for m in ev["markets"] if official_winner(m) == "Yes"]
            for k in keys:
                o = self.st["open"][k]
                m = next((m for m in ev["markets"] if m.get("conditionId") == o.get("_cid")
                          or norm(m.get("groupItemTitle")) == o.get("_key")), None)
                res = official_winner(m) if m else None
                if res is None:
                    continue
                won = o["my_choice"] == res.upper()
                payout = o["shares"] if won else 0.0
                pnl = payout - o["bet_usd"]
                self.st["cash"] += payout
                pnl_ui = None
                if o.get("entry_price_ui"):
                    cu = o["entry_price_ui"] + fee_per_share(FEE_RATE, o["entry_price_ui"])
                    pnl_ui = ((o["bet_usd"] / cu) if won else 0.0) - o["bet_usd"]
                c = self.st["bands"].setdefault(o["price_band"], {"n": 0, "wins": 0, "staked": 0.0, "pnl": 0.0, "cost": 0.0})
                c["n"] += 1; c["wins"] += int(won); c["staked"] += o["bet_usd"]; c["pnl"] += pnl; c["cost"] += o["cost_per_share"]
                self.st["loss_streak"] = 0 if won else self.st["loss_streak"] + 1
                self.st["settled_count"] += 1
                self.st["open"].pop(k)
                o.pop("_cid", None); o.pop("_key", None)
                o.update(status="SETTLED", outcome_official=res.upper(), winning_bracket=" / ".join(w for w in winners if w),
                         win=int(won), pnl_usd=r2(pnl), pnl_if_ui_price_usd=r2(pnl_ui), bankroll_after=r2(self.equity()),
                         settled_utc=iso())
                append_csv(ALL_CSV, COLS, o)
                self.log(f"SETTLED {o['bet_id']} {o['city']} {o['event_date']} '{o['bracket_label']}' my_choice={o['my_choice']} "
                         f"official={res.upper()} {'WIN' if won else 'LOSS'} pnl ${pnl:+.2f}")
        self.write_open(); self.write_bands(); self.write_daily(); self.save()

    # ---------------------------------------------------------------- reports
    def write_open(self):
        write_csv(OPEN_CSV, COLS, [self.st["open"][k] for k in sorted(self.st["open"])])

    def write_bands(self):
        out = []
        for b in sorted(self.st["bands"]):
            c = self.st["bands"][b]
            roi = c["pnl"] / c["staked"] if c["staked"] else None
            mult = 1.0 if (c["n"] < BAND_MIN_N or roi is None) else (1.5 if roi > 0 else 0.5 if roi < -0.2 else 1.0)
            out.append({"price_band": b, "bets_settled": c["n"], "wins": c["wins"], "win_rate": r4(c["wins"] / c["n"]),
                        "avg_cost_per_share": r4(c["cost"] / c["n"]), "staked_usd": r2(c["staked"]), "pnl_usd": r2(c["pnl"]),
                        "roi": r4(roi), "stake_multiplier": mult})
        write_csv(BAND_CSV, BAND_COLS, out)

    def write_daily(self):
        rows = read_csv(ALL_CSV) + [dict(o) for o in self.st["open"].values()]
        days = {}
        for r in rows:
            days.setdefault(str(r["placed_utc"])[:10], []).append(r)
        out = []
        for d in sorted(days):
            L = days[d]; S = [r for r in L if r["status"] == "SETTLED"]
            w = sum(1 for r in S if str(r["win"]) == "1")
            last = next((r["bankroll_after"] for r in reversed(L) if r.get("bankroll_after") not in (None, "")), "")
            out.append({"utc_day": d, "bets_placed": sum(1 for r in L if r["status"] in ("OPEN", "SETTLED")),
                        "bets_settled": len(S), "wins": w, "losses": len(S) - w, "win_rate": r4(w / len(S)) if S else "",
                        "staked_usd": r2(sum(float(r["bet_usd"] or 0) for r in L if r["status"] in ("OPEN", "SETTLED"))),
                        "pnl_usd": r2(sum(float(r["pnl_usd"] or 0) for r in S)), "bankroll_end": last,
                        "no_open_bracket": sum(1 for r in L if r["status"] == "NO_OPEN_BRACKET"),
                        "ui_rows": sum(1 for r in L if r["data_source"] == "ui"),
                        "api_fallback_rows": sum(1 for r in L if str(r["data_source"]).startswith("api_fallback"))})
        write_csv(DAILY_CSV, DAILY_COLS, out)

    def summary(self):
        if now() - self.st.get("last_summary_t", 0) < 12 * 3600:
            return
        rows = [r for r in read_csv(ALL_CSV) if r["status"] == "SETTLED"]
        if not rows and not self.st["open"]:
            return
        self.st["last_summary_t"] = now()
        w = sum(1 for r in rows if r["win"] == "1"); pnl = sum(float(r["pnl_usd"] or 0) for r in rows)
        self.tg.send(f"wx_sum_{int(now() // 43200)}",
                     "PAPER bets on daily-high temperature markets in 8 cities, brackets picked at RANDOM from the live "
                     "Polymarket page (control group). No real money.\n"
                     f"all time: {len(rows)} settled, {w} won / {len(rows) - w} lost, P&L ${pnl:+.2f} | open bets "
                     f"{len(self.st['open'])} | bankroll ${self.equity():.2f} (start ${self.st['start_bankroll']:.0f})", dedup=False)

    # ---------------------------------------------------------------- main
    async def main(self, minutes=None, force_now=0):
        stop = now() + minutes * 60 if minutes else None
        self.log(f"UI weather paper trader v{VERSION} starting; bankroll ${self.equity():.2f}, "
                 f"{len(self.st['open'])} open bets, {len(self.st['events'])} events known")
        last_settle = 0
        first = True
        while stop is None or now() < stop:
            try:
                if first or now() - self.st.get("last_discover", 0) > DISCOVER_S:
                    await self.discover(force_now if first else 0); first = False
                due = [(s, e) for s, e in self.st["events"].items() if e["plan"] and e["plan"][0] <= now()]
                if due:
                    try:
                        await self.ui.start()
                    except Exception as ex:
                        self.log(f"browser did not start ({ex.__class__.__name__}); API prices will be used")
                    for s, e in due:
                        e["plan"].pop(0); e["done"] += 1
                        forced = bool(e.pop("forced", False))
                        self.save()
                        try:
                            await asyncio.wait_for(self.place(s, forced), 120)
                        except Exception as ex:
                            self.log(f"bet on {s} failed: {ex.__class__.__name__}: {str(ex)[:120]}")
                    await self.ui.stop()
                    if self.st.get("ui_fail_streak", 0) in (3, 10):
                        self.tg.send("wx_ui_down", f"⚠ weather pages unreadable {self.st['ui_fail_streak']} visits in a row; "
                                                    "bets continue on API prices and are marked api_fallback.")
                if self.st["open"] and now() - last_settle > 10 * 60:
                    last_settle = now()
                    await self.settle()
                self.summary()
            except Exception as ex:
                self.log(f"loop error {ex.__class__.__name__}: {str(ex)[:160]}")
            await asyncio.sleep(LOOP_S if not stop else 10)
        await self.ui.stop()
        self.write_open(); self.write_daily(); self.save()
        self.log(f"stopped; bankroll ${self.equity():.2f}, open bets {len(self.st['open'])}")


def selftest():
    import tempfile
    global STATE_PATH, ALL_CSV, OPEN_CSV, PAGE_CSV, BAND_CSV, DAILY_CSV
    d = tempfile.mkdtemp()
    STATE_PATH, ALL_CSV, OPEN_CSV, PAGE_CSV, BAND_CSV, DAILY_CSV = (os.path.join(d, n) for n in
        ("st.json", "all.csv", "open.csv", "page.csv", "band.csv", "daily.csv"))
    lines = ("Highest temperature in London on September 28? | Past | Sep 27 | 19°C 40% | ALL | 14°C or below | $578 Vol. | <1% | "
             "Buy Yes | 0.3¢ | Buy No | 99.9¢ | 15°C | $407 Vol. | <1% | Buy Yes | 0.1¢ | Buy No | -- | 18°C | $5,936 Vol. | 39% | "
             "Buy Yes | 40¢ | Buy No | 63¢ | 19°C | $3,466 Vol. | 40% | Buy Yes | 41¢ | Buy No | 62¢ | 24°C or higher | $1,038 Vol. | "
             "<1% | Buy Yes | 0.1¢ | Buy No | --").split(" | ")
    br = parse_brackets(lines)
    assert [b["label"] for b in br] == ["14°C or below", "15°C", "18°C", "19°C", "24°C or higher"], br
    assert br[2]["yes"] == 0.40 and br[2]["no"] == 0.63 and br[1]["no"] is None and br[0]["yes"] == 0.003
    assert norm("18°C") == "18c" and norm("14°C or below") == "14corbelow" and band(0.41) == "0.4-0.6" and band(0.95) == "0.8-1.0"
    assert event_slug("nyc", datetime(2026, 9, 28).date()) == "highest-temperature-in-nyc-on-september-28-2026"
    t = WeatherTrader(seed=3); t.tg.send = lambda *a, **k: False
    t.log = print                                               # a self-test must not write into the live log file
    frac, mult, rule = t.stake_frac("0.4-0.6"); assert abs(frac - BASE_FRAC) < 1e-9 and mult == 1.0
    t.st["bands"]["0.4-0.6"] = {"n": 25, "wins": 15, "staked": 100.0, "pnl": 12.0, "cost": 12.0}
    assert t.stake_frac("0.4-0.6")[1] == 1.5
    t.st["bands"]["0.4-0.6"]["pnl"] = -30.0
    assert t.stake_frac("0.4-0.6")[1] == 0.5
    t.st["loss_streak"] = 5
    assert "halved" in t.stake_frac("0.0-0.2")[2]
    t.write_bands(); assert read_csv(BAND_CSV)[0]["stake_multiplier"] == "0.5"
    print("SELFTEST OK - parsed", len(br), "brackets; stake rules and band table verified")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=None, help="bounded run (verification)")
    ap.add_argument("--force-now", type=int, default=0, help="pull this many planned bets forward to now (verification)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    try:
        asyncio.run(WeatherTrader().main(a.minutes, a.force_now))
    except KeyboardInterrupt:
        pass
