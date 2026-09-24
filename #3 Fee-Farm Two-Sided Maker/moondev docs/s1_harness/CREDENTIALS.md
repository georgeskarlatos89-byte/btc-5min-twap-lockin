# CREDENTIALS — what I need, when, and how to hand it over safely

**Short answer to "do you need my credentials?":**
*Not today. Never in chat. And never your main wallet.*

## Today (dry-run phase) — nothing is needed

`trader.py` runs in DRY RUN and logs what it *would* do. It cannot go live,
because going live requires four simultaneous conditions **in code**:

1. `s1_harness/.env` with `LIVE_TRADING=1` and `POLY_PRIVATE_KEY=0x…` (you create it);
2. **Arbiter gate:** ≥50 full-coverage rounds in `rounds.csv` with `fa_twap=yes`
   (true-feed settlement semantics still validated — currently accumulating);
3. **Dry gate:** ≥20 of the trader's own dry signals at ≥90% win-rate;
4. no `KILL` file, fresh feed, inside risk caps ($10/trade, 3/hour, $20 daily stop).

## When the gates pass — the safe procedure (you do all of this)

1. **New burner wallet.** Generate a fresh EVM wallet used *only* for this
   incubation. Not your main wallet, not your harvester wallet.
2. **Fund it small:** ~$20 USDC on Polygon + ~$1 of POL for gas (one one-time
   token-approval transaction; the SDK triggers it on first order).
3. **Create the env file yourself, in your terminal — never paste the key into
   this chat and never write it into any doc/knowledge file** (your own rule 4):

   ```bash
   cd s1_harness
   umask 077 && touch .env
   echo "POLY_PRIVATE_KEY=0xYOUR_BURNER_KEY" >> .env
   echo "LIVE_TRADING=0" >> .env
   chmod 600 .env
   ```

4. Watch `trader.log` print `arbiter=50/50` and the dry gate pass. **Then**, and
   only then, flip `LIVE_TRADING=1`. The next qualifying signal trades $10, FOK,
   one position per round.
5. Kill switch, any time: `touch s1_harness/KILL` → instant drop back to dry.
   The $20 daily stop writes KILL automatically.

## Where to run live

This sandbox is fine for the dry run. For live $10 incubation, prefer **your VPS**
(the one already running `harvest.py`): stable IP, lower latency to the CLOB, and
it keeps the key on a machine you control. Copy `s1_harness/` over, `pip install
websockets py-clob-client`, run `trader.py`.

## Hardening later (optional)

- **Session Keys** (docs: Trading → Session Keys): scoped, time-limited signer so
  the hot key can only trade, never withdraw.
- Rotate the burner after the incubation window; sweep remaining funds out.

## Compliance note

Polymarket enforces geographic restrictions (docs → Geographic Restrictions).
Confirm your jurisdiction permits trading before flipping `LIVE_TRADING`; the
venue's rules are yours to satisfy — the bot only enforces *our* risk rules.
