"""Fail-safe: confirm the slide lyrics and the sheet-music pages are the same song.

For each song in a week's outline we compare the lyrics saved for the slides
with the text on the sheet-music pages chosen for the choir packet. A song is
flagged when the page header shows a different hymn number or when too few
lyric words appear on the music.
"""
import os
import re

from pypdf import PdfReader

try:
    from . import choir
except ImportError:  # run as a script
    import choir

MIN_COVERAGE = 0.55
HEADER_NUM = re.compile(r"^\s*(\d{1,3})\s*(?:[—–-]\s*)?\S")
_cache = {}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("&", "and")).strip("-")[:80]


def _letters(text):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def slides_songs_dir(app):
    base = os.path.dirname(os.path.realpath(app.config["UPLOAD_DIR"]))
    return os.path.join(base, "slides_files", "songs")


def find_lyrics(app, song):
    """Return the saved slide lyrics (text) for an outline song, or ''."""
    folder = slides_songs_dir(app)
    keys = [_slug(song.get("title"))]
    if song.get("number"):
        keys.append(_slug(f"{song.get('book', '')}-{song['number']}"))
    for key in keys:
        path = os.path.join(folder, key + ".txt")
        if key and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return ""


def _page_texts(app, source):
    lib = choir.load_library(app)
    book = choir._book_by_id(lib, source["book_id"])
    if not book:
        return None, "book not in library"
    path = choir.book_path(app, book)
    if not os.path.exists(path):
        return None, "book file missing"
    reader = _cache.get(path) or _cache.setdefault(path, PdfReader(path))
    offset = int(book.get("page_offset", 0))
    texts = []
    for p in range(source["start"], source["end"] + 1):
        idx = p + offset - 1
        if 0 <= idx < len(reader.pages):
            texts.append(reader.pages[idx].extract_text() or "")
    return texts, ""


def _header_numbers(texts):
    """Hymn numbers shown in the header lines of each sheet-music page."""
    found = set()
    for text in texts:
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("Home")]
        for ln in lines[:4]:
            m = HEADER_NUM.match(ln)
            if m and re.search(r"[A-Za-z]{3}", ln):
                found.add(m.group(1))
    return found


def lyric_coverage(lyrics, texts):
    """Share of distinct lyric words (4+ letters) found in the music text.

    Notation text splits words into syllables, so the music text is compared
    with spaces and punctuation removed.
    """
    blob = _letters(" ".join(texts))
    words = {w for w in re.findall(r"[a-z]+", lyrics.lower()) if len(w) >= 4}
    if not words:
        return None
    return sum(1 for w in words if w in blob) / len(words)


def check_song(app, song, lyrics=None):
    """Return {'status': ok|review|no_lyrics|no_music|skipped, 'detail': str, 'coverage': float|None}."""
    source = song.get("primary")
    if not song.get("include", True) and not source:
        return {"status": "skipped", "detail": "", "coverage": None}
    if not source:
        return {"status": "no_music", "detail": "No sheet music matched.", "coverage": None}
    texts, err = _page_texts(app, source)
    if texts is None:
        return {"status": "no_music", "detail": f"Sheet music unavailable ({err}).", "coverage": None}
    problems = []
    # The outline's hymn number is not the book's own number, so verify the pages
    # against the library entry the song was matched to.
    number = str(source.get("entry_number") or "")
    if number:
        nums = _header_numbers(texts)
        if nums and number not in nums:
            problems.append(
                f"Matched {source['entry_title']} (no. {number}) but the music pages are numbered "
                f"{', '.join(sorted(nums))}."
            )
    lyrics = find_lyrics(app, song) if lyrics is None else lyrics
    cov = None
    if not lyrics.strip():
        status, detail = "no_lyrics", "No slide lyrics saved for this song."
    else:
        cov = lyric_coverage(lyrics, texts)
        if cov is not None and cov < MIN_COVERAGE:
            problems.append(f"Only {round(cov * 100)}% of the slide lyrics appear on the music pages.")
        status, detail = "ok", "Lyrics match the sheet music."
    if problems:
        status, detail = "review", " ".join(problems)
    return {"status": status, "detail": detail, "coverage": cov}


def check_week(app, songs):
    """Check every included song; returns (results list, blocking list)."""
    results, blocking = [], []
    for song in songs:
        if song.get("include") is False:
            continue
        res = check_song(app, song)
        res["label"], res["title"] = song.get("label", ""), song.get("title", "")
        results.append(res)
        if res["status"] == "review":
            blocking.append(res)
    return results, blocking
