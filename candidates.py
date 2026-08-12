#!/usr/bin/env python3
"""Show several candidate photos per coaster so a good one can be chosen directly.

Blind "reject and take the next best" converges slowly on RCDB pages that are
mostly signage and construction. This lays out the top N landscape candidates
for each coaster as one row per ride, so the right photo can be picked in a
single pass.

    # build sheets for the coasters listed in a file (one "name|park" per line)
    docker exec -it coaster-ranker python3 /app/candidates.py --qkeys /app/cache/bad.txt

    # then apply choices ("name|park<TAB>candidate number" per line)
    docker exec -it coaster-ranker python3 /app/candidates.py --choose /app/cache/picks.tsv

Review thumbnails come from RCDB's own small sizes, so building the sheets is
cheap; only the chosen photo is fetched at full resolution.
"""

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend as b  # noqa: E402

N_CAND = 6
CELL_W, CELL_H = 300, 188
LABEL_H = 22
ROW_LABEL_H = 20
PAD = 5
ROWS_PER_SHEET = 5
OUT_DIR = os.path.join(b.CACHE_DIR, "candidates")


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def cover_crop(img, w, h):
    sa, da = img.width / img.height, w / h
    if sa > da:
        nw = int(img.height * da)
        img = img.crop(((img.width - nw) // 2, 0, (img.width - nw) // 2 + nw, img.height))
    else:
        nh = int(img.width / da)
        img = img.crop((0, (img.height - nh) // 2, img.width, (img.height - nh) // 2 + nh))
    return img.resize((w, h), Image.LANCZOS)


def candidates_for(rcdb_path, exclude, n=N_CAND):
    """Top n landscape pictures: [(score, thumb_url, full_url, w, h), ...]"""
    try:
        html = b._get_text(b.RCDB + rcdb_path)
    except Exception:
        return []
    m = re.search(r"id=pic_json>(\{.*?\})</script>", html, re.DOTALL)
    if not m:
        return []
    try:
        pics = json.loads(m.group(1)).get("pictures", [])
    except Exception:
        return []

    scored = []
    for pic in pics:
        sizes = [s for s in pic.get("sizes", []) if s.get("url")]
        if not sizes:
            continue
        big = max(sizes, key=lambda s: (s.get("width", 0) or 0) * (s.get("height", 0) or 0))
        if b.RCDB + big["url"] in exclude:
            continue
        w, h = big.get("width") or 0, big.get("height") or 0
        if not w or not h or w < h:          # landscape only
            continue
        # smallest size at least 300px wide keeps the review fetch light
        thumbs = sorted(sizes, key=lambda s: s.get("width", 0) or 0)
        thumb = next((s for s in thumbs if (s.get("width") or 0) >= 300), big)
        scored.append((b._pic_score(w, h), thumb["url"], big["url"], w, h))
    scored.sort(reverse=True)
    return scored[:n]


def build(qkeys, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    exclude = {r[0] for r in b.db_query("SELECT src FROM rejects")}
    font, small = _font(12), _font(11)

    sheet_w = N_CAND * (CELL_W + PAD) + PAD
    row_h = ROW_LABEL_H + CELL_H + LABEL_H + PAD
    sheet_h = ROWS_PER_SHEET * row_h + PAD

    man = open(os.path.join(out_dir, "manifest.tsv"), "w")
    man.write("qkey\tcandidate\tfull_url\tdims\n")

    sheet = draw = None
    n_sheets = 0
    for i, qkey in enumerate(qkeys):
        row = i % ROWS_PER_SHEET
        if row == 0:
            if sheet:
                sheet.save(os.path.join(out_dir, f"cand-{n_sheets:02d}.jpg"), quality=86, optimize=True)
            n_sheets += 1
            sheet = Image.new("RGB", (sheet_w, sheet_h), (22, 22, 30))
            draw = ImageDraw.Draw(sheet)

        rows = b.db_query("SELECT rcdb FROM lookup WHERE qkey=?", (qkey,))
        rcdb_path = rows[0][0] if rows else None
        y0 = PAD + row * row_h
        name, _, park = qkey.partition("|")
        draw.text((PAD, y0 + 4), f"{name}  @  {park}", font=font, fill=(255, 210, 120))

        cands = candidates_for(rcdb_path, exclude) if rcdb_path else []
        for c, (score, thumb_url, full_url, w, h) in enumerate(cands):
            x = PAD + c * (CELL_W + PAD)
            y = y0 + ROW_LABEL_H
            try:
                raw = b._get(b.RCDB + thumb_url)
                with Image.open(io.BytesIO(raw)) as im:
                    sheet.paste(cover_crop(im.convert("RGB"), CELL_W, CELL_H), (x, y))
            except Exception:
                draw.rectangle([x, y, x + CELL_W, y + CELL_H], fill=(60, 30, 30))
            draw.rectangle([x, y + CELL_H, x + CELL_W, y + CELL_H + LABEL_H], fill=(38, 38, 50))
            draw.text((x + 4, y + CELL_H + 4), f"[{c + 1}]  {w}x{h}", font=small, fill=(230, 230, 245))
            man.write(f"{qkey}\t{c + 1}\t{full_url}\t{w}x{h}\n")
        if not cands:
            draw.text((PAD, y0 + ROW_LABEL_H + 8), "no landscape candidates left",
                      font=font, fill=(255, 140, 140))

    if sheet:
        sheet.save(os.path.join(out_dir, f"cand-{n_sheets:02d}.jpg"), quality=86, optimize=True)
    man.close()
    print(f"{len(qkeys)} coasters -> {n_sheets} sheets in {out_dir}")


def choose(path):
    picks = {}
    for line in open(path):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        qkey, _, idx = line.partition("\t")
        picks[qkey.strip()] = int(idx.strip())

    man = {}
    for line in open(os.path.join(OUT_DIR, "manifest.tsv")):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3 or parts[0] == "qkey":
            continue
        man[(parts[0], int(parts[1]))] = parts[2]

    ok = miss = 0
    for qkey, idx in picks.items():
        full = man.get((qkey, idx))
        if not full:
            print(f"  ? {qkey}: no candidate {idx}")
            miss += 1
            continue
        rows = b.db_query("SELECT ckey, rcdb, ctype FROM lookup WHERE qkey=?", (qkey,))
        if not rows:
            print(f"  ? {qkey}: not in cache")
            miss += 1
            continue
        old_ckey, rcdb_path, ctype = rows[0]
        if old_ckey:
            src = b.db_query("SELECT src FROM images WHERE ckey=?", (old_ckey,))
            if src:
                b.db_write("INSERT OR REPLACE INTO rejects VALUES (?,?,?)",
                           (src[0][0], "manual review", int(time.time())))
        try:
            ckey = b.cache_image(b.RCDB + full)
        except Exception as e:
            print(f"  ! {qkey}: {e}")
            miss += 1
            continue
        b.db_write("INSERT OR REPLACE INTO lookup VALUES (?,?,?,?,?)",
                   (qkey, ckey, ctype, rcdb_path, int(time.time())))
        ok += 1
        print(f"  + {qkey.split('|')[0][:30]:32} candidate {idx}")
    print(f"\n{ok} applied, {miss} skipped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qkeys", help="file of 'name|park' lines to build sheets for")
    ap.add_argument("--choose", help="file of 'name|park<TAB>candidate' lines to apply")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    if args.choose:
        choose(args.choose)
        return
    if not args.qkeys:
        ap.error("give me --qkeys or --choose")
    qkeys = [l.strip() for l in open(args.qkeys) if l.strip() and not l.startswith("#")]
    build(qkeys, args.out)


if __name__ == "__main__":
    main()
