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
    """Box of the product: the largest connected patch of pixels that are not near-white and not strongly coloured
    (the catalog's purple/blue background is coloured; PVC fittings are grey or white). The block's title text and
    thin frame lines are separate, smaller patches and are left out."""
    w, h = im.size
    step = max(1, max(w, h) // 220)                   # work on a ~220 px grid
    gw, gh = w // step, h // step
    small = im.resize((gw, gh), Image.BILINEAR)
    hsv = small.convert("HSV").load()
    px = small.load()
    on = [[False] * gw for _ in range(gh)]
    for y in range(gh):
        for x in range(gw):
            r, g, b = px[x, y]
            # not white, not strongly coloured, not black (titles are printed in black; PVC is grey/white)
            on[y][x] = min(r, g, b) < 238 and hsv[x, y][1] < 70 and max(r, g, b) > 75
    seen = [[False] * gw for _ in range(gh)]
    best, best_box, best_cells = 0, None, []
    for y0 in range(gh):
        for x0 in range(gw):
            if not on[y0][x0] or seen[y0][x0]:
                continue
            stack, n, cells = [(x0, y0)], 0, []
            seen[y0][x0] = True
            bx0 = bx1 = x0
            by0 = by1 = y0
            while stack:
                x, y = stack.pop()
                n += 1
                cells.append((x, y))
                bx0, bx1, by0, by1 = min(bx0, x), max(bx1, x), min(by0, y), max(by1, y)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < gw and 0 <= ny < gh and on[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if n > best:
                best, best_box, best_cells = n, (bx0, by0, bx1, by1), cells
    if not best_box or best < 30:
        return None
    # a title printed right under (or over) the picture can touch it: cut the patch at thin rows and keep the
    # biggest part
    rows: dict[int, int] = {}
    for _, y in best_cells:
        rows[y] = rows.get(y, 0) + 1
    widest = max(rows.values())
    parts, cur = [], []
    for y in range(best_box[1], best_box[3] + 1):
        if rows.get(y, 0) <= max(1, int(0.08 * widest)):
            if cur:
                parts.append(cur)
            cur = []
        else:
            cur.append(y)
    if cur:
        parts.append(cur)
    if len(parts) > 1:
        keep_rows = set(max(parts, key=lambda ys: sum(rows[y] for y in ys)))
        best_cells = [(x, y) for x, y in best_cells if y in keep_rows]
        xs = [x for x, _ in best_cells]
        best_box = (min(xs), min(keep_rows), max(xs), max(keep_rows))
    x0, y0, x1, y1 = best_box
    keep = Image.new("L", (gw, gh), 0)
    kp = keep.load()
    for x, y in best_cells:
        kp[x, y] = 255
    from PIL import ImageFilter
    keep = keep.filter(ImageFilter.MaxFilter(3))   # close small holes, keep edges
    _object_box.mask = keep.resize((w, h), Image.BILINEAR)
    return max(0, x0 * step - step), max(0, y0 * step - step), min(w, (x1 + 2) * step), min(h, (y1 + 2) * step)


def make(img: Image.Image, box: list[float], size: int = 1000) -> Image.Image:
    W, H = img.size
    mx, my = 0.01 * W, 0.01 * H
    crop = img.crop((max(0, int(box[0] * W - mx)), max(0, int(box[1] * H - my)),
                     min(W, int(box[2] * W + mx)), min(H, int(box[3] * H + my))))
    ob = _object_box(crop)
    if ob:
        white = Image.new("RGB", crop.size, (255, 255, 255))
        crop = Image.composite(crop, white, _object_box.mask).crop(ob)   # everything but the product turns white
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
        import re
        short = re.split(r"[\d۰-۹]", name, maxsplit=1)[0].strip()
        if short and short != name:
            box, u2 = find_box(img, short)
            usd += u2
    if not box:
        return None, None, usd
    pic = make(img, box)
    buf = io.BytesIO()
    pic.save(buf, "WEBP", quality=88)
    return buf.getvalue(), box, usd
