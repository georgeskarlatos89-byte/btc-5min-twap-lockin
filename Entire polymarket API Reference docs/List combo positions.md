# List combo positions

> Combo positions for a user, in the standard
`{ data, pagination }` envelope.

Sorts: `FIRST_ENTRY` (default) | `ENTRY_COST` (+ `CURRENT_VALUE` alias) |
`UPDATED`, each with `sort_direction`. `FIRST_ENTRY DESC` pages by the
two-state keyset anchor `(first_entry_at_micros, combo_condition_id,
outcome_index)` (NULL micros = the NULL tail); `UPDATED ASC` by the
single-state `(updated_at_micros, …)` anchor; every other combination by an
offset-shaped token. The cursor binds sort + direction and the resume
ADOPTS them, so a replay cannot flip or re-sort the walk mid-stream.

It binds the cohort; `user`/`status`/`condition`/`updated_after`/
`updated_before`; the same way: following a
page needs nothing but `?user=` (always required here) and `?cursor=`, and
restating a filter is allowed but must agree. Omitting one is not a
contradiction, it is the absence of a choice; **contradicting** one is a
400, because the token's walk state only means anything against the cohort
it was minted on.

Incremental sync: `updated_after`/`updated_before` are an INCLUSIVE
epoch-second window on `updated_at`. Without a `status` filter, either
bound switches the response to the mirror-complete sync view (every live
row, not only held ones); a `status` filter keeps its own visibility. The
upper end is always capped by a short stability ceiling (~90s behind now)
so a mirror never reads rows that may still move. Pair with
`sortBy=UPDATED&sortDirection=ASC` for the stable sync order.

A malformed `condition` combo condition id is a `400` naming the value; a
well-formed one that matches nothing serves an empty `data` array, which
is the meaningful zero-state.



## OpenAPI

````yaml https:/data-api.polymarket.com/v2/openapi.json get /v2/positions/combos
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
  /v2/positions/combos:
    get:
      tags:
        - wallet
      summary: List combo positions
      description: >-
        Combo positions for a user, in the standard

        `{ data, pagination }` envelope.


        Sorts: `FIRST_ENTRY` (default) | `ENTRY_COST` (+ `CURRENT_VALUE` alias)
        |

        `UPDATED`, each with `sort_direction`. `FIRST_ENTRY DESC` pages by the

        two-state keyset anchor `(first_entry_at_micros, combo_condition_id,

        outcome_index)` (NULL micros = the NULL tail); `UPDATED ASC` by the

        single-state `(updated_at_micros, …)` anchor; every other combination by
        an

        offset-shaped token. The cursor binds sort + direction and the resume

        ADOPTS them, so a replay cannot flip or re-sort the walk mid-stream.


        It binds the cohort; `user`/`status`/`condition`/`updated_after`/

        `updated_before`; the same way: following a

        page needs nothing but `?user=` (always required here) and `?cursor=`,
        and

        restating a filter is allowed but must agree. Omitting one is not a

        contradiction, it is the absence of a choice; **contradicting** one is a

        400, because the token's walk state only means anything against the
        cohort

        it was minted on.


        Incremental sync: `updated_after`/`updated_before` are an INCLUSIVE

        epoch-second window on `updated_at`. Without a `status` filter, either

        bound switches the response to the mirror-complete sync view (every live

        row, not only held ones); a `status` filter keeps its own visibility.
        The

        upper end is always capped by a short stability ceiling (~90s behind
        now)

        so a mirror never reads rows that may still move. Pair with

        `sortBy=UPDATED&sortDirection=ASC` for the stable sync order.


        A malformed `condition` combo condition id is a `400` naming the value;
        a

        well-formed one that matches nothing serves an empty `data` array, which

        is the meaningful zero-state.
      operationId: get_combo_positions
      parameters:
        - name: user
          in: query
          description: >-
            The wallet to anchor on. Required; combo positions are
            user-anchored.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: limit
          in: query
          description: >-
            First-page size. Ignored when `cursor` is supplied (the cursor's
            size wins).
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
        - name: status
          in: query
          description: >-
            One of `OPEN`, `REDEEMABLE`, `PARTIAL`, `RESOLVED_WIN`,
            `RESOLVED_LOSS`,

            `RESOLVED_PARTIAL`; default (absent) is the held-visibility listing.


            Aligned with `/v2/positions`: `OPEN` is the superset; it includes

            still-held redeemable positions; and `REDEEMABLE` narrows to exactly

            the rows whose `redeemable` flag is `true`. `REDEEMABLE` must be the

            sole value; the other values may be combined comma-separated and
            keep

            per-value equality semantics.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: sort_by
          in: query
          description: >-
            `FIRST_ENTRY` (default, except under `status=REDEEMABLE`, which
            defaults

            to `ENTRY_COST` so the largest claims lead) | `ENTRY_COST` |

            `CURRENT_VALUE` (alias of `ENTRY_COST`) | `UPDATED`. `FIRST_ENTRY
            DESC`

            and `UPDATED ASC` page by keyset; every other combination pages by
            an

            offset-shaped cursor. The minted cursor binds sort + direction along

            with the `status`/`condition` cohort, so a page-2 request needs only

            `?user=` and `?cursor=`; restating a filter is allowed but must
            agree.
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: sort_direction
          in: query
          description: '`ASC` or `DESC` (default `DESC`).'
          required: false
          schema:
            type:
              - string
              - 'null'
        - name: updated_after
          in: query
          description: >-
            Incremental-sync watermark: INCLUSIVE lower bound on `updated_at`,

            epoch seconds. Without a `status` filter, either watermark serves
            the

            sync view; every live row (mirror-complete), not only the held

            listing; a `status` filter keeps its own row visibility. The
            window's

            upper end is always capped by a short stability ceiling (about 90

            seconds behind now), so a bound in the future cannot serve rows that

            may still move.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
        - name: updated_before
          in: query
          description: |-
            Incremental-sync watermark: INCLUSIVE upper bound on `updated_at`,
            epoch seconds; must not precede `updated_after`. See `updated_after`
            for the sync-view and ceiling semantics.
          required: false
          schema:
            type:
              - integer
              - 'null'
            format: int64
      responses:
        '200':
          description: A page of combo positions
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ComboPositionsPage'
        '400':
          description: >-
            Invalid query params or cursor, an invalid sync watermark (negative,
            or an inverted window), a malformed 'condition' combo id, or a
            'user' that is a known protocol contract address
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
    ComboPositionsPage:
      type: object
      description: '`{ data, pagination }` envelope for `/v2/positions/combos`.'
      required:
        - data
        - pagination
      properties:
        data:
          type: array
          items:
            $ref: '#/components/schemas/ComboPosition'
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
    ComboPosition:
      type: object
      description: >-
        One combo position (`/v2/positions/combos`); a user's holding in a
        single

        combo outcome, with leg rollups and enrichment.


        Paginated on `(first_entry_at_micros, combo_condition_id,
        outcome_index)`;

        follow the response's `next_cursor` rather than rebuilding that triple.
      required:
        - combo_condition_id
        - outcome_index
        - outcome_label
        - combo_position_id
        - proxy_wallet
        - current_size
        - entry_avg_price_usdc
        - entry_cost_usdc
        - gross_entry_cost_usdc
        - entry_fees_usdc
        - realized_payout_usdc
        - status
        - redeemable
        - first_entry_at
        - legs_total
        - legs_resolved
        - legs_pending
        - legs
        - updated_at
        - updated_at_micros
      properties:
        combo_condition_id:
          type: string
          description: On-chain combo condition id (structural, `0x03`-prefixed).
        combo_position_id:
          type: string
          description: Token id of the combo position.
        current_size:
          type: number
          format: double
          description: Current holding in shares.
        entry_avg_price_usdc:
          type: number
          format: double
          description: Weighted-average entry price per share, in USDC.
        entry_cost_usdc:
          type: number
          format: double
          description: Entry cost basis in USDC (rounded weighted-average form).
        entry_fees_usdc:
          type: number
          format: double
          description: >-
            Attributed BUY-fee portion of `gross_entry_cost_usdc`, 6-decimal
            grain.

            SELL fees are exit costs and are excluded.
        first_entry_at:
          type: string
          description: First acquisition time, RFC3339.
        first_entry_at_micros:
          type:
            - integer
            - 'null'
          format: int64
          description: Epoch-micros of `first_entry_at`; `null`/absent on the NULL tail.
        gross_entry_cost_usdc:
          type: number
          format: double
          description: >-
            Exact fee-inclusive entry basis at 6-decimal grain; formatting to 6
            dp

            recovers the stored value. Do NOT reconstruct it as

            `entry_cost_usdc + entry_fees_usdc`; `entry_cost_usdc` is rounded
            WAC;

            the fee-exclusive basis is `gross_entry_cost_usdc −
            entry_fees_usdc`.
        legs:
          type: array
          items:
            $ref: '#/components/schemas/ComboLeg'
          description: The combo's legs, in leg order, with market and event enrichment.
        legs_pending:
          type: integer
          format: int32
          description: Legs still awaiting resolution.
        legs_resolved:
          type: integer
          format: int32
          description: Legs whose markets have resolved.
        legs_total:
          type: integer
          format: int32
          description: Number of legs in the combo.
        outcome_index:
          type: integer
          format: int32
          description: Index of the combo outcome held; `999` means unlabelable.
        outcome_label:
          type: string
          description: Label of the combo outcome held.
        proxy_wallet:
          type: string
          description: |-
            The holder's wallet. `proxy_wallet` on every /v2 response; `user` is
            the REQUEST param vocabulary, never a response field (2026-08-19).
        realized_payout_usdc:
          type: number
          format: double
          description: |-
            Gross redemption payout received so far in USDC; turnover, not
            profit; net result = payout minus `gross_entry_cost_usdc`.
        redeemable:
          type: boolean
          description: Whether the combo can be redeemed now.
        resolved_at:
          type:
            - string
            - 'null'
          description: >-
            When the combo fully resolved, RFC3339; `null` while any leg is
            open.
        status:
          type: string
          description: |-
            Lifecycle state of the position (OPEN, REDEEMABLE, RESOLVED_WIN,
            RESOLVED_LOSS, RESOLVED_PARTIAL).
        updated_at:
          type: string
          description: Last event touching the position, RFC3339.
        updated_at_micros:
          type: integer
          format: int64
          description: Epoch-micros of `updated_at`.
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