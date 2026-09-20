#!/usr/bin/env python3
"""
S1 PREFLIGHT — read-only proof that the trader can SEE its money. PLACES NO ORDERS.

Stage A (needs no key, always runs):
  * this host's country (Polymarket geoblocks US IPs for signed orders)
  * on-chain pUSD balance at the funder (proxy) via a keyless Polygon RPC
  * the trader's own gate status (arbiter, dry signals, KILL, .env keys present)
Stage B (only if .env has POLY_PRIVATE_KEY + POLY_FUNDER):
  * builds the client through trader.client_for() - the EXACT path a live order
    would use (py_clob_client_v2, signature_type=3, funder=proxy)
  * get_api_keys()  -> the creds are accepted
  * update/get_balance_allowance(COLLATERAL) -> the CLOB ledger shows the proxy's pUSD
  * compares CLOB balance to on-chain balance
Never prints the private key. Exit 0 = PASS, 1 = FAIL / not ready.

usage:  python3 preflight_check.py [--funder 0x...]   (--funder only needed when no .env yet)
"""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trader  # same directory; importing runs no network and places nothing

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"   # Polymarket collateral (NOT USDC)
RPCS = ["https://1rpc.io/matic", "https://polygon.drpc.org", "https://polygon-bor-rpc.publicnode.com"]

def http(url, data=None, hdr=None):
    h = {"User-Agent": "Mozilla/5.0"}; h.update(hdr or {})
    req = urllib.request.Request(url, data, h)
    return urllib.request.urlopen(req, timeout=20).read().decode()

def onchain_pusd(addr):
    pad = addr[2:].lower().rjust(64, "0")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": PUSD, "data": "0x70a08231" + pad}, "latest"]}).encode()
    for rpc in RPCS:
        try:
            r = json.loads(http(rpc, body, {"Content-Type": "application/json"}))
            return int(r["result"], 16) / 1e6, rpc
        except Exception:
            continue
    return None, None

def main():
    argf = sys.argv[sys.argv.index("--funder") + 1] if "--funder" in sys.argv else None
    env = trader.load_env()
    funder = env.get("POLY_FUNDER") or argf
    ok = True
    print("=" * 72); print("S1 PREFLIGHT (read-only, no orders)  " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    print("=" * 72)

    # ---- Stage A --------------------------------------------------------------
    print("\n[A1] host country")
    try:
        cc = http("https://ipinfo.io/country").strip()
        print(f"     {cc}  " + ("OK (non-US)" if cc != "US" else "FAIL - US IP is geoblocked for orders"))
        ok &= cc != "US"
    except Exception as e:
        print(f"     unknown ({e}) - WARN");

    print("\n[A2] on-chain pUSD at funder (proxy)")
    if not funder:
        print("     no POLY_FUNDER in .env and no --funder given - cannot check"); ok = False; bal = None
    else:
        bal, rpc = onchain_pusd(funder)
        if bal is None:
            print("     all RPCs failed - WARN");
        else:
            print(f"     funder {funder[:6]}...{funder[-4:]}  pUSD = {bal:,.6f}   (via {rpc})")
            if bal <= 0: print("     FAIL - no collateral at the funder"); ok = False

    print("\n[A3] trader gate status (from the trader's own files)")
    arb = trader.arbiter_count()
    s = trader.t.stats
    wr = s["wins"] / s["n"] if s["n"] else 0.0
    print(f"     arbiter      {arb}/{trader.ARB_MIN}   {'OK' if arb >= trader.ARB_MIN else 'NOT MET'}")
    print(f"     dry signals  n={s['n']}/{trader.SIG_MIN}  wr={wr:.0%} (need >= {trader.SIG_WR:.0%})   "
          f"{'OK' if s['n'] >= trader.SIG_MIN and wr >= trader.SIG_WR else 'NOT MET'}")
    print(f"     KILL file    {'PRESENT - live blocked' if os.path.exists(trader.KILL) else 'absent'}")
    print(f"     .env         {'present' if os.path.exists(trader.ENV_PATH) else 'absent -> trader is DRY'}"
          + (f"  keys set: {sorted(k for k in env if env[k])}" if env else ""))
    print(f"     LIVE_TRADING={env.get('LIVE_TRADING','0')}  FORCE_LIVE={env.get('FORCE_LIVE','0')}  "
          f"POLY_SIGNATURE_TYPE={env.get('POLY_SIGNATURE_TYPE','3 (default)')}")
    try:
        import py_clob_client_v2; print("     py_clob_client_v2  importable OK")
    except Exception as e:
        print(f"     py_clob_client_v2  MISSING ({e}) - FAIL"); ok = False

    # ---- Stage B --------------------------------------------------------------
    print("\n[B] authenticated check through trader.client_for()  (needs .env)")
    if not (env.get("POLY_PRIVATE_KEY") and env.get("POLY_FUNDER")):
        print("     skipped - .env has no POLY_PRIVATE_KEY + POLY_FUNDER yet (create it yourself on the VM)")
    else:
        try:
            from eth_account import Account
            signer = Account.from_key(env["POLY_PRIVATE_KEY"]).address
            print(f"     signer EOA  {signer[:6]}...{signer[-4:]}   funder {funder[:6]}...{funder[-4:]}   "
                  f"sig_type={env.get('POLY_SIGNATURE_TYPE','3')}")
            if signer.lower() == funder.lower():
                print("     WARN signer == funder: that is a bare EOA, not a proxy - check POLY_FUNDER")
            c = trader.client_for(env)
            keys = c.get_api_keys()
            n = len(keys.get("apiKeys", [])) if isinstance(keys, dict) else len(keys or [])
            print(f"     API creds accepted  ({n} key(s) registered for this signer)")
            from py_clob_client_v2 import BalanceAllowanceParams, AssetType
            p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            raw = c.get_balance_allowance(params=p)
            clob = float(raw.get("balance", 0) if isinstance(raw, dict) else raw) / 1e6
            allow = raw.get("allowance") if isinstance(raw, dict) else None
            print(f"     CLOB ledger sees  pUSD balance = {clob:,.6f}   allowance = {allow}")
            if bal is not None:
                diff = abs(clob - bal)
                print(f"     on-chain vs CLOB  {bal:,.6f} vs {clob:,.6f}  -> {'MATCH' if diff < 0.01 else f'DIFF {diff:.4f}'}")
            if clob <= 0:
                print("     FAIL - the client sees NO money: wrong funder / signature type"); ok = False
        except Exception as e:
            print(f"     FAIL - {e!r}"); ok = False

    gates_ok = (arb >= trader.ARB_MIN and s["n"] >= trader.SIG_MIN and wr >= trader.SIG_WR
                and not os.path.exists(trader.KILL))
    print("\n" + ("PLUMBING PASS" if ok else "PLUMBING FAIL")
          + "  |  " + ("STRATEGY GATES MET" if gates_ok else "STRATEGY GATES NOT MET -> trader stays DRY")
          + "  |  NO ORDER WAS PLACED")
    print("     (this script proves the trader can see its money; it never decides go-live -")
    print("      the trader's own gates do that, in code, and only with LIVE_TRADING=1 in .env)")
    print("=" * 72)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
