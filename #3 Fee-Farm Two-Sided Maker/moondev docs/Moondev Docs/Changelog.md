# Changelog

> Source: https://moondev.com/docs#changelog · captured 2026-09-23

## Changelog

2026-08-14

  * New `GET /api/positions/majors.json`: all positions ≥ $10M within 50% of liquidation, every symbol, uncapped — a position never leaves the feed while it exceeds $10M. Same schema as `all.json`, ~90s updates.

2026-08-13

  * New `GET /api/user/{address}/flow`: cap-proof net buy/sell flow for a wallet over a time window (per-coin or breakdown).
  * `GET /api/user/{address}/fills` responses now include `order` (guaranteed `newest_first`, newest-kept on cap), exact `truncated`, `newest_ms`/`oldest_ms`, and `page_summary` (replaces `summary`, which remains as a deprecated alias).
  * Fills endpoints now accept time windows: `minutes`/`since_ms` on `/api/user/{address}/fills`, `startTime`/`minutes` on `/api/fills/{address}`. Windowed queries are ~50ms for any wallet.
  * New 503 code `fills_scanner_busy` (retry / fall back); an empty `fills` array now always means genuinely no fills.
  * Retired (410): `/api/hlp/funding`, `/api/hlp/funding/hip3`, `/api/hlp/trades`, `/api/hlp/trades/stats`, `/api/launch/*`, `/ws/launch/*`, `/api/sol-launch/*`. Use `/api/hlp/sentiment` and `/api/hlp/positions` instead.
