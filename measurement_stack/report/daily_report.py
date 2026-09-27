#!/usr/bin/env python3
"""
daily_report.py — ONE integrated daily report for the measurement stack.

Runs every sub-report (backtest, US proxy validation, mispricing monitors), reads the weather
and regime-watch outputs, writes reports/DAILY-REPORT-YYYY-MM-DD.md in living-record style
and sends ONE short Telegram summary. No orders, no keys.

  python3 daily_report.py            # full run + Telegram
  python3 daily_report.py --no-send  # write the file only
  python3 daily_report.py --no-run   # do not re-run sub-reports, just collect
"""
import argparse, csv, glob, gzip, json, os, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(STACK, "common"))
import labcommon
REPORTS = os.path.join(STACK, "reports"); os.makedirs(REPORTS, exist_ok=True)
P = lambda *a: os.path.join(STACK, *a)

def run(cmd, cwd, timeout=3600):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "")[-3000:], (r.stderr or "")[-1500:]
    except Exception as e:
        return 99, "", repr(e)
def jload(p, d=None):
    try: return json.load(open(p))
    except Exception: return d
def rows(p):
    try:
        with (gzip.open(p, "rt") if p.endswith(".gz") else open(p, newline="", encoding="utf-8")) as f: return list(csv.DictReader(f))
    except Exception: return []
def fl(x):
    try: return float(x)
    except Exception: return None
def svc(name):
    try: return subprocess.run(["systemctl", "is-active", name + ".service"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception: return "?"
def tbl(R, cols, heads=None):
    if not R: return "_(no rows yet)_\n"
    h = heads or cols
    return "| " + " | ".join(h) + " |\n|" + "|".join("---" for _ in h) + "|\n" + "".join(
        "| " + " | ".join(str("" if r.get(c) is None else r.get(c)) for c in cols) + " |\n" for r in R)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--no-send", action="store_true"); ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args()
    now = time.time(); today = datetime.now(timezone.utc).strftime("%Y-%m-%d"); since = now - 86400
    errs = []
    if not a.no_run:
        for name, cmd, cwd in (("backtest", ["python3", "backtest7d.py", "--days", "7"], P("backtest7d")),
                               ("validation", ["python3", "validation_report.py"], P("us_measure")),
                               ("monitors", ["python3", "mispricing.py", "--days", "1", "--bme-days", "2"], P("monitors"))):
            rc, out, err = run(cmd, cwd)
            if rc: errs.append(f"{name} failed (rc {rc}): {err.strip().splitlines()[-1] if err.strip() else 'no stderr'}"[:200])

    # ---------------- collect
    bt_latest = jload(P("backtest7d", "out", "latest.json"), {})
    bt = jload(os.path.join(bt_latest.get("out", ""), "summary.json"), {}) if bt_latest else {}
    val = jload(P("us_measure", "us_rounds", "validation_summary.json"), {})
    mon = jload(P("monitors", "out", "summary.json"), {})
    hb = {n: labcommon.read_heartbeat(n) for n in ("us_recorder", "nws_watch", "regime_watch", "stack_health")}
    sums = [r for r in rows(P("us_measure", "weather", "bucket_sums.csv")) if (fl(r["ts_unix"]) or 0) >= since]
    fcs = [r for r in rows(P("us_measure", "weather", "nws_forecasts.csv")) if (fl(r["detected_unix"]) or 0) >= since]
    lags = rows(P("us_measure", "weather", "lag_table.csv"))
    lags24 = [r for r in lags if r.get("detected_utc", "") >= datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
    changes = []
    try:
        for ln in open(P("regime_watch", "changes.jsonl")):
            j = json.loads(ln)
            if j["ts"] >= since: changes.append(j)
    except Exception: pass
    alerts_sent = 0
    try:
        for ln in open(P("state", "alerts.log"), encoding="utf-8"):
            if " SENT[" in ln and ln[1:21] >= datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"): alerts_sent += 1
    except Exception: pass
    us_rounds = [r for r in rows(P("us_measure", "us_rounds", "rounds.csv")) if (fl(r["end_unix"]) or 0) >= since]
    us_stat = Counter(r["match"] for r in us_rounds)

    wx = defaultdict(list)
    for r in sums:
        if r["market_day"] <= today: wx[(r["city"], r["market_day"])].append(r)
    wx_rows = []
    for (city, d), R in sorted(wx.items()):
        asks = [fl(r["ask_sum"]) for r in R if fl(r["ask_sum"]) is not None and r["n_with_ask"] == r["n_buckets"]]
        if not asks: continue
        wx_rows.append(dict(city=city.upper(), market_day=d, scans=len(R), ask_sum_min=min(asks), ask_sum_median=sorted(asks)[len(asks) // 2],
                            ask_sum_max=max(asks), buy_arb_scans=sum(r["verdict"] == "BUY-ARB" for r in R),
                            overround_pct_median=round(100 * (sorted(asks)[len(asks) // 2] - 1), 1)))
    wx_today = [r for r in wx_rows if r["market_day"] == today] or wx_rows[-5:]

    # ---------------- markdown
    services = {u: svc(u) for u in ("ms-us-recorder", "ms-nws-watch", "ms-regime-watch")}
    L = [f"# Measurement stack — daily report {today}\n",
         f"Generated {labcommon.iso()} on the research box. Read-only research: no keys, no orders, public data only.\n",
         "## 1. Health\n",
         tbl([dict(k=u, v=s, h=(f"heartbeat {max(0, int(time.time() - (hb.get(n) or {}).get('ts', 0)))} s ago" if hb.get(n) else "no heartbeat"))
              for (u, s), n in zip(services.items(), ("us_recorder", "nws_watch", "regime_watch"))], ["k", "v", "h"], ["service", "state", "liveness"]),
         f"\nDisk free {labcommon.disk_free_gb():.0f} GB. Telegram alerts sent in the last 24 h: {alerts_sent}. "
         + ("Sub-report errors: " + "; ".join(errs) if errs else "All sub-reports ran."), ""]
    L += ["## 2. 7-day backtest: chain labels vs public labels\n"]
    if bt:
        c = bt.get("coverage", {})
        L += [f"Window: {bt.get('tag')}. Rounds {c.get('rounds')}, chain-verified {c.get('chain')}, public labels that disagree with the chain on "
              f"{bt.get('public_disagreements')} rounds, our signals mis-scored by a public label: {bt.get('signals_mislabeled')}.\n",
              tbl(bt.get("summary", []), ["strategy", "label_source", "trades_taken", "wins", "win_rate_pct", "breakeven_win_rate_pct", "net_usd_fee_adjusted",
                                          "days_stopped_by_20usd_rule"]),
              f"\nFull report and CSVs: `backtest7d/out/{bt.get('tag')}/`.\n"]
    else: L += ["_(no backtest output yet)_\n"]
    L += ["## 3. US rounds and the BRTI proxy\n",
          f"Rounds closed in the last 24 h: {len(us_rounds)} ({', '.join(f'{k} {v}' for k, v in us_stat.items()) or 'none'}).\n"]
    if val:
        L += [f"Validation so far: **{val.get('comparisons')} of {val.get('target')}** comparisons, proxy agrees on {val.get('agree')} "
              f"({val.get('agree_pct')} %); clear rounds (gap at least 2 bps) {val.get('clear_rounds_agree')} of {val.get('clear_rounds_2bp')}. "
              f"Settlement over the websocket after median {val.get('settlement_delay_median_s')} s. Two-witness check: {val.get('two_witness_checked')} rounds, "
              f"{val.get('two_witness_disagree')} disagree. Verdict: **{val.get('verdict')}**.\n"]
    L += ["## 4. Mispricing monitors\n"]
    if mon:
        L += ["One-sided book in the final minute (share of seconds with a missing bid or ask):\n",
              tbl([dict(k=k, v=v, n=(mon.get("one_sided_seconds_observed") or {}).get(k)) for k, v in (mon.get("one_sided_last_minute") or {}).items()],
                  ["k", "v", "n"], ["venue and horizon", "one-sided % in last minute", "seconds observed (all minutes)"]),
              f"\nMarket vs oracle (US): {mon.get('divergence_rounds')} rounds, median RMS divergence {mon.get('divergence_median_rms')}. "
              + (f"Largest: {mon['divergence_worst']['round_slug']} at {mon['divergence_worst']['worst_at_secs_to_end']} s to the end, market "
                 f"{mon['divergence_worst']['worst_market_px']} vs model {mon['divergence_worst']['worst_model_p']}." if mon.get("divergence_worst") else ""),
              f"\nCross-venue 15m (.com vs US, same window): {mon.get('cross_venue_rounds')} rounds, mean price gap {mon.get('cross_venue_mean_gap')}; "
              f"outcomes compared on {mon.get('cross_venue_resolved_compared')}, resolved differently on {mon.get('cross_venue_resolved_differently')}.\n"]
    L += ["## 5. Weather markets\n", f"NWS forecast updates detected in the last 24 h: {len(fcs)} "
          f"(high changed on {sum(r.get('high_changed') == '1' for r in fcs)}). Reaction-lag rows: {len(lags24)} (all time {len(lags)}).\n",
          "Executable ask-sums per bucket set (1.00 = fair, below 0.985 = buying every bucket would be riskless before fees):\n",
          tbl(wx_today, ["city", "market_day", "scans", "ask_sum_min", "ask_sum_median", "ask_sum_max", "overround_pct_median", "buy_arb_scans"]), ""]
    if lags24:
        L += ["Reaction to forecast updates where the high changed:\n",
              tbl([r for r in lags24 if r.get("high_delta_f") not in ("", "0", None)][-10:],
                  ["city", "nws_update_time", "detection_delay_s", "previous_high_f", "new_high_f", "first_quote_change_after_update_s", "max_abs_px_move_300s"]), ""]
    L += ["## 6. Rule changes (regime watch)\n"]
    if changes:
        L += [tbl([dict(utc=c["utc"], source=c["source"], critical="yes" if c["critical"] else "no", summary=c["summary"].replace("\n", " ")[:140]) for c in changes[-20:]],
                  ["utc", "source", "critical", "summary"])]
    else:
        rw = hb.get("regime_watch") or {}
        L += [f"No rule change in the last 24 h. Sources healthy: {rw.get('sources_ok')} of {rw.get('sources_total')}"
              + (f", blind: {', '.join(rw['blind'])}" if rw.get("blind") else "") + ".\n"]
    L += ["\n## 7. Standing constraints\n",
          "Measure, never attack. No authenticated surface, no promo or bonus interaction, no endpoint hammering. "
          "No real orders until the user re-approves after seeing backtest and validation results. S1 stays hard-blocked.\n"]
    path = os.path.join(REPORTS, f"DAILY-REPORT-{today}.md")
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(dict(path=path, generated=int(now)), open(os.path.join(REPORTS, "latest.json"), "w"))

    # ---------------- telegram (one short message)
    chain = next((s for s in bt.get("summary", []) if s["label_source"].startswith("(a)") and "trader" in s["strategy"]), None)
    pub = next((s for s in bt.get("summary", []) if s["label_source"].startswith("(b1)") and "trader" in s["strategy"]), None)
    T = [f"📋 Daily report {today}",
         "Services: " + ", ".join(f"{u.replace('ms-', '')} {'✅' if s == 'active' else '❌ ' + s}" for u, s in services.items())]
    if chain:
        T.append(f"S1 (DRY) last 7 days on chain labels: {chain['trades_taken']} trades, {chain['win_rate_pct']}% wins "
                 f"(needs {chain['breakeven_win_rate_pct']}% to break even), net ${chain['net_usd_fee_adjusted']} after fees."
                 + (f" On public labels it would show ${pub['net_usd_fee_adjusted']}." if pub and pub.get("trades_taken") else ""))
    if bt: T.append(f"Public labels wrong vs chain on {bt.get('public_disagreements')} of {bt.get('coverage', {}).get('chain')} rounds.")
    if val: T.append(f"US proxy validation: {val.get('comparisons')}/{val.get('target')} rounds, agrees {val.get('agree_pct')}%"
                     f" ({val.get('clear_rounds_agree')}/{val.get('clear_rounds_2bp')} on clear rounds).")
    if wx_today:
        lo = min(wx_today, key=lambda r: r["ask_sum_min"])
        T.append(f"Weather: {len(fcs)} forecast updates; lowest bucket-set cost {lo['ask_sum_min']:.2f} ({lo['city']}), so no buy-arb"
                 if lo["ask_sum_min"] >= 0.985 else f"Weather: bucket set {lo['city']} touched {lo['ask_sum_min']:.3f} (< 0.985) — see bucket_sums.csv")
    T.append(f"Rule changes: {len(changes)} ({sum(c['critical'] for c in changes)} critical). Alerts sent: {alerts_sent}.")
    if errs: T.append("⚠ " + "; ".join(errs)[:300])
    T.append(f"Full report: reports/DAILY-REPORT-{today}.md")
    msg = "\n".join(T)
    if not a.no_send: labcommon.alert(f"daily:{today}", msg, "report", "daily_report")
    print(msg); print("\nwritten:", path)

if __name__ == "__main__":
    main()
