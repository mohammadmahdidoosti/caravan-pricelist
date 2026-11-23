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
from concurrent.futures import ThreadPoolExecutor

from . import llm
import math

from .text import to_toman, contains_model, norm_text, same_by_name, weak_key, words_nums

log = logging.getLogger(__name__)

SAME_SYSTEM = """You compare one row of a supplier's price list with the products this supplier sells in an online shop.
Pick every shop product that is the SAME product and the SAME variant as the row: the same size (and size unit), the
same material (استیل / آلومینیوم / برنج / گالوانیزه ...), the same type and design. Words may differ (digits Persian or
Latin, سایز 40 = 40 سانتی, ½ = 1/2). Another size, another variant, a spare part or an accessory is NOT the same.
Usually there is none or one. Reply JSON only: {"same": [<candidate numbers>]}"""

MAX_MODEL_CALLS = 600


def _cands(row: dict, offers: list[dict], n: int = 8) -> list[dict]:
    """Offers sharing words with the row and carrying all its numbers, best first."""
    lw, ln = words_nums(row.get("name") or row.get("model") or "")
    if not lw:
        return []
    scored = []
    for o in offers:
        pw, pn = o["_wn"]
        if ln and not ln <= pn:
            continue
        common = len(lw & pw)
        if common:
            scored.append((common / max(len(lw), 1) + common / max(len(pw), 1), o))
    scored.sort(key=lambda x: -x[0])
    return [o for _, o in scored[:n]]


def _pick(row: dict, cands: list[dict]) -> tuple[list[int], float]:
    user = f"row: {row.get('name') or row.get('model')}" + (f" (model {row['model']})" if row.get("model") else "") + "\n" + \
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
            hits = [o for o in offers if contains_model(o["_hay"], k)]
            if len(hits) == 1:
                res.update(status="green", how="model", offers=[_brief(hits[0])])
            elif hits:
                res.update(status="yellow", how="model_many", cands=[_brief(o) for o in hits[:8]])
        if res["status"] == "new":
            same = [o for o in offers if same_by_name(o.get("name", ""), r.get("name", ""))]
            if len(same) == 1:
                res.update(status="green", how="name", offers=[_brief(same[0])])
            elif same:
                res.update(status="yellow", how="name_many", cands=[_brief(o) for o in same[:8]])
            else:
                cands = _cands(r, offers)
                res["cands"] = [_brief(o) for o in cands]
                if cands:
                    todo.append((res, cands))
        out.append(res)

    if use_model and llm.has_key() and todo:
        todo = todo[:MAX_MODEL_CALLS]
        with ThreadPoolExecutor(6) as ex:
            for (res, cands), (picked, c) in zip(todo, ex.map(lambda x: _pick(x[0], x[1]), todo)):
                usd += c
                if len(picked) == 1:
                    res.update(status="yellow", how="model_pick", offers=[_brief(cands[picked[0]])])
                elif picked:
                    res.update(status="yellow", how="model_pick_many", cands=[_brief(cands[j]) for j in picked])

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
