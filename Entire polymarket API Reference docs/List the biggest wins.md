# List the biggest wins

> The biggest single winning positions.

One row per winning POSITION, not per user: `pnl` is
`final_value - initial_value` for that position, and the window
(`?time_period=`, default `day`) is on when it resolved. `?category=`
narrows to a market category. Deeper pages come only from `?cursor=`,
which **pins the window and category it was minted on**; restating a
different one is a `400`, not a silent re-point.

Combo wins are first-class rows here, tagged `kind = "combo"`. They carry a
`' / '`-joined title of their legs and no Gamma event; `event_id` is `0`
and `event_slug` empty; so **branch on `kind` before building an event
link**.



## OpenAPI

````yaml https://data-api.polymarket.com/v2/openapi.json get /v2/biggest-winners
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
  /v2/biggest-winners:
    get:
      tags:
        - boards
      summary: List the biggest wins
      description: >-
        The biggest single winning positions.


        One row per winning POSITION, not per user: `pnl` is

        `final_value - initial_value` for that position, and the window

        (`?time_period=`, default `day`) is on when it resolved. `?category=`

        narrows to a market category. Deeper pages come only from `?cursor=`,

        which **pins the window and category it was minted on**; restating a

        different one is a `400`, not a silent re-point.


        Combo wins are first-class rows here, tagged `kind = "combo"`. They
        carry a

        `' / '`-joined title of their legs and no Gamma event; `event_id` is `0`

        and `event_slug` empty; so **branch on `kind` before building an event

        link**.
      operationId: get_biggest_winners
      parameters:
        - name: time_period
          in: query
          description: >-
            Window on `resolved_at`: `day` | `week` | `month` | `all`. Defaults
            to `day`.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: category
          in: query
          description: |-
            `overall` (default), a Gamma market category (e.g. `sports`), or a
            synthetic category: `combos` (combinatorial wins get their own full
            top-500 per window) or `esports` (promoted subcategory).
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: limit
          in: query
          description: First-page size. Ignored when `cursor` is supplied.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int32
            maximum: 1000
            minimum: 0
        - name: cursor
          in: query
          description: Opaque pagination cursor from a prior response's `next_cursor`.
          required: false
          schema:
            type:
              - string
              - 'null'
      responses:
        '200':
          description: A page of the biggest winning positions
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/BiggestWinnersPage'
        '400':
          description: >-
            Invalid time_period or cursor, or a param that contradicts the
            cursor pin
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
    BiggestWinnersPage:
      type: object
      description: '`{ data, pagination }` envelope for `/v2/biggest-winners`.'
      required:
        - data
        - pagination
      properties:
        data:
          type: array
          items:
            $ref: '#/components/schemas/BiggestWinner'
          description: The page's rows.
        pagination:
          $ref: '#/components/schemas/Pagination'
          description: 'Paging envelope: follow `next_cursor` until `null`.'
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
    BiggestWinner:
      type: object
      description: >-
        One `/v2/biggest-winners` row: a single winning POSITION, not a user
        total.


        `kind` is `market` or `combo`. Combo rows carry a `' / '`-joined title
        of

        their legs and have no Gamma event; `event_id` is `0` and `event_slug`
        is

        empty; so branch on `kind` before building an event link.
      required:
        - win_rank
        - kind
        - user_id
        - pnl
        - initial_value
        - final_value
        - resolved_at
        - condition_id
        - position_id
        - event_id
        - event_slug
        - event_title
        - user_name
        - profile_image
      properties:
        condition_id:
          type: string
          description: >-
            On-chain condition id of the market (combo rows: the combo
            condition).
        event_id:
          type: integer
          format: int32
          description: Gamma event id of the parent event; `0` on combo rows.
        event_slug:
          type: string
          description: Parent event slug; empty on combo rows.
        event_title:
          type: string
          description: Parent event title; on combo rows, the `' / '`-joined leg questions.
        final_value:
          type: number
          format: double
          description: Value at resolution, in USDC.
        initial_value:
          type: number
          format: double
          description: Cost basis of the winning position, in USDC.
        kind:
          type: string
          description: |-
            `market` or `combo`; combo rows carry no Gamma event (`event_id` 0,
            empty `event_slug`), so branch on this before building event links.
        pnl:
          type: number
          format: double
          description: '`final_value - initial_value`, in USDC.'
        position_id:
          type: string
          description: Token id of the winning position.
        profile_image:
          type: string
          description: Profile image URL.
        resolved_at:
          type: integer
          format: int64
          description: Unix seconds; when the position resolved.
        user_id:
          type: string
          description: The winning wallet.
        user_name:
          type: string
          description: Profile display name of the wallet.
        win_rank:
          type: integer
          format: int32
          description: >-
            Unique 1-based ordinal within the window/category; `row_number()`,
            so

            equal PnL does not share a rank (unlike the leaderboard's `rank`).
          minimum: 0
    Pagination:
      type: object
      required:
        - limit
        - offset
        - has_more
      properties:
        has_more:
          type: boolean
          description: |-
            Exact: `true` iff another page exists; probe-based, never inferred
            from page fullness.
        limit:
          type: integer
          format: int32
          description: Page size this page was served with.
          minimum: 0
        next_cursor:
          type:
            - string
            - 'null'
          description: Opaque, signed cursor for the next page; `null` on the last page.
        offset:
          type: integer
          format: int32
          description: |-
            Running item offset for display continuity across keyset pages (the
            cursor drives the actual seek; this is cosmetic; there is no total).
          minimum: 0
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