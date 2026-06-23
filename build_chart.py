#!/usr/bin/env python3
"""Generate machine-sales.html — interactive per-machine sales chart from orders.db.

Reads shipped orders, builds daily per-machine series, embeds them in a
self-contained HTML page (Chart.js via CDN). Re-run after rebuilding orders.db:
    ../.venv_orders/bin/python build_chart.py
"""
import os
import sqlite3
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def build():
    con = sqlite3.connect(os.path.join(HERE, "orders.db"))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT order_date, machine_code, ROUND(SUM(price_eur),2) rev, COUNT(*) cnt
        FROM orders WHERE shipping_status='Goods Shipped'
        GROUP BY order_date, machine_code
    """).fetchall()
    machines = [dict(r) for r in con.execute(
        "SELECT machine_code, location FROM machines ORDER BY machine_code")]
    con.close()

    dmin = min(r["order_date"] for r in rows)
    dmax = max(r["order_date"] for r in rows)
    d0 = datetime.date.fromisoformat(dmin)
    d1 = datetime.date.fromisoformat(dmax)
    dates = [(d0 + datetime.timedelta(days=i)).isoformat()
             for i in range((d1 - d0).days + 1)]

    idx = {(r["order_date"], str(r["machine_code"])): r for r in rows}

    def label(m):
        return m["location"] if m["location"] else f"Automāts {m['machine_code']}"

    series = []
    for m in machines:
        mc = str(m["machine_code"])
        series.append({
            "code": mc, "label": label(m),
            "rev": [idx[(d, mc)]["rev"] if (d, mc) in idx else 0 for d in dates],
            "cnt": [idx[(d, mc)]["cnt"] if (d, mc) in idx else 0 for d in dates],
        })

    payload = json.dumps({"dates": dates, "series": series, "range": [dmin, dmax]})
    html = HTML_TEMPLATE.replace("/*__DATA__*/null", payload)
    out = os.path.join(HERE, "machine-sales.html")
    with open(out, "w") as f:
        f.write(html)
    print("Wrote", out)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="lv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gaļas Nams — pārdošana pa automātiem</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif;
         margin: 0; padding: 24px; background:#0f1115; color:#e8eaed; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color:#9aa0a6; font-size:13px; margin-bottom:16px; }
  .controls { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; align-items:center; }
  .group { display:flex; gap:4px; background:#11141a; border:1px solid #232733; border-radius:10px; padding:4px; }
  button { background:transparent; color:#cbd1d8; border:none; border-radius:7px;
           padding:7px 13px; font-size:13px; cursor:pointer; }
  button.active { background:#2563eb; color:#fff; }
  .card { background:#171a21; border:1px solid #232733; border-radius:14px; padding:16px; }
  .wrap { position:relative; height:60vh; min-height:360px; }
  .kpis { display:flex; gap:12px; flex-wrap:wrap; margin:14px 0 0; }
  .kpi { background:#171a21; border:1px solid #232733; border-radius:12px; padding:10px 14px; font-size:13px; }
  .kpi b { display:block; font-size:18px; margin-top:2px; }
  .note { color:#6b7280; font-size:12px; margin-top:14px; }
</style>
</head>
<body>
  <h1>Pārdošana pa automātiem</h1>
  <div class="sub" id="rangelbl"></div>
  <div class="controls">
    <div class="group">
      <button id="m-rev" class="active">Ieņēmumi (€)</button>
      <button id="m-cnt">Pasūtījumi</button>
    </div>
    <div class="group">
      <button id="g-day">Dienas</button>
      <button id="g-week" class="active">Nedēļas</button>
    </div>
  </div>
  <div class="card"><div class="wrap"><canvas id="chart"></canvas></div></div>
  <div class="kpis" id="kpis"></div>
  <div class="note">Tikai izsniegtie pasūtījumi. Nedēļas skats summē pirmdiena–svētdiena (mazina nedēļas nogales svārstības).</div>

<script>
const DATA = /*__DATA__*/null;
const COLORS = ["#3b82f6","#ef4444","#10b981","#f59e0b","#a855f7"];
let metric = "rev";   // rev | cnt
let gran = "week";    // day | week

document.getElementById("rangelbl").textContent =
  "Periods: " + DATA.range[0] + " — " + DATA.range[1] + "  ·  " + DATA.series.length + " automāti";

// Group daily values into ISO weeks (Monday start). Returns {labels, map: series->values}.
function weekly() {
  const labels = [], bucketOf = [];
  let curKey = null, idxMap = [];
  DATA.dates.forEach((d) => {
    const dt = new Date(d + "T00:00:00");
    const day = (dt.getDay() + 6) % 7;            // Mon=0..Sun=6
    const monday = new Date(dt); monday.setDate(dt.getDate() - day);
    const key = monday.toISOString().slice(0, 10);
    if (key !== curKey) { curKey = key; labels.push(key); }
    bucketOf.push(labels.length - 1);
  });
  return { labels, bucketOf };
}

function buildView() {
  if (gran === "day") {
    return { labels: DATA.dates, series: DATA.series.map(s => s[metric].slice()) };
  }
  const { labels, bucketOf } = weekly();
  const series = DATA.series.map(s => {
    const v = new Array(labels.length).fill(0);
    s[metric].forEach((x, i) => { v[bucketOf[i]] += x; });
    return v.map(x => Math.round(x * 100) / 100);
  });
  return { labels, series };
}

function datasets() {
  const view = buildView();
  const machineSets = DATA.series.map((s, i) => ({
    label: s.label,
    data: view.series[i],
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: COLORS[i % COLORS.length] + "22",
    borderWidth: 2, tension: 0.3,
    pointRadius: gran === "week" ? 3 : 0, pointHoverRadius: 5, fill: false,
  }));
  const totalData = view.labels.map((_, j) =>
    Math.round(DATA.series.reduce((a, s, i) => a + view.series[i][j], 0) * 100) / 100);
  const totalSet = {
    label: "Kopā (visi automāti)",
    data: totalData,
    borderColor: "#e8eaed",
    backgroundColor: "transparent",
    borderWidth: 3, borderDash: [6, 4], tension: 0.3,
    pointRadius: gran === "week" ? 3 : 0, pointHoverRadius: 5, fill: false,
  };
  return { labels: view.labels, datasets: [totalSet, ...machineSets] };
}

const fmt = v => metric === "rev"
  ? "€" + v.toLocaleString("lv-LV", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : v + " pas.";

const init = datasets();
const chart = new Chart(document.getElementById("chart"), {
  type: "line",
  data: init,
  options: {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { labels: { color: "#e8eaed", usePointStyle: true } },
      tooltip: { callbacks: { label: c => c.dataset.label + ": " + fmt(c.parsed.y) } },
    },
    scales: {
      x: { ticks: { color: "#9aa0a6", maxRotation: 0, autoSkip: true, maxTicksLimit: 14 }, grid: { color: "#1f2430" } },
      y: { ticks: { color: "#9aa0a6", callback: v => metric === "rev" ? "€" + v : v }, grid: { color: "#1f2430" }, beginAtZero: true },
    },
  },
});

function refresh() {
  const d = datasets();
  chart.data.labels = d.labels;
  chart.data.datasets = d.datasets;
  chart.update();
  renderKpis();
}

function renderKpis() {
  const el = document.getElementById("kpis"); el.innerHTML = "";
  const grand = DATA.series.reduce((a, s) => a + s[metric].reduce((x, y) => x + y, 0), 0);
  const td = document.createElement("div"); td.className = "kpi";
  td.style.borderColor = "#e8eaed";
  td.innerHTML = "<span style='color:#e8eaed'>▦ </span>Kopā (visi automāti)" +
    "<b>" + (metric === "rev" ? fmt(Math.round(grand * 100) / 100) : grand + " pas.") + "</b>";
  el.appendChild(td);
  DATA.series.forEach((s, i) => {
    const total = s[metric].reduce((a, b) => a + b, 0);
    const d = document.createElement("div"); d.className = "kpi";
    d.innerHTML = "<span style='color:" + COLORS[i % COLORS.length] + "'>● </span>" + s.label +
      "<b>" + (metric === "rev" ? fmt(Math.round(total * 100) / 100) : total + " pas.") + "</b>";
    el.appendChild(d);
  });
}

function setActive(on, off) { document.getElementById(on).classList.add("active"); document.getElementById(off).classList.remove("active"); }
document.getElementById("m-rev").onclick = () => { metric = "rev"; setActive("m-rev", "m-cnt"); refresh(); };
document.getElementById("m-cnt").onclick = () => { metric = "cnt"; setActive("m-cnt", "m-rev"); refresh(); };
document.getElementById("g-day").onclick = () => { gran = "day"; setActive("g-day", "g-week"); refresh(); };
document.getElementById("g-week").onclick = () => { gran = "week"; setActive("g-week", "g-day"); refresh(); };

renderKpis();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
