# arena-ai-btc-5-minute — new strategies #1: TWAP Lock-In Convergence, late-round taker

**Living record.** Every time something is run or changed for this strategy, a new **Part #N**
is appended below with (a) the context — why it was done and what the data said — and (b) the
exact change or run mechanism. Nothing is rewritten; corrections get their own Part.
No private keys, tokens or full wallet addresses appear in this file (addresses are shown
truncated, e.g. `0xF095…A3Ed`). Strategy rules and constants are **never altered** here — only
execution plumbing, deployment, and checks.

| thing | value |
|---|---|
| Box | `twapvm` — GCP `europe-west4-a`, `34.34.13.7`, user `ubuntupolymarket3`, country **NL** (non-US: Polymarket geoblocks US IPs for signed orders) |
| Working copy (the one that runs) | `~/s1_harness/` |
| Reference copy (all files, incl. the original archive) | `~/#1 TWAP Lock-In Convergence late-round taker/` |
| Services | `s1-harness.service` (observer, writes `rounds.csv`) + `s1-trader.service` (gated trader) + `s1-monitor.service` (read-only alerts + data checks, Part #9) + `s9-watcher.service` (S9 wallet watcher + edge-agreement, measure-only, Part #12) — trader DRY |
| S9 stack docs (reference) | `~/#1 …/S1-VPS-STACK/s1-vps-stack/` on the VM: `README-VPS-DEPLOY.md`, `EDGE-AGREEMENT-BUILD-LOG.md`, `docs/` (10 strategies, S9 replay, S9 phase-2, gabagool22 recon) |
| Account for incubation | **account 2**, Polymarket proxy `0xF095…A3Ed`, **$65.07 pUSD** on-chain (2026-09-20) |
| Alerts | Telegram group `-1002282822022`, thread `600` ("BTC-5-minute") — **wired 2026-09-21** via `s1_monitor.py`; token in `telegram.env` (chmod 600, never in docs) |
| Local mirror of this folder | `Copy trade btc-5 minute gabagool22 Cinese programmer/arena-ai-btc-5-minute new strategies #1 TWAP Lock-In Convergence late-round taker/` |

---

## Part #1 — 2026-09-20 · Bundle received: what `s1_deploy.tar.gz` is

**Context.** A deploy bundle for a new strategy ("S1 TWAP Lock-In") arrived. It was placed in
the folder above, extracted (`tar --force-local -xzf` — Git Bash otherwise reads `C:` as a
remote host), and scanned for secrets: **CLEAN, no `.env`**. 10 files:

| file | what it is |
|---|---|
| `S1-PLAYBOOK.md` | the strategy on one page (edge, 5 entry rules, risk caps, promotion gate) |
| `backtest_report.md` | the honest B-step: what was killed, what survived |
| `README.md` | plain-language harness overview |
| `CREDENTIALS.md` | key-handling guide (placeholder only, no key) |
| `trader.py` | the $10 gated trader — **DRY by default** |
| `twap_lockin_harness.py` | the observer/arbiter — DRY, never trades, writes `rounds.csv` |
| `run_harness.sh`, `run_trader.sh` | launchers |
| `deploy/s1-harness.service`, `deploy/s1-trader.service` | systemd units |

**The strategy, in plain words.** Polymarket's BTC 5-min / 15-min "Up or Down" rounds do
**not** settle on the last price. They settle on the **average** of the Chainlink 60-second
TWAP over the whole round versus the open. An average fills like a bathtub — with one minute
left, ~80% of the water is already in. So late in the round the outcome is nearly locked, but
the order book keeps trading the last tick, where bots still pay for "comeback" odds the math
no longer supports. **Buy the side that is already winning, cheaply, because the book lags the
true settlement.**

**Entry rules (all hardcoded in `trader.py`, all must hold):**
1. last **45 s** (5m) / **90 s** (15m) of the round;
2. locked-TWAP gap **≥ 4 bps** from the open (`GAP_MIN = 4.0`);
3. our side's ask in **[0.55, 0.97]** (`ASK_BAND`) — the *lagging* book. If the opposite side
   has already converged (our ask ≤ 0.05) that means *our* proxy is wrong → never trade;
4. post-fee edge **> 2 ¢** (`EDGE_MIN = 0.02`, fee = `0.07·p·(1−p)`);
5. hard gates: key + funder present, no `KILL` file, feed < 10 s old.

**Risk caps:** `TRADE_USD = 10`, one trade per round, `MAX_PER_HOUR = 3`, `DAILY_STOP = 20`
(writes `KILL` automatically), FOK orders only, hold to resolution.

**Backtest verdict (the report's own words).** Settlement semantics validated **70/70 (100%) at
gap > 4 bps**, coin-flip below 1 bps. The naive "money-printer" reading was **killed** — its
divergence trades won **40%**. The only survivor is the lagging-book entry at ≥ 4 bps
(15 rounds, 73% — *"below the proof bar"*). Hence the promotion gate, in code:
`ARB_MIN = 50` arbiter rounds with `fa_twap = yes` **and** `SIG_MIN = 20` dry signals at
`SIG_WR ≥ 0.90`. `FORCE_LIVE=1` exists in the shipped `trader.py` (lines 103/188/192) and
bypasses only the *statistical* gates; the hard gates can never be bypassed.

---

## Part #2 — 2026-09-20 · `twapvm` redeploy: both services, path reconciled

**Context.** Before this, `twapvm` ran a single home-made unit (`twap-harness.service`) that
started **only `trader.py`**. The observer (`twap_lockin_harness.py`) had been run once by hand
at 14:29Z and died at 14:30Z. Consequence: `rounds.csv` was **0 bytes**, so the arbiter gate
read `0/50` and could never climb — the trader only *reads* `rounds.csv`; only the observer
*writes* it. The bundle ships two units for exactly this reason.

The bundle's units pointed at `/opt/s1`, but everything already deployed and documented
(runbooks, VPS context prompt) uses `~/s1_harness`. Decision: **standardize on
`/home/ubuntupolymarket3/s1_harness`** and fix the two unit files to match, rather than create a
second location. Verified first that both scripts anchor `rounds.csv` / `.env` / `KILL` / logs to
`HERE = dirname(__file__)`, so the launchers' `cd ..` is harmless — observer and trader use the
same `rounds.csv` no matter the cwd.

**Change.**
- `deploy/s1-harness.service`, `deploy/s1-trader.service`: `WorkingDirectory` and `ExecStart`
  → `/home/ubuntupolymarket3/s1_harness/…`; added `User=/Group=ubuntupolymarket3`,
  `After/Wants=network-online.target`, `RestartSec=10`, and `StandardOutput/Error=append:`
  to `s1-<name>.service.log` so tracebacks are never lost. (Synced into the bundle copy too.)
- Old `twap-harness.service`: **stopped + disabled, file kept** (nothing deleted).

**Run mechanism.**
```bash
scp -rq <bundle>/. twapvm:~/s1_harness/           # refresh code, add PLAYBOOK + deploy/
ssh twapvm 'sudo systemctl disable --now twap-harness.service
  sudo cp ~/s1_harness/deploy/s1-*.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now s1-harness.service s1-trader.service'
```
**Verified (20:20Z):** both `active` + `enabled`; observer child `python3 -u
s1_harness/twap_lockin_harness.py` alive under the service cgroup (checked via `systemctl status`,
**not** `pgrep -f`, which matches its own ssh command); RTDS connected and streaming; trader
`trader starting | arbiter=0/50 | dry n=2 | live=0`; no `.env` → DRY; no tracebacks.

---

## Part #3 — 2026-09-20 · Account 2 funds and activity confirmed (read-only)

**Context.** Before any incubation money is discussed, prove the money exists and that the
account is idle.

**Run mechanism.** Keyless `eth_call balanceOf` on Polygon against the proxy for pUSD
(`0xC011…2DFB`, Polymarket's collateral — **not** USDC), USDC.e and native USDC; then the
public data-api `/value`, `/positions`, `/activity`.

**Result.**
| | |
|---|---|
| pUSD at proxy `0xF095…A3Ed` | **65.065718** — two independent RPCs agree exactly (`polygon.drpc.org` from the PC, `1rpc.io/matic` from `twapvm`) |
| USDC.e / native USDC | 0 / 0 |
| data-api `/value` | **0** — trap: `/value` counts open *positions* only, the $65 is *cash* |
| open positions | 3 dust tokens from July, worth $0 |
| last activity | 2026-09-17 14:49Z, a batch of REDEEMs ($5/$10) — the EXP-A cleanup; **idle since** |

Trap noted: `polygon-rpc.com` (401), `publicnode` (403) and `ankr` (API key) all refused the
PC; `1rpc.io` rate-limits (429). The keyless ones that worked: `polygon.drpc.org`,
`1rpc.io/matic` (from the VM). The preflight script tries three in order.

---

## Part #4 — 2026-09-20 · All files pushed to `twapvm` reference folder

**Change.** `~/#1 TWAP Lock-In Convergence late-round taker/` on `twapvm` now holds the
archive plus the full `s1_harness/` (11 files). Secret scan on the *pushed* files: CLEAN, no
`.env`. The running copy stays `~/s1_harness/` — two folders on purpose (reference vs. live).

**Trap (fixed).** `scp` now uses SFTP, which does **not** pass the remote path through a
shell, so quoting a path containing `#` and spaces made it create a directory literally named
`'#1 TWAP …'` (quote characters included) while `mkdir` over ssh made the correct one. The
first listing came back empty and the "CLEAN" scan was therefore meaningless. Files were moved
into the correct folder; only the empty mangled dir was removed (`rmdir` cannot delete
anything with content). Rule: after any `scp`, verify with a real file count, never with the
exit code alone.

---

## Part #5 — 2026-09-20 · Signing bug found and fixed (execution plumbing only)

**Context — the bug.** `trader.py` line 117 built
`ClobClient("https://clob.polymarket.com", key=…, chain_id=137)` — **no `signature_type`, no
`funder`**, v1 client. That signs as a bare EOA. Account 2's money sits at its **proxy**, not at
the signer, so every live order would have been signed for an **empty wallet** and rejected.
The *proven* configuration comes from the live `btc5bot` on the fleet box (read-only grep):
`py_clob_client_v2` + **`signature_type=3` (POLY_1271)** + `funder=<proxy>`; memory also records
that the v1 client rejects type 3. (Another AI session had suggested type 1 + v1 — that would
have reproduced the bug; the fleet's own code is the ground truth.)

**Change list (`trader.py`) — 4 edits, strategy untouched.** Verified by `diff` against the
original: all 10 strategy constants (`ROUNDS, GAP_MIN, EDGE_MIN, ASK_BAND, TRADE_USD,
MAX_PER_HOUR, DAILY_STOP, ARB_MIN, SIG_MIN, SIG_WR`) byte-identical; entry logic, sizing, gates
logic, scoring untouched.
1. Docstring: `.env` now needs `POLY_FUNDER` too.
2. `gates()`: new **hard** reason `no POLY_FUNDER (proxy) in .env` — without it the client
   would silently sign as the EOA, so this is a hard block, never bypassable.
3. `client_for(env)`: L1 `ClobClient(host, key, chain_id=137).create_or_derive_api_key()` →
   L2 `ClobClient(host, key, chain_id=137, creds, signature_type=POLY_SIGNATURE_TYPE (default 3),
   funder=POLY_FUNDER)` → best-effort `update_balance_allowance(COLLATERAL)` so the CLOB ledger
   sees the proxy's pUSD (exactly what the live bot does).
4. `place()`: v2 imports; `create_and_post_order(args, order_type=OrderType.FOK)` — in v2
   `order_type` is a **keyword argument** (v1 took `options={"order_type": …}`); `options=None`
   lets the client resolve tick size / neg-risk from the CLOB itself.

**Two API facts checked in the v2 source before trusting them.**
- `OrderArgs is OrderArgsV2` → True (the type `create_and_post_order` expects).
- `OrderArgs.side` is a **`str`** (`"BUY"`/`"SELL"`). `py_clob_client_v2.Side` is an **IntEnum
  (BUY = 0)** meant for `MarketOrderArgsV2`. The builder normalizes the enum only inside
  `if isinstance(side, Side):`, then compares to the `"BUY"` string and raises otherwise — so
  the string constant `order_builder.constants.BUY` is the canonical, safe input. Used that.

**Run mechanism.** `pip3 install --break-system-packages py-clob-client-v2==1.1.0` on `twapvm`
(same version as the fleet box; both Python 3.12.3). `py_compile` locally and on the VM. scp
`trader.py` → `~/s1_harness/`, `sudo systemctl restart s1-trader.service` (observer not
touched). Synced the same file to the local bundle and the VM reference folder —
**md5 `5d8725d7…` identical in all three places**.

**Verified (20:44Z):** trader `active`, new PID, `trader starting | arbiter=2/50 | dry n=2 |
live=0`, 0 tracebacks, still no `.env` → DRY.

---

## Part #6 — 2026-09-20 · `preflight_check.py` added (read-only, places no orders)

**What it does.** Proves the trader can *see* its money through the **exact** code path a
live order would use, without placing one.
- **Stage A (no key needed, always runs):** host country (must be non-US); on-chain pUSD at the
  funder via keyless RPC; the trader's own gate status read from its files (arbiter count from
  `rounds.csv`, dry stats from `trader_stats.json`, `KILL`, which `.env` keys are set — names
  only); `py_clob_client_v2` importable.
- **Stage B (only when `.env` has `POLY_PRIVATE_KEY` + `POLY_FUNDER`):** builds the client via
  `trader.client_for()` itself; prints signer EOA and funder truncated; warns if signer == funder
  (that would be a bare EOA, not a proxy); `get_api_keys()`; `update/get_balance_allowance
  (COLLATERAL)`; compares CLOB balance to on-chain → MATCH / DIFF.
- Never prints the key. Exit 0 = plumbing OK, 1 = plumbing problem. Final line states
  separately: plumbing PASS/FAIL · strategy gates MET/NOT MET · NO ORDER WAS PLACED.

**Run mechanism.** `cd ~/s1_harness && python3 preflight_check.py [--funder 0x…]` — the
`--funder` flag is only for running it *before* a `.env` exists; the address is then given on
the command line and never written to any file.

**First run (20:44Z, no `.env`):** country NL OK · pUSD **65.065718** at `0xF095…A3Ed` ·
arbiter **2/50 NOT MET** · dry signals **n=2/20, wr=50% NOT MET** · KILL absent · `.env` absent
→ DRY · v2 importable · Stage B skipped · **NO ORDER PLACED**. Exactly the honest state.

---

## Part #7 — 2026-09-20 · The arbiter is accumulating (first real rows)

**Data (20:44Z, 25 minutes after the observer came up):** `rounds.csv` has **3 completed
rounds** (was 0 for the whole previous day).

| round | open (TWAP) | full-round avg | settled | `fa_twap` | gap |
|---|---|---|---|---|---|
| 5m 1789936200 | 81196.86 | 81132.93 | Down | **yes** | −7.9 bps |
| 5m 1789936500 | 81106.88 | 81095.39 | Up | **NO** | −1.4 bps |
| (third row) | | | | yes | |

The `NO` row is not a bug: at −1.4 bps the gap is inside the sub-1-bps-to-4-bps zone the
report calls a coin-flip, and its end value (81112.52) was *above* the open — the "end value"
hypothesis matched there, the full-round-average one did not. This is precisely what the ≥ 50
arbiter rounds are for: measuring how often the settlement rule holds on the **live** feed.
Only `fa_twap = yes` rows count toward `50`.

**Timeline, from the observed rate (not a guess):** ~3 rounds / 25 min with 2 of 3 `yes` ≈
5 counting rounds per hour → **≈ 10 h to 50/50** if the observer stays up (5m + 15m rounds,
288 + 96 per day). The slower gate is the **dry-signal** one: 2 signals in ~5 h, currently
**50% win-rate against a 90% bar** — on n = 2 that number means nothing yet, but it is the gate
that must turn green, and it will take days, not hours. That is the design (the ≥ 4 bps +
lagging-book filter is selective on purpose).

---

## Part #8 — What to do next (runbook; the strategy is not altered by any of this)

1. **Leave both services running.** They accumulate both gates in parallel, for free, with no
   key on the box. Check any time:
   ```bash
   ssh twapvm 'systemctl is-active s1-harness s1-trader; tail -3 ~/s1_harness/trader.log; \
     python3 ~/s1_harness/preflight_check.py --funder 0x<account-2-proxy>'
   ```
2. **When you decide to prepare the account (still DRY):** create `.env` **yourself, on the VM,
   in the terminal** — never paste the key into chat, a doc, or GitHub:
   ```bash
   ssh twapvm
   cd ~/s1_harness && umask 077 && cat > .env <<'EOF'
   POLY_PRIVATE_KEY=0x<account-2 signer key>
   POLY_FUNDER=0x<account-2 Polymarket proxy — the address that holds the $65>
   POLY_SIGNATURE_TYPE=3
   LIVE_TRADING=0
   EOF
   chmod 600 .env
   python3 preflight_check.py        # expect Stage B: CLOB balance 65.07, MATCH, NO ORDER PLACED
   ```
   The trader re-reads `.env` on every check — no restart needed. With `LIVE_TRADING=0` it
   stays DRY even with the key present.
3. **Go live only when the trader's own log prints both** `arbiter=50/50` **and**
   `dry n≥20 wr≥90%`. Then, and only then, set `LIVE_TRADING=1` in `.env`. Do **not** use
   `FORCE_LIVE=1` — it exists, and it overrides the exact evidence the incubation is collecting.
4. **Kill switch, any time:** `touch ~/s1_harness/KILL` → instant drop back to dry. The $20
   daily stop writes it automatically.
5. **Sizing reality check** (for the go-live decision, not a change): $65 bankroll, $10/trade,
   $20 daily stop = a 31% daily loss cap. Fine for a proven edge; consider whether to top up
   before flipping live.
6. **Candidate next changes (not done, not decided):** Telegram alerts to thread
   `-1002282822022_600` (this bundle has none); a `SCORE`-line watcher so gate progress is
   pushed instead of polled.

---

## Trap ledger for this strategy (T-numbers, local to this file)

| # | trap | rule |
|---|---|---|
| T1 | `pgrep -f` matches its own ssh command → false "running" / self-kill | check processes via `systemctl status` / `MainPID` |
| T2 | `scp` (SFTP) takes quotes literally in remote paths with `#`/spaces | verify with a real file count after every scp |
| T3 | data-api `/value` = 0 for a cash-only account | `/value` is positions only; cash is on-chain `balanceOf` |
| T4 | most "public" Polygon RPCs now refuse keyless calls (401/403/429) | `polygon.drpc.org`, `1rpc.io/matic` worked; try several |
| T5 | bare-EOA client on a proxy account signs for an empty wallet | `signature_type=3` + `funder=<proxy>` + v2 client (the live bot's config) |
| T6 | `py_clob_client_v2.Side.BUY` is `0`, `OrderArgs.side` wants `"BUY"` | use `order_builder.constants.BUY` for `OrderArgs` |
| T7 | v2 `create_and_post_order` takes `order_type=` as a kwarg | v1's `options={"order_type": …}` silently means GTC in v2 |
| T8 | the observer must be a *service*; a hand-run dies with the terminal | `rounds.csv` empty = arbiter frozen at 0 forever |

---

## Part #9 — 2026-09-20/21 · Telegram alerts + data-quality monitor, `.env` hand-off, GitHub push

**Context.** Requests: (1) an `.env` the user fills in and Claude pushes; (2) push
`VPS-CONTEXT-PROMPT.md` to GitHub without credentials; (3) Telegram alerts that carry the
strategy name and send **only what matters**; (4) check the data for errors early so we do
not wait days and then discover the numbers were bad.

**Design decision — a separate read-only service.** Alerts and checks live in a third unit,
`s1-monitor.service` (`s1_monitor.py`), that tails the logs and reads `rounds.csv`. `trader.py`
is **not modified** (md5 still `5d8725d7…`), so the strategy stays provably untouched and a bug
in the monitor can never affect trading.

**What it sends (each once):**
- every `DRY SIGNAL`, `SCORE` (with a gates line: arbiter x/50, dry n/20, win-rate, dry P&L),
  `LIVE ORDER`, `LIVE ORDER FAILED`, `DAILY STOP`, `FORCE_LIVE` warning, `trader starting`;
- milestones: arbiter 10 / 25 / 50, dry signals 5 / 10 / 20, and `🟢 READY` when both gates
  are met (sent once ever);
- state changes: `.env` appeared (key **names** only), `LIVE_TRADING` flipped, `FORCE_LIVE`
  set, `KILL` written/removed, a service down or restarted by systemd;
- one daily summary at 07:00 UTC.
**What it never sends:** open-ref lines, stream updates, per-round `ROUND-RESULT`s, the
observer's duplicate `⚡ SIGNAL`. Warnings are de-duplicated for 6 h.

**Data-quality checks (thresholds in the file header):**
| check | fires when |
|---|---|
| **core hypothesis contradicted** | a round with \|gap\| ≥ 4 bps settles against the full-round-average rule (backtest said 70/70) — sent immediately, every time |
| trader-vs-observer settlement | the trader's `SCORE` and the observer's `settled` disagree for the same round |
| row sanity | non-numeric prices, `settled` ∉ {Up, Down}, bad `fa_twap`, misaligned/duplicate `round_start`, twap-open vs spot-open > 100 bps, any price > 3% from open |
| partial coverage | > 30% of the last 20 rounds are `partial(…)` (observer missing feed time — those rounds do not count) |
| rounds being lost | > 3 "row skipped / not final" per hour |
| frozen | no new round for 30 min while the observer is active; trader not snapping opens for 12 min |
| feed | > 6 RTDS reconnects per hour (trader or observer) |
| stats | `wins > n` in `trader_stats.json` |
| **constants guard** | any of the 8 strategy constants on disk differs from the expected value |

**Config.** `~/s1_harness/telegram.env` (chmod 600) — the bot token was copied **VM-to-VM**
from the fleet box through a pipe and never displayed; chat `-1002282822022`, thread `600`.
`.env` `TELEGRAM_*` keys override it. Without a token the monitor still runs every check.

**Run mechanism.** `deploy/s1-monitor.service` (User `ubuntupolymarket3`, `Restart=always`,
log `s1-monitor.service.log`); `sudo systemctl enable --now s1-monitor.service`.
**Verified 21:03Z:** three services active (`s1-harness`, `s1-trader`, `s1-monitor`); the
"monitor online" message was sent (HTTP 200, 0 Telegram errors); constants guard passed; all
three copies of the file identical. Arbiter at that moment: **5 rows, 3 `yes`, 0 partial**.

**`.env` hand-off (pending the user).** Template `PASTE-HERE-account2.env` in this folder
(git-ignored, verified with `git check-ignore`). Pre-filled: `POLY_FUNDER` (proxy),
`POLY_SIGNATURE_TYPE=3`, `LIVE_TRADING=0`, Telegram chat/thread. The user pastes **one** line
(the account-2 key). Then Claude: `scp` → `~/s1_harness/.env`, `chmod 600`, run
`preflight_check.py` → expect Stage B `CLOB balance 65.07 → MATCH | NO ORDER WAS PLACED`.
The trader re-reads `.env` each check; with `LIVE_TRADING=0` it stays DRY.

**GitHub.** `VPS-CONTEXT-PROMPT.md` (updated: twapvm = two services + reference folder)
pushed to `georgeskarlatos89-byte/btc-5min-twap-lockin`, commit `51be53b`, secret scan clean,
verified by reading the file back from the API. **Note:** that repo's `s1_harness/` folder is
the *original* bundle — the patched `trader.py`, `preflight_check.py`, `s1_monitor.py` and the
reconciled units are **not on GitHub yet** (not requested). Candidate next Part.

**Traps added:** T9 — a Windows working copy (CRLF) vs an LF repo makes `diff` show every line
changed; compare with `diff --strip-trailing-cr`. T10 — `gh repo clone … -- -q` can print
"failed to run git: exit status 128" while the clone succeeded; verify by listing the folder.

---

## Part #10 — 2026-09-21 · `.env` live on the box (still DRY), preflight Stage B PASS, GitHub push + a correction, first data findings

**`.env` hand-off — done.** The user pasted the key into `PASTE-HERE-account2.env`; the
placeholder text had been left on the same line, so it was stripped and the line validated by
shape only (`^POLY_PRIVATE_KEY=0x[0-9a-f]{64}$` → 1, never printed). Shipped to
`twapvm:~/s1_harness/.env`, `chmod 600`, owner `ubuntupolymarket3`. Keys present (names):
`POLY_PRIVATE_KEY POLY_FUNDER POLY_SIGNATURE_TYPE LIVE_TRADING`. `LIVE_TRADING=0` → the trader
stays DRY; the monitor sent `🔔 .env appeared - keys present: …` (names only) at 21:14:47Z.
The local paste file still contains the key (git-ignored); the user may delete it.

**Preflight Stage B — PASS (21:14Z, no order placed).** Through `trader.client_for()` itself:
signer EOA `0xC959…9886` · funder `0xF095…A3Ed` · sig_type 3 · API creds accepted (1 key
registered) · **CLOB ledger sees pUSD 65.065718 = on-chain 65.065718 → MATCH**. The single
`400 "Could not create api key"` line is benign: `create_or_derive_api_key()` tries *create*
first, the key already exists, so it *derives* it.

**GitHub.** Pushed `s1_harness/preflight_check.py`, `s1_harness/s1_monitor.py`,
`s1_harness/deploy/s1-monitor.service` → `georgeskarlatos89-byte/btc-5min-twap-lockin`,
commit `91d404c`, secret scan clean, sizes verified via the API.
**Correction to Part #9 (E-note).** Part #9 said the repo's `s1_harness/` held the original
bundle. **That was never verified and is false**: after this push the folder contains *only*
the three files above — no `trader.py`, no observer, no other units. A fresh clone is
therefore **not runnable** (`preflight_check.py` imports `trader`), and the runbook's
"Option A — clone from GitHub" would yield nothing. Cause: I repeated the runbook's text
instead of listing the repo. Rule: never state what a repo contains without listing it.
Fix offered (not done): push the complete patched `s1_harness/` set (secret-free).

**Monitor — first real alerts, all correct (0 Telegram errors):** the 21:09 `DRY SIGNAL`, the
21:13 `SCORE`, the `.env appeared` event. Nothing else was sent.

**Data findings after ~1 h (7 rounds seen by the trader, 6 rows in `rounds.csv`):**
| final gap | settled | `fa_twap` |
|---|---|---|
| −7.9 / −10.3 / −14.2 bps | Down / Down / Down | **yes, yes, yes** → **3/3 at ≥ 4 bps** |
| +0.9 / −1.4 / +1.5 bps | Up / Up / Down | yes / NO / NO → 1/3 below 4 bps (the coin-flip zone, as the report says) |

**A real data-collection bug (found by the trader-vs-observer comparison).** The trader
signaled on round `5m 1789938300` (21:09:36Z, t−26 s, gap **+6.9 bps**, Up @ 0.68, edge 30.5%)
and it settled **Down → LOSS**. The observer has **no row** for that round:
`[21:10:45Z] 5m 1789938300: no open-ref snapshot (late join) — row skipped` — although it had
been running since 20:20Z. Cause (in `twap_lockin_harness.py`): `settle_check()` is awaited
*inside* the ticker loop and polls gamma every 30 s up to 10× when a resolution is slow, so the
ticker can be blocked for up to 5 min and miss the **3-second** window in which it snapshots
the next round's open. `poll_book()` (HTTP, 15 s timeout) can do the same. Every slow
resolution silently drops the following round → the arbiter loses ~1 round in 7 and, worse,
the dropped rounds are the ones right after slow resolutions. Consequence here: we cannot tell
whether the +6.9 bps average genuinely flipped in the last 26 s (a ~0.7% move) or the model
was early — exactly the evidence the incubation exists to collect. Dry signals now **1/3
(33%)** on n = 3 — far too few to mean anything, but the honest state.

---

## Part #11 — 2026-09-21 · Observer fix deployed: open snapshot moved onto the feed event

**Context.** Part #10 found that the observer misses a round's 3-second open window whenever
its ticker is blocked (resolution polling up to 5 min, or a slow book HTTP call). The log
timeline confirms the mechanism exactly: the 15m round `1789937100`'s resolution poll ran
`21:00:45Z → 21:05:46Z` (10 × 30 s, then gave up) — and the 5m bell at `21:05:00Z` inside that
window is the round that was skipped (`1789938300`). An earlier block did the same to
`1789937700` at 21:00. **3 rounds lost in the first hour** out of ~14 seen.

**Change (`twap_lockin_harness.py`, one code line + comment; nothing else touched).** In
`on_twap_update()` — the RTDS feed handler, which runs in its own coroutine and is never
blocked by the ticker's awaits — call `snapshot_open_refs(T, label, start, now)` for each
round type. Same function, same rule (first 60 s-TWAP value within 3 s of the bell), so the
semantics of `open_ref_twap` are unchanged; the ticker's call stays as a fallback and is a
no-op once the open is set. `diff` against the original: exactly one added statement.

**Run mechanism.** `py_compile` locally + on the VM; shipped to `~/s1_harness/`, the VM
reference folder and the local bundle (md5 identical); `sudo systemctl restart
s1-harness.service` at 21:18:48Z (trader and monitor untouched). Cost: the in-progress round
`1789938900` was lost to the restart (one-time).

**Verified.** Next bell `21:20:00Z`, round `1789939200`: observer
`open refs snapped twap=80867.06 spot=80879.06` at **21:20:00Z**; trader `open ref 80867.42`
at the same second. Rows: 8 (6 `fa_twap = yes`). The blocked-ticker case cannot be forced on
demand, so the ongoing proof is the monitor's *lost-rounds* counter: "late join" skips should
now be ~0; if they continue, this Part is wrong and gets a correction.

**Second loss mode found (not fixed, candidate next Part).** `resolution not final yet,
skipping row` — when gamma has not flagged the market closed within the 10 × 30 s poll, the
observer **drops the round entirely** (its open/average data is discarded), whereas the trader
keeps retrying every tick until final. Seen once (15m `1789937100`, 21:05:46Z). Fix would be:
keep the round pending and retry later instead of skipping. Expect it to hit 15m rounds more.

---

## Part #12 — 2026-09-21 · `S1-VPS-STACK.zip`: the S9 wallet watcher deployed as a 4th service, alerts extended

**What the zip is.** A full "run what ran in the sandbox" package from the other session
(the US-sandboxed one): its versions of `trader.py` / `twap_lockin_harness.py` /
`preflight_check.py`, an `.env.example`, **the new `s9_watch.py`**, its own run data
(`rounds.csv` 2/50, `trader_stats.json`, logs, `s9_data/`), three systemd units (`YOURUSER`
placeholders, venv paths), tmux start/stop scripts, `requirements.txt`, and docs
(`README-VPS-DEPLOY.md`, `EDGE-AGREEMENT-BUILD-LOG.md`, `docs/` × 4). Extracted next to the
zip into `S1-VPS-STACK/`; secret scan **CLEAN** (the only 64-hex strings are market
condition-ids in `s9_follows.csv`; no keys, no tokens, no `.env`).

**What `s9_watch.py` does (measure-only, stdlib only, never trades).** Every 4 s it polls the
public data-api for the taker fills of four wallets — **gabigol** (PRIMARY, rule: 2nd+ BUY,
≥150 s left, ask ≤ his price + 1¢), **ce25** (SECONDARY, 1st BUY, ≥60 s left), **13e0**
(MONITOR only), **gabagool22** (any fill → RETURN alert; 3rd+ early buy → PYRAMID signal) —
classifies each fill's signal index and time-left, fetches the live book for fresh fills,
logs `CANDIDATE FOLLOW` when a rule passes, resolves each candidate after the round settles
(`OUTCOME WIN/LOSS`, cumulative EV) against pre-registered gates (INCUBATE only at ≥100
followed rounds AND ≥40% availability AND EV > +2¢/sh; KILL < +3¢), and measures latency.
Its **edge-agreement** module tails *our* `harness.log`/`trader.log`, pins every dry signal to
its round, and after settlement asks: did any watched wallet trade that same round — same
side, opposite, or absent, how big, and how long before/after us? One CSV row per signal ×
wallet (`s9_data/s9_edge_agreement.csv`). Its own docs report both copy candidates
**already failed** their gates on deep replays (gabigol −2.8¢/sh over 146 rounds; ce25 −0.6¢,
availability 37%); the watcher continues as *measurement*, and the surviving hypothesis is
"conviction pyramids early in a round" as a market signal, not a wallet to copy.

**Decision — what was and was NOT deployed.**
| zip component | decision | why |
|---|---|---|
| `s9_watch.py` | **deployed** to `~/s1_harness/` (4th service) | the only new thing; anchors to its own dir → tails our logs, writes `~/s1_harness/s9_data/` |
| `s9_data/` (state + 12 agreement rows + 43 follows + latency) | **seeded**, adapted | continuity, as the author intended; adaptations below |
| its `trader.py`, `.env.example` | **not deployed** | v1 client + `POLY_SIGNATURE_TYPE=1` — the config shown in Part #5 to sign for an *empty* wallet on account 2 (needs v2 + type 3 + funder, which is what runs) |
| its `twap_lockin_harness.py` | **not deployed** | lacks the Part #11 snapshot fix |
| its `preflight_check.py` | **not deployed** | ours is the one that passed Stage B |
| its `rounds.csv` / `trader_stats.json` / logs | **not deployed** | sandbox data (2/50) would clobber the VM's live arbiter (14/50) |
| its systemd units / tmux scripts / venv | **not used** | our units already run from `~/s1_harness` on system python; the watcher unit was rewritten (below) |
| docs | copied to the VM reference folder `~/#1 …/S1-VPS-STACK/` | reference only |

**Seed adaptations (`s9_watch_state.json`, before first start).** `log_offsets` → `{}` (they
pointed at the sandbox's paths `/home/user/btc-5min-twap-lockin/…`; with our paths the code
falls back to a first-read backfill pinned by bell lines, which is the author's documented
behaviour); wallet cursors `last_ts` → now − 600 s (their cursors were ~4 h old; the follow
rules only evaluate *live* fills, so back-processing thousands of old ce25 fills yields no
measurement, only a request burst); 7 stale `pending` candidates cleared (they would have hit
the author's 1-h resolution timeout anyway). `agree_done` (3) and `seen` (777) kept.

**Unit (`deploy/s9-watcher.service`) — one hazard fixed.** The watcher *exits* (code 0) when
`~/s1_harness/KILL` exists, and our trader *writes* `KILL` on its $20 daily stop — with the
author's `Restart=always` that is an infinite restart loop. Added
`ConditionPathExists=!/home/ubuntupolymarket3/s1_harness/KILL`: systemd holds the unit while
KILL exists and resumes on the next start/boot after removal. `--backfill 0 --poll 4` as
shipped; stdout → null (it self-logs; redirecting duplicates lines, per the author), stderr →
`s9-watcher.service.log`.

**Monitor extension (`s1_monitor.py`, additive; `trader.py` untouched).** Tails
`s9_data/s9_watch.log` and forwards, each once: `OUTCOME` (📊 resolved follow with cumulative
EV), `*** G22 RETURN` / `PYRAMID` (🚨 gabagool22 is back), `EDGE-AGREE` and `CO-SIGNAL` (🔗
did the pros trade our signal's round?), S9 gate-verdict **changes** only (🟢 MET / 🛑 KILL),
`KILL file present` exit, `s9_watch start`. Never: per-fill lines (~150 per minute at busy
bells), `CANDIDATE FOLLOW` (each becomes an `OUTCOME`), `SIGNAL-SEEN` (duplicate of the DRY
SIGNAL we already send), `STATUS` lines (used for the daily summary instead). Data checks:
feed lag > 30 s, > 10 errors/h, resolution timeouts (data gaps), no STATUS for 15 min while
active (stalled), `s9-watcher` added to the service-down / restart checks. First-sight
priming of the seeded 103 KB log so history is not replayed into Telegram.

**Run mechanism.** `py_compile` locally + on the VM; scp watcher + seed + unit + monitor;
seed patch via python3 on the VM; `sudo systemctl restart s1-monitor`; `sudo systemctl
enable --now s9-watcher` (21:48:08Z). NTP verified synchronized first (round pinning is
wall-clock).

**Verified (21:48–21:50Z).** Four services active. Watcher: 152 fills classified in the first
45 s, STATUS `feed_lag=7s`. On backfill it registered **three of our own earlier signals**
and cross-checked them at once — a real end-to-end test of the whole chain:
| our signal | result | who was on our side (and when) |
|---|---|---|
| harness 5m `1789938900` Up @ 0.90 (gap +1.6 bps) | **WON** | gabigol 12 fills **223 s before** us; 13e0 1 fill 49 s before |
| trader 15m `1789919100` Up @ 0.92 (gap +6.4 bps) | **WON** | ce25 26 fills 644 s before; 13e0 6 fills 795 s before |
| trader 15m `1789924500` Up @ 0.62 (gap +6.4 bps) | **LOST** | ce25 9 fills 744 s before; 13e0 25 fills 333 s before |
Running tally (same-side / signals): gabigol 4/6, ce25 2/6, 13e0 6/6, gabagool22 0/6 —
the pros are on our side *and earlier*, on winners and losers alike: **agreement ≠ win**,
exactly the author's finding #2. All three `EDGE-AGREE` lines, the watcher start, and the
`arbiter 10/50` milestone reached Telegram; 0 send errors. 4 HTTP-429/error lines in the
first minute (the seeded-cursor burst; the author's throttle absorbed them).

**Two corrections (E-notes).** (1) One spurious `🚨 s9-watcher.service is inactive` alert:
I restarted the monitor 3 s *before* enabling the watcher, so its first service check ran on a
not-yet-started unit. Sequencing artifact; the next messages show it running. Rule: start new
units *before* restarting the monitor. (2) Part #11's follow-up check "0 skips since the fix"
used an `awk` anchor on a timestamp (`21:18:48`) that never appears in the log, so it counted
nothing — **invalid**. The real record: exactly **two** skips after the restart, both the
rounds *in progress* at the restart (5m + 15m `1789938900`), then **every bell snapped** —
seven consecutive 5m bells 21:20→21:50 and both 15m bells — versus 7 rounds lost in the hour
before the fix. The monitor's `lost 4 rounds` warning at 21:32 was correct (3 bug-caused + 2
restart-caused inside its 60-min window). Arbiter at 21:50Z: **14 rows, 10 `yes`, 0 partial**.

**Not done (not asked):** GitHub still holds only the 3 files from Part #10; the watcher,
patched trader/observer and units are not there. The rest of the zip's S9 tooling referenced
in its docs (`s9_deepwalk.py`, `s9_replay_g22.py`, `s9_habitat.py`, …) is **not in the zip**.

---

## Part #13 — 2026-09-21 · The ping storm (three stacked failures, fixed) — and the arbiter's verdict: **the S1 settlement premise is falsified**

### A. The incident (23:33Z → 01:31Z): ~1,600 Telegram messages

User: *"There's an issue am getting a lot of pings in telegram … here's so much."* Diagnosis
from the box (loop stopped first with `systemctl stop s9-watcher`):

| # | failure | evidence |
|---|---|---|
| 1 | **The trader wrote `KILL` on paper losses.** `trader.py` fires its "$20 daily stop" on `t.stats["pnl"]` — the *cumulative dry* P&L, never reset (the `day`/`day_pnl` fields exist but are unused) — regardless of `LIVE_TRADING`. Dry P&L touched −$20 at n=7 (43%) → `KILL` written 23:33:32Z. It then won 5 straight (n=12, 67%, −$15.9) but `KILL` stays forever. | `KILL` = `daily stop 2026-09-20T23:33:32`; `trader.log` `DAILY STOP hit -> KILL written` |
| 2 | **My unit condition did nothing.** `ConditionPathExists=!…/KILL` was on disk, but systemd does **not** re-evaluate `Condition*=` on automatic `Restart=` (only on explicit starts/boot). Watcher exits 0 on KILL → restarted in 10 s → exits → … | `NRestarts = 687`; 0 "condition" journal lines |
| 3 | **My monitor had no storm protection.** `s9_watch start` / `KILL file present` were forwarded as "always send" events, and restart alerts were keyed per restart *number*. | monitor log: **1,447** S9 forwards + **185** restart alerts |

Two of the three are mine. The user had explicitly asked for "only the relevant details, don't
send random notifications". E-notes below.

### B. Fixes (all deployed 01:33Z, verified)

- **`trader.py`** (risk plumbing; the 9 strategy constants byte-identical, re-checked): the
  daily-stop `KILL` write now requires `LIVE_TRADING == "1"`. A real-money kill switch must not
  trip on paper losses — and must never silently stop an unrelated service. (Left as-is: it is
  *cumulative*, not daily; when live that is the more conservative reading.)
- **Stale `KILL` retired by rename**, never deleted: `KILL.dry_daily_stop_2026-09-20T2333.retired`.
- **`deploy/s9-watcher.service`**: `Restart=on-failure` (clean exit on KILL stays stopped; the
  monitor reports it once; crashes still restart). Condition kept for explicit starts/boot.
- **`s1_monitor.py`**: (i) lifecycle lines (`start`, `KILL … exiting`) are `warn` class, keyed
  per service, 6 h dedup — never events; (ii) restart alerts keyed per *service*, 6 h dedup, with
  the count; (iii) a **global circuit-breaker**: at most **10 alerts per 10 min**, then ONE
  "🔇 ALERT STORM … muting 30 min" message, silence, then ONE "🔈 mute ended: N suppressed"
  summary. Proven offline by importing the real module with Telegram stubbed: 25 alerts fired →
  10 delivered + 1 mute notice, 15 suppressed, exactly one summary on lift, warn/once dedup
  intact.
- **Verified on the box:** four services active; watcher `NRestarts 0` and no `KILL` lines;
  after the monitor restart exactly **2** messages went out; 0 Telegram errors; all copies md5
  identical.

### C. What the storm hid: the milestone messages

While the thread was flooded the monitor also delivered `arbiter 25/50` (22:57Z), `dry 5/20`,
`dry 10/20`, and at **01:05:14Z `🏁 arbiter reached 51/50 — ARBITER GATE MET`** — and, at
**23:34:09Z, the one message that mattered:**
`🚨 CORE HYPOTHESIS CONTRADICTED: 15m 1789946100 gap +5.1 bps, full-round-avg said Up but settled Down.`
That round's observer row: `full=Up/NO tail60=Up/NO end=Down/YES` — the venue followed the
**end value**. It is also the round the trader lost (Up @ 0.74) that tripped the daily stop.

### D. The arbiter's verdict (71 full-coverage live rounds: 53 × 5m, 18 × 15m; `hypo_check.py`)

**Match rate of each settlement hypothesis against the official outcome:**
| hypothesis (vs TWAP open) | all rounds | rounds with \|avg gap\| ≥ 4 bps |
|---|---|---|
| full-round average (**the S1 premise**) | 59/71 = 83.1% | 38/39 = 97.4% |
| trailing-60 s average | 64/71 = 90.1% | 38/39 = 97.4% |
| **end value of the 60 s-TWAP stream** | **70/71 = 98.6%** | **39/39 = 100%** |
| (same three vs spot open) | 83.1 / 84.5 / 91.5% | 97.4 / 92.3 / 94.9% |

Those columns agree on most rounds, so only the rounds where they **disagree** can decide:
| deciding rounds: full-avg and end-value on opposite sides | **11** |
|---|---|
| settlement matched the **end value** | **11 / 11** |
| settlement matched the **full-round average** | **0 / 11** |
(Examples: 15m `1789939800` avg +3.1 / end −7.7 → Down; 15m `1789946100` avg +5.1 / end −1.7
→ Down; 15m `1789953300` avg +1.2 / end −23.7 → Down; 5m `1789945500` avg −2.1 / end +3.5 → Up.)
By |end-gap|: 9/10 within 1 bps (the single miss is a sub-1-bps bell-timing round), 11/11 at
1–4 bps, **50/50 at ≥ 4 bps**.

**Conclusion.** Polymarket's BTC 5m/15m rounds settle on the **60-second Chainlink TWAP at the
close versus at the open** — the *end value* of the stream — **not** on the average over the
whole round. (This is also what the fleet's own memory records as the Aug-7 "60 s-TWAP rule";
the S1 author misread it as a full-round average.) Consequences:
1. The "bathtub" lock-in exists only **inside the last 60 seconds**: with one minute left, **0%**
   of the settling window is known; at t−45 s (the trader's 5m window) 25%; at t−10 s 83%. The
   playbook's "with one minute left ~80% of the water is in" is wrong.
2. The "lagging book" is not lagging — it is pricing the end value correctly. S1's "edge" was the
   model being right *whenever the price did not reverse in the last seconds*, and losing whole
   stakes when it did. The dry ledger says exactly that: 8/12 wins bought at 0.94–0.97, four
   full losses, **−$15.9**.
3. The **arbiter gate is mis-specified**: it counts `fa_twap = yes`, which is mostly coincidental
   agreement (59/60 when both hypotheses agree). "58/50 — GATE MET" is therefore a **false
   green**. The gate needed to require the premise to *win the disagreement rounds* — the test
   above. That test is now in the monitor (see E).
4. The 70/70 in `backtest_report.md` was the same coincidence measured on Kraken-proxy data.

**Status of S1 as a strategy: premise falsified.** Per RBI it goes back to Research (or is
killed): any redesign must model **P(end-of-round 60 s TWAP > open)** given the current stream
value and the remaining seconds — which is what the book already prices; a residual edge, if any,
lives in the last ~10–20 s (the trailing-60 s hypothesis, 97%) where fast bots operate. Not
decided here; the user decides. **The trader must not go live on the current model.** Barriers
in place: `LIVE_TRADING=0` in `.env`, and the dry gate (8/12 = 67% < 90%).

### E. Changes made because of D (monitor only; the observer's data is what proved this)

- `s1_monitor.py`: a permanent **settlement-rule test** on every `rounds.csv` pass — on the
  disagreement rounds (≥ 5), if the full-round-average premise wins < 30%, alert once
  (`🛑 SETTLEMENT RULE … premise falsified … do NOT go live`), and the daily summary now carries
  the hypothesis line (fa / ev match, disagreement tally).
- `hypo_check.py` kept in `~/s1_harness/` (and the bundle copies) — re-run any time.
- One deliberate Telegram message sent with the finding (the earlier alert was buried).

### F. Other data since Part #12

- Second loss mode ("resolution not final yet, skipping row"): 4 in 4 h, **all 5m rounds ending
  on :00 / :25 / :30** — when a 15m round settles at the same moment the sequential resolution
  polling gives up on the 5m one. ~6% of 5m rounds. Fix still a candidate (retry later instead
  of dropping). Late-join skips after the restart: **0** (proper anchor this time).
- Watcher STATUS: ce25 follow candidates **39 rounds, 8% win, EV −0.283/share** — copying ce25
  is measured to be ruinous, consistent with its docs. gabigol: no taker fills for ~4 h (a maker
  now). 1 `agreement skip (no condition found)` (15m `1789943400`; gamma gap).

### G. E-notes
- **E: "Condition*= guards auto-restarts"** — false; verified by 687 restarts. Rule: a clean exit
  on a flag needs `Restart=on-failure`.
- **E: monitor without a global cap** — 1,600 pings. Rule: every alerter gets a circuit-breaker
  before it gets a token. Saved to memory as `feedback_alert_storm_rules`.
- **E: Parts #7 and #12 reported "the rule holds 3/3 / 37/38 at ≥ 4 bps" as support for the
  premise** — superseded: agreement rounds cannot support it; only disagreement rounds can test
  it, and they refute it 0/11. Rule: when two hypotheses are correlated, score them **only where
  they differ**.

---

## Part #14 — 2026-09-21 · The circuit-breaker tripped in production (CO-SIGNAL burst); once-class verdicts now bypass the mute

**What happened (01:34:39Z).** Minutes after Part #13's monitor went live, the S9 watcher
flagged a round our trader had signalled and logged a `CO-SIGNAL` line for **every fill** ce25
made in it — a scalper, so 20+ lines in three minutes. Each was forwarded as an event → the new
breaker hit 10 alerts / 10 min and **muted for 30 min, exactly as designed** (one `🔇 ALERT
STORM` message, then silence). But the mute also swallowed the `🛑 SETTLEMENT RULE` verdict at
01:42:54Z **and marked its once-key consumed**, so it would never have been sent. Two design
flaws in one incident, caught by reading the monitor's own log rather than trusting the plan.

**Changes (`s1_monitor.py`).**
1. `once`-class alerts (milestones, verdicts) **bypass the mute**: each key fires once ever, so
   they cannot storm; a once-key is marked consumed **only when actually sent**.
2. `CO-SIGNAL` de-duplicated to **one per wallet per round** (`warn` class, keyed
   `cosig:<wallet>:<round>`, "first fill only; full tally in EDGE-AGREE").
3. The swallowed key `hypo_falsified` was removed from the persisted once-list on the box
   (monitor stopped, state edited, started).

**Verified.** `ALERT[once:hypo_falsified] 🛑 SETTLEMENT RULE …` delivered at **01:45:20Z**
while the mute was still active (ends ~02:04Z; one `🔈 mute ended: N suppressed` summary will
follow). Offline, importing the real module with Telegram stubbed: 25 events → 10 delivered +
1 mute notice; a once-alert **during** the mute → delivered; its repeat → deduped; a plain
event during the mute → suppressed (counter 16). 0 Telegram errors; all copies md5 identical.
Messages the mute suppressed and that live only in the CSV/log: two `EDGE-AGREE` for round
`1789954200` (both signals **WON** Down; gabigol same side ~115 s earlier).

**Lessons added to memory (`feedback_alert_storm_rules`):** per-fill logs need per-entity
dedup before forwarding; verdict-class alerts must bypass any mute; never mark a once-key
consumed unless it was sent.

---

## Part #15 — 2026-09-21 · Independent confirmation against the venue's docs; S1 hard-blocked from live in code

**Context.** The other session verified Part #13's finding against Polymarket's own documentation
and wrote `SETTLEMENT-RULE-CONFIRMATION.md` (now filed in this folder and in the VM reference
folder; secret scan clean). Its documentary half, which the VM data alone could not supply:
- **Predictions Changelog, Aug 7 2026:** *"Both the price to beat and the final settlement price
  come from the applicable TWAP feed. Averaging windows: 5-minute markets use a 30-second
  lookback; 15-minute and 4-hour markets use a 60-second lookback."* **Aug 14:** 5-minute markets
  moved to the 60-second TWAP. Two boundary prices from the feed; the "window" is the feed's own
  lookback. A full-round average would be a 300 s / 900 s window — the 30 s → 60 s change is
  meaningless under that reading.
- **The live market description** is the sentence that misled S1: *"the TWAP … of the time range
  specified in the title"* invites "average over the round"; the changelog resolves it the other way.
- **Chainlink does not publish** the feed's sampling boundaries, weighting or rounding — so any
  reconstruction has an irreducible noise floor: the sub-4 bps band and the one 70/71 miss.
- Its own shipped `rounds.csv` held exactly one distinguishing round (`1789938300`, the 21:09Z
  losing signal): `fa=NO, t60=NO, ev=YES` — 1/1 for end-value, consistent with the VM's 11/11.
- Standing consequence #1 in its words: **"S1-as-built must never go live … under any circumstances."**

**Change — make that true in code, not prose (`trader.py`, hard gates; 9 constants byte-identical,
re-checked).** `gates()` now adds the reason `S1 live BLOCKED (settlement premise falsified - see
S1_LIVE_BLOCKED)` whenever the marker file `~/s1_harness/S1_LIVE_BLOCKED` exists. Hard gates are
the ones `FORCE_LIVE` **cannot** bypass (`live_ok = (not hard) and …`, line 220). The marker
(read-only, 565 B) states the reason and the sources. Reversible only by deliberately deleting it.
**`s1_monitor.py`:** watches the marker; if it ever disappears → `🚨 S1_LIVE_BLOCKED marker
REMOVED … Intended?`; daily summary shows `S1_LIVE_BLOCKED=yes/NO`.

**Run mechanism.** Compile local + VM; scp; marker written on the VM (`chmod 444`); restart
`s1-trader` then `s1-monitor` (02:17Z); one deliberate Telegram notice.

**Verified.** From the trader's own code: `python3 -c "import trader; print(trader.gates())"` →
`['S1 live BLOCKED (settlement premise falsified - see S1_LIVE_BLOCKED)', 'feed stale']`. Four
services active; monitor sent exactly one forward after restart; 0 Telegram errors; all copies md5
identical. The Part #14 mute ended at 02:04:54Z with one summary (23 suppressed). Arbiter 68/50
(still a false green — recorded for completeness; it now gates nothing that can trade).

**Barriers to real money, in order:** (1) `S1_LIVE_BLOCKED` hard gate — cannot be bypassed;
(2) `LIVE_TRADING=0` in `.env`; (3) dry gate 8/12 = 67% < 90%; (4) hard gates key/funder/KILL/feed.

**Open decision (the user's):** S1′ — re-research on the true rule
(`TWAP_feed(end) vs TWAP_feed(open)`, edge if any in the last ~10–20 s, the fast-bot habitat) as a
**new** strategy starting at Research — or stop here. The observer, watcher and monitor keep
running either way; their data is what proved this.

---

## Part #16 — 2026-09-21 · Breaker tripped a second time (S9 `OUTCOME` lines); per-item forwarding removed for good

**What happened.** The user forwarded `🔇 ALERT STORM: 10 alerts in 10 min … last: 📊 S9 OUTCOME
[ce25-unnamed] WIN …`. From the monitor log: 11 S9 events inside one 10-minute window, and the
watcher log shows **30 `OUTCOME` lines in the 02:00 hour** — ce25 is a scalper, so one round can
yield many follow candidates, each resolving to its own line. The breaker muted correctly (one
notice, one summary when it lifted). Not a storm — but still a per-item source I had left in.

**Change (`s1_monitor.py`).** `OUTCOME` and `CO-SIGNAL` are now **counted, never forwarded**:
the daily summary reports "N follow outcomes in 24h" plus the watcher's STATUS line (per-wallet
follows / win-rate / EV); `EDGE-AGREE` (one line per *signal round*, after settlement, with the
full per-wallet tally) remains the forwarded S9 event, together with gabagool22 RETURN/PYRAMID,
gate-verdict changes and 6-h-deduped lifecycle lines. Remaining event sources and their worst-case
rate: trader `DRY SIGNAL`/`SCORE`/`LIVE ORDER` ≤ ~4 per 10 min; `EDGE-AGREE` 2 per signal round;
everything else deduped or once-only — all comfortably under the 10-per-10-min ceiling.

**Verified (03:06Z).** Four services active; monitor restarted with **zero** forwards after
"monitor started"; 0 Telegram errors; working / reference / local copies md5 identical.

**Rule generalized (memory `feedback_alert_storm_rules` #6):** any log that emits one line per
fill / tick / item is never forwarded — count it, digest it daily, forward only per-round or
per-signal summaries. Two trips in one night were needed to learn it fully; recorded so it is not
learned a third time.

---

## Part #17 — 2026-09-21 · `POLYMARKET-VPS-STACK.zip`: security review, the BME recorder deployed as the 5th service, disk sizing, integrity job, hourly status

**What the zip is (35 MB, 88 files, extracted to `POLYMARKET-VPS-STACK/`).** The other session's
four-strategy package with its own verdicts: `1-S1-TWAP-LOCKIN` **falsified**, `2-S9-WHALE-COPYTRADING`
**closed** (7-day habitat backtest: follower EV −1.35¢/sh on 9,941 rounds), `3-S11-SCHEDULED-BOOTH`
**killed** (a post-only rebate-maker idea; both "pockets" failed a 7-day pre-registered
validation: −0.83¢ and −4.30¢/sh — *"there is no $65 booth"*; `booth.py` is marked "not a service"),
and `4-BME-BOOK-MOVEMENT-ENGINE` = **the one active thread**: record 7 days of order-book
movements (a dataset no endpoint serves historically), then score five pre-registered signals.
Plus `source-docs/` (venue pages), a START-HERE, units, tmux scripts.

**"Make sure the zip has no bad code" — what was checked.** Every `.py`/`.sh`/`.service`
scanned for `eval/exec/subprocess/os.system/curl|sh/base64/socket/paramiko/crontab/rm -rf/sudo/
requests.post/smtplib/pickle`: only websocket `ws.send` (subscribe/PING) hits. **Every network
destination**: `gamma-api`, `data-api`, `clob.polymarket.com`, `ws-subscriptions-clob`,
`ws-live-data`, `polymarket.com`, `api.kraken.com` (backtest data) — nothing else. No `.env`, no
key material; secret-scan hits are market/condition ids in data CSV/JSON and all-zero example
values in three source-doc pages. **Read line by line:** `booth.py` (dry by default; live only
with `LIVE_BOOTH=1` + key + unreachable virtual gates; v1 client + sig type default 0 — would
need the Part #5 fix if ever used; **not run**), `bme_capture.py`, `bme_score.py`, `g22_watch.py`,
the `s9_watch.py` diff (only a log-path lookup for the packaged layout). `trader.py`, the
observer and `preflight_check.py` are byte-identical to the previous zip (already reviewed; not
deployed — ours are the fixed/hard-blocked versions). Research scripts (`s9_pyramid_backtest*`,
`s9_replay_g22`, `s9_shortlist`, `s9_habitat`, `s9_deepdive`, `s9_deepwalk`, `backtest_s1`) were
scanned, not read — they are offline tools and do not run on the VM. **Credentials:** nothing
that runs needs any (the recorder is a public websocket); account 2's `.env` stays in
`~/s1_harness` and was not copied anywhere.

**What runs from it (and what deliberately does not).** Deployed to `twapvm:~/POLYMARKET-VPS-STACK/`
(the package as reference + the BME runtime). **Only `bme-capture.service`** was started —
the package's own START-HERE names BME the active thread. Its S1/S9 units were **not** enabled:
they would duplicate the services already running (patched) from `~/s1_harness`. The shipped
20-minute sandbox sample (dated today) was moved to `s9_data/bme/sample_20min_sandbox/` so the
recorder could neither append to it nor overwrite it at midnight.

**Disk — the real risk, measured twice.** First 5-min window: **801 MB/hour raw, 1,078 events/s,
gzip 11.6×**. The author's design keeps the whole UTC day raw and gzips at midnight → ~19 GB of raw
file per day on a box with **15 GB free**: the capture would have died around hour 16, every day.
Patch (`bme_capture.py`, storage plumbing only — what is recorded is unchanged): events and
books are written as **streaming gzip** (peak raw ≈ 0; each restart appends a gzip member),
rotation appends instead of overwriting (`ab`), and **SIGTERM is handled** so `systemctl stop`
closes the streams with a valid trailer. `bme_score.py`: skips header rows at member boundaries
and **tolerates a truncated last member** (a crash mid-stream no longer aborts the whole score).
Existing raw files from the first 10 minutes were folded into the `.gz` as members (nothing lost).
Second measurement, 10 min with two bells, patched build: **93 MB/hour compressed (11.1×) ≈
2.2 GB/day → ≈ 15 GB for 7 days — exactly the free space.** Verdict: **the boot disk must be
resized (→ 50 GB, GCP console, no downtime; then `growpart` + `resize2fs`)**; the guard below is
a backstop, not a plan. CPU 12–17%, RSS ~120 MB, 0 reconnects in the first hour.

**Guards + monitoring added (units in `POLYMARKET-VPS-STACK/deploy-twapvm/`).**
- `bme-capture.service`: `Restart=on-failure` (exits 0 on its own KILL — the Part #13 lesson),
  `Nice=5`, stdout → `s9_data/bme/bme.log`.
- `bme-diskguard.timer` (every 5 min, root): stops the recorder when free disk < 2 GB and logs
  why; dry-run at 15 GB free did nothing (verified). The monitor alerts if the guard ever fires.
- `s1_monitor.py`: `bme-capture` in the service checks; disk free alerts (⚠ < 25 %, 🚨 < 10 %);
  today's `.gz` must keep growing (stall alert at 10 min); reconnect storms; **live validation of
  the 1-s book-state file** (fields, prices in (0,1), mid = (bid+ask)/2, depths ≥ 0, ≥ 60 rows per
  token per 5 min); `hashes_*.csv` freshness. A locked book (bid = ask = 0.56 for one second at
  08:31:47) surfaced on the first pass — a real market state, now counted separately and warned
  only above 1 % of rows.
- **`bme-integrity.timer` → `bme_integrity.py`, daily 03:30 UTC on yesterday's files**, read-only,
  cross-referenced against the venue: [1] every events row parses + per-type validity + counts;
  [2] round coverage and > 60 s gaps; [3] **our recorded TWAP ticks vs the S1 observer's
  `rounds.csv`** (a separate process on the same feed — must agree within 1.5 bps); [4] the tokens
  we subscribed to vs gamma's `clobTokenIds`; [5] our trade prints vs the official data-api tape
  (sampled, ratio); [6] every round resolvable via gamma. One Telegram line, report JSON + txt.
  **First run on today's partial data: 2,392,207 rows, 0 invalid; 9 rounds; RTDS vs observer 3/3
  (worst 0.1 bps); tokens 10/10; prints/tape 1.92; outcomes 4/4 — ✅, in 21 s.**
- **Hourly status** (user request): ONE combined message at the top of every hour — services,
  then for each instrument a plain-English one-liner (`STRAT_DESC`) plus its numbers (rounds +
  settlement tally + dry ledger + LIVE_BLOCKED; S9 outcomes last hour + STATUS; BME MB added, rows,
  reconnects, today's file, disk, day N of 7). Bounded, so it bypasses the breaker. The 07:00 daily
  summary stays. Everything else remains urgent-only under the 10-per-10-min breaker.

**Verified (08:47Z).** Five services active (`s1-harness`, `s1-trader`, `s1-monitor`,
`s9-watcher`, `bme-capture`), both timers active, monitor restarted with zero forwards, 0 Telegram
errors, all copies md5 identical, disk 15 GB free, events file 54 MB gz after 28 min.

**What to expect / timeline / RBI (answer to the user, recorded here).** Days 1–7: hourly
status lines; a daily ✅/🚨 integrity line at 03:30 UTC; the 07:00 summary; alerts only for real
faults. No orders, ever — every live path is gated or hard-blocked. **Valid results = after 7
full UTC days** (`bme_score.py`), the package's pre-registered B-step; the gate is
**≥ +2¢/share post-fee EV over ≥ 100 independent rounds**; borderline → extend to 14 days
(weekday/weekend regimes). RBI mapping: recording is the R instrument, the day-7 score is B,
incubation (I: $10, code-gated, `.env`-keyed) opens only on a pre-registered pass — exactly how
S1, S9 and S11 died at B for $0. Direction if something passes: S3 (early-round imbalance) or S4
(ask-pull latency after informed buys) are the candidates monetizable at our speed; S2 (pre-tick
cancel bursts) confirming would **not** be tradeable by us — it needs ~200 ms reactions, the fast
lane refused on the record. If nothing passes: the fourth honest kill; documented next habitats
are maker-rebate economics outside crypto and the Perps venue.

---

## Part #18 — 2026-09-21 · Boot disk resized 20 → 50 GB (new finding: the console path that works)

**Context.** Part #17 measured the BME recorder at ~2.2 GB/day compressed ≈ 15 GB for the 7-day
run, against 15 GB free on the 20 GB boot disk. Resize required.

**Finding — the console path that actually works (found by the user; recorded so it is not
searched for again).** Compute Engine → left sidebar **Storage → Disks** → click the disk **name**
in the list (`arena-ai-bots`, the first clickable link) → **Edit** (pencil, top toolbar of the
*Manage disk* page) → **Size** `20` → `50` → **Save**. Three dead ends cost a round-trip each:
(1) the VM *instance* Edit form shows the boot disk in a table but the name is **not clickable**
and the size is not editable there; (2) **Additional disks → Add new disk** creates a *second,
blank* disk (format + mount + move data) — wrong path, cancelled; (3) the disk page opens on
Properties, and Edit is in the top toolbar. CLI alternative: Cloud Shell
`gcloud compute disks resize arena-ai-bots --size=50GB --zone=europe-west4-a`. The snapshot
schedule `default-schedule-1` was left as is.

**Run mechanism (VM side, live, no reboot).** Checked the layout first: `sda` 50 GB, root =
`sda1` ext4 (19 GB). `sudo growpart /dev/sda 1` → `CHANGED: partition=1 … new size=102758367`;
`sudo resize2fs /dev/sda1` → `now 12844795 (4k) blocks long`.

**Verified.** Console: *Size 50 GB* (Disks list and disk page). VM: `sda1` 49 GB, filesystem
**48 GB, 44 GB free, 9 % used**. All five services stayed `active` throughout (`s1-harness`,
`s1-trader`, `s1-monitor`, `s9-watcher`, `bme-capture`). Headroom for the 7-day recording:
~44 GB vs ~15 GB needed (~3×); the disk guard (stop < 2 GB free) and the monitor's disk alerts
(⚠ < 25 %, 🚨 < 10 %) remain as backstops. At ~2.2 GB/day the disk would last ~20 days, so the
README's prune command (`*.gz -mtime +7 -delete`, after scoring) is only needed for runs past
~2.5 weeks.

**Recorded in:** memory `reference_gcp_disk_resize` (+ index line); `set-up new vm in cloud
console.md` Part 8 (the runbook for future VMs); this Part. Pushed to the VM reference folder and
GitHub.

---

## Part #19 — 2026-09-21 · Found and repaired: my commit `51be53b` had deleted 249 files from the GitHub repo

**How it surfaced.** Pushing Part #18, git on Windows refused the record's path (`Filename too
long`), and the scratch clone listed nearly the whole repo as *untracked*; GitHub then returned 404
for the runbook. Commit history (file count per commit):

| commit | files in repo | change |
|---|---|---|
| `f54306e` initial | 248 | +248 |
| `f92fd22` runbook | 249 | +1 |
| `aea7403` VPS prompt | 250 | +1 |
| **`51be53b` (mine, Part #9)** | **1** | **249 removed** |
| `91d404c` (mine, Part #10) | 4 | +3 |

**Cause.** The scratch clone made with `gh repo clone` on Windows had failed its checkout on long
paths (it printed `failed to run git: exit status 128` — trap T10 — which I recorded as harmless
after listing the folder). The working tree was partly populated but the **index was nearly empty**,
so `git commit` recorded every file missing from the index as deleted. I verified the pushed file
by reading it back, not the commit's diff stat, so the deletion went unnoticed.

**Correction to Part #10.** Part #10's E-note said the repo's `s1_harness/` "never held the
original bundle" and that a fresh clone was not runnable. **Wrong again, in the other direction:**
the repo *did* hold it (and 245 other files: API docs, changelogs, strategy doc, runbook) — my
`51be53b` had just deleted them 11 minutes earlier. The rule written there ("never state what a
repo contains without listing it") stands; the new rule is below.

**Repair (no force-push, no history rewrite).** Fresh clone with `core.longpaths=true` into a short
path (`C:\g\tlfix`); health check first: HEAD tree = index = 4 files, status clean. Restored
**exactly** the 249 paths deleted in `51be53b` from `aea7403` (`git diff --diff-filter=D … | xargs
git checkout aea7403 --`); none had been re-added since. Added the Part #18 finding (runbook Part 8,
this record). Staged: 250 A, **0 D**; secret scan clean. Commit **`bc22600`** pushed.

**Verified on GitHub (API, not the local clone).** 254 files (the 250 originals + 3 from
`91d404c` + this record); **0** of `aea7403`'s files missing; `bc22600` removed 0 files;
`VPS-CONTEXT-PROMPT.md` kept at its newer version; runbook has Part 8; record has Part #18.
The broken scratch clone (`scratchpad/tl_repo`) was renamed `tl_repo.BROKEN-do-not-commit`.

**Rules (memory `feedback_git_windows_clone`):** on Windows always clone with
`-c core.longpaths=true` into a short path; before any commit check that `git ls-files | wc -l`
equals `git ls-tree -r HEAD --name-only | wc -l`; after every push read the commit's
`removed`/`deletions` count, not just the file I meant to change.
