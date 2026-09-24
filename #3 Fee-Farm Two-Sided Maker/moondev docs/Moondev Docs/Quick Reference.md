# Quick Reference

> Source: https://moondev.com/docs#quick-ref · captured 2026-09-23

## Quick Reference - All Endpoints

# Market Data

/api/prices

/api/price/{coin}

/api/orderbook/{coin}

/api/account/{address}

/api/fills/{address}?limit=N&startTime=&minutes= (time windows NEW)

/api/candles/{coin}?interval={1m,5m,15m,1h,4h,1d}

# HL Direct Proxies (NEW)

/api/hl/clearinghouse/{address}?dex= (alias of /api/account)

/api/hl/open_orders/{address}?coin= (drop-in for info.open_orders)

# Core Data

/api/positions.json (combined)

/api/positions_crypto.json (crypto only)

/api/positions_hip3.json (HIP3 only)

/api/positions/all.json (combined)

/api/positions/all_crypto.json (crypto only)

/api/positions/all_hip3.json (HIP3 only)

/api/positions/majors.json ($10M+ whale positions, uncapped, NEW)

/api/whales.json (75 symbols: native + HIP-3)

/api/buyers.json

/api/depositors.json

/api/whale_addresses.txt

# Blockchain

/api/events.json

/api/contracts.json

# Tick Data (129 symbols)

/api/ticks/{stats,latest}.json

/api/ticks/{symbol}.json

/api/ticks/{symbol}_{10m,1h,4h,24h,7d}.json

# Trades & Order Flow (130 symbols)

/api/trades.json

/api/large_trades.json

/api/orderflow.json

/api/orderflow/stats.json

/api/imbalance/{5m,15m,1h,4h,24h}.json

Raw data: 90-day retention | Hourly aggregates: permanent

# Hyperliquid Liquidations

/api/liquidations/{stats,scan_summary}.json

/api/liquidations/{10m,1h,4h,12h,24h,2d,7d,14d,30d}.json

# HIP3 Liquidations (TradFi)

/api/hip3_liquidations/stats.json

/api/hip3_liquidations/{10m,1h,24h,7d}.json

# HIP3 Market Data (136 symbols / 7 dexes — auto-discovered)

/api/hip3/meta (all symbols + per-dex counts)

/api/hip3/candles/symbols

/api/hip3/prices

/api/hip3/price/{coin}

/api/hip3/candles/{coin}?interval={1m,5m,15m,1h,4h,1d}

/api/hip3/ticks/{coin}?duration={10m,1h,4h,24h,7d}

# Multi-Exchange Liquidations

/api/all_liquidations/stats.json

/api/all_liquidations/{10m,1h,4h,12h,24h,2d,5d}.json (30s updates)

/api/all_liquidations/{7d,14d,30d}.json (15min updates)

/api/binance_liquidations/stats.json

/api/binance_liquidations/{10m,1h,24h,7d,30d}.json

/api/bybit_liquidations/stats.json

/api/bybit_liquidations/{10m,1h,24h,7d,30d}.json

/api/okx_liquidations/stats.json

/api/okx_liquidations/{10m,1h,24h,7d,30d}.json

# Binance Futures Liquidations

/api/binance_liquidations/coverage.json?api_key=KEY (segments + gap, standard key)

/api/bulk/binance_liquidations?api_key=KEY&symbol=&start=&end=&side=&min_usd=&limit=&offset= (Quant Elite)

35M+ records, June 2024+ (data gap 2026-04-23 → 2026-07-18), bulk requires _qe API key

# Position Snapshots (Liquidation Risk)

/api/position_snapshots/symbol/{BTC,ETH,SOL,XRP,HYPE}?hours=24

/api/position_snapshots/stats?hours=24

Tracks positions within 15% of liquidation, min $10k value, 1-min snapshots

# Smart Money

/api/smart_money/{rankings,leaderboard}.json

/api/smart_money/signals_{10m,1h,24h}.json

# User Data (Local Node)

/api/user/{address}/positions

/api/user/{address}/fills?limit=N&minutes=&since_ms= (time windows NEW)

/api/user/{address}/flow?minutes=&since_ms=&coin= (cap-proof net flow NEW)

# HLP (Hyperliquidity Provider)

/api/hlp/positions

/api/hlp/positions?include_strategies=false

/api/hlp/positions/history?hours=N

/api/hlp/liquidators

/api/hlp/deltas?hours=N

# HLP Sentiment & Analytics

/api/hlp/sentiment

/api/hlp/liquidators/status

/api/hlp/market-maker

/api/hlp/timing

/api/hlp/correlation

# Polymarket (NEW)

/api/poly/profitable-traders (profitable traders sorted by 7d P&L)

/api/poly/health (no auth required)

# Polymarket Whales (NEW)

/api/poly/whales (recent whale fills, $1k+ trades)

/api/poly/whales/top-traders (leaderboard by wallet)

/api/poly/whales/top-markets (leaderboard by market)

/api/poly/whales/daily (per-day rollup)

/api/poly/whales/health (no auth required)
