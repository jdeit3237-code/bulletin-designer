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
