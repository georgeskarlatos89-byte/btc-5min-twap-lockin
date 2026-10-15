# Latency probe (read-only) 2026-10-15T120643Z - btc-updown-5m-1792065900

| leg | median ms | p10 | p90 | n |
|---|---|---|---|---|
| sign_ms | 14.8 | 13.6 | 19.5 | 30 |
| auth_get_warm_ms | 38.5 | 34.5 | 45.9 | 30 |
| feed_lag_ms | 10.5 | 9.0 | 67.7 | 30 |
| event_auth_get_ms | 39.7 | 34.6 | 121.8 | 30 |
| event_to_resp_ms | 52.1 | 44.7 | 180.8 | 30 |

- auth GET cold (first call): 35 ms; derive api key 188 ms; SDK pre-checks tick 56 ms, neg_risk 53 ms (once per token)
- event_to_resp = book event server timestamp -> authenticated response received = the reaction time floor from this machine

## log

    market btc-updown-5m-1792065900 up ...76642557
    client ready sig_type=3 funder=...B6A3Ed derive_api_key 188 ms
    SDK pre-checks (first call only, cached after): tick_size 56 ms, neg_risk 53 ms
    sign only: {'n': 30, 'median': 14.8, 'p10': 13.6, 'p90': 19.5, 'min': 13.1, 'max': 64.4}
    auth GET cold (first): 35 ms
    auth GET warm: {'n': 30, 'median': 38.5, 'p10': 34.5, 'p90': 45.9, 'min': 33.3, 'max': 62.5}
      E #1 feed lag 13 ms, auth GET 50 ms, event->response 64 ms
      E #2 feed lag 10 ms, auth GET 40 ms, event->response 50 ms
      E #3 feed lag 10 ms, auth GET 39 ms, event->response 49 ms
      E #4 feed lag 10 ms, auth GET 32 ms, event->response 41 ms
      E #5 feed lag 10 ms, auth GET 39 ms, event->response 50 ms
      E #6 feed lag 10 ms, auth GET 43 ms, event->response 53 ms
      E #7 feed lag 22 ms, auth GET 284 ms, event->response 307 ms
      E #8 feed lag 138 ms, auth GET 43 ms, event->response 181 ms
      E #9 feed lag 95 ms, auth GET 122 ms, event->response 217 ms
      E #10 feed lag 10 ms, auth GET 38 ms, event->response 49 ms
      E #11 feed lag 10 ms, auth GET 41 ms, event->response 52 ms
      E #12 feed lag 9 ms, auth GET 38 ms, event->response 47 ms
      E #13 feed lag 11 ms, auth GET 34 ms, event->response 45 ms
      E #14 feed lag 12 ms, auth GET 35 ms, event->response 46 ms
      E #15 feed lag 24 ms, auth GET 134 ms, event->response 159 ms
      E #16 feed lag 10 ms, auth GET 55 ms, event->response 65 ms
      E #17 feed lag 40 ms, auth GET 39 ms, event->response 80 ms
      E #18 feed lag 11 ms, auth GET 36 ms, event->response 46 ms
      E #19 feed lag 10 ms, auth GET 46 ms, event->response 56 ms
      E #20 feed lag 9 ms, auth GET 33 ms, event->response 42 ms
      E #21 feed lag 9 ms, auth GET 40 ms, event->response 49 ms
      E #22 feed lag 10 ms, auth GET 43 ms, event->response 54 ms
      E #23 feed lag 9 ms, auth GET 35 ms, event->response 44 ms
      E #24 feed lag 68 ms, auth GET 39 ms, event->response 107 ms
      E #25 feed lag 11 ms, auth GET 36 ms, event->response 47 ms
      E #26 feed lag 15 ms, auth GET 41 ms, event->response 56 ms
      E #27 feed lag 22 ms, auth GET 37 ms, event->response 59 ms
      E #28 feed lag 37 ms, auth GET 36 ms, event->response 73 ms
      E #29 feed lag 43 ms, auth GET 44 ms, event->response 87 ms
      E #30 feed lag 9 ms, auth GET 42 ms, event->response 51 ms
