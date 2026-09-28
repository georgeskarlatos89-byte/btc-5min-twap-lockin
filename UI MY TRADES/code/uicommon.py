#!/usr/bin/env python3
"""
uicommon.py - shared plumbing for the Polymarket UI paper traders (BTC 5m + weather).

PAPER ONLY. There is no wallet, key, signer or order endpoint anywhere in this package.
Everything "traded" here is a hypothetical bet written to a CSV.

Contents: paths, logging, atomic CSV/state files, HTTP with retries, Telegram (rate limited),
and the headless-browser session that reads the real polymarket.com page.
"""
import asyncio, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

VERSION = "1.0.8"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# VPS:   <Trading polymarket UI on vps weather and btc-5-min>/code  -> CSVs in <that folder>/UI MY TRADES/{btc,weather}
# local: <workspace>/UI MY TRADES/code                              -> CSVs in <workspace>/UI MY TRADES/{btc,weather}
TRADES_DIR = ROOT if os.path.basename(ROOT) == "UI MY TRADES" else os.path.join(ROOT, "UI MY TRADES")
BTC_DIR = os.path.join(TRADES_DIR, "btc")
WX_DIR = os.path.join(TRADES_DIR, "weather")
STATE_DIR = os.path.join(ROOT, "state")
LOG_DIR = os.path.join(ROOT, "logs")
for _d in (BTC_DIR, WX_DIR, STATE_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
# Data requests say what they are. 2026-09-27 19:26Z: the order book endpoint began answering 403 to
# requests that carried the browser's identity string but came from Python (curl and an honest
# identity both got 200). The browser identity above is used by the real browser only.
API_UA = "polymarket-ui-paper-trader/1.0 (read-only research; python-urllib)"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
SITE = "https://polymarket.com"
TG_ENV_CANDIDATES = [os.path.join(ROOT, "telegram.env"), os.path.expanduser("~/s1_harness/telegram.env")]


# ------------------------------------------------------------------ time / log
def now():
    return time.time()

def iso(ts=None, ms=False):
    d = datetime.fromtimestamp(now() if ts is None else ts, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if ms else d.strftime("%Y-%m-%dT%H:%M:%SZ")

def utc_day(ts=None):
    return datetime.fromtimestamp(now() if ts is None else ts, timezone.utc).strftime("%Y-%m-%d")

def make_logger(name):
    path = os.path.join(LOG_DIR, name + ".log")
    def log(msg):
        line = f"[{iso()}] {msg}"
        print(line, flush=True)
        try:
            # rotate at 20 MB: keep one previous file
            if os.path.exists(path) and os.path.getsize(path) > 20 * 2**20:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    return log


# ------------------------------------------------------------------ files
def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def load_json(path, default):
    for p in (path, path + ".tmp"):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            continue
        except Exception:
            continue                                            # corrupt file: fall through to default
    return default

def write_csv(path, cols, rows):
    """Atomic full rewrite (used for files whose rows get completed later, e.g. outcome)."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    os.replace(tmp, path)

def append_csv(path, cols, row):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    if not new:
        with open(path, encoding="utf-8") as f:
            head = f.readline().rstrip("\r\n")
        if head != ",".join(cols):                              # schema changed: keep the old file, start a new one
            os.replace(path, path[:-4] + f".schema-{int(now())}.csv")
            new = True
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in cols})

def read_csv(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


# ------------------------------------------------------------------ http
def http_json(url, timeout=8, retries=3, backoff=0.8, headers=None):
    """GET JSON with retries. Raises the last error after `retries` attempts."""
    last = None
    h = {"User-Agent": API_UA, "Accept": "application/json"}
    h.update(headers or {})
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(backoff * (2 ** i))
    raise last

async def ahttp_json(url, **kw):
    return await asyncio.to_thread(http_json, url, **kw)

def arr(x):
    return json.loads(x) if isinstance(x, str) else (x or [])

def fee_per_share(rate, p):
    """Polymarket taker fee per share: rate * p * (1 - p). Crypto 0.07, weather 0.05 (bundled fee doc)."""
    return rate * p * (1.0 - p)

def best_bid_ask(token_id, timeout=4):
    """(bid, bid_size, ask, ask_size, book_ts) from the public CLOB book. Missing side -> None."""
    b = http_json(f"{CLOB}/book?token_id={token_id}", timeout=timeout, retries=2, backoff=0.3)
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    bid = max(bids) if bids else (None, None)
    ask = min(asks) if asks else (None, None)
    try:
        ts = float(b.get("timestamp", 0)) / 1000.0
    except Exception:
        ts = None
    return bid[0], bid[1], ask[0], ask[1], ts

def gamma_market(slug, closed_first=True):
    """Market object for an event slug. closed=true is asked first: plain /events lags ~100 s."""
    order = ("&closed=true", "") if closed_first else ("", "&closed=true")
    for q in order:
        try:
            ev = http_json(f"{GAMMA}/events?slug={slug}{q}", timeout=8, retries=2)
            if ev and ev[0].get("markets"):
                return ev[0]
        except Exception:
            continue
    return None

def official_winner(market):
    """'Up'/'Down'/'Yes'/'No' only when the market is closed with exact 0/1 prices; else None."""
    try:
        if not market.get("closed"):
            return None
        outs = arr(market.get("outcomes")); prices = [float(x) for x in arr(market.get("outcomePrices"))]
        if len(outs) != 2 or sorted(prices) != [0.0, 1.0]:
            return None
        return outs[prices.index(1.0)]
    except Exception:
        return None


# ------------------------------------------------------------------ telegram (bounded)
class Telegram:
    """At most `per_hour` messages an hour, 6 h dedup per key. Never raises."""
    def __init__(self, tag, state_path, per_hour=4, dedup_s=6 * 3600):
        self.tag, self.path, self.per_hour, self.dedup_s = tag, state_path, per_hour, dedup_s
        self.st = load_json(state_path, {"sent": {}, "ts": []})
        self.cfg = {}
        for p in TG_ENV_CANDIDATES:
            if os.path.exists(p):
                try:
                    for ln in open(p, encoding="utf-8"):
                        if "=" in ln and not ln.strip().startswith("#"):
                            k, v = ln.strip().split("=", 1); self.cfg[k.strip()] = v.strip()
                    break
                except Exception:
                    pass

    def send(self, key, text, dedup=True):
        try:
            t = now()
            if dedup and t - self.st["sent"].get(key, 0) < self.dedup_s:
                return False
            self.st["ts"] = [x for x in self.st["ts"] if x > t - 3600]
            if len(self.st["ts"]) >= self.per_hour:
                return False
            tok, chat = self.cfg.get("TELEGRAM_BOT_TOKEN"), self.cfg.get("TELEGRAM_CHAT_ID")
            if not tok or not chat:
                return False
            body = {"chat_id": chat, "text": (f"\U0001F537 {self.tag}\n{text}")[:4000], "disable_web_page_preview": "true"}
            if self.cfg.get("TELEGRAM_THREAD_ID"):
                body["message_thread_id"] = self.cfg["TELEGRAM_THREAD_ID"]
            req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                         urllib.parse.urlencode(body).encode())
            ok = urllib.request.urlopen(req, timeout=10).status == 200
            if ok:
                self.st["sent"][key] = t; self.st["ts"].append(t); save_json(self.path, self.st)
            return ok
        except Exception:
            return False


# ------------------------------------------------------------------ browser
BLOCK_TYPES = {"image", "media", "font"}
BLOCK_DOMAINS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "facebook.com", "facebook.net",
                 "tiktok.com", "snapchat.com", "sc-static.net", "reddit.com", "redditstatic.com", "twitter.com",
                 "x.com", "t.co", "amplitude.com", "segment.com", "segment.io", "hightouch.com",
                 "hightouch-events.com", "intercom.io", "intercomcdn.com", "customer.io", "braze.com", "tapad.com",
                 "sentry.io", "datadoghq.com", "hotjar.com", "clarity.ms", "google.com", "gstatic.com")

def blocked(url):
    """Block ad/analytics hosts by exact domain (a substring test once blocked polymarket.com via 't.co')."""
    h = (urlparse(url).hostname or "").lower()
    return (any(h == d or h.endswith("." + d) for d in BLOCK_DOMAINS)
            or "/cdn-cgi/rum" in url or "/api/impact" in url or "/capi" in url)

# Animations and the chart canvas are what made the page unusable on a 2-core VM
# (1.1 GB, updates 15-60 s late). With them off: ~0.4 s reads, ~790 MB.
LIGHT_CSS = ("*,*::before,*::after{animation:none!important;transition:none!important}"
             "canvas,video,iframe{display:none!important}")
INIT_JS = ("(() => { const add = () => { try { const s = document.createElement('style'); s.id='__light';"
           " s.textContent = %s; (document.head||document.documentElement).appendChild(s) } catch(e){} };"
           " if (document.documentElement) add(); else document.addEventListener('DOMContentLoaded', add); })()"
           % json.dumps(LIGHT_CSS))
DISMISS_JS = ("() => { const b = [...document.querySelectorAll('button,a')].find(e => "
              "/continue in view only mode/i.test(e.textContent||'')); if (b) { b.click(); return true } return false }")


class UIBrowser:
    """One headless Chromium + one page. Relaunches itself on any failure."""
    def __init__(self, log, tz="America/New_York"):
        self.log, self.tz = log, tz
        self.pw = self.browser = self.ctx = self.page = None
        self.started = 0.0
        self.launches = 0

    async def start(self):
        from playwright.async_api import async_playwright
        await self.stop()
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True, args=[
            "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu", "--disable-extensions",
            "--disable-background-networking", "--mute-audio", "--js-flags=--max-old-space-size=512"])
        self.ctx = await self.browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US",
                                                  user_agent=UA, timezone_id=self.tz, reduced_motion="reduce")
        async def route(r):
            try:
                rq = r.request
                if rq.resource_type in BLOCK_TYPES or blocked(rq.url):
                    await r.abort()
                else:
                    await r.continue_()
            except Exception:
                pass
        await self.ctx.route("**/*", route)
        await self.ctx.add_init_script(INIT_JS)
        self.page = await self.ctx.new_page()
        self.started = now(); self.launches += 1
        self.log(f"browser launched (#{self.launches})")

    async def stop(self):
        for obj, meth in ((self.ctx, "close"), (self.browser, "close"), (self.pw, "stop")):
            if obj is not None:
                try:
                    await asyncio.wait_for(getattr(obj, meth)(), 10)
                except Exception:
                    pass
        self.pw = self.browser = self.ctx = self.page = None

    async def goto(self, url, timeout_s=50):
        """Full page load. Returns seconds taken, or None on failure."""
        t0 = now()
        try:
            if self.page is None:
                await self.start()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            return now() - t0
        except Exception as e:
            self.log(f"goto failed ({e.__class__.__name__}): {str(e)[:120]}")
            return None

    async def js(self, script, timeout_s=6):
        """Evaluate in the page with a hard timeout. Returns (value, latency_s) or (None, None)."""
        if self.page is None:
            return None, None
        t0 = now()
        try:
            v = await asyncio.wait_for(self.page.evaluate(script), timeout_s)
            return v, now() - t0
        except Exception:
            return None, None

    async def dismiss_notice(self):
        v, _ = await self.js(DISMISS_JS, 4)
        return bool(v)
