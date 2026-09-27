#!/usr/bin/env python3
"""
status_pdf.py — turns the hourly fleet status into a formatted PDF and sends it to Telegram.

READ-ONLY. It reads the status snapshot the monitor writes (state/status_latest.json), asks
systemd for service facts, reads the last hour of each log for errors, and renders a PDF.
It changes no strategy, no service and no state file of any strategy.

  status_pdf.py                       render from state/status_latest.json and send
  status_pdf.py --json X --no-send    render only
Output: reports/status/STATUS-YYYY-MM-DD-HH00.pdf (PDFs older than 14 days are pruned).
"""
import argparse, collections, glob, json, os, re, shutil, subprocess, sys, time, urllib.request, uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(STACK, "common"))
import labcommon
HOME = os.path.expanduser("~")
OUTDIR = os.path.join(STACK, "reports", "status"); os.makedirs(OUTDIR, exist_ok=True)
DEF_JSON = os.path.join(STACK, "state", "status_latest.json")

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT, BOLD, MONO = "Helvetica", "Helvetica-Bold", "Courier"
try:
    D = "/usr/share/fonts/truetype/dejavu/"
    pdfmetrics.registerFont(TTFont("DejaVu", D + "DejaVuSans.ttf")); pdfmetrics.registerFont(TTFont("DejaVu-Bold", D + "DejaVuSans-Bold.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVuMono", D + "DejaVuSansMono.ttf"))
    FONT, BOLD, MONO = "DejaVu", "DejaVu-Bold", "DejaVuMono"
except Exception:
    pass

INK, MUTED, LINE, PANEL = colors.HexColor("#1b2430"), colors.HexColor("#5b6675"), colors.HexColor("#d9dee5"), colors.HexColor("#f4f6f9")
NAVY, GOOD, BAD, WARN = colors.HexColor("#16324f"), colors.HexColor("#1e7b4a"), colors.HexColor("#b3261e"), colors.HexColor("#a35a00")
GOOD_BG, BAD_BG, WARN_BG = colors.HexColor("#e6f4ec"), colors.HexColor("#fbe9e7"), colors.HexColor("#fdf1e0")
S = dict(
    h1=ParagraphStyle("h1", fontName=BOLD, fontSize=17, leading=21, textColor=colors.white),
    sub=ParagraphStyle("sub", fontName=FONT, fontSize=8.5, leading=11, textColor=colors.HexColor("#cfd8e3")),
    h2=ParagraphStyle("h2", fontName=BOLD, fontSize=11.5, leading=15, textColor=NAVY, spaceBefore=6, spaceAfter=3),
    card=ParagraphStyle("card", fontName=BOLD, fontSize=10.5, leading=13, textColor=colors.white),
    desc=ParagraphStyle("desc", fontName=FONT, fontSize=7.8, leading=10.2, textColor=MUTED),
    lab=ParagraphStyle("lab", fontName=BOLD, fontSize=8, leading=10.5, textColor=INK),
    val=ParagraphStyle("val", fontName=FONT, fontSize=8, leading=10.5, textColor=INK),
    small=ParagraphStyle("small", fontName=FONT, fontSize=7.4, leading=9.6, textColor=MUTED),
    mono=ParagraphStyle("mono", fontName=MONO, fontSize=6.9, leading=9, textColor=INK),
    tileN=ParagraphStyle("tileN", fontName=BOLD, fontSize=15, leading=18, textColor=INK),
    tileL=ParagraphStyle("tileL", fontName=FONT, fontSize=7.5, leading=9.5, textColor=MUTED),
    th=ParagraphStyle("th", fontName=BOLD, fontSize=7.8, leading=10, textColor=colors.white),
    ok=ParagraphStyle("ok", fontName=BOLD, fontSize=8, leading=10.5, textColor=GOOD),
    bad=ParagraphStyle("bad", fontName=BOLD, fontSize=8, leading=10.5, textColor=BAD),
)
W = A4[0] - 28 * mm

def esc(x): return str("" if x is None else x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def P(t, s="val"): return Paragraph(esc(t), S[s])
def sh(cmd, timeout=15):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception: return ""
def clean(t):
    """status text carries emoji and arrows that PDF fonts render as boxes"""
    t = str(t)
    for a, b in (("✅", "OK"), ("❌", "DOWN"), ("🚨", "[URGENT]"), ("⚠", "[WARN]"), ("🔵", ""), ("🔷", ""), ("🟢", ""), ("🛑", "[STOP]"), ("🔔", ""),
                 ("🔎", ""), ("📋", ""), ("🧪", ""), ("🔇", "[MUTED]"), ("→", "->"), ("—", "-"), ("–", "-"), ("\u00d7", "x")):
        t = t.replace(a, b)
    t = t.replace("mid # outside band", "price outside the quoting band")
    return re.sub(r"[\U00010000-\U0010ffff\ufe0f]", "", t).strip()

# ------------------------------------------------------------------ facts
def service_rows(names):
    rows = []
    for n in names:
        out = sh(["systemctl", "show", n + ".service", "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts", "-p", "ActiveEnterTimestamp",
                  "-p", "MemoryCurrent", "-p", "UnitFileState"])
        p = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        if p.get("UnitFileState") in ("disabled", "") and p.get("ActiveState") != "active": continue      # retired / not installed
        mem = p.get("MemoryCurrent", "")
        since = p.get("ActiveEnterTimestamp", "")
        try:
            t = datetime.strptime(since, "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc).timestamp(); up = time.time() - t
            ups = f"{up / 86400:.1f} days" if up > 86400 else f"{up / 3600:.1f} h" if up > 3600 else f"{up / 60:.0f} min"
        except Exception: ups = "-"
        rows.append(dict(name=n, state=p.get("ActiveState", "?"), sub=p.get("SubState", ""), restarts=p.get("NRestarts", "?"),
                         mem=f"{int(mem) / 2 ** 20:.0f} MB" if mem.isdigit() else "-", up=ups))
    return rows

LOGS = [("S1 observer", "s1_harness/harness.log"), ("S1 trader", "s1_harness/trader.log"), ("S9 whale watcher", "s1_harness/s9_data/s9_watch.log"),
        ("BME book recorder", "POLYMARKET-VPS-STACK/polymarket-stack/4-BME-BOOK-MOVEMENT-ENGINE/s9_data/bme/bme.log"),
        ("S3 fee-farm maker", "s3_feefarm/s3_maker.log"), ("S46 cascade + coin-flip", "s46_coinflip_cascade/s46_harvester.log"),
        ("S10 vol-event box", "s10_volbox/s10_box.log"), ("S8 session router", "s8_router/s8_router.log"),
        ("S5 wick-fade recorder", "s5_wickfade/s5-collector.service.log"),
        ("US round recorder", "mstack/us_measure/ms-us-recorder.service.log"), ("Weather watcher", "mstack/us_measure/ms-nws-watch.service.log"),
        ("Rule-change watcher", "mstack/regime_watch/regime_watch.log")]
ERR_RE = re.compile(r"error|traceback|exception|failed|problem|stall|silent for|FATAL|REJECTED|BLIND|DIVERGENCE|DATA ERROR|slow consumer", re.I)
OK_RE = re.compile(r"errors?[ =]0\b|errs=0|no regime changes|error-like 0")

def tail_lines(path, max_bytes=400000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - max_bytes)); return f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []

def errors_last_hour():
    now = datetime.now(timezone.utc); sod = now.hour * 3600 + now.minute * 60 + now.second
    out = []
    for name, rel in LOGS:
        p = os.path.join(HOME, rel)
        if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 7200: continue
        hits = []
        for ln in tail_lines(p):
            m = re.match(r"\[?(?:\d{4}-\d\d-\d\d[ T])?(\d\d):(\d\d):(\d\d)", ln)
            if not m: continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if (sod - t) % 86400 <= 3600 and ERR_RE.search(ln) and not OK_RE.search(ln): hits.append(ln)
        # a feed that drops and reconnects by itself is routine; everything else is a real error line
        for routine, group in ((True, [h for h in hits if ROUTINE_RE.search(h)]), (False, [h for h in hits if not ROUTINE_RE.search(h)])):
            if not group: continue
            kinds = collections.Counter(re.sub(r"\d+(\.\d+)?", "#", re.sub(r"^\[[^\]]+\]\s*", "", clean(h)))[:110] for h in group)
            k, v = kinds.most_common(1)[0]
            out.append(dict(source=name, routine=routine, count=len(group), kinds=len(kinds), top=f"{v}x  {k}", latest=clean(group[-1])[:230]))
    return out

ROUTINE_RE = re.compile(r"Going away|slow consumer|silent for \d+s|ConnectionClosed|reconnect in|Service restarting|backoff \d+s", re.I)

def short_reason(line):
    for pat, txt in (("slow consumer", "server dropped a slow connection, reconnected"), ("Going away", "server closed the connection, reconnected"),
                     ("silent for", "feed went silent, watchdog reconnected"), ("Service restarting", "server restarted, reconnected")):
        if pat.lower() in line.lower(): return txt
    return "connection dropped, reconnected"

def alerts_last_hour():
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3600)); out = []
    for ln in tail_lines(os.path.join(HOME, "s1_harness", "s1-monitor.service.log"), 200000):
        m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)Z\] (ALERT\[[^\]]*\]|MUTED\[[^\]]*\]|STORM[^ ]*|monitor internal error[^:]*:?)\s*(.*)", ln)
        if m and m.group(1) >= cut: out.append(dict(utc=m.group(1)[11:], source="fleet monitor", text=clean(m.group(3) or m.group(2))[:260]))
    cut2 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    for ln in tail_lines(os.path.join(STACK, "state", "alerts.log"), 100000):
        m = re.match(r"\[([^\]]+)\] SENT\[(\w+):([^:\]]+):[^\]]*\] (.*)", ln)
        if m and m.group(1) >= cut2 and m.group(2) != "report": out.append(dict(utc=m.group(1)[11:19], source="measurement stack", text=clean(m.group(4))[:260]))
    return out

def measurement_section():
    hb = {n: labcommon.read_heartbeat(n) or {} for n in ("us_recorder", "nws_watch", "regime_watch")}
    def j(p):
        try: return json.load(open(p))
        except Exception: return {}
    val = j(os.path.join(STACK, "us_measure", "us_rounds", "validation_summary.json"))
    bt = j(j(os.path.join(STACK, "backtest7d", "out", "latest.json")).get("out", "") + "/summary.json") if os.path.exists(os.path.join(STACK, "backtest7d", "out", "latest.json")) else {}
    u, w, r = hb["us_recorder"], hb["nws_watch"], hb["regime_watch"]
    age = lambda h: f"{max(0, int(time.time() - h.get('ts', 0)))} s ago" if h.get("ts") else "no heartbeat"
    rows = [("US round recorder", f"heartbeat {age(u)}; {u.get('frames_last_min', '?')} venue frames in the last minute; exchange books live: {', '.join(u.get('books') or []) or 'none'}; "
             f"rounds closed since start {u.get('rounds_closed', 0)}, divergences {u.get('divergences', 0)}"),
            ("Settlement calculator check", f"{val.get('comparisons', 0)} of {val.get('target', 200)} rounds compared, agrees on {val.get('agree', 0)}"
             + (f" ({val.get('agree_pct')} %)" if val.get("comparisons") else "") + f". Verdict: {val.get('verdict', 'not run yet')}"),
            ("Weather watcher", f"heartbeat {age(w)}; {w.get('quoted', '?')} of {w.get('buckets', '?')} temperature markets quoted; forecast updates seen {w.get('forecast_events', 0)}; "
             f"buy-every-bucket flags {w.get('arb_flags', 0)}; NWS call errors {w.get('nws_errors', 0)}"),
            ("Rule-change watcher", f"heartbeat {age(r)}; sources healthy {r.get('sources_ok', '?')} of {r.get('sources_total', '?')}"
             + (f"; BLIND on {', '.join(r['blind'])}" if r.get("blind") else "") + f"; changes in the last cycle: {', '.join(r.get('changes_this_cycle') or []) or 'none'}")]
    tr = next((s for s in bt.get("summary", []) if s.get("label_source", "").startswith("(a)")), None)
    if tr:
        rows.append(("7-day backtest (S1, true labels)", f"{tr['trades_taken']} trades, {tr['win_rate_pct']} % wins against {tr['breakeven_win_rate_pct']} % needed, "
                     f"net ${tr['net_usd_fee_adjusted']} after fees. Public labels disagreed with the blockchain on {bt.get('public_disagreements')} rounds."))
    return rows

# ------------------------------------------------------------------ rendering
PREFIX = [  # (regex on the segment, label, value template) - first match wins
    (r"^last hour:\s*(.*)$", "Last hour", r"\1"), (r"^all-time:\s*(.*)$", "All-time", r"\1"),
    (r"^rounds recorded\s+(.*)$", "Rounds recorded", r"\1"),
    (r"^settlement rule:\s*full-avg\s+(.*)$", "Rule check: full-round average", r"matched \1 rounds"),
    (r"^end-value\s+(.*)$", "Rule check: end value", r"matched \1 rounds"),
    (r"^on (\d+) disagreement rounds:\s*(.*)$", "Where the two rules disagree", r"\1 rounds: \2"),
    (r"^S1 dry ledger\s+(.*)$", "Dry ledger", r"\1"), (r"^live\s+(.*)$", "Live trading", r"\1"),
    (r"^feed_lag=(.*)$", "Feed lag", r"\1"), (r"^edge-agree\(same/signals\)=(.*)$", "Same side as our signal", r"\1"),
    (r"^([0-9a-z]+-[a-z0-9]+):\s*(follows=.*)$", r"Wallet \1", r"\2"),
    (r"^today's file\s+(.*)$", "Today's file", r"\1"), (r"^disk free\s+(.*)$", "Disk free", r"\1"), (r"^7-day gate:\s*(.*)$", "7-day gate", r"\1"),
    (r"^session\s+(\d{8}T.*)$", "Current session", r"\1"), (r"^sessions on disk\s+(.*)$", "Sessions on disk", r"\1"),
    (r"^session\s+(.*)$", "Trading session", r"\1"), (r"^heartbeat\s+(.*)$", "Heartbeat", r"\1"),
    (r"^moondev\s+(.*)$", "Moon Dev key", r"\1"), (r"^regime\s+(.*)$", "Regime", r"\1"), (r"^state\s+(.*)$", "State age", r"\1"),
    (r"^sigma\s+(.*)$", "Volatility", r"\1"), (r"^allowed:\s*(.*)$", "Allowed now", r"\1"), (r"^blocked:\s*(.*)$", "Blocked now", r"\1"),
    (r"^next macro:\s*(.*)$", "Next macro event", r"\1"), (r"^transitions this hour:\s*(.*)$", "Regime changes this hour", r"\1"),
    (r"^errors\s+(.*)$", "Errors", r"\1"), (r"^kill-test sample:\s*(.*)$", "Kill-test sample", r"\1"), (r"^kill-test:?\s*(.*)$", "Kill-test", r"\1"),
    (r"^S4:\s*last hour\s*(.*)$", "S4 last hour", r"\1"), (r"^cascades logged\s+(.*)$", "Cascades logged", r"\1"),
    (r"^entries\s+(.*)$", "Entries", r"\1"), (r"^vol(?:atility)? baseline\s+(.*)$", "Volatility baseline", r"\1"),
    (r"^(LIVE=.*|KILL=.*)$", "Mode", r"\1"),
]

def split_digest(d):
    rows = []
    for seg in re.split(r"\s+\|\|?\s+", clean(d)):
        seg = seg.strip()
        if not seg: continue
        for rx, lab, tpl in PREFIX:
            m = re.match(rx, seg, re.I)
            if m:
                rows.append((m.expand(lab) if "\\" in lab else lab, m.expand(tpl))); break
        else:
            m = re.match(r"^([A-Za-z0-9 .'/-]{2,28}):\s+(.*)$", seg)
            if m and len(m.group(1).split()) <= 3: rows.append((m.group(1)[:1].upper() + m.group(1)[1:], m.group(2)))
            else:
                w = seg.split(" ", 1); rows.append((w[0][:1].upper() + w[0][1:], w[1] if len(w) > 1 else ""))
    return rows

def card(title, desc, digest, width=W):
    head = Table([[Paragraph(esc(clean(title)), S["card"])]], colWidths=[width])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    body = [[Paragraph(esc(clean(desc)), S["desc"]), ""]]
    for lab, v in split_digest(digest): body.append([P(lab, "lab"), P(v)])
    t = Table(body, colWidths=[36 * mm, width - 36 * mm])
    st = [("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (1, 0), PANEL), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
          ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
          ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2)]
    for i, (lab, v) in enumerate(split_digest(digest), start=1):
        if lab == "Errors" and not re.match(r"^0\b", v): st.append(("BACKGROUND", (0, i), (1, i), BAD_BG))
    t.setStyle(TableStyle(st))
    return KeepTogether([head, t, Spacer(1, 7)])

def build(status, path):
    svc_names = list(status.get("services", {}).keys()) or []
    for extra in ("s1-monitor", "ms-us-recorder", "ms-nws-watch", "ms-regime-watch"):
        if extra not in svc_names: svc_names.append(extra)
    sv = service_rows(svc_names)
    errs, alerts = errors_last_hour(), alerts_last_hour()
    du = shutil.disk_usage("/"); free, total = du.free / 2 ** 30, du.total / 2 ** 30
    mem = sh(["free", "-m"]).splitlines(); memrow = mem[1].split() if len(mem) > 1 else []
    load = (open("/proc/loadavg").read().split()[:3] if os.path.exists("/proc/loadavg") else ["?"])
    up = sum(1 for r in sv if r["state"] == "active"); down = [r for r in sv if r["state"] != "active"]
    routine = [e for e in errs if e["routine"]]; errs = [e for e in errs if not e["routine"]]
    n_err = sum(e["count"] for e in errs); n_rout = sum(e["count"] for e in routine)
    gen = datetime.now(timezone.utc)

    def page(c, doc):
        c.saveState(); c.setFont(FONT, 7); c.setFillColor(MUTED)
        c.drawString(14 * mm, 9 * mm, f"Fleet status report, {status.get('hour', '')} UTC. Read-only summary. No strategy, service or trading setting was changed.")
        c.drawRightString(A4[0] - 14 * mm, 9 * mm, f"page {doc.page}"); c.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=15 * mm,
                            title=f"Fleet status {status.get('hour', '')} UTC", author="fleet monitor")
    E = []
    head = Table([[Paragraph("Fleet status report", S["h1"])],
                  [Paragraph(esc(f"Status hour {status.get('hour', '?')} UTC   |   generated {gen.strftime('%Y-%m-%d %H:%M:%S')} UTC   |   server {os.uname().nodename}"), S["sub"])]],
                 colWidths=[W])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (0, 0), 9),
                              ("BOTTOMPADDING", (0, 1), (0, 1), 9), ("TOPPADDING", (0, 1), (0, 1), 0)]))
    E += [head, Spacer(1, 7)]

    def tile(n, l, bg, fg=INK):
        st = ParagraphStyle("x", parent=S["tileN"], textColor=fg)
        t = Table([[Paragraph(esc(n), st)], [Paragraph(esc(l), S["tileL"])]], colWidths=[W / 4 - 3])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (0, 0), 6), ("BOTTOMPADDING", (0, 1), (0, 1), 6)]))
        return t
    pct = 100 * free / total if total else 0
    tiles = Table([[tile(f"{up} of {len(sv)}", "services running", GOOD_BG if not down else BAD_BG, GOOD if not down else BAD),
                    tile(f"{free:.0f} GB", f"disk free of {total:.0f} GB ({pct:.0f} %)", GOOD_BG if pct >= 25 else WARN_BG if pct >= 10 else BAD_BG, GOOD if pct >= 25 else WARN if pct >= 10 else BAD),
                    tile(str(n_err), "errors in the last hour", GOOD_BG if n_err == 0 else BAD_BG, GOOD if n_err == 0 else BAD),
                    tile(str(len(alerts)), "alerts sent in the last hour", GOOD_BG if not alerts else WARN_BG, GOOD if not alerts else WARN)]],
                  colWidths=[W / 4] * 4)
    tiles.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 1.5), ("RIGHTPADDING", (0, 0), (-1, -1), 1.5), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    E += [tiles, Spacer(1, 4)]
    E.append(Paragraph(esc(f"Memory {memrow[2] if len(memrow) > 2 else '?'} of {memrow[1] if len(memrow) > 1 else '?'} MB used.  Load average {', '.join(load)}.  "
                           + f"Routine feed reconnects in the last hour: {n_rout}.  "
                           + ("Every service is running." if not down else "NOT running: " + ", ".join(f"{r['name']} ({r['state']})" for r in down) + ".")), S["small"]))

    E.append(Paragraph("1. Services", S["h2"]))
    data = [[P(h, "th") for h in ("Service", "State", "Running for", "Restarts", "Memory")]]
    for r in sv:
        data.append([P(r["name"]), P("RUNNING" if r["state"] == "active" else f"{r['state'].upper()} ({r['sub']})", "ok" if r["state"] == "active" else "bad"),
                     P(r["up"]), P(r["restarts"]), P(r["mem"])])
    t = Table(data, colWidths=[W * .30, W * .22, W * .18, W * .14, W * .16], repeatRows=1)
    ts = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL])]
    for i, r in enumerate(sv, start=1):
        if r["state"] != "active": ts.append(("BACKGROUND", (0, i), (-1, i), BAD_BG))
    t.setStyle(TableStyle(ts)); E += [t, Spacer(1, 3)]
    E.append(Paragraph("s2-collect and s5-collector restart on purpose at the end of each recording session, so their restart counts grow by design.", S["small"]))

    E.append(Paragraph("2. Strategies", S["h2"]))
    for sec in status.get("sections", []):
        E.append(card(sec.get("title", sec.get("key", "")), sec.get("desc", ""), sec.get("digest", "")))

    E.append(Paragraph("3. Measurement stack (read-only research)", S["h2"]))
    rows = [[P(a, "lab"), P(b)] for a, b in measurement_section()]
    t = Table(rows, colWidths=[44 * mm, W - 44 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
                           ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, PANEL])]))
    E += [t, Spacer(1, 6)]

    E.append(Paragraph("4. Errors and alerts in the last hour", S["h2"]))
    if not errs:
        box = Table([[P("No errors in any log in the last hour.", "ok")]], colWidths=[W])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOOD_BG), ("BOX", (0, 0), (-1, -1), 0.6, GOOD), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        E += [box, Spacer(1, 5)]
    else:
        data = [[P(h, "th") for h in ("Source", "Lines", "Most common", "Most recent line")]]
        for e in sorted(errs, key=lambda x: -x["count"]):
            data.append([P(e["source"], "lab"), P(e["count"]), Paragraph(esc(e["top"]), S["mono"]), Paragraph(esc(e["latest"]), S["mono"])])
        t = Table(data, colWidths=[W * .19, W * .08, W * .33, W * .40], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BAD), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAD_BG])]))
        E += [t, Spacer(1, 6)]
    if routine:
        data = [[P(h, "th") for h in ("Routine feed reconnects", "Count", "What happened", "Last at (UTC)")]]
        for e in sorted(routine, key=lambda x: -x["count"]):
            tm = re.match(r"\[?([0-9:-]+[ T]?[0-9:]*)", e["latest"])
            data.append([P(e["source"], "lab"), P(e["count"]), P(short_reason(e["latest"])), P((tm.group(1) if tm else "")[-8:])])
        t = Table(data, colWidths=[W * .30, W * .10, W * .44, W * .16], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), MUTED), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL])]))
        E += [t, Spacer(1, 3), Paragraph("These are not errors. The price and order-book servers close connections from time to time and every recorder reconnects by itself within seconds.",
                                         S["small"]), Spacer(1, 6)]
    if alerts:
        data = [[P(h, "th") for h in ("Time UTC", "From", "Alert")]]
        for a in alerts: data.append([P(a["utc"]), P(a["source"]), P(a["text"])])
        t = Table(data, colWidths=[W * .13, W * .20, W * .67], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BAD), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        E.append(t)
    doc.build(E, onFirstPage=page, onLaterPages=page)
    return dict(services=len(sv), up=up, down=[r["name"] for r in down], free_gb=free, total_gb=total, errors=n_err, error_sources=len(errs),
                routine_reconnects=n_rout, alerts=len(alerts))

def send_document(path, caption):
    cfg = labcommon.read_env(labcommon.TG_ENV)
    token, chat, thread = cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID"), cfg.get("TELEGRAM_THREAD_ID")
    if not token or not chat: return False, "telegram not configured"
    b = uuid.uuid4().hex; parts = []
    fields = {"chat_id": chat, "caption": caption[:1000]}
    if thread: fields["message_thread_id"] = thread
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{os.path.basename(path)}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
                 + open(path, "rb").read() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    for _ in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=40)
            return (r.status == 200), f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(6); continue
            return False, f"HTTP {e.code}"
        except Exception as e:
            time.sleep(3); last = e.__class__.__name__
    return False, "network error"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json", default=DEF_JSON); ap.add_argument("--no-send", action="store_true"); ap.add_argument("--out", default="")
    a = ap.parse_args()
    status = json.load(open(a.json))
    tag = re.sub(r"[^0-9]", "", status.get("hour", datetime.now(timezone.utc).strftime("%Y-%m-%d %H")))
    path = a.out or os.path.join(OUTDIR, f"STATUS-{tag[:4]}-{tag[4:6]}-{tag[6:8]}-{tag[8:10]}00.pdf")
    info = build(status, path)
    for old in glob.glob(os.path.join(OUTDIR, "STATUS-*.pdf")):
        if time.time() - os.path.getmtime(old) > 14 * 86400:
            try: os.remove(old)
            except Exception: pass
    cap = (f"Fleet status {status.get('hour', '')}:00 UTC\n{info['up']} of {info['services']} services running"
           + (f" (DOWN: {', '.join(info['down'])})" if info["down"] else "") +
           f"\nDisk free {info['free_gb']:.0f} of {info['total_gb']:.0f} GB\nErrors last hour: {info['errors']}  |  routine reconnects: {info['routine_reconnects']}"
           f"  |  alerts sent: {info['alerts']}")
    sent, why = (False, "not sent (--no-send)") if a.no_send or os.environ.get("LAB_NO_TELEGRAM") else send_document(path, cap)
    labcommon.heartbeat("status_pdf", path=path, sent=sent, detail=why, **info)
    print(json.dumps(dict(path=path, bytes=os.path.getsize(path), sent=sent, detail=why, **info)))

if __name__ == "__main__":
    import urllib.error
    main()
