# Binance Futures Liqs

> Source: https://moondev.com/docs#bulk-binance-liqs · captured 2026-09-23

## Binance Futures Liquidations

Historical and live Binance Futures liquidation data — **35 million+ records** dating back to June 2024, plus the real-time feed.

⚠️ Data gap: 2026-04-23 → 2026-07-18

This dataset has two segments — a **historical archive** (2024-06-04 → 2026-04-23) and the **live feed** (2026-07-18 onward) — with no data in between. No liquidations were collected during that window, and **the gap is permanent** (the data cannot be recovered). Use the Coverage endpoint below to get exact, always-current segment boundaries before assuming a date range has data.

### Coverage Endpoint

Returns the available data segments and the gap between them. Stays current as the live feed grows. Uses a **standard API key** — no Quant Elite key required.

Method| URL  
---|---  
GET| /api/binance_liquidations/coverage.json?api_key=YOUR_KEY  
  
#### Response

    {
      "dataset": "binance_futures_liquidations",
      "generated_at": "2026-07-19T15:17:00Z",
      "segments": [
        {
          "name": "historical",
          "status": "frozen",
          "note": "Complete historical archive. This segment is final and will not change.",
          "start": "2024-06-04T12:46:58Z",
          "end":   "2026-04-23T16:30:49Z",
          "records": 35624285
        },
        {
          "name": "live",
          "status": "collecting",
          "note": "Live feed, collected in real time.",
          "start": "2026-07-18T23:47:58Z",
          "end":   "2026-07-19T15:17:00Z",
          "records": 1234
        }
      ],
      "gap": {
        "from": "2026-04-23",
        "to": "2026-07-18",
        "reason": "No liquidation data was collected during this period. This gap is permanent and the data cannot be recovered."
      },
      "access": {
        "bulk_endpoint": "/api/bulk/binance_liquidations",
        "tier": "quant_elite",
        "historical_example": "/api/bulk/binance_liquidations?end=2026-04-23",
        "live_example": "/api/bulk/binance_liquidations?start=2026-07-18"
      }
    }

Field| Description  
---|---  
segments[].name| historical (frozen archive) or live (real-time feed)  
segments[].status| frozen = final, never changes; collecting = growing in real time  
segments[].start / end| ISO 8601 UTC boundaries of the segment  
segments[].records| Number of liquidations in the segment  
gap.from / to| The dates with no data. Queries spanning this range simply return fewer rows.  
  
### Bulk Data Endpoint

#### Quant Elite Only

Requires a **Quant Elite** API key (must end in `_qe`). Standard keys receive `403 Forbidden`. Pass the key via `?api_key=` or the `X-API-Key` header.

Both segments live in one continuous table, so a single endpoint serves everything. Slice by date to target the historical archive or the live feed.

Method| URL  
---|---  
GET| /api/bulk/binance_liquidations?api_key=YOUR_QE_KEY  
  
#### Parameters

Param| Type| Description  
---|---|---  
api_key| string| Quant Elite API key (required)  
start| string| Start date YYYY-MM-DD or unix ms timestamp  
end| string| End date YYYY-MM-DD or unix ms timestamp  
symbol| string| Filter by symbol (e.g. BTCUSDT)  
side| string| BUY (short liquidated) or SELL (long liquidated)  
min_usd| float| Minimum liquidation size in USD  
limit| int| Records per page (default 10,000 / max 100,000)  
offset| int| Pagination offset (default 0)  
  
#### Selecting a Segment

Goal| Request  
---|---  
Historical archive only| ?end=2026-04-23  
Live feed only| ?start=2026-07-18  
Everything| no date filter (gap rows simply do not exist)  
  
#### Response

    {
      "endpoint": "bulk_binance_liquidations",
      "access_tier": "quant_elite",
      "total_records": 35624285,
      "returned": 10000,
      "limit": 10000,
      "offset": 0,
      "has_more": true,
      "next_offset": 10000,
      "data": [
        {
          "symbol": "BTCUSDT",
          "side": "SELL",
          "price": 96500.0,
          "avg_price": 96312.5,
          "qty": 0.103,
          "usd_size": 9920.19,
          "trade_time": 1771514647172
        }
      ]
    }

Field| Description  
---|---  
symbol| Trading pair  
side| BUY = short position liquidated, SELL = long position liquidated  
price / avg_price| Order price / average fill price  
qty| Quantity  
usd_size| Liquidation value in USD  
trade_time| Unix timestamp in milliseconds  
  
#### Pagination & Rate Limits

Page with `limit` \+ `offset`; follow `next_offset` until `has_more` is false. Rate limits: **60 req/s sustained, 200 burst**. At `limit=100000` the full 35M+ dataset downloads in ~3 minutes.

### Python — Download Full Dataset to CSV

    import requests, csv, time
    
    API_KEY = "yourkey_qe"
    URL = "https://api.moondev.com/api/bulk/binance_liquidations"
    limit, offset = 100000, 0
    
    with open("binance_liqs.csv", "w", newline="") as f:
        writer = None
        while True:
            r = requests.get(URL, params={
                "api_key": API_KEY,
                "limit": limit,
                "offset": offset
            }).json()
            if not r["data"]:
                break
            if not writer:
                writer = csv.DictWriter(f, fieldnames=r["data"][0].keys())
                writer.writeheader()
            writer.writerows(r["data"])
            print(f"{offset + len(r['data']):,} / {r['total_records']:,}")
            if not r["has_more"]:
                break
            offset = r["next_offset"]
            time.sleep(0.5)
