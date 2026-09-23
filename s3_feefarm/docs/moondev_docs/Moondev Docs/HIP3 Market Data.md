# HIP3 Market Data

> Source: https://moondev.com/docs#hip3-market-data · captured 2026-09-23

## HIP3 Market Data (Multi-Dex)

Real-time tick data for **every HIP3 symbol on HyperLiquid** — currently **136 symbols across 7 dexes** , up from the previous top-10-by-volume cap. New symbols are auto-discovered as HyperLiquid lists them, with no config changes required. All data is served on-demand from our SQLite tick store at request time, so the API scales to thousands of symbols without pre-generating files.

🌙 Why this beats the HyperLiquid Data API

  * **HyperLiquid 1m candles** — max **3.6 days** of history
  * **HyperLiquid 5m candles** — max **17 days** of history
  * **Moon Dev API (starting 2026-04-26)** — unlimited days going forward, every tick, every HIP3 symbol. Want 30-second bars? We have every tick — roll your own resolution.

### Coverage at a Glance

|   
---|---  
Symbol count| 136 (live count — grows automatically)  
Dexes tracked| xyz (XYZ), flx (Felix), vntl (Ventuals), hyna (HyENA), km (Kinetiq), cash (dreamcash), para (Paragon)  
Categories| stocks, indices, commodities, FX, crypto, pre-IPO, other  
Trading hours| 24/7 (HyperLiquid is always open — no market-hours gaps, even for equity-named perps)  
Tick resolution| every trade on the venue (~500ms heartbeat target)  
Retention| 30 days of raw ticks per symbol  
Freshness| sub-second (queried live from the tick DB)  
  
### Categories

Category| Examples  
---|---  
Stocks| TSLA, NVDA, HIMS, AAPL, META, COIN  
Indices| XYZ100, USA500, USTECH, SMALL2000  
Commodities| GOLD, SILVER, CL, OIL, NATGAS, COPPER  
FX| EUR, JPY  
Crypto| BTC, ETH, SOL, HYPE, XMR  
Pre-IPO| OPENAI, ANTHROPIC, SPACEX  
  
**Symbol Format:** `{dex}:{ticker}` (e.g., `xyz:TSLA`, `hyna:BTC`, `km:US500`, `vntl:OPENAI`)

### Get All HIP3 Symbols & Prices (Auto-Discovery)

The `/meta` endpoint auto-discovers dexes via `perpDexs` (5-min cache), so newly added HyperLiquid dexes appear automatically — no config changes required.

Endpoint| Description  
---|---  
GET /api/hip3/meta| Returns all 136 symbols with current prices, categories, and per-dex active/delisted counts  
GET /api/hip3/candles/symbols| Same coverage, organized by category  
GET /api/hip3/meta?include_delisted=true| Include delisted symbols  
  
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3/meta

### Get Tick Collector Stats

Endpoint| Description  
---|---  
GET /api/hip3_ticks/stats.json| Returns tick collector statistics across all 136 symbols / 7 dexes  
  
    {
      "generated_at": "2026-04-26T15:39:48Z",
      "market_type": "HIP3",
      "collection_method": "WebSocket Streaming",
      "dexes": ["xyz", "flx", "vntl", "hyna", "km", "cash", "para"],
      "symbol_count": 136,
      "by_category": {
        "stocks": ..., "indices": ..., "commodities": ...,
        "fx": ..., "crypto": ..., "pre_ipo": ..., "other": ...
      }
    }

### Get Individual Symbol Tick Data

Endpoint| Description  
---|---  
GET /api/hip3_ticks/{dex}_{ticker}.json| Returns tick data for a specific symbol  
  
#### XYZ Dex (27) - Stocks, Commodities, FX

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/xyz_tsla.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/xyz_nvda.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/xyz_gold.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/xyz_eur.json

#### FLX Dex (7) - Stocks, XMR, Commodities

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/flx_xmr.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/flx_gold.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/flx_oil.json

#### VNTL Dex (7) - Pre-IPO & Indices

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/vntl_openai.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/vntl_anthropic.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/vntl_spacex.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/vntl_mag7.json

#### HYNA Dex (12) - Crypto

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/hyna_btc.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/hyna_eth.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/hyna_hype.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/hyna_sol.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/hyna_fartcoin.json

#### KM Dex (5) - US Indices

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/km_us500.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/km_ustech.json
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_ticks/km_small2000.json

### Python SDK Usage

    from api import MoonDevAPI
    
    api = MoonDevAPI()
    
    # Get all HIP3 symbols with prices
    meta = api.get_hip3_meta()
    for sym in meta['symbols']:
        print(f"{sym['dex']}:{sym['ticker']} = ${sym['price']}")
    
    # Get tick stats
    stats = api.get_hip3_tick_stats()
    print(f"Symbols: {stats['symbol_count']}")
    print(f"Ticks received: {stats['collector_stats']['ticks_received']}")
    
    # Get tick data by dex:ticker
    tsla = api.get_hip3_ticks("xyz", "tsla")      # Tesla
    openai = api.get_hip3_ticks("vntl", "openai") # OpenAI Pre-IPO
    btc = api.get_hip3_ticks("hyna", "btc")       # Bitcoin
    sp500 = api.get_hip3_ticks("km", "us500")     # S&P 500

### Example Dashboard

    python examples/21_hip3_market_data.py               # All 58 symbols with prices
    python examples/21_hip3_market_data.py xyz tsla      # Tesla ticks
    python examples/21_hip3_market_data.py vntl openai   # OpenAI Pre-IPO ticks
    python examples/21_hip3_market_data.py hyna btc      # Bitcoin ticks
    python examples/21_hip3_market_data.py km us500      # S&P 500 ticks

### Legacy Endpoints (Still Supported)

The following endpoints from the original HIP3 Market Data are still available:

Endpoint| Description  
---|---  
GET /api/hip3/candles/symbols| List all symbols organized by category  
GET /api/hip3/prices| All prices at once  
GET /api/hip3/price/{coin}| Single symbol price  
GET /api/hip3/candles/{coin}?interval=| OHLCV candles (1m, 5m, 15m, 1h, 4h, 1d)  
GET /api/hip3/ticks/{coin}?duration=| Raw tick data (10m, 1h, 4h, 24h, 7d)  
  
### List All HIP3 Symbols

Endpoint| Description  
---|---  
GET /api/hip3/candles/symbols| Returns all 136 HIP3 symbols organized by category and dex  
  
    {
      "count": 136,
      "categories": {
        "stocks": ["xyz:TSLA", "xyz:NVDA", "xyz:HIMS", ...],
        "commodities": ["xyz:GOLD", "xyz:SILVER", "xyz:CL", "flx:OIL", ...],
        "fx": ["xyz:JPY", "xyz:EUR"],
        "indices": ["xyz:XYZ100", "cash:USA500", "km:USTECH", ...],
        "crypto": ["hyna:BTC", "hyna:ETH", "hyna:HYPE", ...],
        "pre_ipo": ["vntl:OPENAI", "vntl:ANTHROPIC", "vntl:SPACEX"]
      }
    }

### Get All HIP3 Prices

Endpoint| Description  
---|---  
GET /api/hip3/prices| Returns current prices for all 136 HIP3 symbols  
  
    {
      "generated_at": "2026-04-26T15:39:48Z",
      "market_type": "HIP3",
      "dexes": ["xyz", "flx", "vntl", "hyna", "km", "cash", "para"],
      "prices": {
        "xyz:HIMS":    {"dex": "xyz",  "ticker": "HIMS",    "price": 30.399, "category": "other"},
        "xyz:TSLA":    {"dex": "xyz",  "ticker": "TSLA",    "price": 431.64, "category": "stocks"},
        "cash:USA500": {"dex": "cash", "ticker": "USA500",  "price": 6780.6, "category": "indices"},
        "vntl:OPENAI": {"dex": "vntl", "ticker": "OPENAI",  "price": 412.5,  "category": "pre_ipo"}
      }
    }

### Get Single Symbol Price

Endpoint| Description  
---|---  
GET /api/hip3/price/{coin}| Returns current price for a single HIP3 symbol  
  
The `dex` and `ticker` fields are now split out as top-level keys, so consumers don't need to parse the colon-qualified symbol string.

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3/price/HIMS

    {
      "symbol": "xyz:HIMS",
      "dex": "xyz",
      "ticker": "HIMS",
      "price": 30.399,
      "category": "other",
      "market_type": "HIP3",
      "timestamp": "2026-04-26T15:39:48.793000+00:00"
    }

### Get OHLCV Candles

Endpoint| Description  
---|---  
GET /api/hip3/candles/{coin}?interval={interval}| Returns OHLCV candle data for any HIP3 symbol  
  
Param| Default| Description  
---|---|---  
coin| required| Symbol (TSLA, GOLD, EUR, XYZ100, etc.)  
interval| 5m| Candle interval: 1m, 5m, 15m, 1h, 4h, 1d  
startTime| auto| Start timestamp (Unix ms)  
endTime| now| End timestamp (Unix ms)  
  
    curl -H "X-API-Key: YOUR_KEY" "https://api.moondev.com/api/hip3/candles/TSLA?interval=1h"

    [
      {
        "t": 1737288000000,
        "T": 1737291599999,
        "s": "TSLA",
        "i": "1h",
        "o": "430.50",
        "h": "432.20",
        "l": "429.80",
        "c": "431.90",
        "v": "0",
        "n": 245
      }
    ]

#### Candle Response Fields

Field| Description  
---|---  
t| Open time (Unix ms)  
T| Close time (Unix ms)  
s| Symbol  
i| Interval  
o| Open price  
h| High price  
l| Low price  
c| Close price  
v| Volume  
n| Number of ticks in candle  
  
### Get Raw Tick Data

Endpoint| Description  
---|---  
GET /api/hip3/ticks/{coin}?duration={duration}| Returns raw tick-by-tick price data for any HIP3 symbol  
  
Param| Default| Description  
---|---|---  
coin| required| Symbol (TSLA, GOLD, EUR, XYZ100, etc.)  
duration| 1h| Time window: 10m, 1h, 4h, 24h, 7d  
limit| 10000| Max ticks to return  
startTime| -| Start time (Unix ms), overrides duration  
endTime| -| End time (Unix ms)  
  
    curl -H "X-API-Key: YOUR_KEY" "https://api.moondev.com/api/hip3/ticks/GOLD?duration=1h"

    {
      "symbol": "GOLD",
      "duration": "1h",
      "tick_count": 112,
      "latest_price": 4672.40,
      "ticks": [
        {
          "t": 1737288000000,
          "p": 4671.50,
          "dt": "2026-01-19T13:00:00.000000+00:00"
        },
        {
          "t": 1737288003000,
          "p": 4671.80,
          "dt": "2026-01-19T13:00:03.000000+00:00"
        }
      ]
    }

### Python SDK Usage

    from api import MoonDevAPI
    
    api = MoonDevAPI()
    
    # Get all HIP3 symbols by category
    symbols = api.get_hip3_symbols()
    print(f"{symbols['count']} symbols available")
    print(f"Stocks: {symbols['categories']['stocks']}")
    print(f"Commodities: {symbols['categories']['commodities']}")
    
    # Get all HIP3 prices at once
    prices = api.get_hip3_prices()
    print(f"TSLA: ${prices['prices']['TSLA']['price']}")
    print(f"GOLD: ${prices['prices']['GOLD']['price']}")
    
    # Get single symbol price
    tsla = api.get_hip3_price("TSLA")
    print(f"TSLA: ${tsla['price']} ({tsla['category']})")
    
    # Get OHLCV candles
    candles = api.get_hip3_candles("TSLA", interval="5m")
    for c in candles[-5:]:
        print(f"O:{c['o']} H:{c['h']} L:{c['l']} C:{c['c']}")
    
    # Get 1-hour candles for GOLD
    gold_candles = api.get_hip3_candles("GOLD", interval="1h")
    
    # Get daily candles
    daily = api.get_hip3_candles("NVDA", interval="1d")
    
    # Get raw tick data
    ticks = api.get_hip3_ticks("TSLA", duration="1h")
    print(f"{ticks['tick_count']} ticks, latest: ${ticks['latest_price']}")
    
    # Get 24h of tick data
    ticks_24h = api.get_hip3_ticks("GOLD", duration="24h")
    
    # Custom time range (Unix ms)
    ticks = api.get_hip3_ticks("EUR", start_time=1737280000000, end_time=1737290000000)

### Use Cases

  * **Technical Analysis** \- Build charts and indicators using OHLCV candles
  * **Algorithmic Trading** \- Use tick data for high-frequency strategies
  * **Cross-Asset Analysis** \- Compare crypto with TradFi assets on same platform
  * **Price Alerts** \- Monitor tick-by-tick price changes for specific assets
  * **Backtesting** \- Historical candle data for strategy testing

Like what you see? [Grab your API key and start building.](<https://moondev.com/t/docs>)

### Data Collection Details

  * Polling Interval: 500ms (only stores price changes)
  * Auto-Discovery: New symbols detected every 5 minutes
  * Candle Intervals: 1m, 5m, 15m, 1h, 4h, 1d
  * Tick Durations: 10m, 1h, 4h, 24h, 7d

Rate limit: 3,600 requests per minute. Data updates at 500ms intervals. Tick data stored only on price changes.
