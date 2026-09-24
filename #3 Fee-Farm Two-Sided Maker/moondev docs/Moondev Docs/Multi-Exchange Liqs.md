# Multi-Exchange Liqs

> Source: https://moondev.com/docs#multi-exchange-liqs · captured 2026-09-23

## Multi-Exchange Liquidations

Real-time liquidation data from multiple exchanges: **Binance** , **Bybit** , **OKX** , and **HyperLiquid**. Get individual exchange data or combined aggregated stats across all exchanges.

#### Update Frequency

  * **Live endpoints** (10m - 5d): Updated every **30 seconds**
  * **Archive endpoints** (7d - 30d): Updated every **15 minutes**

### Combined (All Exchanges) - Live

Endpoint| Description  
---|---  
GET /api/all_liquidations/stats.json| Combined stats from all exchanges  
GET /api/all_liquidations/10m.json| Last 10 minutes  
GET /api/all_liquidations/1h.json| Last 1 hour  
GET /api/all_liquidations/4h.json| Last 4 hours  
GET /api/all_liquidations/12h.json| Last 12 hours  
GET /api/all_liquidations/24h.json| Last 24 hours  
GET /api/all_liquidations/2d.json| Last 2 days  
GET /api/all_liquidations/5d.json| Last 5 days  
  
### Combined (All Exchanges) - Archive

Endpoint| Description  
---|---  
GET /api/all_liquidations/7d.json| Last 7 days (15-min updates)  
GET /api/all_liquidations/14d.json| Last 14 days (15-min updates)  
GET /api/all_liquidations/30d.json| Last 30 days (15-min updates)  
  
### Rolling Totals (Long vs Short), all windows

One call returns the combined long and short liquidation split across all four exchanges for six rolling windows: **5m, 15m, 1h, 2h, 3h, 4h**. Every window carries `long_volume_usd` / `long_count` and `short_volume_usd` / `short_count`, and the same split repeats inside each exchange under `by_exchange` (binance, bybit, okx, hyperliquid). Standard key. Updated every **20 seconds**.

Endpoint| Description  
---|---  
GET /api/all_liquidations/totals.json| Long vs short totals across all exchanges, six rolling windows (5m to 4h)  
  
### Response Fields (totals.json)

Field| Type| Description  
---|---|---  
windows| object| Keyed by window: 5m, 15m, 1h, 2h, 3h, 4h  
total_volume_usd| number| Total liquidated USD in the window (long plus short)  
total_count| number| Total liquidation events in the window  
long_volume_usd| number| USD of long liquidations  
long_count| number| Number of long liquidations  
short_volume_usd| number| USD of short liquidations  
short_count| number| Number of short liquidations  
by_exchange| object| Same six fields, one entry per exchange  
generated_at| string| ISO 8601 UTC timestamp of the reading  
note| string| Plain text summary of what the file contains  
  
### Example Request

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/all_liquidations/totals.json
    
    # or with the key as a query parameter
    curl "https://api.moondev.com/api/all_liquidations/totals.json?api_key=YOUR_KEY"

### Response Example (all_liquidations/totals.json)

    {
      "generated_at": "2026-07-20T19:47:02Z",
      "note": "Total liquidations (USD amount + count) across binance, bybit, okx, hyperliquid.",
      "windows": {
        "5m": {
          "total_volume_usd": 247147,
          "total_count": 112,
          "long_volume_usd": 166096,
          "long_count": 78,
          "short_volume_usd": 81051,
          "short_count": 34,
          "by_exchange": {
            "binance": {
              "volume_usd": 103379,
              "count": 56,
              "long_volume_usd": 78161,
              "long_count": 38,
              "short_volume_usd": 25217,
              "short_count": 18
            },
            "bybit":       { "...": "same shape" },
            "okx":         { "...": "same shape" },
            "hyperliquid": { "...": "same shape" }
          }
        },
        "15m": { "...": "same shape" },
        "1h":  { "...": "same shape" },
        "2h":  { "...": "same shape" },
        "3h":  { "...": "same shape" },
        "4h":  { "...": "same shape" }
      }
    }

### Binance Liquidations

Endpoint| Description  
---|---  
GET /api/binance_liquidations/stats.json| Binance stats (24h volume, counts, top coins)  
GET /api/binance_liquidations/10m.json| Last 10 minutes  
GET /api/binance_liquidations/1h.json| Last hour  
GET /api/binance_liquidations/24h.json| Last 24 hours  
GET /api/binance_liquidations/7d.json| Last 7 days  
GET /api/binance_liquidations/30d.json| Last 30 days  
  
### Bybit Liquidations

Endpoint| Description  
---|---  
GET /api/bybit_liquidations/stats.json| Bybit stats (24h volume, counts, top coins)  
GET /api/bybit_liquidations/10m.json| Last 10 minutes  
GET /api/bybit_liquidations/1h.json| Last hour  
GET /api/bybit_liquidations/24h.json| Last 24 hours  
GET /api/bybit_liquidations/7d.json| Last 7 days  
GET /api/bybit_liquidations/30d.json| Last 30 days  
  
### OKX Liquidations

Endpoint| Description  
---|---  
GET /api/okx_liquidations/stats.json| OKX stats (24h volume, counts, top coins)  
GET /api/okx_liquidations/10m.json| Last 10 minutes  
GET /api/okx_liquidations/1h.json| Last hour  
GET /api/okx_liquidations/24h.json| Last 24 hours  
GET /api/okx_liquidations/7d.json| Last 7 days  
GET /api/okx_liquidations/30d.json| Last 30 days  
  
### Response Example (all_liquidations/stats.json)

    {
      "updated_at": "2026-01-09T12:00:00Z",
      "exchanges": ["binance", "bybit", "okx"],
      "combined": {
        "total_liquidations": 45000,
        "total_value_usd": 380000000,
        "long_liquidations": {
          "count": 25000,
          "value_usd": 215000000
        },
        "short_liquidations": {
          "count": 20000,
          "value_usd": 165000000
        }
      },
      "by_exchange": {
        "binance": {"count": 18000, "value_usd": 165000000},
        "bybit": {"count": 15000, "value_usd": 125000000},
        "okx": {"count": 12000, "value_usd": 90000000}
      },
      "top_liquidated_coins": [
        {"symbol": "BTC", "value_usd": 125000000, "count": 9500},
        {"symbol": "ETH", "value_usd": 85000000, "count": 8200},
        {"symbol": "SOL", "value_usd": 45000000, "count": 5100}
      ]
    }

### Response Example (binance_liquidations/1h.json)

    {
      "exchange": "binance",
      "timeframe": "1h",
      "generated_at": "2026-01-09T12:00:00Z",
      "stats": {
        "total_count": 850,
        "long_count": 480,
        "short_count": 370,
        "total_value_usd": 8500000,
        "long_value_usd": 5200000,
        "short_value_usd": 3300000
      },
      "liquidations": [
        {
          "symbol": "BTCUSDT",
          "side": "SELL",
          "quantity": 0.15,
          "price": 42500.00,
          "value_usd": 6375.00,
          "timestamp": "2026-01-09T11:55:00Z"
        }
      ]
    }

### Python Example - Multi-Exchange

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    headers = {"X-API-Key": API_KEY}
    
    # Get combined stats from ALL exchanges
    all_stats = requests.get(
        f"{BASE_URL}/api/all_liquidations/stats.json",
        headers=headers
    ).json()
    
    print("=== COMBINED LIQUIDATIONS (All Exchanges) ===")
    print(f"Total Volume: ${all_stats['combined']['total_value_usd']:,.0f}")
    print(f"Longs Rekt:   ${all_stats['combined']['long_liquidations']['value_usd']:,.0f}")
    print(f"Shorts Rekt:  ${all_stats['combined']['short_liquidations']['value_usd']:,.0f}")
    
    print("\n=== BY EXCHANGE ===")
    for exchange, data in all_stats['by_exchange'].items():
        print(f"{exchange.upper()}: {data['count']:,} liquidations, ${data['value_usd']:,.0f}")
    
    # Get individual exchange data
    for exchange in ["binance", "bybit", "okx"]:
        hourly = requests.get(
            f"{BASE_URL}/api/{exchange}_liquidations/1h.json",
            headers=headers
        ).json()
        print(f"\n{exchange.upper()} Last Hour: {hourly['stats']['total_count']} liquidations")

Note: `side: "SELL"` = Long position liquidated, `side: "BUY"` = Short position liquidated.

Cross-exchange liquidation data in one API call. [Get your API key here.](<https://moondev.com/t/docs>)
