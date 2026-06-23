# Vending-machine order dataset

The sales book for the machines (merchant Ackord / 3553). **Not deployed** — local
business data only (git-ignored). Two ways to build it; the **API is now primary**.

## Primary: build from the live API (recommended)
`api_client.py` pulls valid orders straight from the Xynetweb API (see `API.md`),
caches raw rows in `api_cache.json` (keyed by order uuid → incremental & cheap),
and rebuilds `orders.csv` / `orders.db`.

```bash
# token expires — grab a fresh one from DevTools → Network → Authorization header
../.venv_orders/bin/python api_client.py --auth <TOKEN>            # incremental refresh
../.venv_orders/bin/python api_client.py --auth <TOKEN> --since 2026-05-01  # full rebuild
```
Handles the API's quirks automatically: weekly date chunks (long spans return 0),
pageSize cap, request throttling (paced + retried), and the single-day/today edge case.

## Local dashboard (easiest)
A local web app with the chart + today's per-machine/product sales + a Refresh button.

- **Double-click `run-dashboard.command`** (Finder) → it starts the server and opens
  http://localhost:8765 in your browser.
- Or run it manually: `../.venv_orders/bin/python app.py` then open that URL.
- Click **↻ Atjaunot datus** to pull the latest orders — **login is automatic** (see
  Auth below), so no token pasting.
- Shows a **live stock section** (📦 Atlikums automātos): per machine, each product as a
  row with total / sold-since-refill / remaining (low stock highlighted). Pulled live from
  the inventory API (`inventoryOfMachineGoods`); not cached.

## Auth (automatic)
`auth.py` logs in for you and refreshes the token automatically (incl. mid-fetch if it
expires). Credentials are stored once as account + a hash in git-ignored `.creds.json`
(never the plaintext password). See `API.md` for the login/hash details.

```bash
../.venv_orders/bin/python auth.py --set-creds   # store account + password once
../.venv_orders/bin/python auth.py               # print a fresh token (debug)
```
A `--auth <token>` still works as a manual override on `api_client.py`.

## Share a snapshot (single HTML file)
`export_report.py` bakes the current data into one self-contained `sales-report.html`
you can email/send. It opens in any browser (no Python), with the same 3 tabs
(📈 Pārdošana / ⏰ Diena / 📦 Atlikums), interactive toggles and day-picker. It's a
**frozen snapshot** — no live Refresh; re-run to make a fresh one. Charts load from a
CDN, so the recipient needs internet.

```bash
../.venv_orders/bin/python export_report.py                 # -> sales-report.html
../.venv_orders/bin/python export_report.py --out ~/Desktop/parskats.html
```
Note: a live, refreshable copy can't be a lone HTML file — it needs this folder, Python,
and your login. Share the snapshot instead.

## Reports / charts (standalone)
- `today_report.py` — per-machine sales for a day, by product & quantity
  (`--date YYYY-MM-DD`, default = latest day in the book).
- `build_chart.py` — regenerates the static `machine-sales.html` from `orders.db`.

## Legacy: build from Excel exports
`build_dataset.py` consolidates the fragmented `Order+Details*.xls` exports
(de-dupes by `order_number`). Superseded by the API path, kept for reference.

## Files
- `api_client.py` / `api_cache.json` — API fetch + raw cache (source of truth now).
- `orders.csv` — clean, one row per order line.
- `orders.db` — SQLite (table `orders` + reference `machines`). Best for querying.
- `API.md` — reverse-engineered API documentation.
- `Order+Details*.xls`, `build_dataset.py` — legacy export path.

## Schema (`orders`)
| column | notes |
|---|---|
| order_number | unique key, one product line per order |
| payment_time | `YYYY-MM-DD HH:MM:SS` |
| order_date / hour / weekday | derived from payment_time for easy grouping |
| machine_code | vending-machine serial (5 distinct) |
| location | human-readable machine location — **blank, fill in `MACHINE_LOCATIONS` in build_dataset.py** |
| product_name | product sold |
| product_number / cargo_lane | slot identifiers |
| price_eur | sale price (= original = paid; no discounts in data) |
| quantity | 1 for shipped, 0 for the 3 failed shipments |
| shipping_status | `Goods Shipped` or `Shipment failed` (filter on this) |
| source_file | provenance |

Dropped from raw (constant/redundant): merchant id/name (always 3553/Ackord),
machine_name (= machine_code), original_price & payment_amount (= price_eur),
discounted_price (0), purchaser/refund_*/remarks (empty), third-party ids (= order_number).

## Current snapshot
- 1218 unique orders (1465 raw rows; 247 duplicates removed)
- Range: 2026-05-01 → 2026-06-20
- 5 machines, 10 products, ~EUR 14,261 shipped revenue

## Example queries
```bash
sqlite3 -header -column orders.db "SELECT order_date, COUNT(*) orders, ROUND(SUM(price_eur),2) rev FROM orders WHERE shipping_status='Goods Shipped' GROUP BY order_date ORDER BY order_date;"
```
