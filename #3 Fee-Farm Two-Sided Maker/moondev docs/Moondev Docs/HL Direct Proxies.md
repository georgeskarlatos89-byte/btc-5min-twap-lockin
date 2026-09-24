# HL Direct Proxies

> Source: https://moondev.com/docs#hl-proxies · captured 2026-09-23

## HL Direct Proxies (Bot Endpoints)

Low-latency, retry-backed proxies to Hyperliquid's `/info` endpoint.

Drop-in replacements for `info.open_orders()` and `info.clearinghouseState()` that eliminate 429s on repeated polls. Both endpoints try Moon Dev's local HL node first and transparently fall back to `api.hyperliquid.xyz` on failure.

### Common Notes

  * `address` is case-insensitive; `0x` prefix optional. Works for wallets, sub-accounts, and vaults.
  * Numeric values are returned as strings (HL native format) — cast client-side.
  * `timestamp` is ms since epoch (int).
  * `source` is `"local"` (HL local node) or `"public"` (public-API fallback).
  * Freshness: ~1–2s from a fill on local; +~1s on fallback.
  * A 200 always means the data is real — never a silent zero/empty from upstream failure. Both endpoints return 502 on total upstream failure instead of an empty body.

### GET /api/hl/clearinghouse/{address}

Alias of `/api/account/{address}`. Returns the exact same body — use whichever name you prefer.

Where| Name| Type| Default| Notes  
---|---|---|---|---  
path| address| string| —| Wallet / sub-account / vault  
query| dex| string| ""| Optional perp dex name. Empty = main perp.  
  
#### Response (200)

    {
      "address": "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",
      "timestamp": 1781703404216,
      "marginSummary": {
        "accountValue": "241896794.51",
        "totalNtlPos": "...",
        "totalRawUsd": "...",
        "totalMarginUsed": "..."
      },
      "crossMarginSummary": {
        "accountValue": "...",
        "totalNtlPos": "...",
        "totalRawUsd": "...",
        "totalMarginUsed": "..."
      },
      "crossMaintenanceMarginUsed": "0.0",
      "withdrawable": "0.0",
      "assetPositions": [
        {
          "type": "oneWay",
          "position": {
            "coin": "BTC",
            "szi": "0.001",
            "entryPx": "93210.0",
            "positionValue": "...",
            "unrealizedPnl": "...",
            "returnOnEquity": "0.0123",
            "leverage": { "type": "isolated", "value": 10 },
            "liquidationPx": "...",
            "marginUsed": "...",
            "maxLeverage": 40,
            "cumFunding": { "allTime": "...", "sinceOpen": "...", "sinceChange": "..." }
          }
        }
      ],
      "source": "local"
    }

A genuinely flat account returns 200 with `accountValue: "0.0"` and `assetPositions: []`.

#### Errors

Code| When  
---|---  
401| Missing / invalid API key  
502| Both local node and public HL upstream failed. Body: {"detail":{"error":"Upstream Failure","message":"..."}}  
  
#### Examples

    curl -H "X-API-Key: $KEY" \
      "https://api.moondev.com/api/hl/clearinghouse/0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

    import requests
    
    r = requests.get(
        f"https://api.moondev.com/api/hl/clearinghouse/{addr}",
        headers={"X-API-Key": KEY},
        timeout=5,
    )
    r.raise_for_status()
    state = r.json()
    account_value = float(state["marginSummary"]["accountValue"])

### GET /api/hl/open_orders/{address}

Drop-in replacement for `info.open_orders(address)`. Returns all resting orders for the address.

Where| Name| Type| Default| Notes  
---|---|---|---|---  
path| address| string| —| Wallet / sub-account / vault  
query| coin| string| ""| Optional server-side filter (e.g. BTC). Case-insensitive.  
  
#### Response (200)

    {
      "address": "0xc71cc6f1d8b6d1b0ee55cfc70cd210b2bd1bc506",
      "timestamp": 1781704744647,
      "orders": [
        {
          "coin": "BTC",
          "oid": 123456789,
          "side": "B",
          "limitPx": "93000.0",
          "sz": "0.001",
          "origSz": "0.001",
          "timestamp": 1781700000000,
          "reduceOnly": false,
          "orderType": "Limit",
          "tif": "Gtc",
          "cloid": null
        }
      ],
      "source": "local"
    }

When the account is genuinely flat, `orders` is `[]` and the status is still 200 — use that as the signal to skip cancel loops. Minimum fields the bot needs: `coin` and `oid`.

#### Errors

Code| When  
---|---  
401| Missing / invalid API key  
502| Both local node and public HL upstream failed. Never a silent [] on failure.  
  
#### Examples

    # All open orders
    curl -H "X-API-Key: $KEY" \
      "https://api.moondev.com/api/hl/open_orders/0xC71CC6f1d8B6D1b0ee55cfc70CD210b2bd1BC506"
    
    # Only BTC
    curl -H "X-API-Key: $KEY" \
      "https://api.moondev.com/api/hl/open_orders/0xC71CC6f1d8B6D1b0ee55cfc70CD210b2bd1BC506?coin=BTC"

    import requests
    
    r = requests.get(
        f"https://api.moondev.com/api/hl/open_orders/{addr}",
        headers={"X-API-Key": KEY},
        params={"coin": "BTC"},
        timeout=5,
    )
    r.raise_for_status()
    oids = [o["oid"] for o in r.json()["orders"]]

### SDK Migration Cheatsheet

HL SDK call| Replace with  
---|---  
info.user_state(address)| GET /api/hl/clearinghouse/{address} (or /api/account/{address})  
info.open_orders(address)| GET /api/hl/open_orders/{address}  
  
No behaviour change — same field names, same string-typed values, same shape.
