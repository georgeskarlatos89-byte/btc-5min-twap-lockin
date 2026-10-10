#!/usr/bin/env python3
"""
maker_bot.py (v2) - OPEN-BOOK-MAKER live quoting bot for Polymarket BTC 5-minute rounds

WHAT IT DOES: posts a BUY on the Up token at fair - 1.5c and a BUY on the Down token at
(1 - fair) - 1.5c, re-quotes when fair drifts, cancels before the final 20 s.

SAFETY (all enforced in code)
  * DRY RUN BY DEFAULT (.env DRY_RUN=true). .env IS loaded (python-dotenv).
  * KILL FILE: create it -> every resting order cancelled within ~1 s, standby until removed.
  * DAILY STOP: realized + marked loss beyond DAILY_STOP_USD -> cancel all, halt until next UTC day.
    `realized` is carried across rounds (booked at the model's end value = the settlement rule).
  * POST-ONLY twice: the bot skips a bid that would cross the touch AND the order is posted
    with post_only=True so the exchange rejects it instead of taking.
  * Fills reconciled from the CLOB trade list (TradeParams(maker_address=funder), MAKER side,
    this round's tokens only, idempotent by trade id).
  * Cancels target OUR order ids; cancel_all only for the kill file / daily stop.
  * Prices on the venue tick (0.01); size LIVE_QUOTE_SIZE; inventory cap MAX_NET_SHARES.
  * Telegram (notify.py, rate-limited): start, balance, every fill, round summaries hourly,
    order errors, daily stop, kill.

SIGNING: the account-2 path proven by the live btc5bot and S1 preflight:
  py_clob_client_v2, signature_type=3 (POLY_1271), funder = the Polymarket proxy that holds
  the pUSD. .env keys: POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE (file-to-file copy
  from s1_harness/.env; never through chat). Falls back to py_clob_client (sig type 2) if v2
  is not installed.

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
import notify

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
MODE = "DRY RUN" if DRY_RUN else "LIVE"


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
        self.fills = []
        self.day_rounds = 0; self.day_fills = 0; self.day_quotes = 0; self.day_errors = 0

    @property
    def net(self): return self.up - self.down

    def new_day_check(self):
        d = datetime.now(timezone.utc).date()
        if d != self.day:
            if self.day is not None:
                notify.send(f"UTC day {self.day} closed: rounds {self.day_rounds}, quotes {self.day_quotes}, "
                            f"fills {self.day_fills}, booked P&L {self.realized:+.2f} USDC, order errors {self.day_errors}")
            self.day, self.realized, self.halted = d, 0.0, False
            self.day_rounds = self.day_fills = self.day_quotes = self.day_errors = 0
            log(f"NEW UTC DAY {d} - loss counter reset")

    def on_fill(self, outcome, price, size):
        if outcome == "UP": self.up += size
        else: self.down += size
        self.cost += price * size; self.round_fills += 1; self.day_fills += 1
        self.fills.append((outcome, price, size))

    def mark(self, fair):
        return self.realized + self.up * fair + self.down * (1 - fair) - self.cost

    def close_round(self, p_end):
        if self.round_start is not None:
            self.realized += self.up * p_end + self.down * (1 - p_end) - self.cost
            self.day_rounds += 1

    def new_round(self, start):
        self.round_start = start
        self.up = self.down = self.cost = 0.0
        self.round_quotes = 0; self.round_fills = 0; self.fills = []


ledger = Ledger()

# ---------------------------------------------------------------- CLOB client
client = None
MY_ADDRESS = None
V2 = False


def init_client():
    """account-2 proxy signing path (py_clob_client_v2, sig type 3, funder=proxy)."""
    global client, MY_ADDRESS, V2
    if DRY_RUN:
        return
    pk = env("POLY_PRIVATE_KEY") or env("POLYMARKET_PRIVATE_KEY")
    funder = env("POLY_FUNDER") or env("POLYMARKET_PROXY_ADDRESS") or None
    if not pk:
        raise SystemExit("LIVE mode requires POLY_PRIVATE_KEY in .env")
    if not funder:
        raise SystemExit("LIVE mode requires POLY_FUNDER (the proxy that holds the pUSD) in .env")
    try:
        from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType
        sig = int(env("POLY_SIGNATURE_TYPE", "3"))
        creds = ClobClient(host=CLOB_HOST, key=pk, chain_id=137).create_or_derive_api_key()
        client = ClobClient(host=CLOB_HOST, key=pk, chain_id=137, creds=creds, signature_type=sig, funder=funder)
        V2 = True
        try:
            client.update_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            bal = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            usd = float(bal.get("balance", 0)) / 1e6 if isinstance(bal, dict) else None
        except Exception as ex:
            usd = None; log(f"balance check skipped: {ex!r}")
    except ImportError:
        from py_clob_client.client import ClobClient
        sig = int(env("POLY_SIGNATURE_TYPE") or env("POLYMARKET_SIG_TYPE") or "2")
        client = ClobClient(CLOB_HOST, key=pk, chain_id=137, signature_type=sig, funder=funder)
        client.set_api_creds(client.create_or_derive_api_creds()); usd = None
    MY_ADDRESS = funder.lower()
    log(f"CLOB client ready (LIVE, {'v2' if V2 else 'v1'} client, sig_type={sig}) funder=...{funder[-6:]} balance={usd}")
    notify.send(f"LIVE mode started. Signing path {'py_clob_client_v2 sig3' if V2 else 'py_clob_client'}, "
                f"funder ...{funder[-6:]}, CLOB balance {usd if usd is None else round(usd, 2)} USDC, "
                f"size {QUOTE_SIZE} sh/side (~${QUOTE_SIZE / 2:.0f}), cap {MAX_NET_SHARES} sh, daily stop ${DAILY_STOP_USD}", force=True)


def clob_balance():
    """collateral balance in USDC as the CLOB ledger sees it (None in DRY / on error)."""
    if DRY_RUN or client is None or not V2:
        return None
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        bal = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return round(float(bal.get("balance", 0)) / 1e6, 2)
    except Exception:
        return None


def _log_order(rec):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "orders.jsonl"), "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


_err_count = 0


def place_buy(token_id, price, size, tag):
    """post-only GTC BUY. -> order id, 'dry', or None on error."""
    global _err_count
    rec = dict(ts=now_iso(), action="quote", mode="dry" if DRY_RUN else "live",
               side=f"BUY-{tag}", token=token_id[-12:], price=round(price, 2), size=size)
    if DRY_RUN:
        _log_order(rec); return "dry"
    try:
        if V2:
            from py_clob_client_v2 import OrderArgs, OrderType
            from py_clob_client_v2.order_builder.constants import BUY
            order = client.create_order(OrderArgs(token_id=token_id, price=round(price, 2), size=size, side=BUY))
            resp = client.post_order(order, order_type=OrderType.GTC, post_only=True)
        else:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
            resp = client.post_order(client.create_order(OrderArgs(price=round(price, 2), size=size, side=BUY, token_id=token_id)), OrderType.GTC)
        if not isinstance(resp, dict):
            resp = {"raw": str(resp)[:200]}
        rec["order_id"] = resp.get("orderID") or resp.get("orderId") or resp.get("id")
        rec["ok"] = bool(resp.get("success", bool(rec["order_id"])))
        if resp.get("errorMsg"):
            rec["error"] = str(resp.get("errorMsg"))[:200]
        _log_order(rec)
        if not rec["ok"]:
            _err_count += 1; ledger.day_errors += 1
            log(f"ORDER REJECTED {tag} {price} x{size}: {rec.get('error') or resp}")
            if _err_count in (1, 5, 20, 100):
                notify.send(f"order rejected ({_err_count} so far): {tag} {price} x{size}: {rec.get('error') or str(resp)[:160]}")
            return None
        return rec["order_id"]
    except Exception as e:
        rec["error"] = str(e)[:200]; _log_order(rec)
        _err_count += 1; ledger.day_errors += 1
        log(f"ORDER ERROR {tag}: {e!r}"[:300])
        if _err_count in (1, 5, 20, 100):
            notify.send(f"order error ({_err_count} so far): {tag} {price} x{size}: {str(e)[:160]}")
        return None


def cancel_order(order_id, tag):
    if DRY_RUN or not order_id or order_id == "dry":
        return
    try:
        if V2:
            client.cancel_orders([order_id])
        else:
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
            if tag == "UP": resting.up_px, resting.up_id = (want, oid) if oid else (None, None)
            else: resting.down_px, resting.down_id = (want, oid) if oid else (None, None)
            ledger.round_quotes += 1; ledger.day_quotes += 1


seen_fills = set()
cur_tokens = {"up": "", "down": ""}
round_tokens = {}      # round_start -> {"up": token, "down": token, "p_end": float|None}


def fetch_trades():
    if V2:
        from py_clob_client_v2 import TradeParams
        trades = client.get_trades(TradeParams(maker_address=MY_ADDRESS), only_first_page=True) or []
    else:
        from py_clob_client.clob_types import TradeParams
        trades = client.get_trades(TradeParams(maker_address=MY_ADDRESS)) or []
    if isinstance(trades, dict):
        trades = trades.get("data", [])
    return trades


def our_fills(trades):
    """-> [(key, asset_id, price, size)] for OUR maker entries. The top-level trade record
       is the TAKER's (its asset/price/size are theirs); our fill is the maker_orders[] entry
       whose maker_address is our funder."""
    out = []
    for t in trades:
        tid = t.get("id") or t.get("transactionHash") or ""
        for mo in t.get("maker_orders", []) or []:
            if str(mo.get("maker_address", "")).lower() != MY_ADDRESS:
                continue
            key = f"{tid}:{mo.get('order_id')}:{mo.get('matched_amount')}"
            try:
                out.append((key, str(mo.get("asset_id") or ""), float(mo.get("price", 0)), float(mo.get("matched_amount", 0))))
            except (TypeError, ValueError):
                continue
    return out


def seed_seen_fills():
    """at start: mark every existing fill as seen so old trades are never booked."""
    if DRY_RUN or client is None:
        return
    try:
        n = 0
        for key, *_ in our_fills(fetch_trades()):
            seen_fills.add(key); n += 1
        log(f"fill reconciliation seeded with {n} earlier fill(s) (ignored)")
    except Exception as e:
        log(f"WARN seeding fills failed: {e}")


def poll_fills():
    """LIVE: reconcile our maker fills since the last poll (idempotent by trade+order key).
       Fills reported after the round rolled over are booked straight into `realized`."""
    if DRY_RUN or client is None:
        return
    try:
        trades = fetch_trades()
    except Exception as e:
        log(f"WARN get_trades failed: {e}"); return
    for key, asset, px, sz in our_fills(trades):
        if key in seen_fills or sz <= 0:
            continue
        seen_fills.add(key)
        if asset in (cur_tokens["up"], cur_tokens["down"]):
            outcome = "UP" if asset == cur_tokens["up"] else "DOWN"
            ledger.on_fill(outcome, px, sz)
            log(f"FILL {outcome} {sz:g}@{px:.2f} key={key[:10]}")
            notify.send(f"FILL bought {outcome} {sz:g} sh @ {px:.2f} (round {ledger.round_start}); round inventory up {ledger.up:g} / down {ledger.down:g}; day P&L booked {ledger.realized:+.2f}")
            continue
        for rstart, rt in round_tokens.items():
            if asset in (rt["up"], rt["down"]):
                outcome = "UP" if asset == rt["up"] else "DOWN"
                p = rt["p_end"] if rt["p_end"] is not None else 0.5
                pnl = (p - px) * sz if outcome == "UP" else ((1 - p) - px) * sz
                ledger.realized += pnl; ledger.day_fills += 1
                log(f"LATE FILL {outcome} {sz:g}@{px:.2f} for round {rstart}: booked {pnl:+.2f} at model end {p:.2f}")
                notify.send(f"late FILL bought {outcome} {sz:g} sh @ {px:.2f} for round {rstart}, booked {pnl:+.2f}")
                break
        else:
            log(f"fill for an unknown token ignored: {asset[-10:]} {sz:g}@{px:.2f}")


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
    cur_start = None; last_hourly = time.time(); kill_notified = False
    while True:
        try:
            ledger.new_day_check()
            if os.path.exists(KILL_FILE):
                if resting.up_id or resting.down_id or not kill_notified:
                    clear_quotes("kill"); log("KILL file present - all orders cancelled, standing by")
                    if not kill_notified:
                        notify.send("KILL file present: all orders cancelled, standing by until it is removed", force=True)
                    kill_notified = True
                await asyncio.sleep(1); continue
            if kill_notified:
                kill_notified = False; log("KILL file removed - resuming"); notify.send("KILL file removed - resuming", force=True)
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
                    if tok:
                        round_tokens[cur_start] = {"up": tok[0][0], "down": tok[0][1], "p_end": p_end}
                        for old in [k for k in round_tokens if k < cur_start - 3 * ROUND_T]:
                            round_tokens.pop(old, None)
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
            if time.time() - last_hourly >= 3600:
                last_hourly = time.time()
                notify.send(f"hourly ({MODE}): today rounds {ledger.day_rounds}, quotes {ledger.day_quotes}, fills {ledger.day_fills}, "
                            f"booked P&L {ledger.realized:+.2f} USDC, CLOB balance {clob_balance()}, order errors {ledger.day_errors}, "
                            f"feed age {time.time() - br.state.last_feed:.0f}s")
            rs = br.round_state(300, "5m", start)
            if t_rem < NO_QUOTE:
                clear_quotes("round"); poll_fills()
                p = br.state.model.p_up(rs["o_twap"], t_rem) if rs["o_twap"] is not None else None
                if p is not None and ledger.mark(p) < -DAILY_STOP_USD:
                    ledger.halted = True; clear_quotes("daily_stop")
                    log(f"DAILY STOP hit ({ledger.mark(p):.2f} USDC) - halted until next UTC day")
                    notify.send(f"DAILY STOP hit: {ledger.mark(p):+.2f} USDC. All orders cancelled, halted until the next UTC day.", force=True)
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
                notify.send(f"DAILY STOP hit: {ledger.mark(fair):+.2f} USDC. All orders cancelled, halted until the next UTC day.", force=True)
            await asyncio.sleep(1.0)
        except Exception as e:
            log(f"WARN loop error: {e!r}"[:300])
            await asyncio.sleep(2)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    log(f"maker_bot v2 starting - mode={MODE} size={QUOTE_SIZE}/side cap={MAX_NET_SHARES} "
        f"stop=${DAILY_STOP_USD}/day tick={TICK} kill_file={KILL_FILE}")
    notify.send(f"started in {MODE}. Two-sided fair-value maker on BTC 5-minute rounds: bids the Up and the Down token "
                f"1.5c below the model's fair value, re-quotes as fair moves, cancels 20 s before the end. "
                f"Size {QUOTE_SIZE:g} shares per side, inventory cap {MAX_NET_SHARES:g}, daily stop ${DAILY_STOP_USD:g}.", force=True)
    if not DRY_RUN:
        init_client(); seed_seen_fills()
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.create_task(br.rtds()); loop.create_task(br.watchdog()); loop.create_task(main_loop())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        clear_quotes("kill"); log("stopped by user; all quotes cancelled")


if __name__ == "__main__":
    main()
