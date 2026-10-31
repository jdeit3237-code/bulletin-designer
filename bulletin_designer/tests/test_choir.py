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

