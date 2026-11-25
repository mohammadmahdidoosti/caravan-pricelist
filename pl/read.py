"""Files of one price list -> rows {model, name, price, unit, toman, stock, ...}.

Copied and trimmed from product-crawler/crawler/pricelist.py (23 Nov 2026): pictures and PDF pages are read by a
vision model (one call per picture/page), Excel/CSV by its columns (several product blocks side by side, a stock
column, rows in red = out of stock); a sheet the columns cannot explain goes to the model as text. A PDF with a text
layer checks every read price against its own page text (free).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import llm
from .text import FA_DIGITS, list_date_rank, model_key, page_nums, row_key, to_toman

log = logging.getLogger(__name__)

VISION_SYSTEM = """You read Iranian supplier price lists and catalogs (posters, catalog pages, tables, a photo of a
printed list, or a single product photo with its price written on it).
Return JSON: {"list_date": "<date or month and year printed, e.g. آبان ۱۴۰۵ or 1405/08/01, or empty>",
 "brand": "<the maker or supplier named on the page, as printed, or empty>",
 "rows": [{"model": "<the product's model code ONLY when one is printed as an identifier (letters and/or digits such as
            VR2402-BCK, LE-710, P3355, 2119); empty when the row has no model code. Never put a size, a size range
            (90-315, 1/2 اینچ) or a row number here>",
           "name": "<the row's full product name as printed, with its sizes and variant words>",
           "size": "<this row's size as printed, with its column header word when the size sits in its own column
            (قطر خارجی 90, سایز 40, 1/2 اینچ, 110-45), or empty when the row has no size>",
           "brand": "<maker of this row if it differs from the page's, else empty>",
           "price": "<the price digits exactly as printed, or empty when the row shows no price>",
           "unit": "rial|toman|thousand_toman|thousand_rial|unknown",
           "vat": <true only when the price you copied includes VAT (با احتساب ارزش افزوده) and no price without it is printed>,
           "stock": "<yes|no from a stock column or words (موجود, ناموجود, 0, ✓, ✗); empty when the page says nothing>"}]}
One row for EVERY product line, also rows without a model code or without a price.
GRID TABLES: when a table is a grid (product types down the side, sizes in a column, and several price columns for
variants such as استیل / آلومینیوم / گالوانیزه), give one row per printed price and build its name from the product
type + the size (with the size's header word, e.g. «سایز 40») + the column's variant word, e.g.
«شیلنگ دوسر مهره سایز 40 استیل». A product type written once for a block of rows applies to every row of that block.
SIZE COLUMNS: when the size is printed in its own column (قطر خارجی, سایز, اندازه, قطر, diameter) beside a product
picture or a block title, every row's name MUST still carry its own size, e.g. «رابط انبساطی (موفه) قطر خارجی 90»;
rows of one block differ only by their size, so a name without the size is wrong.
TITLES IN A FANCY FONT: a block's product name is often printed once, big and decorative, beside the table. Read it
letter by letter; it is a real trade word (کلاهک, بوشن ترمزدار, کمربند تعمیری, رابط انبساطی, سه راه, زانو), never a
shortened or invented one.
Copy digits exactly: every digit and every group of three, never drop or add a zero. A page prints its prices with the
same number of digits in a column; a price much shorter than its neighbours is a misread. Iranian lists often group
digits with a slash or a dot instead of a comma: «۱/۴۹۹/۰۰۰» or 1.499.000 is 1499000; never read the slash as a decimal
point or drop a group. When a row has several prices for buyers, copy the consumer price (مصرف کننده / خرده) rather than
the dealer price (همکار / عمده), and the price WITHOUT VAT rather than with it. The unit comes from the words near the
prices (ریال = rial, تومان = toman, هزار تومان = thousand_toman); a note such as «قیمت‌ها به ریال» applies to the page.
Leave out cartons, counts, row numbers, phone numbers and other numbers that are not the price. No prose, only the JSON."""

CHECK_SYSTEM = """You check a price list page. Return JSON only: {"rows": [{"model": "<model code exactly as printed>",
 "price": "<its price digits exactly as printed, or empty>"}]} for EVERY row that has a model code, in page order. Read
each price digit by digit; Iranian lists group digits with a slash or a dot («۱/۴۹۹/۰۰۰», 1.499.000). Never invent a row:
a code that is not printed on this page must not appear."""

TEXT_USER = "Read every priced product of this price list (a spreadsheet as text, one line per row, cells separated by ' | ')."

STOCK_WORDS = ("موجودی", "وضعیت", "موجود", "stock", "available")
NO_STOCK = re.compile(r"ناموجود|نا موجود|عدم موجود|اتمام|تمام شد|out\s*of\s*stock|^\s*(0|no|خیر|✗|×|-)\s*$", re.I)
PICTURE_EXT = (".jpg", ".jpeg", ".png", ".webp")
TABLE_EXT = (".xlsx", ".xlsm", ".csv")


# ------------------------------------------------------------------ pictures

def images_of(f: Path) -> list[tuple[str, bytes, str]]:
    """(label, picture bytes, mime) of one file: a picture as it is (made smaller when huge), a PDF page by page."""
    low = f.name.lower()
    if low.endswith(PICTURE_EXT):
        return [(f.name, _fit(f.read_bytes()), "image/jpeg")]
    if low.endswith(".pdf"):
        import pdfplumber
        out = []
        with pdfplumber.open(str(f)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                buf = io.BytesIO()
                page.to_image(resolution=200).original.convert("RGB").save(buf, "JPEG", quality=85)
                out.append((f"{f.name} ص{i}", buf.getvalue(), "image/jpeg"))
        return out
    return []


def _fit(pic: bytes, side: int = 2200) -> bytes:
    """A phone photo of 4000 px is cut to 2200 px on its long side (still sharp for digits) and sent as JPEG."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(pic))).convert("RGB")
        if max(im.size) > side:
            im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:                                    # noqa: BLE001 - send it as it came
        return pic


# ------------------------------------------------------------------ spreadsheets

def _red(cell) -> bool:
    """A cell written or filled in red (a supplier's way of saying out of stock)."""
    for col in (getattr(cell.font, "color", None) if cell.font else None,
                cell.fill.fgColor if cell.fill is not None and cell.fill.fill_type else None):
        try:
            rgb = col.rgb if col is not None else None
            if isinstance(rgb, str) and len(rgb) >= 6:
                r, g, b = int(rgb[-6:-4], 16), int(rgb[-4:-2], 16), int(rgb[-2:], 16)
                if r >= 0xC0 and g <= 0x60 and b <= 0x60:
                    return True
        except (ValueError, TypeError, AttributeError):
            continue
    return False


def _grid(f: Path) -> tuple[list[list], list[list[bool]]]:
    """Cell values and 'written in red' of the first sheet (or the CSV)."""
    if f.name.lower().endswith(".csv"):
        raw = f.read_bytes()
        for enc in ("utf-8-sig", "cp1256", "utf-16"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            txt = raw.decode("utf-8", "ignore")
        rows = list(csv.reader(io.StringIO(txt)))
        return rows, [[False] * len(r) for r in rows]
    import openpyxl
    ws = openpyxl.load_workbook(f, data_only=True).active
    vals, reds = [], []
    for r in ws.iter_rows():
        vals.append([c.value for c in r])
        reds.append([_red(c) for c in r])
    return vals, reds


def _is(h: str, *words) -> bool:
    return any(w in h for w in words)


def table_rows(f: Path) -> list[dict]:
    """Excel/CSV by its headers. A sheet may hold two or more product blocks side by side (the same headers again
    further along): each name (or model) column takes the nearest free price column. Empty list when unclear."""
    vals, reds = _grid(f)
    if not vals:
        return []
    width = max(len(r) for r in vals)
    vals = [list(r) + [None] * (width - len(r)) for r in vals]
    reds = [list(r) + [False] * (width - len(r)) for r in reds]
    hi = next((i for i, r in enumerate(vals[:15]) if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 2), None)
    if hi is None:
        return []
    head = [str(c or "").strip().lower() for c in vals[hi]]
    prices = [i for i, h in enumerate(head) if _is(h, "قیمت", "price", "مبلغ", "فی")]
    names = [i for i, h in enumerate(head) if _is(h, "نام", "شرح", "عنوان", "name", "product", "کالا", "محصول")
             and i not in prices]
    models = [i for i, h in enumerate(head) if _is(h, "مدل", "model", "کد", "code") and i not in prices and i not in names]
    anchors = names or models
    if not anchors or not prices:
        return []
    free, pairs = set(prices), []
    for a in anchors:
        if not free:
            break
        p = min(free, key=lambda j: (abs(j - a), j))
        free.discard(p)
        pairs.append((a, p))
    out = []
    for a, p in pairs:
        lo, hi_ = min(a, p), max(a, p)
        m = next((j for j in models if abs(j - a) <= 2 and j != a), a if a in models else None)
        s = next((j for j, h in enumerate(head) if lo - 2 <= j <= hi_ + 2 and _is(h, *STOCK_WORDS)
                  and j not in prices), None)
        unit = "rial" if "ریال" in head[p] else "thousand_toman" if "هزار" in head[p] else "toman" if "تومان" in head[p] else "unknown"
        for r_i, row in enumerate(vals[hi + 1:], start=hi + 1):
            name = str(row[a] if row[a] is not None else "").strip()
            model = str(row[m] if m is not None and row[m] is not None else "").strip() if m != a else ""
            if a in models and not names:
                model, name = name, ""
            raw = row[p]
            if not (name or model) or name.lower() == "nan":
                continue
            price = "" if raw is None else (str(int(raw)) if isinstance(raw, float) and raw == int(raw) else str(raw).strip())
            stock = ""
            if price and NO_STOCK.search(price) and not re.search(r"\d", price.translate(FA_DIGITS)):
                price, stock = "", "no"
            if s is not None and row[s] is not None and str(row[s]).strip():
                stock = "no" if NO_STOCK.search(str(row[s])) else "yes"
            if any(reds[r_i][lo:hi_ + 1]):
                stock = "no"
            if not price and not stock:
                continue
            out.append({"brand": "", "model": model, "name": name, "price": price, "unit": unit, "stock": stock,
                        "vat": "", "list_date": ""})
    return out


def sheet_text(f: Path, max_rows: int = 600) -> str:
    vals, _ = _grid(f)
    lines = []
    for r in vals[:max_rows]:
        cells = [str(c).strip() for c in r if c is not None and str(c).strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


# ------------------------------------------------------------------ one list

def _nums(s: str) -> list[str]:
    return re.findall(r"\d+(?:[./]\d+)?", s.translate(FA_DIGITS))


def _with_size(name: str, size: str) -> str:
    """The row's size joins its name when the model left it out (a size column beside a block of rows)."""
    name, size = name.strip(), size.strip()
    want = _nums(size)
    if not want or not name:
        return name or size
    have = set(_nums(name))
    if all(n in have for n in want):
        return name
    return f"{name} {size}"


def _rows_of(j: dict | None, label: str) -> list[dict]:
    out = []
    for r in (j or {}).get("rows") or []:
        if isinstance(r, dict):
            out.append({"brand": str(r.get("brand") or "").strip() or str((j or {}).get("brand") or ""),
                        "model": str(r.get("model") or ""), "name": _with_size(str(r.get("name") or ""), str(r.get("size") or "")),
                        "price": str(r.get("price") or ""), "unit": str(r.get("unit") or "unknown"),
                        "vat": "yes" if r.get("vat") is True else "",
                        "stock": str(r.get("stock") or "").strip().lower(),
                        "list_date": str((j or {}).get("list_date") or ""), "file": label})
    return out


def read_files(files: list[Path], unit_hint: str = "auto", progress=None) -> dict:
    """Every file -> merged rows. {'rows': [...], 'usd': .., 'calls': .., 'failed': [...], 'pages': ..}"""
    rows, usd, calls, failed, pics = [], 0.0, 0, [], []
    check_usd = 0.0                                  # the second reading's share of usd
    for f in files:
        low = f.name.lower()
        try:
            if low.endswith(TABLE_EXT):
                got = table_rows(f)
                if got:
                    rows += [{**r, "file": f.name} for r in got]
                    continue
                text = sheet_text(f)                     # columns not understood: the model reads the sheet as text
                if not text:
                    failed.append(f"{f.name}: فایل خالی است")
                    continue
                for i in range(0, len(text), 8000):
                    j, c = llm.ask([{"role": "system", "content": VISION_SYSTEM},
                                    {"role": "user", "content": TEXT_USER + "\n\n" + text[i:i + 8000]}], max_tokens=8000)
                    usd, calls = usd + c, calls + 1
                    if j is None:
                        failed.append(f"{f.name}: خوانده نشد")
                    rows += _rows_of(j, f.name)
                continue
            got = images_of(f)
            if not got:
                failed.append(f"{f.name}: نوع فایل پشتیبانی نمی‌شود")
            pics += [(f, *x) for x in got]
        except Exception as e:                           # noqa: BLE001 - one file never stops the others
            log.warning("read %s: %s", f.name, e)
            failed.append(f"{f.name}: {e}"[:200])

    def look(x):
        _, label, pic, mime = x
        j, c = llm.ask([{"role": "system", "content": VISION_SYSTEM},
                        {"role": "user", "content": [{"type": "text", "text": "Read every priced product of this price list."},
                                                     llm.image_part(pic, mime)]}], max_tokens=8000)
        # a second, independent reading of codes and prices only: a misread digit or an invented row shows up
        k, c2 = llm.ask([{"role": "system", "content": CHECK_SYSTEM},
                         {"role": "user", "content": [{"type": "text", "text": "List every model code and its price."},
                                                      llm.image_part(pic, mime)]}], max_tokens=6000)
        return label, j, c, c2, k
    with ThreadPoolExecutor(4) as ex:
        for n, (label, j, c, c2, k) in enumerate(ex.map(look, pics), 1):
            usd, calls, check_usd = usd + c + c2, calls + 2, check_usd + c2
            if j is None:
                failed.append(f"{label}: خوانده نشد")
            got = _rows_of(j, label)
            cross_check(got, k)
            rows += got
            if progress:
                progress(n, len(pics))
    _check_with_text(files, rows, unit_hint)
    items = merge(rows, unit_hint)
    return {"rows": items, "usd": round(usd, 4), "calls": calls, "failed": failed, "pages": len(pics),
            "cost": {"read": round(usd - check_usd, 4), "check": round(check_usd, 4)}}


def _digits(p) -> str:
    return re.sub(r"\D", "", str(p or "").translate(FA_DIGITS))


def cross_check(rows: list[dict], second: dict | None) -> None:
    """Rows of one page against the second reading: a code the second reading did not see, or a price it read
    differently, gets a note and waits for a person."""
    if not isinstance(second, dict) or not isinstance(second.get("rows"), list) or not second["rows"]:
        return
    seen: dict[str, str] = {}
    for x in second["rows"]:
        if isinstance(x, dict) and model_key(x.get("model")):
            seen[model_key(x.get("model"))] = _digits(x.get("price"))
    for r in rows:
        k = model_key(r.get("model"))
        if len(k) < 3:
            continue
        if k not in seen:
            r["check"] = "این کد در خواندن دوم صفحه دیده نشد؛ بررسی کنید"
        elif seen[k] and _digits(r.get("price")) and seen[k] != _digits(r.get("price")):
            r["check"] = f"قیمت در خواندن دوم {seen[k]} خوانده شد؛ بررسی کنید"


def _check_with_text(files: list[Path], rows: list[dict], unit_hint: str) -> None:
    """A PDF with a text layer: a price read from the picture must be printed on its page (as read, or as toman
    or rial of it). Rows whose price is not there get a note and wait for a person."""
    texts: dict[str, set] = {}
    for f in files:
        if not f.name.lower().endswith(".pdf"):
            continue
        try:
            import pdfplumber
            with pdfplumber.open(str(f)) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    texts[f"{f.name} ص{i}"] = page_nums(page.extract_text() or "")
        except Exception:                                # noqa: BLE001
            continue
    for r in rows:
        nums = texts.get(r.get("file", ""))
        if not nums:
            continue
        raw = re.sub(r"\D", "", str(r.get("price") or "").translate(FA_DIGITS))
        if raw and int(raw) not in nums and int(raw) * 10 not in nums and int(raw) // 10 not in nums:
            r["check"] = "قیمت خوانده‌شده در متن صفحه پیدا نشد؛ بررسی کنید"


def merge(rows: list[dict], unit_hint: str = "auto") -> list[dict]:
    """One row per model (or per name); the newest list's price wins, a different price on the same date is noted."""
    by: dict[str, dict] = {}
    for r in rows:
        k = row_key(r.get("model", ""), r.get("name", ""))
        if not k:
            continue
        unit = r["unit"] if r.get("unit") in ("rial", "toman", "thousand_toman", "thousand_rial") else \
            (unit_hint if unit_hint in ("rial", "toman") else "")
        t = to_toman(r.get("price"), unit) if unit and r.get("price") else None
        notes = []
        if r.get("price") and not unit:
            notes.append("واحد قیمت معلوم نیست")
        elif r.get("price") and t is None:
            notes.append("قیمت خوانده‌شده معقول نیست")
        if r.get("vat") == "yes" and t:
            notes.append(f"قیمت با ارزش افزوده چاپ شده ({t:,} تومان)")
        if r.get("check"):
            notes.append(r["check"])
        row = {"key": k, "brand": r.get("brand", ""), "model": str(r.get("model") or "").strip(),
               "name": str(r.get("name") or "").strip(), "price": str(r.get("price") or ""), "unit": unit,
               "toman": t, "stock": r.get("stock") if r.get("stock") in ("yes", "no") else "",
               "vat": r.get("vat", ""), "list_date": r.get("list_date", ""), "file": r.get("file", ""),
               "note": "؛ ".join(notes)}
        old = by.get(k)
        if old is None:
            by[k] = row
            continue
        a, b = list_date_rank(old["list_date"]), list_date_rank(row["list_date"])
        if (b > a and row["toman"]) or (not old["toman"] and row["toman"]):
            by[k] = row
        elif b == a and row["toman"] and old["toman"] and row["toman"] != old["toman"]:
            old["note"] = "؛ ".join(x for x in (old["note"], f"جای دیگر {row['toman']:,} خوانده شد؛ بررسی کنید") if x)
    return list(by.values())
