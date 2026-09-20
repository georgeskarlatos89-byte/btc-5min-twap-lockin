# VPS context prompt — paste this into any Claude session

Copy the block below and paste it at the start of a session so the assistant knows **which
VPS** you mean. (Sessions opened *inside the polymarket-bot project* already know this from
memory; use this for fresh sessions, other machines, or other tools.)

No credentials are in this file — SSH keys and tokens live only on the PC/VM, never here.

---

```
CONTEXT — I have TWO GCP VMs (both in the Netherlands; Polymarket geoblocks US IPs, so both
are non-US on purpose). Connect via the SSH aliases in my ~/.ssh/config.

1) polyvps   — 34.178.162.193, europe-west4, user ubuntupolymarket3.
   THE LIVE FLEET (~13 bots). Runs real money (btc5bot LIVE) plus btc5collector, hip3-funding,
   liq-statarb, liq-scanner, moonsniper, scout, strategy1-sltp, paper-maker, weatherbot, and the
   BTC-5min research stack (15m collector, audit, reconcile). ***Do not disturb this box by
   mistake — it holds real money.***

2) twapvm    — 34.34.13.7, europe-west4-a, user ubuntupolymarket3.
   THE TWAP LOCK-IN box. Runs ONLY the s1_harness (twap-harness.service, DRY, places no orders).
   Set up 2026-09-20 for the new strategy.

HOW TO TELL WHICH I MEAN:
- fleet / weather / hip3 / liq / moonsniper / btc5bot / audit / reconcile / probe / 15m collector
  / EXP-A..D / "main box" / "old box" / polyvps / 34.178.162.193   ->  polyvps
- TWAP / Lock-In / s1_harness / arbiter / arena-ai-bots / "new box" / twapvm / 34.34.13.7  ->  twapvm
- If it's genuinely ambiguous, ASK before running anything on either box.

Extras: alerts for the TWAP/BTC-5min work go to Telegram group -1002282822022 thread 600
("BTC-5-minute"). GitHub for this work is the account georgeskarlatos89-byte. Never put a
private key or token in chat; I create any .env myself on the VM.
```

---

## Quick reference (for humans)

| alias | IP | purpose |
|---|---|---|
| `polyvps` | 34.178.162.193 | **LIVE fleet** — real money, ~13 bots. Handle with care. |
| `twapvm` | 34.34.13.7 | **TWAP Lock-In** harness only, dry. |

Setup runbook: `set-up new vm in cloud console.md`.
