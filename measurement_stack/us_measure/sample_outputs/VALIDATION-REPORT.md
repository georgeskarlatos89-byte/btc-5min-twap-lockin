# US BTC Up/Down — BRTI proxy validation

Generated 2026-09-27T12:42:25Z. Measure-only. Proxy = mid of 4 constituent top-of-books, TWAP60 at each boundary, 2 dp, ties Up.

**Verdict: NOT READY: fewer than 200 comparisons.**

| item | value |
|---|---|
| rounds recorded | 2 |
| valid comparisons | 1 of 200 needed |
| proxy agrees with venue | 1 of 1 (100.0 %) |
| clear rounds (gap at least 2 bps) agree | 1 of 1 |
| excluded rows | NO-OPEN-REF 1 |
| settlement arrives over the websocket after | median 0.19 s, max 0.19 s |
| websocket vs HTTP settlement | 1 checked, 0 disagree |
| venue Up rate | 50.0 % of 2 |
| dead-flat rounds (proxy gap under 0.05 bps) | 0, of which 0 settled Up |

## Agreement by proxy gap

| gap | rounds | agree | agree % | 95 % interval |
|---|---|---|---|---|
| 4-8bp | 1 | 1 | 100.0 | 20.7 to 100.0 |

## Divergences

| round | proxy gap bps | proxy open | proxy close | settlement | source |
|---|---|---|---|---|---|

Limits: the venue publishes the contract settlement (about 0.01 or 0.99), not the BRTI value, so only the DIRECTION can be compared. The proxy uses 4 of BRTI's constituents and top-of-book mids instead of the full depth-weighted curve, so disagreement on razor rounds is expected.
