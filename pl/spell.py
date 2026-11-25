"""A word misread from a fancy title font is put right before matching.

Catalog pages print a product's name once, big and in a decorative font, beside its table; the vision model then
sometimes drops or swaps one letter (کلاهک -> کلاک, ترمزدار -> ترمودار, کمربند -> کمرند). Such a word is in no shop
product, so the row cannot be matched. A word of the row that is unknown, and is ONE letter short of (or ONE letter
different from) exactly one known word, becomes that word. Known words are the supplier's own product names plus a
short list of trade words. Every change is written in the row's note so a person sees it.
"""
from __future__ import annotations

import re
from collections import Counter

from .text import norm_text

TRADE_WORDS = """
زانو سه راه چهارراه چهار سراه تبدیل بوشن بوش ترمزدار ترمز دار کلاهک کمربند تعمیری رابط انبساطی موفه سوکت پله
چسبی چسب درپوش دریچه بازدید سیفون کفشور لوله اتصالات فاضلابی فاضلاب پلیکا پلی اتیلن پلیمر پی وی سی کوپله
دوسر سر تخت بدون منظوره چند بست قطر خارجی داخلی درجه مادگی نری مهره ماسوره شیر فلکه توپی کشویی گازی
آبیاری فشار قوی ضعیف استخری استیل برنجی برنج گالوانیزه آلومینیوم چدنی چدن مسی فلنج واشر لاستیکی اورینگ
نوار تفلون مغزی رابط کلید پریز کابل سیم جعبه تقسیم قوطی سینی کانال داکت ترانکینگ هواکش ونت مانیفولد
شلنگ شیلنگ پمپ کوپلینگ گیربکس الکتروموتور یاتاقان بلبرینگ تسمه پولی چرخدنده
"""

_WORD = re.compile(r"^[؀-ۿ]+$")


def _near(a: str, b: str) -> bool:
    """b is a with one letter added, or with one letter changed (never one letter removed: that is a suffix)."""
    if len(b) == len(a) + 1:
        return any(b[:i] + b[i + 1:] == a for i in range(len(b)))
    if len(b) == len(a):
        return sum(x != y for x, y in zip(a, b)) == 1
    return False


def vocabulary(offers: list[dict]) -> Counter:
    vocab: Counter = Counter()
    for o in offers or []:
        for w in norm_text(o.get("name") or "").split():
            if _WORD.match(w):
                vocab[w] += 1
    for w in TRADE_WORDS.split():
        vocab[w] += 1
    return vocab


def fix_word(w: str, vocab: Counter) -> str | None:
    """The known word an unknown word was misread from, or None."""
    if len(w) < 3 or w in vocab or not _WORD.match(w):
        return None
    cands = [v for v in vocab if len(v) >= 4 and _near(w, v)]
    if not cands:
        return None
    cands.sort(key=lambda v: -vocab[v])
    if len(cands) > 1 and vocab[cands[0]] == vocab[cands[1]]:
        return None                                    # two equally likely words: leave it to a person
    return cands[0]


def fix_names(rows: list[dict], offers: list[dict]) -> int:
    """Put misread words of every row's name right (in place). Returns how many rows changed."""
    vocab = vocabulary(offers)
    changed = 0
    for r in rows:
        name = str(r.get("name") or "")
        parts, notes = [], []
        for tok in norm_text(name).split():
            good = fix_word(tok, vocab)
            if good:
                notes.append(f"{tok}←{good}")
                tok = good
            parts.append(tok)
        if notes:
            r["name_read"] = name
            r["name"] = " ".join(parts)
            r["note"] = "؛ ".join(x for x in (r.get("note") or "", "نام اصلاح شد: " + "، ".join(
                n.split("←")[0] + " → " + n.split("←")[1] for n in notes)) if x)
            changed += 1
    return changed
