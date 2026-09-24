# Get a user's profile stats

> The profile card for one wallet in a single call.

`?user=` is required. Returns `{ data: { trades, biggest_win, views,
join_date, all_time_pnl } }`, or `{ data: null }` when the wallet is not a
known user.

**`data: null` and a row of zeros mean different things.** `null` is "no such
user"; zeros are "this user exists but has never traded, never won and has no
profile views".

`trades` counts DISTINCT MARKETS traded, not individual trades. It is exact,
not estimated.

`biggest_win` is the best single **resolved** position win in USDC, market
and combo arms unified, and `0` when the wallet has no win over $1.

`join_date` is epoch seconds, or `null` when unknown.

`all_time_pnl` is the newest persisted cumulative `/v2/user-pnl` point at
its actual observation timestamp. It is `null` when the user exists but no
observation has been published yet. For the composition card: trading
realized is `realized_market_pnl + realized_combo_pnl`; LP realized is
`realized_lp_pnl`; rebates, yield, rewards, referral, and fees paid map to
their same-named fields (`fees_paid` is refunds minus charges). `volume`,
`volume_usdc`, and `trade_count` are cumulative maker-attributed canonical
fill metrics.

Not paginated: one row by construction, so `data` is the bare object with no
`pagination`; the `/v2/value` shape.



## OpenAPI

````yaml https:/data-api.polymarket.com/v2/openapi.json get /v2/user-stats
openapi: 3.1.0
info:
  title: Polymarket Data API v2
  description: >-
    The Polymarket Data API: wallet portfolios, trade and activity feeds, market
    state and ranked boards.


    ## Conventions every endpoint shares


    - **Envelope**: every response wraps its payload in `data` (paged routes add
    `pagination`). A documented miss is `data: null` or an empty list, never an
    error.

    - **Pagination is cursor-only**: follow `pagination.next_cursor` until
    `null`; `has_more` is exact, and there is no `offset` query parameter
    (sending one is a `400`). Cursors are signed, typed per endpoint, and
    opaque. The feeds (`trades`, `activity`, combo activity) are keyset walks,
    stable across concurrent writes; the boards, `holders` and most
    combo-position sorts are offset walks behind the opaque token, so a page
    taken across a data refresh can skip or repeat rows. Where a cursor binds
    its cohort (the boards, positions, combo positions), resuming bare is fine,
    restating the same values is fine, and contradicting them is a `400`. The
    `trades`/`activity` feed cursors carry only the seek anchor and page size
    (plus the sort direction on activity): re-send identical filters on every
    page, because changing one mid-walk re-anchors silently.

    - **Rate limiting**: `429` with `Retry-After` is the busy signal for heavy
    queries and pool saturation. A heavy query may first be queued briefly for a
    capacity slot; the `429` arrives only if that short wait ends unserved. Each
    caller also has a per-client request allowance, and bursts past it get the
    same `429` with `Retry-After` sized to the remaining wait. Retry after the
    given delay.

    - **Identifiers**: `condition` (aliases `condition_id`, `conditionId`) is
    the unified query key for on-chain 0x condition ids; `market_id` fields
    carry Gamma's own market ids; `event_id` takes Gamma event ids; `token_id`
    is the CLOB asset id (the key on `/v2/prices-history`).

    - **Params** accept both snake_case and camelCase spellings.

    - **Units**: bare `volume`/`size` values are **shares**; `_usdc` suffixed
    fields are USD; `taker_` prefixed volumes are one-side.

    - **Sentinels**: `outcome_index: 999` means the outcome could not be
    labeled; a missing or `null` numeric field means unavailable, never zero.

    - **Windows on `/v2/trades?user=` and `/v2/activity`**: an omitted or `0`
    `start` floors to three years back (`start=1` asks for full history); an
    omitted or `0` `end` is now plus one day. The other `/v2/trades` shapes
    ignore `start`/`end`: `condition`/`event_id` serve a fixed three-year window
    and the bare feed serves the rolling current-plus-previous month. Other
    windowed routes treat omitted/`0` bounds as unbounded; each documents its
    own rule. `/v2/prices-history` is the strict one, where a `0` bound is a
    `400`.

    - **Errors**: every unsuccessful response is JSON with a human-readable
    `error`, stable `code`, `retryable` flag, and opaque `trace_id`; validation
    failures may also name `parameter`. Codes map to statuses as follows:
    `invalid_request` = `400`, `unauthorized` = `401`, `not_found` = `404`,
    `method_not_allowed` = `405`, `rate_limited` = `429`, `internal` = `500`,
    and both `request_timeout` (the request deadline or the datastore's
    statement timeout) and `dependency_unavailable` = `503`. A `429` or `503`
    that is worth retrying carries `Retry-After` in seconds. Every response,
    successful or not, echoes the same id in the `x-trace-id` header; supply it
    when reporting a failure so operators can correlate it with telemetry.

    - **Auth**: none. All data routes are public; no API key or token is
    required.
  contact:
    name: Polymarket
  license:
    name: MIT
    identifier: MIT
  version: 0.1.0
servers:
  - url: https://data-api.polymarket.com
    description: Production
  - url: https://data-api-rs.stage.pmd.use1.polymarket.sh
    description: Staging
security: []
tags:
  - name: wallet
    description: >-
      Everything anchored on one wallet: positions (base and combos), portfolio
      value, PnL history, the profile card, trading volume, and token approvals.
      Pass the proxy wallet as `user`. One exception cuts across sections:
      `/v2/positions` with `condition` alone (no `user`) answers the market-wide
      holders question.
  - name: feeds
    description: >-
      The high-traffic keyset feeds: trades, activity and combo activity. Filter
      by `user`, `condition` or `event_id`; page with `next_cursor`.
  - name: markets
    description: >-
      Market and event state, keyed by on-chain `condition` ids, Gamma
      `event_id`s or a CLOB `token_id`: open interest, holders, per-event taker
      volume, resolution lifecycle, and price history.
  - name: boards
    description: >-
      Ranked, windowed boards: the PnL/volume leaderboard, biggest single wins,
      and the builder standings and volume buckets. Cursors pin the board they
      were minted on.
  - name: service
    description: >-
      Service metadata: data freshness: the serving watermark, its lag, and
      per-stream ingestion cursors.
paths:
  /v2/user-stats:
    get:
      tags:
        - wallet
      summary: Get a user's profile stats
      description: >-
        The profile card for one wallet in a single call.


        `?user=` is required. Returns `{ data: { trades, biggest_win, views,

        join_date, all_time_pnl } }`, or `{ data: null }` when the wallet is not
        a

        known user.


        **`data: null` and a row of zeros mean different things.** `null` is "no
        such

        user"; zeros are "this user exists but has never traded, never won and
        has no

        profile views".


        `trades` counts DISTINCT MARKETS traded, not individual trades. It is
        exact,

        not estimated.


        `biggest_win` is the best single **resolved** position win in USDC,
        market

        and combo arms unified, and `0` when the wallet has no win over $1.


        `join_date` is epoch seconds, or `null` when unknown.


        `all_time_pnl` is the newest persisted cumulative `/v2/user-pnl` point
        at

        its actual observation timestamp. It is `null` when the user exists but
        no

        observation has been published yet. For the composition card: trading

        realized is `realized_market_pnl + realized_combo_pnl`; LP realized is

        `realized_lp_pnl`; rebates, yield, rewards, referral, and fees paid map
        to

        their same-named fields (`fees_paid` is refunds minus charges).
        `volume`,

        `volume_usdc`, and `trade_count` are cumulative maker-attributed
        canonical

        fill metrics.


        Not paginated: one row by construction, so `data` is the bare object
        with no

        `pagination`; the `/v2/value` shape.
      operationId: get_user_stats
      parameters:
        - name: user
          in: query
          description: |-
            The wallet whose profile stats to return. Required; `0x` followed by
            40 hexadecimal characters.
          required: false
          schema:
            type:
              - string
              - 'null'
      responses:
        '200':
          description: The wallet's profile stats, or null when it is not a known user
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Envelope_Option_UserStats'
        '400':
          description: >-
            Missing or invalid 'user' address, or a 'user' that is a known
            protocol contract address
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '401':
          description: Missing or invalid authentication token
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '429':
          description: Too many requests; retry after `Retry-After`
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '500':
          description: Internal server error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
        '503':
          description: >-
            Timed out (the request deadline or the datastore statement timeout)
            or a serving dependency is unavailable; retry after `Retry-After`
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
components:
  schemas:
    Envelope_Option_UserStats:
      type: object
      description: >-
        `{ "data": T }`; the envelope for endpoints that don't paginate.


        There is no `pagination` key: an aggregate or bounded list has no next
        page.

        Paginated feeds return a `*Page` shape (`{ data, pagination }`) instead.
      required:
        - data
      properties:
        data:
          oneOf:
            - type: 'null'
            - type: object
              description: >-
                The profile card for one wallet, plus its newest persisted
                all-time PnL

                observation.


                `join_date` is `null` when unknown, which is a real state rather
                than an

                error; a meaningful share of accounts have no recorded creation
                time.

                `biggest_win` is `0` when the wallet has no win over $1.
                `all_time_pnl` is

                `null` when the user is known but has no observation yet; a
                missing user is

                represented by the outer response `data: null` instead.
              required:
                - proxy_wallet
                - trades
                - biggest_win
                - views
              properties:
                all_time_pnl:
                  oneOf:
                    - type: 'null'
                    - $ref: '#/components/schemas/UserPnlPoint'
                      description: >-
                        Newest persisted cumulative all-time PnL point, or
                        `null` when absent.
                biggest_win:
                  type: number
                  format: double
                  description: Largest single resolved win, in USDC.
                join_date:
                  type:
                    - integer
                    - 'null'
                  format: int64
                  description: Epoch seconds, or `null` when the join date is unknown.
                proxy_wallet:
                  type: string
                  description: The proxy wallet these lifetime statistics describe.
                trades:
                  type: integer
                  format: int64
                  description: Distinct markets the wallet traded; an exact count.
                  minimum: 0
                views:
                  type: integer
                  format: int64
                  description: Profile view count.
                  minimum: 0
    ErrorResponse:
      type: object
      description: Error body returned by Data API endpoints for unsuccessful requests.
      required:
        - error
        - code
        - retryable
        - trace_id
      properties:
        code:
          $ref: '#/components/schemas/ErrorCode'
          description: Stable classification suitable for programmatic branching.
        error:
          type: string
          description: Human-readable error message.
        parameter:
          type:
            - string
            - 'null'
          description: Query or body parameter associated with a validation failure.
        retryable:
          type: boolean
          description: Whether an automated consumer may retry the request unchanged.
        trace_id:
          type: string
          description: Opaque identifier shared with structured logs and error telemetry.
    UserPnlPoint:
      type: object
      description: >-
        One dense cumulative v2 chart point in USDC.


        Nullable values mean the historical source or mark was unavailable.
        Clients

        must not coerce them to zero.
      required:
        - timestamp
        - source_block
        - realized_market_pnl
        - realized_lp_pnl
        - realized_combo_pnl
        - realized_pnl
        - volume
        - volume_usdc
        - trade_count
      properties:
        cashflow_net:
          type:
            - number
            - 'null'
          format: double
          description: '`deposits - withdrawals`.'
        deposits:
          type:
            - number
            - 'null'
          format: double
          description: Collateral moved into the wallet.
        economic_pnl:
          type:
            - number
            - 'null'
          format: double
          description: '`position_pnl + wallet_income`; the all-in economic result.'
        fees:
          type:
            - number
            - 'null'
          format: double
          description: Negative cumulative fee charges, for direct chart composition.
        fees_paid:
          type:
            - number
            - 'null'
          format: double
          description: Refunds minus charges; a disclosure, not another PnL adjustment.
        fees_refunded:
          type:
            - number
            - 'null'
          format: double
          description: 'Fee atom: total fees refunded.'
        maker_rebate:
          type:
            - number
            - 'null'
          format: double
          description: Maker-side fee rebates credited.
        position_pnl:
          type:
            - number
            - 'null'
          format: double
          description: '`realized_pnl + unrealized_pnl`; the position-only result.'
        realized_combo_pnl:
          type: number
          format: double
          description: Realized PnL from combo positions.
        realized_lp_pnl:
          type: number
          format: double
          description: Realized PnL from AMM liquidity-provision activity.
        realized_market_pnl:
          type: number
          format: double
          description: Realized PnL from market positions.
        realized_pnl:
          type: number
          format: double
          description: '`realized_market_pnl + realized_lp_pnl + realized_combo_pnl`.'
        referral_income:
          type:
            - number
            - 'null'
          format: double
          description: Referral income credited.
        reward_income:
          type:
            - number
            - 'null'
          format: double
          description: Reward-program income credited.
        settled_pnl:
          type:
            - number
            - 'null'
          format: double
          description: '`realized_pnl + wallet_income`; settled economics, no marks.'
        source_block:
          type: integer
          format: int64
          description: Chain block the point was observed at.
        sponsored_income:
          type:
            - number
            - 'null'
          format: double
          description: '`reward_income + yield_income + referral_income`.'
        taker_rebate:
          type:
            - number
            - 'null'
          format: double
          description: Taker-side fee rebates credited.
        timestamp:
          type: integer
          format: int64
          description: |-
            Point timestamp, in epoch seconds. Every amount below is CUMULATIVE
            through this instant, in USDC; `null` means the source is uncovered,
            never zero.
        trade_count:
          type: integer
          format: int64
          description: Cumulative maker-attributed canonical exchange fill count.
          minimum: 0
        trade_pnl:
          type:
            - number
            - 'null'
          format: double
          description: |-
            `position_pnl - realized_lp_pnl + fees_charged - fees_refunded`; the
            compatibility chart series (`p` on the bare user-pnl route).
        unrealized_pnl:
          type:
            - number
            - 'null'
          format: double
          description: Mark-to-market of open inventory.
        volume:
          type: number
          format: double
          description: Cumulative maker-attributed canonical exchange fill shares.
        volume_usdc:
          type: number
          format: double
          description: Cumulative maker-attributed canonical exchange fill cash in USDC.
        wallet_income:
          type:
            - number
            - 'null'
          format: double
          description: |-
            All income credited to the wallet: rebates (maker + taker) plus
            reward, yield and referral income.
        withdrawals:
          type:
            - number
            - 'null'
          format: double
          description: Collateral moved out of the wallet.
        yield_income:
          type:
            - number
            - 'null'
          format: double
          description: Yield income credited.
    ErrorCode:
      type: string
      description: Stable machine-readable classification for Data API failures.
      enum:
        - invalid_request
        - unauthorized
        - not_found
        - method_not_allowed
        - request_timeout
        - rate_limited
        - dependency_unavailable
        - internal

````