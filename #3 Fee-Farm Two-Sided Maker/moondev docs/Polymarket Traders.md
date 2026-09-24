# Polymarket Traders

> Source: https://moondev.com/docs#poly-traders · captured 2026-09-23

## Polymarket Profitable Traders

NEWFollow the smart money in prediction markets. Moon Dev's backend continuously scans Polymarket and surfaces the wallets actually making money, ranked by 7-day P&L.

### Overview

Field| Value  
---|---  
Endpoint| GET /api/poly/profitable-traders  
SDK method| MoonDevAPI().get_poly_profitable_traders()  
Example| examples/34_polymarket_traders.py  
Auth| API key required (MOONDEV_API_KEY)  
Parameters| None  
  
### How traders are discovered

The service finds profitable traders from two live discovery sources, then keeps only wallets clearing a **$300+ 7-day P &L** threshold:

  * BTC 5m (`source: "btc_5m"`) — traders active in BTC 5-minute prediction markets.
  * Trending (`source: "trending"`) — traders making big trades ($500+) in trending markets.

Results are always sorted by **7-day P &L, highest first**.

### Endpoints

Method| Endpoint| Auth| Description  
---|---|---|---  
GET| /api/poly/profitable-traders| Yes| Profitable Polymarket traders sorted by 7-day P&L  
GET| /api/poly/health| No| Polymarket service health check  
  
### Access Tiers

Tier| API Key| What You Get  
---|---|---  
Standard| regular key| Top 25 traders  
Quant Elite| _qe key| Full list of all profitable traders  
  
The `full_list` field in the response confirms which tier you received (`true` = full Quant Elite list, `false` = standard top 25).

### cURL

    curl "https://api.moondev.com/api/poly/profitable-traders?api_key=YOUR_API_KEY"

### Python (SDK)

    from api import MoonDevAPI
    
    api = MoonDevAPI()  # reads MOONDEV_API_KEY from .env
    data = api.get_poly_profitable_traders()
    
    for trader in data["traders"]:
        print(f"{trader['wallet']}: ${trader['pnl_7d']:,.2f} P&L | ${trader['volume_7d']:,.2f} volume")
        print(f"  Profile: {trader['polymarket_link']}")

### Python (requests)

    import requests
    
    response = requests.get(
        "https://api.moondev.com/api/poly/profitable-traders",
        headers={"X-API-Key": "YOUR_API_KEY"}
    )
    data = response.json()
    
    for trader in data["traders"]:
        print(f"{trader['wallet']}: ${trader['pnl_7d']:,.2f} P&L ({trader['trades_7d']} trades)")
        print(f"  Profile: {trader['polymarket_link']}")

### JavaScript

    const response = await fetch(
      "https://api.moondev.com/api/poly/profitable-traders",
      { headers: { "X-API-Key": "YOUR_API_KEY" } }
    );
    const data = await response.json();
    
    data.traders.forEach(t => {
      const short = `${t.wallet.slice(0,6)}...${t.wallet.slice(-4)}`;
      console.log(`${short}: $${t.pnl_7d.toFixed(2)} P&L (${t.trades_7d} trades)`);
    });

### Response Format

    {
      "total": 2894,
      "full_list": true,
      "updated_at": "2026-04-14T15:41:42.182139+00:00",
      "stats": {
        "wallets_checked": 1163,
        "queue_depth": 0,
        "uptime_minutes": 5.3
      },
      "traders": [
        {
          "wallet": "0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3",
          "polymarket_link": "https://polymarket.com/0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3",
          "pnl_7d": 1183556.88,
          "volume_7d": 1376544.64,
          "trades_7d": 470,
          "redeems_7d": 1,
          "discovered_at": "2026-04-13T22:50:05.965342+00:00",
          "source": "trending"
        }
      ]
    }

### Top-Level Fields

Field| Type| Description  
---|---|---  
total| integer| Number of traders returned in this response  
full_list| boolean| true for Quant Elite keys, false for standard keys (top 25)  
updated_at| string| ISO 8601 timestamp of when this response was generated  
stats| object| Service health metrics  
traders| array| Profitable traders sorted by pnl_7d descending  
  
### Stats Object

Field| Type| Description  
---|---|---  
wallets_checked| integer| Total wallets analyzed since service start  
queue_depth| integer| Wallets waiting to be analyzed (0 = fully caught up)  
uptime_minutes| float| Service uptime in minutes  
  
### Trader Object

Field| Type| Description  
---|---|---  
wallet| string| Ethereum wallet address (0x...) — use this as the identifier  
polymarket_link| string| Direct link to Polymarket profile (wallet-based)  
pnl_7d| float| 7-day profit & loss in USD. Minimum $300  
volume_7d| float| 7-day trading volume in USD  
trades_7d| integer| Number of trades in last 7 days  
redeems_7d| integer| Number of winning redemptions in last 7 days  
discovered_at| string| ISO 8601 timestamp of when wallet was discovered  
source| string| Discovery source: "trending" or "btc_5m"  
  
### Breaking Changes (2026-04-14)

If you integrated before 2026-04-14, update your code:

Change| Before| After  
---|---|---  
name field| Present (Polymarket pseudonym)| Removed — was unreliable  
display_name field| Present| Removed  
polymarket_link| https://polymarket.com/@Username| https://polymarket.com/<wallet>  
  
**Migration:** Replace any usage of `name` or `display_name` with `wallet`. The wallet address is now the only stable identifier, and `polymarket_link` is wallet-based so it will never break due to username changes.

### Data Freshness

Metric| Frequency  
---|---  
Scanner discovery| Every 5 minutes  
P&L recalculation| Continuous (5 worker threads)  
Seen wallets reset| Every 6 hours (fresh re-evaluation)  
JSON snapshot to disk| Every 60 seconds  
  
### How It Works

  1. BTC 5-Min Scanner — Every 5 min, scans the 3 most recently closed BTC up/down prediction markets and discovers all trading wallets
  2. Trending Markets Scanner — Every 5 min, fetches top 50 markets by 24h volume, filters for trades where size x price >= $500
  3. P&L Workers (5 threads) — Fetches 7-day activity per wallet, computes P&L = sells + redemptions - buys
  4. Filter — Only traders with P&L >= $300 are included
  5. Persistence — Results survive restarts via periodic JSON snapshots

### Use Cases

  * Copy-trade profitable Polymarket traders (follow the wallet)
  * Identify sharp money in prediction markets
  * Track consistent winners on BTC 5-min markets
  * Monitor whale activity on trending markets
  * Build alerts when new highly-profitable wallets are discovered

### Run the example

    python examples/34_polymarket_traders.py

The example renders a full terminal dashboard: a service-stats panel, a detailed **Top 10** leaderboard, a compact full list, summary stat cards (combined P&L, combined volume, total trades, and a discovery-source breakdown), and a footer.

### Related Polymarket endpoints

  * `GET /api/poly/whales` — live whale trade log ($1,000+ fills, newest first)
  * `GET /api/poly/whales/top-traders` — whale leaderboard by wallet
  * `GET /api/poly/whales/top-markets` — whale leaderboard by market
  * `GET /api/poly/whales/daily` — per-day whale activity rollup (charting)
  * `GET /api/poly/whales/health` — whale ingestion status (no auth)
  * `GET /api/poly/health` — Polymarket service health (no auth)
