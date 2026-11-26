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


def test_thousand_unit_with_full_price_is_plain():
    from pl.read import merge
    rows = merge([{"name": "تلفن دکمه ای", "model": "", "price": "5,500,000", "unit": "thousand_toman"},
                  {"name": "کلید", "model": "", "price": "5.500", "unit": "thousand_toman"}])
    by = {r["name"]: r for r in rows}
    assert by["تلفن دکمه ای"]["toman"] == 5_500_000 and by["تلفن دکمه ای"]["unit"] == "toman"
    assert by["کلید"]["toman"] == 5_500_000


MITSU = [
    "پنل شستی کابین آریانا دو متری طرح میتسوبیشی نقره‌ای خشدار با عرض 25 (ورق ۱ میل نگیر)",
    "پنل شستی کابین آریانا دو متری طرح میتسوبیشی نقره‌ای میرور با عرض 25 (ورق ۱ میل نگیر)",
    "پنل شستی کابین آریانا دو متری طرح میتسوبیشی طلایی میرور با عرض 25 (ورق ۱ میل نگیر)",
    "پنل شستی کابین آریانا دو متری طلایی میرورر با کلید آریانا عرض 18.5",
    "پنل شستی کابین آریانا دو متری طلایی میرور لیزری با عرض 18.5 (ورق 0.7 نگیر)",
    "شستی احضار طبقه میتسوبیشی طلایی با کلید 204",
]


def _one(name):
    r = match([_row(name)], _offers(MITSU), use_model=False)["rows"][0]
    return r["status"], (r["offers"][0]["name"] if r["offers"] else "")


def test_typo_in_site_name_is_still_exact():
    st, n = _one("پنل شستی کابین آریانا دو متری طلایی میرور با کلید آریانا عرض 18.5")
    assert st == "green" and "میرورر" in n


def test_same_attributes_is_a_suggestion():
    st, n = _one("پنل شستی کابین آریانا دو متری طرح میتسوبیشی نقره‌ای میرور با عرض 25 و ق میل ثابت")
    assert st == "yellow" and "نقره‌ای میرور" in n and "عرض 25" in n


def test_economy_is_another_product():
    st, n = _one("شستی احضار طبقه اکونومی طرح میتسوبیشی طلایی با کلید 204")
    assert n == ""
