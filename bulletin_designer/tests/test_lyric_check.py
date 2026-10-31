import io
import os

from reportlab.pdfgen import canvas

from bulletin_designer import choir, lyric_check
from tests.test_bulletin import app  # noqa: F401


def _book(app, header, body):
    folder = os.path.join(choir.library_dir(app), "books")
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, header)
    c.drawString(72, 700, body)
    c.save()
    with open(os.path.join(folder, "t.pdf"), "wb") as f:
        f.write(buf.getvalue())
    lib = {"books": [{"id": "b1", "name": "T", "code": "BB26", "edition": "keyboard", "file": "t.pdf",
                      "page_offset": 0, "entries": []}]}
    choir.save_library(app, lib)


def _song(number="718"):
    return {"label": "Entrance", "title": "For All the Saints", "book": "BB26", "number": number,
            "include": True,
            "primary": {"book_id": "b1", "book_name": "T", "edition": "keyboard", "start": 1, "end": 1,
                        "entry_title": "For All the Saints", "entry_number": "718"}}


LYRICS = "For all the saints who from their labors rest\nWho thee by faith before the world confessed"


def test_matching_lyrics_and_number_pass(app):
    _book(app, "718 For All the Saints", "For all the saints who from their labors rest who thee by faith before the world confessed")
    assert lyric_check.check_song(app, _song(), LYRICS)["status"] == "ok"


def test_wrong_hymn_number_is_flagged(app):
    _book(app, "388 For All the Saints", "For all the saints who from their labors rest who thee by faith before the world confessed")
    assert lyric_check.check_song(app, _song(), LYRICS)["status"] == "review"


def test_different_lyrics_are_flagged(app):
    _book(app, "718 Another Song", "Amazing grace how sweet the sound that saved a wretch like me")
    res = lyric_check.check_song(app, _song(), LYRICS)
    assert res["status"] == "review" and res["coverage"] < 0.55


def test_missing_lyrics_reported(app):
    _book(app, "718 For All the Saints", "For all the saints")
    assert lyric_check.check_song(app, _song(), "")["status"] == "no_lyrics"


def test_language_option_uses_other_language_only_when_in_books():
    lib = {"books": [{"id": "k", "name": "K", "code": "BB26", "edition": "keyboard", "file": "k.pdf",
                      "entries": [
                          {"number": "275", "title": "I Am the Bread of Life", "start": 5, "end": 6},
                          {"number": "445", "title": "Pan de Vida/Bread of Life", "start": 9, "end": 9},
                          {"number": "10", "title": "Come to Me", "start": 3, "end": 3}]}]}
    songs = [
        {"label": "Communion", "title": "Bread of Life/Pan de Vida", "setting": "", "composer": "", "book": "", "number": ""},
        {"label": "Gifts", "title": "Come to Me/Ven a Mi", "setting": "", "composer": "", "book": "", "number": ""},
    ]
    en = choir.match_songs(lib, songs, None)
    assert en[0]["primary"]["entry_number"] == "445" and not en[0]["english_only"]
    es = choir.match_songs(lib, songs, None, "other")
    assert es[0]["primary"]["entry_number"] == "445" and es[0]["language"] == "other"
    assert es[1]["primary"]["entry_number"] == "10" and es[1]["english_only"]
