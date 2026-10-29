#!/usr/bin/env python3
"""Impose a PDF as a saddle-stitched booklet on 11" x 17" landscape sheets.

Usage:  python booklet.py input.pdf [booklet_output.pdf]

Print double-sided, flip on the SHORT edge, then fold the stack in half.
Each 8.5" x 11" page is placed, unscaled, on one half of a 17" x 11" sheet.
"""
import io
import sys

from pypdf import PageObject, PdfReader, PdfWriter, Transformation

SHEET_W, SHEET_H = 17 * 72, 11 * 72   # 11x17 landscape, in points
CELL_W = SHEET_W / 2                  # one 8.5" x 11" slot


def imposition_order(n):
    """Return [(left, right), ...] page numbers (1-based, None = blank) per sheet side.

    Order is front of sheet 1, back of sheet 1, front of sheet 2, ... For 16 pages:
    (16,1) (2,15) | (14,3) (4,13) | (12,5) (6,11) | (10,7) (8,9).
    """
    total = -(-n // 4) * 4
    pad = lambda p: p if p <= n else None
    sides = []
    for k in range(total // 4):
        sides.append((pad(total - 2 * k), pad(2 * k + 1)))      # outside / front
        sides.append((pad(2 * k + 2), pad(total - 2 * k - 1)))  # inside / back
    return sides


def _place(sheet, page, x_off):
    """Scale the page to fit its 8.5x11 slot (never distorted) and centre it."""
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    s = min(CELL_W / w, SHEET_H / h)
    tx = x_off + (CELL_W - w * s) / 2
    ty = (SHEET_H - h * s) / 2
    sheet.merge_transformed_page(page, Transformation().scale(s, s).translate(tx, ty))


def make_booklet(src):
    """src: PDF bytes or path/file object. Returns the imposed PDF as bytes."""
    reader = PdfReader(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    out = PdfWriter()
    for left, right in imposition_order(len(reader.pages)):
        sheet = PageObject.create_blank_page(width=SHEET_W, height=SHEET_H)
        for num, x_off in ((left, 0), (right, CELL_W)):
            if num is not None:
                _place(sheet, reader.pages[num - 1], x_off)
        out.add_page(sheet)
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dest = sys.argv[2] if len(sys.argv) > 2 else "booklet_output.pdf"
    with open(sys.argv[1], "rb") as fh:
        data = make_booklet(fh.read())
    with open(dest, "wb") as fh:
        fh.write(data)
    print(f"Wrote {dest}")
