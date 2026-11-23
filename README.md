# caravan-pricelist

Supplier price lists for [caravansanat.com](https://caravansanat.com): a supplier sends whatever he has — a PDF
catalog, an Excel sheet, a photo of a printed list, or single product photos with the price on them — and this
service turns it into rows matched to **that supplier's own offers** on the site. The site shows the rows
(green = sure, yellow = a person decides, new = not on the site yet) and applies the approved prices itself.

Reading and matching are copied (and trimmed) from `product-crawler` (`crawler/pricelist.py`), so the two stay
independent: this service never touches the site's database and never writes prices.

## API

| | |
|---|---|
| `GET /health` | `{"ok": true, "model": "...", "key": true}` |
| `POST /jobs` | multipart: `files` (1–40 × PDF/xlsx/csv/jpg/png/webp, ≤ 30 MB) + `meta` (JSON, below) → `{"id": "..."}` |
| `GET /jobs/{id}` | status `queued → reading → matching → done / failed`, `progress`, and `result` when done |

Every call but `/health` needs the header `X-PL-Token`.

`meta`:
```json
{"supplier_id": 851, "unit_hint": "auto|toman|rial", "big_pct": 20,
 "offers": [{"offer_id": 1, "product_id": 2, "variation_id": 0, "name": "...", "sku": "...", "toman": 4800000}],
 "aliases": {"<row key>": "<offer_id>"},
 "callback_url": "https://.../optional"}
```

`result.rows[]`: `key, model, name, price (as printed), unit, toman, stock (yes|no|''), list_date, file, note,
status (green|yellow|new), how, offers[] (the match), cands[] (choices), change_pct, big`.
`result.not_in_list[]`: the supplier's offers no row reached.

## Environment (Coolify)

| variable | |
|---|---|
| `OPENROUTER_API_KEY` | vision/text model (OpenRouter) — **never in git** |
| `PL_TOKEN` | shared secret with the site — **never in git** |
| `PL_MODEL` | default `deepseek/deepseek-v4.1-flash` |
| `PL_DATA_DIR` | default `/data` (mount a volume) |
| `PL_KEEP_DAYS` | uploaded files are deleted after this many days (default 30) |

Supplier files are business data: they stay on the server volume and are git-ignored.
