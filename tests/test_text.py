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
