from bulletin_designer import choir
from tests.test_bulletin import setup_admin, tok, app  # noqa: F401


def _song(label, title, setting=""):
    return {"label": label, "title": title, "setting": setting, "composer": ""}


def test_diff_songs_detects_swapped_song():
    old = [_song("Entrance", "For All the Saints"), _song("Communion", "Look Beyond")]
    new = [_song("Entrance", "For All the Saints"), _song("Communion", "I Am the Bread of Life")]
    added, removed = choir.diff_songs(old, new)
    assert [s["title"] for s in added] == ["I Am the Bread of Life"]
    assert [s["title"] for s in removed] == ["Look Beyond"]


def test_choir_pages_load_for_admin(app):
    c = setup_admin(app)
    for url in ("/choir", "/choir/find", "/choir/library"):
        assert c.get(url).status_code == 200, url
    t = tok(c, "/choir")
    r = c.post("/choir/outline", data={"csrf": t, "outline_text": "ALL SAINTS\nNovember 01, 2026\nINTRODUCTORY RITES\nEntrance For All the Saints (SINE NOMINE) BB26-718\n"})
    assert r.status_code == 302 and "/choir/draft/" in r.headers["Location"]
    assert c.get(r.headers["Location"]).status_code == 200


def test_unknown_packet_link_is_404(app):
    assert app.test_client().get("/choir/get/" + "a" * 22).status_code == 404


def test_separate_packets_per_instrument_and_share_message(app):
    import io
    import os
    from datetime import date

    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    folder = os.path.join(choir.library_dir(app), "books")
    books = []
    for bid, edition in (("k", "keyboard"), ("g", "guitar"), ("s", "solo")):
        buf = io.BytesIO()
        cv = canvas.Canvas(buf)
        cv.drawString(72, 720, f"{edition} page")
        cv.save()
        with open(os.path.join(folder, bid + ".pdf"), "wb") as f:
            f.write(buf.getvalue())
        books.append({"id": bid, "name": edition, "code": "BB26", "edition": edition, "file": bid + ".pdf",
                      "page_offset": 0, "entries": []})
    choir.save_library(app, {"books": books})

    def src(bid, ed):
        return {"book_id": bid, "book_name": ed, "edition": ed, "start": 1, "end": 1, "entry_title": "X", "entry_number": ""}

    songs = [
        {"label": "Entrance", "title": "Song A", "include": True, "primary": src("k", "keyboard"),
         "guitar": src("g", "guitar"), "solo": None},
        {"label": "Psalm", "title": "Psalm B", "kind": "psalm", "include": True, "primary": src("k", "keyboard"),
         "guitar": None, "solo": None},
    ]
    header = {"title": "Test Sunday", "date": date(2026, 11, 8)}
    kb, _ = choir.build_packet(app, header, songs, False, edition="keyboard")
    gt, miss_g = choir.build_packet(app, header, songs, False, edition="guitar")
    so, _ = choir.build_packet(app, header, songs, False, edition="solo")
    assert len(PdfReader(io.BytesIO(kb)).pages) == 3  # cover + 2 songs
    assert len(PdfReader(io.BytesIO(gt)).pages) == 3  # cover + guitar song + psalm
    assert "Entrance: Song A" not in miss_g
    assert len(PdfReader(io.BytesIO(so)).pages) == 2  # cover + psalm only
    nothing, _ = choir.build_packet(app, header, [dict(songs[0], guitar=None)], False, edition="guitar")
    assert nothing is None

    metas = [choir.publish_packet(app, header, pdf, songs, edition=e) for e, pdf in (("keyboard", kb), ("guitar", gt), ("solo", so))]
    assert len({m["token"] for m in metas}) == 3
    again = choir.publish_packet(app, header, kb, songs, edition="keyboard")
    assert again["token"] == metas[0]["token"]

    c = setup_admin(app)
    page = c.get("/choir").get_data(as_text=True)
    assert page.count("sheet music for November 8:") == 3
    for m in metas:
        assert c.get("/choir/get/" + m["token"]).status_code == 200


def test_book_page_images_for_psalm_by_date(app):
    import io
    import os
    from datetime import date

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "psalm page")
    cv.showPage()
    cv.drawString(72, 720, "acclamation page")
    cv.save()
    with open(os.path.join(choir.library_dir(app), "books", "ra.pdf"), "wb") as f:
        f.write(buf.getvalue())
    entries = [{"number": "", "title": "X – Responsorial Psalm", "start": 1, "end": 1, "kind": "psalm", "date": "2026-11-08"},
               {"number": "", "title": "X – Gospel Acclamation", "start": 2, "end": 2, "kind": "acclamation", "date": "2026-11-08"}]
    choir.save_library(app, {"books": [{"id": "ra", "name": "RA", "code": "RA26", "edition": "keyboard", "file": "ra.pdf",
                                        "page_offset": 0, "entries": entries}]})
    song = {"label": "Responsorial Psalm", "title": "Lord, This Is the People", "setting": "", "composer": "", "book": "", "number": "", "kind": "psalm"}
    imgs = choir.book_page_images(app, song, date(2026, 11, 8))
    assert len(imgs) == 1 and imgs[0][:4] == b"\x89PNG"
    assert choir.book_page_images(app, song, date(2026, 11, 15)) == []


def test_book_page_images_opens_shared_book_once(app, monkeypatch):
    import fitz
    import io
    import os
    from datetime import date

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    for title in ("first song", "second song"):
        cv.drawString(72, 720, title)
        cv.showPage()
    cv.save()
    with open(os.path.join(choir.library_dir(app), "books", "songs.pdf"), "wb") as f:
        f.write(buf.getvalue())
    choir.save_library(app, {"books": [{
        "id": "songs", "name": "Songs", "code": "BB27", "edition": "keyboard",
        "file": "songs.pdf", "page_offset": 0,
        "entries": [
            {"number": "1", "title": "First Song", "start": 1, "end": 1},
            {"number": "2", "title": "Second Song", "start": 2, "end": 2},
        ],
    }]})
    opened = []
    real_open = fitz.open

    def counted_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(fitz, "open", counted_open)
    songs = [
        {"label": "Entrance", "title": "First Song", "setting": "", "composer": "",
         "book": "BB27", "number": "1", "kind": "entrance"},
        {"label": "Communion", "title": "Second Song", "setting": "", "composer": "",
         "book": "BB27", "number": "2", "kind": "communion"},
    ]
    images = choir.book_page_images_for_songs(app, songs, date(2026, 11, 8))
    assert [len(pages) for pages in images] == [1, 1]
    assert all(pages[0][:4] == b"\x89PNG" for pages in images)
    assert len(opened) == 1


def test_respond_acclaim_upload_generates_entries(app):
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "2027 Keyboard Edition")
    cv.drawString(72, 700, "November 29, 2026 - November 27, 2027")
    cv.showPage()
    cv.drawString(72, 750, "4")
    cv.drawString(72, 730, "Home | Indexes | List of Page Numbers")
    cv.drawString(72, 710, "First Sunday of Advent")
    cv.drawString(72, 690, "November 29")
    cv.drawString(72, 670, "Responsorial Psalm: Psalm 80")
    cv.showPage()
    cv.drawString(72, 750, "5")
    cv.drawString(72, 730, "FIRST SUNDAY OF ADVENT, cont. (2)")
    cv.drawString(72, 710, "Gospel Acclamation: Psalm 85:8")
    cv.showPage()
    cv.drawString(72, 750, "32")
    cv.drawString(72, 730, "Third Sunday in Ordinary Time")
    cv.drawString(72, 710, "January 24")
    cv.drawString(72, 690, "Responsorial Psalm: Psalm 19")
    cv.showPage()
    cv.drawString(72, 750, "33")
    cv.drawString(72, 730, "THIRD SUNDAY IN ORDINARY TIME, cont. (2)")
    cv.drawString(72, 710, "Gospel Acclamation: Luke 4:18")
    cv.save()
    buf.seek(0)

    c = setup_admin(app)
    r = c.post("/choir/library/add", data={
        "csrf": tok(c, "/choir/library"),
        "name": "Respond and Acclaim 2027",
        "code": "RA27",
        "edition": "keyboard",
        "page_offset": "0",
        "entries": "",
        "pdf": (buf, "Respond_And_Acclaim_2027.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    entries = choir.load_library(app)["books"][0]["entries"]
    assert [(e["kind"], e["date"], e["start"]) for e in entries] == [
        ("psalm", "2026-11-29", 4),
        ("acclamation", "2026-11-29", 5),
        ("psalm", "2027-01-24", 32),
        ("acclamation", "2027-01-24", 33),
    ]


def test_library_page_repairs_existing_empty_respond_acclaim_book(app):
    import io
    import os

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "2027 Keyboard Edition")
    cv.drawString(72, 700, "November 29, 2026 - November 27, 2027")
    cv.showPage()
    cv.drawString(72, 750, "4")
    cv.drawString(72, 710, "First Sunday of Advent")
    cv.drawString(72, 690, "November 29")
    cv.drawString(72, 670, "Responsorial Psalm: Psalm 80")
    cv.showPage()
    cv.drawString(72, 750, "5")
    cv.drawString(72, 710, "Gospel Acclamation: Psalm 85:8")
    cv.save()
    folder = os.path.join(choir.library_dir(app), "books")
    with open(os.path.join(folder, "ra27.pdf"), "wb") as fh:
        fh.write(buf.getvalue())
    choir.save_library(app, {"books": [{
        "id": "ra27",
        "name": "Respond & Acclaim 2027",
        "code": "RA27",
        "edition": "keyboard",
        "file": "ra27.pdf",
        "page_offset": 0,
        "entries": [],
    }]})

    c = setup_admin(app)
    page = c.get("/choir/library")
    assert b"Generated entries from the PDF" in page.data
    assert len(choir.load_library(app)["books"][0]["entries"]) == 2


def test_breaking_bread_upload_generates_song_entries(app):
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    for _ in range(12):
        cv.drawString(72, 720, "Front matter")
        cv.showPage()
    cv.drawString(72, 720, "101 Amazing Grace")
    cv.showPage()
    cv.drawString(72, 720, "101 - Amazing Grace, cont.")
    cv.showPage()
    cv.drawString(72, 720, "102 Be Not Afraid")
    cv.save()
    buf.seek(0)

    c = setup_admin(app)
    c.post("/choir/library/add", data={
        "csrf": tok(c, "/choir/library"),
        "name": "Breaking Bread 2027 Keyboard",
        "code": "BB27",
        "edition": "keyboard",
        "page_offset": "0",
        "entries": "",
        "pdf": (buf, "Breaking_Bread_2027_Keyboard.pdf"),
    }, content_type="multipart/form-data")
    entries = choir.load_library(app)["books"][0]["entries"]
    assert entries == [
        {"number": "101", "title": "Amazing Grace", "start": 13, "end": 14},
        {"number": "102", "title": "Be Not Afraid", "start": 15, "end": 15},
    ]
    path = choir.book_path(app, choir.load_library(app)["books"][0])
    assert choir.generate_book_entries(path, "CP4", "Choral Praise 4") == entries
    assert choir.generate_book_entries(path, "SSAIDE", "Spirit & Psalm") == entries
    assert c.get("/choir/library/add", follow_redirects=True).status_code == 200


def test_numbered_songbook_index_disambiguates_numeric_title():
    texts = ["Front matter"] * 14 + [
        "Lord, Have Mercy\n2\n15",
        "40 Days\nMatt Maher\n534",
        "155—40 Days (2)\n535",
        "Scriptural Index\n2 Corinthians",
        "First Lines and Common Titles\n2 Lord, Have Mercy\n155 40 Days",
    ]
    assert choir._numbered_songbook_entries_from_texts(texts) == [
        {"number": "2", "title": "Lord, Have Mercy", "start": 15, "end": 15},
        {"number": "155", "title": "40 Days", "start": 16, "end": 17},
    ]


def test_breaking_bread_index_maps_bb_number_to_music_issue_entry(app):
    import io
    import os
    from datetime import date

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    for _ in range(12):
        cv.drawString(72, 720, "Front matter")
        cv.showPage()
    cv.drawString(72, 720, "216 Give Me Jesus")
    cv.showPage()
    cv.drawString(72, 720, "Alphabetical Index")
    cv.showPage()
    cv.drawString(72, 720, "A | B | C")
    cv.drawString(72, 700, "Give Me Jesus (Spiritual) ... BB/MI 517 216")
    cv.save()
    path = os.path.join(choir.library_dir(app), "books", "bb27.pdf")
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())

    entries = choir.generate_book_entries(path, "BB27", "Breaking Bread 2027 Keyboard")
    assert entries == [{
        "number": "216",
        "title": "Give Me Jesus (Spiritual)",
        "start": 13,
        "end": 13,
        "catalog_numbers": {"BB": "517", "MI": "517", "ACCOMP": "216"},
    }]

    old_entry = {key: value for key, value in entries[0].items() if key != "catalog_numbers"}
    book = {"id": "bb27", "name": "Breaking Bread 2027 Keyboard", "code": "BB27",
            "edition": "keyboard", "file": "bb27.pdf", "page_offset": 0, "entries": [old_entry]}
    choir.save_library(app, {"books": [book]})
    assert setup_admin(app).get("/choir/library").status_code == 200
    book = choir.load_library(app)["books"][0]
    assert book["entries"][0]["catalog_numbers"] == {
        "BB": "517", "MI": "517", "ACCOMP": "216",
    }
    assert book["index_version"] == choir.BOOK_INDEX_VERSION

    song = {"label": "Final Song", "title": "Give Me Jesus", "setting": "", "composer": "James Hansen",
            "book": "BB27", "number": "517"}
    matched = choir.match_songs({"books": [book]}, [song], date(2027, 1, 24))[0]
    assert matched["primary"]["entry_number"] == "216"
    assert [r["title"] for r in choir.search_library(app, "Give Me Jesus")] == ["Give Me Jesus (Spiritual)"]
    assert len(choir.search_library(app, "517")) == 1
    assert len(choir.search_library(app, "216")) == 1
    assert len(choir.search_library(app, "BB27-517")) == 1


def test_catalog_numbers_survive_entry_edit_and_existing_book_upgrade():
    entries = [{"number": "216", "title": "Give Me Jesus", "start": 40, "end": 42,
                "catalog_numbers": {"BB": "517", "MI": "517", "ACCOMP": "216"}}]
    reparsed, errors = choir.parse_entries(choir.entries_to_text(entries))
    assert errors == []
    assert reparsed == entries

    old_book = {"entries": [{"number": "216", "title": "Give Me Jesus", "start": 40, "end": 42}]}
    assert choir.upgrade_book_entries(old_book, entries)
    assert old_book["entries"][0]["catalog_numbers"] == {
        "BB": "517", "MI": "517", "ACCOMP": "216",
    }
    assert old_book["index_version"] == choir.BOOK_INDEX_VERSION


def test_existing_book_upgrade_adds_newly_discovered_song():
    book = {"code": "BB27", "entries": [{
        "number": "517",
        "title": "Somebody’s Knockin’ At Your Door",
        "start": 987,
        "end": 987,
    }, {
        "number": "104",
        "title": "Beyond the Moon and Stars",
        "start": 200,
        "end": 200,
    }]}
    generated = [
        {"number": "216", "title": "Give Me Jesus", "start": 410, "end": 410},
        {"number": "517", "title": "Somebody’s Knockin’ At Your Door", "start": 986, "end": 986},
        {"number": "104", "title": "Beyond the Moon and Stars", "start": 199, "end": 200},
    ]
    assert choir.upgrade_book_entries(book, generated)
    assert book["entries"] == [
        {"number": "517", "title": "Somebody’s Knockin’ At Your Door", "start": 986, "end": 986},
        {"number": "104", "title": "Beyond the Moon and Stars", "start": 199, "end": 200},
        {"number": "216", "title": "Give Me Jesus", "start": 410, "end": 410},
    ]


def test_bb_mi_index_parser_accepts_split_pdf_columns():
    expected = [{
        "title": "Give Me Jesus (Spiritual)",
        "BB": "517",
        "MI": "517",
        "ACCOMP": "216",
    }]
    assert choir._bb_mi_index_records(
        "Give Me Jesus (Spiritual) ... BB/MI 517 216"
    ) == expected
    assert choir._bb_mi_index_records(
        "Give Me Jesus (Spiritual) ...\nBB/MI\n517\n216"
    ) == expected
    assert choir._bb_mi_index_records(
        "★Give Me Jesus (Spiritual) ...\nBB/MI\n517\n216"
    ) == expected


def test_numbered_outline_does_not_use_partial_title_fallback():
    from datetime import date

    book = {
        "id": "bb26",
        "name": "Breaking Bread 2026",
        "code": "BB26",
        "edition": "keyboard",
        "file": "bb26.pdf",
        "entries": [{
            "number": "148",
            "title": "Come, Emmanuel",
            "start": 299,
            "end": 300,
        }],
    }
    song = {"label": "Entrance", "title": "O Come, O Come, Emmanuel", "setting": "",
            "composer": "", "book": "BB26", "number": "39"}
    assert choir.match_songs({"books": [book]}, [song], date(2026, 11, 29))[0]["primary"] is None


def test_requested_book_code_wins_until_that_edition_is_unavailable():
    from datetime import date

    def book(book_id, code):
        return {"id": book_id, "name": code, "code": code, "edition": "keyboard", "file": book_id + ".pdf",
                "entries": [{"number": "459", "title": "Companions on the Journey", "start": 10, "end": 11}]}

    song = {"label": "Final Song", "title": "Companions on the Journey", "setting": "", "composer": "",
            "book": "BB27", "number": "459"}
    matched = choir.match_songs({"books": [book("old", "BB26"), book("new", "BB27")]}, [song], date(2027, 1, 24))[0]
    assert matched["primary"]["book_id"] == "new"

    unavailable = book("empty", "BB27")
    unavailable["entries"] = []
    fallback = choir.match_songs({"books": [book("old", "BB26"), unavailable]}, [song], date(2027, 1, 24))[0]
    assert fallback["primary"]["book_id"] == "old"


def test_choir_director_role_sees_only_choir(app):
    import sqlite3
    from tests.test_bulletin import setup_admin, tok
    c = setup_admin(app)
    c.post("/admin/users", data={"action": "create", "role": "choir", "username": "dir@example.com", "display_name": "Director",
                                 "csrf": tok(c, "/admin/users")})
    db = sqlite3.connect(app.config["DATABASE"])
    row = db.execute("SELECT id FROM users WHERE username='dir@example.com'").fetchone()
    assert row
    from bulletin_designer.app import ROLES
    assert "choir" in ROLES
    d = app.test_client()
    with d.session_transaction() as sess:
        sess["uid"] = row[0]
        sess["csrf"] = "x"
    db.execute("UPDATE users SET must_reset=0 WHERE id=?", (row[0],)); db.commit()
    page = d.get("/choir/find")
    assert page.status_code == 200 and "Design studio" not in page.text and "Bulletins" not in page.text
    assert d.get("/bulletins/1/preview").status_code == 403
    assert d.get("/").status_code in (302, 308)


def test_year_plan_hides_past_and_links_started_weeks(app):
    from datetime import date, timedelta
    c = setup_admin(app)
    html = c.get("/slides/year").text
    assert 'id="yr-search"' in html and "yr-scroll" in html
    t = date.today().isoformat()
    upcoming = html.split('id="yr-past"')[0]
    assert all(d < t for d in __import__("re").findall(r'<td>(\d{4}-\d{2}-\d{2})</td>', html.split('id="yr-past"')[1])) if 'id="yr-past"' in html else True
    assert not any(d < t for d in __import__("re").findall(r'<td>(\d{4}-\d{2}-\d{2})</td>', upcoming))
