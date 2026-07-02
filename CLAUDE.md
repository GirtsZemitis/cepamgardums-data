# CLAUDE.md

Guidance for working in this repo.

## What this is

Internal **sales dashboard** for **Gaļas Nams** vending machines — pulls order & stock
data from the machines' backend (Xynet), caches it, and shows a per-machine chart,
daily totals, hourly breakdown, and live stock. In Latvian.

This is a **separate app** from the marketing site:
- `cepamgardums.lv` → the public marketing site (different repo: `CepamGardums`, Azure Static Web Apps).
- **`data.cepamgardums.lv` → this dashboard** (Azure Container App). Password-protected, not public.

## Stack

- **Pure Python standard library** at runtime — `http.server` + `sqlite3` + `urllib`. No web
  framework, **no pip deps** to run the dashboard (the container only adds `tzdata`).
- **Chart.js** from a CDN in the browser.
- Data source: the **Xynet API** (reverse-engineered — see `API.md`).

## Run locally

```bash
python3 app.py          # -> http://localhost:8765
```
Set credentials once so refresh works: `python3 auth.py --set-creds`.
(The `../.venv_orders` virtualenv is only for the **legacy** Excel importer, which needs
pandas/xlrd; the dashboard itself needs none of that.)

## Key files

| File | Role |
|---|---|
| `app.py` | The dashboard web server + page (HTML/JS inline). Page loads read only the cache. |
| `api_client.py` | Fetch orders from Xynet → `orders.db` / `orders.csv` / `api_cache.json` (incremental, paced). |
| `auth.py` | Automated login → token. Creds from env or `.creds.json`. |
| `export_report.py` | Build a shareable static snapshot `sales-report.html`. |
| `API.md` | Reverse-engineered Xynet API + login/hash docs. |
| `Dockerfile` | Container image (listens on **80**). |
| `.github/workflows/build-image.yml` | Build + push private image to ghcr.io on push to `master`. |

## Data / caching model (important)

- **Page loads never hit the Xynet API** — they serve `orders.db` + in-memory `STATE`
  (cached stock + `last_refresh`).
- The API is touched **only by a refresh**, which is **rate-limited to once per 5 min**
  (`REFRESH_MIN_INTERVAL`) and lock-serialized — this is deliberate, to not hammer Xynet.
- Refresh runs on the ↻ button (live SSE progress), on page load if data is stale, and on
  startup if `AUTO_REFRESH=1`.
- Refreshed data is **not persisted across container restarts** (ephemeral FS); it falls back
  to the committed `orders.db`, then re-fetches.

## Deploy (Azure Container App, image from ghcr.io — no paid registry)

Push to `master` → GitHub Action builds & pushes `ghcr.io/girtszemitis/cepamgardums-data:latest`
(private). The Container App (`data-app`, resource group `ackord`) pulls it with a
`read:packages` GitHub token. **Ingress targetPort = 80.**

Env vars to set in Azure:
- `XYNET_ACCOUNT`, `XYNET_INNER` (= `md5(account+password)`; or `XYNET_PASSWORD`) — enables login/refresh.
- `DASH_PASSWORD` (and optional `DASH_USER`) — **password-gates the dashboard; always set it** (URL is public).
- `AUTO_REFRESH=1` — pull fresh data on startup.

## Gotchas

- **Secrets never committed:** `.creds.json`, `.token`, `state.json` are git-ignored. In the
  cloud, credentials come from env vars.
- **"Today" uses Riga time** (server is UTC). The current day always shows, with €0 until
  sales arrive; `tzdata` is installed in the image for the timezone lookup.
- **Quantity is always 1** for shipped orders, so order-count == units sold.
- **Short product names** in card detail come from `NAME_MAP` in `app.py`/`export_report.py`
  (unmapped names fall back to the full name).
- The dashboard HTML/CSS/JS is **duplicated** between `app.py` (live) and `export_report.py`
  (static snapshot) — apply UI changes to both.
