# MOON DEV 6+ — KNOWLEDGE BASE

Living file. Every Moon Dev transcript we process gets distilled here: the method, how to
actually do it, and an honest note on what was substance versus sales.

**Sources processed so far**
| date | source | file | status | signal density |
|---|---|---|---|---|
| 2026-09 | Zoom call, 65 min | `Moondev call 06-09-2026 transcript.txt` | **fully processed** | ~10% method, ~90% sales pitch |
| — | same call, source audio | `moondev live call 07-09-2026.MP3` | nothing further to extract | — |

Note: the transcript and the MP3 are the **same single call** — the MP3 is just the source
audio the user transcribed. The filenames carry different dates (06-09 vs 07-09); the
content is one 65-minute session ending with the API keys pasted into chat. There is no
second, unprocessed call.

**No API key or credential appears in this file.** The key lives in
`Moondev api 06-09-2026.txt`, which is gitignored.

---

# PART 1 — THE RBI FRAMEWORK (the core method)

> *"Every single winning bot I've ever built followed these three same letters. It's boring
> on purpose. Boring makes money, exciting loses money."*

## R — Research: find an EDGE, not an indicator

His definition is the useful part:

> *"An edge is the reason the market pays you over and over and over again."*

Not a signal, not an indicator, not a Discord tip. A **reason**. If you cannot say *why*
the money is there and *who* is losing it, you do not have an edge.

What he calls fake research: *"Most people copy a YouTube strategy, watch two videos, and
then risk real money. That's not research, that's hope."*

**How to do it**
1. Write the causal story first, in one sentence: who pays, and why they keep paying.
2. Use AI to read papers and expand the idea — he explicitly uses it to digest PhD papers.
3. Build a LIST of candidate ideas before touching code or money.

## B — Backtest: kill bad ideas cheaply

> *"Just because it worked in the past doesn't guarantee it works in the future, but if it
> never worked in the past, it's not going to start working now."*

That asymmetry is the whole point: a backtest is a **filter, not a proof**.

His flight-simulator framing: *"Pilots don't get their first hours in a 737 with 200
passengers. Backtesting is a flight simulator — you're allowed to crash, the money is
fake, the lessons are real."*

**How to do it**
- Describe the idea in plain English, let AI write the backtest.
- *"You run 25 versions in an afternoon."* Volume of hypotheses is the advantage, not
  perfection of one.

## I — Incubate: SMALL REAL MONEY, explicitly NOT paper

This is the highest-value claim in the entire transcript, and it is a direct challenge to
how we currently run experiments:

> *"Small real money, but not a demo account, NOT PAPER. This is real money, like $10 size.
> Run the bot for 2 weeks, 3 weeks, 4 weeks. Does it match up to the backtest?"*
>
> *"You're going to see hidden costs, bad fills, slow trades, weird stuff at 3 a.m. This
> stuff you don't see on past data, you see it live. You don't see it on paper trading."*

**How to do it**
1. Backtest passes → deploy at ~$10 per trade with REAL money.
2. Run 2–4 weeks.
3. Compare live results against the backtest. Only scale if they match.

**See Part 4 — we independently proved him right on this one, the expensive way.**

---

# PART 2 — PRINCIPLES WORTH KEEPING

### P1 — Alpha decay: a bought bot is worthless
> *"I get people asking every day, 'can I just run one of your algorithms?' I say Google
> alpha decay. Nobody's going to give you a plug-and-play bot… if everybody runs the same
> bot, it's going to go to zero."*

Directly relevant to the gabagool22 copy-trade work: **copying a strategy is not the same
as owning the edge.** Any published edge decays as it is adopted, so the transferable
asset is the METHOD, not the parameter set.

### P2 — A stack of uncorrelated bots beats one holy grail
> *"What if you have three bots with three different edges? One bad week for one bot, the
> other two carry the curve."*

The operative word is **different**. Three bots on correlated edges are one bot with extra
steps. Our EXP-A/B/C/D structure is already this shape — and B and C were correctly killed
when they failed, which is the framework working.

### P3 — "Always in a trade" means you cannot win
> *"The market does not pay you for being there. It pays you for being correct. Being right
> means waiting, but humans hate waiting. A bot waits for free."*
>
> *"A bot will sit there for 6 days and wait for one good setup. I couldn't wait 6 minutes."*

Selectivity IS the edge. A bot that trades constantly has usually had its filters loosened
to keep it busy — which is a failure mode we should watch for in our own gates.

### P4 — Regime change is the edge, not the enemy
Citing Jim Simons: *"You have to keep making your systems better because that's what
everybody else is going to do."*

> *"The market changing is actually our edge. That's why I don't teach one strategy, I
> teach a framework."*

Relevant to us: the BTC 5-min strategy died on 2026-08-07 when Polymarket moved to 60s
TWAP. Under this view that is normal, not a catastrophe — the response is a new edge, not
a repair of the old one.

### P5 — Discipline is not a strategy
> *"Willpower is a phone battery. Telling a human 'stop being human' isn't a plan, it's a
> setup to fail. Hand the trigger to a bot."*

The practical version: **encode the rule in code, not in intention.** Every gate that
exists only as a resolution will eventually be overridden.

### P6 — Leverage framing
> *"100x leverage isn't a gift, it's a trap. They make money when you lose."*

Not directly applicable to Polymarket (no leverage), but the general point — the venue's
incentives are not aligned with yours — applies to fee schedules and rebate structures.

---

# PART 3 — WHAT IS *NOT* IN THIS TRANSCRIPT (read before mining it again)

Honest accounting, so nobody re-reads 66KB hoping for more:

- **No strategies.** Not one entry rule, exit rule, parameter, or indicator.
- **No code, no formulas, no backtest results.**
- **No API usage examples**, despite the key being handed out at the end.
- **No Polymarket specifics** beyond "there's a course for it."
- Roughly **90% of the 65 minutes is a sales pitch** for a $1,200 Labor Day offer
  (testimonials, pricing, urgency, payment options).

The genuine content is the RBI framework plus the six principles above. **That is the whole
harvest — this source is exhausted.** The MP3 is the same call's source audio, so there is
nothing further to extract from it. Do not re-mine this transcript; add new calls instead.

---

# PART 4 — ⭐ WHERE HE IS RIGHT, PROVEN BY OUR OWN DATA

His "incubate with real money, NOT paper" claim is the one testable assertion in the
transcript, and **we proved it independently on 2026-09-06 before ever reading it.**

Our paper harness produced a headline of **+5.71pp margin, "SIGNIFICANT"**. It was false:

```
place_order():
    if DRY_RUN:
        ... fetch book ...
        result.update({"filled": True, ...})
        return result          # <-- returns BEFORE the Phase-1 EV gates
```

The live price gates (`MIN_LIVE_ASK 0.60`, `MAX_LIVE_ASK 0.80`) sit AFTER that return, so
in paper mode **every signal was recorded as a fill at any price**. Measured consequence:

```
72.4% of paper "fills" were above the live ceiling
20.9% were below the live floor
only 6.8% (n=27) were trades the live bot would actually have taken
real live-band result: +0.73pp, 95% CI [-15.80, +17.26]  -> NO EVIDENCE
```

**A paper harness that skips the live gates is not a simulation of the bot.** That is
exactly his "you don't see it on paper trading" point, and it cost us a headline we
believed for a week. Full detail: `Ed-Thorp-entire CONTEXT.MD` Part 19, ledger E28.

**Caveat, in fairness:** his prescription is $10 of real money, which surfaces fills,
slippage and 3 a.m. weirdness — but real money does NOT fix a statistics problem. Our live
band needs ~3–4 more weeks for n to be meaningful whether the money is real or not. Real
money fixes **execution realism**; only time fixes **sample size**. He conflates the two.

---

# PART 5 — THE MOON DEV API: VERIFIED ACCESS MAP

Tested 2026-09-07 with the `moongroup_*` key. Base `https://api.moondev.com`,
auth `X-API-Key:` header (also `?api_key=` or `Authorization: Bearer`).

## 🔴 CORRECTION — my first access map was WRONG, and the cause was my own bug

An earlier version of this file reported a "clean tier boundary: static JSON dumps yes,
live computed endpoints no" and claimed the **Polymarket endpoints were locked out**.
**That was false.** Every endpoint tested works.

The cause: the source txt had run two lines together —

```
API Key: moongroup_<16hex>api      <- the "api" of "api docs & ai" stuck on
```

so a loose extraction pattern produced a **29-character key** instead of the real 26. What
made this so misleading is how the API responds to a *slightly* wrong key:

```
                        CORRECT   WRONG(+api)   JUNK    NO KEY
/api/positions.json       200        200         401     401
/api/prices               200        401         429     429
/api/poly/whales          200        401         429     429
```

**The static JSON dumps accept the malformed key; the live endpoints reject it.** That
produces a perfect, entirely fake "tier boundary". Two lessons worth keeping:

1. **Validate the SHAPE of a credential before trusting any result derived from it.**
   `harvest.py` now refuses to start unless the key matches `moon<word>_ + 16 hex`.
2. **A partially-working key is more dangerous than a dead one** — total failure is
   obvious, selective failure looks like a documented product tier.

## Key status: VALID, FULL ACCESS, NOT the 24h-rotating class

Docs state `moonstream_*` keys rotate every ~24h. Ours is **`moongroup_*`**, a different
class — consistent with his on-call remark: *"this API key should work for like a week or
so."* No `_qe` suffix, so **not** Quant Elite.

## ✅ VERIFIED WORKING (2026-09-07, correct key) — everything tested

All 18 endpoints below return 200 with fresh data and are now being harvested:

| group | endpoints | rows observed |
|---|---|---|
| **Polymarket** | `/api/poly/profitable-traders` | 25 traders (standard cap; `_qe` unlocks all) |
| | `/api/poly/whales`, `/whales/top-traders`, `/whales/top-markets`, `/whales/daily` | 250 / 50 / 50 / 31 |
| **Liquidations** | `/api/all_liquidations/{1h,totals,stats}.json` | 2,644 / 6 windows / 10 |
| | `/api/liquidations/1h.json`, `/api/binance_liquidations/1h.json` | 71 / 1,520 |
| | `/api/hip3_liquidations/1h.json` | 9 |
| **Order flow** | `/api/orderflow.json`, `/api/imbalance/1h.json` | 130 / 125 coins |
| | `/api/large_trades.json`, `/api/trades.json` | 200 / 500 |
| **Positioning** | `/api/positions.json`, `/api/positions/majors.json` | 50 / 18 |
| **Prices** | `/api/prices` | 233 coins + funding + OI |

`/api/hlp/*` and `/api/hip3/*` also return 200 with the correct key; they are simply not
in the harvest set yet.

### The profitable-traders payload (most relevant to this project)

```
keys: total, full_list, updated_at, stats, traders
row : wallet, polymarket_link, pnl_7d, volume_7d, trades_7d, redeems_7d,
      discovered_at, source
top : pnl_7d 6,596,051 on volume 3,646,588 over 496 trades
```

Same shape as the gabagool22 study inputs — a ready-made candidate list.

## 🔴 STALE DATA — returns HTTP 200 but is months old

| endpoint | last updated | age |
|---|---|---|
| `/api/whales.json` | 2026-02-05 | **~7 months** |
| `/api/smart_money/*` | 2026-02-11 | **~7 months** |

Deliberately **excluded from the harvest set**. A 200 is not freshness: always read
`updated_at` / `generated_at` and reject stale data before building on it. Same failure
mode as ledger E30 (Pyth silently returning nothing).

## What this means for strategy testing

- **Polymarket profitable-traders and whales ARE available** — the wallet-discovery work
  that took manual effort in the gabagool22 study is now a single API call.
- **Liquidation cascades** are testable: multi-exchange, fresh, ~2,600 rows per 1h window.
- **Order flow / imbalance** across 125–130 coins, updating every ~90s.
- **Do not touch** `whales.json` or `smart_money/*` until their freshness is fixed.

---

# PART 5b — THE HARVESTER (running on the VPS)

The key lasts about a week, so everything reachable is being pulled to disk **now**.

```
~/Moondev API/
  harvest.py            18 endpoints, cadence matched to each feed's refresh
  monitor_moondev.py    7 checks, Telegram only on findings
  data/<slug>/<date>/<epoch>.json.gz     snapshots, written ONLY on content change
  data/manifest_<date>.csv               one row per poll, changed or not
  .env                                   the key, chmod 600
```

`moondev-harvest.service` runs continuously (`Restart=always`);
`moondev-monitor.timer` checks every 15 minutes. Both enabled, so they survive a reboot.

**Measured:** 56 polls, **0 failures**, 18/18 endpoints, ~840 KB in the first 20 minutes →
roughly **59 MB/day, ~0.4 GB for the full week**. Content-hash dedupe means unchanged
feeds cost nothing but a manifest row, while the manifest still distinguishes "checked and
unchanged" from "never checked".

## Monitor checks — all proven to fire (10/10)

| check | fires when |
|---|---|
| **M1** | an endpoint stops being polled, or never appears at all |
| **M2** | repeated HTTP errors or request failures |
| **M3** | **EMPTY ROWS** — 200 OK but zero rows |
| **M4** | the feed's own `updated_at` stops advancing |
| **M5** | **DATA MISMATCH** — row count collapses below half its recent median |
| **M6** | content byte-identical for far longer than its cadence (frozen feed) |
| **M7** | **401s appear — the key has expired** |

Findings must persist 30 minutes before alerting (fingerprinted per endpoint), so a
transient blip cannot page anyone. Telegram delivery was proven end-to-end with an
injected empty-rows fault, not assumed.

**When the key expires, M7 is the alarm that will tell you.**

## 10-MINUTE LIQUIDATION WINDOWS — added 2026-09-07, and WHY

A BTC 5-min market lives for 300 seconds. A 1-hour liquidation aggregate smears a cascade
across 12 rounds and destroys the signal, so three 10-minute windows were added at **60s**
cadence. **These cannot be backfilled once the key expires — collect now or never.**

```
all_liq_10m       /api/all_liquidations/10m.json       270-278 rows
binance_liq_10m   /api/binance_liquidations/10m.json   180-194 rows
hl_liq_10m        /api/liquidations/10m.json            48-60  rows
```

### ⭐ Measured freshness — this decides what is usable intra-round

```
all_liq_10m      lag  min 0s  | MEDIAN 1s   | max 3s     <- effectively real-time
binance_liq_10m  lag  min 39s | MEDIAN 163s | max 225s   <- HALF A ROUND behind
hl_liq_10m       carries no upstream stamp - lag unmeasurable
```

**The multi-exchange aggregate is the one to build on.** At a 1-second median lag it can
inform a decision inside a live 5-minute round. The Binance-specific feed is a median
**163 seconds** stale — more than half a round — so it is fine for post-hoc analysis but
must NOT be used as an intra-round signal. Treating them as interchangeable would put a
2.7-minute-old number into a 5-minute decision.

`hl_liq_10m` exposes no upstream timestamp, so its freshness is unverifiable; treat it as
unproven until measured another way.

Harvest total with all 21 endpoints: **~30 MB/day, ~180 MB for the remaining ~6 days.**

---

## 🔴 M5 tuning correction — measure natural variance BEFORE setting a threshold

M5 originally fired on ANY feed whose row count dropped below half its median. Within
hours it paged twice, and **both were false alarms**:

```
all_liq_1h      1145 rows vs median 2815   "-59% collapse"
binance_liq_1h   766 rows vs median 1613   "-53% collapse"
```

A liquidation 1h feed is a **rolling window of market events** — its row count IS market
volatility. Measured over the first hours of harvesting:

```
all_liq_1h       1055 -> 4167   natural swing 75%
binance_liq_1h    715 -> 3519   natural swing 80%
hl_liq_1h          57 ->  100
hip3_liq_1h         4 ->    9
--- versus structural feeds ---
prices            233 ->  233   0%
positions          50 ->   50   0%
poly_whales       250 ->  250   0%
imbalance_1h      125 ->  130   4%
trades            500 ->  500   0%   (fixed-cap "recent N")
large_trades      200 ->  200   0%   (fixed-cap)
```

I set a 50% threshold without first measuring each feed's natural range. M5 is now
restricted to the **14 structural feeds**; the 4 genuinely event-driven liquidation windows
report their range as a note instead. Emptiness (M3) still covers them — zero liquidations
across many polls would still be suspicious.

**The lesson, and it is the same one as E32/E33/E35/E36 in the Ed Thorp ledger:**
before alerting on a difference, know what the number naturally does. A threshold set
without measuring the baseline is a false-alarm generator, and FA-4 says a false alarm is
the bug that makes people stop reading the alert.

# PART 6 — HOW TO PROCESS THE NEXT TRANSCRIPT

1. Skim for the ~10% that is method; ignore pricing, testimonials, urgency, and biography.
2. For each claim ask: **is it testable against our data?** Only testable claims earn a
   place in Part 4.
3. Add the source to the table at the top with an honest signal-density rating.
4. Never copy a key, wallet, or credential into this file.
5. If a claim contradicts something we have measured, **write both down** and say which
   evidence is stronger — do not quietly pick a side.
