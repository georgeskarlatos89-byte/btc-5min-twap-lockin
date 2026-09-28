#!/usr/bin/env python3
"""
dashboard.py - a VIEWER for the two paper traders. It only reads the files they write.
It cannot place a bet, change a setting or restart anything.

Listens on 127.0.0.1 only. Nothing is reachable from the internet. Open it from the PC through
the SSH tunnel:  ssh -N -L 8899:127.0.0.1:8787 twapvm   then   http://localhost:8899
(8899 on the PC because another program there already uses 8787)

  /            the page
  /api/state   everything the page shows, as JSON
  /shot.jpg    the latest picture of the Polymarket page the BTC trader is reading
"""
import csv, json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRADES = ROOT if os.path.basename(ROOT) == "UI MY TRADES" else os.path.join(ROOT, "UI MY TRADES")
BTC, WX, STATE = os.path.join(TRADES, "btc"), os.path.join(TRADES, "weather"), os.path.join(ROOT, "state")
HOST, PORT = "127.0.0.1", 8787
_cache = {}

def cached(key, ttl, fn):
    t = time.time(); c = _cache.get(key)
    if c and t - c[0] < ttl:
        return c[1]
    try:
        v = fn()
    except Exception as e:
        v = c[1] if c else None
    _cache[key] = (t, v)
    return v

def rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def num(x):
    try:
        return float(x)
    except Exception:
        return None

def services():
    out = {}
    for u in ("ui-btc-trader", "ui-weather-trader", "ui-dashboard"):
        try:
            r = subprocess.run(["systemctl", "show", u, "-p", "ActiveState", "-p", "NRestarts", "-p", "MemoryCurrent",
                                "-p", "ActiveEnterTimestamp"], capture_output=True, text=True, timeout=5).stdout
            d = dict(l.split("=", 1) for l in r.strip().splitlines() if "=" in l)
            mem = num(d.get("MemoryCurrent"))
            out[u] = {"state": d.get("ActiveState"), "restarts": d.get("NRestarts"),
                      "memory_mb": round(mem / 2**20) if mem and mem < 1e13 else None, "since": d.get("ActiveEnterTimestamp")}
        except Exception:
            out[u] = {"state": "unknown"}
    try:
        la = open("/proc/loadavg").read().split()[:3]
        mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable")}
        out["_machine"] = {"load": la, "cpus": os.cpu_count(), "mem_total_gb": round(mi["MemTotal"] / 2**20, 1),
                           "mem_free_gb": round(mi["MemAvailable"] / 2**20, 1)}
    except Exception:
        pass
    return out

def btc_block():
    A = rows(os.path.join(BTC, "btc_trades_all.csv"))
    st = jload(os.path.join(STATE, "btc_state.json"), {}) or {}
    opens = [o for _, o in sorted((st.get("open") or {}).items())]
    S = [r for r in A if r.get("status") == "SETTLED"]
    wins = sum(1 for r in S if r.get("win") == "1")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    Sd = [r for r in S if r["round_start_utc"][:10] == today]
    series = [{"t": r["settled_utc"] or r["round_end_utc"], "v": num(r["bankroll_after"])} for r in S if num(r.get("bankroll_after")) is not None][-1500:]
    pnl = [{"t": r["round_start_utc"], "v": num(r["pnl_usd"]), "choice": r["my_choice"], "px": num(r["entry_price_exec"]),
            "bet": num(r["bet_usd"]), "phase": r["phase"]} for r in S][-80:]
    keep = ("round_start_utc", "status", "phase", "entry_slot_s_remaining", "decision_ui_countdown_s", "ui_clock_drift_s", "gap_bps",
            "my_choice", "market_favourite_side", "entry_price_exec", "entry_price_ui", "bet_usd", "outcome_official", "outcome_ui",
            "win", "pnl_usd", "bankroll_after", "data_source", "choice_reason", "price_to_beat_ui", "ui_current_price")
    recent = [{k: r.get(k) for k in keep} for r in (A[-40:] + [{k: o.get(k) for k in keep} for o in opens])]
    recent.sort(key=lambda r: r["round_start_utc"], reverse=True)
    sn = rows(os.path.join(BTC, "btc_round_snapshots.csv"))[-2200:]      # about the last 200 rounds
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 6 * 3600))
    sn6 = [r for r in sn if r["snap_utc"] >= cut]
    drift = sorted(abs(num(r["ui_clock_drift_s"])) for r in sn6 if num(r.get("ui_clock_drift_s")) is not None)
    fav = [r for r in S if r.get("market_favourite_side")]
    f1 = [r for r in fav if r["my_choice"].capitalize() == r["market_favourite_side"]]
    f2 = [r for r in fav if r["my_choice"].capitalize() != r["market_favourite_side"]]
    grp = lambda L: {"bets": len(L), "won": sum(1 for r in L if r["win"] == "1"), "pnl": round(sum(num(r["pnl_usd"]) or 0 for r in L), 2)}
    T = rows(os.path.join(BTC, "btc_pattern_table.csv"))
    for r in T:
        r["best_edge"] = max([x for x in (num(r.get("edge_buy_leader")), num(r.get("edge_buy_underdog"))) if x is not None], default=None)
    Tk = sorted([r for r in T if (num(r["observations"]) or 0) >= 30], key=lambda r: -(r["best_edge"] if r["best_edge"] is not None else -9))
    eq = (st.get("cash") or 0) + sum(o.get("bet_usd") or 0 for o in opens)
    return {"bankroll": round(eq, 2), "start_bankroll": st.get("start_bankroll", 200), "settled": len(S), "won": wins, "lost": len(S) - wins,
            "pnl": round(sum(num(r["pnl_usd"]) or 0 for r in S), 2), "staked": round(sum(num(r["bet_usd"]) or 0 for r in S), 2),
            "today": {"settled": len(Sd), "won": sum(1 for r in Sd if r["win"] == "1"), "pnl": round(sum(num(r["pnl_usd"]) or 0 for r in Sd), 2),
                      "rounds_so_far": (int(time.time()) % 86400) // 300,
                      "recorded": sum(1 for r in A if r["round_start_utc"][:10] == today) + sum(1 for o in opens if o["round_start_utc"][:10] == today),
                      "missed": sum(1 for r in A if r["round_start_utc"][:10] == today and r["status"] == "MISSED")},
            "rounds_recorded": len(A) + len(opens), "missed": sum(1 for r in A if r["status"] == "MISSED"),
            "no_bet_possible": sum(1 for r in A if r["status"] == "NO_LIQUIDITY"), "open": len(opens),
            "phase": "LEARN" if (st.get("settled_count") or 0) < 150 else "ADAPT", "settled_count": st.get("settled_count"),
            "loss_streak": st.get("loss_streak"),
            "page_health_6h": {"checkpoints": len(sn6), "from_page": sum(1 for r in sn6 if r["data_source"] == "ui"),
                               "behind_median_s": drift[len(drift) // 2] if drift else None,
                               "behind_p95_s": drift[int(len(drift) * .95)] if drift else None},
            "page_vs_official": {"agree": sum(1 for r in A if r.get("outcomes_agree") == "1"), "disagree": sum(1 for r in A if r.get("outcomes_agree") == "0")},
            "favourite_split": {"bought_favourite": grp(f1), "bought_other_side": grp(f2)},
            "bankroll_series": series, "pnl_bars": pnl, "recent": recent,
            "patterns": {"cells": len(T), "cells_30plus": len(Tk), "top": Tk[:12]}}

def weather_block():
    W = rows(os.path.join(WX, "weather_trades_all.csv"))
    st = jload(os.path.join(STATE, "weather_state.json"), {}) or {}
    opens = [o for _, o in sorted((st.get("open") or {}).items())]
    S = [r for r in W if r.get("status") == "SETTLED"]
    keep = ("bet_id", "placed_utc", "city", "event_date", "bracket_label", "my_choice", "entry_price_exec", "entry_price_ui", "bet_usd",
            "status", "outcome_official", "winning_bracket", "win", "pnl_usd", "bankroll_after", "data_source", "ui_url")
    allb = [{k: r.get(k) for k in keep} for r in S] + [{k: o.get(k) for k in keep} for o in opens]
    allb.sort(key=lambda r: r["placed_utc"], reverse=True)
    nxt = sorted(t for e in (st.get("events") or {}).values() for t in e.get("plan", []))
    eq = (st.get("cash") or 0) + sum(o.get("bet_usd") or 0 for o in opens)
    by = {}
    for r in allb:
        c = by.setdefault(r["city"], {"bets": 0, "settled": 0, "won": 0, "pnl": 0.0, "open": 0})
        c["bets"] += 1
        if r["status"] == "SETTLED":
            c["settled"] += 1; c["won"] += int(r["win"] in ("1", 1)); c["pnl"] = round(c["pnl"] + (num(r["pnl_usd"]) or 0), 2)
        else:
            c["open"] += 1
    return {"bankroll": round(eq, 2), "start_bankroll": st.get("start_bankroll", 200), "settled": len(S),
            "won": sum(1 for r in S if r["win"] == "1"), "lost": sum(1 for r in S if r["win"] == "0"),
            "pnl": round(sum(num(r["pnl_usd"]) or 0 for r in S), 2), "open": len(opens), "planned": len(nxt),
            "next_bet_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(nxt[0])) if nxt else None,
            "no_open_bracket": sum(1 for r in W if r["status"] == "NO_OPEN_BRACKET"), "by_city": by, "bets": allb[:60]}

def state():
    live = jload(os.path.join(STATE, "btc_live.json"), None)
    shot = os.path.join(STATE, "btc_page.jpg")
    return {"server_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "server_unix": round(time.time(), 2),
            "live": live, "live_age_s": (round(time.time() - live["written_unix"], 1) if live else None),
            "shot_age_s": (round(time.time() - os.path.getmtime(shot), 1) if os.path.exists(shot) else None),
            "btc": cached("btc", 4, btc_block), "weather": cached("wx", 15, weather_block), "services": cached("svc", 10, services)}


class H(BaseHTTPRequestHandler):
    server_version = "ui-dashboard/1.0"
    def log_message(self, *a):
        pass
    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send(200, json.dumps(state()).encode(), "application/json")
            if path == "/shot.jpg":
                p = os.path.join(STATE, "btc_page.jpg")
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        return self.send(200, f.read(), "image/jpeg")
                return self.send(404, b"no picture yet", "text/plain")
            return self.send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.send(500, ("error: " + e.__class__.__name__).encode(), "text/plain")
            except Exception:
                pass
    def do_POST(self):
        self.send(405, b"this is a viewer: nothing can be changed from here", "text/plain")
    do_PUT = do_DELETE = do_PATCH = do_POST


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        s = state(); json.dumps(s)
        print("SELFTEST OK - keys:", sorted(s.keys()), "| btc settled", s["btc"]["settled"], "| weather bets", len(s["weather"]["bets"]))
        sys.exit(0)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"dashboard listening on http://{HOST}:{PORT} (local only; open it through the SSH tunnel)", flush=True)
    srv.serve_forever()
