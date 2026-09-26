# S5 deployment handoff for the next AI

## Mission and non-negotiable boundaries

Deploy **S5 Wick Fade / Tape-Lag Reversion research collection** on a user-approved
VPS, verify both public feeds, then collect synchronized data for 7–14 days.
This package is **not a live trading bot or completed profitability backtest**.
It has no order submission, wallet integration or validated fair-value model.
Do not invent one to unblock entries. No credentials are needed for collection.

Read this file and `README.md` before executing commands. Inspect the actual code
and host state, not just this handoff. Commands below are templates: substitute
approved paths and SSH alias only after confirmation.

### Current state at packaging

- Code and tests built in the Arena workspace; 17 unit tests passed.
- One finite 15-second public-feed smoke test completed; both feeds returned data.
- Smoke log is included under `data/smoke/`; outputs under `reports/smoke/`.
- **No VPS was accessed or deployment performed. No collector was left running.**
- No validated strategy backtest exists. Replay is receive-order event research,
  gate checks and bid markouts, **not fills or realized PnL**.
- No source changes have been pushed to GitHub as part of this package.

## 1. Confirm the target — never guess

Repository context mentions two machines, but that record can be stale:

| SSH alias | Previously documented role |
|---|---|
| `polyvps` | Existing real-money fleet; do not touch without explicit authorization |
| `twapvm` | Previously documented TWAP/S1 research host; confirm present role/capacity |

Ask the user: **Which VPS, SSH user, installation directory and data-volume budget
are approved for S5?** Do not infer authorization to change any existing bot.
Use the user's configured SSH authentication; never request keys pasted into chat.
Do not bypass geographic restrictions. Confirm venue eligibility and local rules
before any eventual trading phase, which is outside this task.

Read-only checks on the approved host:

```bash
hostname
whoami
python3 --version                   # needs Python 3.11+
df -h "$HOME"
free -h
timedatectl status                  # inspect NTP/clock sync, if available
systemctl --user list-units --type=service --all | grep -Ei 's5|wick' || true
pgrep -af 'collect.py|s5_wickfade' || true
```

If another S5 collector exists, inspect its paths, service and most recent data.
**Do not start a duplicate writer.** Report whether it is running and healthy;
ask before replacement or restarting it. Do not kill a generic `python` process.

## 2. Transfer, verify and install without overwriting

From the operator's machine, replace `APPROVED_ALIAS` with the confirmed alias:

```bash
scp s5-wickfade-research.zip APPROVED_ALIAS:~/
ssh APPROVED_ALIAS
unzip -l ~/s5-wickfade-research.zip
# STOP if this directory already exists. Back up/version it with user approval.
test ! -e ~/s5_wickfade && unzip ~/s5-wickfade-research.zip -d ~/
cd ~/s5_wickfade
sha256sum -c SHA256SUMS
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

If unzip, venv or Python 3.11+ is missing, request permission to install the minimal
packages. Never upgrade the whole machine or alter dependencies of another bot.
The included service is a **user service** and will need its paths updated for
this standalone ZIP installation (see below).

## 3. Finite preflight on the VPS

```bash
cd ~/s5_wickfade
.venv/bin/python collect.py --minutes 1 --output data/preflight
.venv/bin/python replay.py data/preflight/*.jsonl --output reports/preflight
```

Inspect logs, not only the exit code. A collector can retry network errors until
its timer ends and exit cleanly without a healthy feed.

```bash
.venv/bin/python - <<'PY'
import collections, glob, json
rows = [json.loads(line) for path in sorted(glob.glob('data/preflight/*.jsonl'))
        for line in open(path)]
print('Sources:', dict(collections.Counter(r['source'] for r in rows)))
print('Status records:')
for r in rows:
    if r['source'] == 'status': print(r['payload'])
trades = sum(len(r['payload'].get('data', [])) for r in rows
             if r['source'] == 'kraken'
             and r['payload'].get('channel') == 'trade'
             and r['payload'].get('type') == 'update')
print('Actual live Kraken trade rows:', trades)
print('Market metadata records:', sum(r['source'] == 'market' for r in rows))
print('Polymarket websocket payloads:', sum(r['source'] == 'polymarket' for r in rows))
PY
```

Require actual Kraken trades, exact-round market metadata, book snapshots and book
updates, reasonable source/receive clock differences and no repeating errors.
Kraken status/heartbeat messages alone are not a successful trade-feed test. Check
Polymarket token/outcome mapping and metadata. Re-run across a five-minute boundary
to verify round rollover before leaving it unattended. No wick in a short preflight
is normal. Missing fairs blocking entries is also **expected**, not a bug to bypass.

## 4. Set storage controls BEFORE long collection

Raw book traffic can be substantial. The included local smoke log is approximately
2.8 MB for 15 seconds including other files; that is a sizing warning, **not a
reliable daily-volume forecast**. Measure actual VPS data growth for 10–30 minutes,
project disk usage for 14 days, and reserve ample free space.

The code creates daily JSONL filenames but has **no built-in disk quota, retention
or disk-full alarm**. It cannot responsibly be left unattended until the operator
has arranged disk/free-space alerts and an archive/retention procedure. Use an
approved dedicated data directory/volume if needed and set `--output` accordingly.
Never let research data fill the disk hosting real-money bots. Preserve raw data
needed for evaluation; don't silently delete it to keep the collector alive.

Only compress/archive a day once no process is writing it. Replay accepts plain
JSONL, so decompress archives into an analysis directory before replay. Do not run
logrotate copy-truncate against an actively written event file.

## 5. Install a recording-only user service

Only after target approval, tests, feed checks and storage arrangements:

```bash
cd ~/s5_wickfade
mkdir -p ~/.config/systemd/user
# Inspect any existing service before overwriting it.
test ! -e ~/.config/systemd/user/s5-collector.service || {
  echo 'Existing service: STOP and inspect before continuing'; exit 1;
}
sed 's|%h/btc-5min-twap-lockin/s5_wickfade|%h/s5_wickfade|g' \
  deploy/s5-collector.service > ~/.config/systemd/user/s5-collector.service
cat ~/.config/systemd/user/s5-collector.service
systemctl --user daemon-reload
systemctl --user enable --now s5-collector.service
systemctl --user status s5-collector.service --no-pager
journalctl --user -u s5-collector.service -n 50 --no-pager
```

The service runs finite 24-hour collector sessions and restarts them automatically;
small restart gaps are possible and must be included in coverage analysis. It does
not stop automatically after two weeks. Schedule an approved review/stop date.
For running after logout/reboot, user lingering may be required. Check
`loginctl show-user "$USER" -p Linger`; request permission before an administrator
runs `loginctl enable-linger USERNAME`. If a user systemd bus is unavailable, adapt
with the administrator instead of assuming `sudo systemctl` is interchangeable.

For a non-default install or volume, edit **WorkingDirectory**, **ExecStart** and
`--output` explicitly. Use absolute paths and the isolated venv. No API keys, wallet
files or existing bot environment variables should be copied into this service.

## 6. Confirm that it is really collecting

A green service is not enough. Validate at deployment and periodically afterward:

```bash
systemctl --user is-active s5-collector.service
journalctl --user -u s5-collector.service -n 50 --no-pager
ls -lh ~/s5_wickfade/data/*.jsonl
tail -n 3 ~/s5_wickfade/data/$(date -u +%F).jsonl
du -sh ~/s5_wickfade/data
df -h ~/s5_wickfade/data
```

Routine activity is written to JSONL, not printed to the journal. Inspect recent
`recv_ts`, source event timestamps, current-round metadata, both feed populations
and status/error rows. Check file growth again later. Check round rollover and
clock health. Do not label a retrying/offline process as a healthy collector.

Stop only S5 using:

```bash
systemctl --user stop s5-collector.service
# To prevent automatic startup on subsequent user sessions/reboot:
systemctl --user disable s5-collector.service
```

`touch ~/s5_wickfade/KILL` blocks replay candidate eligibility. It does **not** stop
collection and is not a replacement for stopping the service to protect disk space.

## 7. Replay collected data — NOT a completed backtest

Prefer closed daily files or a consistent copied prefix. Do not parse a file while
its final line is being written. One safe option is briefly stopping S5, taking a
copy and restarting (with user permission), recording the resulting coverage gap.
Do not mix smoke/preflight sessions with production logs.

```bash
cd ~/s5_wickfade
# Replace with an actual CLOSED production date:
.venv/bin/python replay.py \
  data/YYYY-MM-DD.jsonl \
  --output reports/review-01
```

For multiple days, list distinct real filenames in chronological order.
Do not repeat a file. Replays reject backward receive time. Clock steps should be
investigated, not hidden by sorting events on source timestamp. Replay has
`--fee-per-share` and `--latency` research knobs; defaults are assumptions, not
measured venue costs or latency.

Outputs:
- `summary.json`: counts, assumptions and fair-provider status.
- `events.jsonl`: wick detections, delayed gate decisions and observed bid markouts.
- `MISSING_ENTRIES_BLOCKED` is expected without independently validated fair events.
- There is no actual entry/exit simulator, position ledger, exchange reconciliation,
  maker queue model or computed trading PnL in this package.

Summarize research honestly: coverage/gaps, wick counts by direction/time, quote
responses, missing/censored horizons, source-to-receive delay and sensitivity to
fees/slippage/latency. The collector logs raw records but does not automatically
produce a comprehensive coverage/latency audit; build that analysis explicitly.
Do not cherry-pick only recovered wicks or only nonmissing markouts.

## 8. Remaining work before small-real-money incubation

Follow the README promotion plan. Main blockers:
1. Joint high-resolution spot/books **and oracle/reference** data quality. This
   package does not yet collect Chainlink/settlement-oracle data.
2. Exact market rules and a walk-forward validated early-round fair model.
3. Token-specific fees, measured latency and conservative execution backtesting.
4. Out-of-sample evidence after costs and adequate observations.
5. Execution adapter with persistent risk state, one position, partial-fill and
   restart reconciliation, independent risk-reducing exits and shared-bot controls.
6. Explicit user approval for $10 real-money trades, max three/day. The current
   three/day cap is per independent shadow replay, not persistent live accounting.

The corrected latency kill is based on a quote opportunity ending before an entry
can fill, not spot reversion taking longer than a holding interval. See
`core.latency_verdict` and README for the provisional conservative diagnostic.
Unknown latency or unvalidated fair blocks live promotion.

## Required report back to the user

State these explicitly, with no fabricated success:
- Confirmed host/user, install/data paths and service name.
- Whether **collecting**, **stopped**, **replaying**, or **blocked**; do not conflate.
- Test results, evidence of both live feeds and rollover checks.
- Start time UTC, latest receive time, file sizes and available disk.
- Errors, missing pieces, archive plan and next review/stop date.
- Whether any replay was run, which data interval, and its actual limitations.
- Confirmation that no orders were placed and other bots were unchanged.

If blocked by access, network or dependencies, stop and ask for the minimum missing
input. Never describe this local package as already running on a VPS.
