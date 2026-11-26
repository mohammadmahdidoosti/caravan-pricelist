"""(26 Nov) A product photo from the web when the catalog's own picture is too small.

A 600 px poster from a messenger holds a 60 px picture of each product: cut out and enlarged it is a blur. The
product is searched on Google Images (Serper, SERPER_API_KEY) by its name; up to six big candidates are downloaded
and one vision call picks the one that is the SAME product as the catalog's small picture and carries no other
shop's logo, watermark or text (a clean 400 px photo of a Dezli strobe from the maker's agent was right; the first
hit carried the logo of another shop). No key, no hit, or no clean match: None, and the catalog cut is used.
"""
from __future__ import annotations

import io
import logging
import os
import re

import requests
from PIL import Image, ImageOps

from . import llm

log = logging.getLogger(__name__)

SERPER = "https://google.serper.dev/images"
SKIP_HOSTS = ("instagram.", "pinterest.", "facebook.", "aparat.", "youtube.", "tiktok.")
MIN_SIDE = 300

PICK_SYSTEM = """You choose a product photo for an online shop. The first picture is a small picture of the product cut
from the supplier's catalog (it may be blurry); the next pictures are numbered candidates found on the web. Return JSON
only: {"pick": <number of the candidate>, "why": "<few words>"} or {"pick": null, "why": "<few words>"}.
Pick a candidate ONLY when all of these hold:
- it is the SAME product as the catalog picture and the product name (same shape, colour and kind; a different model
  of the same family is NOT the same product);
- the maker: when the product name has a brand (e.g. دزلی / Dezli) and the candidate shows another maker's name or logo
  ON the product (e.g. «POWER SMART» printed on a battery), it is NOT the same product; a generic item of another
  maker that only looks alike is NOT the same product either;
- its colour matches the catalog picture (a black battery is not the catalog's red battery);
- it shows the product itself, alone or nearly alone (not a poster, a price list, a box of many items, a person);
- no other shop's logo, watermark, phone number, website or price is printed over or beside the product (the
  maker's own logo ON the product is fine).
When none qualifies, pick null. Prefer the largest and cleanest of the good ones."""


def has_key() -> bool:
    return bool(os.environ.get("SERPER_API_KEY"))


def candidates(query: str, n: int = 6) -> list[dict]:
    """Big images for the query: [{url, w, h, host, title}], largest first."""
    try:
        r = requests.post(SERPER, json={"q": query, "gl": "ir", "num": 20},
                          headers={"X-API-KEY": os.environ.get("SERPER_API_KEY", ""), "Content-Type": "application/json"},
                          timeout=30)
        items = r.json().get("images") or [] if r.ok else []
    except (requests.RequestException, ValueError) as e:
        log.info("serper: %s", e)
        return []
    out = []
    for it in items:
        url = str(it.get("imageUrl") or "")
        w, h = int(it.get("imageWidth") or 0), int(it.get("imageHeight") or 0)
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if not url.startswith("http") or min(w, h) < MIN_SIDE or any(s in host for s in SKIP_HOSTS):
            continue
        out.append({"url": url, "w": w, "h": h, "host": host, "title": str(it.get("title") or "")[:120]})
    return out[:n]


def _get(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        if not r.ok:
            return None
        data = r.raw.read(6_000_000, decode_content=True)
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert("RGB")
        return im if min(im.size) >= MIN_SIDE else None
    except Exception as e:                               # noqa: BLE001 - a bad candidate is skipped
        log.info("candidate %s: %s", url[:80], e)
        return None


def _jpeg(im: Image.Image, side: int = 768) -> bytes:
    im = im.copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def find(name: str, crop: Image.Image | None, model: str | None = None) -> tuple[Image.Image | None, str, float, int]:
    """(chosen photo, its url, model cost $, searches made). The query is the product name as the site will show it."""
    if not has_key():
        return None, "", 0.0, 0
    cands = candidates(name)
    got = [(c, im) for c in cands for im in [_get(c["url"])] if im is not None]
    if not got:
        return None, "", 0.0, 1
    content: list = [{"type": "text", "text": f"Product: {name}\nPicture 0 is the catalog's picture."}]
    if crop is not None:
        content.append(llm.image_part(_jpeg(crop, 400)))
    for i, (c, im) in enumerate(got, 1):
        content.append({"type": "text", "text": f"Candidate {i} ({im.width}×{im.height}, from {c['host']})"})
        content.append(llm.image_part(_jpeg(im)))
    j, usd = llm.ask([{"role": "system", "content": PICK_SYSTEM}, {"role": "user", "content": content}],
                     max_tokens=200, title="Caravan product photo", model_id=model or os.environ.get(
                         "PL_MODEL_STRONG", "google/gemini-2.5-flash"))   # a wrong pick costs more than the call
    try:
        k = int((j or {}).get("pick"))
    except (TypeError, ValueError):
        return None, "", usd, 1
    if not 1 <= k <= len(got):
        return None, "", usd, 1
    c, im = got[k - 1]
    log.info("web photo for %s: %s (%s)", name[:60], c["url"][:100], (j or {}).get("why", ""))
    return im, c["url"], usd, 1
