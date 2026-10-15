# Step 1 result: the real order loop, measured with tiny live orders (2026-10-15, owner-authorized)

What ran: `~/pm/latency_probe.py` on twapvm (Netherlands), account 2, py_clob_client_v2 signing path
(the same one the 2026-10-10 live bot used). Each order: post-only BUY, 5 shares at 0.01 = $0.05 at risk,
cancelled right after the acknowledgement. Three runs, 109 orders posted in total.

| run (UTC) | orders | result |
|---|---|---|
| 12:31 | 60 posted | all acknowledged; my cancel call used the wrong argument shape, so the 60 bids rested for ~90 s, then were cancelled by id (60/60) |
| 12:33 | 4 posted | the round's Up side was dying (ask at 0.01): 3 orders rejected as "marketable BUY, min size $1", 1 order MATCHED: we bought 5 Up shares for $0.05 (balance 32.4621 -> 32.4121) |
| 12:35 | 45 posted | 45 cancelled, 0 leftovers, probing only the likely-winner side (its ask is far from 0.01) |

Lesson kept in the code: never rest a 1-cent bid on the side that is about to lose; the bot's own quotes never
go below 0.02 and always sit inside the pair cap, so this cannot happen there.

## The numbers (milliseconds, VM under heavy load from the 19-day replay running at the same time)

| leg | run 12:31 (n=20 per phase) | run 12:35 (n=15 per phase) |
|---|---|---|
| sign an order locally | 25 median | 26 median |
| POST /order, order already signed | 57 median, p90 83 | 73 median, p90 248 |
| POST /order incl. signing | 99 median, p90 384 | 74 median, p90 124 |
| DELETE /order (cancel) | not measured (bug) | 68-93 median |
| book change reaches us (server stamp to receipt) | 12.3 median | 12.4 median |
| **book change to our order acknowledged** | **72 median, p90 216** | **128 median, p90 265** |

Earlier, read-only (12:06): authenticated GET 38.5 ms median, so the matching engine itself adds roughly
20-35 ms on top of the plain round trip for an order.

## What it means

- The reaction time from this VM, as it is today, is about 70-130 ms typical and about 250 ms in the
  bad tail. The old loop was 1,000-1,400 ms. The network was never the problem; the bot design was.
- The tail (p90) is dominated by VM load: the replay was using 3 cores at the time, and the bot's own
  loop drift reached 150 ms during the same minutes. A quiet VM should sit near the 70 ms median.
- For the step-3 replay the honest bucket is lat = 0.1 s (0.05 s is the best case, 0.2 s the bad tail).
- Co-location would remove most of the ~45 ms network part, not the ~25 ms signing or the engine time.

## Files

- VM: `~/pm/results/latency_probe_2026-10-15T123120Z.*`, `...T123339Z.*`, `...T123505Z.*` (json + md with
  full logs), copied to the repo `paired_maker/results/`.
- Code: repo `paired_maker/latency_probe.py` (owner-run only; it places real orders).
