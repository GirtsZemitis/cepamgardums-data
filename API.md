# Xynetweb vending-machine API — reverse-engineered notes

The backend behind your machines (merchant **Ackord / 3553**). It's a Chinese smart-vending
SaaS — Vue SPA at `www.xynetweb.com`, API gateway at `xcx.xynetweb.com`. **No public docs
exist**; everything below was extracted from the app's JS bundles
(`static/js/app.*.js`, `static/js/kcpd.*.js`) and confirmed with one live read-only call.

> ⚠️ Unofficial. Endpoints/fields can change without notice. `kcpd` = 库存盘点 ("inventory
> stock-take"); most field names are Chinese pinyin abbreviations (glossary below).

## Connection basics
- **Base URL:** `https://xcx.xynetweb.com`
- **Method:** almost everything is `POST` with `Content-Type: application/json;charset=utf-8`
- **Auth:** header `Authorization: <token>` (raw token, **no** `Bearer` prefix). The token is
  the `session_key` returned by login (see below). It expires; the tooling auto-relogins.
- **Required headers seen:** `Origin: https://www.xynetweb.com`, `Referer: https://www.xynetweb.com/`

## Login (fully scriptable → automated token)
The token can be obtained programmatically — no browser needed (`auth.py` does this):

1. `GET /sram/comm/login/getCheckCode` → `{ "code":"H0000", "data": 2878 }`
   The "captcha" code is returned **in the JSON body** (not an image), so it's scriptable.
2. `POST /sram/comm/login/onLogin` with
   `{ "account", "password", "checkCode", "language":"en", "channel":"1" }`
   where the password is hashed (MD5, lowercase hex):
   ```
   inner = md5(account + plaintext_password)
   password = md5(account + inner + checkCode)      # checkCode from step 1
   ```
   So the captured hash is **single-use** (bound to that captcha).
3. Response `data.session_key` **is the Authorization token**. (Also returns `userInfo`,
   `menuList`, `customData`.)

Error codes seen: `B1006` captcha invalid/expired, `B1010` wrong account/password.
We store only `account` + `inner` (in git-ignored `.creds.json`), never the plaintext.

## Response envelope
```json
{ "code": "H0000", "data": { "list": [ ... ], "total": 0 }, "msg": "..." }
```
- Success: `code == "H0000"` (or numeric `200`).
- `code == "S9999"` → token expired/invalid → re-login.
- List endpoints return `data.list` + `data.total`; the SPA reads `.list` / `.total`.

## Inventory module (`/service-machine/kcpd/...`) — read endpoints
| Endpoint | Purpose |
|---|---|
| `inventoryOfLineGoods` | Stock per **cargo lane / product line** (the one you captured) |
| `inventoryOfLineGoodsDetails` | Drill-down for one line |
| `inventoryOfMachineCargo` | Stock per **machine cargo slot** |
| `inventoryOfMachineGoods` | Stock per **machine × product** |
| `getglyid` | List administrators (for the `glyid` filter) |
| `selectCkjq` / `selectBhls` | Query warehouses / replenishment lists |
| `queryBhjl` | Replenishment records |
| `bhjlDownload`, `merchantDownload`, `dcBhls`, `xlspkcxqDownload`, `spkcdc` | Excel/CSV exports |

Write endpoints exist too (`bindCkjq`, `bhyjsz`, `szqhyz`, `szspyz`, warehouse `add*/edit*/del*`)
— **not documented here on purpose; don't call them.**

### `POST /service-machine/kcpd/inventoryOfLineGoods`
Request body (all keys present; blanks act as "no filter"):
```json
{ "orderBy":"", "pageNum":1, "pageSize":20,
  "bhmc":"", "xldw":"", "spmc":"", "glyid":"", "type":"", "jqbh":"" }
```
| field | meaning |
|---|---|
| pageNum / pageSize | pagination |
| orderBy | sort spec |
| jqbh | **machine code** filter (e.g. `2202000072` — same IDs as in `orders`) |
| spmc | product-name search |
| bhmc | point/machine name search |
| xldw | line unit |
| glyid | administrator id (from `getglyid`) |
| type | view type |

Response: `data.list[]` of stock rows. Confirmed live fields (NULLs are fields unused in this view):
| field | meaning |
|---|---|
| **kcsl** | **current stock quantity** |
| **kcrl** | stock **capacity** (max for the lane) |
| **qhsl** | **shortage qty** (capacity − stock = how much to refill) |
| sjkysl | actual available qty |
| spbh / spmc | product code / name (e.g. `0017` = "400 g. Marinēti sīpoli") |
| sptxm / dsfspbh | barcode / third-party product code |
| spgg / spdw | spec / unit |
| jqbh / jqmc / jqxh / jqlx | machine code / name / model / type |
| **dwmc / dwid** | **point (location) name / id** ← maps machines to real kiosk locations |
| xlid / xlmc / hdbh | cargo-lane id / name / slot no. |
| shbh / shmc | merchant code / name |
| glyid / glyname | administrator |

## Why this is useful for us
1. **Fills the missing `location` gap.** `dwmc` (point name) + `jqbh` (machine code) finally
   links each machine serial to its real kiosk — exactly the mapping `build_dataset.py` left blank.
2. **Live restock intelligence.** `kcsl` (current), `kcrl` (capacity), `qhsl` (shortage) per lane
   → "which machine needs refilling, of what, right now." Pairs perfectly with the weekend demand
   curve (Fri–Sun spikes) we found in the order data.

## Glossary (pinyin → meaning)
jq=机器 machine · sp=商品 product · kc=库存 stock · qh=缺货 out-of-stock · xl=线路 line/lane ·
hd=货道 cargo-lane/slot · dw=点位 point/location · sh=商户 merchant · gly=管理员 admin ·
bh=编号 code/number · mc=名称 name · sl=数量 quantity · rl=容量 capacity · bh(补货)=replenish
