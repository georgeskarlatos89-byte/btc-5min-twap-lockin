# List combo activity

> Keyset-paginated combo lifecycle + redemption
feed for a user, in the standard `{ data, pagination }` envelope.

Pass `?cursor=` from a prior response's `next_cursor` to fetch the next page.
Unlike the activity feed, the anchor behind the cursor is
`(block_number, log_index)`; this endpoint's total order.

A malformed `condition` combo condition id is a `400` naming the value; a
well-formed one that matches nothing serves an empty `data` array, which
is the meaningful zero-state.



## OpenAPI

````yaml https:/data-api.polymarket.com/v2/openapi.json get /v2/activity/combos
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
  /v2/activity/combos:
    get:
      tags:
        - feeds
      summary: List combo activity
      description: >-
        Keyset-paginated combo lifecycle + redemption

        feed for a user, in the standard `{ data, pagination }` envelope.


        Pass `?cursor=` from a prior response's `next_cursor` to fetch the next
        page.

        Unlike the activity feed, the anchor behind the cursor is

        `(block_number, log_index)`; this endpoint's total order.


        A malformed `condition` combo condition id is a `400` naming the value;
        a

        well-formed one that matches nothing serves an empty `data` array, which

        is the meaningful zero-state.
      operationId: get_combos
      parameters:
        - name: user
          in: query
          description: Required; the feed is user-anchored.
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
        - name: condition
          in: query
          description: |-
            Combo condition id(s), comma-separated (at most 20 distinct values).
            `condition_id` / `conditionId` are accepted aliases.
          required: false
          schema:
            type:
              - string
              - 'null'
      responses:
        '200':
          description: A page of combo activity
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ComboActivityPage'
        '400':
          description: >-
            Invalid query params or cursor, a malformed 'condition' combo id, or
            a 'user' that is a known protocol contract address
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
          description: Service is at heavy-query capacity; retry after `Retry-After`
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
    ComboActivityPage:
      type: object
      description: '`{ data, pagination }` envelope for `/v2/activity/combos`.'
      required:
        - data
        - pagination
      properties:
        data:
          type: array
          items:
            $ref: '#/components/schemas/ComboActivity'
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
    ComboActivity:
      type: object
      description: |-
        One combo lifecycle/redemption event (`/v2/activity/combos`).

        Ordered and paginated by on-chain position; `(block_number, log_index)`.
        `timestamp` is the event's wall-clock, not the ordering key.
      required:
        - id
        - type
        - proxy_wallet
        - combo_condition_id
        - combo_position_id
        - block_number
        - timestamp
        - transaction_hash
        - legs
      properties:
        amount_usdc:
          type:
            - number
            - 'null'
          format: double
          description: Cash amount of the action in USDC; `null` where no cash leg applies.
        block_number:
          type: integer
          format: int64
          description: Block number of the action.
        combo_condition_id:
          type: string
          description: On-chain combo condition id (structural, `0x03`-prefixed).
        combo_position_id:
          type: string
          description: Token id of the combo position the action touched.
        id:
          type: string
          description: '`tx_hash-log_index`.'
        legs:
          type: array
          items:
            $ref: '#/components/schemas/ComboLeg'
          description: The combo's legs, in leg order, with market and event enrichment.
        payout_usdc:
          type:
            - number
            - 'null'
          format: double
          description: Redemption payout in USDC on REDEEM rows; `null` otherwise.
        proxy_wallet:
          type: string
          description: Proxy wallet the action belongs to.
        timestamp:
          type: integer
          format: int64
          description: Event time (epoch seconds).
        transaction_hash:
          type: string
          description: Hash of the settling transaction.
        type:
          type: string
          description: >-
            Canonical action verb; SPLIT / MERGE / CONVERT / COMPRESS / WRAP /

            UNWRAP / REDEEM. The only action field on the wire; `timestamp` is
            the

            served event clock.
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
    ComboLeg:
      type: object
      description: One leg of a combo, with its market and event enrichment.
      required:
        - leg_index
        - leg_position_id
        - leg_condition_id
        - leg_outcome_index
        - leg_outcome_label
        - leg_status
        - leg_current_price
        - market
      properties:
        leg_condition_id:
          type: string
          description: On-chain condition id of the leg's market.
        leg_current_price:
          type: number
          format: double
          description: Live price of the leg outcome (Gamma marks).
        leg_index:
          type: integer
          format: int32
          description: Position of the leg within the combo, 0-based.
        leg_outcome_index:
          type: integer
          format: int32
          description: |-
            Index of the outcome the combo takes on this leg; `999` means the
            outcome could not be labeled.
        leg_outcome_label:
          type: string
          description: Label of the outcome the combo takes on this leg.
        leg_position_id:
          type: string
          description: Outcome token id of the leg.
        leg_resolved_at:
          type:
            - string
            - 'null'
          description: Gamma `closed_time`, RFC3339; `null` while open.
        leg_status:
          type: string
          description: OPEN / RESOLVED_WIN / RESOLVED_LOSS (live resolution state).
        market:
          $ref: '#/components/schemas/ComboLegMarket'
          description: The leg's market, with its (single) event nested.
    ComboLegMarket:
      type: object
      description: A leg's market, with its (single) event nested.
      required:
        - market_id
        - slug
        - title
        - outcome
        - image_url
        - icon_url
        - category
        - subcategory
        - tags
        - end_date
        - event
      properties:
        category:
          type: string
          description: Gamma market category (e.g. `sports`).
        end_date:
          type: string
          description: Market end date, RFC3339; empty when Gamma has none.
        event:
          $ref: '#/components/schemas/ComboLegEvent'
          description: The market's parent event.
        group_item_title:
          type: string
          description: |-
            Raw short per-leg label, without the fallback `title` applies; `""`
            when Gamma has none.
        icon_url:
          type: string
          description: Market icon URL.
        image_url:
          type: string
          description: Market image URL.
        line:
          type:
            - number
            - 'null'
          format: double
          description: The sports line the market is quoted on; `null` when it has none.
        market_id:
          type: string
          description: |-
            Gamma's own market id; NOT the on-chain condition id (that is the
            leg's `leg_condition_id`).
        outcome:
          type: string
          description: Label of the leg's outcome on this market.
        outcomes:
          type: array
          items:
            type: string
          description: >-
            The market's outcome labels, in outcome-index order; `[]` when Gamma

            has none. Together with `sports_market_type` and `line`, this lets
            an

            executed combo card render both sides of a leg without a Gamma
            lookup.
        question:
          type: string
          description: |-
            The market's full question. Serves `""` when Gamma has none.

            The five display-metadata fields below default when absent so cached
            payloads written before they existed still deserialize; the query
            always serves them.
        slug:
          type: string
          description: Market slug; the URL segment on polymarket.com.
        sports_market_type:
          type: string
          description: |-
            Granular sports market type (for example `totals` or
            `anytime_touchdowns`); `""` for non-sports markets.
        subcategory:
          type: string
          description: Gamma market subcategory.
        tags:
          type: array
          items:
            type: string
          description: Reserved; always `[]` today.
        title:
          type: string
          description: >-
            Short per-leg label (`group_item_title`), falling back to the
            question.
    ComboLegEvent:
      type: object
      description: A leg market's event.
      required:
        - event_id
        - event_slug
        - event_title
        - event_image
      properties:
        event_id:
          type: string
          description: Gamma event id.
        event_image:
          type: string
          description: Event image URL.
        event_slug:
          type: string
          description: Event slug; the URL segment on polymarket.com.
        event_title:
          type: string
          description: Event title.

````