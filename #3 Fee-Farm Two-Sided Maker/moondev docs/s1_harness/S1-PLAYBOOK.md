# S1 — TWAP LOCK-IN · OPERATOR PLAYBOOK (the one tested strategy)

Market: Polymarket **BTC Up or Down, 5-minute and 15-minute** rounds.
Status 2026-09-19: mechanics validated (70/70 vs official at gap > 4bps),
naive version killed by backtest, refined rule below is the survivor.

## THE EDGE (one sentence)

Rounds settle on the Chainlink 60s-TWAP stream **averaged over the whole round**
vs the open print; late in a round that average is mostly locked, but the order
book keeps trading the last tick — so it lags our true-feed math.

## ENTRY — all five must hold (coded in trader.py)

1. Last **45 s** of a 5m round / last **90 s** of a 15m round.
2. Locked-TWAP gap **≥ 4 bps** from the open print (the measured 100% zone).
3. Our side's ask in **[0.55, 0.97]** — the *lagging* book. A converged opposite
   book (our ask ≤ 0.05) means OUR proxy is wrong: never traded (40% wins in backtest).
4. Post-fee edge **> 2¢**, fee = `0.07 · p · (1−p)` (official crypto taker fee).
5. Hard gates: key present, no KILL file, feed < 10 s old.

## EXECUTION / RISK (coded)

* Buy the leading side, **FOK at the ask, $10**, one trade per round, ≤ 3/hour.
* **Daily loss cap $20** → writes KILL automatically. `touch KILL` = manual kill.
* Hold to resolution (rounds are ≤ 15 min; exits would only pay spread).
* DRY RUN unless `.env` says otherwise; `FORCE_LIVE=1` = operator override of the
  statistical gates (arbiter 50 / dry 20@90%) — hard gates stay on always.

## DATA (no credentials)

* `wss://ws-live-data.polymarket.com` — topics `crypto_prices_twap_sixty`
  (settlement feed) + `crypto_prices_chainlink` (open-ref snapshot); send `PING` every 5 s.
* Book: `https://clob.polymarket.com/book?token_id=…`; round tokens from Gamma
  `markets?slug=btc-updown-{5m|15m}-{round_start_epoch}`.

## EVIDENCE SO FAR

* Semantics: full-round-avg matches official **70/70 at gap > 4bps**; <1bps = 50% coin-flip.
* Live lagging-book catches logged (book repriced to our model after our signal).
* Naive divergence trading: **killed** (40% wins) — do not resurrect.

## EXPECTATION & SCALING RULE

Small edge per trade (2–10¢ on $10) at high hit-rate. Run 2–4 weeks at $10;
scale ONLY if live PnL matches this document's rule statistics (Moon Dev I-step).
