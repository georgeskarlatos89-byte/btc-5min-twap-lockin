# HLP (Hyperliquidity)

> Source: https://moondev.com/docs#hlp · captured 2026-09-23

## HLP (Hyperliquidity Provider)

Complete reverse engineering of Hyperliquid's native market-making protocol (~$210M+ AUM, 7 strategies).

Endpoint| Description  
---|---  
GET /api/hlp/positions| All 7 HLP strategy positions + combined net exposure  
GET /api/hlp/positions?include_strategies=false| Summary only (faster response)  
GET /api/hlp/positions/history?hours=N| Position snapshots over time  
GET /api/hlp/liquidators| Liquidator activation events  
GET /api/hlp/deltas?hours=N| Net exposure changes over time  
  
### Response Example (positions)

    {
      "total_value_usd": 210000000,
      "net_exposure": {
        "BTC": {"long": 125000000, "short": 98000000, "net": 27000000},
        "ETH": {"long": 85000000, "short": 92000000, "net": -7000000}
      },
      "strategies": [
        {
          "name": "Strategy A",
          "vault_address": "0x...",
          "value_usd": 45000000,
          "positions": [
            {"symbol": "BTC", "side": "long", "size_usd": 12000000}
          ]
        }
      ]
    }

### Response Example (deltas)

    {
      "deltas": [
        {
          "timestamp": "2026-01-08T10:00:00Z",
          "BTC": {"delta": 500000, "direction": "more_long"},
          "ETH": {"delta": -200000, "direction": "more_short"}
        }
      ]
    }

### Python Example

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    
    # Get HLP positions (full details)
    hlp = requests.get(
        f"{BASE_URL}/api/hlp/positions",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    print(f"Total HLP Value: ${hlp['total_value_usd']:,.0f}")
    print(f"Strategies: {len(hlp['strategies'])}")
    
    # Net exposure per coin
    for coin, exp in hlp["net_exposure"].items():
        direction = "LONG" if exp["net"] > 0 else "SHORT"
        print(f"  {coin}: ${abs(exp['net']):,.0f} {direction}")
    
    # Position snapshots over the last 24 hours
    history = requests.get(
        f"{BASE_URL}/api/hlp/positions/history?hours=24",
        headers={"X-API-Key": API_KEY}
    ).json()

HLP data collection started Nov 3, 2025. Historical analysis covers this full period.

This data is nowhere else. [Get your API key and start using it.](<https://moondev.com/t/docs>)
