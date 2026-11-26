from pl.match import match, _same_brand_text


def _offers(names, brand="آریانا نوین"):
    return [{"offer_id": i + 1, "name": n, "sku": "", "toman": 20_000_000, "brand": brand} for i, n in enumerate(names)]


SIBLINGS = [
    "پنل شستی کابین آریانا دو متری نقره‌ای خشدار با کلید آریانا عرض 18.5",
    "پنل شستی کابین آریانا دو متری نقره‌ای میرور با کلید آریانا عرض 18.5",
    "پنل شستی کابین آریانا دو متری طلایی میرور با کلید آریانا عرض 18.5",
]


def _row(name, brand=""):
    return {"key": "N1", "name": name, "model": "", "price": "24500000", "unit": "toman", "brand": brand}


def test_exact_sibling_is_green():
    m = match([_row("پنل شستی کابین آریانا دو متری نقره ای خشدار با کلید آریانا عرض 18.5")], _offers(SIBLINGS), use_model=False)
    r = m["rows"][0]
    assert r["status"] == "green" and r["offers"][0]["offer_id"] == 1


def test_no_exact_sibling_stays_yellow():
    m = match([_row("پنل شستی کابین آریانا دو متری نقره ای با کلید آریانا عرض 18.5")], _offers(SIBLINGS), use_model=False)
    assert m["rows"][0]["status"] == "yellow"


def test_misread_brand_is_same_maker():
    for b in ("آریاناتوین", "آریاناتین", "آریانانوین", "آریانوین"):
        assert _same_brand_text(b, "آریانا نوین"), b
    assert not _same_brand_text("هیرمان", "دزلی")
    m = match([_row("شستی احضار طبقه طرح آس دودی A100", "آریاناتوین")],
              _offers(["شستی احضار طبقه طرح آس دودی A100"]), use_model=False)
    assert m["rows"][0]["status"] == "green"
