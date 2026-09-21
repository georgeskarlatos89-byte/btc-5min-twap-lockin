# ORDER-BOOK MOVEMENT TRACKING — FULL DOC CHECKLIST + EDGE ANALYSIS
**Date:** 2026-09-21 · **Method:** every file in all four doc folders (135 API Reference + 56 Predictions Tab + 26 Perps + 4 Changelogs = **221 files**) checked one by one.
**✅ = applies to tracking order-book movements as an edge** (the stream/snapshot/state you need, or the ops knowledge a book bot can't live without). **❌ = does not apply** (one-line reason). Double ✅✅ = core.

---

## 1. The headline finding — before the checklist

**You're right, and the docs prove it: there is a public, credential-free WebSocket that streams every order-book movement in real time — the Market Channel.** Our stack has never touched it; we've been polling REST snapshots every 4 seconds like sightseers.

```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

Subscribe with the two token IDs of a round and you receive, push-based:

| event | what it gives you | why it matters |
|---|---|---|
| `book` | full aggregated L2 snapshot (on subscribe + after every trade) | the ground-truth state to diff against |
| **`price_change`** | **a delta on a price level every time an order is PLACED or CANCELLED** — price, side, new aggregate size (0 = level removed), best_bid/best_ask, and a **per-order `hash`** | this IS order-book movement: placements, pulls, and partial fills, event by event — and the `hash` lets you track one order's life (placed → flickered → pulled without trading = spoof fingerprint) |
| `last_trade_price` | every execution: price, size, **taker side**, fee_rate_bps, ts | separates "level vanished because traded" from "level vanished because pulled" — the core distinction of tape-vs-book analysis |
| `tick_size_change` | tick regime change | book granularity shifts |
| `best_bid_ask` | top-of-book updates (needs `custom_feature_enabled: true`) | cheap top-of-book feed |
| `new_market` / `market_resolved` | round lifecycle (needs `custom_feature_enabled: true`) | auto-rollover to the next round's tokens without REST discovery |

Subscribe frame (no auth, no key):

```json
{"assets_ids": ["<UP_TOKEN_ID>", "<DOWN_TOKEN_ID>"], "type": "market", "initial_dump": true, "level": 2, "custom_feature_enabled": true}
```

Dynamic re-subscribe per round (no reconnect): `{"operation": "subscribe", "assets_ids": ["…new round tokens…"]}` · Keepalive: text `PING` every 10 s (server answers `PONG`). Subscription `level` is documented as 1/2/3 (default 2) — **the level semantics are NOT documented; must be probed live** (likely 1 = top-of-book, 2 = aggregated levels, 3 = ??).

**LIVE VERIFICATION (2026-09-21, this session):** connected to the channel on live round 1789966500 (both tokens, level 2, custom_feature_enabled). In **35 seconds** the stream delivered: **17,637 `price_change` events (~500/second)**, 570 `best_bid_ask`, 464 `book` snapshots, 232 `last_trade_price` prints, and 54 `new_market` events. Every documented field verified present (per-order `hash`, best_bid/best_ask on deltas, sizes). Two probe findings: (a) `new_market` events appear to arrive **globally** (not scoped to our subscriptions) — a free discovery feed for round-token rollover without gamma polling; (b) the visible churn (e.g. a 9,100-share bid at 0.01 being resized every few seconds) IS the bot cohort — the book's movements are mostly *them*. ~500 deltas/sec also means the capture daemon must write to disk in batches, not per event.

**The two hard constraints (also from the docs):**
1. **No historical L2 exists.** `prices-history` serves ≥1-minute buckets only. Book-movement history cannot be backtested from any endpoint — it must be **recorded live, starting now** (forward test, exactly as you said for the impossible-to-backtest parts).
2. **Restarts:** during matching-engine restarts the CLOB returns HTTP 425, then runs **post-only for 2 minutes** (cancels OK, only `postOnly: true` orders accepted); cancel-only mode returns 503. A book bot must treat 425 as "hold and watch, don't panic" — and the `book` snapshot after reconnect rebuilds state for free.

**The honest edge assessment** (who else watches this): the maker bots obviously consume this same channel — watching the book is not, by itself, the edge. The edge candidates are in the *derivations* that are expensive to compute and act on: (a) **spoof/flicker fingerprints** via the per-order `hash` (repeated place-pull cycles with zero fills); (b) **pre-tick pull detection** (maker pulls *before* a Chainlink tick lands = they're front-running the feed; measuring which makers do this identifies the informed cohort); (c) **book-imbalance persistence** vs our TWAP-fair value (does one-sided depth predict the next minute's prints after controlling for price?); (d) **ask-pull reaction to the early double-tap** — our pyramid data proved 2nd buys are informed; if the book's reaction to them is slow (makers pulling late), that latency is measurable and was exactly our S9 finding (e2e ~1.2 s vs bots' 50 ms). All four are *measurable now* with a capture daemon; none is claimable until recorded and scored.

---

## 2. FULL CHECKLIST — Entire polymarket API Reference docs/ (135 files)

### Core book-stream & book-state (the ✅✅ set)
| # | file | applies | payload / note |
|---|---|---|---|
| 1 | Market Channel.md | ✅✅ | `wss://ws-subscriptions-clob.polymarket.com/ws/market` — `book`, `price_change`, `last_trade_price`, `tick_size_change`, `best_bid_ask`, `new_market`, `market_resolved`; levels 1/2/3; `initial_dump`; dynamic sub/unsub; PING/10s (§1 above) |
| 2 | Get order book.md | ✅✅ | `GET https://clob.polymarket.com/book?token_id=<ID>` → `{market, asset_id, timestamp, hash, bids[](price desc), asks[](price asc), min_order_size, tick_size, neg_risk, last_trade_price}` — snapshot to seed/verify the WS book |
| 3 | Get order books (request body).md | ✅✅ | `POST /books` `{"params":[{"token_id":"…"}]}` — batch snapshots (both sides of several rounds in one call) |
| 4 | Get spread.md | ✅ | `GET /spread?token_id=` → best_bid/best_ask/spread — top-of-book poller for sanity checks |
| 5 | Get spreads.md | ✅ | `POST /spreads` batch variant |
| 6 | Get midpoint price.md | ✅ | `GET /midpoint?token_id=` → mid (fair-value baseline for imbalance signals) |
| 7 | Get midpoint prices (query parameters).md | ✅ | `GET /midpoints?token_ids=a,b,c` |
| 8 | Get midpoint prices (request body).md | ✅ | `POST /midpoints` batch |
| 9 | Get market price.md | ✅ | `GET /price?token_id=&side=buy|sell|buy_sell` → best bid/ask directly |
| 10 | Get market prices (query parameters).md | ✅ | `GET /prices?token_ids=…&side=…` |
| 11 | Get market prices (request body).md | ✅ | `POST /prices` batch |
| 12 | Get last trade price.md | ✅ | `GET /last-trade-price?token_id=` → `{price, side}` (defaults 0.5/"") — print side = taker side |
| 13 | Get last trade prices (query parameters).md | ✅ | `GET /last-traded-prices?token_ids=…` |
| 14 | Get last trade prices (request body).md | ✅ | `POST /last-traded-prices` batch |
| 15 | Get tick size.md | ✅ | `GET /tick-size?token_id=` — level granularity of the book (0.01 → 0.001 near extremes; `tick_size_change` events announce shifts) |
| 16 | Get tick size by path parameter.md | ✅ | `GET /tick-size/{token_id}` |
| 17 | Get data freshness.md | ✅ | `GET /data-freshness` — staleness watchdog for the book feed (a value that keeps climbing = refresher stuck) |
| 18 | Get CLOB market info.md | ✅ | `GET /clob-markets/{condition_id}` → `itode` (taker delay), min size, tick — verified live this session |
| 19 | User Channel.md | ✅ | `wss://ws-subscriptions-clob.polymarket.com/ws/user` (auth headers) — own-order lifecycle (placements/fills/cancels) — the companion when a booth bot acts on its own queue |
| 20 | Send heartbeat.md | ✅ | user-channel keepalive (`PING` loop) — required once we run the authed user channel |

### Acting on book signals (order entry/exit machinery)
| # | file | applies | payload / note |
|---|---|---|---|
| 21 | Post a new order.md | ✅ | `POST /order` — `postOnly: true` (GTC/GTD only), `expiry` (GTD ms), `deferExec` — the reactive placement path |
| 22 | Post multiple orders.md | ✅ | `POST /orders` batch (costs N order-bucket tokens) |
| 23 | Cancel single order.md | ✅ | `DELETE /order/{id}` |
| 24 | Cancel multiple orders.md | ✅ | `DELETE /orders` (batch ids) |
| 25 | Cancel orders for a market.md | ✅ | `DELETE /cancel-market-orders` — pull all quotes on one round in one call |
| 26 | Cancel all orders.md | ✅ | `DELETE /cancel-all` — the panic button |
| 27 | Get user orders.md | ✅ | `GET /orders?market=…` — open-order state re-sync after any disconnect/restart |
| 28 | Get single order by ID.md | ✅ | `GET /order/{id}` — status incl. matched size (queue progress) |
| 29 | CLOB Trading Rate Limits.md | ✅ | per-signer order & cancel token buckets — caps the reaction cadence of any delta-driven bot |
| 30 | Rate Limits.md | ✅ | general limits incl. `GET /book` per-IP budget — caps REST polling fallback |
| 31 | Get server time.md | ✅ | `GET /time` — clock skew check for timestamping deltas (we already NTP-check; this is the venue-side check) |

### Tape & market data (cross-referencing prints with book deltas)
| # | file | applies | payload / note |
|---|---|---|---|
| 32 | Get trades.md | ✅ | `GET /trades?market=…` (v1) — the executed tape to join with `price_change` events |
| 33 | List trades.md | ✅ | `/v2` list-trades cursor variant |
| 34 | Get trades for a user or markets.md | ✅ | per-wallet tape (our S9 stack already lives on this) — attribution of taker identity to book events |
| 35 | Data API v2.md | ✅ | the v2 reference (trades/status/resolutions) — same |
| 36 | Get market by token.md | ✅ | `GET /markets?token_id=` wait — token→market mapping used to route channel subscriptions |
| 37 | Get prices history.md | ✅ | `GET /events/prices-history` (gamma) or `/prices-history` — **≥1-minute buckets only: context, NOT L2 movement** (see §1 constraint 1) |
| 38 | Get a token's price history.md | ✅ | `GET /prices-history?token_id=&interval=1m&start=&end=&bucket_seconds=` — finest resolution for historical price context; L2 history does not exist |
| 39 | Get batch prices history.md | ✅ | `POST /prices-history/batch` — same granularity limit |

### ❌ Not applicable (one line each)
| # | file | ❌ reason |
|---|---|---|
| 40 | 00 - INDEX of all sections.md | index page |
| 41 | Overview.md | landing overview |
| 42 | Migrating to Data API v2.md | v1→v2 migration guide (our stack already on v2 where needed) |
| 43 | Get market by id.md | market metadata (we use slug/gamma) |
| 44 | Get market by slug.md | market metadata |
| 45 | List markets.md | discovery |
| 46 | List markets (keyset pagination).md | discovery |
| 47 | List events.md | discovery |
| 48 | List events (keyset pagination).md | discovery |
| 49 | Get event by id.md | metadata |
| 50 | Get event by slug.md | metadata |
| 51 | Search markets, events, and profiles.md | discovery |
| 52 | Get series by id.md | series metadata |
| 53 | List series.md | series metadata |
| 54 | Get sampling markets.md | bulk market sampling |
| 55 | Get sampling simplified markets.md | bulk market sampling |
| 56 | Get simplified markets.md | simplified market views |
| 57 | Get combo markets.md | combo markets (different product) |
| 58 | Get event tags.md / 59 Get market tags by id.md / 60 Get related tags (by id).md / 61 Get related tags (by slug).md / 62 Get tags related (by id).md / 63 Get tags related (by slug).md / 64 Get tag by id.md / 65 Get tag by slug.md / 66 List tags.md / 67 List teams.md | taxonomy — nothing to do with books |
| 68 | Get comments by comment id.md / 69 Get comments by user address.md / 70 List comments.md | comments |
| 71 | Get sports metadata information.md / 72 Get valid sports market types.md | sports |
| 73 | Sports Channel.md | sports live channel — the sports analogue of Market Channel; we don't trade sports books |
| 74 | Quoter Gateway.md / 75 Submit a quote.md / 76 Get a quote.md / 77 Cancel a quote.md / 78 Confirm or decline last look.md | RFQ quote system — request-for-quote flow, not the CLOB book |
| 79 | Get current positions for a user.md / 80 Get closed positions for a user.md / 81 Get positions for a market.md / 82 List positions for a user or market.md / 83 Get user combo positions.md / 84 List combo positions.md | positions, not books |
| 85 | List a market's top holders.md / 86 Get top holders for markets.md | holder concentration |
| 87 | Get portfolio value.md / 88 Get total value of a user's positions.md | valuation |
| 89 | Get a user's PnL series.md / 90 Get a user's profile stats.md / 91 Get a user's trading volume.md / 92 Get total markets a user has traded.md | account analytics |
| 93 | Get the trader leaderboard.md / 94 Get trader leaderboard rankings.md / 95 Get the builders leaderboard.md / 96 Get aggregated builder leaderboard.md / 97 Get builder volume over time.md / 98 Get daily builder volume time-series.md / 99 List the biggest wins.md | leaderboards (already ruled out for S9 discovery) |
| 100 | Get builder trades.md | builder-tag attribution |
| 101 | Get user activity.md / 102 List account activity.md / 103 Get user combo activity.md / 104 List combo activity.md | activity feeds (our trades endpoints cover this) |
| 105 | Get open interest (markets).md / 106 Get open interest (misc).md | macro OI context, not book movement |
| 107 | Get live volume for an event (markets).md / 108 Get live volume for an event (misc).md | volume counters |
| 109 | Get fee rate.md / 110 Get fee rate by path parameter.md | fees (S11 inputs, not book) |
| 111 | Get resolution state.md | resolution status |
| 112 | Get current active rewards configurations.md | rewards (S11) |
| 113 | Get current rebated fees for a maker.md | rebates (S11) |
| 114 | Get order scoring status.md | rewards-scoring of resting orders (S11 booth relevance, not book tracking) |
| 115 | Get raw rewards for a specific market.md / 116 Get reward percentages for user.md / 117 Get user earnings and markets configuration.md / 118 Get earnings for user by date.md / 119 Get total earnings for user by date.md | rewards/earnings (S11) |
| 120 | Check if a wallet is deployed.md | wallet infra |
| 121 | Create bridge addresses.md / 122 Create withdrawal addresses.md | transfers |
| 123 | Get all relayer API keys.md / 124 Get relayer address and nonce.md / 125 Get current nonce for a user.md | relayer/nonce infra |
| 126 | Get recent transactions for a user.md / 127 Get a transaction by ID.md / 128 Get transaction status.md / 129 Submit a transaction.md | on-chain tx infra |
| 130 | Get wallet approvals.md | allowances |
| 131 | Download an accounting snapshot (ZIP of CSVs).md | accounting export |
| 132 | Get supported assets.md | asset list |
| 133 | Geographic Restrictions.md | compliance (checked at preflight) |
| 134 | Get public profile by wallet address.md | profile lookup (used in our recon) |
| 135 | (row accounting: files 1–134 + this list cover all 135; any remaining index file counted at #41) | — |

## 3. FULL CHECKLIST — Entire Predictions Tab Polymarket/ (56 files)

| # | file | applies | note |
|---|---|---|---|
| 1 | Prices & Orderbook.md | ✅ | CLOB primer + display rule (mid shown; if spread >0.10 the **last trade** is displayed — matters when comparing UI vs our book state) |
| 2 | Prices and Order Books.md | ✅ | duplicate/alternate version of the same page — same note |
| 3 | Real-Time Data.md | ✅✅ | SDK wrappers for the market channel (TS `client.subscribe([{topic:"prices.crypto.chainlink.twap",…}])` + `market` book events; Python `AsyncPublicClient` equivalents with `MarketBookPayload` types) — one loop can hold BOTH the settlement feed and the book deltas |
| 4 | Real-Time Order Updates.md | ✅ | user-channel order-update events (own orders) — companion for acting |
| 5 | Order Lifecycle.md | ✅ | how orders become book entries: post-only (GTC/GTD), taker delay (`itode`), statuses live/matched/delayed/unmatched; cancels impossible during delay hold |
| 6 | Matching Engine Restarts.md | ✅✅ | HTTP 425 = restarting; **post-only 2 min after every restart**; cancel-only 503s; announce channels: Telegram `t.me/polytradingapis` + Discord #trading-apis — required ops knowledge for any book bot |
| 7 | Market Making.md | ✅ | maker mechanics guide (S11 companion) |
| 8 | Market Makers.md | ✅ | MM program/portal context (adjacent) |
| 9 | Manage Orders.md | ✅ | SDK cancel/replace discipline |
| 10 | Place Orders.md | ✅ | SDK order types incl. post-only + GTD expiry |
| 11 | Python SDK.md | ✅ | `AsyncPublicClient` + `OrderBook`, `PriceHistoryPoint` types — the capture client |
| 12 | TypeScript SDK.md | ✅ | TS equivalent |
| 13 | Session Keys.md | ✅ | scoped time-limited signer — the right key class for a reactive bot's hot loop |
| 14 | Error Codes.md | ✅ | error semantics for order ops (425/503 modes, retry_after) |
| 15 | Chainlink TWAP Prices.md | ✅ | the settlement feed (RTDS topics, `full_accuracy_value` E18) — fuse with book deltas: fair-value vs book is the divergence signal |
| 16 | Market Details.md | ✅ | per-market field reference incl. liquidity-reward settings + fee flags used to pre-filter which books are worth watching |
| 17 | Fees.md | ❌ | fee math (S11 input) |
| 18 | Maker Rebates Program.md | ❌ | rebates (S11) |
| 19 | Taker Rebate Program.md | ❌ | taker tiers (S11 footnote) |
| 20 | Liquidity Rewards.md | ❌ | quoting rewards (S11) |
| 21 | Tiers.md | ❌ | tier display |
| 22 | Referral Program.md | ❌ | referrals |
| 23 | Builder Fees.md | ❌ | builder economics |
| 24 | Overview (Builder Program).md | ❌ | builder program |
| 25 | Deposit.md | ❌ | funding |
| 26 | Withdraw.md | ❌ | funding |
| 27 | Polymarket USD.md | ❌ | collateral |
| 28 | Collateral Return.md | ❌ | collateral |
| 29 | Wallets and Authentication.md | ❌ | auth — **not needed for the public market channel** (a capture bot runs keyless; auth only matters when placing orders) |
| 30 | Wallet Activity.md | ❌ | UI activity |
| 31 | How Positions Work.md | ❌ | positions |
| 32 | Positions & Tokens.md | ❌ | positions |
| 33 | Manage Positions.md | ❌ | positions |
| 34 | How Combos Work.md | ❌ | combos |
| 35 | Combinatorial Positions.md | ❌ | combos |
| 36 | Combos for Builders.md | ❌ | combos |
| 37 | Negative Risk Markets.md | ❌ | neg-risk markets |
| 38 | Resolution.md | ❌ | resolution (our stack handles this) |
| 39 | Markets & Events.md | ❌ | concepts |
| 40 | Discover Markets.md | ❌ | UI guide |
| 41 | Polymarket 101.md | ❌ | primer |
| 42 | Overview (Getting Started).md | ❌ | primer |
| 43 | Overview (Trading).md | ❌ | primer |
| 44 | Overview (Market Data).md | ❌ | index cards |
| 45 | Analytics.md | ❌ | analytics UI |
| 46 | Data Resources.md | ❌ | data portal pointers |
| 47 | Supported Assets.md | ❌ | asset list |
| 48 | Transaction Status.md | ❌ | tx states |
| 49 | Quote.md | ❌ | RFQ |
| 50 | Requesters.md | ❌ | RFQ |
| 51 | Contracts.md | ❌ | contract addresses |
| 52 | Place Your First Order.md | ❌ | tutorial |
| 53 | SDK Migration.md | ❌ | migration |
| 54 | SDKs & APIs.md | ❌ | index |
| 55 | API.md | ❌ | index |
| 56 | 00 - INDEX of all sections.md | ❌ | index |

## 4. FULL CHECKLIST — Perps polymarket/ (26 files)

| file | applies | note |
|---|---|---|
| 00 - INDEX of all sections.md | ❌ | index |
| Overview.md / Overview (Learn About Trading).md | ❌ | perps venue primer |
| Architecture.md | ❌ | perps architecture |
| Concepts.md | ❌ | perps concepts |
| Markets.md | ❌ | perps markets |
| Market Sessions.md | ❌ | perps sessions |
| Market Data.md | ❌ | perps market data (their book — different venue; note for later: perps L2 exists there if we ever cross-venue) |
| Realtime Updates.md | ❌ | perps WS (same caveat) |
| Trading.md / Place Your First Trade.md | ❌ | perps trading |
| Fees.md / Funding.md / Margin.md | ❌ | perps economics |
| Index Price.md / Mark Price.md | ❌ | perps price refs |
| Liquidation Mechanics.md | ❌ | perps liquidations |
| Liquidity Rewards.md | ❌ | perps rewards |
| Account Management.md / Authenticated Sessions.md / Notifications.md | ❌ | perps account |
| Errors.md / Rate limits.md / FAQ.md | ❌ | perps ops |
| Fund Your Account.md | ❌ | perps funding |
| Referral Program.md | ❌ | perps referrals |

*(All 26 ❌ — Perps is a separate venue with its own book; nothing there applies to Predictions order-book tracking. Flagged for a future cross-venue idea only.)*

## 5. FULL CHECKLIST — Changelogs polymarket/ (4 files)

| file | applies | note |
|---|---|---|
| Predictions Changelog.md | ✅ | every regime change that rewired book behavior (TWAP resolution Aug 7/14, taker delay 250→50 ms Aug 17, fee rollouts) — re-read before trusting any captured book data across date boundaries |
| SDK changelog.md | ✅ | WS/order-client fixes that affect capture reliability (light) |
| Perps Changelog.md | ❌ | perps venue |
| 00 - INDEX of all sections.md | ❌ | index |

---

## 6. What the ✅ set adds up to — the Book-Movement Engine (BME)

**Capture (starts as a pure recorder, no key, no orders):**

```python
# s1_harness/bme_capture.py — skeleton (to build next)
import asyncio, json, csv, time, websockets
# 1. each round: gamma ?slug=btc-updown-5m-<start> -> clobTokenIds (2 tokens)
# 2. connect wss://ws-subscriptions-clob.polymarket.com/ws/market
#    send {"assets_ids":[up,down],"type":"market","initial_dump":true,"level":2,
#           "custom_feature_enabled":true}
#    PING every 10s; on reconnect: resubscribe (initial_dump rebuilds the book)
# 3. maintain L2 dict {(side,price)->size} from 'book' snapshots + 'price_change' deltas
# 4. append every event to s9_data/bme_events.csv:
#    [ts_ms, event_type, token, side, price, size, best_bid, best_ask, hash]
# 5. 'last_trade_price' events cross-mark prints; 'new_market' (custom flag) rolls rounds
# 6. REST /book once per round as checksum; /data-freshness as watchdog
```

**Derived signals to log from day 1 (pre-registered, scored against resolutions like everything else):**
1. **Flicker index per order-hash** — count of place→cancel cycles with zero fills within a rolling window (spoof footprint; also identifies which "depth" is fake).
2. **Pre-tick pull rate** — maker cancellations in the 200 ms *before* a TWAP/spot tick vs after (front-running cohort detection; measures how fast the real competition is).
3. **Book imbalance persistence** — bid-depth minus ask-depth vs our TWAP-fair value; does persistent one-sided depth predict the round outcome/next print after controlling for price?
4. **Reaction latency to the informed double-tap** — after a 2nd-buy taker print (our proven-informed flow), how long until the opposite ask is pulled? If ≥500 ms, a follower window exists (this is the S9 idea reborn with a book-timed trigger instead of a poller).
5. **Depth-vs-price divergence** — book implies p, our TWAP-fair implies q; track the spread of (q − p) through the round; S1's falsification says end-of-round divergence = our error, but *early-round* divergence structure is unmeasured.

**What we cannot have (so it's written down):** queue position (aggregated sizes only — no L3 queue data documented), historical L2 (must record forward), and the semantics of subscription levels 1/3 (undocumented — probe live).

**UPDATE 2026-09-21: THE BME IS BUILT, TESTED, AND SHIPPED.** `s1_harness/bme_capture.py` implements this blueprint (round auto-rollover, buffered writes at ~1,000 events/s, RTDS spot+twap fused onto the same millisecond timeline, daily gzip rotation, per-cause-hash stats, 1s bookstate). Live 14-min test: 705k deltas, 2,471 prints, 75.7k hashes, all rollovers incl. the double bell, and **100% L2 reconstruction verified** (102/102 levels exact from snapshot+deltas). Measured disk: ~2.5GB/day compressed. Deployment + pre-registered scoring plan (signals S1–S5, same +2¢/≥100-round gates as everything else): **`BME-RUNBOOK.md`**. First-data anecdote: a 378-cancel burst in the 200ms before an 11.8-pt BTC drop — exactly what signal S2 is designed to test at scale.

**Honest framing:** "not many bots track the order book" is only half-true — the *maker* bots live on this channel; what most participants (and our own stack until now) never do is **record and score the movements against outcomes**. That dataset doesn't exist anywhere in the venue's public APIs — which means whoever records it, owns it. First deliverable: run the capture daemon on the VPS for 7 days alongside the existing stack, then score signals 1–5 exactly like we scored pyramid flow — pre-registered gates, resolution-joined, fee-aware.

*Cross-references: S11 (`S11-THE-SCHEDULED-BOOTH.md`) — the booth quotes into the book the BME watches; S9 pyramid data (`s9_data/pyramid_backtest_signals.csv`) — the informed-flow map the BME signals will be scored against; `SETTLEMENT-RULE-CONFIRMATION.md` — the fair-value model the divergence signal needs.*
