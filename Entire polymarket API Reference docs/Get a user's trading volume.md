# Get a user's trading volume

> One wallet's trading volume over a window, in both
units side by side.

`?user=` is required. `?start=` / `?end=` are epoch seconds, inclusive and
optional: omitted or `0` disables a bound (full history). Volume is tracked in
whole UTC days, so each bound is floored to its UTC day; the served window
is the smallest whole-day window containing `[start, end]`.

Returns `{ data: { volume, volume_usdc, trade_count } }`; exactly one row
by construction (the view aggregates with no GROUP BY), zeros for a wallet
with no trades in the window. An inverted window is zeros too, not an error.

`volume` is both-sides SHARES; `volume_usdc` is the same measure in USD;
the endpoint that shows the unit convention side by side: the bare name is
the natural unit, `_usdc` is the denomination, the same pairing as
`current_size` / `entry_cost_usdc` on `/v2/positions`.



## OpenAPI

````yaml https:/data-api.polymarket.com/v2/openapi.json get /v2/user-volume
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
  /v2/user-volume:
    get:
      tags:
        - wallet
      summary: Get a user's trading volume
      description: >-
        One wallet's trading volume over a window, in both

        units side by side.


        `?user=` is required. `?start=` / `?end=` are epoch seconds, inclusive
        and

        optional: omitted or `0` disables a bound (full history). Volume is
        tracked in

        whole UTC days, so each bound is floored to its UTC day; the served
        window

        is the smallest whole-day window containing `[start, end]`.


        Returns `{ data: { volume, volume_usdc, trade_count } }`; exactly one
        row

        by construction (the view aggregates with no GROUP BY), zeros for a
        wallet

        with no trades in the window. An inverted window is zeros too, not an
        error.


        `volume` is both-sides SHARES; `volume_usdc` is the same measure in USD;

        the endpoint that shows the unit convention side by side: the bare name
        is

        the natural unit, `_usdc` is the denomination, the same pairing as

        `current_size` / `entry_cost_usdc` on `/v2/positions`.
      operationId: get_user_volume
      parameters:
        - name: user
          in: query
          description: The wallet whose volume to return. Required.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: start
          in: query
          description: >-
            Window start, epoch seconds, inclusive. Omitted or `0` disables the

            bound (the feeds' convention). Volume is tracked in whole UTC days,
            so

            the bound is floored to its UTC day.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
        - name: end
          in: query
          description: >-
            Window end, epoch seconds, inclusive, floored to its UTC day.
            Omitted

            or `0` disables the bound.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
      responses:
        '200':
          description: >-
            The wallet's volume over the window: both-sides shares, the same
            measure in USD, and the trade count
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Envelope_UserVolume'
        '400':
          description: >-
            Missing or empty 'user', or a 'user' that is a known protocol
            contract address
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
    Envelope_UserVolume:
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
          type: object
          description: >-
            `/v2/user-volume`: one wallet's trading volume over a whole-day
            window, in

            both units side by side.


            `volume` is both-sides SHARES; `volume_usdc` is the same measure in
            USD;

            the bare name is the natural unit and `_usdc` is the denomination,
            the same

            pairing as `current_size` / `entry_cost_usdc` on `/v2/positions`.

            `trade_count` is the number of trades in the window.
          required:
            - volume
            - volume_usdc
            - trade_count
          properties:
            trade_count:
              type: integer
              format: int64
              description: Number of fills in the window.
              minimum: 0
            volume:
              type: number
              format: double
              description: Both-sides traded volume over the window, in shares.
            volume_usdc:
              type: number
              format: double
              description: Both-sides cash volume over the window, in USD.
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