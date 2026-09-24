# Blockchain Events

> Source: https://moondev.com/docs#events · captured 2026-09-23

## Blockchain Events

Endpoint| Description  
---|---  
GET /api/events.json| Real-time decoded blockchain events from Hyperliquid L1  
  
    {
      "updated_at": "2026-01-06T19:17:00Z",
      "stats": {
        "total_events": 2424,
        "events_by_type": {
          "Transfer": 1200,
          "Swap": 500,
          "Approval": 200,
          "Deposit": 150,
          "Withdrawal": 100
        }
      },
      "recent_events": [
        {
          "block_number": 23863116,
          "tx_hash": "0x...",
          "event_type": "Transfer",
          "contract": "0x5555555555555555555555555555555555555555",
          "timestamp": "2026-01-06T19:17:00Z"
        }
      ]
    }
