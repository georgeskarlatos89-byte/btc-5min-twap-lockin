# Changelogs polymarket — Section Index

All pages of the docs **Changelogs** tab, copied verbatim (same content as each page's 'Copy page' button).
Every dated/versioned entry is listed below for navigation.

Source root: https://docs.polymarket.com  ·  Crawled: 2026-09-19
Total pages saved: 3 · total entries: 122

## Predictions Changelog.md — 42 dated entries

- **Sep 4, 2026** — Data API v2 release
- **Aug 17, 2026** — Crypto taker delay reduced to 50ms
- **Aug 14, 2026** — 5-minute crypto markets moved to a 60-second Chainlink TWAP
- **Aug 10, 2026** — Data API: per-outcome redemption activity, position fee basis fields, and event artwork fallback
- **Aug 7, 2026** — Chainlink TWAP resolution for crypto up/down markets
- **Jul 17, 2026** — Latency improvements and order response changes — Friday July 24, 04:00 UTC
- **Jul 14, 2026** — Relayer: deprecating CLOB v1 Neg Risk Adapter
- **Jul 10, 2026** — Sports taker fee and maker rebate update
- **Jul 2, 2026** — World Cup markets decimalized to a 0.0025 (0.25¢) tick size
- **Jun 25, 2026** — Bridge API: optional X-Builder-Code header
- **Jun 15, 2026** — CLOB DELETE /orders maximum batch size reduced to 1000
- **Jun 1, 2026** — Increased CLOB order rate limits
- **May 18, 2026** — Data API: builderCode added to /v1/builders/leaderboard and /v1/builders/volume
- **May 14, 2026** — GET /markets/keyset maximum limit reduced to 100
- **Apr 28, 2026** — CLOB V2 is live on production
- **Apr 21, 2026** — Relayer API: POST /submit returns immediately without transactionHash
- **Apr 17, 2026** — CLOB V2: upgrades go live April 28 at ~11:00 UTC, with ~1 hour of downtime
- **Apr 13, 2026** — Bridge API: added support link for bridging issues
- **Apr 10, 2026** — New keyset pagination endpoints for markets and events
- **Apr 9, 2026** — GET /markets: closed defaults to false
- **April 8, 2026** — Increased API Rate Limits
- **Mar 31, 2026** — REST API Fee Fields Update
- **Mar 30, 2026** — Fee Structure V2
- **Mar 17, 2026** — March Madness: $2M+ in Liquidity Rewards
- **Mar 1, 2026** — Taker Fees & Maker Rebates: All Crypto Markets
- **Feb 12, 2026** — 5-Minute Crypto Markets
- **Feb 11, 2026** — Taker Fees & Maker Rebates: NCAAB and Serie A
- **Jan 28, 2026** — Bridge API: Withdrawal Endpoint
- **Jan 16, 2026** — Docs Update: RTDS documentation
- **Jan 16, 2026** — Docs Update: Maker Rebates Program
- **Jan 6, 2026** — New API Features
- **Jan 5, 2026** — Taker Fees & Maker Rebates
- **Sept 24, 2025** — Polymarket Real-Time Data Socket (RTDS) official release
- **September 15, 2025** — WSS price_change event update
- **August 26, 2025** — Updated /trades and /activity endpoints
- **August 21, 2025** — Batch Orders Increase
- **July 23, 2025** — Get Book(s) update
- **June 3, 2025** — New Batch Orders Endpoint
- **June 3, 2025** — Change to /data/trades
- **May 28, 2025** — Websocket Changes
- **May 28, 2025** — New FAK Order Type
- **May 15, 2025** — Increased API Rate Limits

## Perps Changelog.md — 22 dated entries

- **Sep 17, 2026** — Held cancels during accept, 503 on engine timeouts, and reduce-only displacement
- **Sep 15, 2026** — Trading-key internal transfers within account groups; Retry-After on internal-transfer 503s
- **Sep 15, 2026** — Gateway pod identity: x-pmp-pod response header and pod field on auth acknowledgements
- **Sep 14, 2026** — Non-paginated history routes reject cursor; 50-level book and leaderboard account-value sort
- **Sep 8, 2026** — Cancel-by-coid grace window, stricter filter validation, and faster rejected-order lookups
- **Sep 4, 2026** — OI reward eligibility threshold increased to $5M
- **Sep 2, 2026** — Exchange statistics endpoint added
- **Sep 1, 2026** — Concurrent WebSocket posts and HTTP overload shedding
- **Aug 24, 2026** — Response timestamps, rejection references, and liquidation metadata
- **Aug 16, 2026** — Current position fills endpoint added
- **Aug 14, 2026** — Equity and PnL history honour the requested interval
- **Aug 13, 2026** — Deposit and withdrawal history amounts are always decimal token units
- **Aug 11, 2026** — Fill history flags maker fills executed under liquidation
- **Aug 10, 2026** — Fills gain an adl flag; liq no longer set on ADL counterparty legs
- **Aug 8, 2026** — Position deleveraged notification added
- **Aug 7, 2026** — Exchange info reports engine version and cancel-only state
- **Aug 6, 2026** — Portfolio margin summary includes available order margin
- **Jul 6, 2026** — Cancel all orders added
- **Jun 11, 2026** — Cancel responses include order IDs
- **Jun 10, 2026** — Taker delay added for immediately matching orders
- **Jun 9, 2026** — Reduce-only orders added
- **Jun 8, 2026** — Auto-cancel and rate-limit updates

## SDK changelog.md — 58 version entries across tabs

**TypeScript**: 0.10.0, 0.9.0, 0.8.1, 0.8.0, 0.7.0, 0.6.0, 0.5.0, 0.4.0, 0.3.0, 0.2.0, 0.1.0, 0.1.0-beta.18, 0.1.0-beta.17, 0.1.0-beta.16, 0.1.0-beta.15, 0.1.0-beta.14, 0.1.0-beta.13, 0.1.0-beta.12, 0.1.0-beta.11, 0.1.0-beta.10, 0.1.0-beta.9, 0.1.0-beta.8, 0.1.0-beta.7, 0.1.0-beta.6, 0.1.0-beta.5, 0.1.0-beta.4, 0.1.0-beta.3, 0.1.0-beta.2

**Python**: 0.10.0, 0.9.0, 0.8.0, 0.7.1, 0.7.0, 0.6.0, 0.5.0, 0.4.0, 0.3.0, 0.2.0, 0.1.0, 0.1.0b21, 0.1.0b20, 0.1.0b19, 0.1.0b18, 0.1.0b17, 0.1.0b16, 0.1.0b15, 0.1.0b14, 0.1.0b13, 0.1.0b12, 0.1.0b11, 0.1.0b10, 0.1.0b9, 0.1.0b8, 0.1.0b7, 0.1.0b6, 0.1.0b5, 0.1.0b4, 0.1.0b1

