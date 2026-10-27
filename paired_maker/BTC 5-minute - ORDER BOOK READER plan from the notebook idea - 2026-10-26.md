# BTC 5-minute: ORDER BOOK READER - the plan to build the notebook idea (2026-10-26)

Source: the user's handwritten notes dated 25-10-2026 (two pages) plus three phone screenshots of the
Polymarket BTC Up or Down 5m page (line-chart icon, bitcoin icon, candles icon).

## 0. What "snapshot like clicking inspect" is, and why we already have it

"Inspect" (F12 in the browser, Network tab) shows the page talking to the exchange:
- a websocket `wss://ws-subscriptions-clob.polymarket.com/ws/market` that pushes `book` (full snapshot),
  `price_change` (one level changed), `last_trade_price` (a trade happened) and best bid/ask messages;
- REST calls `https://clob.polymarket.com/book?token_id=...` (one snapshot) and `/midpoint`, `/price`;
- a second websocket (RTDS) with the Chainlink bitcoin price the market settles on (the bitcoin icon line)
  and the 60-second TWAP (the tooltip "TWAP resolved market": the result is the average of the last 60 s
  of the round versus the average of the first 60 s, not one print).
We do not need the browser: our recorder stored exactly these messages for 19 days (26 Sep - 14 Oct,
5,472 rounds, 1.8-3.1 GB per day) and the paired-maker bot reads the live stream 12 ms after the server
stamps it. "Taking a snapshot" is one line of code. The archive is a snapshot at every change of every
round, so every question below can be answered on recorded data first (no live money, no waiting weeks).

## 1. Build: the book reader (the "inspect" tool as a program) - 1 day

`book_reader.py` on twapvm, read-only, no keys. Two modes:
- live: every second print one line per side: best bid, best ask, spread, size at the top 5 levels,
  bid-vs-ask size imbalance, taker buys vs sells in the last 10 s, seconds left in the round, Chainlink
  price vs price-to-beat, Up "chance" (= Up share price). `--snapshot` prints one full book and exits.
- replay: the same lines from the archive for any past round at any second, so we can look at what the
  book looked like right before a fill or a sweep.
Output: CSV per round in `~/pm/book_reader/` + a plain-text print. This is also the eye we use to check
every result below by hand.

## 2. Test: each notebook question as a measurement on the 19 recorded days - 2-3 days

All on the queue-aware replay we already have (our order joins a level behind what rests there; a trade
at our level eats the queue first; latency 0.1 s = measured). Pass/fail per idea, with the same gate as
the paired-maker plan: profit range (bootstrap 95% CI) low end > 0 AFTER fees and rebates, at lat 0.1 s.

2a "Will a taker trade at my price?" - fill probability table: a 20-share bid at best bid / 1c / 2c
   below, posted at second e, (i) chance it fills before the round ends, (ii) what that share is worth at
   settlement (the adverse-selection number). Known so far: at best bid, continuous quoting, fills were
   worth -3.9c/share at settlement (#11 autopsy), i.e. takers mostly hit us when they know something.
2b "Place at specific times": post once at 1:00, 2:00, 3:00, 4:00, 4:30, 4:53 left, hold to settlement
   or until paired. Fill rate and edge per time bucket. Known so far: for the always-on quoter the late
   fills were the worst (-4.8c/share at 180-240 s, -12.1c at 240-300 s); a single timed post is a
   different thing and has not been tested.
2c "Numbers people don't think of": measure resting size at each cent level (odd vs even cents, x0/x5
   "round" levels vs the rest). Then post one cent off the crowded level and compare fill rate and edge.
   Note: the grid is 1 cent in the middle; it becomes 0.1 cent only above 96c / below 4c (that is why the
   dying Down side showed "0.1 cent"). So "odd numbers" means odd cents, not fractions.
2d "Read the flow, then place": define flow = bid/ask imbalance + sweep detector (trades in the last 1-5 s)
   + Chainlink move vs target. Rules: post only when flow is toward our side; pull the quote the instant a
   sweep starts (our loop is ~70-130 ms, old one was 1 s). Measure whether this lifts fill quality from
   -3.9c/share to >= 0.
2e "Sell immediately with a limit order to remove a position": for every filled leg compare three exits:
   A sell at the best bid at once (that is a taker trade, so the taker fee applies), B buy the other side
   so the pair costs < $1 (what the bot does now, pair cap 0.97), C hold to settlement. Profit per exit.
2f "Earn daily USDC rebates": pull the published maker-rebate rule for these markets and add it to every
   P&L line. Known so far: gabigol received $144 in 24 h on $38.5k of maker fills (about 0.37%); that is
   not enough by itself to cover -3.9c/share (3.9%), so a rule must win before rebates to pass.
Output: `ORDER BOOK READER - RESULTS - <date>.md` with one table per idea and a PASS/FAIL line each.

## 3. If a rule passes: wire it into the paired-maker bot - 1-2 days

`pm_bot.py` is already event-driven (reacts to each book change, DRY by default, cancel/replace in
~70-130 ms, kill file, daily stop). Add the passing rule as a mode, run DRY one day and compare its paper
fills to the replay, then the 5-share live queue test (>= 100 pairs) only on the owner's explicit go,
account 2 ($32.41). Co-location form (step 5 of the latency plan) only if the live test is also positive.

## 4. If nothing passes: write it up and stop (same sunset rule as #11)

## What is already known going in (honest)
- #11 open-book maker: posting at best bid all round lost -2.4 to -5.7c/share in all 12 variants; live
  test lost $32.60 in 25 min. Archived.
- Paired maker replay, 13 of 19 days: every latency setting negative, even instant (join -0.5% at 0 s,
  -4.7% at 0.1 s, fair-value tactic -6.4%); the last 6 days are finishing now (relaunched 2026-10-26).
- gabigol wins as a pure maker: both sides, 20-share clips, 97% paired, fast. Speed we now have
  (~70-130 ms); the missing parts are which prices and which moments, which is exactly what 2a-2e test.

## Order of work
replay finishes (today) -> step-3 verdict file -> book reader (1) -> measurements (2) -> decision.
