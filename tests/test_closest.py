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


def test_maker_with_persian_and_latin_name():
    from pl.match import match
    lmc = [{"offer_id": i, "name": f"شیلنگ هیدرولیک دو لاسیم سایز {s} ال ام سی R2-{s} LMC", "sku": "", "toman": 9e5, "brand": "ال ام سی LMC"}
           for i, s in enumerate(["1/4", "3/8", "1/2"], 1)]
    mc = [{"offer_id": 10 + i, "name": f"شیلنگ هیدرولیک یک لاسیم سایز {s} ام سی", "sku": "", "toman": 9e5, "brand": "ام سی MC"}
          for i, s in enumerate(["1", "1/2", "3/8"], 1)]
    chi = [{"offer_id": 20 + i, "name": f"شیلنگ هیدرولیک یک لاسیم سایز {s} طول 15-7 متر سمپریت", "sku": "", "toman": 9e5, "brand": ""}
           for i, s in enumerate(["1", "1/2", "3/8"], 1)]
    r = match([{"key": "N1", "name": "شيلنگ هيدروليك يك لاسيم سايز 1/2 ال ام سي 1/2\"", "model": "", "price": "700000",
                "unit": "toman", "brand": ""}], lmc + mc + chi, use_model=False)["rows"][0]
    assert all(c["offer_id"] < 10 for c in r["cands"] + r["offers"])


def test_maker_is_whole_words():
    from pl.match import _has_words
    assert not _has_words("سیم", "شیلنگ یک لاسیم") and not _has_words("هیدرو", "شیلنگ هیدرولیک")
    assert _has_words("ال ام سی", "شیلنگ ال ام سی 1/2")


def test_glued_inch_size():
    from pl.text import words_nums
    assert words_nums("شیلنگ نخدار روغن سمپریت سایز 11/4")[1] == words_nums("شیلنگ نخدار روغن سمپریت سایز 1 1/4")[1]
    assert "1/4" in words_nums("شیلنگ سایز 1/4")[1]


def test_pdf_text_line_settles_price(tmp_path):
    from reportlab.pdfgen import canvas
    from pl.read import _check_with_text
    f = tmp_path / "list.pdf"
    c = canvas.Canvas(str(f))
    c.drawString(40, 700, "2137719  10  12/108  19,100,000  ETIMAT 10 C 32A 220V DC 6KA 1P")
    c.drawString(40, 680, "2137720  11  12/108  26,700,000  ETIMAT 10 C 40A 220V DC 6KA 1P")
    c.save()
    rows = [{"model": "2137719", "price": "19,200,000", "file": "list.pdf ص1", "check": "قیمت در خواندن دوم 19100000 خوانده شد؛ بررسی کنید"},
            {"model": "2137720", "price": "26,700,000", "file": "list.pdf ص1", "check": "قیمت در خواندن دوم 19100000 خوانده شد؛ بررسی کنید"}]
    _check_with_text([f], rows, "rial")
    assert rows[0]["price"] == "19,100,000" and "PDF" in rows[0]["check"]
    assert rows[1]["price"] == "26,700,000" and "check" not in rows[1]


def test_brands_settle_to_the_maker():
    from pl.read import settle_brands
    rows = [{"brand": "ASTI", "name": f"کلید مینیاتوری ETIMAT 10 B {a}A"} for a in range(1, 9)] + \
           [{"brand": "ETI", "name": f"فیوز سیلندری CH10X38 {a}A"} for a in range(1, 9)] + \
           [{"brand": "ETIBREAK", "name": f"کلید اتوماتیک EB2 {a}"} for a in range(1, 7)] + \
           [{"brand": "LS", "name": "کنتاکتور MC-9"}]
    out = settle_brands(rows)
    assert all(r["brand"] == "ETI" for r in out[:22]) and out[-1]["brand"] == "LS"


def test_text_pdf_pages_found(tmp_path):
    from reportlab.pdfgen import canvas
    from pl.read import pdf_text_pages, _fa_order
    f = tmp_path / "cat.pdf"
    c = canvas.Canvas(str(f))
    y = 780
    for k in range(12):
        c.drawString(40, y, f"21217{10 + k}  {k + 1}  12/108  {10 + k},500,000  ETIMAT 10 B {k + 2}A 230/400V AC 10KA 1P")
        y -= 18
    c.showPage()
    c.drawString(40, 700, "picture only page")
    c.save()
    pages = pdf_text_pages(f)
    assert list(pages) == [1] and len(pages[1].splitlines()) == 12 and "2121710" in pages[1].splitlines()[0]
    assert _fa_order("ETIMAT مینیاتوری کلید 2137714") == "ETIMAT کلید مینیاتوری 2137714"


def test_longer_name_picks_its_own_product():
    # 27 Nov (محمد / روکا گستر): «زانو مغزی فلزی بست دار» passed the name test for «زانو فلزی بست دار» too and stayed grey
    offers = _offers(["زانو فلزی بست دار سایز ۲۰*۱/۲ روکا گستر", "زانو مغزی فلزی بست دار سایز ۲۰*۱/۲ روکا گستر"], brand="روکا گستر")
    r = match([_row("زانو مغزی فلزی بست دار 20*1/2")], offers, use_model=False)["rows"][0]
    assert r["offers"] and r["offers"][0]["offer_id"] == 2


def test_table_code_with_dropped_zero_takes_text_price(tmp_path):
    # 27 Nov (روکا گستر): two price tables side by side; the picture read «۴۲۰۱» as 421 and 2,198,084 as 3,198,084
    from reportlab.pdfgen import canvas
    from pl.read import _check_with_text
    f = tmp_path / "rk.pdf"
    c = canvas.Canvas(str(f))
    c.drawString(20, 700, "1,713,659  70  20*1/2  4201    2,198,084  50  50  2409")
    c.drawString(20, 680, "2,131,977  60  25*1/2  4203    3,025,198  25  63  2411")
    c.drawString(20, 660, "-  15  75  2413")
    c.save()
    rows = [{"model": "421", "name": "زانو فلزی بست دار 20*1/2", "price": "1,713,659", "file": "rk.pdf ص1"},
            {"model": "2409", "name": "زانو 45 درجه 50", "price": "3,198,084", "file": "rk.pdf ص1"},
            {"model": "423", "name": "زانو فلزی بست دار 25*1/2", "price": "2,131,977", "file": "rk.pdf ص1"},
            {"model": "2413", "name": "زانو 45 درجه 75", "price": "1,700,000", "file": "rk.pdf ص1"}]
    _check_with_text([f], rows, "rial")
    assert rows[0]["model"] == "4201" and rows[0]["price"] == "1,713,659" and "check" not in rows[0]
    assert rows[1]["price"] == "2,198,084" and "PDF" in rows[1]["check"]
    assert rows[2]["model"] == "4203"
    assert rows[3]["price"] == "1,700,000"               # no price printed for 2413: nothing taken from 2411


def test_side_by_side_tables_are_not_read_as_text():
    from pl.read import side_by_side
    two = "\n".join(f"1,{i:03d},000  50  20  {4000 + i}    2,{i:03d},000  25  63  {2400 + i}" for i in range(10))
    one = "\n".join(f"21377{i:02d}  10  12/108  19,{i:03d},000  ETIMAT 10 C 32A" for i in range(10))
    assert side_by_side(two) and not side_by_side(one)


def test_table_cells_with_three_digit_counts():
    from pl.read import _table_codes
    t = ("226,233 300 20 1001 1,856,488 30 20 0001\n"
         "قیمت شاخه اندازه کد لوله 1201 20*25 350 199,527\n"
         "_ 25 75*63 1231 38,078,640 3 75 0113")
    got = _table_codes(t)
    assert got["1001"][0] == 226233 and got["0001"][0] == 1856488
    assert got["1201"][0] == 199527 and got["0113"][0] == 38078640 and "1231" not in got


def test_table_cells_end_at_words_and_missing_prices():
    from pl.read import _table_codes
    t = ("2,198,084 50 50 2409 فلزی زانو\n"
         "2411 63 25 3,025,198 قیمت تعداد اندازه کد فلزی زانو\n"
         "1,616,905 110 20*1/2 4001 15 75 2413\n"
         "5,930,110 12 63 3011 2657774 72*1/2 441")
    got = _table_codes(t)
    assert got["2409"][0] == 2198084 and got["2411"][0] == 3025198
    assert got["4001"][0] == 1616905 and "2413" not in got and got["3011"][0] == 5930110


def test_glued_cells_and_one_digit_misread_code():
    from pl.read import _table_codes, _table_price
    t = _table_codes("3207 40*20*40 30 1,329,051 قیمت تعداد\n"
                     "قیمت تعداد اندازه کد راهی سه 3,725,13124 20*1/2 4301\n"
                     "هرورزی 61032,744,678 80 25*3/4")
    assert t["3207"][0] == 1329051 and t["4301"][0] == 3725131 and t["6103"][0] == 2744678
    assert _table_price(t, "2207", "سه راهی تبدیل 40*20*40") == ("3207", 1329051)
    assert _table_price(t, "2205", "سه راهی تبدیل 40*20*40") is None


def test_misread_code_with_common_size_needs_its_price():
    from pl.read import _table_codes, _table_price
    t = _table_codes("7,110,857 40 20 6401\n1,110,857 40 20 6501\n2,517,131 150 20*1/2 6201")
    assert _table_price(t, "691", "شیر تکضرب 20", 7110857) == ("6401", 7110857)
    assert _table_price(t, "691", "شیر تکضرب 20", 7110000) is None
    assert _table_price(t, "6301", "سه راهی 20*1/2", 123) is None
