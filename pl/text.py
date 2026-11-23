"""Small text helpers: Persian digits and letters, prices, model codes (copied from product-crawler, 23 Nov 2026)."""
from __future__ import annotations

import hashlib
import re
import unicodedata

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_CHARS = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "‌": " ", "‏": "", "‎": "", "ـ": ""})
_DIACRITICS = re.compile(r"[ً-ٰٟ]")

GENERIC_WORDS = {"مدل", "برند", "سری", "با", "و", "به", "همراه", "عدد", "هرکدام", "سایز", "اینچ", "میلیمتر", "قیمت",
                 "خرید", "دستگاه", "کیت", "ست", "جدید", "حرفه ای", "صنعتی", "پرتابل", "نوع", "از"}


def norm_text(s: object) -> str:
    """Lowercase, one form of Persian letters and digits, no punctuation, single spaces."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).translate(FA_DIGITS).translate(_CHARS)
    s = _DIACRITICS.sub("", s).lower()
    s = re.sub(r"[\"'«»“”()\[\]{}،,؛;:|]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def model_key(s) -> str:
    """'VR2402-BCK', 'vr 2402 bck' and 'VR2402BCK' are one model."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").translate(FA_DIGITS).upper())


def name_key(name: str) -> str:
    """'N' + a short hash of the normalized name, for a row with no model code."""
    n = norm_text(name)
    return ("N" + hashlib.sha1(n.encode()).hexdigest()[:10].upper()) if len(n) >= 4 else ""


def row_key(model: str, name: str) -> str:
    k = model_key(model)
    if len(k) >= 3 and re.search(r"\d", k):
        return k
    return name_key(name)


def weak_key(key: str) -> bool:
    """A code of digits only or of 4 characters or fewer is also a size: it alone never makes a match."""
    return key.isdigit() or len(key) <= 4


def contains_model(text: str, key: str) -> bool:
    """The model code inside a name/SKU, its parts maybe split by a space or dash; 'VR2010' is not in 'VR20105'."""
    if len(key) < 3 or not re.search(r"\d", key):
        return False
    t = str(text or "").translate(FA_DIGITS).upper()
    pat = r"(?<![A-Z0-9])" + r"[\s\-_/.]?".join(map(re.escape, key)) + r"(?![0-9])"
    return re.search(pat, t) is not None


def words_nums(text: str) -> tuple[set, set]:
    t = norm_text(text).replace("/", " / ").replace("*", " * ").replace("×", " × ")
    nums = set(re.findall(r"\d+(?:\.\d+)?", t))
    words = {w for w in t.split() if not re.search(r"\d", w) and len(w) > 1 and w not in GENERIC_WORDS}
    return words, nums


def same_by_name(product_name: str, list_name: str) -> bool:
    """Every number of the row is in the product's name, and most words of each are in the other."""
    lw, ln = words_nums(list_name)
    pw, pn = words_nums(product_name)
    if not lw or not pw or not ln <= pn:
        return False
    return len(lw & pw) >= 0.8 * len(lw) and len(lw & pw) >= 0.6 * len(pw)


def to_toman(price, unit: str) -> int | None:
    """'۴۸/۰۰۰/۰۰۰' rial -> 4800000 toman; None when unreadable or out of a sane range."""
    if isinstance(price, float):
        price = int(price) if price == price else None
    txt = str(price or "").translate(FA_DIGITS).replace("٫", ".").strip()
    txt = re.sub(r"^(\d+)\.0+$", r"\1", txt)
    if unit in ("thousand_toman", "thousand_rial") and re.fullmatch(r"\d+\.\d{1,2}", txt):
        v = float(txt) * 1000
    else:
        raw = re.sub(r"\D", "", txt)
        if not raw:
            return None
        v = int(raw) * (1000 if unit in ("thousand_toman", "thousand_rial") else 1)
    v = int(v)
    if unit in ("rial", "thousand_rial"):
        v //= 10
    return v if 1_000 <= v <= 20_000_000_000 else None


MONTHS = {m: i for i, m in enumerate(["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان",
                                      "آذر", "دی", "بهمن", "اسفند"], 1)}


def list_date_rank(s: str) -> int:
    """'آبان ۱۴۰۵' / '1405/08/13' -> 140508; 0 when unknown."""
    s = str(s or "").translate(FA_DIGITS)
    g = re.search(r"(1[34]\d\d)/(\d{1,2})", s)
    if g:
        return int(g.group(1)) * 100 + int(g.group(2))
    y = re.search(r"1[34]\d\d", s)
    m = next((v for k, v in MONTHS.items() if k in s), 0)
    return int(y.group(0)) * 100 + m if y else 0


def page_nums(text: str) -> set[int]:
    """Every number of four digits or more printed in a text ('۶۳۰,۰۰۰,۰۰۰', '63.000.000', '48/000/000')."""
    t = str(text or "").translate(FA_DIGITS).replace("٬", ",").replace("٫", ".")
    out = set()
    for g in re.findall(r"\d{1,3}(?:[,./]\d{3})+|\d+", t):
        v = re.sub(r"\D", "", g)
        if len(v) >= 4:
            out.add(int(v))
    return out
