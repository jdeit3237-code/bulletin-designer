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


VERSE_NUM_RE = re.compile(r"^\s*(?:verse\s*)?\(?\d{1,2}\s*[.):\-]?\s+(?=\S)", re.I | re.M)


def strip_verse_numbers(part):
    """Remove leading verse numbers such as '1.', '2)' or 'Verse 3' from a slide part."""
    return VERSE_NUM_RE.sub("", part).strip()


def repeat_refrain(parts):
    """Slide parts with verse numbers removed and the refrain placed between every verse."""
    numbered = [bool(VERSE_NUM_RE.match(p)) for p in parts]
    parts = [strip_verse_numbers(p) if n else p for p, n in zip(parts, numbered)]
    refrain = next((p for p in parts if REFRAIN_RE.match(p)), None)
    if refrain is None and any(numbered):
        refrain = next((p for p, n in zip(parts, numbered) if not n), None)
    if not refrain:
        return parts
    out = []
    for i, part in enumerate(parts):
        if part is refrain or REFRAIN_RE.match(part):
            out.append(part)
        else:
            out.append(part)
            nxt = parts[i + 1] if i + 1 < len(parts) else None
            if nxt is None or not (nxt is refrain or REFRAIN_RE.match(nxt)):
                out.append(refrain)
    return out


READING_HEAD_RE = re.compile(
    r"^[\s*_#]*(?P<h>(?:first|second|third)\s+reading|reading\s*(?:1|2|3|i{1,3}|one|two|three)|responsorial\s+psalm|psalm|"
    r"gospel\s+acclamation|alleluia|gospel)\s*[:.\-]?[\s*_]*$", re.I)
R_LINE_RE = re.compile(r"^\s*(?:\u211f|R\s*[./:])\s*(?:\(see [^)]*\)\s*)?", re.I)
_ORDINAL = {"1": "First", "i": "First", "one": "First", "2": "Second", "ii": "Second", "two": "Second", "3": "Third", "iii": "Third", "three": "Third"}


def reading_title(head):
    """Canonical slide title for a readings-file heading (Reading 1 -> First Reading)."""
    h = re.sub(r"\s+", " ", head.strip()).lower()
    m = re.match(r"reading (\w+)$", h)
    if m:
        return _ORDINAL.get(m.group(1), m.group(1).title()) + " Reading"
    return {"psalm": "Responsorial Psalm", "alleluia": "Gospel Acclamation"}.get(h, h.title())


def reading_sections(raw):
    """Split a readings file on its heading lines into [(title, parts)]; None when no headings are found.

    Responsorial Psalm and Gospel Acclamation: each 'R.' response is its own slide, with the verses between."""
    sections, title, buf = [], None, []

    def flush():
        if title is None:
            return
        text = "\n".join(buf).strip()
        if title == "Gospel Acclamation":
            lines = [x for x in text.splitlines() if not re.match(r"^[\s*_]*(?:\u211f|R\s*[./:])?\s*(?:alleluia|praise to you)\b[\s.,!*_]*(?:alleluia[\s.,!*_]*)*$", x, re.I)]
            parts = [x.strip() for x in re.split(r"\n\s*\n", "\n".join(lines)) if x.strip()]
            sections.append((title, [piece for p in parts for piece in split_long(p)]))
            return
        if title == "Responsorial Psalm":
            text = re.sub(r"\(see [^)]*\)\s*", "", text)
            text = re.sub(r"^[ \t]*((?:\u211f|R\s*[./:])[^\n]*)$", r"\n\1\n", text, flags=re.M | re.I)
        parts = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
        if title == "Responsorial Psalm":
            parts = [R_LINE_RE.sub("", p) if R_LINE_RE.match(p) else p for p in parts]
            raw_resp = [p for p in re.findall(r"^[ \t]*(?:\u211f|R\s*[./:])\s*(.+)$", text, flags=re.M | re.I)]
            if raw_resp:
                resp = raw_resp[0].strip()
                out = []
                for p in parts:
                    if p == resp:
                        out.append(p)
                    else:
                        out.append(p)
                        out.append(resp)
                dedup = []
                for p in out:
                    if dedup and dedup[-1] == p == resp:
                        continue
                    dedup.append(p)
                if dedup[0] != resp:
                    dedup.insert(0, resp)
                parts = dedup
        parts = [piece for p in parts for piece in split_long(p)]
        sections.append((title, parts))

    for line in raw.splitlines():
        m = READING_HEAD_RE.match(line)
        if m:
            flush()
            title, buf = reading_title(m.group("h")), []
        elif title is not None:
            buf.append(line)
    flush()
    return sections or None


def no_orphans(text):
    """Join the last two words of every line with a non-breaking space so a wrapped line never ends on one lone word."""
    out = []
    for line in text.split("\n"):
        words = line.rstrip().split(" ")
        if len(words) > 2:
            line = " ".join(words[:-2] + [words[-2] + "\u00a0" + words[-1]])
        out.append(line)
    return "\n".join(out)




ANNOUNCE_RE = re.compile(r"^[\s*_]*(lector\s*:?\s*)?(?=our parish announcements)", re.I)


def announcements_from(parts):
    """Keep a Word script from 'Our Parish Announcements this week' onward (dropping the lector cue)."""
    for i, part in enumerate(parts):
        if ANNOUNCE_RE.match(part):
            return [ANNOUNCE_RE.sub("", part, count=1)] + parts[i + 1:]
    return parts

def read_text(path):
    """Read a text file saved as UTF-8 (with or without BOM) or Windows-1252, without junk characters."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


SOFT_BREAK_RE = re.compile(r"[,;:\u2014\u2013]\s+|\s+(?=(?:and|but|or|who|whom|which|that|for|so|because|when|while|then)\s)", re.I)


def _soft_cut(sent, limit):
    """Index to cut an over-long sentence: after the last comma/semicolon (or before a conjunction) that fits."""
    best = 0
    for m in SOFT_BREAK_RE.finditer(sent):
        end = m.end() if m.group(0).strip() else m.start()
        if end > limit:
            break
        if end >= limit * 0.35:
            best = end
    if best:
        return best
    cut = sent.rfind(" ", 0, limit)
    return cut if cut > 0 else limit


def split_long(part, limit=220):
    """Break prose into slide-sized pieces: between sentences, or at a comma/semicolon/conjunction inside a long sentence."""
    text = " ".join(x.strip() for x in part.splitlines() if x.strip())
    if len(text) <= limit:
        return [text] if text else []
    sentences = [x for x in re.split(r"(?:(?<=[.!?])|(?<=[.!?][\"\u201d\u2019']))\s+", text) if x]
    out, cur = [], ""
    for sent in sentences:
        while len(sent) > limit:
            cut = _soft_cut(sent, limit)
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + len(sent) + 1 > limit:
            out.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent) if cur else sent
    if cur:
        out.append(cur)
    return out


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
    return {"date": when.isoformat() if when else "", "songs": songs, "order": [r["label"] for r in rows]}


READING_LABELS = ("First Reading", "Responsorial Psalm", "Second Reading", "Gospel Acclamation", "Gospel")
STANDARD_KEYWORDS = (
    ("Penitential Act", ("penitential", "confiteor", "i confess", "kyrie", "lord have mercy")),
    ("Glory to God", ("gloria", "glory to god")),
    ("Gospel Acclamation", ("alleluia", "acclamation")),
    ("Creed", ("creed", "profession")),
    ("Mystery of Faith", ("mystery", "memorial")),
    ("Holy", ("holy", "sanctus")),
    ("Lord's Prayer", ("lord's prayer", "lords prayer", "our father")),
    ("Lamb of God", ("lamb", "agnus")),
    ("Amen", ("amen",)),
)
_NORM = lambda x: re.sub(r"[^a-z0-9]+", "", x.lower())


def chunk_label(kind, name, group, order):
    """Which outline item a slide file or reading section belongs to, or None when unknown."""
    if kind == "readings" and group in READING_LABELS:
        return group
    if kind == "standard":
        low = name.lower().replace("_", " ")
        return next((lab for lab, keys in STANDARD_KEYWORDS if any(k in low for k in keys)), None)
    if kind == "announcements":
        return "Announcements"
    if kind == "special":
        return "Special"
    stem = _NORM(name)
    for lab in sorted(order, key=len, reverse=True):
        if stem.startswith(_NORM(lab)):
            return lab
    return next((lab for lab in order if kind != "songs" and LABEL_KIND.get(lab) == kind), None)


MASS_ORDER = ["Prelude", "Entrance", "Penitential Act", "Glory to God", "First Reading", "Responsorial Psalm", "Second Reading",
              "Gospel Acclamation", "Gospel", "Creed", "Presentation and Preparation of the Gifts", "Holy", "Mystery of Faith",
              "Lord's Prayer", "Amen", "Lamb of God", "Communion", "Song of Praise", "Announcements", "Special", "Final Song"]
LABEL_ALIAS = {"Presentation of the Gifts": "Presentation and Preparation of the Gifts", "Offertory": "Presentation and Preparation of the Gifts",
               "Final Song or Hymn": "Final Song", "Recessional": "Final Song", "Sequence": "Gospel Acclamation"}


def order_chunks(chunks, order=()):
    """Sort slide chunks into the order of the Mass (the liturgy outline); each chunk gets its "label". Stable for ties."""
    names = list(dict.fromkeys(list(order) + MASS_ORDER + list(LABEL_ALIAS)))
    pos = {lab: float(i) for i, lab in enumerate(MASS_ORDER)}
    last = -1.0
    for ch in chunks:
        lab = chunk_label(ch["kind"], ch["name"], ch.get("group"), names)
        lab = LABEL_ALIAS.get(lab, lab)
        ch["label"] = lab
        ch["rank"] = pos.get(lab, last)
        last = ch["rank"]
    return sorted(chunks, key=lambda c: c["rank"])
