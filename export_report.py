#!/usr/bin/env python3
"""Export a single self-contained HTML snapshot you can share (email/send a file).

Bakes the current data (sales chart, today, hourly, live stock) into one .html that
opens in any browser — no Python. Interactive (tabs, toggles, day-picker, time-frame)
but FROZEN at export time: no live Refresh. Re-run to refresh.

    ../.venv_orders/bin/python export_report.py            # -> sales-report.html
    ../.venv_orders/bin/python export_report.py --out ~/Desktop/parskats.html

Charts load from a CDN, so the recipient needs internet to render them.
"""
import argparse
import datetime
import json
import os

import app  # reuse the dashboard's data assembly (import does NOT start the server)

HERE = os.path.dirname(os.path.abspath(__file__))


def build(out):
    data = app.read_data()
    hourly = app.read_hourly()
    try:
        stock = app.compute_stock()
    except Exception as e:
        stock = {"machines": [], "error": str(e)}
    days = {d: app.read_day(d) for d in data.get("dates", [])}   # per-day breakdown, baked in
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = (TEMPLATE
            .replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
            .replace("/*HOURLY*/null", json.dumps(hourly, ensure_ascii=False))
            .replace("/*STOCK*/null", json.dumps(stock, ensure_ascii=False))
            .replace("/*DAYS*/null", json.dumps(days, ensure_ascii=False))
            .replace("__STAMP__", stamp))
    with open(out, "w") as f:
        f.write(html)
    print(f"Wrote {out}  ({os.path.getsize(out)//1024} KB)")
    print("Share this file — opens in any browser. Snapshot from", stamp)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="lv"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gaļas Nams — pārdošanas pārskats</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{color-scheme:dark}*{box-sizing:border-box}
  body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:22px;background:#0f1115;color:#e8eaed}
  h1{font-size:20px;margin:0 0 2px}.sub{color:#9aa0a6;font-size:13px;margin-bottom:6px}
  .stamp{color:#6b7280;font-size:12px;margin-bottom:12px}
  .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
  .group{display:flex;gap:4px;background:#11141a;border:1px solid #232733;border-radius:10px;padding:4px}
  button{background:transparent;color:#cbd1d8;border:none;border-radius:7px;padding:8px 13px;font-size:13px;cursor:pointer}
  button.active{background:#2563eb;color:#fff}
  select{background:#11141a;border:1px solid #232733;color:#e8eaed;border-radius:8px;padding:8px 10px;font-size:13px}
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
  .status{font-size:12px;color:#9aa0a6}.note{color:#6b7280;font-size:12px}
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
  .chev{transition:transform .15s;color:#cbd1d8;font-size:32px;line-height:1}.hcard.open .chev{transform:rotate(180deg)}
  .hdetail{display:none;margin-top:10px;border-top:1px solid #232733;padding-top:6px}.hcard.open .hdetail{display:block}
  @media(max-width:560px){.hbig{font-size:26px}.hcard{flex:1 1 100%}}
  .tabs{display:flex;gap:4px;margin:8px 0 16px;border-bottom:1px solid #232733}
  .tabs button{border-radius:8px 8px 0 0;padding:11px 18px;color:#9aa0a6;font-size:14px}
  .tabs button.active{color:#fff;border-bottom:2px solid #2563eb}
  .tab{display:none}.tab.show{display:block}
  @media(max-width:560px){body{padding:14px}.tabs button{padding:11px 10px;flex:1}.wrap{height:46vh}}
</style></head><body>
<h1>Pārdošanas pārskats</h1>
<div class="sub" id="sub"></div>
<div class="stamp">📸 Momentuzņēmums: __STAMP__ · dati nemainās (lai atjauninātu, ģenerē no jauna)</div>

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
  <p class="note" id="stockhead">Atlikums = no automāta (momentuzņēmuma brīdī). “Pārd. 7d” = reāli pārdots pēdējās 7 dienās. “Ietilp.” = konfigurētā ietilpība (informatīvi).</p>
  <div class="grid" id="stock"></div>
</section>

<script>
const DATA=/*DATA*/null, HOURLY=/*HOURLY*/null, STOCK=/*STOCK*/null, DAYS=/*DAYS*/null;
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
let metric="rev", gran="day", rangeDays=21, chart=null, hmetric="rev", hourChart=null, dayIdx=0;
const colorByLabel={};
const TOUCH_EVENTS=["mousemove","mouseout","click"];

function load(){
  if(!DATA||DATA.empty){$("sub").textContent="Nav datu.";return;}
  $("sub").textContent=`Periods: ${DATA.range[0]} — ${DATA.range[1]}  ·  ${DATA.series.length} automāti  ·  kopā €${DATA.total_revenue.toLocaleString("lv-LV")}`;
  setDay(DATA.dates.length-1); try{draw();}catch(e){}
}
function renderDay(idx){
  const dd=(DAYS[DATA.dates[idx]]||{machines:[]});
  const dmap={}; dd.machines.forEach(m=>dmap[m.label]=m);
  const el=$("hero"); el.innerHTML="";
  DATA.series.forEach((s,i)=>{                        // a card per machine; tap to expand its products
    const c=COLORS[i%5], m=dmap[s.label]||{qty:0,rev:0,products:[]};
    const rows=m.products.length
      ? m.products.map(p=>`<div class="row"><span>${shortName(p.name)}</span><span class="q">${p.qty} × · €${p.rev.toFixed(2)}</span></div>`).join("")
      : `<div class="row"><span class="q">— nav pārdošanas —</span></div>`;
    el.insertAdjacentHTML("beforeend",
      `<div class="hcard" style="border-top-color:${c}"><div class="hlabel" style="color:${c}">${s.label}</div>
       <div class="hbig">€${(m.rev||0).toFixed(2)}</div>
       <div class="hsub"><span>${m.qty||0} gab.</span><span class="chev">▾</span></div>
       <div class="hdetail">${rows}</div></div>`);
  });
}
function setDay(idx){
  const last=DATA.dates.length-1;
  dayIdx=Math.max(0,Math.min(idx,last));
  $("daylabel").textContent=DATA.dates[dayIdx]+(dayIdx===last?" · šodien":"");
  $("dprev").disabled=dayIdx<=0; $("dnext").disabled=dayIdx>=last; $("dtoday").disabled=dayIdx===last;
  renderDay(dayIdx);
}
function activeData(){
  const n=Math.min(rangeDays, DATA.dates.length), start=Math.max(0,DATA.dates.length-n);
  return {dates:DATA.dates.slice(start), series:DATA.series.map(s=>({label:s.label, rev:s.rev.slice(start), cnt:s.cnt.slice(start)}))};
}
function weekly(dates){const labels=[],bucket=[];let cur=null;
  dates.forEach(d=>{const dt=new Date(d+"T00:00:00");const wd=(dt.getDay()+6)%7;const mon=new Date(dt);mon.setDate(dt.getDate()-wd);const k=mon.toISOString().slice(0,10);if(k!==cur){cur=k;labels.push(k);}bucket.push(labels.length-1);});return{labels,bucket};}
function view(){const ad=activeData();
  if(gran==="day")return{labels:ad.dates,series:ad.series.map(s=>s[metric].slice()),labelsFor:ad.series.map(s=>s.label)};
  const{labels,bucket}=weekly(ad.dates);
  return{labels,series:ad.series.map(s=>{const v=Array(labels.length).fill(0);s[metric].forEach((x,i)=>v[bucket[i]]+=x);return v.map(x=>Math.round(x*100)/100);}),labelsFor:ad.series.map(s=>s.label)};}
const fmt=v=>metric==="rev"?"€"+v.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2}):v+" pas.";
function sets(){const vw=view();
  const m=vw.series.map((arr,i)=>({label:vw.labelsFor[i],data:arr,borderColor:COLORS[i%5],backgroundColor:COLORS[i%5]+"22",borderWidth:2,tension:.3,pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false}));
  const tot=vw.labels.map((_,j)=>Math.round(vw.series.reduce((a,arr)=>a+arr[j],0)*100)/100);
  m.unshift({label:"Kopā",data:tot,borderColor:"#e8eaed",borderDash:[6,4],borderWidth:3,tension:.3,pointRadius:gran==="week"?3:0,pointHoverRadius:5,fill:false});
  return{labels:vw.labels,datasets:m};}
function draw(){const d=sets();
  if(chart){chart.data.labels=d.labels;chart.data.datasets=d.datasets;chart.update();return;}
  chart=new Chart($("chart"),{type:"line",data:d,options:{responsive:true,maintainAspectRatio:false,events:TOUCH_EVENTS,interaction:{mode:"index",intersect:false},
    plugins:{legend:{labels:{color:"#e8eaed",usePointStyle:true}},tooltip:{callbacks:{label:c=>c.dataset.label+": "+fmt(c.parsed.y)}}},
    scales:{x:{ticks:{color:"#9aa0a6",maxTicksLimit:14,maxRotation:0},grid:{color:"#1f2430"}},y:{ticks:{color:"#9aa0a6",callback:v=>metric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});}
function loadHourly(){const sel=$("hourdate");if(!HOURLY.dates.length)return;
  sel.innerHTML=HOURLY.dates.map(d=>`<option>${d}</option>`).join("");
  sel.value=HOURLY.dates[HOURLY.dates.length-1];drawHourly();}
function drawHourly(){const d=$("hourdate").value;
  const series=(hmetric==="rev"?HOURLY.revenue:HOURLY.orders)[d]||[];
  const labels=Array.from({length:24},(_,h)=>String(h).padStart(2,"0"));
  const total=series.reduce((a,b)=>a+b,0);
  $("hourtot").textContent=hmetric==="rev"?`Kopā €${total.toLocaleString("lv-LV",{minimumFractionDigits:2,maximumFractionDigits:2})}`:`Kopā ${total} pas.`;
  const cfg={labels,datasets:[{data:series,backgroundColor:"#3b82f6",borderRadius:4}]};
  if(hourChart){hourChart.data=cfg;hourChart.update();return;}
  hourChart=new Chart($("hourchart"),{type:"bar",data:cfg,options:{responsive:true,maintainAspectRatio:false,events:TOUCH_EVENTS,
    plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>hmetric==="rev"?"€"+c.parsed.y:c.parsed.y+" pas."}}},
    scales:{x:{ticks:{color:"#9aa0a6"},grid:{display:false}},y:{ticks:{color:"#9aa0a6",callback:v=>hmetric==="rev"?"€"+v:v},grid:{color:"#1f2430"},beginAtZero:true}}}});}

function renderStock(){const el=$("stock");
  if(STOCK.error){el.innerHTML="<p class='note'>Atlikums nav pieejams: "+STOCK.error+"</p>";return;}
  if(!STOCK.machines.length){el.innerHTML="<p class='note'>Nav atlikuma datu.</p>";return;}
  el.innerHTML="";
  STOCK.machines.forEach((m,i)=>{const c=COLORS[i%5];
    const rows=m.products.map(p=>{const pct=p.capacity?p.remaining/p.capacity:1;const cls=p.remaining===0?"rem-out":(pct<=0.25?"rem-low":"rem-ok");
      return `<tr><td>${p.name}</td><td>${p.sold7}</td><td class="${cls}">${p.remaining}</td><td class="cap">${p.capacity}</td></tr>`;}).join("");
    el.insertAdjacentHTML("beforeend",`<div class="mcard" style="border-left-color:${c}"><h3 style="color:${c}">${m.label}</h3>
      <table class="stock"><thead><tr><th>Produkts</th><th>Pārd. 7d</th><th>Atlikums</th><th class="cap">Ietilp.</th></tr></thead><tbody>${rows}</tbody></table></div>`);});}

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
function showTab(id){document.querySelectorAll(".tab").forEach(s=>s.classList.toggle("show",s.id===id));
  document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===id));
  if(id==="t-sales"&&chart)chart.resize();if(id==="t-day"&&hourChart)hourChart.resize();}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.tab));

load();loadHourly();renderStock();
</script></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "sales-report.html"))
    build(ap.parse_args().out)
