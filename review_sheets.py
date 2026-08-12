#!/usr/bin/env python3
"""Build labelled contact sheets of every cached image, for eyeballing quality.

Each cell is centre-cropped to 16:10 exactly the way `.cr-img-wrap` +
`object-fit: cover` crops it in the browser, so what you review is what a
visitor actually sees - not the uncropped original.

    docker exec -it coaster-ranker python3 /app/review_sheets.py
    # sheets land in cache/review/ on the host (it's a bind mount)

Cells are numbered; cache/review/manifest.tsv maps each number back to the
coaster, park and cache key so anything bad can be re-picked by name.
"""

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend as b  # noqa: E402

COLS, ROWS = 6, 5
CELL_W, CELL_H = 320, 200          # 16:10, same shape as the card slot
LABEL_H = 26
PAD = 6
OUT_DIR = os.path.join(b.CACHE_DIR, "review")


def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    # python:3.11-slim ships no fonts; Pillow >= 10.1 can scale its bundled one.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def cover_crop(img, w, h):
    """Replicate CSS object-fit: cover."""
    src_ar, dst_ar = img.width / img.height, w / h
    if src_ar > dst_ar:                      # too wide - trim sides
        new_w = int(img.height * dst_ar)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    else:                                    # too tall - trim top/bottom
        new_h = int(img.width / dst_ar)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))
    return img.resize((w, h), Image.LANCZOS)


def rows_to_review(since=None):
    sql = """
        SELECT l.qkey, i.ckey, i.ext, i.width, i.height, l.ctype
        FROM lookup l JOIN images i ON i.ckey = l.ckey
    """
    args = ()
    if since:
        # Only what changed recently - handy for checking re-picks.
        sql += " WHERE l.updated >= ?"
        args = (since,)
    return b.db_query(sql + " ORDER BY l.qkey", args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--per-sheet", type=int, default=COLS * ROWS)
    ap.add_argument("--since", type=int,
                    help="only coasters whose pick changed at/after this unix time")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = rows_to_review(args.since)
    if not rows:
        print("nothing cached yet")
        return

    font = _font(13)
    small = _font(11)
    sheet_w = COLS * (CELL_W + PAD) + PAD
    sheet_h = ROWS * (CELL_H + LABEL_H + PAD) + PAD

    manifest = open(os.path.join(args.out, "manifest.tsv"), "w")
    manifest.write("index\tsheet\tcoaster\tpark\tckey\tdims\ttype\n")

    sheet = None
    draw = None
    n_sheets = 0
    for idx, (qkey, ckey, ext, w, h, ctype) in enumerate(rows):
        slot = idx % args.per_sheet
        if slot == 0:
            if sheet:
                sheet.save(os.path.join(args.out, f"sheet-{n_sheets:02d}.jpg"),
                           quality=88, optimize=True)
            n_sheets += 1
            sheet = Image.new("RGB", (sheet_w, sheet_h), (24, 24, 32))
            draw = ImageDraw.Draw(sheet)

        col, row = slot % COLS, slot // COLS
        x = PAD + col * (CELL_W + PAD)
        y = PAD + row * (CELL_H + LABEL_H + PAD)

        path = os.path.join(b.IMAGE_DIR, f"{ckey}.{ext}")
        try:
            with Image.open(path) as im:
                sheet.paste(cover_crop(im.convert("RGB"), CELL_W, CELL_H), (x, y))
        except Exception:
            draw.rectangle([x, y, x + CELL_W, y + CELL_H], fill=(70, 30, 30))
            draw.text((x + 8, y + 8), "unreadable", font=font, fill=(255, 180, 180))

        name, _, park = qkey.partition("|")
        label = f"{idx + 1}. {name[:26]}"
        draw.rectangle([x, y + CELL_H, x + CELL_W, y + CELL_H + LABEL_H], fill=(40, 40, 52))
        draw.text((x + 5, y + CELL_H + 3), label, font=font, fill=(240, 240, 250))
        draw.text((x + 5, y + CELL_H + 15), f"{park[:30]}  {w}x{h}", font=small,
                  fill=(150, 150, 170))

        manifest.write(f"{idx + 1}\t{n_sheets - 1}\t{name}\t{park}\t{ckey}\t{w}x{h}\t{ctype}\n")

    if sheet:
        sheet.save(os.path.join(args.out, f"sheet-{n_sheets:02d}.jpg"), quality=88, optimize=True)
    manifest.close()

    print(f"{len(rows)} images -> {n_sheets} sheets in {args.out}")
    print(f"manifest: {os.path.join(args.out, 'manifest.tsv')}")


if __name__ == "__main__":
    main()
