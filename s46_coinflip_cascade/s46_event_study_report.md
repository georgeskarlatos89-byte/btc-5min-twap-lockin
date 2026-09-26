# S4 Event Study — Real Harvest Data

Source files: 25507, cascade events: 154 (raw window hits before cross-snapshot dedupe: 1429)
Generated: 2026-09-26T10:17:10.916047+00:00

## Counts by side
- BUY: 83
- SELL: 71

## Counts by size
- >=$1.5M: 154
- >=$2.5M: 25
- >=$4.0M: 13
- >=$8.0M: 3

## Events per day
- 2026-09-08: 6
- 2026-09-09: 6
- 2026-09-10: 9
- 2026-09-11: 14
- 2026-09-13: 1
- 2026-09-14: 7
- 2026-09-15: 15
- 2026-09-16: 3
- 2026-09-17: 2
- 2026-09-18: 24
- 2026-09-19: 3
- 2026-09-20: 5
- 2026-09-21: 27
- 2026-09-22: 4
- 2026-09-23: 17
- 2026-09-24: 6
- 2026-09-25: 5

## Events by UTC hour (S6 quiet hours are 00-12 and 16-18; S4 is armed all day)
00h:5 01h:4 02h:3 03h:4 04h:3 05h:4 06h:2 07h:1 08h:12 09h:10 10h:0 11h:3 12h:17 13h:21 14h:23 15h:6 16h:5 17h:3 18h:13 19h:4 20h:5 21h:1 22h:1 23h:4

## Largest 10 events
- 2026-09-21 08:38:55Z BUY $35.23M (538 prints)
- 2026-09-16 18:08:18Z BUY $20.23M (176 prints)
- 2026-09-10 23:14:58Z SELL $10.44M (55 prints)
- 2026-09-21 15:07:59Z BUY $7.31M (96 prints)
- 2026-09-20 02:44:17Z SELL $5.72M (46 prints)
- 2026-09-09 22:08:58Z SELL $5.58M (73 prints)
- 2026-09-21 08:38:11Z BUY $5.53M (81 prints)
- 2026-09-14 20:23:33Z BUY $5.44M (78 prints)
- 2026-09-16 18:08:06Z BUY $4.61M (2 prints)
- 2026-09-13 03:27:56Z SELL $4.25M (9 prints)

## What this report does NOT show
- Continuation after the cascade (spot at +60 s / +180 s): needs a spot-history join, not done.
- Whether the 15m round resolved in the cascade direction: deliberately NOT joined; the round resolves vs its OPEN, not vs the price at cascade time, so it would overstate S4 edge.
- Book repricing speed at cascade time: needs the BME order-book capture join, not done.

## Next steps for P_model
- Join each cascade ts with Kraken 1s spot: measure spot 60s/180s later
- Join with Polymarket Gamma 15m resolutions: does cascade side win?
- Join with CLOB prices-history: how fast does book reprice after cascade?
- Compute P_model = P(continuation | size, spot_confirm, vol_regime)
- Use P_model in s46_harvester.py S4 entry: ask <= P_model - fee - 0.02

### Current conservative prior (from strategy doc)
- 1.5M->0.60, 2.5M->0.65, 4M->0.70, 8M->0.75 + 0.03 if spot >=6bps, +0.02 if >=10bps, cap 0.80
