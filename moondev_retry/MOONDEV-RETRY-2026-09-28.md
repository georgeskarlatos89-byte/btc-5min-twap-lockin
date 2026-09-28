# Moon Dev liquidation feed: immediate retry on a cut-off answer (2026-09-28)

Applies to three strategies on `twapvm`: **S3** (`s3_feefarm/s3_maker.py`), **S46**
(`s46_coinflip_cascade/s46_harvester.py`), **S10** (`s10_volbox/s10_box.py`).

## Why

The hourly status report listed `moondev liq error JSONDecodeError(...)` on S3 and S46. Measured
on the VPS over each bot's whole log:

| bot | cut-off answers | timeouts | key rejected (401/403) | rate limited (429) | rounds skipped because the feed was stale | longest run of failures in a row |
|---|---|---|---|---|---|---|
| S3 | 42 | 5 | 0 | 0 | 0 | 3 (about 6 s) |
| S46 | 14 | 2 | 0 | 0 | 0 | 1 |
| S10 | 9 | 1 | 0 | 0 | 0 | 1 |

The answer is about 290 KB of JSON and is polled every 2 seconds. Now and then the download
arrives cut off mid-way (`JSONDecodeError`, `IncompleteRead`). The poll was simply lost until the
next one. No bot ever went blind: that needs 60 seconds without a valid answer. Key test at the
same time: 36 of 36 polls valid on all three keys.

So the effect was small. The retry makes it smaller and keeps the error count in the status
report for problems that matter.

## What changed

One helper, `moondev_fetch_json(url, hdr, tries=2)`, inserted above `moondev_loops()` in each
file, and the three lines that downloaded the liquidation answer now call it.

| situation | before | now |
|---|---|---|
| answer cut off (incomplete JSON or incomplete download) | poll lost, logged as an error | asked again at once, one time; logged as "answer cut off … asking again at once" |
| second answer also cut off | n/a | logged as `moondev liq error …` exactly as before |
| key rejected 401/403 | logged, bot goes flat | unchanged, never retried |
| rate limited 429 | 30 s back-off | unchanged, never retried |
| timeout | logged | unchanged, never retried (a retry would block the loop another 10 s) |

Nothing else was touched. Strategy constants are byte-identical before and after in all three
files (checked). The imbalance feed keeps its old code: its answer is small and has not failed
this way.

## Verification

Offline test (`test_mdretry.py`, no network) on the helper as it sits in each file: good answer
1 call; cut-off then good = 2 calls and the good answer returned; two cut-off answers = error
after exactly 2 calls; 401, 403, 429, 500 and timeout = 1 call, not retried; the retry log line
contains neither "error" nor "HTTP", so a successful retry is not counted as a fault.

## Install

Live files were read from the VPS, not from older local copies, and their checksums were
compared again right before installing (the files are shared between sessions). Backups next to
each file: `*.pre-mdretry-20260928`. Installed at 05:39:05 UTC, in the part of the rounds where
no quotes are resting (5m round 4 minutes old, 15m round 9 minutes old). Services restarted:
`s3-maker`, `s46-harvester`, `s10-box`. All active, no tracebacks.

A real liquidation cascade was in progress at that moment ($59M in 10 s on the feed). All three
bots read it and reacted as designed within seconds of starting, which is as good a live test of
the new download path as one could ask for.

## Roll back

```bash
cp ~/s3_feefarm/s3_maker.py.pre-mdretry-20260928 ~/s3_feefarm/s3_maker.py && sudo systemctl restart s3-maker
# same pattern for s46_harvester.py (s46-harvester) and s10_box.py (s10-box)
```
