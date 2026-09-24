# User Data

> Source: https://moondev.com/docs#user-data · captured 2026-09-23

## User Data (Local Node)

Get positions and trade history for any Hyperliquid wallet. Powered by Moon Dev's local node for fast responses.

Endpoint| Description  
---|---  
GET /api/user/{address}/positions| Current positions for any wallet  
GET /api/user/{address}/fills?limit=N| Historical fills (default: 100, max: 2000, -1 for ALL)  
GET /api/user/{address}/flow| Net buy/sell flow over a time window — cap-proof, server-side aggregate (NEW)  
  
### Fills Query Parameters (time windows added 2026-08-13)

Param| Type| Description  
---|---|---  
limit| integer| Max fills to return (default 100, max 2000, -1 = all). Unchanged — combines with the window.  
minutes| number, 0–4320| Only return fills from the last N minutes. Strongly recommended for polling — activates the fast lane (see note below).  
since_ms| integer| Only return fills at/after this epoch-milliseconds timestamp. Same fast lane. If both are given, the later (narrower) one wins.  
  
    curl "https://api.moondev.com/api/user/0xABC.../fills?minutes=10&limit=2000&api_key=YOUR_KEY"

⚡ Use a time window when polling

Passing a time window makes every wallet equally fast (~50ms) regardless of how many fills it has. Without a window, first contact with a fill-sparse wallet forces a full 3-day archive scan that can take ~30s (returns 200, it's just slow). For deep history, page backwards with `since_ms` windows instead of one giant call.

⚠️ Window size is the performance dial

Small windows (≤60 min) are the hot path — ~50ms for any wallet. The maximum (`minutes=4320`, 3 days) is effectively a full-archive scan and can take 30–60s on a cold cache: it's a deep-history call, not a polling call. If you need deep history on a schedule, page through it in smaller windows.

The fills response now echoes `since_ms` — the resolved window start (`null` when no window was passed) — so clients can assert the server honored their window.

### Response Example (positions)

    {
      "address": "0xABC123...",
      "positions": [
        {
          "symbol": "BTC",
          "side": "long",
          "size": 0.5,
          "size_usd": 21250,
          "entry_price": 42500,
          "mark_price": 42800,
          "unrealized_pnl": 150,
          "leverage": 10
        }
      ],
      "total_value": 45000,
      "margin_used": 4500
    }

### Response Example (fills)

    {
      "address": "0xABC123...",
      "fills": [
        {
          "symbol": "ETH",
          "side": "buy",
          "size": 2.5,
          "price": 2245.00,
          "fee": 1.12,
          "realized_pnl": 0,
          "timestamp": "2026-01-08T10:30:00Z",
          "order_type": "limit"
        }
      ],
      "total_count": 1543,
      "since_ms": 1765183800000,
      "order": "newest_first",
      "truncated": true,
      "newest_ms": 1786651098765,
      "oldest_ms": 1786650012345,
      "page_summary": {
        "scope": "returned_page",
        "total_pnl": 1250.40,
        "total_volume": 185000.00,
        "total_fees": 92.50
      },
      "summary": "(deprecated alias of page_summary)"
    }

#### New Response Fields (added 2026-08-13)

Field| Meaning  
---|---  
order| Always "newest_first" — see the ordering guarantee below  
truncated| true iff limit cut off older in-window fills. Exact, not inferred — count == limit with truncated: false means the window genuinely held exactly that many  
newest_ms / oldest_ms| Real time coverage of the returned page; oldest_ms is the paging cursor  
page_summary| PnL/volume/fees of the returned page only, never the whole window (use /flow for window-true aggregates). summary remains as a deprecated alias  
  
⚠️ Ordering guarantee (permanent API contract)

Fills are always returned descending (newest first). When `limit` caps the result, the newest fills are kept and the oldest dropped. This is a permanent API contract, and it is intentionally the opposite of HyperLiquid's `userFillsByTime` (which returns ascending and keeps the oldest 2000 — silently blinding recency-based consumers on busy wallets). Build recency signals against this endpoint with confidence; it will not flip for HL parity.

New 503 response (both fills endpoints)

    {
      "error": "Service Busy",
      "message": "Fills scanner is at its concurrency limit - retry shortly or use your fallback source",
      "code": "fills_scanner_busy"
    }

Retry shortly or fall back — do **not** treat a 503 as an empty result. The flip side is now guaranteed: an empty `fills` array with HTTP 200 genuinely means the wallet has no fills. (Previously it could also mean "scanner busy" — if you coded around that old ambiguity, you can drop the workaround.)

### Net Flow (NEW — added 2026-08-13)

`GET /api/user/{address}/flow` — net flow for a wallet over a time window. Answers "has this wallet net-bought or net-sold since T?" in one call. Aggregates all fills in the window server-side with **no result cap** , so the answer can never be truncated — correct even on wallets with tens of thousands of fills. Standard auth (`?api_key=` / `X-API-Key` / Bearer).

Param| Type| Required| Description  
---|---|---|---  
since_ms| integer (epoch ms)| one of these two| Window start, absolute  
minutes| number (0–4320)| one of these two| Window: last N minutes. If both given, the later (narrower) wins  
coin| string| no| Exact symbol (e.g. BTC). Omit for a per-coin breakdown  
  
    curl "https://api.moondev.com/api/user/0xABC.../flow?minutes=60&coin=BTC&api_key=YOUR_KEY"

⚠️ Window size is the performance dial. Small windows (≤60 min) are the hot path — ~50ms for any wallet. The maximum (`minutes=4320`, 3 days) is effectively a full-archive scan and can take 30–60s on a cold cache: it's a deep-history call, not a polling call. If you need deep history on a schedule, page through it in smaller windows.

#### Response Example (with coin)

    {
      "address": "0x...",
      "since_ms": 1786650000000,
      "coin": "BTC",
      "bought_sz": 1.08012,
      "sold_sz": 0.77272,
      "net_sz": 0.3074,
      "bought_usd": 68540.12,
      "sold_usd": 49102.77,
      "net_usd": 19437.35,
      "count": 8,
      "first_ms": 1786650012345,
      "last_ms": 1786651098765,
      "truncated": false
    }

`net_sz` = `bought_sz` − `sold_sz` (positive = net buyer). `truncated` is always `false` — kept in the shape so clients can share response handling with `/fills`.

Without `coin`: same envelope, plus `"coins": {"BTC": {…}, "ETH": {…}}` (each with the fields above) and USD totals only at the top level — coin sizes aren't comparable across coins, so there's no global size total.

Errors: `400 window_required` (a window is mandatory — this endpoint answers recency questions), `503 fills_scanner_busy` (retry or fall back), `401` standard.

### Python Example

    import requests
    
    API_KEY = "your_api_key"
    WALLET = "0x1234567890abcdef..."
    
    # Get positions
    positions = requests.get(
        f"https://api.moondev.com/api/user/{WALLET}/positions",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    print(f"Total Value: ${positions['total_value']:,.2f}")
    for pos in positions["positions"]:
        print(f"  {pos['symbol']} {pos['side']}: ${pos['size_usd']:,.0f}")
    
    # Poll recent fills (fast lane — ~50ms for any wallet)
    recent = requests.get(
        f"https://api.moondev.com/api/user/{WALLET}/fills?minutes=10&limit=2000",
        headers={"X-API-Key": API_KEY}
    )
    if recent.status_code == 503:
        # fills_scanner_busy — retry shortly or use your fallback source
        print("Scanner busy, retrying shortly...")
    else:
        data = recent.json()
        print(f"Fills in last 10 min: {data['total_count']} (window start: {data['since_ms']})")
        if data["truncated"]:
            # newest fills kept — page backwards from oldest_ms for the rest
            print(f"Page covers {data['oldest_ms']} → {data['newest_ms']}, older fills cut off")
    
    # Net flow — has this wallet net-bought or net-sold in the last hour?
    # Cap-proof: aggregated server-side over EVERY fill in the window
    flow = requests.get(
        f"https://api.moondev.com/api/user/{WALLET}/flow?minutes=60&coin=BTC",
        headers={"X-API-Key": API_KEY}
    ).json()
    side = "net BUYER" if flow["net_usd"] > 0 else "net SELLER"
    print(f"BTC last hour: {side} of ${abs(flow['net_usd']):,.0f} ({flow['count']} fills)")
    
    # Get ALL fills (no window — first contact with a fill-sparse
    # wallet can take ~30s; prefer paging with since_ms windows)
    fills = requests.get(
        f"https://api.moondev.com/api/user/{WALLET}/fills?limit=-1",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    print(f"Total trades: {fills['total_count']}")
