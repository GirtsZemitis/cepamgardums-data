#!/usr/bin/env python3
"""Sales dashboard for the vending machines (local + Azure). Pure Python stdlib.

Page loads read only the local cache (orders.db + in-memory STATE) — they never hit
the vending API. The API is touched only by a refresh, which is rate-limited to once
per REFRESH_MIN_INTERVAL and serialized by a lock, so we never hammer it.

Run:  ../.venv_orders/bin/python app.py   then open http://localhost:8765
Env:  PORT, DASH_USER/DASH_PASSWORD (auth gate), AUTO_REFRESH=1, XYNET_* (see auth.py)
"""
import base64
import datetime
import json
import os
import re
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import api_client

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "orders.db")
TOKEN_FILE = os.path.join(HERE, ".token")
STATE_FILE = os.path.join(HERE, "state.json")
PORT = int(os.environ.get("PORT", "8765"))
DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASSWORD = os.environ.get("DASH_PASSWORD")
REFRESH_MIN_INTERVAL = 300        # seconds: don't refresh (hit the API) more often than this

_refresh_lock = threading.Lock()
STATE = {"last_refresh": None, "stock": None}   # in-memory cache, persisted to STATE_FILE


def _load_state():
    global STATE
    if os.path.exists(STATE_FILE):
        try:
            STATE = json.load(open(STATE_FILE))
        except Exception:
            pass
    STATE.setdefault("last_refresh", None)
    STATE.setdefault("stock", None)


def _save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(STATE, f)
    except Exception:
        pass


def _secs_since_refresh():
    if not STATE.get("last_refresh"):
        return None
    try:
        t = datetime.datetime.fromisoformat(STATE["last_refresh"])
        return (datetime.datetime.now() - t).total_seconds()
    except Exception:
        return None


# ----------------------------- data assembly --------------------------------
def read_day(day):
    """Per-machine product breakdown for one day (qty + revenue)."""
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", day) or not os.path.exists(DB):
        return {"date": day, "machines": []}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT machine_code, location, product_name,
               SUM(CAST(quantity AS INT)) qty, ROUND(SUM(price_eur),2) rev
        FROM orders WHERE order_date=? AND shipping_status='Goods Shipped'
        GROUP BY machine_code, location, product_name
        ORDER BY machine_code, qty DESC""", (day,)).fetchall()
    con.close()
    tmap = {}
    for r in rows:
        mc = str(r["machine_code"])
        tmap.setdefault(mc, {"label": r["location"] or f"Automāts {mc}",
                             "qty": 0, "rev": 0, "products": []})
        tmap[mc]["products"].append({"name": r["product_name"], "qty": r["qty"], "rev": r["rev"]})
        tmap[mc]["qty"] += r["qty"]
        tmap[mc]["rev"] = round(tmap[mc]["rev"] + r["rev"], 2)
    return {"date": day, "machines": list(tmap.values())}


def read_data():
    secs = _secs_since_refresh()
    stale = (secs is None) or (secs > REFRESH_MIN_INTERVAL)
    if not os.path.exists(DB):
        return {"empty": True, "last_refresh": STATE.get("last_refresh"), "stale": stale}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    drows = con.execute("""
        SELECT order_date, machine_code, location,
               ROUND(SUM(price_eur),2) rev, COUNT(*) cnt
        FROM orders WHERE shipping_status='Goods Shipped'
        GROUP BY order_date, machine_code""").fetchall()
    machines = [dict(r) for r in con.execute(
        "SELECT machine_code, location FROM machines ORDER BY machine_code")]
    dmin = min((r["order_date"] for r in drows), default=None)
    dmax = max((r["order_date"] for r in drows), default=None)

    def label(m):
        return m["location"] or f"Automāts {m['machine_code']}"

    series, dates = [], []
    if dmin:
        d0 = datetime.date.fromisoformat(dmin)
        d1 = datetime.date.fromisoformat(dmax)
        dates = [(d0 + datetime.timedelta(days=i)).isoformat()
                 for i in range((d1 - d0).days + 1)]
        idx = {(r["order_date"], str(r["machine_code"])): r for r in drows}
        for m in machines:
            mc = str(m["machine_code"])
            series.append({
                "label": label(m),
                "rev": [idx[(d, mc)]["rev"] if (d, mc) in idx else 0 for d in dates],
                "cnt": [idx[(d, mc)]["cnt"] if (d, mc) in idx else 0 for d in dates],
            })

    total_rev = round(sum(r["rev"] for r in drows), 2)
    con.close()
    return {
        "dates": dates, "series": series, "range": [dmin, dmax],
        "today": read_day(dmax) if dmax else {"date": None, "machines": []},
        "total_revenue": total_rev,
        "last_refresh": STATE.get("last_refresh"), "stale": stale,
    }


def read_hourly():
    if not os.path.exists(DB):
        return {"dates": [], "orders": {}, "revenue": {}}
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT order_date, hour, COUNT(*), ROUND(SUM(price_eur),2) "
        "FROM orders WHERE shipping_status='Goods Shipped' "
        "GROUP BY order_date, hour").fetchall()
    con.close()
    dates = sorted({r[0] for r in rows})
    orders = {d: [0] * 24 for d in dates}
    revenue = {d: [0.0] * 24 for d in dates}
    for d, h, c, rev in rows:
        try:
            hi = int(h)
        except (TypeError, ValueError):
            continue
        if 0 <= hi < 24:
            orders[d][hi] = c
            revenue[d][hi] = rev or 0
    return {"dates": dates, "orders": orders, "revenue": revenue}


def compute_stock():
    """Live per-machine stock + units sold in the last 7 days (from the order book).
    Only called during a refresh — the result is cached in STATE['stock']."""
    inv = api_client.fetch_inventory()
    sold = {}
    if os.path.exists(DB):
        con = sqlite3.connect(DB)
        maxd = con.execute("SELECT MAX(order_date) FROM orders").fetchone()[0]
        if maxd:
            since = (datetime.date.fromisoformat(maxd) - datetime.timedelta(days=6)).isoformat()
            for mc, name, q in con.execute(
                    "SELECT machine_code, product_name, SUM(CAST(quantity AS INT)) "
                    "FROM orders WHERE order_date >= ? AND shipping_status='Goods Shipped' "
                    "GROUP BY machine_code, product_name", (since,)):
                sold[(str(mc), name)] = q
        con.close()
    for m in inv["machines"]:
        for p in m["products"]:
            p["sold7"] = sold.get((m["machine_code"], p["name"]), 0)
    return inv


def perform_refresh(emit=lambda e: None, force=False):
    """Refresh orders + stock from the API, streaming step events to `emit`.
    Rate-limited (unless force) and serialized so we never hammer the API."""
    secs = _secs_since_refresh()
    if not force and secs is not None and secs < REFRESH_MIN_INTERVAL and STATE.get("stock"):
        mins = int(secs // 60)
        emit({"done": True, "throttled": True,
              "msg": f"ℹ️ Dati jau svaigi (atjaunoti pirms {mins} min). Mēģini vēlāk.",
              "last_refresh": STATE.get("last_refresh")})
        return {"throttled": True}
    if not _refresh_lock.acquire(blocking=False):
        emit({"done": True, "busy": True, "msg": "⏳ Atjaunošana jau notiek…"})
        return {"busy": True}
    try:
        import auth
        if not auth.have_creds() and not os.path.exists(TOKEN_FILE):
            emit({"error": "Nav pieejas (nav konta/tokena)."})
            return {"error": "no creds"}
        emit({"step": "login", "msg": "🔐 Pieslēdzos Gaļas Nams sistēmai…"})
        token = auth.login() if auth.have_creds() else auth.get_token()
        emit({"step": "login_ok", "msg": "✓ Pieslēgšanās izdevās"})
        emit({"step": "api", "msg": "📡 Pieprasu pasūtījumus no API…"})

        def log(m):
            m = m.strip()
            mm = re.match(r"(\S+) … (\S+): \+(\d+)", m)
            if mm:
                emit({"step": "recv",
                      "msg": f"⬇️ Saņemu {mm.group(1)} – {mm.group(2)}  (+{mm.group(3)} pasūtījumi)"})
            elif m.startswith("Rebuilding"):
                emit({"step": "save", "msg": "💾 Saglabāju pasūtījumus…"})

        summary = api_client.run_fetch(token, log=log)
        emit({"step": "stock", "msg": "📦 Atjaunoju atlikumu…"})
        STATE["stock"] = compute_stock()
        STATE["last_refresh"] = datetime.datetime.now().isoformat(timespec="seconds")
        _save_state()
        emit({"done": True, "summary": summary, "last_refresh": STATE["last_refresh"],
              "msg": f"✅ Pabeigts · {summary['new_orders']} jauni pasūtījumi · €{summary['shipped_revenue']}"})
        return summary
    except Exception as e:
        emit({"error": str(e)})
        return {"error": str(e)}
    finally:
        _refresh_lock.release()


# ------------------------------- http server --------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def _authed(self):
        if not DASH_PASSWORD:
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                u, p = base64.b64decode(hdr[6:]).decode().split(":", 1)
                if u == DASH_USER and p == DASH_PASSWORD:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Galas Nams"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()

    def _refresh_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            perform_refresh(emit=self._sse, force=False)
        except Exception as e:
            try:
                self._sse({"error": str(e)})
            except Exception:
                pass

    def do_GET(self):
        if not self._authed():
            return
        if self.path == "/" or self.path.startswith("/index"):
            self._send(PAGE, "text/html")
        elif self.path == "/api/data":
            self._send(read_data())
        elif self.path == "/api/hourly":
            self._send(read_hourly())
        elif self.path.startswith("/api/day"):
            q = parse_qs(urlparse(self.path).query)
            self._send(read_day((q.get("date") or [""])[0]))
        elif self.path == "/api/stock":
            self._send(STATE.get("stock") or {"machines": []})
        elif self.path == "/api/refresh-stream":
            self._refresh_stream()
        else:
            self._send({"error": "not found"}, code=404)

    def do_POST(self):
        if not self._authed():
            return
        self._send({"error": "use /api/refresh-stream"}, code=404)


class DualStackServer(ThreadingHTTPServer):
    """Listen on IPv4 + IPv6 so 'localhost' works regardless of resolution."""
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        print("request error:", exc)


PAGE = r"""<!DOCTYPE html>
<html lang="lv"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gaļas Nams — pārdošanas panelis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{color-scheme:dark}*{box-sizing:border-box}
  body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:22px;background:#0f1115;color:#e8eaed}
  h1{font-size:20px;margin:0 0 2px}.sub{color:#9aa0a6;font-size:13px;margin-bottom:12px}
  .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
  .group{display:flex;gap:4px;background:#11141a;border:1px solid #232733;border-radius:10px;padding:4px}
  button{background:transparent;color:#cbd1d8;border:none;border-radius:7px;padding:8px 13px;font-size:13px;cursor:pointer}
  button.active{background:#2563eb;color:#fff}
  .refresh{background:#10b981;color:#06281f;font-weight:600}
  button:disabled{opacity:.45;cursor:not-allowed}
  select{background:#11141a;border:1px solid #232733;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px}
  .spinner{display:none;width:14px;height:14px;border:2px solid #ffffff55;border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-left:6px}
  .spinner.on{display:inline-block}@keyframes spin{to{transform:rotate(360deg)}}
  .status{font-size:12px;color:#9aa0a6}.lastref{font-size:12px;color:#6b7280}
  .card{background:#171a21;border:1px solid #232733;border-radius:14px;padding:16px;margin-bottom:18px}
  .wrap{position:relative;height:52vh;min-height:300px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
  .mcard{background:#171a21;border:1px solid #232733;border-left-width:4px;border-radius:12px;padding:14px}
  .mcard h3{margin:0 0 2px;font-size:14px}.mcard .tot{font-size:13px;margin-bottom:8px;font-weight:600}
  .row{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;border-top:1px solid #1f2430}
  .row .q{color:#9aa0a6}
  table.stock{width:100%;border-collapse:collapse;font-size:12.5px}
  table.stock th{color:#9aa0a6;text-align:right;font-weight:500;padding:4px;border-bottom:1px solid #232733}
  table.stock th:first-child{text-align:left}
  table.stock td{padding:4px;border-top:1px solid #1f2430;text-align:right}
  table.stock td:first-child{text-align:left}
  .rem-ok{color:#10b981}.rem-low{color:#f59e0b}.rem-out{color:#ef4444;font-weight:600}table.stock .cap{color:#5b616b}
  .note{color:#6b7280;font-size:12px}
  .daynav{display:flex;align-items:center;gap:10px;margin-bottom:14px}
  .daynav button{background:#11141a;border:1px solid #232733;color:#e8eaed;border-radius:10px;padding:7px 12px;font-size:14px;min-width:40px}
  .daynav button:active{background:#2563eb}
  .daynav button:disabled{opacity:.3}
  #daylabel{font-size:14px;font-weight:600;min-width:110px;text-align:center}
  #dtoday{margin-left:auto}
  .hero{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start;margin-bottom:18px}
  .hcard{flex:1 1 160px;min-width:150px;background:#171a21;border:1px solid #232733;border-top:3px solid;border-radius:14px;padding:14px 16px;cursor:pointer}
  .hlabel{font-size:13px;font-weight:600;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .hbig{font-size:30px;font-weight:800;letter-spacing:-1px;line-height:1}
  .hsub{font-size:12px;color:#9aa0a6;margin-top:4px;display:flex;justify-content:space-between;align-items:center}
  .chev{transition:transform .15s;color:#cbd1d8;font-size:32px;line-height:1}
  .hcard.open .chev{transform:rotate(180deg)}
  .hdetail{display:none;margin-top:10px;border-top:1px solid #232733;padding-top:6px}
  .hcard.open .hdetail{display:block}
  @media(max-width:560px){.hbig{font-size:26px}.hcard{flex:1 1 100%}}
  .tabs{display:flex;gap:4px;margin:8px 0 16px;border-bottom:1px solid #232733}
  .tabs button{border-radius:8px 8px 0 0;padding:11px 18px;color:#9aa0a6;font-size:14px}
  .tabs button.active{color:#fff;border-bottom:2px solid #2563eb}
  .tab{display:none}.tab.show{display:block}
  @media(max-width:560px){body{padding:14px}.tabs button{padding:11px 10px;flex:1}.wrap{height:46vh}}
</style></head><body>
<h1>Pārdošanas panelis</h1>
<div class="sub" id="sub">Ielādē…</div>

<div class="bar">
  <button class="refresh" id="refresh">↻ Atjaunot datus<span class="spinner" id="spin"></span></button>
  <span class="lastref" id="lastref"></span>
  <span class="status" id="status"></span>
</div>

<div class="tabs">
  <button data-tab="t-sales" class="active">📈 Pārdošana</button>
  <button data-tab="t-day">⏰ Diena</button>
  <button data-tab="t-stock">📦 Atlikums</button>
</div>

<section id="t-sales" class="tab show">
  <div class="daynav">
    <button id="dprev" title="Iepriekšējā diena">◀</button>
    <span id="daylabel"></span>
    <button id="dnext" title="Nākamā diena">▶</button>
    <button id="dtoday">Šodien</button>
  </div>
  <div class="hero" id="hero"></div>
  <div class="bar" style="margin-top:20px">
    <div class="group"><button id="m-rev" class="active">Ieņēmumi (€)</button><button id="m-cnt">Pasūtījumi</button></div>
    <div class="group"><button id="g-day" class="active">Dienas</button><button id="g-week">Nedēļas</button></div>
    <select id="rangesel">
      <option value="7">Pēdējā nedēļa</option>
      <option value="14">2 nedēļas</option>
      <option value="21" selected>3 nedēļas</option>
      <option value="30">30 dienas</option>
      <option value="9999">Viss periods</option>
    </select>
  </div>
  <div class="card"><div class="wrap"><canvas id="chart"></canvas></div></div>
</section>

<section id="t-day" class="tab">
  <div class="bar">
    <select id="hourdate"></select>
    <div class="group"><button id="h-cnt">Pasūtījumi</button><button id="h-rev" class="active">Ieņēmumi (€)</button></div>
    <span class="status" id="hourtot"></span>
  </div>
  <div class="card"><div class="wrap" style="height:34vh;min-height:240px"><canvas id="hourchart"></canvas></div></div>
</section>

<section id="t-stock" class="tab">
  <p class="note" id="stockhead">Atlikums = dzīvs no automāta. “Pārd. 7d” = reāli pārdots pēdējās 7 dienās.</p>
  <div class="grid" id="stock"></div>
</section>

<script>
const COLORS=["#3b82f6","#ef4444","#10b981","#f59e0b","#a855f7"];
const NAME_MAP={
  "1 KG Cāļu fileja MAIGUMIŅŠ":"MAIGUMIŅŠ",
  "1KG Cūkas šašliks TRADICIONĀLAIS":"TRADICIONĀLAIS",
  "1KG Cūkas šašliks GRUZĪNU":"GRUZĪNU",
  "1KG Vistas Giross-Atkauloti cāļu šķiņķīši":"ŠĶIŅĶĪŠI",
  "1KG Fermentēti dārzeņi korejiešu kimčī KĀPOSTIŅŠ":"KĀPOSTIŅŠ"
};
const shortName=n=>NAME_MAP[n]||n;   // fall back to full name if not mapped
const $=id=>document.getElementById(id);
let DATA=null, metric="rev", gran="day", rangeDays=21, chart=null, dayIdx=0;
const colorByLabel={};
let HOURLY=null, hmetric="rev", hourChart=null;
let refreshing=false, autoTried=false;
const TOUCH_EVENTS=["mousemove","mouseout","click"];   // ignore touchmove so scrolling doesn't pop tooltips

async function load(){
  try{ DATA=await (await fetch("/api/data")).json(); }
  catch(e){ $("sub").textContent="Kļūda ielādējot datus: "+e; return; }
  updateRefreshUI();
  if(DATA.empty){ $("sub").textContent="Nav datu — nospied “Atjaunot datus”."; maybeAuto(); return; }
  $("sub").textContent=`Periods: ${DATA.range[0]} — ${DATA.range[1]}  ·  ${DATA.series.length} automāti  ·  kopā €${DATA.total_revenue.toLocaleString("lv-LV")}`;
  setDay(DATA.dates.length-1); try{ draw(); }catch(e){}
  maybeAuto();
}
function maybeAuto(){ if(DATA && DATA.stale && !autoTried && !refreshing){ autoTried=true; startRefresh(); } }

function activeData(){
  const n=Math.min(rangeDays, DATA.dates.length), start=Math.max(0,DATA.dates.length-n);
  return {dates:DATA.dates.slice(start),
          series:DATA.series.map(s=>({label:s.label, rev:s.rev.slice(start), cnt:s.cnt.slice(start)}))};
}
function weekly(dates){
  const labels=[],bucket=[];let cur=null;
  dates.forEach(d=>{const dt=new Date(d+"T00:00:00");const wd=(dt.getDay()+6)%7;
    const mon=new Date(dt);mon.setDate(dt.getDate()-wd);const k=mon.toISOString().slice(0,10);
    if(k!==cur){cur=k;labels.push(k);}bucket.push(labels.length-1);});
  return{labels,bucket};
}
function view(){
  const ad=activeData();
  if(gran==="day")return{labels:ad.dates,series:ad.series.map(s=>s[metric].slice()),labelsFor:ad.series.map(s=>s.label)};
  const{labels,bucket}=weekly(ad.dates);
  return{labels,series:ad.series.map(s=>{const v=Array(labels.length).fill(0);
    s[metric].forEach((x,i)=>v[bucket[i]]+=x);return v.map(x=>Math.round(x*100)/100);}),labelsFor:ad.series.map(s=>s.label)};
}
const fmt=v=>metric==="rev"?"€"+v.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2}):v+" pas.";
function sets(){
  const vw=view();
  const m=vw.series.map((arr,i)=>({label:vw.labelsFor[i],data:arr,borderColor:COLORS[i%5],
    backgroundColor:COLORS[i%5]+"22",borderWidth:2,tension:.3,pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false}));
  const tot=vw.labels.map((_,j)=>Math.round(vw.series.reduce((a,arr)=>a+arr[j],0)*100)/100);
  m.unshift({label:"Kopā",data:tot,borderColor:"#e8eaed",borderDash:[6,4],borderWidth:3,tension:.3,
    pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false});
  return{labels:vw.labels,datasets:m};
}
function draw(){
  const d=sets();
  if(chart){chart.data.labels=d.labels;chart.data.datasets=d.datasets;chart.update();return;}
  chart=new Chart($("chart"),{type:"line",data:d,options:{
    responsive:true,maintainAspectRatio:false,events:TOUCH_EVENTS,interaction:{mode:"index",intersect:false},
    plugins:{legend:{labels:{color:"#e8eaed",usePointStyle:true}},
      tooltip:{callbacks:{label:c=>c.dataset.label+": "+fmt(c.parsed.y)}}},
    scales:{x:{ticks:{color:"#9aa0a6",maxTicksLimit:14,maxRotation:0},grid:{color:"#1f2430"}},
      y:{ticks:{color:"#9aa0a6",callback:v=>metric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});
}
function renderDay(dd){
  const dmap={}; dd.machines.forEach(m=>dmap[m.label]=m);
  const el=$("hero"); el.innerHTML="";
  DATA.series.forEach((s,i)=>{                        // a card per machine; tap to expand its products
    const c=COLORS[i%5], m=dmap[s.label]||{qty:0,rev:0,products:[]};
    const rows=m.products.length
      ? m.products.map(p=>`<div class="row"><span>${shortName(p.name)}</span><span class="q">${p.qty} × · €${p.rev.toFixed(2)}</span></div>`).join("")
      : `<div class="row"><span class="q">— nav pārdošanas —</span></div>`;
    el.insertAdjacentHTML("beforeend",
      `<div class="hcard" style="border-top-color:${c}">
         <div class="hlabel" style="color:${c}">${s.label}</div>
         <div class="hbig">€${(m.rev||0).toFixed(2)}</div>
         <div class="hsub"><span>${m.qty||0} gab.</span><span class="chev">▾</span></div>
         <div class="hdetail">${rows}</div></div>`);
  });
}
let dayReq=0;
async function setDay(idx){
  const last=DATA.dates.length-1;
  dayIdx=Math.max(0,Math.min(idx,last));
  $("daylabel").textContent=DATA.dates[dayIdx]+(dayIdx===last?" · šodien":"");
  $("dprev").disabled=dayIdx<=0; $("dnext").disabled=dayIdx>=last; $("dtoday").disabled=dayIdx===last;
  const date=DATA.dates[dayIdx], my=++dayReq;
  let dd; try{ dd=await (await fetch("/api/day?date="+date)).json(); }catch(e){ dd={machines:[]}; }
  if(my!==dayReq) return;     // a newer day was selected; ignore this stale response
  renderDay(dd);
}

async function loadHourly(){
  try{ HOURLY=await (await fetch("/api/hourly")).json(); }catch(e){ return; }
  const sel=$("hourdate"); if(!HOURLY.dates.length)return;
  const keep=sel.value;
  sel.innerHTML=HOURLY.dates.map(d=>`<option>${d}</option>`).join("");
  sel.value=(keep&&HOURLY.dates.includes(keep))?keep:HOURLY.dates[HOURLY.dates.length-1];
  drawHourly();
}
function drawHourly(){
  if(!HOURLY||!HOURLY.dates.length)return;
  const d=$("hourdate").value;
  const series=(hmetric==="rev"?HOURLY.revenue:HOURLY.orders)[d]||[];
  const labels=Array.from({length:24},(_,h)=>String(h).padStart(2,"0"));
  const total=series.reduce((a,b)=>a+b,0);
  $("hourtot").textContent=hmetric==="rev"?`Kopā €${total.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2})}`:`Kopā ${total} pas.`;
  const cfg={labels,datasets:[{data:series,backgroundColor:"#3b82f6",borderRadius:4}]};
  if(hourChart){hourChart.data=cfg;hourChart.update();return;}
  hourChart=new Chart($("hourchart"),{type:"bar",data:cfg,options:{responsive:true,maintainAspectRatio:false,events:TOUCH_EVENTS,
    plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>hmetric==="rev"?"€"+c.parsed.y:c.parsed.y+" pas."}}},
    scales:{x:{ticks:{color:"#9aa0a6"},grid:{display:false}},
      y:{ticks:{color:"#9aa0a6",callback:v=>hmetric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});
}

async function loadStock(){
  const el=$("stock");
  let s; try{ s=await (await fetch("/api/stock")).json(); }catch(e){ el.innerHTML="<p class='note'>Kļūda: "+e+"</p>"; return; }
  if(!s.machines||!s.machines.length){ el.innerHTML="<p class='note'>Nav atlikuma datu — atjauno datus.</p>"; return; }
  el.innerHTML="";
  s.machines.forEach((m,i)=>{
    const c=COLORS[i%5];
    const rows=m.products.map(p=>{const pct=p.capacity?p.remaining/p.capacity:1;
      const cls=p.remaining===0?"rem-out":(pct<=0.25?"rem-low":"rem-ok");
      return `<tr><td>${p.name}</td><td>${p.sold7}</td><td class="${cls}">${p.remaining}</td></tr>`;}).join("");
    el.insertAdjacentHTML("beforeend",
      `<div class="mcard" style="border-left-color:${c}"><h3 style="color:${c}">${m.label}</h3>
       <table class="stock"><thead><tr><th>Produkts</th><th>Pārd. 7d</th><th>Atlikums</th></tr></thead><tbody>${rows}</tbody></table></div>`);
  });
}

function agoText(iso){
  if(!iso) return "nav atjaunots";
  const s=(Date.now()-new Date(iso).getTime())/1000;
  if(s<60) return "tikko atjaunots";
  if(s<3600) return "atjaunots pirms "+Math.floor(s/60)+" min";
  return "atjaunots pirms "+Math.floor(s/3600)+" h";
}
function updateRefreshUI(){
  const iso=DATA&&DATA.last_refresh;
  $("lastref").textContent=agoText(iso);
  if(refreshing) return;
  const s=iso?(Date.now()-new Date(iso).getTime())/1000:Infinity;
  const btn=$("refresh");
  if(s<300){ btn.disabled=true; btn.title="Pieejams pēc "+Math.ceil((300-s)/60)+" min"; }
  else { btn.disabled=false; btn.title=""; }
}

function startRefresh(){
  if(refreshing) return;
  refreshing=true;
  const btn=$("refresh"), spin=$("spin"), st=$("status");
  btn.disabled=true; spin.classList.add("on"); st.textContent="…";
  let finished=false;
  const es=new EventSource("/api/refresh-stream");
  const finish=async()=>{ es.close(); refreshing=false; spin.classList.remove("on");
    await load(); await loadHourly(); await loadStock(); };
  es.onmessage=async(ev)=>{
    const d=JSON.parse(ev.data);
    if(d.error){ finished=true; st.textContent="✗ "+d.error; await finish(); return; }
    if(d.done){ finished=true; if(d.msg) st.textContent=d.msg; await finish(); return; }
    st.textContent=d.msg;     // single status line that changes per step (real-time)
  };
  es.onerror=async()=>{ if(!finished){ st.textContent="✗ Savienojuma kļūda"; } await finish(); };
}

function setA(on,off){$(on).classList.add("active");$(off).classList.remove("active");}
$("m-rev").onclick=()=>{metric="rev";setA("m-rev","m-cnt");draw();};
$("m-cnt").onclick=()=>{metric="cnt";setA("m-cnt","m-rev");draw();};
$("g-day").onclick=()=>{gran="day";setA("g-day","g-week");draw();};
$("g-week").onclick=()=>{gran="week";setA("g-week","g-day");draw();};
$("rangesel").onchange=e=>{rangeDays=parseInt(e.target.value);draw();};
$("dprev").onclick=()=>setDay(dayIdx-1);
$("dnext").onclick=()=>setDay(dayIdx+1);
$("dtoday").onclick=()=>setDay(DATA.dates.length-1);
$("hero").onclick=e=>{const card=e.target.closest(".hcard"); if(card)card.classList.toggle("open");};
$("hourdate").onchange=drawHourly;
$("h-cnt").onclick=()=>{hmetric="cnt";setA("h-cnt","h-rev");drawHourly();};
$("h-rev").onclick=()=>{hmetric="rev";setA("h-rev","h-cnt");drawHourly();};
$("refresh").onclick=startRefresh;
function showTab(id){
  document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("show",s.id===id));
  document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===id));
  if(id==="t-sales"&&chart)chart.resize();
  if(id==="t-day"&&hourChart)hourChart.resize();
}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.tab));

setInterval(updateRefreshUI,30000);   // keep "x min ago" fresh and re-enable button at 5 min
load(); loadHourly(); loadStock();
</script></body></html>"""


def _startup_refresh():
    perform_refresh(force=True)


if __name__ == "__main__":
    os.chdir(HERE)
    _load_state()
    import auth
    print(f"dashboard on :{PORT} | auth={'on' if DASH_PASSWORD else 'off'} | "
          f"creds={'yes' if auth.have_creds() else 'no'}", flush=True)
    if os.environ.get("AUTO_REFRESH") == "1" and auth.have_creds():
        threading.Thread(target=_startup_refresh, daemon=True).start()
    DualStackServer(("::", PORT), Handler).serve_forever()
