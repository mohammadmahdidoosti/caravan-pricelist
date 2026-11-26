"""Rows of a list -> the supplier's own offers on the site.

The site sends the supplier's offers (offer id, product name, SKU, current toman price) and its memory of earlier
confirmations (row key -> offer id). Each row gets:
  green   sure: remembered, or one offer holds the row's model code, or one offer's name agrees word for word
  yellow  a person decides: several offers hold the code, or the model picked one among close names
  new     none of the supplier's offers; candidates (if any) are listed
The matching rules come from product-crawler (contains_model, weak_key, same_by_name, the 'same product' prompt).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from . import llm
import math

from .text import to_toman, contains_model, norm_text, same_by_name, weak_key, words_nums

log = logging.getLogger(__name__)

SAME_SYSTEM = """You compare one row of a supplier's price list with the products this supplier sells in an online shop.
Pick every shop product that is the SAME product and the SAME variant as the row: the same size (and size unit), the
same material (استیل / آلومینیوم / برنج / گالوانیزه ...), the same type and design. Words may differ (digits Persian or
Latin, سایز 40 = 40 سانتی, ½ = 1/2). Another size, another variant, a spare part or an accessory is NOT the same.
When the row's brand (maker) is given and a product names ANOTHER brand, it is NOT the same; a product with no brand in
its name may still be the same.
Another kind of fitting is NOT the same even at the same size (سوکت پله ای is not رابط انبساطی, a socket is not a
coupling). Usually there is none or one. Reply JSON only: {"same": [<candidate numbers>]}"""

# Words in a brand line that say nothing about the maker.
_BRAND_STOP = {"برند", "شرکت", "گروه", "صنایع", "تولیدی", "بازرگانی", "پخش", "فروشگاه", "کارخانه", "و", "co", "company",
               "group", "ltd", "inc", "industrial", "industries"}

MAX_MODEL_CALLS = 600


def _brand_words(row: dict) -> set:
    """Significant words of the brand read from the list ('ناردین پلیمر اسپادانا' -> ناردین, پلیمر, اسپادانا)."""
    lw, _ = words_nums(row.get("brand") or "")
    return {w for w in lw if len(w) >= 3 and w not in _BRAND_STOP}


def _brand_hits(row: dict, offer: dict) -> int:
    bw = _brand_words(row)
    return len(bw & offer["_wn"][0]) if bw else 0


def _by_brand(row: dict, items: list[dict]) -> list[dict]:
    """Among several same-looking offers, keep the ones of the row's brand (if the brand settles it)."""
    if len(items) < 2 or not _brand_words(row):
        return items
    best = max(_brand_hits(row, o) for o in items)
    if best == 0:
        return items                                 # the brand is the distributor's, not in any product name
    return [o for o in items if _brand_hits(row, o) == best]


def _other_brand(row: dict, offer: dict) -> str:
    """(26 Nov) The site product's brand when it is clearly another maker than the list's ('' when not sure).
    A Dezli list matched three Dezli rows by name to Hirman products and marked them green."""
    bw = _brand_words(row)
    ob = str(offer.get("brand") or "").strip()
    if not bw or not ob:
        return ""
    if bw & words_nums(ob)[0] or _brand_hits(row, offer) or _same_brand_text(row.get("brand") or "", ob):
        return ""                                    # same brand, or the list's brand is in the product's name
    return ob


def _same_brand_text(a: str, b: str) -> bool:
    """(26 Nov) The reader joins or misspells a brand: «آریاناتوین», «آریانوین» for «آریانا نوین». Compared without
    spaces, close spellings are the same maker."""
    from difflib import SequenceMatcher
    x, y = (re.sub(r"\s+", "", norm_text(t)) for t in (a, b))
    if len(x) < 4 or len(y) < 4:
        return False
    return x in y or y in x or SequenceMatcher(None, x, y).ratio() >= 0.75


def _brand_guard(out: list[dict], by_id: dict) -> None:
    """A suggestion of another brand is never sure: green -> yellow, and the suggestion becomes a choice. A green
    found by name (not by the model code) for a row that HAS a model code the site's product does not carry is also
    only a guess."""
    for res in out:
        if not res["offers"] or res.get("how") == "memory":   # a choice a person made before stands
            continue
        o = by_id.get(str(res["offers"][0].get("offer_id")))
        if not o:
            continue
        note = ""
        other = _other_brand(res, o)
        if other:
            note = f"برند کالای پیشنهادی ({other}) با برند لیست فرق دارد؛ بررسی کنید"
        elif res["status"] == "green" and res.get("how") == "name":
            k = res.get("key", "")
            if k and not k.startswith("N") and not weak_key(k) and not contains_model(o["_hay"], k):
                note = "کد مدل لیست در نام کالای سایت نیست؛ بررسی کنید"
        if not note:
            continue
        res["note"] = "؛ ".join(x for x in (res.get("note") or "", note) if x)
        if other:
            res["cands"] = res["offers"] + [c for c in res["cands"] if c.get("offer_id") != res["offers"][0].get("offer_id")]
            res["offers"] = []
            res["status"] = "yellow" if res["cands"] else "new"
        else:
            res["status"] = "yellow"
        res["how"] = (res.get("how") or "") + "_guard"


def _near(a: str, b: str) -> bool:
    """Same word, or a one-letter typo of a long word («میرورر» / «میرور»)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def _same_words(x: set, y: set) -> bool:
    return all(any(_near(a, b) for b in y) for a in x) and all(any(_near(b, a) for a in x) for b in y)


def _closest(row: dict, same: list[dict]) -> list[dict]:
    """(26 Nov) Several products pass the name test (silver / gold, brushed / mirror, one / two metres of the same
    panel). The one whose words are the row's words (a one-letter typo allowed), and alone in that, is the product."""
    lw, ln = words_nums(row.get("name", ""))
    exact = [o for o in same if o["_wn"][1] == ln and _same_words(o["_wn"][0], lw)]
    return exact if len(exact) == 1 else []


# Words that make two look-alike products different (colour, finish, size, series). Two names with the same of
# these, and the row's numbers in the product's, are the same product even when other words differ
# («ورق ۱ میل نگیر» on the site, «و ق میل ثابت» read from the list).
ATTR_WORDS = {"نقره‌ای", "نقره", "طلایی", "دودی", "مشکی", "سفید", "خشدار", "میرور", "لیزری", "لمسی", "شیشه‌ای",
              "یک", "دو", "سه", "یکمتری", "دومتری", "بیضی", "میتسوبیشی", "لبخند", "قوس", "اکونومی", "اقتصادی",
              "توکار", "روکار", "دوبلکس", "پلکسی", "تک", "شش", "تاچ", "آمتال", "اپل", "لاتیس", "سگمنت", "ریزشی"}


def _attrs(words: set) -> set:
    return {w for w in words if any(_near(w, a) for a in ATTR_WORDS)}


def _by_attrs(row: dict, items: list[dict]) -> list[dict]:
    """(26 Nov) The one product with exactly the row's colour / finish / size words (and its numbers). A
    suggestion to tick, not a sure match: it saves a model call and a person's search."""
    lw, ln = words_nums(row.get("name", ""))
    ra = _attrs(lw)
    if len(ra) < 2:
        return []
    hit = [o for o in items if ln <= o["_wn"][1] and _same_words(_attrs(o["_wn"][0]), ra)
           and len(lw & o["_wn"][0]) >= 0.6 * len(lw)]
    return hit if len(hit) == 1 else []


def _head(name: str) -> str:
    """The kind of product: the first one or two words before any number ('زانو', 'سه راه', 'تبدیل')."""
    out = []
    for w in norm_text(name).split():
        if re.search(r"\d", w) or len(out) == 2:
            break
        out.append(w)
    return " ".join(out)


_KIND_SKIP = {"های", "ها", "قطر", "سایز", "خارجی", "داخلی", "درجه", "و", "با"}


# Words that make a fitting another kind of the same family (زانو خم, زانو دو سر کوپله, سه راه تبدیل, کلاهک H ...).
_SUBKIND = {"خم", "دو", "کوپله", "ترمزدار", "تخت", "پله", "انبساطی", "تبدیل", "h", "چند", "منظوره", "تعمیری", "کوتاه",
            "بلند", "یکطرفه", "دوطرفه", "اکونومی", "اقتصادی"}   # (26 Nov) an economy panel is a cheaper product


def _same_kind(row: dict, offer: dict) -> bool:
    """The row's kind is the product's kind: the first word before any number is in the product's name (سوکت is not
    رابط), and a word that makes another kind of the same family (خم, دو سر کوپله, تبدیل, H ...) is in both names
    or in neither (a plain زانو is not a زانو خم, a زانو خم is not a زانو دو سر کوپله). Other words may differ
    (لوله تحت فشار / لوله آبرسانی). 'چهارراه' / 'چهار راه' and 'فلانج' / 'فلنج' are one."""
    def flat(t: str) -> str:
        return t.replace(" ", "").replace("ا", "").replace("آ", "")

    rk = [w for w in _head(row.get("name") or "").split() if w not in _KIND_SKIP and len(w) >= 2]
    on = norm_text(offer.get("name", ""))
    if rk and flat(rk[0]) not in flat(on):
        return False
    rs = set(norm_text(row.get("name") or "").split()) & _SUBKIND
    os_ = set(on.split()) & _SUBKIND
    return rs == os_


def _narrow(row: dict, items: list[dict]) -> list[dict]:
    """Several products fit: a product carrying a word the row does not have (کلاهک H for a row that says only
    کلاهک) is another variant; keep the plainest one when it is a single product."""
    if len(items) < 2:
        return items
    rw = set(w for w in norm_text(row.get("name") or "").split() if not re.search(r"\d", w))
    toks = [set(w for w in norm_text(o.get("name", "")).split() if not re.search(r"\d", w)) for o in items]
    shared = set.intersection(*toks)
    # words of its own the row lacks + the row's words it lacks; only a product with none of either is "the plain one"
    some = set.union(*toks)
    score = [len(t - shared - rw - _brand_words(row)) + len(((rw - shared) & some) - t) for t in toks]
    keep = [o for o, e in zip(items, score) if e == 0]
    return keep if len(keep) == 1 else items


def _rule_pick(row: dict, cands: list[dict]) -> list[dict]:
    """No answer from the model: the candidates of the same kind ('زانو' is not 'سه راه') carrying exactly the row's
    numbers (a 90 elbow of size 110 is not the 90 elbow of size 90), then the row's brand."""
    head = _head(row.get("name") or "")
    _, ln = words_nums(row.get("name") or "")
    if not head or not ln:
        return []
    same = [o for o in cands if _head(o.get("name", "")).startswith(head.split()[0]) and o["_wn"][1] == ln
            and _same_kind(row, o)]
    if len(same) > 1:
        two = [o for o in same if _head(o.get("name", "")) == head]
        same = two or same
    return _narrow(row, _by_brand(row, same))


def _cands(row: dict, offers: list[dict], n: int = 8) -> list[dict]:
    """Offers sharing words with the row and carrying all its numbers, best first; a product with exactly the row's
    numbers comes first (a 90 elbow of size 90 before the 90 elbows of other sizes). When none carries them all, the
    ones missing a single number (the list adds «۱۶ بار» the shop's name leaves out)."""
    lw, ln = words_nums(row.get("name") or row.get("model") or "")
    if not lw:
        return []

    def scored(miss: int) -> list:
        out = []
        for o in offers:
            pw, pn = o["_wn"]
            if ln and len(ln - pn) > miss:
                continue
            common = len(lw & pw)
            if common:
                out.append((common / max(len(lw), 1) + common / max(len(pw), 1) + 0.5 * _brand_hits(row, o)
                            + (1.0 if ln and pn == ln else 0.0), o))
        out.sort(key=lambda x: -x[0])
        return out

    got = scored(0)
    if not got and len(ln) >= 3:
        got = scored(1)
    return [o for _, o in got[:n]]


def _pick(row: dict, cands: list[dict]) -> tuple[list[int], float]:
    user = f"row: {row.get('name') or row.get('model')}" + (f" (model {row['model']})" if row.get("model") else "") + \
        (f" — brand: {row['brand']}" if row.get("brand") else "") + "\n" + \
        "\n".join(f"{j}: {c['name']}" for j, c in enumerate(cands))
    ans, usd = llm.ask([{"role": "system", "content": SAME_SYSTEM}, {"role": "user", "content": user}], max_tokens=200,
                       title="Caravan supplier price list match")
    out = []
    for j in (ans or {}).get("same") or []:
        try:
            j = int(j)
        except (TypeError, ValueError):
            continue
        if 0 <= j < len(cands) and j not in out:
            out.append(j)
    return out, usd


def _brief(o: dict) -> dict:
    return {"offer_id": o.get("offer_id"), "name": o.get("name", ""), "toman": o.get("toman")}


def match(rows: list[dict], offers: list[dict], aliases: dict | None = None, big_pct: float = 20.0,
          use_model: bool = True) -> dict:
    aliases = {str(k): v for k, v in aliases.items()} if isinstance(aliases, dict) else {}   # PHP sends [] when empty
    by_id = {}
    for o in offers:
        o["_wn"] = words_nums(o.get("name", ""))
        o["_hay"] = f"{o.get('name', '')} {o.get('sku', '')}"
        by_id[str(o.get("offer_id"))] = o
    out, todo, usd = [], [], 0.0
    for r in rows:
        res = {**r, "status": "new", "how": "", "offers": [], "cands": []}
        k = r["key"]
        if k in aliases and str(aliases[k]) in by_id:
            res.update(status="green", how="memory", offers=[_brief(by_id[str(aliases[k])])])
        elif not k.startswith("N") and not weak_key(k):
            hits = _by_brand(r, [o for o in offers if contains_model(o["_hay"], k)])
            if len(hits) == 1:
                res.update(status="green", how="model", offers=[_brief(hits[0])])
            elif hits:
                res.update(status="yellow", how="model_many", cands=[_brief(o) for o in hits[:8]])
        if res["status"] == "new":
            same = _by_brand(r, [o for o in offers if same_by_name(o.get("name", ""), r.get("name", ""))])
            best = _closest(r, same) if len(same) > 1 else []
            if len(same) == 1 or best:
                res.update(status="green", how="name", offers=[_brief((best or same)[0])])
            elif same:
                one = _by_attrs(r, same)
                res.update(status="yellow", how="name_attr" if one else "name_many",
                           offers=[_brief(one[0])] if one else [], cands=[_brief(o) for o in same[:8]])
            else:
                cands = _cands(r, offers)
                res["cands"] = [_brief(o) for o in cands]
                one = _by_attrs(r, cands)
                if one:
                    res.update(status="yellow", how="attr", offers=[_brief(one[0])])
                elif cands:
                    todo.append((res, cands))
        out.append(res)

    if use_model and llm.has_key() and todo:
        todo = todo[:MAX_MODEL_CALLS]
        with ThreadPoolExecutor(6) as ex:
            for (res, cands), (picked, c) in zip(todo, ex.map(lambda x: _pick(x[0], x[1]), todo)):
                usd += c
                chosen = _narrow(res, _by_brand(res, [cands[j] for j in picked if _same_kind(res, cands[j])]))
                how_one = "model_pick" if len(picked) == 1 else "model_pick_brand"
                if not chosen:
                    chosen = _rule_pick(res, cands)
                    how_one = "rule_pick"
                if len(chosen) == 1:
                    res.update(status="yellow", how=how_one, offers=[_brief(chosen[0])])
                elif chosen:
                    res.update(status="yellow", how="model_pick_many", cands=[_brief(o) for o in chosen])

    _brand_guard(out, by_id)
    _infer_units(out)
    for res in out:
        o = res["offers"][0] if res["offers"] else None
        res["change_pct"] = None
        res["big"] = False
        if o and res.get("toman") and o.get("toman"):
            pct = (res["toman"] - float(o["toman"])) / float(o["toman"]) * 100
            res["change_pct"] = round(pct, 1)
            res["big"] = abs(pct) > big_pct
    for res in out:                                      # keep the answer small: a sure row needs no choices
        res["cands"] = [] if res["status"] == "green" else res["cands"][:5]
    counts = {s: sum(1 for r in out if r["status"] == s) for s in ("green", "yellow", "new")}
    for o in offers:
        o.pop("_wn", None)
        o.pop("_hay", None)
    return {"rows": out, "counts": counts, "usd": round(usd, 4)}


def _infer_units(rows: list[dict]) -> None:
    """A file that never says rial or toman: the unit that puts its matched rows nearest to the supplier's current
    prices on the site (by a vote over the file), then every unit-less row of that file gets it."""
    votes: dict[str, list[str]] = {}
    for r in rows:
        if r.get("unit") or not r.get("price") or r.get("status") != "green" or not r.get("offers") \
                or not r["offers"][0].get("toman"):
            continue                                     # only sure matches may vote (a guess must not set the unit)
        cur = float(r["offers"][0]["toman"])
        best, gap = "", 9e9
        for unit in ("toman", "rial", "thousand_toman"):
            t = to_toman(r["price"], unit)
            if t:
                g = abs(math.log(t / cur))
                if g < gap:
                    best, gap = unit, g
        if best and gap < math.log(3):
            votes.setdefault(r.get("file", ""), []).append(best)
    pick = {f: max(set(v), key=v.count) for f, v in votes.items() if v}
    for r in rows:
        u = pick.get(r.get("file", ""))
        if r.get("unit") or not u or not r.get("price"):
            continue
        r["unit"], r["toman"] = u, to_toman(r["price"], u)
        r["note"] = "؛ ".join(x for x in ((r.get("note") or "").replace("واحد قیمت معلوم نیست", "").strip("؛ "),
                                          f"واحد ({'تومان' if u == 'toman' else 'ریال' if u == 'rial' else 'هزار تومان'}) از روی قیمت‌های فعلی حدس زده شد") if x)


def unmatched_offers(result_rows: list[dict], offers: list[dict]) -> list[dict]:
    """The supplier's offers no row of the list reached (the site may mark them 'not in the latest list')."""
    seen = {str(o["offer_id"]) for r in result_rows for o in r.get("offers") or []}
    return [_brief(o) for o in offers if str(o.get("offer_id")) not in seen]


def norm(s: str) -> str:
    return norm_text(s)
