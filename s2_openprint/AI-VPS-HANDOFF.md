# AI handoff: deploy S2 read-only research collection

## Your task and authority boundary

You are receiving `s2_openprint-research.zip`. Deploy **read-only forward collection**
on the user's authorized VPS, verify actual data flow, and only then run resolved-outcome
replay. Ask for the hostname, SSH user and approved install location if not provided.
Do not guess credentials, print secrets, modify existing trading services, or place orders.
This package has no live order adapter. Do not add one as part of deployment.

Read `README.md`, `engine.py`, `capture.py`, and `docs/MOONDEV-KNOWLEDGE.md` first.
MoonDev RBI means research, backtest, then separately authorized small real-money
incubation. Paper/replay results are not evidence of live fills.

## Exact handoff state

- Implemented: 5m/15m public-feed recorder; conservative candidate gates; Gamma outcome
  join; hypothetical replay; ten unit tests; VPS session wrapper and systemd template.
- Existing tests passed in the build workspace. Rerun on the VPS.
- A 12-second smoke test retrieved Gamma metadata but **no RTDS ticks**. End-to-end
  capture is NOT verified. No collector was left running in the build workspace.
- No historical backtest has been completed. No positive edge established.
- No real orders have been submitted. All candidates are live-ineligible.
- Deployment files are supplied as templates; they have not been exercised on a VPS.
- Smoke-test output, virtualenvs, keys and Git internals are excluded from the ZIP.

## Strategy caveats — do not “fix” by loosening gates

The 5m/15m displacement thresholds are 5/8 bps and the code refuses thresholds >10 bps.
The default model uses assumed 7.5-bps 5m volatility and terminal diffusion: it is NOT a
validated TWAP-resolution model. The open is a **candidate** Chainlink spot observation
available at/before the bell, not a verified official benchmark. Market descriptions
must establish actual opening and settlement semantics separately for both durations.
Do not silently substitute a later tick, exchange price, interpolated candle, or TWAP
value as the opening reference.

The default crypto fee rate 0.07 is from bundled documentation, not a freshly validated
per-market fee. At 60¢, assumed fee is 1.68¢ per share and break-even is 61.68% before
slippage. The engine also adds 0.5¢ slippage and requires it to fit within the 60¢ ceiling.
Maker quotes are only intents, no fills/rebates or maker P&L are modeled.

## 1. Upload and inspect

Example commands on the operator's machine (replace placeholders):

```bash
scp s2_openprint-research.zip SSH_USER@VPS_HOST:~/
ssh SSH_USER@VPS_HOST
unzip -l ~/s2_openprint-research.zip
```

Use a fresh staging directory rather than overwriting a running install:

```bash
mkdir -p ~/s2-staging
unzip ~/s2_openprint-research.zip -d ~/s2-staging
cd ~/s2-staging/s2_openprint
sha256sum -c SHA256SUMS
```

Verify the uploaded ZIP hash against the builder's separate `.sha256` file if available.
Do not overwrite an existing `/opt/s2_openprint` without a backup and user approval.

## 2. Install in isolation (Debian/Ubuntu example)

The service template assumes `/opt/s2_openprint` and a dedicated `s2research` user.
These commands need sudo authorization. Adapt for the actual distribution.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv unzip
id s2research || sudo useradd --system --home /opt/s2_openprint --shell /usr/sbin/nologin s2research
# STOP if this path already exists; do not merge over existing research data.
sudo test ! -e /opt/s2_openprint
sudo cp -a ~/s2-staging/s2_openprint /opt/s2_openprint
sudo mkdir -p /opt/s2_openprint/data
sudo chown -R s2research:s2research /opt/s2_openprint
sudo -u s2research python3 -m venv /opt/s2_openprint/.venv
sudo -u s2research /opt/s2_openprint/.venv/bin/pip install -r /opt/s2_openprint/requirements.txt
cd /opt/s2_openprint
sudo -u s2research .venv/bin/python -m unittest discover -s . -v
sudo -u s2research .venv/bin/python -m compileall -q .
timedatectl status
df -h /opt/s2_openprint
```

Execute sequentially and stop on failures. Verify clock synchronization, DNS and
outbound HTTPS/WSS on port 443. No inbound ports or wallet credentials are needed.
Use Python 3.10+; the original unit tests were run on Python 3.13.

## 3. Bounded foreground validation BEFORE enabling a service

```bash
cd /opt/s2_openprint
sudo -u s2research .venv/bin/python -u capture.py --minutes 20 --out data/preflight
```

This catches at least one 15m boundary regardless of start time. Start more than 60s
before any particular boundary you want to measure. The collector prefetches metadata
in the last minute before the bell and skips rounds without that metadata.

In another terminal inspect file sizes and selected records:

```bash
find /opt/s2_openprint/data/preflight -maxdepth 1 -type f -printf '%f %s bytes\n'
tail -n 5 /opt/s2_openprint/data/preflight/health.jsonl
tail -n 2 /opt/s2_openprint/data/preflight/ticks.jsonl
tail -n 2 /opt/s2_openprint/data/preflight/samples.jsonl
tail -n 5 /opt/s2_openprint/data/preflight/decisions.jsonl
```

Files may not exist until their first record. That is not by itself a program crash.

### Acceptance criteria

1. `ticks.jsonl` contains advancing observations and receipt timestamps for both
   `crypto_prices_chainlink` and `crypto_prices_twap_sixty`; BTC values are plausible.
2. `markets.jsonl` contains correct upcoming BTC 5m and 15m metadata and explicit
   Up/Down outcome mapping. Review and retain resolution descriptions.
3. After eligible boundaries, `samples.jsonl` has timestamped book/spot/reference rows
   for both durations. Check observed/received clocks and the 5–15s decision window.
4. `decisions.jsonl` records candidates OR explicit skips. Zero qualifying trades on a
   quiet tape is expected; zero ticks is a feed problem, not strategy selectivity.
5. No order endpoints, private keys or signing clients appear in the process.

### If RTDS is connected but no ticks arrive

Do NOT say collection is healthy merely because the process stays alive. The current
adapter uses legacy wire topic names inherited from this repo. Inspect public RTDS
subscription acknowledgements/frame schemas in a separate diagnostic script; compare
with current official documentation and bundled `docs/CHAINLINK-TWAP.md`. Topic/schema
changes may require an adapter update. Preserve raw diagnostics without secrets.

The modern SDK topic names are not automatically valid raw WebSocket topic names.
Confirm actual observed frames, timestamp units, symbol filters, source and TWAP window.
Do not count subscription snapshots as fresh ticks or infer a TWAP window from cadence.
Test adapter changes with fixtures before deployment; document exactly what changed.
The package does not include an automated feed-health alert service.

## 4. Enable sustained collection only after preflight passes

```bash
sudo cp /opt/s2_openprint/deploy/s2-collect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now s2-collect.service
sudo systemctl status s2-collect.service --no-pager
sudo journalctl -u s2-collect.service -n 100 --no-pager
```

`collect-session.sh` creates a UTC-named output directory under `data/sessions/` and
runs for 60 minutes; systemd restarts it after ten seconds. This bounds in-memory state
and file size per session. Each restart causes a short warm-up/missing-open gap: measure
and report it. The wrapper refuses a new session below 2 GiB free or when `KILL` exists.
The disk check is session-start only, **not continuous protection**. Arrange host-level
free-space alerts and an approved retention/archive policy before unattended operation.
Never delete data automatically to hide a space problem. Compress completed sessions
only, not files still being appended. Preserve the clocks and gap rates in analysis.

Stop/disable explicitly:

```bash
sudo systemctl disable --now s2-collect.service
```

Emergency collection stop: `sudo touch /opt/s2_openprint/KILL`. It stops collection,
not orders (there are none). For a deliberate resume, remove KILL and restart the service.
A process may take several seconds to exit while a network request times out.

## 5. Join outcomes and replay — not a historical-data downloader

Wait until rounds have resolved. Analyze **completed** sessions, not the active file.
For a selected session, replace SESSION with its actual directory name:

```bash
cd /opt/s2_openprint
S=data/sessions/SESSION
sudo -u s2research .venv/bin/python resolve.py "$S/samples.jsonl" "$S/resolved.jsonl"
sudo -u s2research .venv/bin/python replay.py "$S/resolved.jsonl" --output "$S/report.json"
```

Only run this when samples exist. `resolve.py` conservatively requires Gamma `closed`,
`umaResolutionStatus=resolved` and exact binary outcome prices; pending or unsupported
schemas remain unknown. Review current official resolution semantics before changing
that gate. Re-run joins later for unresolved rounds. Never infer a winner merely from
`acceptingOrders=false` or a 0.99 traded price.

For cumulative research, concatenate selected **completed** session sample files into a
new analysis file and resolve/replay it once. Avoid including both raw and resolved copies
or overlapping preflight sessions. Replay deduplicates candidates by duration/start, but
it is not a complete dataset-integrity audit. Do not sum independent report confidence
intervals. Review duplicate, missing and pending-round counts separately.

Outputs are hypothetical taker results at an assumed cost, not executable fill evidence.
The Wilson win-rate interval is descriptive: overlapping 5m/15m markets are correlated.
A rigorous promotion review still needs chronological holdout, day/time clustering,
volatility calibration, missingness analysis and correct settlement semantics.
Never interpolate 1m candle closes to “recover” a ten-second entry price.

## 6. Required report back to the user

Return: VPS/install path; deployed file hashes; Python/dependency versions; test result;
service state; actual UTC collection start; tick counts and newest source/receipt times
per feed; samples/decisions by duration; skip reasons; missing-round/data-gap counts;
resolved vs pending outcomes; disk usage and daily growth estimate; and any adapter fixes.
Explicitly distinguish:

- COLLECTING HEALTHY (timestamps and samples verified),
- RUNNING BUT DATA BLOCKED (no valid stream/samples),
- REPLAY READY (resolved observations exist),
- RESEARCH INCONCLUSIVE (no validated edge).

Do not claim “backtest passed” from a few positive candidates. Do not move to real-money
incubation without separate authorization, settlement verification, live execution/risk
engineering, and cost-adjusted out-of-sample evidence. No other bot should be restarted
or reconfigured by this deployment.
