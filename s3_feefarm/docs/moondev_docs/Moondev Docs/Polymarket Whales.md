# Polymarket Whales

> Source: https://moondev.com/docs#poly-whales · captured 2026-09-23

## Polymarket Whales API

NEWLive whale tracker for Polymarket prediction markets. A background service maintains a persistent WebSocket to `wss://ws-live-data.polymarket.com` and records every trade with USD notional **≥ $1,000** into a database. Query by time window, dollar threshold, wallet, and market.

### Authentication & Rate Limits

Pass your key as `?api_key=YOUR_KEY` query param, `X-API-Key: YOUR_KEY` header, or `Authorization: Bearer YOUR_KEY`. Keys ending in `_qe` get the full result set; standard keys are capped per endpoint. Rate limit: **60 req/sec sustained, 200 burst** (shared across all API endpoints).

### Endpoints

Method| Endpoint| Auth| Description  
---|---|---|---  
GET| /api/poly/whales| Yes| Recent whale trade log (newest first)  
GET| /api/poly/whales/top-traders| Yes| Leaderboard by wallet over the window  
GET| /api/poly/whales/top-markets| Yes| Leaderboard by market over the window  
GET| /api/poly/whales/daily| Yes| Per-day OHLCV-style rollup of whale activity  
GET| /api/poly/whales/health| No| Service status & live ingestion counters  
  
### GET /api/poly/whales — recent whale trade log

Returns individual whale fills, newest first.

Param| Default| Notes  
---|---|---  
min_usd| 1000| Minimum trade size in USD. Floor is $1,000 — anything below is never collected.  
days| 1| Lookback window. Max 365.  
wallet| —| Filter to a single wallet address (proxyWallet).  
market| —| Filter by market_slug or event_slug.  
side| —| BUY or SELL.  
limit| 250| Max rows. Standard keys capped at 250, _qe keys up to 5,000.  
  
**Example:**

    curl "https://api.moondev.com/api/poly/whales?api_key=YOUR_API_KEY&min_usd=5000&days=7&side=BUY"

Each trade includes: `ts`, `wallet`, `pseudonym`, `market_title`, `market_slug`, `event_slug`, `outcome`, `side`, `price`, `size`, `usd_amount`, `tx_hash`.

### GET /api/poly/whales/top-traders — leaderboard by wallet

Aggregates whale trades per wallet over the window.

Param| Default| Notes  
---|---|---  
min_usd| 1000|   
days| 7|   
limit| 100| Standard keys capped at 50, _qe up to 1,000.  
  
Returns per wallet: `trade_count`, `total_volume`, `biggest_trade`, `markets_traded`, `last_trade_ts`. Sorted by `total_volume` desc.

### GET /api/poly/whales/top-markets — leaderboard by market

Same shape as top-traders but grouped by market.

Param| Default  
---|---  
min_usd| 1000  
days| 7  
limit| 100  
  
Returns per market: `market_slug`, `event_slug`, `market_title`, `whale_trades`, `whale_volume`, `unique_whales`, `biggest_trade`.

### GET /api/poly/whales/daily — per-day rollup

OHLCV-style daily summary of whale activity. Use this for charting.

Param| Default  
---|---  
min_usd| 1000  
days| 30  
  
Returns one row per UTC day: `trade_count`, `total_volume`, `biggest_trade`, `smallest_trade`, `avg_trade`, `unique_whales`, `unique_markets`.

### GET /api/poly/whales/health — service status (no auth)

Live counters: `trades_seen`, `whales_queued`, `whales_written`, `biggest_usd`, `biggest_market`, `ws_connects`, `ws_disconnects`, `last_trade_at`, `queue_depth`, `uptime_minutes`. Useful for confirming the WebSocket is connected and ingestion is fresh.

### Python (Moon Dev SDK)

    from api import MoonDevAPI
    
    api = MoonDevAPI()
    
    whales      = api.get_poly_whales(min_usd=5000, days=7, side="BUY")
    top_wallets = api.get_poly_whale_top_traders(days=7)
    top_markets = api.get_poly_whale_top_markets(days=7)
    daily       = api.get_poly_whale_daily(days=30)
    status      = api.poly_whales_health()   # no auth required
    
    for trade in whales["trades"]:
        print(f"{trade['ts']} {trade['side']} ${trade['usd_amount']:,.0f} | {trade['market_title']}")
        print(f"  wallet: {trade['wallet']}  tx: {trade['tx_hash']}")

### Python (requests)

    import requests
    
    response = requests.get(
        "https://api.moondev.com/api/poly/whales",
        headers={"X-API-Key": "YOUR_API_KEY"},
        params={"min_usd": 5000, "days": 7, "side": "BUY"},
    )
    data = response.json()
    
    for trade in data["trades"]:
        print(f"{trade['ts']} {trade['side']} ${trade['usd_amount']:,.0f} | {trade['market_title']}")

### JavaScript

    const url = new URL("https://api.moondev.com/api/poly/whales");
    url.searchParams.set("min_usd", "5000");
    url.searchParams.set("days", "7");
    url.searchParams.set("side", "BUY");
    
    const response = await fetch(url, { headers: { "X-API-Key": "YOUR_API_KEY" } });
    const data = await response.json();
    
    data.trades.forEach(t => {
      console.log(`${t.ts} ${t.side} $${t.usd_amount.toLocaleString()} | ${t.market_title}`);
    });

### How It Works (Internals)

  * **Service:** `poly-whales.service` (systemd, auto-restart, 512 MB / 50% CPU caps). Code: `data-layer/scripts/poly_whales.py`. Internal port 8105, exposed via nginx behind Cloudflare.
  * **Ingestion:** subscribes to `{topic: "activity", type: "orders_matched"}`. Filters `price * size < $1,000` in-process before any DB write — cheapest possible reject.
  * **Storage:** SQLite at `data-layer/data/polymarket/whales.db`, WAL mode. Indexed on `ts`, `wallet`, `market_slug`, `event_slug`, `usd_amount`. Dedup via `dedup_key` UNIQUE (transaction hash when present, composite of `ts|wallet|asset_id|size|price` otherwise) — safe across WebSocket reconnects.
  * **Writes:** batched every 5 s or 500 rows, whichever comes first. Application-layer ping every 30 s keeps the WebSocket alive through Polymarket's proxies; reconnects after 5 s on drop.
  * **Storage projection:** ~400 bytes/row including indexes. At ~5,000 whale fills/day (busy day) that's 2 MB/day → ~700 MB/year.

### Relationship to /api/poly/profitable-traders

Completely separate service (port 8104, polling-based, focused on 7-day P&L). The whales endpoint is the live order-flow log; profitable-traders is the "who's actually making money" filter. **Cross-reference them by wallet address.**

### Use Cases

  * Build real-time alerts when whales open size in trending markets
  * Chart whale activity per day to spot conviction shifts
  * Stack-rank markets by whale dollar volume to find sharp interest early
  * Cross-reference whale wallets with profitable-traders to follow sharp money
  * Backtest signals where whale buys/sells lead price moves
