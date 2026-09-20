# Margin

> Initial margin, maintenance margin, equity, margin modes, and withdrawal requirements

Margin is the collateral required to open and maintain leveraged positions. It
ensures traders have enough collateral to cover potential losses and gives the
system a buffer to close positions before they become insolvent.

Trading uses two thresholds. **Initial margin (IM)** is the collateral required to
open or increase a position. **Maintenance margin (MM)** is the minimum
collateral required to keep a position open. When equity drops below maintenance
margin, the position is [liquidated](/perps/learn-about-trading/liquidation-mechanics).

## Equity

Equity is the real-time value of an account, incorporating all open positions at
current Mark Price.

```text theme={null}
Equity = Collateral + UnrealizedPnL(Mark) - FeesDue - FundingDue
```

### Unrealized PnL

```text theme={null}
Long PnL = PositionSize * (Mark - EntryPrice)
Short PnL = PositionSize * (EntryPrice - Mark)
```

Because equity depends on Mark Price, equity follows live mark updates. See
[Mark Price](/perps/learn-about-trading/mark-price).

## Margin Requirements

Initial margin is set by your configured leverage:

```text theme={null}
IM = Notional / Leverage
```

Leverage tiers cap the leverage available as your position grows — larger
positions must run at lower leverage and therefore post proportionally more
initial margin. The cap is enforced against your worst-case position notional
(position plus resting orders on the heavier side): an order that would grow it
into a tier whose `max_leverage` is below your configured leverage is rejected
with `invalid_leverage`, and you must lower your leverage setting first. Your
configured leverage applies to your entire notional, not bracket by bracket.
Fetch each market's tier schedule from
[Market Data](/perps/market-data#fetch-instruments).

Maintenance margin uses a flat per-market rate, independent of position size,
tier, and your leverage setting:

```text theme={null}
MM = Notional × MMR        where MMR = 0.5 / MaxLeverage
```

`MaxLeverage` is the market's maximum leverage, so on a 20x market
`MMR = 2.5%` for every position. MM equals half the initial margin of a
position opened at max leverage; at lower leverage your IM is higher but MM
stays the same, so the gap between entry requirement and liquidation grows.

Margin requirements are static across sessions.

## Default Leverage and Margin Mode

Every position uses one of two margin modes. **Cross margin** backs the
position with the account's shared free collateral. **Isolated margin** backs
the position only with the collateral allocated to it.

The exchange may assign your account to a group with shared leverage or margin
defaults. Each setting resolves independently for each instrument:

| Priority | Leverage                                                       | Margin Mode                                                                           |
| -------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 1        | Your explicit per-instrument setting                           | Your explicit per-instrument setting                                                  |
| 2        | Your account group's default, capped at the instrument maximum | Your account group's default; cross resolves to isolated on isolated-only instruments |
| 3        | The instrument's maximum leverage                              | Isolated margin                                                                       |

Group defaults also apply to future listings. An explicit per-instrument setting
is retained when group defaults or membership change.

Set your preferred leverage and margin mode after the instrument is listed and
before placing your first order, using
[Update Leverage](/perps/trading#update-leverage). Account configuration reads
report the resolved values; an update sets both values explicitly. Setting the
current resolved pair explicitly preserves it against later group changes.

Before switching between isolated and cross margin:

* Close the position in that instrument.
* Cancel all orders in that instrument, including orders awaiting risk approval
  or execution.
* Check that the instrument supports the requested margin mode.

Leverage changes within the same mode still require sufficient margin and a
positive leverage no greater than the instrument maximum. If the account is
subscribed to the Backstop Liquidity Provider program for that instrument,
unsubscribe before updating either setting, even to re-submit the current pair.

## Margin States

An account is always in one of three states.

| State       | Condition           | What Happens                                   |
| ----------- | ------------------- | ---------------------------------------------- |
| Healthy     | `Equity >= IM`      | Normal trading                                 |
| Margin call | `MM <= Equity < IM` | Can only reduce exposure or deposit collateral |
| Liquidation | `Equity < MM`       | The system begins closing the position         |

## Margin Checks

### Pre-Trade

Before any order executes, the system verifies the account can afford it:

1. Compute the new position after the order fills.
2. Calculate required initial margin using the market's leverage tiers.
3. Reject the order if equity is below required initial margin.

This prevents accounts from entering a margin-call state through new trades.

### Continuous Monitoring

The system continuously evaluates accounts:

* If equity falls below maintenance margin, liquidation begins.
* If equity is between maintenance margin and initial margin, the account may enter reduce-only mode.

## Deposits and Withdrawals

Deposits increase equity. A deposit during margin call can restore the account to
healthy status immediately.

### Withdrawal Margin Requirements

Withdrawals must leave enough collateral to cover existing margin commitments
and at least **10% of the total notional value of all open positions**:

```text theme={null}
TotalPositionValue = sum(abs(PositionSize) * MarkPrice)
WithdrawalMarginRequired = max(CollateralReserved, 0.1 * TotalPositionValue)
```

`TotalPositionValue` includes both cross and isolated positions at current Mark
Price. `CollateralReserved` covers cross-position initial margin, isolated
collateral allocations, and margin and fee reserves for accepted open orders.
For a cross-only account with no open orders, it equals required initial margin.

The 10% floor applies to withdrawals, independently of the leverage used to open
a position. It does not increase the opening-margin requirement or change the
maintenance-margin threshold for liquidation.

For example, assume a \$100 position at 20x, no other positions or orders, and no
fees, funding, or unrealized PnL:

```text theme={null}
InitialMargin = $100 / 20 = $5
WithdrawalMarginRequired = max($5, 0.1 * $100) = $10
```

With $20 of collateral, $10 is available for withdrawal. With only \$5 of
collateral, the position can open, but nothing is available for withdrawal.
In this example, the floor is stricter than initial margin above 10x leverage;
at 10x or below, initial margin already covers the floor.

<Accordion title="How is the withdrawable balance calculated?">
  The account's withdrawal capacity in USD is:

  ```text theme={null}
  WithdrawalEquity = CollateralValue + CrossUnrealizedPnL
  Withdrawable = max(0, WithdrawalEquity - WithdrawalMarginRequired - PendingOrderMargin)
  ```

  `CollateralValue` is the account's valued collateral. `CrossUnrealizedPnL`
  includes only cross positions; isolated unrealized PnL is not added to
  withdrawal equity. `PendingOrderMargin` is the additional initial margin
  reserved for orders still awaiting risk checks.

  Use the returned `withdrawable` value from [Get Portfolio](/api-reference/get-portfolio)
  rather than subtracting initial margin from account equity. A withdrawal is
  also limited by the balance of the asset being withdrawn and is checked again
  when processed.
</Accordion>

## Adjusting Isolated Margin

For each position, `initial_margin` reports the collateral currently backing
the position. For cross positions, it is the required initial margin based on
position size, Mark Price, the applicable risk tier, and configured leverage.
For isolated positions, it is the position's current equity:

```text theme={null}
InitialMargin = SignedAllocation + UnrealizedPnL - SettledFunding
```

The isolated value is a point-in-time snapshot that changes with Mark Price and
funding. The legacy `initial_margin` name is retained for API compatibility;
`margin` would describe this value more accurately.

A positive margin adjustment moves free account collateral into the isolated
allocation. A negative adjustment releases value back to free collateral and
may include unrealized profit, so the signed allocation itself can reach zero
or become negative. The request is accepted only when the resulting position
equity remains at or above current required initial margin.

Removing isolated margin releases collateral within the account. This uses the
position's initial-margin check above; withdrawing the released collateral from
the account must also satisfy the [withdrawal margin requirements](#withdrawal-margin-requirements).

Both additions and removals are blocked while the account is in cross
liquidation or the target position is in isolated liquidation. An isolated
liquidation on a different instrument does not block the request. Cancel-only
mode does not gate margin adjustments.
