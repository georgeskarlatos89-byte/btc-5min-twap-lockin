# HIP3 Tick Data

> Source: https://moondev.com/docs#hip3-ticks · captured 2026-09-23

## HIP3 Tick Data (All Symbols)

Tick-level data and server-computed OHLCV candles for **every HIP3 symbol on HyperLiquid** — currently 136 symbols across 7 dexes (xyz, flx, vntl, hyna, km, cash, para). As of **2026-04-26** , the previous top-10-by-volume cap is gone. New symbols are auto-discovered as HyperLiquid lists them. Data is served on-demand from our SQLite tick store at request time, candles are computed live from those ticks, and retention is 30 days of raw ticks per symbol.

🌙 Why this matters

HIP3 tick data on traditional venues costs thousands per month per symbol. Because we run a HyperLiquid node, every HIP3 symbol — stocks, commodities, FX, indices, crypto, pre-IPO — is available through one API at no marginal cost, 24/7, with sub-second freshness and OHLCV at any interval.

  * **HyperLiquid Data API** : 1m candles capped at **3.6 days** , 5m candles capped at **17 days**.
  * **Moon Dev API** : from 2026-04-26 forward, unlimited days back per tick, every tick. Want 30-second bars? We have every trade — roll your own bucket.

### Endpoints

All endpoints require `api_key` as a query param or `X-API-Key` header.

Endpoint| Description  
---|---  
GET /api/hip3/candles/symbols| List all currently tracked symbols, intervals, and categories  
GET /api/hip3/ticks/{coin}?duration=1h| Raw tick data for a symbol (durations: 10m, 1h, 4h, 24h, 7d)  
GET /api/hip3/candles/{coin}?interval=5m| OHLCV candles computed from ticks (intervals: 1m, 5m, 15m, 1h, 4h, 1d)  
GET /api/hip3/price/{coin}| Latest price for a single symbol  
GET /api/hip3/prices| Latest prices for all tracked symbols  
  
### Symbol Lookup

Three symbol formats are accepted — bare ticker, full `dex:ticker`, or non-xyz dex prefix. Bare tickers auto-resolve to the correct dex.

    # Bare ticker (resolves to correct dex automatically)
    curl "https://api.moondev.com/api/hip3/ticks/CL?duration=1h&api_key=YOUR_KEY"
    
    # Full dex:ticker format
    curl "https://api.moondev.com/api/hip3/ticks/xyz:CL?duration=1h&api_key=YOUR_KEY"
    
    # Non-xyz dex
    curl "https://api.moondev.com/api/hip3/ticks/cash:USA500?duration=1h&api_key=YOUR_KEY"

### 1\. List Available Symbols

    curl "https://api.moondev.com/api/hip3/candles/symbols?api_key=YOUR_KEY"

#### Response Example

    {
      "count": 136,
      "dexes": ["xyz", "flx", "vntl", "hyna", "km", "cash", "para"],
      "intervals": ["1m", "5m", "15m", "1h", "4h", "1d"],
      "by_category": {
        "stocks":      ["xyz:TSLA", "xyz:NVDA", "xyz:HIMS", "..."],
        "indices":     ["xyz:XYZ100", "cash:USA500", "km:USTECH", "..."],
        "commodities": ["xyz:CL", "xyz:GOLD", "xyz:SILVER", "flx:OIL", "..."],
        "fx":          ["xyz:EUR", "xyz:JPY"],
        "crypto":      ["hyna:BTC", "hyna:ETH", "hyna:HYPE", "..."],
        "pre_ipo":     ["vntl:OPENAI", "vntl:ANTHROPIC", "vntl:SPACEX"]
      },
      "market_type": "HIP3"
    }

### 2\. Raw Tick Data

Durations: `10m`, `1h`, `4h`, `24h`, `7d`

    curl "https://api.moondev.com/api/hip3/ticks/CL?duration=1h&api_key=YOUR_KEY"

#### Response Example

    {
      "symbol": "xyz:CL",
      "category": "commodities",
      "market_type": "HIP3",
      "duration": "1h",
      "tick_count": 1842,
      "start_time": 1741611600000,
      "end_time": 1741615200000,
      "latest_price": 87.89,
      "ticks": [
        {"t": 1741611600500, "p": 87.65, "dt": "2026-03-10T13:00:00+00:00"},
        {"t": 1741611601000, "p": 87.66, "dt": "2026-03-10T13:00:01+00:00"}
      ]
    }

### 3\. OHLCV Candles (computed from ticks)

Intervals: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. Optional params: `startTime`, `endTime` (Unix ms).

    curl "https://api.moondev.com/api/hip3/candles/CL?interval=5m&api_key=YOUR_KEY"
    curl "https://api.moondev.com/api/hip3/candles/cash:USA500?interval=1h&api_key=YOUR_KEY"

#### Response Example

    [
      {
        "t": 1741611600000, "T": 1741611899999,
        "s": "CL", "i": "5m",
        "o": "87.65", "h": "87.92", "l": "87.60", "c": "87.89",
        "v": "0", "n": 42
      }
    ]

### 4\. Latest Price (single symbol)

The `dex` and `ticker` fields are split out as top-level keys, so consumers don't need to parse the colon-qualified symbol string.

    curl "https://api.moondev.com/api/hip3/price/HIMS?api_key=YOUR_KEY"

#### Response Example

    {
      "symbol": "xyz:HIMS",
      "dex": "xyz",
      "ticker": "HIMS",
      "price": 30.399,
      "category": "other",
      "market_type": "HIP3",
      "timestamp": "2026-04-26T15:39:48.793000+00:00"
    }

### 5\. All Latest Prices

    curl "https://api.moondev.com/api/hip3/prices?api_key=YOUR_KEY"

#### Response Example

    {
      "generated_at": "2026-04-26T15:39:48+00:00",
      "market_type": "HIP3",
      "dexes": ["xyz", "flx", "vntl", "hyna", "km", "cash", "para"],
      "prices": {
        "xyz:HIMS":     {"dex": "xyz",  "ticker": "HIMS",     "price": 30.399, "category": "other"},
        "xyz:CL":       {"dex": "xyz",  "ticker": "CL",       "price": 87.89,  "category": "commodities"},
        "cash:USA500":  {"dex": "cash", "ticker": "USA500",   "price": 6780.6, "category": "indices"},
        "vntl:OPENAI":  {"dex": "vntl", "ticker": "OPENAI",   "price": 412.5,  "category": "pre_ipo"},
        "hyna:BTC":     {"dex": "hyna", "ticker": "BTC",      "price": 96420,  "category": "crypto"}
      }
    }

### Tick + Candle Query Params

All parameters are strictly validated. Invalid input returns HTTP 400 with a helpful message — no silent fallbacks. Unknown symbols return HTTP 404 with a pointer to `/api/hip3/candles/symbols`.

Parameter| Valid values| Error on invalid  
---|---|---  
duration| 10m, 1h, 4h, 24h, 7d| 400 Invalid duration 'X'. Supported: 10m, 1h, 4h, 24h, 7d  
interval| 1m, 5m, 15m, 1h, 4h, 1d| 400 Invalid interval. Supported: 1m, 5m, 15m, 1h, 4h, 1d  
limit| integer 1..100000 (default 10000 ticks / 200 candles)| 400 Invalid limit X. Must be 1..100000  
order| asc or desc (default asc)| 400 Invalid order 'X'. Use 'asc' or 'desc'  
startTime / endTime| Unix ms (overrides duration when provided)| —  
  
### Quick Examples

    # Live HIMS price
    curl "https://api.moondev.com/api/hip3/price/HIMS?api_key=KEY"
    
    # Last hour of HIMS ticks
    curl "https://api.moondev.com/api/hip3/ticks/HIMS?duration=1h&api_key=KEY"
    
    # 1-minute HIMS candles for the last 7 days
    NOW=$(($(date +%s)*1000)); WEEK=$((NOW - 7*86400*1000))
    curl "https://api.moondev.com/api/hip3/candles/HIMS?interval=1m&startTime=$WEEK&endTime=$NOW&api_key=KEY"
    
    # All current HIP3 prices (136 symbols across 7 dexes)
    curl "https://api.moondev.com/api/hip3/prices?api_key=KEY"
    
    # Pre-IPO exposure via Ventuals
    curl "https://api.moondev.com/api/hip3/price/vntl:OPENAI?api_key=KEY"
    curl "https://api.moondev.com/api/hip3/price/vntl:ANTHROPIC?api_key=KEY"

### Key Notes

  * **All 136 symbols** — the previous top-10 cap was removed on 2026-04-26; every HIP3 symbol on HyperLiquid is now tracked
  * **Auto-discovery** — new symbols and dexes are picked up automatically via `perpDexs` (5-min cache) — no config changes required
  * **7 dexes** — xyz, flx, vntl, hyna, km, cash, para
  * **24/7 trading** — HyperLiquid never closes, so equity-named perps trade through nights and weekends
  * **30-day raw tick retention** — every trade for the last 30 days, per symbol
  * **Bare ticker lookups** — pass just `HIMS`, `CL`, or `USA500`; ambiguous tickers (e.g. `GOLD` on xyz/flx/km) require the full `dex:ticker` form
  * **Server-computed candles** — built live from stored ticks; pick any standard interval, or pull raw ticks and bucket them yourself (e.g. 30s)
  * **Beats the HyperLiquid Data API** — their 1m candle history caps at ~3.6 days and 5m at ~17 days; we keep going from 2026-04-26 forward

### Migration Notes

  * **No breaking changes.** Legacy `/api/hip3_ticks/{dex}_{symbol}_{duration}.json` static files continue to publish for the top 10 by volume for backward compatibility.
  * **New consumers should use the on-demand endpoints above** — they cover every symbol, return live data, and support time-window queries.
  * If you previously parsed `xyz:HIMS` client-side to extract the dex, you can now read `dex` and `ticker` directly from the price response.
