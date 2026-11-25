import pl.match as m


def _offers():
    return [
        {"offer_id": 1, "name": "زانو ۹۰ درجه پلیکا قطر ۴۰ میلی متر برند ناردین پلیمر", "sku": "", "toman": 68200},
        {"offer_id": 2, "name": "زانو ۹۰ درجه سایز ۴۰ روکا گستر", "sku": "", "toman": 70000},
    ]


def _row(brand):
    return [{"key": "NPEL01", "model": "NP-EL01", "name": "زانو ۹۰ درجه قطر خارجی ۴۰", "brand": brand,
             "price": "660500", "unit": "rial", "toman": 66050}]


def _run(monkeypatch, brand):
    # The model (as on 25 Nov 2026) says both are the same: only the brand can decide.
    monkeypatch.setattr(m.llm, "has_key", lambda: True)
    monkeypatch.setattr(m, "_pick", lambda row, cands: (list(range(len(cands))), 0.0))
    return m.match(_row(brand), _offers())["rows"][0]


def test_brand_settles_two_same_names(monkeypatch):
    out = _run(monkeypatch, "ناردین پلیمر اسپادانا")
    assert out["offers"] and out["offers"][0]["offer_id"] == 1 and out["how"] == "model_pick_brand"


def test_distributor_brand_changes_nothing(monkeypatch):
    out = _run(monkeypatch, "پخش لوله محمد")
    assert not out["offers"] and len(out["cands"]) == 2


def test_rule_pick_kind_and_size(monkeypatch):
    offers = [
        {"offer_id": 1, "name": "زانو ۹۰ درجه پلیکا قطر ۲۰۰ میلی متر برند ناردین پلیمر", "sku": "", "toman": 1},
        {"offer_id": 2, "name": "سه راه ۹۰ درجه پلیکا قطر ۲۰۰ میلی متر برند ناردین پلیمر", "sku": "", "toman": 1},
        {"offer_id": 3, "name": "زانو ۹۰ درجه پلیکا قطر ۹۰ میلی متر برند ناردین پلیمر", "sku": "", "toman": 1},
        {"offer_id": 4, "name": "زانو ۹۰ درجه پلیکا قطر ۱۱۰ میلی متر برند ناردین پلیمر", "sku": "", "toman": 1},
    ]
    monkeypatch.setattr(m.llm, "has_key", lambda: True)
    monkeypatch.setattr(m, "_pick", lambda row, cands: ([], 0.0))   # the model gave no answer
    rows = [{"key": "A", "model": "NP-EL09", "name": "زانو ۹۰ درجه قطر خارجی ۲۰۰", "brand": "ناردین پلیمر اسپادانا"},
            {"key": "B", "model": "NP-EL05", "name": "زانو ۹۰ درجه قطر خارجی ۹۰", "brand": "ناردین پلیمر اسپادانا"}]
    out = m.match(rows, offers)["rows"]
    assert out[0]["offers"][0]["offer_id"] == 1 and out[0]["how"] == "rule_pick"
    assert out[1]["offers"][0]["offer_id"] == 3


def test_other_kind_is_not_picked():
    from pl.match import _same_kind
    row = {"name": "سوکت پله ای (موفه چسبی) قطر خارجی ۶۳"}
    assert not _same_kind(row, {"name": "رابط انبساطی (موفه) پلیکا قطر ۶۳ میلی متر"})
    assert _same_kind({"name": "چهارراه قطر خارجی ۱۱۰-۴۵"}, {"name": "چهار راه پلیکا قطر ۱۱۰-۴۵"})
    assert _same_kind({"name": "زانو 45 درجه سایز 40"}, {"name": "زانو ۴۵ درجه پلیکا قطر ۴۰"})
    assert _same_kind({"name": "فلانج قطر خارجی ۱۶۰"}, {"name": "فلنج پلیکا قطر ۱۶۰"})


def test_plain_product_wins_over_a_variant():
    from pl.match import _narrow
    row = {"name": "کلاهک قطر خارجی ۹۰", "brand": "ناردین پلیمر اسپادانا"}
    h = {"name": "کلاهک H پلیکا قطر ۹۰ میلی متر برند ناردین پلیمر"}
    plain = {"name": "کلاهک پلیکا قطر ۹۰ میلی متر برند ناردین پلیمر"}
    assert _narrow(row, [h, plain]) == [plain]
    assert _narrow({"name": "کلاهک H قطر خارجی ۹۰"}, [h, plain]) == [h]
