#!/usr/bin/env python3
"""Local sales dashboard for the vending machines.

Run it:
    ../.venv_orders/bin/python app.py
then open http://localhost:8765 in your browser.

Serves one page: the per-machine sales chart + today's breakdown, with a Refresh
button that pulls the latest data from the API (paste your Authorization token once;
it's saved to a local, git-ignored .token file so you don't re-paste each time).

Pure standard library — no web framework needed.
"""
import datetime
import json
import os
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import api_client  # run_fetch(), same folder

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "orders.db")
TOKEN_FILE = os.path.join(HERE, ".token")
PORT = 8765


# ----------------------------- data assembly --------------------------------
def read_data():
    """Build the JSON payload the page renders: chart series + today breakdown."""
    if not os.path.exists(DB):
        return {"empty": True}
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

    # today's (latest day) per-machine, per-product breakdown
    day = dmax
    trows = con.execute("""
        SELECT machine_code, location, product_name,
               SUM(CAST(quantity AS INT)) qty, ROUND(SUM(price_eur),2) rev
        FROM orders WHERE order_date=? AND shipping_status='Goods Shipped'
        GROUP BY machine_code, location, product_name
        ORDER BY machine_code, qty DESC""", (day,)).fetchall() if day else []
    tmap = {}
    for r in trows:
        mc = str(r["machine_code"])
        tmap.setdefault(mc, {"label": r["location"] or f"Automāts {mc}",
                             "qty": 0, "rev": 0, "products": []})
        tmap[mc]["products"].append({"name": r["product_name"], "qty": r["qty"], "rev": r["rev"]})
        tmap[mc]["qty"] += r["qty"]
        tmap[mc]["rev"] = round(tmap[mc]["rev"] + r["rev"], 2)

    total_rev = round(sum(r["rev"] for r in drows), 2)
    con.close()
    meta = json.load(open(api_client.CACHE)).get("fetched_through") \
        if os.path.exists(api_client.CACHE) else None
    return {
        "dates": dates, "series": series, "range": [dmin, dmax],
        "today": {"date": day, "machines": list(tmap.values())},
        "total_revenue": total_rev, "fetched_through": meta,
    }


def read_hourly():
    """Per-day hourly breakdown (orders + revenue), 24 buckets per day."""
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


def stock_with_sales():
    """Live current stock (from the stock API) + real units sold in the last 7 days
    (from the order book) per machine & product."""
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
        pass  # quiet

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(PAGE, "text/html")
        elif self.path == "/api/data":
            self._send(read_data())
        elif self.path == "/api/stock":
            try:
                self._send(stock_with_sales())
            except Exception as e:
                self._send({"error": str(e)})
        elif self.path == "/api/hourly":
            self._send(read_hourly())
        elif self.path == "/api/has-auth":
            import auth
            self._send({"autoLogin": auth.have_creds(),
                        "hasToken": os.path.exists(TOKEN_FILE)})
        else:
            self._send({"error": "not found"}, code=404)

    def do_POST(self):
        if self.path != "/api/refresh":
            return self._send({"error": "not found"}, code=404)
        import auth
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        token = (payload.get("token") or "").strip()
        if not token and not auth.have_creds() and not os.path.exists(TOKEN_FILE):
            return self._send({"ok": False, "error": "Nav pieejas. Saglabā kontu (auth.py --set-creds)."})
        try:
            # run_fetch auto-logins via stored creds; explicit token used only if given
            summary = api_client.run_fetch(token or None, log=lambda m: None)
            self._send({"ok": True, "summary": summary})
        except Exception as e:
            self._send({"ok": False, "error": str(e)})


PAGE = r"""<!DOCTYPE html>
<html lang="lv"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gaļas Nams — pārdošanas panelis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:22px;background:#0f1115;color:#e8eaed}
  h1{font-size:20px;margin:0 0 2px}.sub{color:#9aa0a6;font-size:13px;margin-bottom:16px}
  .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
  .group{display:flex;gap:4px;background:#11141a;border:1px solid #232733;border-radius:10px;padding:4px}
  button{background:transparent;color:#cbd1d8;border:none;border-radius:7px;padding:8px 13px;font-size:13px;cursor:pointer}
  button.active{background:#2563eb;color:#fff}
  .refresh{background:#10b981;color:#06281f;font-weight:600}
  button:disabled{opacity:.5;cursor:not-allowed}
  select{background:#11141a;border:1px solid #232733;color:#e8eaed;border-radius:8px;padding:7px 10px;font-size:13px}
  .spinner{display:none;width:14px;height:14px;border:2px solid #ffffff55;border-top-color:#fff;
    border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-left:6px}
  .spinner.on{display:inline-block}
  @keyframes spin{to{transform:rotate(360deg)}}
  input{background:#11141a;border:1px solid #232733;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px;min-width:230px}
  .card{background:#171a21;border:1px solid #232733;border-radius:14px;padding:16px;margin-bottom:18px}
  .wrap{position:relative;height:54vh;min-height:320px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
  .mcard{background:#171a21;border:1px solid #232733;border-radius:12px;padding:14px}
  .mcard h3{margin:0 0 2px;font-size:14px}.mcard .tot{color:#10b981;font-size:13px;margin-bottom:8px}
  .row{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;border-top:1px solid #1f2430}
  .row .q{color:#9aa0a6}
  table.stock{width:100%;border-collapse:collapse;font-size:12.5px}
  table.stock th{color:#9aa0a6;text-align:right;font-weight:500;padding:4px 4px;border-bottom:1px solid #232733}
  table.stock th:first-child{text-align:left}
  table.stock td{padding:4px 4px;border-top:1px solid #1f2430;text-align:right}
  table.stock td:first-child{text-align:left}
  .rem-ok{color:#10b981}.rem-low{color:#f59e0b}.rem-out{color:#ef4444;font-weight:600}
  table.stock .cap{color:#5b616b}
  .status{font-size:12px;color:#9aa0a6;margin-left:6px}
  .note{color:#6b7280;font-size:12px}
  .tabs{display:flex;gap:4px;margin:10px 0 16px;border-bottom:1px solid #232733}
  .tabs button{border-radius:8px 8px 0 0;padding:11px 18px;color:#9aa0a6;font-size:14px}
  .tabs button.active{color:#fff;border-bottom:2px solid #2563eb}
  .tab{display:none}.tab.show{display:block}
  @media(max-width:560px){body{padding:14px}.tabs button{padding:11px 12px;flex:1}.wrap{height:48vh}}
</style></head><body>
<h1>Pārdošanas panelis</h1>
<div class="sub" id="sub">Ielādē…</div>

<div class="bar">
  <input id="token" type="password" placeholder="Authorization tokens (ja vajag)" style="display:none">
  <button class="refresh" id="refresh">↻ Atjaunot datus<span class="spinner" id="spin"></span></button>
  <span class="status" id="status"></span>
</div>

<div class="tabs">
  <button data-tab="t-sales" class="active">📈 Pārdošana</button>
  <button data-tab="t-day">⏰ Diena</button>
  <button data-tab="t-stock">📦 Atlikums</button>
</div>

<section id="t-sales" class="tab show">
  <div class="bar">
    <div class="group"><button id="m-rev" class="active">Apgrozījums (€)</button><button id="m-cnt">Pasūtījumi</button></div>
    <div class="group"><button id="g-day">Dienas</button><button id="g-week" class="active">Nedēļas</button></div>
  </div>
  <div class="card"><div class="wrap"><canvas id="chart"></canvas></div></div>
  <h2 style="font-size:16px;margin:18px 0 10px" id="todayhead">Šodienas pārdošana</h2>
  <div class="grid" id="today"></div>
</section>

<section id="t-day" class="tab">
  <div class="bar">
    <select id="hourdate"></select>
    <div class="group"><button id="h-cnt" class="active">Pasūtījumi</button><button id="h-rev">Apgrozījums (€)</button></div>
    <span class="status" id="hourtot"></span>
  </div>
  <div class="card"><div class="wrap" style="height:34vh;min-height:240px"><canvas id="hourchart"></canvas></div></div>
</section>

<section id="t-stock" class="tab">
  <p class="note" id="stockhead">Atlikums = dzīvs no automāta. “Pārd. 7d” = reāli pārdots pēdējās 7 dienās. “Ietilp.” = konfigurētā ietilpība (informatīvi).</p>
  <div class="grid" id="stock"></div>
</section>

<script>
const COLORS=["#3b82f6","#ef4444","#10b981","#f59e0b","#a855f7"];
let DATA=null, metric="rev", gran="week", chart=null;

async function load(){
  try{
    DATA=await (await fetch("/api/data")).json();
  }catch(e){document.getElementById("sub").textContent="Kļūda ielādējot datus: "+e;return;}
  if(DATA.empty){document.getElementById("sub").textContent="Nav datu — nospied “Atjaunot datus”.";return;}
  document.getElementById("sub").textContent=
    `Periods: ${DATA.range[0]} — ${DATA.range[1]}  ·  ${DATA.series.length} automāti  ·  kopā €${DATA.total_revenue.toLocaleString("lv-LV")}`;
  renderToday();
  try{ draw(); }
  catch(e){ document.getElementById("sub").textContent+="  (grafiks nav pieejams — pārbaudi interneta savienojumu)"; }
}
let HOURLY=null, hmetric="cnt", hourChart=null;
async function loadHourly(){
  try{ HOURLY=await (await fetch("/api/hourly")).json(); }catch(e){ return; }
  const sel=document.getElementById("hourdate");
  if(!HOURLY.dates.length){return;}
  const keep=sel.value;
  sel.innerHTML=HOURLY.dates.map(d=>`<option>${d}</option>`).join("");
  sel.value=(keep&&HOURLY.dates.includes(keep))?keep:HOURLY.dates[HOURLY.dates.length-1];
  drawHourly();
}
function drawHourly(){
  const d=document.getElementById("hourdate").value;
  const series=(hmetric==="rev"?HOURLY.revenue:HOURLY.orders)[d]||[];
  const labels=Array.from({length:24},(_,h)=>String(h).padStart(2,"0"));
  const total=series.reduce((a,b)=>a+b,0);
  document.getElementById("hourtot").textContent=
    hmetric==="rev"?`Kopā €${total.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2})}`:`Kopā ${total} pas.`;
  const cfg={labels,datasets:[{label:hmetric==="rev"?"€/stundā":"Pasūtījumi",data:series,
    backgroundColor:"#3b82f6",borderRadius:4}]};
  if(hourChart){hourChart.data=cfg;hourChart.update();return;}
  hourChart=new Chart(document.getElementById("hourchart"),{type:"bar",data:cfg,options:{
    responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},
      tooltip:{callbacks:{label:c=>hmetric==="rev"?"€"+c.parsed.y:c.parsed.y+" pas."}}},
    scales:{x:{ticks:{color:"#9aa0a6"},grid:{display:false}},
      y:{ticks:{color:"#9aa0a6",callback:v=>hmetric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});
}
async function loadStock(){
  const el=document.getElementById("stock");
  el.innerHTML="<p class='note'>Ielādē atlikumu…</p>";
  let s;
  try{ s=await (await fetch("/api/stock")).json(); }
  catch(e){ el.innerHTML="<p class='note'>Kļūda: "+e+"</p>"; return; }
  if(s.error){ el.innerHTML="<p class='note'>Kļūda: "+s.error+"</p>"; return; }
  el.innerHTML="";
  s.machines.forEach((m,i)=>{
    const rows=m.products.map(p=>{
      const pct=p.capacity?p.remaining/p.capacity:1;
      const cls=p.remaining===0?"rem-out":(pct<=0.25?"rem-low":"rem-ok");
      return `<tr><td>${p.name}</td><td>${p.sold7}</td><td class="${cls}">${p.remaining}</td><td class="cap">${p.capacity}</td></tr>`;
    }).join("");
    el.insertAdjacentHTML("beforeend",
      `<div class="mcard"><h3 style="color:${COLORS[i%5]}">${m.label}</h3>
       <table class="stock"><thead><tr><th>Produkts</th><th>Pārd. 7d</th><th>Atlikums</th><th class="cap">Ietilp.</th></tr></thead>
       <tbody>${rows}</tbody></table></div>`);
  });
}
function weekly(){
  const labels=[],bucket=[];let cur=null;
  DATA.dates.forEach(d=>{const dt=new Date(d+"T00:00:00");const wd=(dt.getDay()+6)%7;
    const mon=new Date(dt);mon.setDate(dt.getDate()-wd);const k=mon.toISOString().slice(0,10);
    if(k!==cur){cur=k;labels.push(k);}bucket.push(labels.length-1);});
  return{labels,bucket};
}
function view(){
  if(gran==="day")return{labels:DATA.dates,series:DATA.series.map(s=>s[metric].slice())};
  const{labels,bucket}=weekly();
  return{labels,series:DATA.series.map(s=>{const v=Array(labels.length).fill(0);
    s[metric].forEach((x,i)=>v[bucket[i]]+=x);return v.map(x=>Math.round(x*100)/100);})};
}
const fmt=v=>metric==="rev"?"€"+v.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2}):v+" pas.";
function sets(){
  const vw=view();
  const m=DATA.series.map((s,i)=>({label:s.label,data:vw.series[i],borderColor:COLORS[i%5],
    backgroundColor:COLORS[i%5]+"22",borderWidth:2,tension:.3,pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false}));
  const tot=vw.labels.map((_,j)=>Math.round(DATA.series.reduce((a,s,i)=>a+vw.series[i][j],0)*100)/100);
  m.unshift({label:"Kopā",data:tot,borderColor:"#e8eaed",borderDash:[6,4],borderWidth:3,tension:.3,
    pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false});
  return{labels:vw.labels,datasets:m};
}
function draw(){
  const d=sets();
  if(chart){chart.data.labels=d.labels;chart.data.datasets=d.datasets;chart.update();return;}
  chart=new Chart(document.getElementById("chart"),{type:"line",data:d,options:{
    responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
    plugins:{legend:{labels:{color:"#e8eaed",usePointStyle:true}},
      tooltip:{callbacks:{label:c=>c.dataset.label+": "+fmt(c.parsed.y)}}},
    scales:{x:{ticks:{color:"#9aa0a6",maxTicksLimit:14,maxRotation:0},grid:{color:"#1f2430"}},
      y:{ticks:{color:"#9aa0a6",callback:v=>metric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});
}
function renderToday(){
  const t=DATA.today; document.getElementById("todayhead").textContent="Pārdošana "+(t.date||"");
  const el=document.getElementById("today"); el.innerHTML="";
  if(!t.machines.length){el.innerHTML="<p class='note'>Šajā dienā vēl nav pārdošanas.</p>";return;}
  t.machines.forEach((m,i)=>{
    const rows=m.products.map(p=>`<div class="row"><span>${p.name}</span><span class="q">${p.qty} × · €${p.rev.toFixed(2)}</span></div>`).join("");
    el.insertAdjacentHTML("beforeend",
      `<div class="mcard"><h3 style="color:${COLORS[i%5]}">${m.label}</h3>
       <div class="tot">${m.qty} gab. · €${m.rev.toFixed(2)}</div>${rows}</div>`);
  });
}
function setA(on,off){document.getElementById(on).classList.add("active");document.getElementById(off).classList.remove("active");}
const $=id=>document.getElementById(id);
$("m-rev").onclick=()=>{metric="rev";setA("m-rev","m-cnt");draw();};
$("m-cnt").onclick=()=>{metric="cnt";setA("m-cnt","m-rev");draw();};
$("g-day").onclick=()=>{gran="day";setA("g-day","g-week");draw();};
$("g-week").onclick=()=>{gran="week";setA("g-week","g-day");draw();};
$("hourdate").onchange=drawHourly;
$("h-cnt").onclick=()=>{hmetric="cnt";setA("h-cnt","h-rev");drawHourly();};
$("h-rev").onclick=()=>{hmetric="rev";setA("h-rev","h-cnt");drawHourly();};

document.getElementById("refresh").onclick=async()=>{
  const btn=document.getElementById("refresh"), spin=document.getElementById("spin");
  const st=document.getElementById("status");
  btn.disabled=true; spin.classList.add("on"); st.textContent="Atjauno…";
  const token=document.getElementById("token").value;
  try{
    const r=await (await fetch("/api/refresh",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({token})})).json();
    if(r.ok){const s=r.summary; st.textContent=`✓ ${s.new_orders} jauni · €${s.shipped_revenue} · līdz ${s.fetched_through}`;
      document.getElementById("token").value=""; await load(); await loadHourly(); await loadStock();}
    else{st.textContent="✗ "+r.error;}
  }catch(e){st.textContent="✗ "+e;}
  finally{ btn.disabled=false; spin.classList.remove("on"); }
};
fetch("/api/has-auth").then(r=>r.json()).then(j=>{
  // show the manual token box only if there's no stored login at all
  if(!j.autoLogin && !j.hasToken)document.getElementById("token").style.display="";
  if(j.autoLogin)document.getElementById("status").textContent="Automātiska pieslēgšanās ✓";});
function showTab(id){
  document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("show",s.id===id));
  document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===id));
  if(id==="t-sales"&&chart)chart.resize();      // charts mis-size if drawn while hidden
  if(id==="t-day"&&hourChart)hourChart.resize();
}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.tab));

load();
loadHourly();
loadStock();
</script></body></html>"""


class DualStackServer(ThreadingHTTPServer):
    """Listen on both IPv4 and IPv6 so 'localhost' works no matter how it resolves."""
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


if __name__ == "__main__":
    os.chdir(HERE)
    print(f"Sales dashboard → http://localhost:{PORT}   (Ctrl+C to stop)")
    DualStackServer(("::", PORT), Handler).serve_forever()
