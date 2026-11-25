from pl.text import to_toman, contains_model, same_by_name, row_key


def test_to_toman():
    assert to_toman("48/000/000", "rial") == 4800000
    assert to_toman("۲۲۹٬۸۷۱", "toman") == 229871
    assert to_toman("22.8", "thousand_toman") == 22800


def test_model():
    assert contains_model("دریل VR2010 BLX", "VR2010BLX")
    assert not contains_model("VR20105", "VR2010")


def test_name():
    assert same_by_name("سانورتر 6 کیلووات گرووات", "سانورتر 6 گرووات")
    assert not same_by_name("سانورتر 12 کیلووات گرووات", "سانورتر 6 گرووات")
    assert row_key("", "شیلنگ دوسر مهره 40 استیل").startswith("N")


def test_size_column_joins_name():
    from pl.read import _with_size
    assert _with_size("رابط انبساطی (موفه)", "قطر خارجی ۹۰") == "رابط انبساطی (موفه) قطر خارجی ۹۰"
    assert _with_size("زانو 45 درجه سایز 40", "40") == "زانو 45 درجه سایز 40"
    assert _with_size("سوکت", "") == "سوکت"


def test_misread_title_words_are_put_right():
    from pl.spell import fix_names
    offers = [{"name": "کلاهک پلیکا قطر ۹۰ ناردین"}, {"name": "سوکت پله ای پلیکا ۶۳"}]
    rows = [{"name": "بوشن ترمودار قطر خارجی ۶۳"}, {"name": "کلاک قطر خارجی ۹۰"}, {"name": "کمرند تعمیری قطر خارجی ۹۰"}, {"name": "کمرینگ تعمیری ۶۳"},
            {"name": "سوکت پله ای (موفه چسبی) قطر خارجی ۶۳"}]
    assert fix_names(rows, offers) == 4
    assert rows[0]["name"].startswith("بوشن ترمزدار")
    assert rows[1]["name"].startswith("کلاهک ")
    assert rows[2]["name"].startswith("کمربند تعمیری")
    assert rows[3]["name"].startswith("کمربند")
    assert rows[4]["name"] == "سوکت پله ای (موفه چسبی) قطر خارجی ۶۳"
    assert "ترمودار → ترمزدار" in rows[0]["note"]


def test_second_reading_flags_digits_and_invented_rows():
    from pl.read import cross_check
    rows = [{"model": "NP-EI07", "price": "۳,۴۳۹,۶۰۰"}, {"model": "NP-EI06", "price": "3634000"},
            {"model": "NP-MVCD1", "price": "297600"}]
    cross_check(rows, {"rows": [{"model": "NP-EI07", "price": "3339600"}, {"model": "NPEI06", "price": "۳,۶۳۴,۰۰۰"}]})
    assert "3339600" in rows[0]["check"]
    assert "check" not in rows[1]
    assert "دیده نشد" in rows[2]["check"]
