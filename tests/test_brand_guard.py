"""(26 Nov) A Dezli list must not be matched green to another maker's products."""
from pl.match import match


def _offers():
    return [{"offer_id": 1, "name": "دتکتور حرارتی ثابت (24 ولت)", "sku": "", "toman": 1512000, "brand": "هیرمان"},
            {"offer_id": 2, "name": "دتکتور دودی دزلی مدل DZ-SD24 (24 ولت)", "sku": "", "toman": 712000, "brand": "دزلی"},
            {"offer_id": 3, "name": "دتکتور حرارتی افزایشی (24 ولت)", "sku": "", "toman": 1515000, "brand": ""}]


def _row(model, name, brand="دزلی"):
    return {"key": model.replace("-", ""), "model": model, "name": name, "brand": brand, "toman": 1475000, "price": "",
            "unit": "toman", "stock": "", "file": "p"}


def test_other_brand_is_never_green():
    m = match([_row("DZ-HD24F", "دتکتور حرارتی ثابت 24 ولت")], _offers(), use_model=False)
    r = m["rows"][0]
    assert r["status"] != "green" and not r["offers"]
    assert "برند" in (r.get("note") or "") or r["status"] == "new"   # (26 Nov) another maker is not even a candidate


def test_same_brand_by_model_stays_green():
    m = match([_row("DZ-SD24", "دتکتور دودی فتوالکتریک 24 ولت")], _offers(), use_model=False)
    assert m["rows"][0]["status"] == "green"


def test_name_match_without_the_model_code_is_only_yellow():
    m = match([_row("DZ-HD24R", "دتکتور حرارتی افزایشی 24 ولت")], _offers(), use_model=False)
    r = m["rows"][0]
    assert r["status"] != "green"
