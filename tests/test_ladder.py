"""(26 Nov) The reading ladder: main model -> Gemini Flash-Lite -> Gemini Flash, only for pages that look wrong."""
import io

from PIL import Image

from pl import llm, read


def _pic(w=600, h=1040):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


def test_small_picture_is_enlarged_in_strips():
    parts = read.prepare(_pic())
    assert len(parts) == 3
    im = Image.open(io.BytesIO(parts[0]))
    assert im.width == 1500
    big = _pic(2000, 3000)
    assert read.prepare(big) == [big]


def _fake(answers):
    """answers[model] = list of (model code, price) the model reads; the check reading uses the main model."""
    calls = []

    def ask(messages, max_tokens=4000, title="", model_id=None):
        m = model_id or llm.model()
        calls.append(m)
        rows = [{"model": k, "name": "دتکتور " + k, "price": p, "unit": "rial"} for k, p in answers[m]]
        return {"rows": rows}, 0.001
    return ask, calls


def test_page_climbs_until_two_readings_agree(monkeypatch):
    monkeypatch.setenv("PL_MODEL", "deepseek/x")
    ask, calls = _fake({"deepseek/x": [("DZ-SD24", "1750000"), ("DZ-PB24", "1350000")],
                        read.LITE: [("DZ-SD24", "14750000"), ("DZ-PB24", "13750000")],
                        read.STRONG: [("DZ-SD24", "14750000"), ("DZ-PB24", "13750000")]})
    monkeypatch.setattr(llm, "ask", ask)
    pg = {"label": "p", "parts": [b"x"], "readings": [], "file": None}
    read.read_stage(pg, read.ladder()[0])
    assert read.bad_rows(pg["rows"]) == 0            # both cheap readings agree on the wrong price
    cost, _ = read.climb(pg, "far from the site's prices")
    assert [x[0] for x in pg["readings"]] == ["deepseek/x", read.LITE, read.STRONG]
    assert {r["model"]: r["price"] for r in pg["rows"]} == {"DZ-SD24": "14750000", "DZ-PB24": "13750000"}
    assert read.bad_rows(pg["rows"]) == 0             # the strong reading agrees with the lite one
    assert set(cost) == {"lite", "strong"}


def test_climb_stops_when_lite_agrees(monkeypatch):
    monkeypatch.setenv("PL_MODEL", "deepseek/x")
    ask, calls = _fake({"deepseek/x": [("A100", "1000"), ("B200", "2000")],
                        read.LITE: [("A100", "1000"), ("B200", "2000")],
                        read.STRONG: [("A100", "9"), ("B200", "9")]})
    monkeypatch.setattr(llm, "ask", ask)
    pg = {"label": "p", "parts": [b"x"], "readings": [], "file": None}
    read.read_stage(pg, read.ladder()[0])
    read.climb(pg, "test")
    assert read.STRONG not in calls
    assert read.bad_rows(pg["rows"]) == 0


def test_dropped_thousands_group_is_noted():
    rows = [{"price": p} for p in ("267000000", "257000000", "274000000", "257000", "279000000")]
    read.digit_check(rows)
    assert [bool(r.get("check")) for r in rows] == [False, False, False, True, False]


def test_escalation_off(monkeypatch):
    monkeypatch.setenv("PL_ESCALATE", "0")
    assert len(read.ladder()) == 1
