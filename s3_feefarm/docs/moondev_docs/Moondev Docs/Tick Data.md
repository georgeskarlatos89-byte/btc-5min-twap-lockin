# Tick Data

> Source: https://moondev.com/docs#ticks · captured 2026-09-23

## Tick Data

Historical tick data for all **129 tracked symbols**.

⚠️ IMPORTANT: Symbols MUST be lowercase. `BTC.json` returns 404. Use `btc.json`

Available symbols (lowercase only):

0g, 2z, aave, ace, ada, aero, aixbt, apex, apt, ar, arb, aster, atom, avax, avnt, axs, banana, bch, bera, bio, blast, blur, bnb, btc, cake, cc, celo, crv, dash, doge, dot, eigen, ena, eth, ethfi, fartcoin, fet, fil, fogo, gas, grass, hbar, hemi, hmstr, hype, icp, init, inj, io, ip, jto, jup, kaito, layer, ldo, linea, link, lit, ltc, manta, me, mega, melania, meme, merl, met, mina, mnt, mon, moodeng, morpho, move, near, ondo, op, ordi, paxg, pendle, pengu, pol, popcat, prompt, pump, purr, pyth, render, resolv, rune, s, saga, sand, sei, skr, sky, sol, spx, stable, stbl, strk, stx, sui, super, syrup, tao, tia, ton, trump, trx, uni, virtual, vvv, wct, wif, wld, wlfi, xai, xlm, xmr, xpl, xrp, zec, zen, zerebro, zeta, zk, zro, kbonk, kpepe, kshib

Endpoint| Example| Description  
---|---|---  
GET /api/ticks/{symbol}.json| /api/ticks/btc.json| Current/latest ticks  
GET /api/ticks/{symbol}_10m.json| /api/ticks/btc_10m.json| Last 10 minutes  
GET /api/ticks/{symbol}_1h.json| /api/ticks/btc_1h.json| Last 1 hour  
GET /api/ticks/{symbol}_4h.json| /api/ticks/btc_4h.json| Last 4 hours  
GET /api/ticks/{symbol}_24h.json| /api/ticks/btc_24h.json| Last 24 hours  
GET /api/ticks/{symbol}_7d.json| /api/ticks/btc_7d.json| Last 7 days  
GET /api/ticks/latest.json| | Latest tick data for all 129 assets  
GET /api/ticks/stats.json| | Tick collection statistics and metadata  
  
Replace `btc` with any of the 129 symbols listed above.

### Examples

    # Get BTC tick data (last hour)
    curl "https://api.moondev.com/api/ticks/btc_1h.json?api_key=YOUR_KEY"
    
    # Get ADA tick data (last 24 hours)
    curl "https://api.moondev.com/api/ticks/ada_24h.json?api_key=YOUR_KEY"
    
    # Get TRUMP tick data (last 7 days)
    curl "https://api.moondev.com/api/ticks/trump_7d.json?api_key=YOUR_KEY"

### Response Example (stats.json)

    {
      "generated_at": "2026-01-29T15:37:44Z",
      "symbols": ["0G", "2Z", "AAVE", "ACE", "ADA", "...129 total"],
      "collector_stats": {
        "ticks_collected": 1552,
        "ticks_saved": 1552,
        "errors": 0,
        "started_at": "2026-01-06T19:03:35Z"
      },
      "db_size_mb": 0.16,
      "symbol_stats": {
        "BTC": {
          "tick_count": 251,
          "min_price": 91950.5,
          "max_price": 92296.0,
          "last_price": 92256.5
        }
      }
    }

### Response Example (btc_1h.json)

    {
      "symbol": "BTC",
      "generated_at": "2026-01-06T19:17:38Z",
      "duration": "1h",
      "tick_count": 251,
      "latest_price": 92256.5,
      "ticks": [
        {"timestamp": 1767726130918, "price": 91950.5, "datetime": "2026-01-06T19:02:10Z"}
      ]
    }
