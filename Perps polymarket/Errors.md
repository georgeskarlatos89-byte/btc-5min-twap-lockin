# Errors

> Error messages returned by the API and the conditions that trigger them

The API returns descriptive error messages when a request is rejected. Use the
HTTP status and response body to decide whether to retry or correct the request.

## Service Unavailable

The API returns `503 Service Unavailable` for temporary overload or a response
timeout on operations that support safe retries.

```json theme={null}
{
  "status": "err",
  "error": "service_unavailable"
}
```

### Transient Overload (`503`)

A `503` does not prove that the request did not execute. A load-shed rejection
happens before dispatch, but an engine response timeout can occur after the
request was admitted.

| Operation                                                                                       | Retry Action                                                                                        |
| ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Reads and idempotent state updates, such as setting leverage                                    | Retry with backoff.                                                                                 |
| Signed balance changes, such as isolated-margin adjustments, withdrawals, or internal transfers | Resend only the exact original signed request. Do not generate a new signature, salt, or timestamp. |

* Honor `Retry-After` when present; its value is a delay in whole seconds.
* Batch endpoints return a one-element array for a whole-request rejection.
* A `500` with `internal_error` can indicate an unexpected failure or an
  indeterminate outcome. Follow the operation's recovery guidance before retrying.

### Recover an Isolated-Margin Adjustment

For `PATCH /v1/trade/margin`, `amt` is a signed delta, not a replacement
balance. After a `503`, retry only the exact original signed payload. A newly
signed request is a separate adjustment and can apply the delta again.

A `signature_already_used` rejection means the original signed request was
ingested; it does not confirm that the adjustment succeeded. Its decision may
still be pending or may have rejected the adjustment.

`GET /v1/account/portfolio` reports committed state without a per-request
outcome. Neither an unchanged balance nor a matching delta proves what happened:
the request may still be pending, or another adjustment may explain the change.

Do not sign another adjustment for the same account until the original outcome
has been confirmed out of band. No public endpoint currently reports that
request's terminal outcome.

## Order Placement Errors

| Error                                          | Condition                                          |
| ---------------------------------------------- | -------------------------------------------------- |
| `invalid signature`                            | EIP-712 signature verification or timestamp failed |
| `signature expired`                            | Signature older than 5 minutes                     |
| `account not found for proxy`                  | Signer proxy not linked to any account             |
| `no orders provided`                           | Empty order array                                  |
| `FOK orders cannot be post-only`               | FOK + `post_only=true`                             |
| `IOC orders cannot be post-only`               | IOC + `post_only=true`                             |
| `GTC orders require a price`                   | GTC without price                                  |
| `price cannot be zero`                         | Price = 0                                          |
| `client order id cannot be all zeros`          | Client order ID is all zeros                       |
| `price exceeds allowed decimal places`         | Too many decimal places in price                   |
| `price exceeds allowed significant figures`    | More than 5 significant figures in price           |
| `quantity must be positive`                    | Quantity is zero or negative                       |
| `quantity exceeds allowed decimal places`      | Too many decimal places in quantity                |
| `quantity exceeds allowed significant figures` | More than 5 significant figures in quantity        |
| `command expired`                              | `exp_ms` is in the past                            |
| `command expiry too far in future`             | `exp_ms` is more than 5 seconds from now           |

## Modify Order Errors

Modify Order returns one result per requested order. A rejected modification
does not change the live order, although a separately processed fill or
cancellation can still change its state.

| Error                              | Condition                                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------------------------ |
| `modify_already_pending`           | Another modification for the same order is still in progress.                              |
| `modify_no_op`                     | The requested price and total quantity equal the live order values.                        |
| `modify_limit_reached`             | The order has already reached 10,000 successful modifications.                             |
| `modify_would_cross`               | The modified order would lock or cross the live opposite best price.                       |
| `duplicate_order_in_batch`         | An earlier item in the same batch resolved to the same order ID.                           |
| `order_not_modifiable`             | The order is not an eligible standalone resting GTC limit order.                           |
| `order_has_tpsl`                   | The order is a TP/SL leg or has attached or order-scoped TP/SL.                            |
| `modify_quantity_not_above_filled` | The requested new total quantity is less than or equal to the live cumulative fill.        |
| `order_unknown`                    | The client order ID does not resolve to an order owned by the account.                     |
| `order_not_in_orderbook`           | The order ID is unknown, terminal, owned by another account, or otherwise not disclosable. |
| `order_in_flight`                  | The order is still in creation or taker delay and is not yet resting.                      |
| `invalid_command`                  | A per-item price, quantity, or notional validation failed before sequencing.               |

Shared account, instrument, margin, position, reduce-only, and rate-limit errors
can also reject a modification under the same conditions as a new order.

## Order Cancellation Errors

A cancel sent while an order is awaiting risk checks succeeds immediately,
unless the account is being liquidated. The canceled order will not enter
the order book.

A cancel sent while the order's accept is still on its way to the matching
engine does not fail: the exchange holds one such cancel, applies it as soon as
the accept completes, and answers with the final outcome — success once the
order is removed, or `order_already_terminal` if the order filled or was
rejected first. If the exchange disables this behavior, is in cancel-only
maintenance mode, or the account is being liquidated, such a cancel returns
`order_in_flight` instead.

| Error                      | Condition                                                                                                                                                               |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `order_not_found`          | Order doesn't exist                                                                                                                                                     |
| `order_not_pending_engine` | An earlier cancel for the same order is already queued or in flight                                                                                                     |
| `order_not_in_orderbook`   | Order is not in the active book                                                                                                                                         |
| `order_in_flight`          | Order is not yet cancellable: in taker delay, the account is being liquidated, or acceptance is in flight while held cancels are disabled or cancel-only mode is active |
| `order_already_terminal`   | Order filled, was canceled, or was rejected before this cancel                                                                                                          |

## Auto-Cancel Errors

Returned when arming the auto-cancel switch with `PATCH /v1/trade/auto-cancel`.
Disarming skips these checks and is always allowed. A deadline already in the
past is rejected earlier with a plain `400` message.

| Error                             | Condition                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------ |
| `auto_cancel_deadline_too_soon`   | Deadline is less than 5 seconds in the future                                  |
| `auto_cancel_daily_limit_reached` | Account already triggered auto-cancel the maximum number of times this UTC day |
| `auto_cancel_in_flight`           | A previous trigger is still cancelling the account's open orders               |

`auto_cancel_in_flight` is transient. Arming succeeds once the engine finishes
the earlier cancellation.

## Update Leverage Errors

Returned when a leverage or margin-mode update is rejected. Leverage must be
positive; a zero leverage is rejected earlier with a plain `400` validation
message. A disabled instrument is not rejected merely because it is disabled.

| Error                  | Condition                                                                                                                                                                                           |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `instrument_not_found` | The instrument ID has never been listed                                                                                                                                                             |
| `invalid_leverage`     | The requested leverage exceeds the instrument's maximum                                                                                                                                             |
| `position_exists`      | A margin-mode switch was requested while a position is open in the instrument                                                                                                                       |
| `open_orders_exist`    | A margin-mode switch was requested while orders exist in the instrument, including orders awaiting risk approval or execution                                                                       |
| `blp_leverage_locked`  | The account is subscribed to the Backstop Liquidity Provider program for the instrument, even if the update re-submits the unchanged pair; unsubscribe before pinning or changing its configuration |

## Isolated Margin Adjustment Errors

These stable identifiers are returned when an isolated margin adjustment is
rejected after sequencing.

Gateway signature validation can reject a stale or future-skewed timestamp
earlier as `invalid signature`; that request never reaches sequencing.

| Error                                | Condition                                                                   |
| ------------------------------------ | --------------------------------------------------------------------------- |
| `position_not_found`                 | The target open position does not exist                                     |
| `invalid_margin_mode`                | The target position is not using isolated margin                            |
| `invalid_margin_amount`              | The amount is zero, over-precise, or cannot be represented safely           |
| `insufficient_margin`                | An addition exceeds available, unreserved collateral                        |
| `account_liquidating`                | Cross liquidation is active, or the target isolated position is liquidating |
| `margin_below_required_initial`      | A removal would leave position equity below current required initial margin |
| `invalid_margin_signature_timestamp` | Sequencer found the timestamp over five minutes old or one minute ahead     |
| `signature_already_used`             | The exact signed margin request has already been ingested                   |
