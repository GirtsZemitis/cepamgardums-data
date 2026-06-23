#!/usr/bin/env python3
"""Build the sales book directly from the Xynetweb API (replaces the Excel exports).

Pulls valid orders from POST /service-order/ddxx/queryDdxx, caches the raw rows in
api_cache.json (keyed by order uuid, so re-runs are incremental and cheap), then
rebuilds the same orders.csv / orders.db sales book used by the chart.

API quirks handled here (discovered empirically):
  * Long date spans return nothing -> fetch in <=7-day (weekly) windows.
  * pageSize is capped (~50) -> paginate.
  * A single-day or future end-date returns nothing, but today's rows DO appear
    inside a multi-day window -> always refetch a trailing multi-day window.

Auth: the API token expires. Provide a fresh one (from DevTools -> Network ->
Authorization header) via  --auth <token>  or  env XYNET_AUTH. Never committed.

Usage:
    ../.venv_orders/bin/python api_client.py --auth <TOKEN>
    XYNET_AUTH=<TOKEN> ../.venv_orders/bin/python api_client.py
    ../.venv_orders/bin/python api_client.py --auth <TOKEN> --since 2026-05-01
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
import time
import urllib.request

import auth as auth_mod  # automated login -> token

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "api_cache.json")
BASE = "https://xcx.xynetweb.com"
ENDPOINT = "/service-order/ddxx/queryDdxx"

DEFAULT_SINCE = "2026-05-01"   # machines went live May 2026
OVERLAP_DAYS = 1              # re-pull this many days before the last cached day (safety)
PAGE_SIZE = 50
PAGE_CAP = 60                  # safety: max pages per window
REQ_DELAY = 1.3               # the API throttles rapid calls -> null; pace requests
RETRIES = 4                   # retry a null/empty page this many times before giving up

# Map vending-machine serials -> human location (fill in once known; see API dwmc field).
MACHINE_LOCATIONS = {
    "2202000072": "", "2506000017": "", "2506000018": "",
    "2512000367": "", "2512000368": "",
}
SHIP_STATUS = {"已出货": "Goods Shipped", "出货失败": "Shipment failed",
               "未出货": "Not shipped"}


def map_ship(s):
    """Map Chinese shipping status to English (status may carry a reason suffix)."""
    if not s:
        return ""
    for zh, en in SHIP_STATUS.items():
        if s.startswith(zh):
            return en
    return s


def api_post(token, path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json;charset=utf-8",
            "Authorization": token,
            "Origin": "https://www.xynetweb.com",
            "Referer": "https://www.xynetweb.com/",
            "Accept": "application/json, text/plain, */*",
        }, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def post(token, body):
    return api_post(token, ENDPOINT, body)


INV_ENDPOINT = "/service-machine/kcpd/inventoryOfMachineGoods"


def fetch_inventory(token=None):
    """Live per-machine stock: for each machine & product -> total/sold/remaining.

    total = kcrl (lane capacity), remaining = kcsl (current stock),
    sold  = qhsl (sold since last refill = total - remaining).
    """
    tok = [token or auth_mod.get_token()]
    rows, page = {}, 1
    while page <= PAGE_CAP:
        body = {"orderBy": "", "pageNum": page, "pageSize": PAGE_SIZE,
                "bhmc": "", "xldw": "", "spmc": "", "shbh": ""}
        batch, total = [], 0
        for _ in range(RETRIES):
            time.sleep(REQ_DELAY)
            resp = api_post(tok[0], INV_ENDPOINT, body)
            if resp.get("code") == "S9999":
                if auth_mod.have_creds():
                    tok[0] = auth_mod.login(); continue
                raise RuntimeError("Token expired and no stored creds.")
            data = resp.get("data") or {}
            batch = data.get("list") or data.get("data") or []
            total = data.get("total") or 0
            if batch:
                break
        if not batch:
            break
        for r in batch:
            rows[(str(r.get("jqbh")), r.get("spbh"))] = r
        if len(rows) >= total:
            break
        page += 1

    machines = {}
    for r in rows.values():
        mc = str(r.get("jqbh"))
        m = machines.setdefault(mc, {
            "machine_code": mc,
            "label": MACHINE_LOCATIONS.get(mc) or f"Automāts {mc}",
            "products": []})
        # kcsl = live current stock (reliable). kcrl = configured lane capacity
        # (operator setting; inconsistent across machines, so treat as informational).
        m["products"].append({"name": r.get("spmc"),
                              "remaining": r.get("kcsl") or 0,
                              "capacity": r.get("kcrl") or 0})
    for m in machines.values():
        m["products"].sort(key=lambda p: p["remaining"])   # low stock first
    return {"machines": sorted(machines.values(), key=lambda m: m["machine_code"])}


def get_page(tok, start, end, page):
    """Fetch one page, retrying on throttle-induced null. Returns (rows, total).

    `tok` is a one-element list holding the current token so we can re-login in
    place if it expires (S9999). Re-login needs stored creds (auth.py --set-creds).
    """
    body = {
        "pageNum": page, "pageSize": PAGE_SIZE, "queryType": 0,
        "jqmc": "", "shmc": "", "zjzt": "", "ywlx": "", "dsfshdh": "",
        "dsfjybh": "", "zfzt": "", "zffs": "", "zfzh": "", "chzt": "", "spxx": "",
        "starttime": f"{start} 00:00:00", "endtime": f"{end} 23:59:59",
    }
    last_total = 0
    for _ in range(RETRIES):
        time.sleep(REQ_DELAY)
        resp = post(tok[0], body)
        if resp.get("code") == "S9999":          # token expired
            if auth_mod.have_creds():
                tok[0] = auth_mod.login()        # auto re-login and retry
                continue
            sys.exit("Auth token expired (S9999) and no stored creds. Run auth.py --set-creds.")
        data = resp.get("data") or {}
        batch = data.get("data") or []
        last_total = data.get("total") or last_total
        if batch:
            return batch, data.get("total") or 0
    return [], last_total


def fetch_window(tok, start, end):
    """Return all order rows whose payment time falls in [start, end] (dates)."""
    rows = {}
    batch, total = get_page(tok, start, end, 1)
    if not batch:                      # genuinely empty window (after retries)
        return rows
    for r in batch:
        rows[r.get("uuid") or r.get("ddbh")] = r
    pages = min(PAGE_CAP, -(-total // PAGE_SIZE))   # ceil(total/PAGE_SIZE)
    for page in range(2, pages + 1):
        batch, _ = get_page(tok, start, end, page)
        for r in batch:
            rows[r.get("uuid") or r.get("ddbh")] = r
    return rows


def weekly_windows(since, today):
    w = since
    while w <= today:
        yield w, min(w + datetime.timedelta(days=6), today)
        w += datetime.timedelta(days=7)


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    return {"orders": {}, "fetched_through": None}


def last_data_date(orders):
    """Latest order date (YYYY-MM-DD) present in the cache, or None."""
    ds = [(r.get("zfsj") or r.get("cjsj") or "")[:10] for r in orders.values()]
    ds = [d for d in ds if d]
    return max(ds) if ds else None


def parse_extend2(e2):
    """'1KG Cūkas šašliks TRADICIONĀLAIS:003' -> (name, lane)."""
    if not e2:
        return "", ""
    if ":" in e2:
        name, lane = e2.rsplit(":", 1)
        return name.strip(), lane.strip()
    return e2.strip(), ""


def normalize(raw):
    """API row -> sales-book record (same schema as the export-based build)."""
    ts = raw.get("zfsj") or raw.get("cjsj") or ""
    dt = None
    if ts:
        try:
            dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = None
    name, lane = parse_extend2(raw.get("extend2"))
    pnum = str(raw.get("extend5") or "").strip()
    mc = str(raw.get("jqbh") or "")
    price = raw.get("spzj")
    if price is None:
        price = raw.get("zfje") or raw.get("ddzj") or 0
    return {
        "order_number": str(raw.get("ddbh") or ""),
        "payment_time": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "",
        "order_date": dt.strftime("%Y-%m-%d") if dt else "",
        "hour": dt.hour if dt else None,
        "weekday": dt.strftime("%A") if dt else "",
        "machine_code": mc,
        "location": MACHINE_LOCATIONS.get(mc, ""),
        "product_name": name,
        "product_number": pnum.zfill(4) if pnum else "",
        "cargo_lane": lane.zfill(3) if lane else "",
        "price_eur": round(float(price), 2),
        "quantity": int(raw.get("spsl") or 0),
        "shipping_status": map_ship(raw.get("showchzt")),
        "source_file": "api",
    }


def rebuild_book(orders):
    recs = [normalize(r) for r in orders.values()]
    recs = [r for r in recs if r["payment_time"]]
    recs.sort(key=lambda r: r["payment_time"])
    cols = ["order_number", "payment_time", "order_date", "hour", "weekday",
            "machine_code", "location", "product_name", "product_number",
            "cargo_lane", "price_eur", "quantity", "shipping_status", "source_file"]

    import csv
    with open(os.path.join(HERE, "orders.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(recs)

    db = os.path.join(HERE, "orders.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute(f"CREATE TABLE orders ({', '.join(c+' TEXT' for c in cols)})")
    con.executemany(
        f"INSERT INTO orders VALUES ({','.join('?' for _ in cols)})",
        [[r[c] for c in cols] for r in recs])
    machines = sorted({(r["machine_code"], r["location"]) for r in recs})
    con.execute("CREATE TABLE machines (machine_code TEXT, location TEXT, order_lines INT)")
    for mc, loc in machines:
        n = sum(1 for r in recs if r["machine_code"] == mc)
        con.execute("INSERT INTO machines VALUES (?,?,?)", (mc, loc, n))
    con.executescript(
        "CREATE INDEX idx_date ON orders(order_date);"
        "CREATE INDEX idx_machine ON orders(machine_code);"
        "CREATE INDEX idx_product ON orders(product_name);")
    con.commit()
    con.close()
    return recs


def run_fetch(auth=None, since=None, today=None, log=print):
    """Fetch orders into the cache and rebuild the book. Returns a summary dict.

    Token resolution: explicit `auth` token > automated login via stored creds >
    cached .token file. since/today are date or 'YYYY-MM-DD' strings.
    """
    # Resolve a token: explicit arg (caller already logged in) > fresh login > cached.
    if auth:
        token = auth
    elif auth_mod.have_creds():
        token = auth_mod.login()
    else:
        token = auth_mod.get_token()
    tok = [token]

    if isinstance(today, str):
        today = datetime.date.fromisoformat(today)
    today = today or datetime.date.today()
    cache = load_cache()
    orders = cache["orders"]

    if since:
        since = since if isinstance(since, datetime.date) else datetime.date.fromisoformat(since)
    elif orders:
        # incremental: continue from the last day we already have (never re-fetch
        # older completed days); minus a small safety overlap. Backfills any gap.
        last = last_data_date(orders)
        since = (datetime.date.fromisoformat(last) - datetime.timedelta(days=OVERLAP_DAYS)
                 if last else datetime.date.fromisoformat(DEFAULT_SINCE))
    else:
        since = datetime.date.fromisoformat(DEFAULT_SINCE)
    since = min(since, today)

    log(f"Fetching {since} -> {today} (weekly windows)…")
    n0 = len(orders)
    windows = list(weekly_windows(since, today))
    trailing = (today - datetime.timedelta(days=1), today)   # 2-day window guarantees today
    if trailing not in windows:
        windows.append(trailing)
    for s, e in windows:
        got = fetch_window(tok, s.isoformat(), e.isoformat())
        orders.update(got)
        log(f"  {s} … {e}: +{len(got)} rows (cache={len(orders)})")

    log("Rebuilding sales book…")
    cache["orders"] = orders
    cache["fetched_through"] = today.isoformat()
    with open(CACHE, "w") as f:
        json.dump(cache, f)

    recs = rebuild_book(orders)
    shipped = [r for r in recs if r["shipping_status"] == "Goods Shipped"]
    return {
        "total_orders": len(orders),
        "new_orders": len(orders) - n0,
        "range": [recs[0]["order_date"], recs[-1]["order_date"]] if recs else None,
        "shipped_revenue": round(sum(r["price_eur"] for r in shipped), 2),
        "fetched_through": today.isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", default=os.environ.get("XYNET_AUTH"),
                    help="API token (optional; auto-login via stored creds if omitted)")
    ap.add_argument("--since", default=None, help="earliest date YYYY-MM-DD")
    ap.add_argument("--today", default=None, help="override 'today' YYYY-MM-DD")
    args = ap.parse_args()
    if not args.auth and not auth_mod.have_creds():
        sys.exit("No token and no stored creds. Run: auth.py --set-creds")

    s = run_fetch(args.auth, since=args.since, today=args.today)
    print(f"\nCache: {s['total_orders']} orders ({s['new_orders']} new this run)")
    if s["range"]:
        print(f"Range: {s['range'][0]} -> {s['range'][1]}")
    print(f"Shipped revenue: EUR {s['shipped_revenue']:.2f}")
    print("Wrote orders.csv, orders.db, api_cache.json")


if __name__ == "__main__":
    main()
