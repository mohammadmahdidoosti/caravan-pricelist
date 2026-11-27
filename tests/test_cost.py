"""(27 Nov) Cost: a page whose own PDF text settles its doubtful rows does not climb the ladder; a read list can be
rebuilt from its kept readings without asking any model."""
import json

from pl import llm, read


def _pdf(path, lines):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for i, t in enumerate(lines):
        c.drawString(50, 800 - 20 * i, t)
    c.save()


def _page(f, rows, check):
    return {"label": f"{f.name} ص1", "file": f, "readings": [("deepseek/x", rows)], "check": {"rows": check}}


def test_text_settles_doubtful_rows(tmp_path):
    f = tmp_path / "list.pdf"
    _pdf(f, ["2137719 Fuse 19,100,000", "2137720 Fuse 26,700,000", "2137721 Fuse 31,000,000",
             "2137722 Fuse 32,000,000", "2137723 Fuse 33,000,000"])
    rows = [{"model": "2137719", "price": "19,200,000", "file": "list.pdf ص1"},
            {"model": "2137720", "price": "26,700,000", "file": "list.pdf ص1"}]
    pg = _page(f, rows, [{"model": "2137719", "price": "19100000"}, {"model": "2137720", "price": "26000000"}])
    pg["rows"] = read.page_rows(pg)
    assert read.bad_rows(pg["rows"]) == 2
    assert read.text_left([f], {pg["label"]: pg}, "auto") == {pg["label"]: 0}
    assert read.bad_rows(pg["rows"]) == 2            # tried on copies: the page itself is unchanged
    assert pg["label"] in read.priced_text_pages([f])


def test_no_text_keeps_doubt(tmp_path):
    f = tmp_path / "scan.pdf"
    _pdf(f, ["no prices here"])
    rows = [{"model": "AB-100", "price": "1,000", "file": "scan.pdf ص1"}]
    pg = _page(f, rows, [{"model": "AB-100", "price": "9000"}])
    pg["rows"] = read.page_rows(pg)
    assert read.text_left([f], {pg["label"]: pg}, "auto")[pg["label"]] == 1
    assert pg["label"] not in read.priced_text_pages([f])


def test_readings_kept_and_rebuilt_for_free(tmp_path, monkeypatch):
    files = tmp_path / "files"
    files.mkdir()
    f = files / "00-list.pdf"
    _pdf(f, ["2137719 Fuse 19,100,000"])
    rows = [{"model": "2137719", "name": "فیوز", "price": "19,200,000", "file": "00-list.pdf ص1"}]
    pg = _page(f, rows, [{"model": "2137719", "price": "19100000"}])
    pg["rows"] = read.page_rows(pg)
    got = {"_pages": {pg["label"]: pg}, "_other": [{"model": "X1", "name": "شیر توپی", "price": "5,000,000", "unit": "rial", "file": "s.xlsx"}],
           "failed": [], "pages": 1}
    read.save_readings(got, tmp_path / "readings.json")
    assert "parts" not in json.loads((tmp_path / "readings.json").read_text())["pages"][pg["label"]]
    monkeypatch.setattr(llm, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model may be asked")))
    again = read.load_readings(tmp_path / "readings.json", files)
    prices = {r.get("model"): r.get("price") for r in again["rows"]}
    assert prices["2137719"].replace(",", "") == "19100000"   # the catalog's printed price, as on the first run
    assert "X1" in prices and again["usd"] == 0
