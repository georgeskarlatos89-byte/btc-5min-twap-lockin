# Core Data

> Source: https://moondev.com/docs#core-data · captured 2026-09-23

## Core Data

Endpoint| Description  
---|---  
GET /api/positions.json| Top 50 positions across ALL symbols — crypto + HIP3 combined (1s updates)  
GET /api/positions_crypto.json| Top 50 positions — crypto perps only (BTC, ETH, SOL, …)  
GET /api/positions_hip3.json| Top 50 positions — HIP3 only (xyz:GOLD, cash:USA500, …)  
GET /api/positions/all.json| All 182 symbols with top 50 positions each — combined (90s updates)  
GET /api/positions/all_crypto.json| All 134 crypto symbols with top 50 positions each  
GET /api/positions/all_hip3.json| All 48 HIP3 symbols with top 50 positions each  
GET /api/positions/majors.json| All positions ≥ $10M within 50% of liquidation — every symbol, uncapped (90s updates, NEW)  
GET /api/whales.json| Recent whale trades ($25k+, buys & sells)  
GET /api/buyers.json| Recent buyers only ($5k+, HYPE/SOL/XRP/ETH)  
GET /api/depositors.json| All Hyperliquid depositors - canonical address list  
GET /api/whale_addresses.txt| Plain text list of whale addresses  
  
Crypto / HIP3 Position Separation

Position data is now available in **separate crypto and HIP3 feeds** in addition to the existing combined files. Classification rule: any coin with a `:` in its name (e.g. `xyz:GOLD`, `cash:USA500`) is HIP3. All others (e.g. `BTC`, `ETH`) are crypto.

File| Positions| Symbols  
---|---|---  
positions.json (combined)| ~4,477| 182  
positions_crypto.json| ~3,516| 134  
positions_hip3.json| ~961| 48  
  
**Update frequency:** Top-50 files (`positions*.json`) update every ~1s via priority scan. Per-symbol files (`all*.json`) update every ~90s on full scan. Original endpoints are unchanged — fully backward compatible.

Majors Feed — Whale Watching (NEW — added 2026-08-14)

`GET /api/positions/majors.json` — all positions ≥ $10M within 50% of liquidation, across every symbol, uncapped. Same schema as `all.json`. Use this for whale-watching: unlike the top-50 near-liq lists (which rank purely by proximity and can rotate large positions out as smaller ones get closer), a position never leaves this feed while it exceeds $10M. Updated every ~90s.

### Response Example (positions_crypto.json)

    {
      "updated_at": "2026-03-03T14:52:38.731Z",
      "total_positions": 3516,
      "total_longs": 50,
      "total_shorts": 50,
      "min_position_value": 10000,
      "price_source": "websocket",
      "longs": [
        {
          "address": "0x...",
          "coin": "BTC",
          "value": 1250000.50,
          "entry_price": 85000.0,
          "current_price": 86500.0,
          "liq_price": 82000.0,
          "distance_pct": 5.20,
          "leverage": 10.0,
          "size": 14.705882,
          "pnl": 22058.82
        }
      ],
      "shorts": [ ... ]
    }

`longs` and `shorts` are sorted by `distance_pct` ascending (closest to liquidation first). Each array contains up to 50 positions.

### Response Example (all_crypto.json)

    {
      "updated_at": "2026-03-03T14:51:42.000Z",
      "min_position_value": 10000,
      "total_symbols": 134,
      "symbols": {
        "BTC": {
          "total_positions": 892,
          "total_longs": 510,
          "total_shorts": 382,
          "total_long_value": 450000000.00,
          "total_short_value": 280000000.00,
          "longs": [ ... ],
          "shorts": [ ... ]
        },
        "ETH": { ... }
      }
    }

Symbols are sorted by `total_positions` descending. Each symbol contains up to 50 longs + 50 shorts.

Whale Watcher Expansion: 75 Symbols

Whale discovery now watches **75 symbols** across native perps and HIP-3 dexes (previously 3). Whale threshold: $25,000+ per trade.

Category| Count| Examples  
---|---|---  
Always-watch (native perps)| 7| BTC, ETH, SOL, HYPE, XRP, ZEC, FARTCOIN  
Top volume (native perps)| 23| PAXG, PUMP, SUI, DOGE, ASTER, LINK, XMR, MON, BNB, kPEPE, UNI, ENA, ...  
HIP-3 Commodities| 14| xyz:SILVER, xyz:GOLD, xyz:COPPER, xyz:CL, xyz:NATGAS, flx:OIL, ...  
HIP-3 Indices| 8| xyz:XYZ100, cash:USA500, km:USTECH, vntl:MAG7, vntl:SEMIS, vntl:ROBOT  
HIP-3 Stocks| 18| xyz:TSLA, xyz:NVDA, xyz:GOOGL, xyz:PLTR, xyz:META, xyz:AAPL, cash:NVDA, ...  
HIP-3 FX| 2| xyz:EUR, xyz:JPY  
HIP-3 Pre-IPO| 3| vntl:SPACEX, vntl:OPENAI, vntl:ANTHROPIC  
  
HIP-3 perps live on separate dexes within Hyperliquid. Symbol format: `dex:NAME` (e.g., `xyz:SILVER`, `cash:USA500`). Whale addresses discovered from HIP-3 trades are added to the same whale address pool.

Position Scanner: 200 Threads

All 22,500+ discovered whale wallets are scanned continuously. Position tracking is wallet-based — calling `clearinghouseState` for a wallet returns all positions across both native perps and HIP-3 markets automatically. No per-symbol config needed.

  * 200 scan threads (full wallet scans)
  * 30 priority threads (fast updates for top positions)
  * Adding more watched symbols increases the whale address pool over time, not the per-wallet scan cost

### Response Example (depositors.json)

    {
      "updated_at": "2026-01-14T12:00:00Z",
      "stats": {
        "total_count": 125000,
        "total_deposited": 2500000000
      },
      "depositors": [
        {
          "address": "0x1234567890abcdef...",
          "amount": 50000,
          "timestamp": "2026-01-14T11:30:00Z"
        }
      ]
    }

The depositors endpoint contains every wallet that has ever bridged USDC to Hyperliquid - the canonical address list.
