"""Register the licensed 2026 OCP songbooks in the private choir library.

Usage (from the repo root):
    .venv/bin/python scripts/build_music_library.py /path/to/books_dir

The PDFs are copied into private_music/books (git-ignored) and an entry index
is written to private_music/library.json. Entries use the PDF's own page
numbers (page_offset 0). Re-running replaces earlier 2026 registrations.
"""
import glob
import json
import os
import re
import shutil
import sys
import uuid
from datetime import date

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "private_music")

# (celebration, PDF page of the psalm; the acclamation is the next page, date)
RA = [
    ("1st Sunday of Advent", 4, date(2025, 11, 30)),
    ("2nd Sunday of Advent", 6, date(2025, 12, 7)),
    ("Immaculate Conception", 8, date(2025, 12, 8)),
    ("Our Lady of Guadalupe", 10, date(2025, 12, 12)),
    ("3rd Sunday of Advent", 12, date(2025, 12, 14)),
    ("4th Sunday of Advent", 14, date(2025, 12, 21)),
    ("Christmas Vigil", 16, date(2025, 12, 24)),
    ("Christmas Day", 22, date(2025, 12, 25)),
    ("Holy Family", 24, date(2025, 12, 28)),
    ("Mary, Mother of God", 26, date(2026, 1, 1)),
    ("Epiphany", 28, date(2026, 1, 4)),
    ("Baptism of the Lord", 30, date(2026, 1, 11)),
    ("2nd Sunday in Ordinary Time", 32, date(2026, 1, 18)),
    ("3rd Sunday in Ordinary Time", 34, date(2026, 1, 25)),
    ("4th Sunday in Ordinary Time", 36, date(2026, 2, 1)),
    ("5th Sunday in Ordinary Time", 38, date(2026, 2, 8)),
    ("6th Sunday in Ordinary Time", 40, date(2026, 2, 15)),
    ("Ash Wednesday", 42, date(2026, 2, 18)),
    ("1st Sunday of Lent", 44, date(2026, 2, 22)),
    ("2nd Sunday of Lent", 46, date(2026, 3, 1)),
    ("3rd Sunday of Lent", 48, date(2026, 3, 8)),
    ("4th Sunday of Lent", 50, date(2026, 3, 15)),
    ("5th Sunday of Lent", 52, date(2026, 3, 22)),
    ("Palm Sunday", 54, date(2026, 3, 29)),
    ("Holy Thursday", 56, date(2026, 4, 2)),
    ("Good Friday", 58, date(2026, 4, 3)),
    ("Easter Sunday", 72, date(2026, 4, 5)),
    ("2nd Sunday of Easter", 74, date(2026, 4, 12)),
    ("3rd Sunday of Easter", 76, date(2026, 4, 19)),
    ("4th Sunday of Easter", 78, date(2026, 4, 26)),
    ("5th Sunday of Easter", 80, date(2026, 5, 3)),
    ("6th Sunday of Easter", 82, date(2026, 5, 10)),
    ("Ascension", 84, date(2026, 5, 14)),
    ("7th Sunday of Easter", 86, date(2026, 5, 17)),
    ("Pentecost", 94, date(2026, 5, 24)),
    ("Most Holy Trinity", 96, date(2026, 5, 31)),
    ("Corpus Christi", 98, date(2026, 6, 7)),
    ("11th Sunday in Ordinary Time", 100, date(2026, 6, 14)),
    ("12th Sunday in Ordinary Time", 102, date(2026, 6, 21)),
    ("13th Sunday in Ordinary Time", 104, date(2026, 6, 28)),
    ("14th Sunday in Ordinary Time", 106, date(2026, 7, 5)),
    ("15th Sunday in Ordinary Time", 108, date(2026, 7, 12)),
    ("16th Sunday in Ordinary Time", 110, date(2026, 7, 19)),
    ("17th Sunday in Ordinary Time", 112, date(2026, 7, 26)),
    ("18th Sunday in Ordinary Time", 114, date(2026, 8, 2)),
    ("19th Sunday in Ordinary Time", 116, date(2026, 8, 9)),
    ("Assumption Vigil", 118, date(2026, 8, 14)),
    ("Assumption", 120, date(2026, 8, 15)),
    ("20th Sunday in Ordinary Time", 122, date(2026, 8, 16)),
    ("21st Sunday in Ordinary Time", 124, date(2026, 8, 23)),
    ("22nd Sunday in Ordinary Time", 126, date(2026, 8, 30)),
    ("23rd Sunday in Ordinary Time", 128, date(2026, 9, 6)),
    ("24th Sunday in Ordinary Time", 130, date(2026, 9, 13)),
    ("25th Sunday in Ordinary Time", 132, date(2026, 9, 20)),
    ("26th Sunday in Ordinary Time", 134, date(2026, 9, 27)),
    ("27th Sunday in Ordinary Time", 136, date(2026, 10, 4)),
    ("28th Sunday in Ordinary Time", 138, date(2026, 10, 11)),
    ("29th Sunday in Ordinary Time", 140, date(2026, 10, 18)),
    ("30th Sunday in Ordinary Time", 142, date(2026, 10, 25)),
    ("All Saints", 144, date(2026, 11, 1)),
    ("32nd Sunday in Ordinary Time", 146, date(2026, 11, 8)),
    ("33rd Sunday in Ordinary Time", 148, date(2026, 11, 15)),
    ("Christ the King", 150, date(2026, 11, 22)),
    ("Thanksgiving", 152, date(2026, 11, 26)),
]

NAV = re.compile(r"^\s*Home\s*\|.*$", re.M)
START = re.compile(r"^\s*(\d{1,3})\s+(\S.*)$")
CONT = re.compile(r"^\s*(\d{1,3})\s*[—–-]\s*(.+?),\s*cont\.")
INDEX_LINE = re.compile(r"^\s*(\d{1,3})\s+([A-Za-z\"“‘'(].*?)\s*$")
INDEX_HEAD = re.compile(r"(Scriptural|Topical|Liturgical|Alphabetical)\s+Index", re.I)
SMALL = {"of", "the", "a", "an", "and", "to", "in", "on", "for", "o", "is", "my", "me", "us"}


def page_lines(reader, i):
    text = NAV.sub("", reader.pages[i].extract_text() or "")
    return [ln for ln in text.splitlines() if ln.strip()]


def smart_title(s):
    words = re.sub(r"\s+", " ", s).strip().lower().split()
    return " ".join(w if (i and w in SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def first_index_page(reader, first):
    for i in range(first, len(reader.pages)):
        if INDEX_HEAD.search((reader.pages[i].extract_text() or "")[:60]):
            return i
    return len(reader.pages)


def index_titles(reader, first):
    """Clean titles from the alphabetical index (lines like '241 Hail, Holy Queen')."""
    titles = {}
    for i in range(first, len(reader.pages)):
        text = reader.pages[i].extract_text() or ""
        if not text.lstrip().startswith("A | B | C"):
            continue
        for ln in text.splitlines():
            m = INDEX_LINE.match(ln)
            if m and "|" not in ln and ". . ." not in ln:
                titles.setdefault(int(m.group(1)), m.group(2))
    return titles


def build_song_entries(path):
    reader = PdfReader(path)
    first = 12
    stop = first_index_page(reader, first)
    found = {}  # number -> [first page, last page, title]
    for i in range(first, stop):
        lines = page_lines(reader, i)[:4]
        num = title = None
        for ln in lines:
            m = CONT.match(ln)
            if m:
                num, title = int(m.group(1)), m.group(2)
                break
        if num is None:
            for ln in lines:
                m = START.match(ln)
                if m and len(m.group(2)) > 3 and not re.match(r"^[\d.,/ ]+$", m.group(2)):
                    num, title = int(m.group(1)), m.group(2)
                    break
        if num is None:
            continue
        rec = found.setdefault(num, [i + 1, i + 1, smart_title(title)])
        rec[1] = max(rec[1], i + 1)
    titles = index_titles(reader, stop)
    entries = clean_entries([
        {"number": str(n), "title": titles.get(n, found[n][2]), "start": found[n][0],
         "end": found[n][1]}
        for n in sorted(found)
    ])
    return entries, len(reader.pages), stop


def clean_entries(entries):
    """Repair small-caps drop-cap titles ('P Salm') and runaway page ranges."""
    for e in entries:
        m = re.match(r"^([B-HJ-NP-Z]) ([A-Za-z]{2,})(.*)$", e["title"])
        if m and m.group(1) != "A":
            e["title"] = m.group(1) + m.group(2).lower() + m.group(3)
        e["title"] = e["title"].replace("’S", "’s")
    ordered = sorted(entries, key=lambda e: e["start"])
    for cur, nxt in zip(ordered, ordered[1:]):
        if nxt["start"] > cur["start"]:
            cur["end"] = min(cur["end"], nxt["start"] - 1)
    for e in entries:
        e["end"] = max(e["start"], min(e["end"], e["start"] + 8))
    return entries


def ra_entries():
    out = []
    for label, page, d in RA:
        for off, kind, what in ((0, "psalm", "Responsorial Psalm"), (1, "acclamation", "Gospel Acclamation")):
            out.append({"number": "", "title": f"{label} – {what}", "start": page + off,
                        "end": page + off, "kind": kind, "date": d.isoformat()})
    return out


def main(src):
    os.makedirs(os.path.join(LIB_DIR, "books"), exist_ok=True)
    lib_path = os.path.join(LIB_DIR, "library.json")
    try:
        with open(lib_path, encoding="utf-8") as fh:
            lib = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        lib = {"books": []}
    lib["books"] = [b for b in lib["books"] if b.get("code") not in ("BB26", "RA26")]

    specs = [
        ("*Respond_And_Acclaim*[!0-9].pdf", "Respond & Acclaim 2026", "RA26", "keyboard"),
        ("*Keyboard*", "Breaking Bread 2026 Keyboard", "BB26", "keyboard"),
        ("*Guitar*", "Breaking Bread 2026 Guitar", "BB26", "guitar"),
        ("*Solo_Instrument*", "Breaking Bread 2026 Solo Instrument", "BB26", "solo"),
    ]
    for pattern, name, code, edition in specs:
        files = glob.glob(os.path.join(src, pattern))
        if not files:
            print("MISSING", pattern)
            continue
        path = files[0]
        fname = os.path.basename(path)
        shutil.copy2(path, os.path.join(LIB_DIR, "books", fname))
        if code == "RA26":
            entries = ra_entries()
            print(name, len(entries), "entries")
        else:
            entries, n, stop = build_song_entries(path)
            print(name, f"{len(entries)} songs; {n} pdf pages, songs end at page {stop}")
        lib["books"].append({"id": uuid.uuid4().hex[:8], "name": name, "code": code,
                             "edition": edition, "file": fname, "page_offset": 0,
                             "entries": entries})
    with open(lib_path, "w", encoding="utf-8") as fh:
        json.dump(lib, fh, indent=2)


if __name__ == "__main__":
    main(sys.argv[1])
