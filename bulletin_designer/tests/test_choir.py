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
