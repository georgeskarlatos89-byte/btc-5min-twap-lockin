# Error Codes

> Source: https://moondev.com/docs#errors · captured 2026-09-23

## Error Codes  
  
HTTP Code| Meaning  
---|---  
200| Success  
400| Bad request - missing or invalid parameters  
401| Unauthorized - Invalid or missing API key  
404| Endpoint not found  
410| Gone - endpoint permanently retired  
429| Rate limit exceeded  
500| Server error  
503| Service busy - retry shortly  
  
### Machine-Readable Error Codes

Error responses include a `code` field so clients can branch without parsing messages:

Code| Status| Meaning  
---|---|---  
missing_api_key / invalid_api_key| 401| No key / wrong key  
stream_key_expired| 401| Daily moonstream_* key rotated (~24h validity) - fetch a current key  
rate_limited| 429| Rate limit exceeded. Includes clients retrying dead keys - after ~5 explanatory 401s, dead-key retries collapse to 2/min per IP  
endpoint_retired| 410| Permanent - the endpoint has been removed (retired HLP funding/trades and SafeLaunch endpoints)  
fills_scanner_busy| 503| Fills scanner at its concurrency limit - retry shortly or use your fallback source. Do not treat as an empty result.  
window_required| 400| A time window (minutes or since_ms) is mandatory on /api/user/{address}/flow - it answers recency questions  
  
Getting 401 errors? [Get your API key here.](<https://moondev.com/t/docs>)
