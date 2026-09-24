# Trades & Order Flow

> Source: https://moondev.com/docs#trades · captured 2026-09-23

## Trades & Order Flow

Endpoint| Description  
---|---  
GET /api/trades.json| Recent trades (last 1000)  
GET /api/large_trades.json| Large trades only (>$100k)  
GET /api/orderflow.json| Current order flow metrics  
GET /api/orderflow/stats.json| Order flow statistics  
GET /api/imbalance/5m.json| Buy/sell imbalance - 5 minutes  
GET /api/imbalance/15m.json| Buy/sell imbalance - 15 minutes  
GET /api/imbalance/1h.json| Buy/sell imbalance - 1 hour  
GET /api/imbalance/4h.json| Buy/sell imbalance - 4 hours  
GET /api/imbalance/24h.json| Buy/sell imbalance - 24 hours  
  
Orderflow Expansion: Now tracking 130 symbols across all Hyperliquid coins!

Every coin on Hyperliquid is now tracked for buy/sell imbalance, enabling pairs trading discovery and cross-market analysis.

Data Retention Policy

  * **Raw trade data:** Retained for 90 days, then auto-deleted. Export if you need it longer!
  * **Hourly aggregates:** Kept permanently (forever) for historical backtesting. Per-symbol buy/sell pressure every hour.

### Response Example (trades.json)

    {
      "updated_at": "2026-01-06T19:17:00Z",
      "trade_count": 1000,
      "trades": [
        {
          "timestamp": 1767727000000,
          "coin": "BTC",
          "side": "buy",
          "size": 0.5,
          "price": 92000.0,
          "value_usd": 46000.0,
          "is_large": false
        }
      ]
    }

### Response Example (orderflow.json)

    {
      "updated_at": "2026-01-06T19:17:00Z",
      "window": "5m",
      "metrics": {
        "BTC": {
          "buy_volume": 5000000.0,
          "sell_volume": 4500000.0,
          "net_flow": 500000.0,
          "buy_count": 150,
          "sell_count": 140,
          "imbalance_ratio": 1.11
        }
      }
    }

### Response Example (imbalance/1h.json)

Now returns data for all 130 tracked symbols:

    {
      "time_window": "1h",
      "overall": {
        "buy_volume_usd": 923134333.04,
        "sell_volume_usd": 1146007719.76,
        "imbalance_ratio": -0.1077,
        "dominant_side": "SELL"
      },
      "by_coin": {
        "BTC": {
          "buy_volume_usd": 616856512.28,
          "sell_volume_usd": 814592126.43,
          "net_imbalance_usd": -197735614.15,
          "imbalance_ratio": -0.1381,
          "buy_count": 15420,
          "sell_count": 18350,
          "dominant_side": "SELL"
        },
        "ETH": { "..." : "..." },
        "SOL": { "..." : "..." },
        "ADA": { "..." : "..." },
        "EIGEN": { "..." : "..." },
        "...": "130 symbols total"
      }
    }

Each symbol includes:

  * `buy_volume_usd` / `sell_volume_usd` \- Volume in USD
  * `net_imbalance_usd` \- Net buy/sell imbalance
  * `imbalance_ratio` \- Ratio (-1.0 to 1.0, negative = sell pressure)
  * `buy_count` / `sell_count` \- Number of trades
  * `dominant_side` \- Either `BUY` or `SELL`

### Pairs Trading Example

With 130 symbols, you can compare imbalance across any two coins for pairs trading signals:

    import requests
    
    API_KEY = "your_api_key"
    
    r = requests.get(
        "https://api.moondev.com/api/imbalance/1h.json",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    # Compare any two coins for pairs trading
    ada = r["by_coin"]["ADA"]["imbalance_ratio"]
    eigen = r["by_coin"]["EIGEN"]["imbalance_ratio"]
    spread = ada - eigen  # Your entry signal!
    
    print(f"ADA imbalance: {ada:.4f}")
    print(f"EIGEN imbalance: {eigen:.4f}")
    print(f"Spread: {spread:.4f}")

### Exporting Raw Data (Before 90-Day Expiry)

Raw trade data is auto-deleted after 90 days. Pull and store locally if you need it longer:

    import requests, json
    
    API_KEY = "your_api_key"
    
    # Pull all recent trades and save locally
    trades = requests.get(
        "https://api.moondev.com/api/trades.json",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    with open("trades_backup.json", "w") as f:
        json.dump(trades, f)
    
    print(f"Saved {trades['trade_count']} trades")
