"""A product's photo cut out of its catalog page, made like the site's own photos (white square, Caravan logo).

A vision model finds the picture that belongs to the product name on the page (a box in page fractions); the box is
cut from a sharp render, trimmed to the product (grey/white object; the catalog's coloured background and empty
margins dropped), centred on a white 1000 px square, and stamped with the Caravan logo bottom-right, the way
product-crawler's images.make does it. A person sees the photo before the product is published.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

from . import llm

log = logging.getLogger(__name__)

LOGO = Path(__file__).resolve().parent.parent / "assets" / "caravan-logo.png"

FIND_SYSTEM = """You look at one page of a supplier's catalog. Find the PRODUCT PICTURE (the photo or drawing of the item
itself, not its title text, not a table, not the maker's logo or a decoration) that belongs to the product named by
the user. Catalog pages usually print one picture beside each block of rows, with the block's product name under or
next to it. Return JSON only: {"box": [x0, y0, x1, y1]} in fractions of the page width and height (0..1), tight
around the picture, or {"box": null} when the page shows no picture for that product."""


def page_image(path: Path, page: int, dpi: int) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return pdf.pages[page - 1].to_image(resolution=dpi).original.convert("RGB")
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def find_box(img: Image.Image, name: str) -> tuple[list[float] | None, float]:
    small = img.copy()
    small.thumbnail((1600, 1600))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    j, usd = llm.ask([{"role": "system", "content": FIND_SYSTEM},
                      {"role": "user", "content": [{"type": "text", "text": f"Product: {name}"},
                                                   llm.image_part(buf.getvalue())]}], max_tokens=300,
                     title="Caravan catalog photo")
    box = (j or {}).get("box")
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None, usd
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or (x1 - x0) * (y1 - y0) > 0.5:
        return None, usd
    return [x0, y0, x1, y1], usd


def _object_box(im: Image.Image) -> tuple[int, int, int, int] | None:
    """Box of the product: pixels that are not near-white and not strongly coloured (the catalog's purple/blue
    background is coloured; PVC fittings are grey or white)."""
    hsv = im.convert("HSV")
    w, h = im.size
    px, hp = im.load(), hsv.load()
    xs, ys = [], []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            s = hp[x, y][1]
            if min(r, g, b) < 235 and s < 60:
                xs.append(x)
                ys.append(y)
    if len(xs) < 50:
        return None
    xs.sort()
    ys.sort()
    k = max(1, len(xs) // 200)                         # drop a few stray pixels at the edges
    return xs[k], ys[k], xs[-k] + 2, ys[-k] + 2


def make(img: Image.Image, box: list[float], size: int = 1000) -> Image.Image:
    W, H = img.size
    mx, my = 0.01 * W, 0.01 * H
    crop = img.crop((max(0, int(box[0] * W - mx)), max(0, int(box[1] * H - my)),
                     min(W, int(box[2] * W + mx)), min(H, int(box[3] * H + my))))
    ob = _object_box(crop)
    if ob:
        crop = crop.crop(ob)
    side = int(max(crop.size) / 0.84)                  # the product fills ~84% of the square
    out = Image.new("RGB", (side, side), (255, 255, 255))
    out.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    out = out.resize((size, size), Image.LANCZOS)
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        lw = int(size * 0.09)
        logo = logo.resize((lw, max(1, int(logo.height * lw / logo.width))), Image.LANCZOS)
        m = int(size * 0.025)
        out.paste(logo, (size - lw - m, size - logo.height - m), logo)
    return out


def catalog_photo(path: Path, page: int, name: str) -> tuple[bytes | None, list[float] | None, float]:
    """(webp bytes, box, cost) of the product's picture on that page; (None, None, cost) when none was found."""
    img = page_image(path, page, 300)
    box, usd = find_box(img, name)
    if not box:
        return None, None, usd
    pic = make(img, box)
    buf = io.BytesIO()
    pic.save(buf, "WEBP", quality=88)
    return buf.getvalue(), box, usd
