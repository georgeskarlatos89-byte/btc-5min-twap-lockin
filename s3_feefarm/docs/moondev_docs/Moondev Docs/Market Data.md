# Market Data

> Source: https://moondev.com/docs#market-data · captured 2026-09-23

## Market Data

Drop-in replacements for Hyperliquid API calls. All requests go through Moon Dev's local node.

These endpoints replace `metaAndAssetCtxs`, `l2Book`, `clearinghouseState`, `userFills`, and `candleSnapshot`.

### Prices (All 228 Coins)

Endpoint| Description  
---|---  
GET /api/prices| All prices, funding rates, and OI for 228 coins (replaces metaAndAssetCtxs)  
GET /api/price/{coin}| Quick single-coin price with bid/ask/spread (e.g., /api/price/BTC)  
  
### Orderbook

Endpoint| Description  
---|---  
GET /api/orderbook/{coin}| L2 orderbook with ~20 levels each side (replaces l2Book)  
  
### Account & Fills

Endpoint| Description  
---|---  
GET /api/account/{address}| Full account state for any wallet (replaces clearinghouseState)  
GET /api/fills/{address}?limit=N| Trade fills in Hyperliquid-compatible format (replaces userFills)  
  
#### Fills query parameters (added 2026-08-13)

Param| Type| Description  
---|---|---  
startTime| integer| Epoch-ms lower bound — same name and units as HyperLiquid's userFillsByTime, so code written against HL ports directly  
minutes| number, 0–4320| Relative form of the same window — only fills from the last N minutes  
limit| integer| Max fills to return (unchanged; combines with the window)  
  
Passing a time window makes every wallet equally fast (~50ms). See the User Data section for the full fast-lane note and the new `503 fills_scanner_busy` semantics.

⚠️ Window size is the performance dial. Small windows (≤60 min) are the hot path — ~50ms for any wallet. The maximum (`minutes=4320`, 3 days) is effectively a full-archive scan and can take 30–60s on a cold cache: it's a deep-history call, not a polling call. If you need deep history on a schedule, page through it in smaller windows.

### Candles (OHLCV)

Endpoint| Description  
---|---  
GET /api/candles/{coin}?interval=1h| OHLCV candles (intervals: 1m, 5m, 15m, 1h, 4h, 1d)  
  
All coin symbols are case-insensitive (e.g., BTC, btc, Ada all work).

Candle intervals: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. Optional query params: `start_time` and `end_time` (millisecond timestamps).

### Response Example (/api/prices)

    {
      "count": 228,
      "timestamp": "2026-01-14T12:00:00Z",
      "prices": {
        "BTC": "94500.50",
        "ETH": "3250.25",
        "SOL": "185.30"
      },
      "funding_rates": {
        "BTC": "0.0001",
        "ETH": "0.00008"
      },
      "open_interest": {
        "BTC": "125000.5",
        "ETH": "850000.2"
      }
    }

### Response Example (/api/price/BTC)

    {
      "coin": "BTC",
      "timestamp": "2026-01-29T15:37:44Z",
      "best_bid": "85324.0",
      "best_ask": "85325.0",
      "best_bid_size": "0.00036",
      "best_ask_size": "5.7609",
      "mid_price": "85324.5",
      "spread": "1.0",
      "spread_bps": 0.12
    }

### Response Example (/api/orderbook/BTC)

    {
      "symbol": "BTC",
      "best_bid": "94500.00",
      "best_ask": "94501.00",
      "mid_price": "94500.50",
      "spread_bps": "0.11",
      "bid_depth": 20,
      "ask_depth": 20,
      "levels": [
        [
          {"px": "94500.0", "sz": "2.5"},
          {"px": "94499.0", "sz": "1.2"}
        ],
        [
          {"px": "94501.0", "sz": "1.8"},
          {"px": "94502.0", "sz": "3.1"}
        ]
      ]
    }

### Response Example (/api/candles/BTC?interval=1h)

    {
      "coin": "BTC",
      "interval": "1h",
      "start_time": 1738166257000,
      "end_time": 1738252257000,
      "count": 72,
      "candles": [
        {
          "timestamp": 1738166400000,
          "open": "85234.5",
          "high": "85890.2",
          "low": "85100.0",
          "close": "85654.3",
          "volume": 150.5,
          "trades": 1234
        }
      ]
    }

### Migration Cheatsheet

Old Hyperliquid Call| New Moon Dev API  
---|---  
{"type": "metaAndAssetCtxs"}| GET /api/prices  
{"type": "l2Book", "coin": "BTC"}| GET /api/orderbook/BTC  
{"type": "clearinghouseState", "user": "0x..."}| GET /api/account/0x...  
{"type": "userFills", "user": "0x..."}| GET /api/fills/0x...  
{"type": "candleSnapshot", ...}| GET /api/candles/BTC?interval=1h  
  
### Python Example

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    headers = {"X-API-Key": API_KEY}
    
    # Get all prices (replaces metaAndAssetCtxs)
    prices = requests.get(f"{BASE_URL}/api/prices", headers=headers).json()
    print(f"BTC: ${prices['prices']['BTC']}")
    print(f"Loaded {prices['count']} coins")
    
    # Quick price check
    btc = requests.get(f"{BASE_URL}/api/price/BTC", headers=headers).json()
    print(f"Mid: ${btc['mid_price']} | Spread: {btc['spread_bps']} bps")
    
    # Get orderbook (replaces l2Book)
    book = requests.get(f"{BASE_URL}/api/orderbook/BTC", headers=headers).json()
    print(f"Best Bid: ${book['best_bid']} | Best Ask: ${book['best_ask']}")
    
    # Get account state (replaces clearinghouseState)
    wallet = "0x1234567890abcdef..."
    account = requests.get(f"{BASE_URL}/api/account/{wallet}", headers=headers).json()
    print(f"Account Value: ${account['marginSummary']['accountValue']}")
    
    # Get candles
    candles = requests.get(
        f"{BASE_URL}/api/candles/BTC?interval=1h",
        headers=headers
    ).json()
    print(f"Latest close: ${candles[-1]['c']}")
