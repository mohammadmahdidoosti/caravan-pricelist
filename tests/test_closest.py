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


def test_sheet_thickness_misread():
    from pl.text import same_by_name
    assert same_by_name("پنل شستی کابین آریانا یک متری نقره‌ای خشدار با عرض 18.5 (ورق 0.7 نگیر)",
                        "پنل شستی کابین آریانا یک متری نقره ای خشدار با عرض 18.5 ورق 7 نگیر")


def test_six_hundred_thousand_is_plain():
    from pl.read import merge
    r = merge([{"name": "کلید 206", "model": "", "price": "600,000", "unit": "thousand_toman"}])[0]
    assert r["toman"] == 600_000


def test_elevator_words_are_not_corrected():
    from pl.spell import fix_names
    rows = [{"name": "برد ریزشی دودات"}, {"name": "نمایشگر عکس وطبقات 8 اینچ"}, {"name": "کارتخوان تک کانله"}]
    fix_names(rows, [{"name": "برند آریانا نوین"}])
    assert rows[0]["name"].startswith("برد ")
    assert "و طبقات" in rows[1]["name"]
    assert "کاناله" in rows[2]["name"]


def test_catalog_index_find():
    from pl.catalog_index import find
    idx = {"pages": {"39": ["COP panel", "ECONOMY"], "74": ["Control board"], "12": ["display/monitor", "COP panel"]}}
    assert find(idx, ["economy"]) == [39]
    assert find(idx, ["panel", "display"])[0] == 12


def test_failed_text_chunk_is_read_in_halves(monkeypatch):
    from pl import read, llm
    calls = []
    def fake(msgs, max_tokens=8000, **k):
        t = msgs[1]["content"]
        calls.append(len(t))
        if len(t) > 3000:
            return None, 0.001
        return {"rows": [{"name": "x", "price": "1000"}]}, 0.001
    monkeypatch.setattr(llm, "ask", fake)
    rows, usd, n, bad = read._read_text(("سطر قیمت 1000\\n" * 300)[:4000], "a.xls")
    assert bad == 0 and len(rows) == 2 and n == 3


def test_headless_sheet():
    from pl.read import headless_rows
    vals = [[None, None, None, None, 1405], [None] * 5]
    sizes = ['1/8”', '3/16”', '1/4”', '5/16”', '3/8”', '1/2’’']
    for i, sz in enumerate(sizes):
        vals.append(["شيلنگ هيدروليك يك لاسيم سايز", 2140101100 + i * 100, "R1" if i == 0 else None, sz, 200000 + i * 36000.00000000003])
    vals.append(["شيلنگ هيدروليك دو لاسيم سايز", 2140201100, "R2", '1/4”', 410000])
    rows = headless_rows(vals)
    assert len(rows) == 7
    assert rows[0]["model"] == "2140101100" and rows[0]["price"] == "200000"
    assert "R1" in rows[2]["name"] and '1/4"' in rows[2]["name"]
    assert "R2" in rows[-1]["name"]


def test_old_excel_cell_keeps_text():
    from pl.read import _num
    assert _num("شیلنگ") == "شیلنگ"            # the old .xls reader must keep text cells (a helper once shadowed it)


def test_hose_wires_and_fraction_sizes():
    from pl.text import same_by_name, words_nums
    p2 = "شیلنگ هیدرولیک دو لاسیم سایز 1/4 1 ال ام سی R2-1 1/4 LMC"
    assert not same_by_name(p2, "شيلنگ هيدروليك يك لاسيم سايز 1 1/4 ال ام سي")       # one wire is not two
    assert "1-1/4" in words_nums("سایز 1 1/4")[1] and "1/4" in words_nums("سایز 1/4")[1]
    assert not same_by_name("شیلنگ هیدرولیک دو لاسیم سایز 1/4 ال ام سی", "شیلنگ هیدرولیک دو لاسیم سایز 1 1/4 ال ام سی")
    assert same_by_name("شیلنگ هیدرولیک دو لاسیم سایز 1 1/4 ال ام سی LMC", "شيلنگ هيدروليك دو لاسيم سايز 1 1/4 ال ام سي")


def test_reversed_size_and_one_quarter_row():
    from pl.text import same_by_name, words_nums
    assert "1-1/4" in words_nums("شیلنگ دو لاسیم سایز 1/4 1 ال ام سی")[1]
    p = "شیلنگ هیدرولیک دو لاسیم سایز 1/4 1 ال ام سی R2-1 1/4 LMC"
    assert not same_by_name(p, "شيلنگ هيدروليك دو لاسيم سايز 1/4 ال ام سي 1/4\"")
    assert same_by_name(p, "شيلنگ هيدروليك دو لاسيم سايز 1/4 1 ال ام سي 1 1/4\"")


def test_row_naming_a_maker_matches_only_that_maker():
    offers = [{"offer_id": 1, "name": "شیلنگ هیدرولیک دو لاسیم سایز 1/4 چینی", "sku": "", "toman": 300000, "brand": "چینی"},
              {"offer_id": 2, "name": "شیلنگ هیدرولیک دو لاسیم سایز 1/4 ال ام سی LMC", "sku": "", "toman": 500000, "brand": "ال ام سی"}]
    r = match([{"key": "N1", "name": "شيلنگ هيدروليك دو لاسيم سايز 1/4 ال ام سي 1/4\"", "model": "", "price": "272000",
                "unit": "toman", "brand": ""}], offers, use_model=False)["rows"][0]
    assert r["status"] == "green" and r["offers"][0]["offer_id"] == 2
    r = match([{"key": "N2", "name": "شيلنگ هيدروليك يك لاسيم سايز 1/4 ال ام سي", "model": "", "price": "220000",
                "unit": "toman", "brand": ""}], offers, use_model=False)["rows"][0]
    assert not r["offers"] and all(c["offer_id"] == 2 for c in r["cands"])


def test_maker_written_in_names_and_length_ranges():
    from pl.text import words_nums, same_by_name
    assert "15-7" in words_nums("شیلنگ سایز 1/2 طول 15-7 متر سمپریت")[1]
    assert "R2-3" not in words_nums("R2-3")[1] and "2-3" not in words_nums("ال ام سی R2-3 LMC")[1]
    assert not same_by_name("شیلنگ هیدرولیک یک لاسیم سایز 1/2 طول 15-7 متر سمپریت", "شيلنگ هيدروليك يك لاسيم سايز 1/2 طول بالاي 15 متر سمپريت")
    lmc = [{"offer_id": i, "name": f"شیلنگ هیدرولیک دو لاسیم سایز {s} ال ام سی R2-{s} LMC", "sku": "", "toman": 9e5, "brand": "LMC"}
           for i, s in enumerate(["1/4", "3/8", "1/2", "3/4"], 1)]
    chi = [{"offer_id": 10 + i, "name": f"شیلنگ هیدرولیک چهار لاسیم سایز {s} چینی", "sku": "", "toman": 9e5, "brand": "چینی"}
           for i, s in enumerate(["1", "1/2", "3/8"], 1)]
    r = match([{"key": "N1", "name": "شيلنگ هيدروليك يك لاسيم سايز 1 ال ام سي 1\"", "model": "", "price": "700000",
                "unit": "toman", "brand": ""}], lmc + chi, use_model=False)["rows"][0]
    assert all(c["offer_id"] < 10 for c in r["cands"] + r["offers"])


def test_makers_learnt_without_brand_field():
    from pl.match import _name_brands
    lmc = [{"name": f"شیلنگ هیدرولیک چهارلاسیم سایز {s} ال ام سی 4SH-{s} LMC", "brand": ""} for s in ["1", "3/4", "1/2", "3/8", "1/4", "5/8"]]
    chi = [{"name": f"شیلنگ هیدرولیک دو لاسیم سایز {s} چینی", "brand": ""} for s in ["1", "3/4", "1/2", "3/8", "1/4"]]
    fit = [{"name": f"اتصال پنوماتیک سایز {s}", "brand": ""} for s in ["1", "3/4", "1/2", "3/8", "1/4"]]
    got = _name_brands(lmc + chi + fit)["all"]
    assert "lmc" in got and "ال ام سی" in got and "چینی" in got and "سایز" not in got and "پنوماتیک" not in got
