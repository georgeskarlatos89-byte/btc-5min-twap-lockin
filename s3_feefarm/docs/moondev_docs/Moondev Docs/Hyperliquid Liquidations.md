# Hyperliquid Liquidations

> Source: https://moondev.com/docs#liquidations · captured 2026-09-23

## Hyperliquid Liquidations

Real-time liquidation data from Hyperliquid DEX.

Endpoint| Description  
---|---  
GET /api/liquidations/stats.json| Liquidation monitoring statistics  
GET /api/liquidations/scan_summary.json| Recent liquidation scan results  
GET /api/liquidations/10m.json| Last 10 minutes  
GET /api/liquidations/1h.json| Last 1 hour  
GET /api/liquidations/4h.json| Last 4 hours  
GET /api/liquidations/12h.json| Last 12 hours  
GET /api/liquidations/24h.json| Last 24 hours  
GET /api/liquidations/2d.json| Last 2 days  
GET /api/liquidations/7d.json| Last 7 days  
GET /api/liquidations/14d.json| Last 14 days  
GET /api/liquidations/30d.json| Last 30 days  
  
    {
      "scan_timestamp": "2026-01-06T19:00:00Z",
      "blocks_scanned": 500,
      "total_alerts": 150,
      "critical_count": 5,
      "warning_count": 25,
      "alerts": [
        {
          "timestamp": "2026-01-06T18:55:00Z",
          "block_number": 23863000,
          "tx_hash": "0x...",
          "contract_name": "Core Trading",
          "alert_type": "trade_executed",
          "level": "info"
        }
      ]
    }

Alert Levels: `info`, `warning`, `critical`
