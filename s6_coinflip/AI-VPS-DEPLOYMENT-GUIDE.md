# S6 Coin-Flip Harvester — VPS handoff guide for another AI

## Mission

Deploy the S6 experiment to the Polymarket VPS in a safe, observable way. The immediate goal
is **data collection and plumbing validation**, not profit claims and not unrestricted live
trading.

S6 quotes both BTC Up/Down tokens around the round open at 0.49 and measures whether the
first-leg fills are conditionally informed. The primary hypothesis is killed if, after 300
fills, either side's conditional resolution rate differs from 50% by more than 8 percentage
points. Ties resolve Up (`>=`).

## Package contents

- `s6_harvester.py` — runner; DRY by default.
- `fill_asymmetry.py` — reads `s6_stats.json` and reports conditional Up/Down outcomes.
- `README.md` — strategy and RBI status.
- `S6-PLAYBOOK.md` — operating rules and kill criteria.
- `PAIRING-S3.md` — coordination with the existing S3 maker.
- `deploy/s6-harvester.service` — systemd template; adapt paths/user to the VPS.

## Non-negotiable safety rules

1. Never copy credentials into this package, the repository, logs, Telegram, or chat.
2. Create `.env` only on the VPS, chmod 600. Start with `LIVE_TRADING=0`.
3. Do not set `LIVE_TRADING=1` merely because the preflight succeeds. Preflight is plumbing.
4. Keep the S6 KILL file available: `touch s6_coinflip/KILL` must immediately keep the bot flat.
5. Confirm that Moon Dev authentication is healthy. A missing, expired, empty, or stale feed
   must leave S6 flat; never bypass that gate.
6. Do not use Binance liquidation data for the intra-round trigger. The code is designed for
   Moon Dev `all_liquidations` only.
7. S6 and S3 share BTC exposure. Keep the combined net directional exposure under $30 per
   5-minute bucket. The first 45 seconds overlap with S3's start, so do not blindly double
   the size.
8. Do not claim an edge from DRY fills. Public trade prints do not prove queue position.

## VPS deployment sequence

Adapt the paths and service user to the actual VPS. Do not assume the local workstation
path exists on the server.

```bash
# On the VPS, choose a private application directory.
mkdir -p ~/btc-5min-twap-lockin/s6_coinflip
chmod 700 ~/btc-5min-twap-lockin/s6_coinflip

# Copy/extract this package there, preserving the s6_coinflip directory.
# Then verify the source and compile it.
cd ~/btc-5min-twap-lockin
python3 -m py_compile s6_coinflip/s6_harvester.py s6_coinflip/fill_asymmetry.py
```

Create `s6_coinflip/.env` locally on the VPS only:

```dotenv
# Required for feed collection. Never commit this file.
MOONDEV_API_KEY=REPLACE_ON_VPS

# Keep this disabled during the entire collection/backtest phase.
LIVE_TRADING=0

# Do not add POLY_PRIVATE_KEY or POLY_FUNDER until a human explicitly approves
# the real-money incubation step after the B step.
```

Then:

```bash
chmod 600 s6_coinflip/.env
python3 s6_coinflip/s6_harvester.py --preflight
python3 s6_coinflip/s6_harvester.py --minutes 60
```

The bounded run should be used first. Inspect logs, errors, round discovery, feed freshness,
quote-window behavior, and whether fills are being rejected by the live-band gate as expected.
Only then install the service.

## Systemd installation

Edit `deploy/s6-harvester.service` and replace `%i` or home-relative paths with the real VPS
user and absolute paths. The service should run as an unprivileged user, not root.

```bash
sudo cp s6_coinflip/deploy/s6-harvester.service /etc/systemd/system/s6-harvester.service
sudo systemctl daemon-reload
sudo systemctl enable --now s6-harvester.service
sudo systemctl status s6-harvester.service --no-pager
journalctl -u s6-harvester.service -f
```

If the service is not writing useful data, stop it instead of weakening gates:

```bash
sudo systemctl stop s6-harvester.service
touch ~/btc-5min-twap-lockin/s6_coinflip/KILL
```

## Collection review checklist

Review at least three quiet-session days before deciding whether the plumbing is healthy:

- Moon Dev status: no recurring 401, 403, 429, timeout, or empty-feed periods.
- Round discovery: both 5m and 15m markets resolve to valid CLOB token IDs.
- Quote timing: quotes are attempted only during the first 45 seconds.
- Gate integrity: no fill is recorded outside the 0.45–0.55 live band.
- No unexpected live order lines; with `LIVE_TRADING=0`, there must be no real order activity.
- Error rate and reconnects are bounded and explainable.
- `s6_stats.json`, `s6_fills.csv`, and `s6_pulls.csv` are being retained and backed up.

Run the asymmetry report:

```bash
python3 s6_coinflip/fill_asymmetry.py s6_coinflip/s6_stats.json
```

The report must say the sample is below the 300-fill threshold until enough observations
exist. Do not tune parameters based on a small sample.

## Backtesting status and next task

This package contains the gated collection runner, but it does **not** yet contain a
validated historical S6 backtest. Another AI should build the B step before any real-money
incubation:

1. Harvest historical BTC 5m/15m market trades, order books or price history, round starts,
   token IDs, and Gamma resolution outcomes.
2. Reconstruct what a 0.49 resting bid would have seen during the first 45 seconds.
3. Apply the live-band gate **before** recording simulated fills.
4. Include the optimistic queue assumption as a clearly labeled upper bound; do not call it
   execution-realistic.
5. Measure first-leg fill rate, two-leg completion rate, outcome conditional on each filled
   side, combined-cost opportunities below 0.97, and sensitivity across multiple days.
6. Split the sample by session and report confidence intervals. Do not select one lucky
   parameter set from a single day and call it validated.

If historical data cannot support credible queue reconstruction, say so and keep the system
in DRY collection mode. That is a valid RBI result.

## Live incubation gate — human approval required

Do not enable live orders unless all of the following are true:

- Historical B-step results are documented and reproducible.
- At least 300 live fills or a separately approved sample supports the asymmetry conclusion.
- The 8pp kill test is passed, with confidence intervals reported.
- S3 + S6 combined exposure and wallet funding have been reviewed.
- A human has explicitly approved the exact size, dates, and `LIVE_TRADING=1` change.
- A rollback command and KILL-file procedure have been tested.

The Moon Dev RBI lesson is: research first, backtest as a filter, then incubate with small
real money only after the execution plumbing and evidence justify it. Do not convert this
handoff into a promise of profitability.
