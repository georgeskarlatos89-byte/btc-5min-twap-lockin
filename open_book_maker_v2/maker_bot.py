#!/usr/bin/env python3
"""
maker_bot.py (v2) - OPEN-BOOK-MAKER live quoting bot for Polymarket BTC 5-minute rounds

WHAT IT DOES: posts a BUY on the Up token at fair - 1.5c and a BUY on the Down token at
(1 - fair) - 1.5c, re-quotes when fair drifts, cancels before the final 20 s.

SAFETY (all enforced in code)
  * DRY RUN BY DEFAULT (.env DRY_RUN=true). .env IS loaded (python-dotenv) - v1 never read it.
  * KILL FILE: create it -> every resting order cancelled within ~1 s, standby until removed.
  * DAILY STOP: realized + marked loss beyond DAILY_STOP_USD -> cancel all, halt until next UTC day.
    v2 keeps `realized` across rounds (v1 reset it every round, so the stop could never trip).
  * POST-ONLY: a bid that would cross the touch is skipped (and the stale one cancelled).
  * Fills are reconciled with the real CLOB trade list (TradeParams(maker_address=...));
    v1 passed a maker_only argument that the client does not have, so no fill was ever counted.
  * Cancels target OUR order ids; cancel_all is used only for the kill file / daily stop.
  * Prices on the venue tick (0.01); size LIVE_QUOTE_SIZE; inventory cap MAX_NET_SHARES.

FEEDS: book_recorder v2 (RTDS TWAP60 + spot, fair_model.py, gamma events lookup).
OUTPUTS: bot_logs/orders.jsonl, bot_logs/bot_rounds.csv (same columns as sim_rounds.csv,
         incl. condition_id + outcome so chain_verify.py can audit it).
"""
import asyncio, csv, json, os, time, urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

from make_strategy import HALF_SPREAD, FAIR_LO, FAIR_HI, NO_QUOTE_SECS, TICK, round_tick, QUOTE_SIZE, MAX_NET_SHARES
import book_recorder as br

LOG_DIR = os.path.join(HERE, "bot_logs")


def env(k, d=None): return os.environ.get(k, d)
def envf(k, d):
    try: return float(env(k, d))
    except (TypeError, ValueError): return d


DRY_RUN = env("DRY_RUN", "true").strip().lower() != "false"
KILL_FILE = env("KILL_FILE", os.path.join(HERE, "KILL"))
DAILY_STOP_USD = envf("DAILY_STOP_USD", 20.0)
REQUOTE_EPS = envf("REQUOTE_EPS", 0.005)
NO_QUOTE = int(NO_QUOTE_SECS.get("5m", 20))
ROUND_T = 300
CLOB_HOST = "https://clob.polymarket.com"


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)


def now_iso(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_book(token_id):
    try:
        req = urllib.request.Request(f"{CLOB_HOST}/book?token_id={token_id}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            b = json.loads(r.read().decode())
        bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
        asks = sorted(((float(x["price"]), float(x["size"])) for x in b.get("asks", [])))
        return dict(bb=bids[0][0] if bids else None, ba=asks[0][0] if asks else None)
    except Exception:
        return dict(bb=None, ba=None)


class Ledger:
    """daily loss tracking (realized across rounds + mark of the open round) and inventory."""
    def __init__(self):
        self.day = None; self.realized = 0.0; self.halted = False
        self.up = 0.0; self.down = 0.0; self.cost = 0.0
        self.round_start = None; self.round_quotes = 0; self.round_fills = 0
        self.fills = []      # (outcome, price, size) this round

    @property
    def net(self): return self.up - self.down

    def new_day_check(self):
        d = datetime.now(timezone.utc).date()
        if d != self.day:
            self.day, self.realized, self.halted = d, 0.0, False
            log(f"NEW UTC DAY {d} - loss counter reset")

    def on_fill(self, outcome, price, size):
        if outcome == "UP": self.up += size
        else: self.down += size
        self.cost += price * size; self.round_fills += 1
        self.fills.append((outcome, price, size))

    def mark(self, fair):
        return self.realized + self.up * fair + self.down * (1 - fair) - self.cost

    def close_round(self, p_end):
        """book the round at the model's final probability (0/1 at the end: end-TWAP vs open)."""
        if self.round_start is not None:
            self.realized += self.up * p_end + self.down * (1 - p_end) - self.cost

    def new_round(self, start):
        self.round_start = start
        self.up = self.down = self.cost = 0.0
        self.round_quotes = 0; self.round_fills = 0; self.fills = []


ledger = Ledger()

# ---------------------------------------------------------------- CLOB client
client = None
MY_ADDRESS = None


def init_client():
    global client, MY_ADDRESS
    if DRY_RUN:
        return
    from py_clob_client.client import ClobClient
    pk = env("POLYMARKET_PRIVATE_KEY"); funder = env("POLYMARKET_PROXY_ADDRESS") or None
    sig = int(env("POLYMARKET_SIG_TYPE", "2"))
    if not pk:
        raise SystemExit("LIVE mode requires POLYMARKET_PRIVATE_KEY in .env")
    client = ClobClient(CLOB_HOST, key=pk, chain_id=137, signature_type=sig, funder=funder)
    client.set_api_creds(client.create_or_derive_api_creds())
    MY_ADDRESS = (funder or client.get_address()).lower()
    log(f"CLOB client ready (LIVE mode) address=...{MY_ADDRESS[-6:]}")


def _log_order(rec):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "orders.jsonl"), "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def place_buy(token_id, price, size, tag):
    rec = dict(ts=now_iso(), action="quote", mode="dry" if DRY_RUN else "live",
               side=f"BUY-{tag}", token=token_id[-12:], price=round(price, 2), size=size)
    if DRY_RUN:
        _log_order(rec); return "dry"
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        args = OrderArgs(price=round(price, 2), size=size, side=BUY, token_id=token_id)
        resp = client.post_order(client.create_order(args), OrderType.GTC)
        rec["order_id"] = resp.get("orderID") or resp.get("orderId")
        rec["ok"] = bool(resp.get("success", True)); _log_order(rec)
        return rec.get("order_id")
    except Exception as e:
        rec["error"] = str(e)[:200]; _log_order(rec)
        return None


def cancel_order(order_id, tag):
    if DRY_RUN or not order_id or order_id == "dry":
        return
    try:
        client.cancel(order_id)
        _log_order(dict(ts=now_iso(), action="cancel", mode="live", side=tag, order_id=order_id))
    except Exception as e:
        log(f"WARN cancel {tag} failed: {e}")


def cancel_all(reason):
    if DRY_RUN:
        return
    try:
        client.cancel_all()
        _log_order(dict(ts=now_iso(), action="cancel_all", mode="live", reason=reason))
    except Exception as e:
        log(f"WARN cancel_all failed: {e}")


class RestingQuotes:
    def __init__(self):
        self.up_px = None; self.up_id = None; self.down_px = None; self.down_id = None


resting = RestingQuotes()


def clear_quotes(reason="round"):
    if resting.up_id: cancel_order(resting.up_id, "UP")
    if resting.down_id: cancel_order(resting.down_id, "DOWN")
    if reason in ("kill", "daily_stop"):
        cancel_all(reason)
    resting.up_px = resting.down_px = None
    resting.up_id = resting.down_id = None


def sync_quotes(fair, tok_up, tok_down):
    bid_up = min(max(round_tick(fair - HALF_SPREAD), 0.01), 0.99)
    bid_down = min(max(round_tick((1 - fair) - HALF_SPREAD), 0.01), 0.99)
    if ledger.net >= MAX_NET_SHARES: bid_up = None
    if ledger.net <= -MAX_NET_SHARES: bid_down = None
    for tag, tok, px in (("UP", tok_up, bid_up), ("DOWN", tok_down, bid_down)):
        cur_px = resting.up_px if tag == "UP" else resting.down_px
        cur_id = resting.up_id if tag == "UP" else resting.down_id
        want = px
        if want is not None:
            book = fetch_book(tok)
            if book["ba"] is not None and want >= book["ba"]:
                want = None                      # post-only: would cross -> do not quote this side
        if want is None:
            if cur_id:
                cancel_order(cur_id, tag)
            if tag == "UP": resting.up_px, resting.up_id = None, None
            else: resting.down_px, resting.down_id = None, None
            continue
        if cur_px is None or abs(want - cur_px) > REQUOTE_EPS:
            if cur_id:
                cancel_order(cur_id, tag)
            oid = place_buy(tok, want, QUOTE_SIZE, tag)
            if tag == "UP": resting.up_px, resting.up_id = want, oid
            else: resting.down_px, resting.down_id = want, oid
            ledger.round_quotes += 1


seen_trades = set()
cur_tokens = {"up": "", "down": ""}


def poll_fills():
    """LIVE: reconcile our maker fills since the last poll (idempotent by trade id)."""
    if DRY_RUN or client is None:
        return
    try:
        from py_clob_client.clob_types import TradeParams
        trades = client.get_trades(TradeParams(maker_address=MY_ADDRESS)) or []
    except Exception as e:
        log(f"WARN get_trades failed: {e}"); return
    if isinstance(trades, dict):
        trades = trades.get("data", [])
    for t in trades[-50:]:
        tid = t.get("id") or t.get("transactionHash")
        if not tid or tid in seen_trades:
            continue
        if str(t.get("trader_side", "MAKER")).upper() != "MAKER":
            continue
        asset = str(t.get("asset_id") or t.get("token_id") or "")
        if asset not in (cur_tokens["up"], cur_tokens["down"]):
            continue
        seen_trades.add(tid)
        outcome = "UP" if asset == cur_tokens["up"] else "DOWN"
        ledger.on_fill(outcome, float(t.get("price", 0)), float(t.get("size", 0)))
        log(f"FILL {outcome} {t.get('size')}@{t.get('price')} id={tid[:10]}")


def append_round_row(row):
    os.makedirs(LOG_DIR, exist_ok=True)
    p = os.path.join(LOG_DIR, "bot_rounds.csv")
    new = not os.path.exists(p)
    cols = ["mode", "label", "round_start", "day", "n_quotes", "n_fills", "buys", "sells", "net_shares", "notional",
            "avg_buy", "avg_sell", "fair_mean_at_fill", "settled", "outcome", "condition_id", "label_source",
            "pnl_usd", "adv_sel_edge", "model_cert_hit", "model_cert_n"]
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


async def main_loop():
    cur_start = None
    while True:
        try:
            ledger.new_day_check()
            if os.path.exists(KILL_FILE):
                if resting.up_id or resting.down_id:
                    clear_quotes("kill"); log("KILL file present - all orders cancelled, standing by")
                await asyncio.sleep(1); continue
            if ledger.halted:
                await asyncio.sleep(5); continue
            now = time.time()
            start = int(now // ROUND_T) * ROUND_T
            t_rem = start + ROUND_T - now
            if start != cur_start:
                if cur_start is not None:
                    clear_quotes("round"); poll_fills()
                    prs = br.round_state(300, "5m", cur_start)
                    p_end = br.state.model.p_up(prs["o_twap"], 0) if prs["o_twap"] is not None else None
                    outcome = "" if p_end is None else ("up" if p_end >= 0.5 else "down")
                    ledger.close_round(p_end if p_end is not None else 0.5)
                    tok = br.state.tokens.get(("5m", cur_start))
                    buys = [f for f in ledger.fills if f[0] == "UP"]; sells = [f for f in ledger.fills if f[0] == "DOWN"]
                    log(f"ROUND END {cur_start}: quotes={ledger.round_quotes} fills={ledger.round_fills} "
                        f"up={ledger.up:.0f} down={ledger.down:.0f} model_end={outcome} day_pnl={ledger.realized:+.2f}")
                    append_round_row(dict(mode="live" if not DRY_RUN else "dryrun", label="5m", round_start=cur_start,
                                          day=datetime.fromtimestamp(cur_start, timezone.utc).strftime("%Y-%m-%d"),
                                          n_quotes=ledger.round_quotes, n_fills=ledger.round_fills,
                                          buys=len(buys), sells=len(sells), net_shares=round(ledger.net, 1),
                                          notional=round(ledger.cost, 2),
                                          avg_buy=round(sum(f[1] for f in buys) / len(buys), 4) if buys else "",
                                          avg_sell=round(sum(f[1] for f in sells) / len(sells), 4) if sells else "",
                                          settled="", outcome=outcome, condition_id=(tok[1] if tok else ""),
                                          label_source="model-end", pnl_usd="", adv_sel_edge=""))
                ledger.new_round(start); cur_start = start
                log(f"OPEN 5m {start} (t_rem={t_rem:.0f}s)")
            rs = br.round_state(300, "5m", start)
            if t_rem < NO_QUOTE:
                clear_quotes("round"); poll_fills()
                p = br.state.model.p_up(rs["o_twap"], t_rem) if rs["o_twap"] is not None else None
                if p is not None and ledger.mark(p) < -DAILY_STOP_USD:
                    ledger.halted = True; clear_quotes("daily_stop")
                    log(f"DAILY STOP hit ({ledger.mark(p):.2f} USDC) - halted until next UTC day")
                await asyncio.sleep(1); continue
            if rs["o_twap"] is None:
                await asyncio.sleep(0.5); continue
            fair = br.state.model.p_up(rs["o_twap"], t_rem)
            if fair is None:
                await asyncio.sleep(0.5); continue
            if not (FAIR_LO <= fair <= FAIR_HI):
                clear_quotes("band"); await asyncio.sleep(0.5); continue
            tok = br.get_tokens("5m", start)
            if tok and tok[0]:
                cur_tokens["up"], cur_tokens["down"] = tok[0][0], tok[0][1]
                sync_quotes(fair, tok[0][0], tok[0][1])
            poll_fills()
            if ledger.mark(fair) < -DAILY_STOP_USD:
                ledger.halted = True; clear_quotes("daily_stop")
                log(f"DAILY STOP hit ({ledger.mark(fair):.2f} USDC) - halted until next UTC day")
            await asyncio.sleep(1.0)
        except Exception as e:
            log(f"WARN loop error: {e}")
            await asyncio.sleep(2)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log(f"maker_bot v2 starting - mode={'DRY RUN' if DRY_RUN else 'LIVE'} size={QUOTE_SIZE}/side cap={MAX_NET_SHARES} "
        f"stop=${DAILY_STOP_USD}/day tick={TICK} kill_file={KILL_FILE}")
    if not DRY_RUN:
        init_client()
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.create_task(br.rtds()); loop.create_task(br.watchdog()); loop.create_task(main_loop())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        clear_quotes("kill"); log("stopped by user; all quotes cancelled")


if __name__ == "__main__":
    main()
