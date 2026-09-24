# HIP3 Liquidations

> Source: https://moondev.com/docs#hip3-liquidations · captured 2026-09-23

## HIP3 Liquidations (TradFi Assets)

Real-time liquidation data for traditional finance assets trading on Hyperliquid. Includes stocks, commodities, indices, and forex pairs.

### Asset Categories

Category| Assets| Approx OI  
---|---|---  
Stocks| TSLA, NVDA, AAPL, META, MSFT, GOOGL, AMZN, AMD, INTC, PLTR, COIN, HOOD, MSTR, ORCL, MU, NFLX, RIVN, BABA| ~$100M+  
Commodities| GOLD, SILVER, COPPER, CL (Oil), NATGAS, URANIUM| ~$125M  
Indices| XYZ100 (Nasdaq proxy)| ~$120M  
FX| EUR, JPY| ~$3M  
  
### Get HIP3 Liquidations

Endpoint| Description  
---|---  
GET /api/hip3_liquidations/10m.json| Last 10 minutes  
GET /api/hip3_liquidations/1h.json| Last 1 hour  
GET /api/hip3_liquidations/24h.json| Last 24 hours  
GET /api/hip3_liquidations/7d.json| Last 7 days  
  
### Response Fields

Field| Type| Description  
---|---|---  
symbol| string| Asset symbol (TSLA, GOLD, etc.)  
side| string| long or short  
size| number| Position size  
price| number| Liquidation price  
value_usd| number| USD value of liquidation  
category| string| stocks, commodities, indices, or fx  
timestamp| number| Event timestamp (Unix ms)  
  
### Get HIP3 Liquidation Stats

Endpoint| Description  
---|---  
GET /api/hip3_liquidations/stats.json| Aggregated statistics for HIP3 liquidations  
  
### Stats Response Fields

Field| Type| Description  
---|---|---  
total_count| number| Total liquidation count  
total_volume| number| Total USD volume liquidated  
long_count| number| Number of long liquidations  
short_count| number| Number of short liquidations  
long_volume| number| USD volume of long liquidations  
short_volume| number| USD volume of short liquidations  
by_category| object| Breakdown by category (stocks, commodities, indices, fx)  
by_symbol| object| Breakdown by individual symbol  
top_symbols| array| Top symbols by liquidation volume  
  
### Example Request

    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/hip3_liquidations/1h.json

### Python SDK Usage

    from api import MoonDevAPI
    
    api = MoonDevAPI()
    
    # Get HIP3 liquidation stats
    stats = api.get_hip3_liquidation_stats()
    print(f"Total liquidations: {stats['total_count']}")
    print(f"Total volume: ${stats['total_volume']:,.0f}")
    
    # Get HIP3 liquidations for different timeframes
    liqs_10m = api.get_hip3_liquidations("10m")   # Last 10 minutes
    liqs_1h = api.get_hip3_liquidations("1h")     # Last 1 hour
    liqs_24h = api.get_hip3_liquidations("24h")   # Last 24 hours
    liqs_7d = api.get_hip3_liquidations("7d")     # Last 7 days
    
    # Process liquidation data
    for liq in liqs_1h:
        print(f"{liq['symbol']} {liq['side'].upper()} ${liq['value_usd']:,.0f} @ ${liq['price']}")

### Use Cases

  * **Risk Management** \- Monitor liquidation activity across traditional finance assets to gauge market stress
  * **Trading Signals** \- High liquidation volume in one direction may indicate squeeze potential
  * **Market Analysis** \- Track which asset categories are seeing the most liquidation activity
  * **Alert Systems** \- Build alerts for large liquidations on specific symbols

Rate limit: 3,600 requests per minute. Data updates in real-time. Historical data available up to 7 days.

Ready to start building? [Get your API key here.](<https://moondev.com/t/docs>)
