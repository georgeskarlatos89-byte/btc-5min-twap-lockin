#!/usr/bin/env python3
"""evidence.py - the Scout stage (01:00 UTC): runs every scout source and writes the nightly evidence file.

  python3 evidence.py [--date YYYYMMDD] [--dry-run] [--skip wallets,moondev,...]

Sources, each in its own try/except (a failed source never stops the night):
  gamma_rounds (5m, 15m)  prices_archive  moondev_pull  hl_pull  wallet_scan  macro
Then: rounds_yesterday from the live token maps, price1m counts, features day split (fit <= 20261004,
frozen holdout 20261005..20261014, forward = recorder days after), regime from the last 7 feature days
(spot_vol_30s median, quiet/active hours by trade_n_10s; subsampled every 10th second), paper_bot from
data/bot/strategy_state.json, anomalies. Every non-count number carries a label MEASURED or ESTIMATE.
Writes data/evidence/evidence_YYYYMMDD.json (INTERFACES §4), validates it with common.schemas when importable,
calls qdesk.common.tg.heartbeat("scout", ok, note). Exit 0 even when sources fail; exit 2 only if the evidence
file cannot be written. --dry-run: no network (sources report ok=false note "dry"), LAB_NO_TELEGRAM=1.
"""
import argparse
import csv
import glob
import gzip
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from qdesk.scout import sc_util as U

FIT_LAST = "20261004"          # BME fit days end (plan: frozen BME test slice 10-05..10-14)
HOLDOUT = ("20261005", "20261014")
PRIOR_EVIDENCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prior_evidence.json")


def src(name, ok, rows, note):
    return {"name": name, "ok": bool(ok), "rows": int(rows or 0), "note": str(note or "")[:300]}


def feature_days():
    days = sorted(os.path.basename(p)[4:12] for p in glob.glob(os.path.join(U.data_dir(), "features", "day_*.csv.gz")))
    return [d for d in days if d.isdigit() and len(d) == 8]


def regime_from_features(days, log, max_days=7):
    """Median spot_vol_30s (bps) and quiet/active UTC hours from the newest feature days (every 10th second, Up side)."""
    vols = []; hours = {h: [] for h in range(24)}; nrows = 0
    for d in days[-max_days:]:
        p = os.path.join(U.data_dir(), "features", f"day_{d}.csv.gz")
        try:
            with gzip.open(p, "rt", newline="") as f:
                rd = csv.reader(f); hdr = next(rd)
                ix = {k: hdr.index(k) for k in ("side", "t_el", "spot_vol_30s", "trade_n_10s", "session_hour") if k in hdr}
                if len(ix) < 5:
                    log(f"regime: {d} lacks columns {sorted(set(['side','t_el','spot_vol_30s','trade_n_10s','session_hour']) - set(ix))}"); continue
                for r in rd:
                    try:
                        if r[ix["side"]] != "Up" or int(float(r[ix["t_el"]])) % 10 != 0:
                            continue
                        nrows += 1
                        v = r[ix["spot_vol_30s"]]
                        if v != "":
                            vols.append(float(v))
                        n = r[ix["trade_n_10s"]]
                        if n != "":
                            hours[int(float(r[ix["session_hour"]]))].append(float(n))
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            log(f"regime: cannot read {p}: {type(e).__name__}")
    hour_mean = {h: statistics.mean(v) for h, v in hours.items() if v}
    quiet = []; active = []
    if len(hour_mean) >= 6:
        vals = sorted(hour_mean.values()); lo = vals[len(vals) // 3]; hi = vals[2 * len(vals) // 3]
        quiet = sorted(h for h, m in hour_mean.items() if m <= lo); active = sorted(h for h, m in hour_mean.items() if m >= hi)
    return {"spot_vol_30s_median_bps": round(statistics.median(vols), 4) if vols else None,
            "hours_quiet": quiet, "hours_active": active, "days_used": days[-max_days:], "rows_sampled": nrows,
            "trade_n_10s_by_hour": {str(h): round(m, 3) for h, m in sorted(hour_mean.items())},
            "label": "MEASURED" if vols else "NA"}


def paper_bot_status():
    st = U.load_json(U.data_path("bot", "strategy_state.json", mkdir=False))
    if not isinstance(st, dict):
        return {"active_rule": None, "trades_7d": 0, "avg_R": None, "backtest_avg_R": None, "status": "NONE"}
    return {"active_rule": st.get("rule_id"), "trades_7d": int(st.get("trades") or 0), "avg_R": st.get("avg_R"),
            "backtest_avg_R": st.get("backtest_avg_R"), "status": str(st.get("status") or "UNKNOWN"), "since": st.get("since"),
            "label": "MEASURED"}


def recorder_health(anomalies):
    hb = U.load_json(U.data_path("state", "hb_recorder.json", mkdir=False))
    if not isinstance(hb, dict):
        anomalies.append("recorder: no heartbeat file (data/state/hb_recorder.json)"); return None
    age = time.time() - float(hb.get("ts") or 0)
    if age > 180:
        anomalies.append(f"recorder: heartbeat stale {int(age)} s")
    if not hb.get("ok", True):
        anomalies.append(f"recorder: ok=false ({hb.get('note')})")
    return {"age_s": int(age), "ok": hb.get("ok"), "msgs_per_s": hb.get("msgs_per_s"), "reconnects": hb.get("reconnects")}


def build(date, dry_run, skip, log):
    skip = set(skip or [])
    sources = []; anomalies = []
    ev = {"date": date, "generated": U.iso(), "sources": sources, "dry_run": bool(dry_run)}

    # 1. gamma rounds -> live token maps
    rounds = {"5m": {"n": 0, "up_rate": None, "chain_verified": 0, "gamma_disagree": 0},
              "15m": {"n": 0, "up_rate": None, "chain_verified": 0, "gamma_disagree": 0}}
    try:
        from qdesk.scout import gamma_rounds as GR
        r = GR.run(date, dry_run, log)
        for mk in ("5m", "15m"):
            if mk in r:
                rounds[mk] = r[mk]
                sources.append(src(f"gamma_rounds_{mk}", (not dry_run) and r[mk]["n"] > 0, r[mk]["n"], "dry" if dry_run else ("ok" if r[mk]["n"] else "no rounds")))
                if not dry_run and r[mk].get("missing", 0) > 5:
                    anomalies.append(f"gamma_rounds {mk}: {r[mk]['missing']} rounds missing on Gamma")
                if not dry_run and r[mk].get("gamma_disagree", 0) > 0:
                    anomalies.append(f"gamma_rounds {mk}: {r[mk]['gamma_disagree']} gamma/chain disagreements")
    except Exception as e:
        sources.append(src("gamma_rounds", False, 0, f"{type(e).__name__}: {str(e)[:120]}")); anomalies.append(f"gamma_rounds crashed: {type(e).__name__}")
    ev["rounds_yesterday"] = rounds

    # 2. 1-minute price archive
    p1 = {"days_archived": 0, "rounds_with_history": 0}
    try:
        from qdesk.scout import prices_archive as PA
        ok, s, note = PA.run(date, dry_run, log=log)
        p1 = {"days_archived": s["days_archived"], "rounds_with_history": s["rounds_with_history"], "assets_new": s.get("assets_new", 0)}
        sources.append(src("prices_archive", ok, s["rounds_with_history"], note))
    except Exception as e:
        sources.append(src("prices_archive", False, 0, f"{type(e).__name__}: {str(e)[:120]}"))
    ev["price1m"] = p1

    # 3. Moon Dev
    md = {"label": "MEASURED", "pulled": {}, "liq_long_5m_usd": None, "liq_short_5m_usd": None}
    if "moondev" not in skip:
        try:
            from qdesk.scout import moondev_pull as MD
            res = MD.pull_all(date, dry_run, log=log)
            for name, (ok, rows, note) in res.items():
                sources.append(src(f"moondev_{name}", ok, rows, note)); md["pulled"][name] = ok
            if not dry_run:
                tot = MD.load_raw("totals", date)
                lo, sh = MD.liq_totals_5m(tot) if tot else (None, None)
                md["liq_long_5m_usd"], md["liq_short_5m_usd"] = lo, sh
                if tot and lo is None:
                    anomalies.append("moondev totals.json: 5m long/short fields not recognised (see raw archive)")
                if not any(md["pulled"].values()):
                    anomalies.append("moondev: every endpoint failed (" + "; ".join(f"{k}:{v[2]}" for k, v in res.items())[:200] + ")")
        except Exception as e:
            sources.append(src("moondev", False, 0, f"{type(e).__name__}: {str(e)[:120]}"))
    ev["moondev"] = md

    # 4. Hyperliquid
    hl = {"funding_1h": None, "oi_usd": None, "mark": None, "label": "MEASURED"}
    try:
        from qdesk.scout import hl_pull as HL
        ok, s, note = HL.run(date, dry_run, log=log)
        if s:
            hl.update({k: s.get(k) for k in ("funding_1h", "oi_usd", "mark") if k in s}); hl["rows"] = s.get("rows", 0)
        sources.append(src("hyperliquid", ok, (s or {}).get("rows", 0), note))
    except Exception as e:
        sources.append(src("hyperliquid", False, 0, f"{type(e).__name__}: {str(e)[:120]}"))
    ev["hyperliquid"] = hl

    # 5. wallets
    wl = {"scanned": 0, "top": [], "label": "MEASURED"}
    if "wallets" not in skip:
        try:
            from qdesk.scout import wallet_scan as WS
            ok, doc, note = WS.run(date, dry_run, log=log)
            wl["scanned"] = doc.get("scanned", 0); wl["budget"] = doc.get("budget", {})
            wl["top"] = [{k: w.get(k) for k in ("wallet", "pnl_7d", "btc5m_share", "maker_share", "rounds_per_day", "class")} for w in doc.get("wallets", [])[:10]]
            wl["classes"] = {}
            for w in doc.get("wallets", []):
                wl["classes"][w["class"]] = wl["classes"].get(w["class"], 0) + 1
            sources.append(src("wallet_scan", ok, doc.get("scanned", 0), note))
        except Exception as e:
            sources.append(src("wallet_scan", False, 0, f"{type(e).__name__}: {str(e)[:120]}"))
    ev["wallets"] = wl

    # 6. features + regime + macro
    days = feature_days()
    ft = {"days_available": len(days), "newest": days[-1] if days else None,
          "fit_days": [d for d in days if d <= FIT_LAST],
          "frozen_holdout_days": [d for d in days if HOLDOUT[0] <= d <= HOLDOUT[1]],
          "forward_days": [d for d in days if d > HOLDOUT[1]]}
    ev["features"] = ft
    if not days:
        anomalies.append("features: no day_*.csv.gz files (run bme_to_features / the recorder)")
    rg = {"spot_vol_30s_median_bps": None, "hours_quiet": [], "hours_active": [], "macro_next": None, "label": "NA"}
    try:
        if days:
            rg.update(regime_from_features(days, log))
    except Exception as e:
        anomalies.append(f"regime failed: {type(e).__name__}")
    try:
        from qdesk.scout import macro as MC
        now = time.time()
        rg["macro_next"] = MC.next_event(now)
        rg["macro_blackout_now"] = {"5m": MC.is_blackout(now, "5m"), "15m": MC.is_blackout(now, "15m")}
        end = MC.calendar_end()
        sources.append(src("macro", True, len(MC.HARDCODED_MACRO_EVENTS_2026), f"calendar ends {end}"))
        if end and (U.parse_day(end[:10]) - now) < 14 * 86400:
            anomalies.append(f"macro calendar ends {end}: extend HARDCODED_MACRO_EVENTS_2026")
    except Exception as e:
        sources.append(src("macro", False, 0, f"{type(e).__name__}: {str(e)[:120]}"))
    ev["regime"] = rg

    # 7. paper bot, recorder, disk, prior evidence
    ev["paper_bot"] = paper_bot_status()
    ev["recorder"] = recorder_health(anomalies)
    try:
        from qdesk.common.tg import disk_free_gb
        free = disk_free_gb(U.data_dir()); ev["disk_free_gb"] = round(free, 2)
        if free < 3:
            anomalies.append(f"disk free {free:.1f} GB < 3 GB")
    except Exception:
        pass
    ev["prior_evidence_ref"] = PRIOR_EVIDENCE if os.path.exists(PRIOR_EVIDENCE) else ""
    if not ev["prior_evidence_ref"]:
        anomalies.append("prior_evidence.json missing at the qdesk root")
    for s in sources:
        if not s["ok"] and s["note"] != "dry":
            anomalies.append(f"source {s['name']} failed: {s['note'][:80]}")
    ev["anomalies"] = anomalies
    ev["requests"] = U.REQUESTS["n"]
    return ev


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=None); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip", default="", help="comma list: wallets,moondev")
    a = ap.parse_args()
    date = a.date or U.today()
    if a.dry_run:
        os.environ["LAB_NO_TELEGRAM"] = "1"
    t0 = time.time(); log = U.log
    log(f"scout evidence start date={date} dry_run={a.dry_run} data={U.data_dir()}")
    try:
        ev = build(date, a.dry_run, [s for s in a.skip.split(",") if s], log)
    except Exception as e:                       # should not happen (every source is guarded) - still write a file
        ev = {"date": date, "generated": U.iso(), "sources": [src("build", False, 0, f"{type(e).__name__}: {str(e)[:120]}")],
              "rounds_yesterday": {}, "price1m": {"days_archived": 0, "rounds_with_history": 0},
              "features": {"days_available": 0, "newest": None, "fit_days": [], "frozen_holdout_days": [], "forward_days": []},
              "regime": {"spot_vol_30s_median_bps": None, "hours_quiet": [], "hours_active": []}, "moondev": {"label": "MEASURED"},
              "hyperliquid": {}, "wallets": {"scanned": 0, "top": []}, "paper_bot": paper_bot_status(), "prior_evidence_ref": "",
              "anomalies": [f"build crashed: {type(e).__name__}: {str(e)[:120]}"]}
    ev["runtime_s"] = round(time.time() - t0, 1)
    errs = []
    try:
        from qdesk.common.schemas import validate
        errs = validate("evidence", ev)
        if errs:
            ev["anomalies"].append("schema: " + "; ".join(errs)[:300])
    except ImportError:
        log("common.schemas not importable yet: evidence not validated")
    out = U.data_path("evidence", f"evidence_{date}.json")
    try:
        U.save_json(out, ev)
    except Exception as e:
        log(f"FATAL cannot write {out}: {type(e).__name__}: {e}")
        U.heartbeat("scout", False, f"evidence write failed: {type(e).__name__}")
        sys.exit(2)
    n_ok = sum(1 for s in ev["sources"] if s["ok"]); n_all = len(ev["sources"])
    note = f"sources {n_ok}/{n_all} ok, rounds 5m {ev['rounds_yesterday'].get('5m', {}).get('n')} 15m {ev['rounds_yesterday'].get('15m', {}).get('n')}, anomalies {len(ev['anomalies'])}, {ev['runtime_s']} s"
    U.heartbeat("scout", n_ok > 0 or a.dry_run, note)
    log(f"evidence written {out} ({os.path.getsize(out)} bytes) schema_errors={len(errs)} | {note}")
    sys.exit(0)


if __name__ == "__main__":
    main()
