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
import os
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
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
Leave out cartons, counts, row numbers, phone numbers and other numbers that are not the price. A code inside Persian
text («میل سوپاپ ۱۰۱۳-۴») keeps its digit groups in the order a reader sees them left to right (1013-4): never split a
group or reorder the groups. A shop photo of a part with no price on it is still a row, with an empty price. No prose,
only the JSON."""

CHECK_SYSTEM = """You check a price list page. Return JSON only: {"rows": [{"model": "<model code exactly as printed>",
 "price": "<its price digits exactly as printed, or empty>"}]} for EVERY row that has a model code, in page order. Read
each price digit by digit; Iranian lists group digits with a slash or a dot («۱/۴۹۹/۰۰۰», 1.499.000). Never invent a row:
a code that is not printed on this page must not appear."""

TEXT_USER = "Read every priced product of this price list (a spreadsheet as text, one line per row, cells separated by ' | ')."

STOCK_WORDS = ("موجودی", "وضعیت", "موجود", "stock", "available")
NO_STOCK = re.compile(r"ناموجود|نا موجود|عدم موجود|اتمام|تمام شد|out\s*of\s*stock|^\s*(0|no|خیر|✗|×|-)\s*$", re.I)
PICTURE_EXT = (".jpg", ".jpeg", ".png", ".webp")

# (26 Nov) The reading ladder: every page is read by the main model (PL_MODEL, cheap). A page whose reading looks
# wrong (the second reading disagrees, a price with too few digits, or after matching many prices far from the
# site's) is read again by the next model, up to the strong one. Each step is paid only for the pages that need it.
LITE = os.environ.get("PL_MODEL_LITE", "google/gemini-2.5-flash-lite")
STRONG = os.environ.get("PL_MODEL_STRONG", "google/gemini-2.5-flash")
SMALL_SIDE = 1600       # a picture smaller than this on its long side is enlarged (and cut in strips) before reading


def ladder() -> list[str]:
    out = []
    for m in (llm.model(), LITE, STRONG):
        if m and m not in out:
            out.append(m)
    return out if os.environ.get("PL_ESCALATE", "1") != "0" else out[:1]


def stage_name(model: str) -> str:
    """'main' / 'lite' / 'strong': the key its cost is counted under."""
    if model == llm.model():
        return "read"
    return "lite" if model == LITE else "strong"
TABLE_EXT = (".xlsx", ".xlsm", ".xls", ".csv")


# ------------------------------------------------------------------ pictures

def images_of(f: Path, skip: set | frozenset = frozenset()) -> list[tuple[str, bytes, str]]:
    """(label, picture bytes, mime) of one file: a picture as it is (made smaller when huge), a PDF page by page."""
    low = f.name.lower()
    if low.endswith(PICTURE_EXT):
        return [(f.name, _fit(f.read_bytes()), "image/jpeg")]
    if low.endswith(".pdf"):
        import pdfplumber
        out = []
        with pdfplumber.open(str(f)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                if i in skip:                            # read from its text already
                    continue
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


def prepare(pic: bytes) -> list[bytes]:
    """(26 Nov) A small picture (a 600 px poster from a messenger) is enlarged 2.5 times before reading, and a tall one
    is cut in 3 overlapping strips: at its own size every model dropped digits of the prices («۱۴/۷۵۰/۰۰۰» read as
    1750000); enlarged and in strips they were read whole. A big picture or a PDF page goes as it is."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(pic)).convert("RGB")
    except Exception:                                    # noqa: BLE001
        return [pic]
    side = max(im.size)
    if side >= SMALL_SIDE:
        return [pic]
    f = min(2.5, 2600 / side)
    im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    n = 3 if im.height > 1.3 * im.width else 1
    out, step = [], im.height / n
    for i in range(n):
        a, b = max(0, int(i * step - 0.08 * step)), min(im.height, int((i + 1) * step + 0.08 * step))
        buf = io.BytesIO()
        im.crop((0, a, im.width, b)).save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


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


class _HtmlTable(HTMLParser):
    """Rows of the first <table> in an HTML page (many accounting programs save «.xls» files as HTML)."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in, self._depth = [], None, None, False, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            span = dict(attrs).get("colspan")
            self._span = int(span) if span and span.isdigit() else 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._row += [""] * (self._span - 1)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _num(v):
    """Excel numbers come back as floats: 2500000.0 -> 2500000."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _xls_grid(f: Path) -> list[list]:
    """An «.xls» file: real old Excel (xlrd), an xlsx with the wrong name, or an HTML table."""
    raw = f.read_bytes()
    if raw[:2] == b"PK":                                  # an xlsx renamed to .xls
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(raw), data_only=True).active
        return [[c.value for c in r] for r in ws.iter_rows()]
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":     # old binary Excel
        import xlrd
        sh = xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
        return [[_num(sh.cell_value(i, j)) or None for j in range(sh.ncols)] for i in range(sh.nrows)]
    for enc in ("utf-8-sig", "utf-16", "cp1256"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        txt = raw.decode("utf-8", "ignore")
    if "<t" in txt.lower():
        p = _HtmlTable()
        p.feed(txt)
        if p.rows:
            return p.rows
    return list(csv.reader(io.StringIO(txt), delimiter="\t"))  # a tab-separated text file named .xls


def _grid(f: Path) -> tuple[list[list], list[list[bool]]]:
    """Cell values and 'written in red' of the first sheet (or the CSV)."""
    if f.name.lower().endswith(".xls"):
        rows = _xls_grid(f)
        return rows, [[False] * len(r) for r in rows]
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
        return headless_rows(vals)
    head = [str(c or "").strip().lower() for c in vals[hi]]
    if not any(_is(h, "قیمت", "price", "مبلغ", "فی") for h in head):
        got = headless_rows(vals)                         # (26 Nov) Hydraparts: no header row at all
        if got:
            return got
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


def _cell_num(c) -> float | None:
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return float(c)
    t = re.sub(r"[,٬\s]", "", str(c or "").translate(FA_DIGITS))
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def headless_rows(vals: list[list]) -> list[dict]:
    """(26 Nov) A sheet with no header row (Hydraparts: name | 10-digit code | type | size | price). Columns are told
    by what they hold: the one with most Persian words is the name, a column of same-length long numbers is a code,
    the numeric column of varied big numbers on the far side is the price, short cells with digits are a size, and
    a column of short words given on a group's first row (R1, 2SN) goes down its group. Unclear: []."""
    rows = [r for r in vals if sum(1 for c in r if c is not None and str(c).strip() not in ("", "None")) >= 3]
    if len(rows) < 5:
        return []
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    def col(j):
        return [r[j] for r in rows if r[j] is not None and str(r[j]).strip() not in ("", "None")]
    fa = lambda c: len(re.findall(r"[؀-ۿ]", str(c)))
    name = max(range(width), key=lambda j: sum(fa(c) for c in col(j)))
    if sum(fa(c) for c in col(name)) < len(rows) * 3:
        return []
    code, price, size, kind = None, None, None, None
    for j in range(width):
        if j == name:
            continue
        cs = col(j)
        if len(cs) < len(rows) * 0.2:
            continue
        nums = [_cell_num(c) for c in cs]
        if all(n is not None for n in nums):
            lens = {len(str(int(n))) for n in nums}
            if len(lens) == 1 and min(lens) >= 8 and len(cs) >= len(rows) * 0.8:
                code = j
            elif max(nums) >= 1000 and len(set(nums)) > len(nums) * 0.3:
                price = j                                 # the last such column wins: prices sit at the far side
        elif all(len(str(c)) <= 12 and re.search(r"\d\s*/\s*\d|[\"”’″]|اینچ|میل|mm", str(c).translate(FA_DIGITS))
                 for c in cs):
            size = j
        elif all(len(str(c)) <= 8 for c in cs) and len(cs) < len(rows) * 0.8:
            kind = j
    if price is None:
        return []
    out, last_kind = [], ""
    for r in rows:
        nm = str(r[name] or "").strip()
        if r[name] is None or not nm or fa(nm) == 0:
            continue
        if kind is not None and r[kind] is not None and str(r[kind]).strip() not in ("", "None"):
            last_kind = str(r[kind]).strip()
        p = _cell_num(r[price])
        if p is None:
            continue
        parts = [nm] + ([last_kind] if kind is not None and last_kind else []) + (
            [str(r[size]).strip().replace("’’", '"').replace("”", '"')] if size is not None and r[size] is not None else [])
        out.append({"brand": "", "model": str(int(_cell_num(r[code]))) if code is not None and _cell_num(r[code]) else "",
                    "name": " ".join(parts), "price": str(int(round(p))), "unit": "unknown", "stock": "", "vat": "",
                    "list_date": ""})
    return out


_FA = re.compile(r"[\u0600-\u06FF]")


def _fa_order(line: str) -> str:
    """pdfium gives an RTL run of words in visual order («مینیاتوری کلید»): each run of Persian words is turned back."""
    out, run = [], []
    for w in line.split():
        if _FA.search(w):
            run.append(w)
            continue
        out += run[::-1] + [w]
        run = []
    return " ".join(out + run[::-1])


def pdf_text_pages(f: Path) -> dict[int, str]:
    """(26 Nov) A PDF that carries its text (Fuse Central's ETI catalog, 6 MB, 30 pages) is read from the text: free of
    misread digits and far cheaper than a picture. Each printed line is taken as a band across the page, so a row's
    code, name and price stay together. Pages with little text or no prices are left to the picture reading."""
    out: dict[int, str] = {}
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(f))
    except Exception as e:                               # noqa: BLE001
        log.warning("pdf text %s: %s", f.name, e)
        return out
    t0 = time.monotonic()
    for i in range(len(pdf)):
        if time.monotonic() - t0 > 120:
            break
        try:
            page = pdf[i]
            tp = page.get_textpage()
            W = page.get_size()[0]
            n = tp.count_chars()
            if n < 150:
                continue
            boxes = []
            for j in range(n):
                b = tp.get_charbox(j)
                if b[3] - b[1] > 0.5:
                    boxes.append(((b[1] + b[3]) / 2, b[3] - b[1], b[1], b[3]))
            boxes.sort(key=lambda x: -x[0])              # top of the page first
            bands, cur = [], None
            for cy, h, y0, y1 in boxes:
                if cur and abs(cur[0] - cy) <= 0.5 * max(h, cur[1]):
                    cur[2], cur[3] = min(cur[2], y0), max(cur[3], y1)
                else:
                    cur = [cy, h, y0, y1]
                    bands.append(cur)
            lines = []
            for _cy, _h, y0, y1 in bands:
                t = re.sub(r"\s+", " ", str(tp.get_text_bounded(0, y0 - 0.5, W, y1 + 0.5) or "")).strip()
                if t:
                    lines.append(_fa_order(t))
            text = "\n".join(dict.fromkeys(lines))
            if len(re.findall(r"\d{1,3}(?:,\d{3})+", text.translate(FA_DIGITS))) >= 3:
                out[i + 1] = text
        except Exception as e:                           # noqa: BLE001 - that page goes to the picture reading
            log.warning("pdf text %s p%d: %s", f.name, i + 1, e)
    pdf.close()
    return out


def side_by_side(text: str) -> bool:
    """A page whose priced lines mostly carry two or more prices: tables printed next to each other."""
    priced = [len(re.findall(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])", ln.translate(FA_DIGITS).replace("٬", ",")))
              for ln in text.splitlines()]
    priced = [n for n in priced if n]
    return bool(priced) and sum(1 for n in priced if n >= 2) >= 0.2 * len(priced)


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


def _read_text(chunk: str, label: str, depth: int = 0) -> tuple[list[dict], float, int, int]:
    """(rows, $, calls, chunks lost) of a sheet read as text. (26 Nov) Hydraparts: an 8000-character chunk of a long
    sheet came back unreadable (the answer was cut at its token limit) and its rows were lost; such a chunk is read
    again in two halves, cut at a line break."""
    j, c = llm.ask([{"role": "system", "content": VISION_SYSTEM},
                    {"role": "user", "content": TEXT_USER + "\n\n" + chunk}], max_tokens=8000)
    if j is not None or depth >= 2 or len(chunk) < 800:
        return _rows_of(j, label), c, 1, int(j is None)
    mid = chunk.rfind("\n", 0, len(chunk) // 2 + 200)
    mid = mid if mid > len(chunk) // 4 else len(chunk) // 2
    a = _read_text(chunk[:mid], label, depth + 1)
    b = _read_text(chunk[mid:], label, depth + 1)
    return a[0] + b[0], c + a[1] + b[1], 1 + a[2] + b[2], a[3] + b[3]


def read_files(files: list[Path], unit_hint: str = "auto", progress=None) -> dict:
    """Every file -> merged rows. {'rows': [...], 'usd': .., 'calls': .., 'failed': [...], 'pages': ..}"""
    rows, usd, calls, failed, pics = [], 0.0, 0, [], []
    spent: dict[str, float] = {}                     # dollars per stage: read, check, lite, strong
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
                    got, c, n, bad = _read_text(text[i:i + 8000], f.name)
                    usd, calls = usd + c, calls + n
                    rows += got
                    if bad:
                        failed.append(f"{f.name}: {bad} تکه خوانده نشد")
                continue
            text_pages = pdf_text_pages(f) if low.endswith(".pdf") else {}
            # (27 Nov, روکا گستر) two price tables printed side by side share each text line: read as text, a row
            # took its neighbour's name. Such pages are read as pictures; their prices still come from the text.
            text_pages = {k: v for k, v in text_pages.items() if not side_by_side(v)}
            chunks = [(f"{f.name} ص{pno}", text[i:i + 8000]) for pno, text in text_pages.items() for i in range(0, len(text), 8000)]
            with ThreadPoolExecutor(4) as ex:
                for (label, _t), (got, c, n, bad) in zip(chunks, ex.map(lambda x: _read_text(x[1], x[0]), chunks)):
                    spent["text"] = spent.get("text", 0.0) + c
                    calls += n
                    rows += got
                    if bad:
                        failed.append(f"{label}: {bad} تکه خوانده نشد")
            got = images_of(f, set(text_pages))
            if not got and not text_pages:
                failed.append(f"{f.name}: نوع فایل پشتیبانی نمی‌شود")
            pics += [(f, *x) for x in got]
        except Exception as e:                           # noqa: BLE001 - one file never stops the others
            log.warning("read %s: %s", f.name, e)
            failed.append(f"{f.name}: {e}"[:200])

    pages: dict[str, dict] = {}

    def first(x):
        f, label, pic, mime = x
        parts = prepare(pic) if f.name.lower().endswith(PICTURE_EXT) else [pic]
        pg = {"label": label, "parts": parts, "readings": [], "stage": 0, "file": f}
        cost = read_stage(pg, ladder()[0])
        return label, pg, cost
    with ThreadPoolExecutor(4) as ex:
        for n, (label, pg, cost) in enumerate(ex.map(first, pics), 1):
            for k, v in cost.items():
                spent[k] = spent.get(k, 0.0) + v
            calls += pg.pop("_calls", 0)
            pages[label] = pg
            if pg["readings"] and pg["readings"][0][1] is None:
                failed.append(f"{label}: خوانده نشد")
            if progress:
                progress(n, len(pics))
    # pages whose reading looks wrong go up the ladder (in parallel, each page at its own pace)
    todo = [pg for pg in pages.values() if bad_rows(pg["rows"])]
    if todo:
        with ThreadPoolExecutor(4) as ex:
            for cost, c in ex.map(lambda pg: climb(pg), todo):
                for k, v in cost.items():
                    spent[k] = spent.get(k, 0.0) + v
                calls += c
    for pg in pages.values():
        rows += pg["rows"]
    _check_with_text(files, rows, unit_hint)
    items = merge(rows, unit_hint)
    spent["read"] = spent.get("read", 0.0) + usd     # the sheets read as text
    return {"rows": items, "usd": round(sum(spent.values()), 4), "calls": calls, "failed": failed, "pages": len(pics),
            "cost": {k: round(v, 4) for k, v in spent.items()}, "ladder": ladder_report(pages), "_pages": pages,
            "_raw": rows}


def remerge(got: dict, unit_hint: str = "auto") -> None:
    """After some pages were read again: the list's rows from the pages' current readings."""
    rows = [r for r in got["_raw"] if r.get("file") not in got["_pages"]]
    for pg in got["_pages"].values():
        rows += pg["rows"]
    _check_with_text(list(dict.fromkeys(pg["file"] for pg in got["_pages"].values())), rows, unit_hint)
    got["rows"] = merge(rows, unit_hint)
    got["ladder"] = ladder_report(got["_pages"])


def ladder_report(pages: dict) -> dict:
    """How many pages ended at each model, and which pages went up (for the review page)."""
    out: dict = {"models": ladder(), "pages": {}}
    for pg in pages.values():
        m = pg["readings"][-1][0] if pg["readings"] else ""
        out["pages"][stage_name(m) if m else "read"] = out["pages"].get(stage_name(m) if m else "read", 0) + 1
    out["up"] = [{"page": pg["label"], "models": [x[0] for x in pg["readings"]], "why": pg.get("why", "")}
                 for pg in pages.values() if len(pg["readings"]) > 1][:50]
    return out


def _ask_parts(parts: list[bytes], system: str, text: str, model: str, max_tokens: int) -> tuple[list[dict], float, int, bool]:
    """One reading of a page (all its strips): the rows of every strip, cost, calls, whether every strip answered."""
    out, usd, ok = [], 0.0, True
    for i, p in enumerate(parts, 1):
        t = text
        if len(parts) > 1:
            # (26 Nov) a table cut by a strip edge was read twice, once with its first digits lost
            t += (f" This picture is part {i} of {len(parts)} of one tall page, cut in horizontal strips that overlap a"
                  " little. Read only the rows printed whole in this part: leave out a row cut by the top or bottom"
                  " edge (the next or previous part has it whole), and never return a table's title or header as a row.")
        j, c = llm.ask([{"role": "system", "content": system},
                        {"role": "user", "content": [{"type": "text", "text": t}, llm.image_part(p, "image/jpeg")]}],
                       max_tokens=max_tokens, model_id=None if model == llm.model() else model)
        usd += c
        if j is None:
            ok = False
        out.append(j)
    return out, usd, len(parts), ok


def _page_rows(answers: list, label: str) -> list[dict]:
    """Rows of all strips; a row read twice where two strips overlap is kept once."""
    rows, seen, before = [], set(), set()
    for j in answers:
        here = set()
        for r in _rows_of(j, label):
            k = row_key(r.get("model", ""), r.get("name", ""))
            # the same row from two overlapping strips, its code read once with and once without «(ABS)»; rows of
            # ONE strip with the same price are real variants (DZ-SIF95 (R) and (W)) and all stay
            k2 = (model_key(re.sub(r"\(.*?\)", "", r.get("model", ""))), _digits(r.get("price")))
            if (k and k in seen) or (k2[0] and k2[1] and k2 in before):
                continue
            seen.add(k)
            here.add(k2)
            rows.append(r)
        before |= here
    return rows


def read_stage(pg: dict, model: str) -> dict:
    """Read the page with this model (and, for the main model, a second independent reading of codes and prices)."""
    answers, usd, calls, ok = _ask_parts(pg["parts"], VISION_SYSTEM, "Read every priced product of this price list.",
                                         model, 8000)
    rows = _page_rows(answers, pg["label"])
    cost = {stage_name(model): usd}
    if not pg["readings"]:
        # a second, independent reading of codes and prices only: a misread digit or an invented row shows up
        k_answers, c2, n2, _ = _ask_parts(pg["parts"], CHECK_SYSTEM, "List every model code and its price.", model, 6000)
        k = {"rows": [r for j in k_answers if isinstance(j, dict) for r in (j.get("rows") or []) if isinstance(r, dict)]}
        pg["check"] = k
        cross_check(rows, k)
        cost["check"] = c2
        calls += n2
    pg["readings"].append((model, rows if ok or rows else None))
    pg["rows"] = vote(pg) if len(pg["readings"]) > 1 else rows
    if len(pg["readings"]) == 1:
        digit_check(pg["rows"])
    pg["_calls"] = pg.get("_calls", 0) + calls
    return cost


def climb(pg: dict, why: str = "") -> tuple[dict, int]:
    """Read the page with the next models of the ladder until its rows look right (or the ladder ends)."""
    spent: dict[str, float] = {}
    pg["_calls"] = 0
    if why:
        pg["why"] = why
    elif not pg.get("why"):
        n = bad_rows(pg["rows"])
        pg["why"] = f"{n} ردیف مشکوک (دو خواندن یکی نبود یا رقم قیمت کم بود)"
    models = ladder()
    while len(pg["readings"]) < len(models):
        cost = read_stage(pg, models[len(pg["readings"])])
        for k, v in cost.items():
            spent[k] = spent.get(k, 0.0) + v
        if not bad_rows(pg["rows"]):
            break
    return spent, pg.pop("_calls", 0)


def bad_rows(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("check"))


def _prices_by_key(rows) -> dict[str, str]:
    out = {}
    for r in rows or []:
        k = model_key(r.get("model"))
        d = _digits(r.get("price"))
        if len(k) >= 3 and d:
            out.setdefault(k, d)
    return out


def vote(pg: dict) -> list[dict]:
    """The newest (stronger) reading's rows; a price it shares with an earlier reading is confirmed, one no other
    reading saw is noted for a person."""
    model, rows = pg["readings"][-1]
    rows = [dict(r) for r in (rows or [])]
    for r in rows:
        r.pop("check", None)
    earlier = [_prices_by_key(x[1]) for x in pg["readings"][:-1]]
    k_rows = pg.get("check") or {}
    earlier.append({model_key(x.get("model")): _digits(x.get("price")) for x in k_rows.get("rows", [])
                    if isinstance(x, dict) and len(model_key(x.get("model"))) >= 3 and _digits(x.get("price"))})
    digit_check(rows)
    last = len(pg["readings"]) >= len(ladder())
    for r in rows:
        if r.get("check"):
            continue
        k, d = model_key(r.get("model")), _digits(r.get("price"))
        if len(k) < 3 or not d:
            continue
        others = [e[k] for e in earlier if e.get(k)]
        if others and d not in others:
            r["check"] = ("خواندن‌های مختلف قیمت را یکی نخواندند (" + "، ".join(sorted(set(others))) + ")؛ بررسی کنید"
                          if last else "خواندن‌ها یکی نبود")
    return rows


def digit_check(rows: list[dict]) -> None:
    """A price with two or more digits fewer than most prices of its page is a misread (a dropped «۰۰۰» group)."""
    lens = [len(_digits(r.get("price"))) for r in rows if _digits(r.get("price"))]
    if len(lens) < 4:
        return
    med = statistics.median(lens)
    for r in rows:
        d = _digits(r.get("price"))
        if d and len(d) <= med - 2 and not r.get("check"):
            r["check"] = "رقم‌های این قیمت از قیمت‌های دیگر صفحه کمتر است؛ بررسی کنید"


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


def _line_prices(tp, W: float, code: str) -> list[int] | None:
    """The prices printed on the line of the page that carries this code (comma-grouped numbers only, so a pack
    size like «12/108» is not one), or None when the code is not in the page's text."""
    s = tp.search(code, match_case=False)
    hit = s.get_next()
    if not hit:
        return None
    i, n = hit
    boxes = [tp.get_charbox(j) for j in range(i, i + n)]
    y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
    line = str(tp.get_text_bounded(0, y0 - 1, W, y1 + 1) or "").translate(FA_DIGITS).replace("٬", ",")
    return [int(g.replace(",", "")) for g in re.findall(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])", line)]


_PRICE_TOK = re.compile(r"\d{1,3}(?:,\d{3})+")


def _table_codes(text: str) -> dict[str, tuple[int, str]]:
    """(27 Nov, روکا گستر) A price table printed as «price  count  size  code» cells, two tables side by side on one
    line: each short code (3-6 digits) with the comma-grouped price just before it and the cell's size."""
    out: dict[str, tuple[int, str]] = {}
    code_re = re.compile(r"\d{3,6}")
    for line in text.splitlines():
        tok = line.translate(FA_DIGITS).replace("٬", ",").split()
        at = [i for i, w in enumerate(tok) if _PRICE_TOK.fullmatch(w)]
        for n, i in enumerate(at):
            # «price count size code»: the cell runs to the next price; its last token is the code. A count is
            # also 3 digits (300 pieces), so the code is the cell's last token, never any 3-digit number in it.
            end = at[n + 1] if n + 1 < len(at) else len(tok)
            cell = tok[i + 1:end]
            if len(cell) >= 2 and code_re.fullmatch(cell[-1]):
                code, mid = cell[-1], cell[:-1]
            elif i == len(tok) - 1:
                # the same cell printed the other way round, «code size count price» (a line that starts with
                # a header): the code is the first number of the run before the price
                k = i
                while k > 0 and re.fullmatch(r"[\d*/.]+", tok[k - 1]) and (n == 0 or k - 1 > at[n - 1]):
                    k -= 1
                run = tok[k:i]
                if len(run) < 2 or not code_re.fullmatch(run[0]):
                    continue
                code, mid = run[0], run[1:][::-1]
            else:
                continue
            if code not in out:
                out[code] = (int(tok[i].replace(",", "")), " ".join(mid))
    return out


def _table_price(codes: dict, code: str, name: str) -> tuple[str, int] | None:
    """The printed code and price for a row's code. A Persian zero is a dot the picture reading often drops
    («۴۲۰۱» read as 421): a code matches when it is the same without its zeros, and only one printed code does.
    The cell's size must be in the row's name (a 20*1/2 row is not the 25*1/2 cell)."""
    if code in codes:
        hits = [code]
    else:
        bare = code.replace("0", "")
        hits = [k for k in codes if len(k) > len(code) and k.replace("0", "") == bare]
    if len(hits) != 1:
        return None
    price, size = codes[hits[0]]
    want = set(re.findall(r"\d+", size))
    have = set(re.findall(r"\d+", str(name or "").translate(FA_DIGITS)))
    if want and have and not want & have:
        return None
    return hits[0], price


def _check_with_text(files: list[Path], rows: list[dict], unit_hint: str) -> None:
    """A PDF with a text layer: a price read from the picture must be printed on its page (as read, or as toman
    or rial of it). Rows whose price is not there get a note and wait for a person.
    (26 Nov) Fuse Central's ETI catalog: the picture read gave 19,200,000 where the page prints 19,100,000, and the
    second reading disagreed with the first on 11 rows. The text layer is the catalog itself: the price on the line
    that carries the row's code wins, and the note says so."""
    texts: dict[str, set] = {}
    pages: dict[str, tuple] = {}
    tables: dict = {}
    docs = []
    t0 = time.monotonic()
    for f in files:
        if not f.name.lower().endswith(".pdf"):
            continue
        try:
            # pdfium reads a page's text in milliseconds; pdfplumber took over an hour on a 6 MB drawing-heavy
            # catalog (25 Nov 2026) and the job sat at 80%. A text check is a bonus: it stops after 90 seconds.
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(f))
            docs.append(pdf)
            for i in range(len(pdf)):
                if time.monotonic() - t0 > 90:
                    log.warning("text check stopped after 90s at %s page %d", f.name, i + 1)
                    break
                tp = pdf[i].get_textpage()
                label = f"{f.name} ص{i + 1}"
                raw_text = tp.get_text_range() or ""
                texts[label] = page_nums(raw_text)
                pages[label] = (tp, pdf[i].get_size()[0])
                tables[label] = raw_text                 # short catalog codes in price tables (27 Nov)
            lines = pdf_text_pages(f)                    # its lines, when the page has real text, keep cells together
            for i in range(len(pdf)):
                label = f"{f.name} ص{i + 1}"
                if label in tables:
                    tables[label] = _table_codes(lines.get(i + 1) or tables[label])
        except Exception as e:                           # noqa: BLE001
            log.warning("text check %s: %s", f.name, e)
            continue
    for r in rows:
        nums = texts.get(r.get("file", ""))
        if not nums:
            continue
        raw = re.sub(r"\D", "", str(r.get("price") or "").translate(FA_DIGITS))
        code = str(r.get("model") or "").strip()
        if raw and len(code) >= 5 and re.search(r"\d", code) and r.get("file") in pages:
            try:
                got = _line_prices(*pages[r["file"]], code)
            except Exception as e:                       # noqa: BLE001
                log.warning("line price %s: %s", code, e)
                got = None
            if got is not None and len(got) == 1:
                if int(raw) != got[0]:
                    r["price"] = f"{got[0]:,}"
                    r["check"] = f"قیمت از متن خود PDF برداشته شد ({got[0]:,}؛ از روی تصویر {int(raw):,} خوانده شده بود)"
                elif "خواندن دوم" in str(r.get("check") or ""):
                    r.pop("check", None)                 # the page's text agrees with the first reading
                continue
        tab = tables.get(r.get("file", "")) if raw and re.fullmatch(r"\d{3,6}", code) else None
        hit = _table_price(tab, code, r.get("name", "")) if tab else None
        if hit:
            printed, got = hit
            if printed != code:
                r["model"] = printed                     # the code as the catalog prints it
            if int(raw) != got:
                r["price"] = f"{got:,}"
                r["check"] = f"قیمت از متن خود PDF برداشته شد ({got:,}؛ از روی تصویر {int(raw):,} خوانده شده بود)"
            elif "خواندن دوم" in str(r.get("check") or ""):
                r.pop("check", None)
            continue
        if raw and int(raw) not in nums and int(raw) * 10 not in nums and int(raw) // 10 not in nums:
            r["check"] = "قیمت خوانده‌شده در متن صفحه پیدا نشد؛ بررسی کنید"
    for pdf in docs:
        try:
            pdf.close()
        except Exception:                                # noqa: BLE001
            pass


def settle_brands(rows: list[dict]) -> list[dict]:
    """(26 Nov) Fuse Central's ETI catalog came back with brands «ASTI» (451 rows: the logo misread), «ETI» (407) and the
    series names «ETIBREAK», «ETICON», «ETIMAT», «ETISWITCH». A series named after its maker is the maker; a row whose
    brand is in none of its words while its name carries a series of the list's main maker is that maker's."""
    from collections import Counter
    up = lambda b: re.sub(r"[^A-Z0-9]", "", str(b or "").upper())
    cnt = Counter(up(r.get("brand")) for r in rows if up(r.get("brand")))
    mains = [b for b, n in cnt.most_common() if n >= 5 and len(b) >= 3]
    for r in rows:
        b = up(r.get("brand"))
        m = next((x for x in mains if x != b and b.startswith(x)), None)
        if m:
            r["brand"] = m
    cnt = Counter(up(r.get("brand")) for r in rows if up(r.get("brand")))
    if not cnt:
        return rows
    top, n = cnt.most_common(1)[0]
    if n < 0.3 * len(rows) or len(top) < 3:
        return rows
    for r in rows:
        b = up(r.get("brand"))
        words = [up(w) for w in str(r.get("name") or "").split()]
        if b and b != top and not any(w.startswith(b) for w in words if w) and any(w.startswith(top) for w in words if w):
            r["brand"] = top
    return rows


def merge(rows: list[dict], unit_hint: str = "auto") -> list[dict]:
    """One row per model (or per name); the newest list's price wins, a different price on the same date is noted."""
    by: dict[str, dict] = {}
    for r in rows:
        k = row_key(r.get("model", ""), r.get("name", ""))
        if not k:
            continue
        unit = r["unit"] if r.get("unit") in ("rial", "toman", "thousand_toman", "thousand_rial") else \
            (unit_hint if unit_hint in ("rial", "toman") else "")
        raw = re.sub(r"\D", "", str(r.get("price") or "").translate(FA_DIGITS).split(".")[0] if unit.startswith("thousand") and
                     re.fullmatch(r"[\d۰-۹]+[.,/][\d۰-۹]{1,2}", str(r.get("price") or "").strip()) else str(r.get("price") or "").translate(FA_DIGITS))
        if unit.startswith("thousand") and raw and int(raw) >= 100_000:
            # (26 Nov) Ariana Novin: «5,500,000» read as thousand toman (5.5 billion). A price printed in thousands
            # has no million in it (and «600,000» for a key is 600 thousand toman, not 600 million).
            unit = unit.replace("thousand_", "")
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
    return settle_brands(list(by.values()))
