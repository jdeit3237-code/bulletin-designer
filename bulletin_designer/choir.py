"""Choir sheet-music packets built from a Liturgy.com music outline.

Songbook PDFs are licensed material. They live in a private directory that is
git-ignored and never served statically; the only public file is a finished
weekly packet reachable through an unguessable, expiring token.
"""
import io
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PIANO_EDITIONS = ("piano", "keyboard", "solo")
EDITIONS = ("piano", "keyboard", "guitar", "solo")
EXPIRY_DAYS_AFTER_SUNDAY = 7
EDITIONS = (("keyboard", "Keyboard"), ("guitar", "Guitar"), ("solo", "Solo instrument"))

SECTION_HEADERS = (
    "INTRODUCTORY RITES",
    "LITURGY OF THE WORD",
    "LITURGY OF THE EUCHARIST",
    "COMMUNION RITE",
    "CONCLUDING RITES",
)

# Longest labels first so "Gospel Acclamation" wins over "Gospel".
ROW_LABELS = sorted(
    [
        "Entrance", "Penitential Act", "Glory to God", "First Reading",
        "Responsorial Psalm", "Second Reading", "Gospel Acclamation", "Gospel",
        "Presentation and Preparation of the Gifts", "Offertory", "Holy",
        "Mystery of Faith", "Amen", "Lord's Prayer", "Lamb of God",
        "Communion", "Song of Praise", "Sign of Peace", "Final Song or Hymn",
        "Final Song", "Recessional", "Sequence",
    ],
    key=len,
    reverse=True,
)
SKIP_LABELS = {"First Reading", "Second Reading", "Gospel", "Sequence", "Sign of Peace"}

# Outline label -> keyword that identifies the Mass-setting part.
LABEL_PART = {
    "Penitential Act": "kyrie",
    "Glory to God": "glory",
    "Holy": "holy",
    "Mystery of Faith": "mystery",
    "Amen": "amen",
    "Lamb of God": "lamb",
    "Lord's Prayer": "our father",
}
PART_ALIASES = {
    "mystery": ("mystery of faith", "when we eat", "we proclaim", "dying you destroyed"),
    "kyrie": ("kyrie", "lord have mercy"),
    "glory": ("glory to god",),
    "holy": ("holy",),
    "amen": ("amen",),
    "lamb": ("lamb of god",),
    "our father": ("our father", "lords prayer"),
}
LABEL_KIND = {"Responsorial Psalm": "psalm", "Gospel Acclamation": "acclamation"}

MASS_PARTS = {"Penitential Act", "Glory to God", "Holy", "Mystery of Faith", "Amen", "Lamb of God", "Lord's Prayer"}
CODE_RE = re.compile(r"\b([A-Z]{2,4}\d{2})\s*-\s*(\d+)\b")
DATE_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})")
RA_RANGE_RE = re.compile(
    r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})\s*[–—-]\s*"
    r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})"
)
RA_DAYS_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})"
    r"(?:\s+or\s+(?:(January|February|March|April|May|June|July|August|September|October|November|December)\s+)?(\d{1,2}))?$"
)
BB_NAV_RE = re.compile(r"^\s*Home\s*\|.*$", re.M)
BB_START_RE = re.compile(r"^\s*(\d{1,3})\s+(\S.*)$")
BB_CONT_RE = re.compile(r"^\s*(\d{1,3})\s*[—–-]\s*(.+?)(?:,\s*cont\.|\s*\(\d+\))\s*$", re.I)
BB_INDEX_LINE_RE = re.compile(r"^\s*(\d{1,3})\s+([A-Za-z\"“‘'(].*?)\s*$")
BB_MI_INDEX_RE = re.compile(
    r"^\s*([A-Za-z\"“‘'(].*?)\s+(?:\.{2,}\s*)?BB\s*/\s*MI\s+(\d{1,3})\s+(\d{1,3})\s*$",
    re.I,
)
BB_INDEX_HEAD_RE = re.compile(r"(Scriptural|Topical|Liturgical|Alphabetical)\s+Index", re.I)
BB_ANY_INDEX_LINE_RE = re.compile(r"^\s*(\d{1,3})\s+(.+?)\s*$")
BB_SMALL_WORDS = {"of", "the", "a", "an", "and", "to", "in", "on", "for", "o", "is", "my", "me", "us"}
BOOK_INDEX_VERSION = 2


def _norm(text):
    text = (text or "").lower().replace("saint", "st").replace("st.", "st")
    return re.sub(r"[^a-z0-9 ]+", "", text).strip()


def _words(text):
    return re.sub(r"\s+", " ", _norm(text))


# ---------------------------------------------------------------------------
# Outline parsing
# ---------------------------------------------------------------------------

def extract_pdf_text(file_obj):
    reader = PdfReader(file_obj)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _split_label(line):
    for label in ROW_LABELS:
        if line == label or line.startswith(label + " "):
            return label, line[len(label):].strip()
    return None, line


def parse_outline(text):
    """Parse Liturgy.com outline text into header info and song rows."""
    text = text.replace("Gi!s", "Gifts").replace("Gi\ufb02s", "Gifts")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    header = {"title": "", "cycle": "", "date": None, "setting": ""}
    head_lines = []
    for ln in lines:
        if ln in SECTION_HEADERS:
            break
        head_lines.append(ln)
    for ln in head_lines:
        m = DATE_RE.search(ln)
        if m and not header["date"]:
            try:
                header["date"] = datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
                ).date()
                continue
            except ValueError:
                pass
        if re.fullmatch(r"\([A-C]\)", ln):
            header["cycle"] = ln.strip("()")
        elif not header["title"]:
            header["title"] = ln.title()
        elif not header["setting"]:
            header["setting"] = ln

    rows = []
    current = None
    started = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if ln in SECTION_HEADERS:
            started = True
            current = None
            continue
        if not started:
            continue
        # A label can wrap onto two lines ("Presentation and" / "Preparation ...").
        if ln == "Presentation and" and i < len(lines):
            ln = "Presentation and " + lines[i]
            i += 1
        label, rest = _split_label(ln)
        if label and not ln.startswith("("):
            current = {"label": label, "text": rest}
            rows.append(current)
        elif current is not None:
            current["text"] = (current["text"] + " " + ln).strip()

    songs = []
    for row in rows:
        if row["label"] in SKIP_LABELS:
            continue
        song = _parse_row(row["label"], row["text"])
        if song:
            songs.append(song)
    return {"header": header, "songs": songs}


def _parse_row(label, text):
    code = CODE_RE.search(text)
    if "(" not in text and not code:
        return None  # a scripture reference, not music
    body = CODE_RE.sub("", text).strip()
    title = body.split("(")[0].strip() or body
    groups = re.findall(r"\(([^)]*)\)", body)
    setting = next((g for g in groups if re.search(r"\bmass of\b", g, re.I)), "")
    composer = next(
        (g for g in reversed(groups) if g != setting and not g.isupper()), ""
    )
    if setting:
        setting = re.sub(r"(?i)^mass of\s+", "", setting).strip()
    full = title
    if label not in MASS_PARTS and "/" in title:
        title = title.split("/")[0].strip()  # English title only; the full title is kept for the language option
    return {
        "label": label,
        "title": title,
        "title_full": full,
        "setting": setting,
        "composer": composer,
        "book": code.group(1) if code else "",
        "number": code.group(2) if code else "",
    }


# ---------------------------------------------------------------------------
# Private library
# ---------------------------------------------------------------------------

def library_dir(app):
    path = app.config.get("CHOIR_LIBRARY_DIR") or os.environ.get("CHOIR_LIBRARY_DIR")
    if not path:
        base = os.path.dirname(os.path.realpath(app.config["UPLOAD_DIR"]))
        path = os.path.join(base, "private_music")
    path = os.path.abspath(path)
    for sub in ("", "books", "packets", "drafts"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


def _index_path(app):
    return os.path.join(library_dir(app), "library.json")


def load_library(app):
    try:
        with open(_index_path(app), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"books": []}


def save_library(app, lib):
    tmp = _index_path(app) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lib, fh, indent=2)
    os.replace(tmp, _index_path(app))


def parse_entries(text):
    """Entry lines: ``number | title | pages | kind | date | catalog numbers``.

    ``pages`` is ``12`` or ``12-14`` using the book's printed page numbers.
    Catalog numbers are optional comma-separated pairs such as ``BB=517,MI=216``.
    """
    entries, errors = [], []
    for n, raw in enumerate((text or "").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            errors.append(f"Line {n}: expected 'number | title | pages'.")
            continue
        m = re.fullmatch(r"(\d+)\s*(?:-|–)?\s*(\d+)?", parts[2])
        if not m:
            errors.append(f"Line {n}: bad page range '{parts[2]}'.")
            continue
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if end < start:
            errors.append(f"Line {n}: page range ends before it starts.")
            continue
        entry = {"number": parts[0], "title": parts[1], "start": start, "end": end}
        if len(parts) > 3 and parts[3]:
            entry["kind"] = parts[3].lower()
        if len(parts) > 4 and parts[4]:
            try:
                entry["date"] = date.fromisoformat(parts[4]).isoformat()
            except ValueError:
                errors.append(f"Line {n}: date must be YYYY-MM-DD.")
                continue
        if len(parts) > 5 and parts[5]:
            catalog_numbers = {}
            for pair in parts[5].split(","):
                match = re.fullmatch(r"\s*([A-Za-z]+)\s*=\s*(\d+)\s*", pair)
                if not match:
                    errors.append(f"Line {n}: bad catalog number '{pair.strip()}'.")
                    break
                catalog_numbers[match.group(1).upper()] = match.group(2)
            else:
                entry["catalog_numbers"] = catalog_numbers
                entries.append(entry)
                continue
            continue
        entries.append(entry)
    return entries, errors


def entries_to_text(entries):
    lines = []
    for e in entries:
        pages = str(e["start"]) if e["start"] == e["end"] else f"{e['start']}-{e['end']}"
        line = f"{e['number']} | {e['title']} | {pages}"
        if e.get("kind") or e.get("date") or e.get("catalog_numbers"):
            line += f" | {e.get('kind', '')} | {e.get('date', '')}"
        if e.get("catalog_numbers"):
            refs = ",".join(f"{key}={value}" for key, value in sorted(e["catalog_numbers"].items()))
            line += f" | {refs}"
        lines.append(line)
    return "\n".join(lines)


def generate_book_entries(path, code="", name=""):
    """Generate entries for a supported uploaded songbook PDF."""
    identity = _norm(f"{code} {name}")
    known_ra = identity.startswith("ra") or ("respond" in identity and "acclaim" in identity)
    if not known_ra:
        return _numbered_songbook_entries(path)
    reader = PdfReader(path)
    front = [page.extract_text() or "" for page in reader.pages[:3]]
    compact = re.sub(r"[^a-z]", "", "\n".join(front).lower())
    if known_ra or "respondacclaim" in compact:
        entries = _respond_acclaim_outline_entries(reader, "\n".join(front))
        if entries:
            return entries
        texts = front + [page.extract_text() or "" for page in reader.pages[3:]]
        return _respond_acclaim_entries(texts)
    return []


def _numbered_songbook_entries(path):
    """Read song numbers, titles and page ranges from an OCP accompaniment book."""
    try:
        import fitz

        doc = fitz.open(path)
        texts = [page.get_text() or "" for page in doc]
        doc.close()
    except (ImportError, RuntimeError, ValueError):
        reader = PdfReader(path)
        texts = [page.extract_text() or "" for page in reader.pages]
    return _numbered_songbook_entries_from_texts(texts)


def _numbered_songbook_entries_from_texts(texts):
    """Parse extracted page text, using the book's own indexes to disambiguate titles like "40 Days"."""

    def page_lines(page_idx):
        text = BB_NAV_RE.sub("", texts[page_idx])
        return [line for line in text.splitlines() if line.strip()]

    def smart_title(text):
        words = re.sub(r"\s+", " ", text).strip().lower().split()
        return " ".join(
            word if index and word in BB_SMALL_WORDS else word[:1].upper() + word[1:]
            for index, word in enumerate(words)
        )

    first = min(12, len(texts))
    stop = len(texts)
    for page_idx in range(first, len(texts)):
        if BB_INDEX_HEAD_RE.search(texts[page_idx][:80]):
            stop = page_idx
            break

    index_titles = {}
    index_records = {}
    aliases = {}
    first_lines = next(
        (page_idx for page_idx in range(stop, len(texts)) if "First Lines and Common Titles" in texts[page_idx]),
        None,
    )
    if first_lines is not None:
        index_pages = range(first_lines, len(texts))
    else:
        alphabetical = [
            page_idx for page_idx in range(stop, len(texts))
            if texts[page_idx].lstrip().startswith("A | B | C")
        ]
        index_pages = alphabetical or range(stop, len(texts))
    for page_idx in index_pages:
        text = texts[page_idx]
        for line in text.splitlines():
            cross_ref = _bb_mi_index_record(line)
            if cross_ref:
                number = int(cross_ref["MI"])
                title = cross_ref["title"]
                index_titles.setdefault(number, title)
                aliases.setdefault(_words(title), set()).add(number)
                aliases.setdefault(_plain(title), set()).add(number)
                index_records.setdefault(number, cross_ref)
                continue
            match = BB_INDEX_LINE_RE.match(line) or BB_ANY_INDEX_LINE_RE.match(line)
            if not match or "|" in line or ". . ." in line:
                continue
            number, title = int(match.group(1)), re.sub(r"\s+", " ", match.group(2)).strip()
            if not title or title.lower().startswith(("scriptural index", "topical index", "liturgical index")):
                continue
            index_titles.setdefault(number, title)
            aliases.setdefault(_words(title), set()).add(number)

    found = {}

    def indexed_title(number, title):
        key = _words(title)
        return any(
            number in numbers and (alias == key or alias.startswith(key + " mass of"))
            for alias, numbers in aliases.items()
        )

    for page_idx in range(first, stop):
        number = title = None
        continuation = False
        lines = page_lines(page_idx)[:12]
        for line in lines:
            match = BB_CONT_RE.match(line)
            if match:
                number, title, continuation = int(match.group(1)), match.group(2), True
                break
        if number is None:
            for line in lines:
                match = BB_START_RE.match(line)
                if not match or len(match.group(2)) <= 3 or re.match(r"^[\d.,/ ]+$", match.group(2)):
                    continue
                candidate_number, candidate_title = int(match.group(1)), match.group(2)
                if aliases and not indexed_title(candidate_number, candidate_title):
                    continue
                number, title = candidate_number, candidate_title
                break
        if number is None:
            for line in lines:
                match = re.fullmatch(r"(.+?)\s+(\d{1,3})", line)
                if not match:
                    continue
                candidate_title, candidate_number = match.group(1), int(match.group(2))
                if aliases and not indexed_title(candidate_number, candidate_title):
                    continue
                number, title = candidate_number, candidate_title
                break
        if number is None and aliases:
            for line_idx, line in enumerate(lines):
                if not re.fullmatch(r"\d{1,3}", line):
                    continue
                candidate_number = int(line)
                if candidate_number == page_idx + 1:
                    continue
                candidates = []
                if line_idx + 1 < len(lines):
                    candidates.append(lines[line_idx + 1])
                if line_idx:
                    candidates.append(lines[line_idx - 1])
                candidate_title = next(
                    (candidate for candidate in candidates if indexed_title(candidate_number, candidate)),
                    None,
                )
                if candidate_title:
                    number, title = candidate_number, candidate_title
                    break
        if number is None and aliases:
            for line in lines:
                key = _words(re.sub(r"\s*\(\d+\)\s*$", "", line))
                numbers = aliases.get(key, set())
                if len(numbers) == 1:
                    number, title = next(iter(numbers)), line
                    break
        if number is None:
            continue
        record = found.setdefault(number, [page_idx + 1, page_idx + 1, smart_title(title)])
        if continuation or page_idx + 1 >= record[0]:
            record[1] = max(record[1], page_idx + 1)

    entries = []
    for number in sorted(found):
        record = index_records.get(number, {})
        entry = {
            "number": str(number),
            "title": record.get("title", found[number][2]),
            "start": found[number][0],
            "end": found[number][1],
        }
        catalog_numbers = {key: record[key] for key in ("BB", "MI") if record.get(key)}
        if catalog_numbers:
            entry["catalog_numbers"] = catalog_numbers
        entries.append(entry)
    for entry in entries:
        match = re.match(r"^([B-HJ-NP-Z]) ([A-Za-z]{2,})(.*)$", entry["title"])
        if match and match.group(1) != "A":
            entry["title"] = match.group(1) + match.group(2).lower() + match.group(3)
        entry["title"] = entry["title"].replace("’S", "’s")
    ordered = sorted(entries, key=lambda entry: entry["start"])
    for current, following in zip(ordered, ordered[1:]):
        if following["start"] > current["start"]:
            current["end"] = min(current["end"], following["start"] - 1)
    for entry in entries:
        entry["end"] = max(entry["start"], min(entry["end"], entry["start"] + 8))
    return entries


def _bb_mi_index_record(line):
    """Return a title plus BB/MI catalog numbers from an alphabetical-index row."""
    match = BB_MI_INDEX_RE.match(line)
    if match:
        return {"title": match.group(1).rstrip(" ."), "BB": match.group(2), "MI": match.group(3)}
    return None


def upgrade_book_entries(book, generated):
    """Merge newly discovered catalog aliases into an existing book without replacing edits."""
    changed = False
    generated_by_number = {entry.get("number"): entry for entry in generated}
    for entry in book.get("entries", []):
        new_entry = generated_by_number.get(entry.get("number"))
        if not new_entry or not new_entry.get("catalog_numbers"):
            continue
        if entry.get("catalog_numbers") != new_entry["catalog_numbers"]:
            entry["catalog_numbers"] = new_entry["catalog_numbers"]
            changed = True
    if book.get("index_version") != BOOK_INDEX_VERSION:
        book["index_version"] = BOOK_INDEX_VERSION
        changed = True
    return changed


def _ra_years(cover):
    span = RA_RANGE_RE.search(cover)
    edition = re.search(r"Respond\s*&?\s*Acclaim\s*(20\d{2})", cover, re.I)
    if span:
        return datetime.strptime(span.group(1), "%B").month, int(span.group(3)), int(span.group(6))
    if edition:
        year = int(edition.group(1))
        return 11, year - 1, year
    return None


def _ra_days(text):
    match = RA_DAYS_RE.fullmatch(text)
    if not match:
        return []
    days = [(match.group(1), match.group(2))]
    if match.group(4):
        days.append((match.group(3) or match.group(1), match.group(4)))
    return days


def _ra_pair(title, event_date, psalm_page, acclamation_page):
    return [
        {"number": "", "title": f"{title} – Responsorial Psalm", "start": psalm_page,
         "end": psalm_page, "kind": "psalm", "date": event_date},
        {"number": "", "title": f"{title} – Gospel Acclamation", "start": acclamation_page,
         "end": acclamation_page, "kind": "acclamation", "date": event_date},
    ]


def _respond_acclaim_outline_entries(reader, cover):
    """Use the book's navigation outline, which lists every celebration and page."""
    years = _ra_years(cover)
    if not years:
        return []

    def section(items):
        previous_title = ""
        for item in items:
            if isinstance(item, list):
                if previous_title == "Psalms & Acclamations":
                    return item
                found = section(item)
                if found:
                    return found
            else:
                previous_title = getattr(item, "title", "")
        return []

    entries = []
    current_year = years[1]
    previous_month = years[0]
    outline = getattr(reader, "outline", None) or getattr(reader, "outlines", [])
    for destination in section(outline):
        parts = [line.strip() for line in getattr(destination, "title", "").splitlines() if line.strip()]
        if len(parts) < 2:
            continue
        days = _ra_days(parts[-1])
        if not days:
            continue
        page = reader.get_destination_page_number(destination) + 1
        month = datetime.strptime(days[0][0], "%B").month
        if month < previous_month:
            current_year = min(current_year + 1, years[2])
        previous_month = month
        for month_name, day in days:
            event_month = datetime.strptime(month_name, "%B").month
            entries.extend(_ra_pair(
                " ".join(parts[:-1]),
                date(current_year, event_month, int(day)).isoformat(),
                page,
                page + 1,
            ))
    return entries


def _respond_acclaim_entries(texts):
    """Read dated psalm/acclamation pairs from Respond & Acclaim pages."""
    cover = "\n".join(texts[:3])
    years = _ra_years(cover)
    if not years:
        return []

    def printed_page(text, fallback):
        for line in text.splitlines()[:5]:
            line = line.strip()
            if re.fullmatch(r"\d{1,3}", line):
                return int(line)
        return fallback

    celebrations = []
    current_year = years[1]
    previous_month = years[0]
    for page_idx, text in enumerate(texts):
        if "Responsorial Psalm:" not in text:
            continue
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        psalm_idx = next((i for i, line in enumerate(lines) if "Responsorial Psalm:" in line), len(lines))
        date_hit = next(
            ((i, _ra_days(line)) for i, line in enumerate(lines[:psalm_idx]) if _ra_days(line)),
            None,
        )
        if not date_hit:
            continue
        date_idx, days = date_hit
        title_lines = []
        for line in lines[:date_idx]:
            line = re.sub(r"^.*List of Page Numbers", "", line, flags=re.I).strip()
            line = re.sub(r"^Psalms\s*&\s*$", "", line, flags=re.I).strip()
            line = re.sub(r"^Acclamations\s+", "", line, flags=re.I).strip()
            if not line or re.fullmatch(r"\d{1,3}", line) or line.lower() == "home":
                continue
            title_lines.append(line)
        title = " ".join(title_lines).strip() or "Respond & Acclaim"
        month = datetime.strptime(days[0][0], "%B").month
        if month < previous_month:
            current_year = min(current_year + 1, years[2])
        previous_month = month
        celebrations.append({
            "page_idx": page_idx,
            "page": printed_page(text, page_idx + 1),
            "title": title,
            "dates": [
                date(current_year, datetime.strptime(month_name, "%B").month, int(day)).isoformat()
                for month_name, day in days
            ],
        })

    entries = []
    for celebration in celebrations:
        acclamation_page = None
        for page_idx in range(celebration["page_idx"] + 1, len(texts)):
            text = texts[page_idx]
            if "Responsorial Psalm:" in text:
                break
            if "Gospel Acclamation:" in text:
                acclamation_page = printed_page(text, page_idx + 1)
                break
        for event_date in celebration["dates"]:
            if acclamation_page is not None:
                entries.extend(_ra_pair(celebration["title"], event_date, celebration["page"], acclamation_page))
            else:
                entries.append({
                    "number": "",
                    "title": f"{celebration['title']} – Responsorial Psalm",
                    "start": celebration["page"],
                    "end": celebration["page"],
                    "kind": "psalm",
                    "date": event_date,
                })
    return entries


def book_path(app, book):
    name = os.path.basename(book["file"])
    return os.path.join(library_dir(app), "books", name)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _entry_hits(lib, song, outline_date, part_idx=0):
    """Return [(book, entry)] across every edition that matches the song."""
    hits = []
    iso = outline_date.isoformat() if outline_date else None
    kind = LABEL_KIND.get(song["label"])
    part = LABEL_PART.get(song["label"])
    want_title = _plain(song["title"], part_idx)
    want_setting = _words(song["setting"])
    requested_code = _norm(song.get("book"))
    requested_code_available = bool(requested_code) and any(
        _norm(book.get("code")) == requested_code and book.get("entries") for book in lib.get("books", [])
    )
    for book in lib.get("books", []):
        code = book.get("code", "")
        for e in book.get("entries", []):
            if song["book"] and song["number"]:
                # The outline's hymn number does not always match the book's own numbering
                # (BB26-718 is a psalm in the 2026 book), so a number match must agree on title.
                same_code = _norm(code) == requested_code
                if same_code and _entry_matches_number(e, song["number"], requested_code) \
                        and _title_match(e["title"], want_title):
                    hits.append((book, e))
                elif (same_code or not requested_code_available) and not kind and not part \
                        and _title_match(e["title"], want_title) and e["number"]:
                    hits.append((book, e))
                continue
            if iso and e.get("date") == iso and kind and e.get("kind") == kind:
                hits.append((book, e))
                continue
            if want_setting and _words(book.get("name")) == _norm("mass of " + song["setting"]):
                if _part_match(e["title"], want_title, part):
                    hits.append((book, e))
                continue
            if not want_setting and not kind and not e.get("date"):
                if _title_match(e["title"], want_title):
                    hits.append((book, e))
    # Prefer exact title matches ("I Am the Bread of Life" must not pick "Bread of Life").
    exact = [h for h in hits if want_title in _halves(h[1]["title"])]
    if part_idx:
        return exact  # other languages: exact titles only, never a look-alike song
    return exact or hits


def _entry_matches_number(entry, number, requested_code=""):
    if entry.get("number") == number:
        return True
    catalog_numbers = entry.get("catalog_numbers", {})
    prefix = re.match(r"[a-z]+", requested_code or "")
    if prefix and catalog_numbers.get(prefix.group(0).upper()) == number:
        return True
    return number in catalog_numbers.values()


def _plain(text, part=0):
    halves = (text or "").split("/")
    half = halves[part] if part < len(halves) and halves[part].strip() else halves[0]
    return _words(re.sub(r"\([^)]*\)", " ", half))


def _halves(text):
    return [h for h in (_plain(text, i) for i in range(len((text or "").split("/")))) if h]


def _title_match(entry_title, want_title):
    """Match against any language half of a bilingual entry title."""
    want_title = _plain(want_title)
    return bool(want_title) and any(
        et == want_title or et in want_title or want_title in et for et in _halves(entry_title)
    )


def _part_match(entry_title, want_title, part):
    et = _words(entry_title)
    if _title_match(entry_title, want_title):
        return True
    if part:
        aliases = PART_ALIASES.get(part, (part,))
        if part == "holy":
            return et.startswith("holy")
        return any(a in et for a in aliases)
    return False


def match_songs(lib, songs, outline_date, language="english"):
    """Attach primary (piano) and guitar page sources to each song.

    With language="other", a bilingual outline title ("English/Other") is matched on its
    second half, so only versions already printed in the uploaded books are used; nothing
    is translated. Songs without such a version fall back to English and are flagged.
    """
    out = []
    for song in songs:
        has_other = "/" in (song.get("title") or "")
        hits = []
        used_other = False
        if language != "english" and has_other:
            hits = _entry_hits(lib, song, outline_date, 1)
            used_other = bool(hits)
        if not hits:
            hits = _entry_hits(lib, song, outline_date)
        primary = next(
            (h for ed in PIANO_EDITIONS for h in hits if h[0].get("edition") == ed), None
        )
        guitar = next((h for h in hits if h[0].get("edition") == "guitar"), None)
        solo = next((h for h in hits if h[0].get("edition") == "solo"), None)
        if primary is None and guitar is not None:
            primary, guitar = guitar, None  # guitar-only book: still better than nothing
        item = dict(song)
        item["language"] = "other" if used_other else "english"
        item["english_only"] = bool(language != "english" and has_other and not used_other)
        item["primary"] = _source(primary)
        item["guitar"] = _source(guitar)
        item["solo"] = _source(solo)
        item["include"] = bool(primary)
        out.append(item)
    return out


def book_page_images_for_songs(app, songs, outline_date):
    """PNG bytes for each song, opening each referenced book only once."""
    import fitz

    lib = load_library(app)
    results = [[] for _ in songs]
    grouped = {}
    for result_idx, matched in enumerate(match_songs(lib, songs, outline_date)):
        src = matched.get("primary")
        book = _book_by_id(lib, src["book_id"]) if src else None
        path = book_path(app, book) if book else ""
        if path and os.path.exists(path):
            grouped.setdefault(path, []).append((result_idx, src, int(book.get("page_offset", 0))))
    for path, requests in grouped.items():
        with fitz.open(path) as doc:
            rendered = {}
            for result_idx, src, offset in requests:
                for page in range(src["start"], src["end"] + 1):
                    doc_idx = page + offset - 1
                    if 0 <= doc_idx < len(doc):
                        if doc_idx not in rendered:
                            rendered[doc_idx] = doc[doc_idx].get_pixmap(dpi=150).tobytes("png")
                        results[result_idx].append(rendered[doc_idx])
    return results


def book_page_images(app, song, outline_date):
    """PNG bytes of a song's pages in its primary book."""
    return book_page_images_for_songs(app, [song], outline_date)[0]


def _source(hit):
    if not hit:
        return None
    book, e = hit
    return {
        "book_id": book["id"],
        "book_name": book["name"],
        "edition": book.get("edition", ""),
        "start": e["start"],
        "end": e["end"],
        "entry_title": e["title"],
        "entry_number": e.get("number", ""),
    }


# ---------------------------------------------------------------------------
# Packet building
# ---------------------------------------------------------------------------

def _book_by_id(lib, book_id):
    return next((b for b in lib.get("books", []) if b["id"] == book_id), None)


def _edition_source(song, edition):
    """Pages for one instrument; psalms and acclamations come from Respond & Acclaim for everyone."""
    primary = song.get("primary")
    if edition == "keyboard":
        src = primary if primary and primary.get("edition") in ("piano", "keyboard") else None
    else:
        src = song.get(edition)
    if not src and song.get("kind") in ("psalm", "acclamation"):
        src = primary
    return src


def _cover(header, contents, edition_label=""):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, h - 90, "St. Peter Choir – Sheet Music")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w / 2, h - 125, header.get("title") or "")
    c.setFont("Helvetica", 13)
    sub = " · ".join(
        x for x in (
            f"Year {header['cycle']}" if header.get("cycle") else "",
            header["date"].strftime("%B %-d, %Y") if header.get("date") else "",
        ) if x
    )
    c.drawCentredString(w / 2, h - 148, sub)
    if edition_label:
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(w / 2, h - 172, edition_label)
        c.setFont("Helvetica", 13)
    y = h - 200
    c.setFont("Helvetica", 12)
    for label, title, page in contents:
        if y < 90:
            c.showPage()
            c.setFont("Helvetica", 12)
            y = h - 80
        c.drawString(70, y, f"{label}: {title}"[:80])
        c.drawRightString(w - 70, y, f"p. {page}")
        y -= 20
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(
        w / 2, 40, "For St. Peter choir use only. Do not post or share publicly."
    )
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def build_packet(app, header, songs, include_guitar, edition=None, outline_pdf=None):
    """Return (pdf_bytes, missing) for the songs flagged ``include``.

    With ``edition`` ("keyboard", "guitar" or "solo") only that instrument's pages are used and
    pdf_bytes is None when the week has no pages for it.
    """
    lib = load_library(app)
    writer = PdfWriter()
    contents, missing, readers = [], [], {}
    cover_pages = 1
    body = []
    outline_pages = []
    if outline_pdf:
        try:
            outline_pages = list(PdfReader(io.BytesIO(outline_pdf)).pages)
        except Exception:
            outline_pages = []

    def add_source(src, label, title, tag=""):
        book = _book_by_id(lib, src["book_id"])
        if not book:
            missing.append(f"{label}: {title}")
            return
        path = book_path(app, book)
        if not os.path.exists(path):
            missing.append(f"{label}: {title} (book file missing)")
            return
        reader = readers.setdefault(path, PdfReader(path))
        offset = int(book.get("page_offset", 0))
        first = len(body)
        for p in range(src["start"], src["end"] + 1):
            idx = p + offset - 1
            if 0 <= idx < len(reader.pages):
                body.append(reader.pages[idx])
        if len(body) > first:
            contents.append((label, title + tag, first + 1))

    for s in songs:
        if not s.get("include"):
            continue
        if not s.get("primary"):
            missing.append(f"{s['label']}: {s['title']}")
            continue
        if edition:
            src = _edition_source(s, edition)
            if src and s.get(f"{edition}_on", True):
                add_source(src, s["label"], s["title"])
            elif not src:
                missing.append(f"{s['label']}: {s['title']}")
            continue
        add_source(s["primary"], s["label"], s["title"])
        if include_guitar and s.get("guitar") and s.get("guitar_on", True):
            add_source(s["guitar"], s["label"], s["title"], " (guitar)")

    if edition and not body:
        return None, missing
    edition_label = dict(EDITIONS).get(edition, "")

    # Cover lists page numbers relative to the full packet, so shift by cover size.
    while True:
        shifted = [(a, b, c + cover_pages + len(outline_pages)) for a, b, c in contents]
        cover = _cover(header, shifted, edition_label)
        if len(cover.pages) == cover_pages:
            break
        cover_pages = len(cover.pages)
    for page in outline_pages:
        writer.add_page(page)
    for page in cover.pages:
        writer.add_page(page)
    for page in body:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), missing


# ---------------------------------------------------------------------------
# Drafts and published packets (JSON/PDF files in the private directory)
# ---------------------------------------------------------------------------

def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o))


def draft_outline_path(app, draft_id):
    return os.path.join(library_dir(app), "drafts", draft_id + ".pdf")


def save_draft(app, header, songs, changes=None, outline_pdf=None):
    draft_id = uuid.uuid4().hex[:12]
    if outline_pdf:
        with open(draft_outline_path(app, draft_id), "wb") as fh:
            fh.write(outline_pdf)
    path = os.path.join(library_dir(app), "drafts", draft_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"header": header, "songs": songs, "changes": changes}, fh, default=_json_default)
    return draft_id


def load_draft(app, draft_id):
    if not re.fullmatch(r"[0-9a-f]{12}", draft_id or ""):
        return None
    try:
        with open(
            os.path.join(library_dir(app), "drafts", draft_id + ".json"), encoding="utf-8"
        ) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if data["header"].get("date"):
        data["header"]["date"] = date.fromisoformat(data["header"]["date"])
    return data


def _packets_dir(app):
    return os.path.join(library_dir(app), "packets")


def list_packets(app):
    packets = []
    for name in os.listdir(_packets_dir(app)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(_packets_dir(app), name), encoding="utf-8") as fh:
                    packets.append(json.load(fh))
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(packets, key=lambda p: p.get("sunday") or "", reverse=True)


def _song_key(song):
    return (song.get("label", ""), song.get("title", "").lower(), song.get("setting", "").lower())


def previous_packet(app, outline_date):
    if not outline_date:
        return None
    iso = outline_date.isoformat()
    return next((p for p in list_packets(app) if p.get("sunday") == iso and p.get("songs")
                 and p.get("edition", "keyboard") == "keyboard"), None)


def diff_songs(old_songs, new_songs):
    """Return (added, removed) song rows between two outline versions."""
    old = {_song_key(s): s for s in old_songs}
    new = {_song_key(s): s for s in new_songs}
    added = [s for k, s in new.items() if k not in old]
    removed = [s for k, s in old.items() if k not in new]
    return added, removed


def publish_packet(app, header, pdf_bytes, songs=None, edition=""):
    """Store the packet; regenerating the same Sunday (and edition) keeps the same token."""
    sunday = header["date"].isoformat() if header.get("date") else None
    token = None
    if sunday:
        token = next((p["token"] for p in list_packets(app) if p.get("sunday") == sunday and p.get("edition", "") == edition), None)
    token = token or secrets.token_urlsafe(16)
    base = header["date"] if header.get("date") else date.today()
    meta = {
        "token": token,
        "sunday": sunday,
        "title": header.get("title", ""),
        "edition": edition,
        "created": datetime.utcnow().isoformat(timespec="seconds"),
        "expires": (base + timedelta(days=EXPIRY_DAYS_AFTER_SUNDAY)).isoformat(),
        "revoked": False,
        "songs": [
            {k: x.get(k, "") for k in ("label", "title", "setting", "composer")}
            for x in (songs or [])
            if x.get("include")
        ],
    }
    with open(os.path.join(_packets_dir(app), token + ".pdf"), "wb") as fh:
        fh.write(pdf_bytes)
    with open(os.path.join(_packets_dir(app), token + ".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return meta


def get_packet(app, token):
    """Return (meta, pdf_path) for a live token, else None."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return None
    try:
        with open(os.path.join(_packets_dir(app), token + ".json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    pdf = os.path.join(_packets_dir(app), token + ".pdf")
    if meta.get("revoked") or not os.path.exists(pdf):
        return None
    if date.fromisoformat(meta["expires"]) < date.today():
        return None
    return meta, pdf


def revoke_packet(app, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return False
    path = os.path.join(_packets_dir(app), token + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    meta["revoked"] = True
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return True


# ---------------------------------------------------------------------------
# Find Music: look up one song and download only its pages
# ---------------------------------------------------------------------------

def search_library(app, query, book_id=""):
    lib = load_library(app)
    q = _words(query)
    results = []
    for book in lib.get("books", []):
        if book_id and book["id"] != book_id:
            continue
        for idx, e in enumerate(book.get("entries", [])):
            catalog_numbers = " ".join(
                f"{key} {value} {key}-{value}" for key, value in e.get("catalog_numbers", {}).items()
            )
            bb_number = e.get("catalog_numbers", {}).get("BB")
            if bb_number and book.get("code"):
                catalog_numbers += f" {book['code']}-{bb_number}"
            hay = _words(e["title"] + " " + e["number"] + " " + catalog_numbers + " " + book.get("code", ""))
            if not q or all(w in hay.split() or w in hay for w in q.split()):
                results.append(
                    {
                        "book_id": book["id"],
                        "idx": idx,
                        "book_name": book["name"],
                        "edition": book.get("edition", ""),
                        "title": e["title"],
                        "number": e["number"],
                        "catalog_numbers": e.get("catalog_numbers", {}),
                        "start": e["start"],
                        "end": e["end"],
                    }
                )
    results.sort(key=lambda r: (_words(r["title"]), r["edition"]))
    return results


def extract_song(app, book_id, idx):
    """Return (pdf_bytes, filename) for one library entry, or None."""
    lib = load_library(app)
    book = _book_by_id(lib, book_id)
    if not book or not 0 <= idx < len(book.get("entries", [])):
        return None
    e = book["entries"][idx]
    path = book_path(app, book)
    if not os.path.exists(path):
        return None
    reader = PdfReader(path)
    writer = PdfWriter()
    offset = int(book.get("page_offset", 0))
    for p in range(e["start"], e["end"] + 1):
        i = p + offset - 1
        if 0 <= i < len(reader.pages):
            writer.add_page(reader.pages[i])
    if not len(writer.pages):
        return None
    out = io.BytesIO()
    writer.write(out)
    safe = re.sub(r"[^A-Za-z0-9]+", "-", e["title"]).strip("-") or "song"
    return out.getvalue(), f"{safe}-{book.get('edition', '')}.pdf"
