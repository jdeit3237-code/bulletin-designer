"""Read a Liturgy.com weekly music outline and map its songs to slide sections."""
import re
from datetime import datetime

SECTION_HEADERS = ("INTRODUCTORY RITES", "LITURGY OF THE WORD", "LITURGY OF THE EUCHARIST", "COMMUNION RITE", "CONCLUDING RITES")
ROW_LABELS = sorted(
    ["Prelude", "Entrance", "Penitential Act", "Glory to God", "First Reading", "Responsorial Psalm", "Second Reading",
     "Gospel Acclamation", "Gospel", "Presentation and Preparation of the Gifts", "Presentation of the Gifts", "Offertory",
     "Holy", "Mystery of Faith", "Amen", "Lord's Prayer", "Lamb of God", "Communion", "Song of Praise", "Sign of Peace",
     "Final Song or Hymn", "Final Song", "Recessional", "Sequence"],
    key=len, reverse=True)
SKIP_LABELS = {"First Reading", "Second Reading", "Gospel", "Sequence", "Sign of Peace"}
LABEL_KIND = {
    "Prelude": "prelude", "Entrance": "entrance", "Presentation and Preparation of the Gifts": "gifts",
    "Presentation of the Gifts": "gifts", "Offertory": "gifts", "Communion": "communion", "Song of Praise": "praise",
    "Final Song or Hymn": "going_forth", "Final Song": "going_forth", "Recessional": "going_forth",
    "Responsorial Psalm": "songs", "Gospel Acclamation": "songs",
}
MASS_PARTS = {"Penitential Act", "Glory to God", "Holy", "Mystery of Faith", "Amen", "Lamb of God", "Lord's Prayer"}
CODE_RE = re.compile(r"\b([A-Z]{2,4}\d{2})\s*-\s*(\d+)\b")
DATE_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})")


def extract_pdf_text(file_obj):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(file_obj).pages)


REFRAIN_RE = re.compile(r"^\s*(refrain|chorus|response|antiphon|R\s*[./:])", re.I)


def repeat_refrain(parts):
    """Slide parts with the refrain/chorus repeated after every verse that follows it."""
    refrain = next((p for p in parts if REFRAIN_RE.match(p)), None)
    if not refrain:
        return parts
    out, seen = [], False
    for i, part in enumerate(parts):
        out.append(part)
        if REFRAIN_RE.match(part):
            seen = True
        elif seen and not (i + 1 < len(parts) and REFRAIN_RE.match(parts[i + 1])):
            out.append(refrain)
    return out


ANNOUNCE_RE = re.compile(r"^[\s*_]*(lector\s*:?\s*)?(?=our parish announcements)", re.I)


def announcements_from(parts):
    """Keep a Word script from 'Our Parish Announcements this week' onward (dropping the lector cue)."""
    for i, part in enumerate(parts):
        if ANNOUNCE_RE.match(part):
            return [ANNOUNCE_RE.sub("", part, count=1)] + parts[i + 1:]
    return parts


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower().replace("&", "and")).strip("-")[:80]


def _split_label(line):
    for label in ROW_LABELS:
        if line == label or line.startswith(label + " "):
            return label, line[len(label):].strip()
    return None, line


def _parse_row(label, text):
    code = CODE_RE.search(text)
    if "(" not in text and not code:
        return None
    body = CODE_RE.sub("", text).strip()
    title = body.split("(")[0].strip() or body
    groups = re.findall(r"\(([^)]*)\)", body)
    setting = next((g for g in groups if re.search(r"\bmass of\b", g, re.I)), "")
    composer = next((g for g in reversed(groups) if g != setting and not g.isupper()), "")
    full = title
    if label not in MASS_PARTS and "/" in title:
        title = title.split("/")[0].strip()  # English title only
    return {"label": label, "title": title, "title_full": full, "setting": re.sub(r"(?i)^mass of\s+", "", setting).strip(), "composer": composer,
            "book": code.group(1) if code else "", "number": code.group(2) if code else "",
            "kind": LABEL_KIND.get(label, "")}


def parse_outline(text):
    """Return {"date", "songs": [...]}; each song has label, title, setting, composer, book, number, kind."""
    text = text.replace("Gi!s", "Gifts").replace("Gi\ufb02s", "Gifts")
    lines = [ln for ln in (re.sub(r"\s+", " ", x).strip() for x in text.splitlines()) if ln]
    when = None
    for ln in lines:
        if ln in SECTION_HEADERS:
            break
        m = DATE_RE.search(ln)
        if m and not when:
            try:
                when = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
            except ValueError:
                pass
    rows, current, started, i = [], None, False, 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if ln in SECTION_HEADERS:
            started, current = True, None
            continue
        if not started:
            continue
        if ln == "Presentation and" and i < len(lines):
            ln = "Presentation and " + lines[i]
            i += 1
        label, rest = _split_label(ln)
        if label and not ln.startswith("("):
            current = {"label": label, "text": rest}
            rows.append(current)
        elif current is not None:
            current["text"] = (current["text"] + " " + ln).strip()
    songs = [s for r in rows if r["label"] not in SKIP_LABELS for s in [_parse_row(r["label"], r["text"])] if s]
    return {"date": when.isoformat() if when else "", "songs": songs}
