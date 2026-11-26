"""(26 Nov) Web photo: only a candidate the picker accepts is used; no key -> nothing."""
import io

from PIL import Image

from pl import llm, websearch
from pl.photo import square


def _png(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(b, "PNG")
    return b.getvalue()


def test_no_key_no_search(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert websearch.find("چراغ استروب دزلی STL24", None) == (None, "", 0.0, 0)


def test_picks_the_accepted_candidate(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(websearch, "candidates", lambda q, n=6: [{"url": "https://a/1.png", "host": "a", "w": 400, "h": 400},
                                                                {"url": "https://b/2.png", "host": "b", "w": 500, "h": 500}])
    monkeypatch.setattr(websearch, "_get", lambda u: Image.open(io.BytesIO(_png(400 if "1" in u else 500, 400))).convert("RGB"))
    monkeypatch.setattr(llm, "ask", lambda *a, **k: ({"pick": 2, "why": "same"}, 0.001))
    im, url, usd, n = websearch.find("چراغ استروب دزلی STL24", None)
    assert url == "https://b/2.png" and im.width == 500 and n == 1


def test_null_pick_gives_nothing(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setattr(websearch, "candidates", lambda q, n=6: [{"url": "https://a/1.png", "host": "a", "w": 400, "h": 400}])
    monkeypatch.setattr(websearch, "_get", lambda u: Image.new("RGB", (400, 400)))
    monkeypatch.setattr(llm, "ask", lambda *a, **k: ({"pick": None}, 0.001))
    assert websearch.find("x", None)[0] is None


def test_square_is_webp():
    data = square(Image.new("RGB", (400, 300), "red"))
    assert Image.open(io.BytesIO(data)).size == (1000, 1000)
