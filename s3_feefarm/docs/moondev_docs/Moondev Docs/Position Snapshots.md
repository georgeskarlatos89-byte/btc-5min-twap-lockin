# Position Snapshots

> Source: https://moondev.com/docs#position-snapshots · captured 2026-09-23

## Position Snapshots

Historical snapshots of HyperLiquid positions within 15% of liquidation. Captured every 1 minute — perfect for backtesting liquidation cascade strategies and squeeze detection.

#### Tracked Symbols

Currently tracking: **BTC, ETH, SOL, XRP, HYPE**

Filters: Positions within 15% of liquidation, minimum $10k position value, 1-minute snapshots

### Endpoints

Endpoint| Description  
---|---  
GET /api/position_snapshots/stats?hours=N| Aggregate statistics across all tracked symbols  
GET /api/position_snapshots/symbol/{symbol}?hours=N| Historical snapshots for a symbol (BTC, ETH, SOL, XRP, HYPE)  
  
### Query Parameters

Parameter| Default| Description  
---|---|---  
hours| 24| Lookback period in hours  
limit| 1000| Maximum records to return (symbol endpoint only)  
min_distance_pct| -| Filter: minimum distance to liquidation %  
max_distance_pct| -| Filter: maximum distance to liquidation %  
side| -| Filter: "long" or "short"  
  
### Response Example (stats)

    {
      "overall": {
        "total_snapshots": 39128,
        "unique_users": 2579,
        "avg_distance_pct": 6.46
      },
      "by_symbol": {
        "BTC": {
          "snapshots": 22952,
          "unique_users": 1710,
          "avg_distance_pct": 5.95,
          "longs": 17646,
          "shorts": 5306
        },
        "ETH": { "snapshots": 8500, "unique_users": 620, "avg_distance_pct": 6.8, "longs": 5200, "shorts": 3300 },
        "SOL": { "snapshots": 4200, "unique_users": 380, "avg_distance_pct": 7.2, "longs": 2800, "shorts": 1400 },
        "XRP": { "snapshots": 2100, "unique_users": 180, "avg_distance_pct": 8.1, "longs": 1400, "shorts": 700 },
        "HYPE": { "snapshots": 1376, "unique_users": 95, "avg_distance_pct": 7.5, "longs": 900, "shorts": 476 }
      },
      "top_10_closest": [
        {
          "user": "0x702f...",
          "symbol": "BTC",
          "side": "long",
          "position_value": 184600,
          "entry_price": 74913.80,
          "liquidation_price": 67164.00,
          "distance_pct": 0.00
        }
      ]
    }

### Response Example (symbol/BTC)

    {
      "symbol": "BTC",
      "snapshots": [
        {
          "snapshot_time": 1738763400000,
          "user": "0xabc123...",
          "symbol": "BTC",
          "side": "long",
          "position_value": 333500,
          "entry_price": 69469.50,
          "liquidation_price": 66859.73,
          "current_price": 68500.00,
          "distance_pct": 2.43,
          "leverage": 20.0
        }
      ],
      "count": 50
    }

### Response Fields (Snapshot Records)

Field| Description  
---|---  
snapshot_time| Unix timestamp in milliseconds  
user| Wallet address  
symbol| BTC, ETH, SOL, XRP, or HYPE  
side| "long" or "short"  
position_value| USD value of position  
distance_pct| % distance to liquidation  
leverage| Position leverage  
entry_price| Entry price  
liquidation_price| Liquidation price  
current_price| Current market price at snapshot time  
  
### Curl Examples

    # Get stats for last hour
    curl "https://api.moondev.com/api/position_snapshots/stats?hours=1" \
      -H "X-API-Key: YOUR_API_KEY"
    
    # Get BTC snapshots (last hour, limit 50)
    curl "https://api.moondev.com/api/position_snapshots/symbol/BTC?hours=1&limit=50" \
      -H "X-API-Key: YOUR_API_KEY"
    
    # Get ETH shorts only, within 5% of liquidation
    curl "https://api.moondev.com/api/position_snapshots/symbol/ETH?hours=24&side=short&max_distance_pct=5" \
      -H "X-API-Key: YOUR_API_KEY"

### Python Example

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    headers = {"X-API-Key": API_KEY}
    
    # Get aggregate stats
    stats = requests.get(
        f"{BASE_URL}/api/position_snapshots/stats",
        headers=headers,
        params={"hours": 24}
    ).json()
    
    print(f"Total snapshots: {stats['overall']['total_snapshots']}")
    print(f"Unique users: {stats['overall']['unique_users']}")
    print(f"Top at risk: {stats['top_10_closest']}")
    
    # Get BTC positions close to liquidation
    btc = requests.get(
        f"{BASE_URL}/api/position_snapshots/symbol/BTC",
        headers=headers,
        params={"hours": 24, "limit": 100}
    ).json()
    
    for snap in btc['snapshots']:
        print(f"{snap['user']}: {snap['distance_pct']}% from liq")
    
    # Filter to very risky positions only
    risky = requests.get(
        f"{BASE_URL}/api/position_snapshots/symbol/ETH",
        headers=headers,
        params={"hours": 12, "max_distance_pct": 5}
    ).json()

### Backtesting Use Cases

Since this is historical snapshot data, you can use it for:

  * **Liquidation Cascade Detection** — Query positions that were <2% from liquidation, cross-reference with price data to see if they got liquidated, build models predicting cascade events
  * **Squeeze Setup Identification** — Track when shorts cluster near liquidation prices, identify price levels where forced buying would occur, backtest "buy when shorts are trapped" strategies
  * **Whale Liquidation Hunting** — Monitor large positions approaching liquidation, identify recurring whale addresses and their patterns, backtest strategies targeting known liquidation levels
  * **Risk Concentration Analysis** — Analyze where leverage clusters by price level, identify "liquidation magnets" (price zones with heavy exposure), backtest contrarian strategies around these zones

### Example Backtest Query

    # Get all positions that were within 3% of liquidation yesterday
    risky_positions = requests.get(
        f"{BASE_URL}/api/position_snapshots/symbol/BTC",
        headers=headers,
        params={"hours": 24, "max_distance_pct": 3, "limit": 5000}
    ).json()
    
    # Group by snapshot_time to see how risk evolved
    # Cross-reference with price data to see which got liquidated
    for snap in risky_positions['snapshots']:
        print(f"{snap['snapshot_time']}: {snap['user']} - {snap['distance_pct']}% from liq")

Want to plug this into your trading system? [Get your API key here.](<https://moondev.com/t/docs>)
