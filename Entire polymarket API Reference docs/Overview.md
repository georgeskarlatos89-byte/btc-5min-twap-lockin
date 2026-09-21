# Overview

> Explore the APIs available for building with Polymarket Predictions.

## APIs

Polymarket Predictions comprises several APIs, each responsible for a distinct
part of an integration.

<CardGroup cols={2}>
  <Card title="Gamma API" icon="database">
    **`https://gamma-api.polymarket.com`**

    Discover events and markets, and retrieve the metadata needed to work with
    them.
  </Card>

  <Card title="CLOB API" icon="arrows-rotate">
    **`https://clob.polymarket.com`**

    Read live market state, then place and manage orders.
  </Card>

  <Card title="Data API" icon="chart-line" href="https://data-api.polymarket.com/v2/docs">
    **`https://data-api.polymarket.com`**

    Understand account and market activity after discovery and trading. v2 is
    now available, with cursor pagination and a shared response envelope.
  </Card>

  <Card title="Relayer API" icon="bolt">
    **`https://relayer-v2.polymarket.com`**

    Submit supported wallet transactions without requiring the account to hold
    POL for gas.
  </Card>

  <Card title="WebSocket APIs" icon="radio">
    **Realtime streams**

    Keep an integration updated with market, account, sports, and RFQ events.
  </Card>

  <Card title="Bridge API" icon="arrow-right-arrow-left">
    **`https://bridge.polymarket.com`**

    Fund and withdraw from Polymarket using supported assets.
  </Card>
</CardGroup>

## Before You Integrate

<CardGroup cols={2}>
  <Card title="Rate Limits" icon="gauge" href="/api-reference/rate-limits">
    Review request limits for each service and endpoint.
  </Card>

  <Card title="Geographic Restrictions" icon="globe" href="/api-reference/geoblock">
    Check where order placement is available.
  </Card>
</CardGroup>
