"""(26 Nov) What is on each page of a picture-only catalog, for about $0.001 a catalog.

Ariana Novin's 74-page catalog has no text layer and almost no prices, so reading it gave 11 rows and nothing told
where a product's picture was. Pages are drawn small, 12 to a sheet with their numbers, and one cheap vision call
per sheet lists what each page shows. The answer is kept beside the job (index.json); a photo can then be cut from
the right page instead of searching the web.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from . import llm
from .photo import page_image

COLS, ROWS, W = 4, 3, 360
MODEL = "google/gemini-2.5-flash-lite"
ASK = ("This is a sheet of catalog pages of an Iranian industrial supplier; each page is labelled P<number> in its "
       "top-left corner. For every page, list the products shown (short names, Persian or English) and any model "
       "names printed. JSON only: {\"pages\": [{\"page\": <number>, \"items\": [\"...\"]}]}")


def sheet(path: Path, pages: list[int]) -> bytes:
    h = int(W * 1.45)
    im = Image.new("RGB", (COLS * W, ROWS * h), "white")
    d = ImageDraw.Draw(im)
    for i, p in enumerate(pages):
        pg = page_image(path, p, 50).convert("RGB")
        pg.thumbnail((W - 8, h - 30))
        x, y = (i % COLS) * W, (i // COLS) * h
        im.paste(pg, (x + 4, y + 26))
        d.rectangle([x, y, x + 60, y + 24], fill="black")
        d.text((x + 6, y + 4), f"P{p}", fill="yellow")
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


def index(path: Path, n_pages: int, cache: Path | None = None) -> dict:
    """{"pages": {page: [items]}, "usd": cost}; read from cache when there."""
    if cache and cache.exists():
        return json.loads(cache.read_text())
    out: dict = {"pages": {}, "usd": 0.0}
    for start in range(0, n_pages, COLS * ROWS):
        pages = list(range(start + 1, min(n_pages, start + COLS * ROWS) + 1))
        msg = [{"role": "user", "content": [{"type": "text", "text": ASK}, {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(sheet(path, pages)).decode()}}]}]
        ans, usd = llm.ask(msg, 3000, title="Caravan catalog index", model_id=MODEL)
        out["usd"] = round(out["usd"] + usd, 4)
        for pg in (ans or {}).get("pages", []):
            if isinstance(pg, dict) and str(pg.get("page", "")).isdigit():
                out["pages"][str(pg["page"])] = [str(x)[:120] for x in pg.get("items") or []][:20]
    if cache:
        cache.write_text(json.dumps(out, ensure_ascii=False))
    return out


def find(idx: dict, words: list[str]) -> list[int]:
    """Pages whose items mention any of the words (case-insensitive), best first."""
    hits = []
    for p, items in (idx.get("pages") or {}).items():
        text = " ".join(items).lower()
        n = sum(1 for w in words if w and w.lower() in text)
        if n:
            hits.append((-n, int(p)))
    return [p for _, p in sorted(hits)]
