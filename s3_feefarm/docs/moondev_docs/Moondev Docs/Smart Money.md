# Smart Money

> Source: https://moondev.com/docs#smart-money · captured 2026-09-23

## Smart Money

Endpoint| Description  
---|---  
GET /api/smart_money/rankings.json| Top 100 smart + Bottom 100 dumb money by PnL  
GET /api/smart_money/leaderboard.json| Top 50 performers with detailed metrics  
GET /api/smart_money/signals_10m.json| Trading signals - last 10 minutes  
GET /api/smart_money/signals_1h.json| Trading signals - last 1 hour  
GET /api/smart_money/signals_24h.json| Trading signals - last 24 hours  
  
### Response Example (rankings.json)

    {
      "updated_at": "2026-01-06T19:17:19Z",
      "total_tracked_addresses": 3488,
      "smart_money": {
        "description": "Top 100 most profitable addresses",
        "count": 100,
        "addresses": [
          {
            "address": "0x...",
            "total_pnl": 5000000.00,
            "win_rate": 0.72,
            "total_trades": 500
          }
        ]
      },
      "dumb_money": {
        "description": "Bottom 100 least profitable addresses",
        "count": 100,
        "addresses": []
      }
    }

### Response Example (signals_1h.json)

    {
      "updated_at": "2026-01-06T19:17:19Z",
      "duration": "1h",
      "signals": [
        {
          "timestamp": "2026-01-06T19:15:00Z",
          "address": "0x...",
          "address_type": "smart_money",
          "action": "open_long",
          "coin": "BTC",
          "size": 2.5,
          "price": 92000.0,
          "leverage": 10
        }
      ]
    }
