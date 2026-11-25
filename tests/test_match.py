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
