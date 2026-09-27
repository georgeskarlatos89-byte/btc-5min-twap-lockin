# UI MY TRADES — how the two paper traders work

**Paper only.** No wallet, no keys, no order code. Every "bet" is a line in a CSV. Two separate
pretend portfolios of **$200** each: one for BTC 5-minute rounds, one for weather.

| | BTC 5-minute | Weather |
|---|---|---|
| what it watches | the live page `polymarket.com/event/btc-updown-5m-<round>` | the event pages "Highest temperature in <city> on <date>" |
| how often | every round, 288 a day | 3 bets per city per day-event, 8 cities |
| how it picks | reads the page, learns which moments pay, sizes the bet | **random** bracket, random Yes/No (control group) |
| my_choice | UP or DOWN | YES or NO on one temperature bracket |
| result used for P&L | official result (gamma) | official result (gamma) |
| service on the VPS | `ui-btc-trader` | `ui-weather-trader` |

Where things are:

| place | path |
|---|---|
| VPS folder | `~/Trading polymarket UI on vps weather and btc-5-min/` (shortcut `~/ui_trader`) |
| VPS results | `…/UI MY TRADES/btc/` and `…/UI MY TRADES/weather/` |
| this PC | `arena-ai-copy btc-5-minute workspace/UI MY TRADES/` with `btc/`, `weather/`, `code/`, `deploy/` |
| copy results to this PC | `bash deploy/pull_results_to_pc.sh` (Git Bash) |

---

## 1. BTC 5-minute trader, step by step

Think of a person sitting in front of the page all day, with a notebook.

1. **The bell rings** (every 5 minutes, on the clock: :00, :05, :10 …). The trader clicks
   "Go to live market" so the page shows the new round. If the click does not switch within
   15 s it loads the round's address directly. The address changes every round
   (`btc-updown-5m-` + the round's start time as a number), so it is computed, never guessed.
2. **It reads what the page shows**: the *Price To Beat*, the *Current Price*, the Up and Down
   prices in cents, and the **countdown** (MINS / SECS). The countdown on the page is drawn as
   rolling digits; the trader reads which digit is actually in view. It then compares the
   page's countdown with the real clock. If they differ by more than 6 s the page is late and
   the row says so.
3. **Checkpoints.** At 240, 210, 180, 150, 120, 90, 75, 60, 45, 30 and 15 seconds left it takes
   a snapshot: the page's numbers plus the real order book (what a buy would really cost).
   Every snapshot of every round is kept. That is the raw material for reading patterns.
4. **One bet per round.** At one of the checkpoints it writes down `my_choice = UP` or `DOWN`,
   the price, and the stake. The price used is the **real best ask** in the order book plus the
   fee (`0.07 × price × (1 − price)` per share), not the cents label on the page. Earlier
   measurement showed the page's cents can be 2 to 13 cents away from the real book, so the
   label is recorded next to the real price and the P&L is shown both ways.
5. **After the round** it looks up the official result and the result the page itself shows in
   its "Past" strip, and writes both. They are not always the same: the page's strip compares
   spot prices, the market pays on the 60-second average (measured earlier: the strip is wrong
   on about 1 round in 10, mostly the close ones). P&L always uses the official result.

### How it chooses the moment and the size

The "leader" is the side that is ahead right now: Up if Current Price ≥ Price To Beat, else Down.

**Pattern table.** For every checkpoint and every gap size (how far the price is from the
Price To Beat, in basis points: 0–0.5, 0.5–1, 1–2, 2–4, 4–8, 8+) it counts: how many times
was the leader at that moment the final winner, and what did each side cost. From that:

`edge of buying the leader = how often the leader wins − (ask + fee)`

**Phase LEARN (first 150 settled rounds, about 13 hours).** It does not know anything yet, so
it explores: a random checkpoint each round, the leader 3 times out of 4 and the underdog 1
time out of 4, a fixed stake of 2.5 % of the bankroll (about $5).

**Phase ADAPT (after that).** Going through the checkpoints in order, it bets at the first
one whose table cell shows an edge of at least 2 cents per share with at least 30 rounds
behind it. Stake = half of the Kelly fraction, never below 1 % and never above 6 % of the
bankroll. If no checkpoint shows an edge, it still bets (every round is traded): the better
side at 75 s left, at the minimum 1 %. One round in ten keeps exploring so the table does not
go stale. A prior keeps it honest: until a cell has data, it assumes the market price is right.

**Scaling down.** Stakes are halved after 5 losses in a row, and halved while the bankroll is
under 60 % of the start. The venue's minimum of 5 shares is respected. Prices under 2 cents or
over 98 cents are not bought (lottery tickets and sure things teach nothing).

### What happens when things go wrong

| problem | what the trader does | how you see it |
|---|---|---|
| page slow or frozen | takes the same numbers from the page's own data address and the public price feed | `data_source = api_fallback(...)` |
| browser crashed | relaunches it; bets continue on the fallback meanwhile | `browser_launches` goes up |
| internet drops | every request retries with growing waits; the price feed reconnects after 60 s of silence | rows may be `api_fallback` or `NO_LIQUIDITY` |
| nothing can be bought (one-sided book) | no bet is invented | `status = NO_LIQUIDITY`, bet 0 |
| the whole program was down | on restart it writes one row per round it could not see | `status = MISSED` |
| official result not out yet | keeps asking (every 20 s, later every 5 min, up to 24 h) | `status = OPEN` until then |

So every one of the 288 rounds of a day has a row, whatever happened.

### BTC files (`UI MY TRADES/btc/`)

| file | what is in it |
|---|---|
| `btc_trades_YYYY-MM-DD.csv` | one row per round of that day, live view (open bets included) |
| `btc_trades_all.csv` | every finished round since the start, one row each |
| `btc_round_snapshots.csv` | every checkpoint of every round with the final result next to it |
| `btc_pattern_table.csv` | the learning table in plain numbers, with a verdict per cell |
| `btc_daily_summary.csv` | per day: rounds, bets, wins, P&L, bankroll, how many rows came from the page |

Main columns of a trade row: `round_start_utc`, `slug`, `price_to_beat_ui`,
`price_to_beat_captured_utc`, `entry_slot_s_remaining`, `decision_utc`,
`decision_ui_countdown_s`, `ui_clock_drift_s`, `ui_current_price`, `gap_bps`, `ui_up_cents`,
`ui_down_cents`, `book_up_ask`, `book_down_ask`, `data_source`, `phase`, **`my_choice`**,
`choice_reason`, `entry_price_exec`, `entry_price_ui`, `fee_per_share`, **`bet_usd`**, `shares`,
`bankroll_before`, **`outcome_official`**, `outcome_ui`, `outcomes_agree`, `win`, **`pnl_usd`**,
`pnl_if_ui_price_usd`, `bankroll_after`, `notes`.

---

## 2. Weather trader, step by step

This one is random on purpose. It answers: "what does a trader with no opinion earn on these
markets after fees?" That is the baseline any real weather idea has to beat.

1. **Cities:** Hong Kong, Shanghai, Tokyo, Seoul, London, Paris, NYC, Miami. Chosen because
   they are among the most traded and they are spread around the clock.
2. Each city has one event per day with 11 temperature brackets. For every open event (today,
   tomorrow, the day after) it plans **3 bets at random times**.
3. At a planned time it opens the event page, reads every bracket's "Buy Yes / Buy No" price,
   keeps the brackets that are still undecided (Yes price between 5 and 85 cents), picks **one
   at random**, then picks **Yes (60 %) or No (40 %)** at random.
4. Cost = real best ask + fee (`0.05 × price × (1 − price)`). Stake = 2 % of the bankroll.
5. **Scaling:** results are grouped by price band (0–0.2, 0.2–0.4 …). Once a band has 20
   settled bets, stakes in that band are ×1.5 if the band made money and ×0.5 if it lost more
   than 20 %. Same two halving rules as BTC.
6. The browser is opened for the visit and closed afterwards.

### Weather files (`UI MY TRADES/weather/`)

| file | what is in it |
|---|---|
| `weather_trades_all.csv` | every finished bet (and every "no open bracket" visit) |
| `weather_trades_open.csv` | bets waiting for the official result |
| `weather_page_snapshots.csv` | all 11 brackets with the page's prices at every visit |
| `weather_price_band_table.csv` | results by price band and the stake multiplier in force |
| `weather_daily_summary.csv` | per day: bets, wins, P&L, bankroll |

---

## 3. Honest limits

- **Paper fills are optimistic.** The trader assumes it buys at the best ask it saw. A real
  order arrives later and may get a worse price or nothing.
- **The page costs a lot of computer.** On the 2-core VPS the full page used 1.1 GB and ran
  15 to 60 s behind. With animations and the chart switched off it is about 0.6 to 0.8 GB and
  usable, but when other jobs saturate the machine the page falls behind and rows become
  `api_fallback`. The services are capped (CPU 90 % / 60 % of one core, memory 1.5 / 1.1 GB)
  and yield to every other service.
- **The site marks this VPS as view-only** (its location check answers "blocked" for the
  Netherlands). Reading is fine. It only matters if real trading from this machine is planned.
- **A good pattern table is not proof of an edge.** With 35+ cells, a few will look good by
  luck. Treat a cell as real only when it keeps its edge over several days.
- The weather trader is random by design. Expect it to lose about the fee plus the spread.

## 4. Running it

```bash
# status / logs (VPS)
systemctl status ui-btc-trader ui-weather-trader --no-pager
tail -f ~/ui_trader/logs/ui_btc_trader.log

# stop / start
sudo systemctl stop ui-btc-trader ui-weather-trader
sudo systemctl start ui-btc-trader ui-weather-trader

# checks without network
cd ~/ui_trader/code && ../.venv/bin/python ui_btc_trader.py --selftest
cd ~/ui_trader/code && ../.venv/bin/python ui_weather_trader.py --selftest
```

Telegram: one summary every 6 h (BTC) and every 12 h (weather), plus an alert if the page is
unreadable for several rounds or rounds were missed. At most 4 messages an hour per trader.

---

## 5. Build log

### Part #1 — build, three live verification runs, deploy (2026-09-27)

**What existed before.** The workspace had `ui_lab` (one audited round read from the page in a
headless browser, plus a DevTools inspection). No paper trader existed. Reused from it: the
page's price element, the finding that the page's Current Price is the 60-second average
rounded to cents, and the page's own data addresses.

**Setup on the VPS.** New folder, own Python environment, Playwright 1.63 with headless
Chromium (658 MB on disk). Nothing outside the new folder was changed, except two new systemd
unit files. No existing service was touched or restarted.

**What the page looks like to a program (measured on the VPS):**

| measurement | value |
|---|---|
| full page, nothing blocked | load 36 s, 1,089 MB, page updates arrived 15 to 60 s late |
| trackers, images, fonts blocked | load 20 to 33 s |
| plus animations and chart switched off | reads in 0.02 to 1.2 s, 440 to 850 MB |
| Price To Beat appears | 23 to 28 s after the bell |
| page countdown vs real clock | page is 1.5 to 3.8 s behind (6.6 s once, under load) |
| weather event page | load 9 to 20 s, 11 brackets read |
| VPS load while testing | 9 to 19 on 2 cores, caused by another session's scoring and relabel jobs; 1.3 when they paused |

**Errors found by running it for real, and the fixes:**

| # | what went wrong | evidence | fix |
|---|---|---|---|
| E1 | my tracker block list contained `t.co`, which is inside `polymarket.com` | every page load failed with `ERR_FAILED` | block by exact host name |
| E2 | the version check line stopped my install script | browser never downloaded | removed; install re-run |
| E3 | a page-switch task from the previous round restarted the browser under the new round | log: "UI load attempt 1 failed; restarting the browser" 47 s into the next round | one switch task at a time, cancelled at the bell |
| E4 | started mid-round, it "bought" at 0.1 cent | first run, bet at 15 s left, price 0.001 | nothing under 2 cents or over 98 cents is bought; no entry at 15 s |
| E5 | countdown read was empty | the digits are rolling columns and the label is lowercase `mins` | reads the digit with no vertical offset; verified 180 on the page at 180 s left |
| E6 | page read took 4 s | it scanned the whole document every time | the four elements are found once and remembered |
| E7 | order book blank at many checkpoints | snapshot file: both asks empty while the page showed 88/13 cents | 4 s timeout was too short while the browser had the CPU; longer timeout, retries, errors now logged |
| E8 | with a one-sided market the page shows one cents label, and I called it "Up" | row: `ui_up 0.01` while Up's real bid was 0.99 | labels are matched to Up/Down by the text around them |
| E9 | the page's own past result came back empty | first settled row: `outcome_ui` blank | asked by round id (`outcomesBySlug`) |
| E10 | a full round ended with no bet | 14:50 round: planned checkpoint late, prices already at the extremes | enters early when one side's ask reaches 90 cents; uses the page price only if the book cannot be read at the last checkpoint, and says so |
| E11 | weather: 6 visits wasted on days already decided | six `NO_OPEN_BRACKET` rows for Asian and European cities | an event is planned only until 14:00 local time of its day |
| E12 | weather: every random pick became YES | book read failed, so "No had no ask" switched the side | side is switched only when the book was read and really had no ask |
| E13 | self-tests wrote into the live log | five lines dated before the service start | self-tests print to the screen only |
| E14 | my "wait until late in the round" check returned at once | it printed at 15:19:11 | wait target computed from the round grid |

Found after the first deploy, on live rounds (versions 1.0.3 to 1.0.5):

| # | what went wrong | evidence | fix |
|---|---|---|---|
| E15 | the page was unreadable at 10 of 17 checkpoints | snapshot file: `api_fallback(ui_unavailable)` | my CPU cap of 90 % of one core starved the browser; raised to 130 %, priority stays low so the other services win any contest |
| E16 | a full page reload every round cost 22 to 26 s | log: "UI loaded by full page load (24s) at +39s" | waits up to 10 s for "Go to live market" and switches in place: page on the new round 9 to 12 s after the bell |
| E17 | after switching in place the page still showed the previous round's Price To Beat | 84573.67 logged for two rounds in a row, then 84526.23 for three | the old panel stays in the document, hidden; only elements that are actually drawn are read, and the memory of elements is dropped at every round change |
| E18 | my cross-check rejected correct values | page 84526.23 vs "page data" 84504.99 | the data address returns the spot open unless asked with the averaging options; the page displays the 60-second-average open. Now asked the same way: 84448.28 vs 84448.28 |
| E19 | a restart in the middle of a round could have bet twice on it | code review before restarting the live service | a round that already has a row is not traded again |

**A finding worth keeping (not a bug).** In the 14:55 round the page's Current Price was 1.6 bps
ABOVE the Price To Beat, so a person watching the page would call Up the leader. The real
order book at the same second priced Down at 94 cents, and Down won. The page's price is a
60-second average and lags; the book does not. The pattern table now learns the two cases
separately ("page leader = market favourite" and "page leader is NOT the market favourite").

**Verification runs (test data kept on the VPS in `test_run/`, not in the results):**

| run | rounds | result |
|---|---|---|
| BTC run 1 (v1.0.0) | 2 | exposed E3, E4, E5 |
| BTC run 2 (v1.0.1) | 4 | 2 bets settled (1 win, 1 loss), 2 rounds without a bet, 35 checkpoint snapshots, 27 of them read from the page; exposed E7 to E10 |
| weather run 1 (v1.0.2) | 11 visits | 5 bets, 6 no-open-bracket, 121 bracket prices read; exposed E11, E12 |
| weather run 2 (v1.0.2 fixed) | 2 visits | 2 bets, both with real book prices (0.93 vs page 0.92, 0.76 vs page 0.76) |
| offline self-tests | both traders | pass: sizing, fee, settlement, one-sided books, early entry, streak scaling, missed-round backfill, bracket parsing |

**Deploy.** `ui-btc-trader` and `ui-weather-trader` enabled and started 2026-09-27 15:18:14 UTC,
clean $200 each. Units verified with `systemd-analyze`. Fleet check after start: all 15 earlier
services still running, none failed.

**First 40 minutes live (15:18 to 15:56 UTC, real rounds, paper money):**

| item | value |
|---|---|
| BTC rounds recorded | 7 (one row each): 6 bets settled, 1 round with nothing buyable, 0 missed |
| BTC result | 2 won, 4 lost, P&L −$17.45, bankroll $182.56. This is the LEARN phase: random checkpoints and sides, so no conclusion can be drawn from it |
| page vs official result | 7 of 7 agree |
| checkpoints read from the page, final version | 7 of 9 (the other 2 fell back to the feed); before the fixes: 10 of 58 |
| page countdown vs real clock, final version | page 3.3 to 5.1 s behind |
| Price To Beat, page vs its own data | 84448.28 vs 84448.28, 84475.08 vs 84475.22, 84429.98 vs 84429.98 |
| switching rounds in place | page on the new round 9 to 25 s after the bell |
| weather | 3 bets open (Miami YES, Hong Kong NO, Paris NO), 51 more planned over the next 30 h; 2 of 3 visits had to use API prices, fixed in v1.0.6 (higher CPU cap, one retry) |
| memory | BTC trader 610 to 680 MB, weather trader 18 to 50 MB outside visits |
| fleet | 17 services running, none failed, no restarts |

**Versions:** 1.0.0 first build, 1.0.1 to 1.0.2 fixes E3 to E13 from the verification runs,
1.0.3 in-place switch and restart safety, 1.0.4 to 1.0.5 Price To Beat check and visible-element
reading, 1.0.6 weather page retry. Each row carries the `code_version` that produced it.

### What to expect next

- **BTC:** the LEARN phase needs 150 settled rounds, about 13 hours. Expect to lose money in it:
  it buys at random moments and pays the fee every time. After that the stakes follow the
  pattern table. A cell needs 30 rounds before it can be acted on, so the table fills over 2 to
  3 days.
- **Weather:** results arrive when each day's market resolves, so the first settled bets show
  up tomorrow. Random picks should lose roughly the fee plus the spread.
- **If this VPS gets busy again** more rows will say `api_fallback`. The daily summary counts
  them, so the share is always visible. A 4-core machine would remove the problem.

### Part #2 — VPS upgraded to 4 cores (2026-09-27, evening)

The user resized the VM in the cloud console from e2-medium (2 shared cores, 4 GB) to
e2-standard-4 (4 cores, 16 GB). The stop changed the external address from 34.34.13.7 to
34.7.18.36, which was then promoted to a static address so it survives future restarts. All
services came back on their own at boot.

| measure | 2 cores | 4 cores |
|---|---|---|
| checkpoints read from the page | 7 of 9 at best | 77 of 77 |
| page behind the real clock | 3.3 to 5.1 s | median 0.5 s, worst 1.6 s |
| page on the new round after the bell | 9 to 25 s | 8 to 9 s, every round |
| memory free on the machine | 1.7 GB | 13 GB |

The downtime was recorded as 18 MISSED rounds (16:15 to 17:40 UTC). Bankrolls and open bets
survived. Caps raised to use the headroom: BTC trader CPU 250 %, memory 2.5 GB; weather trader
CPU 150 %, memory 1.5 GB. Both still run at low priority. Result at 18:30 UTC: 38 rounds
recorded, 18 settled (12 won, 6 lost), P&L −$11.28, bankroll $188.71, still in the LEARN phase.

### Part #3 — order book refused the trader's requests, fixed in 4 minutes (2026-09-27 19:26 UTC)

**What happened.** During a routine check the log showed `HTTP Error 403: Forbidden` on every
order book read, starting 19:26:32 UTC. One round (19:25) ended without a bet.

**What it was not.** The VPS address was not blocked: `curl` from the same machine got normal
answers, and no other strategy on the machine logged a refusal.

**What it was (E20).** My data requests carried the browser's identity string ("Chrome on
Windows") although they come from a Python program. The order book endpoint started refusing
exactly that combination. Test from the VPS, same second, same address:

| identity sent with the request | order book answer |
|---|---|
| browser identity, from Python | 403, a block page |
| `curl/8.5.0`, from Python | 200 |
| honest identity `polymarket-ui-paper-trader/1.0 (read-only research; python-urllib)` | 200 on the order book, market data and page data |

**Fix (v1.0.7).** Data requests now identify themselves honestly. The browser identity is used
only by the real browser. Request volume is unchanged and small: two book reads per checkpoint,
about 22 per round. Added a Telegram alert when book reads fail 8 times in a row, because this
failure was silent until someone looked at the log.

**State at 19:27 UTC, before the fix:** 49 rounds recorded, 0 rounds without a row, 29 settled
(18 won, 11 lost), P&L −$23.38, bankroll $176.61. Since the move to 4 cores, 198 of 198
checkpoints were read from the page, the page ran a median 1.1 s behind the clock.

**Early pattern, NOT a conclusion (18 bets):** buying the side the market already favours won
11 of 13 (+$3.68); buying the other side won 1 of 5 (−$10.44). When the page's leader and the
market's favourite disagreed (14 checkpoints), the page's leader won only 6 times. The page's
own "past result" disagreed with the official result on 4 of 31 rounds, all of them rounds
decided by less than 2.5 basis points.
