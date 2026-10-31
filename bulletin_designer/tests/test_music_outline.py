import io
import os

from bulletin_designer import outline
from tests.test_bulletin import setup_admin, tok, app  # noqa: F401

OUTLINE = """ALL SAINTS
November 01, 2026
Mass of St. Ann
INTRODUCTORY RITES
Entrance For All the Saints (SINE NOMINE) BB26-718
Penitential Act Kyrie (Mass of Saint Ann, Ed Bolduc)
LITURGY OF THE EUCHARIST
Presentation of the Gifts Come to Me (Gregory Norbet) BB26-538
COMMUNION RITE
Communion I Am the Bread of Life (Suzanne Toolan) BB26-355
CONCLUDING RITES
Final Song Companions on the Journey (Carey Landry) BB26-459
"""


def test_parse():
    p = outline.parse_outline(OUTLINE)
    assert p["date"] == "2026-11-01"
    assert [s["kind"] for s in p["songs"]] == ["entrance", "", "gifts", "communion", "going_forth"]
    assert p["songs"][0]["number"] == "718"


def test_outline_fills_slides_and_replaces_on_change(app):
    c = setup_admin(app)
    t = tok(c, "/slides")
    c.post("/slides/songs", data={"csrf": t, "title": "For All the Saints", "code": "BB26-718", "lyrics": "For all the saints\n\nwho from their labors rest"})
    c.post("/slides/songs", data={"csrf": t, "title": "Come to Me", "lyrics": "Come to me"})
    r = c.post("/slides/outline", data={"csrf": t, "date": "2026-11-01", "outline_text": OUTLINE}, follow_redirects=True)
    assert b"Not in song library" in r.data
    plan = c.get("/slides/preview?date=2026-11-01"); plan.data = plan.data.replace(b"\xc2\xa0", b" ")
    assert b"For all the saints" in plan.data and b"Come to me" in plan.data
    changed = OUTLINE.replace("Come to Me (Gregory Norbet) BB26-538", "Another Hymn (X) BB26-1")
    c.post("/slides/outline", data={"csrf": t, "date": "2026-11-01", "outline_text": changed})
    plan = c.get("/slides/preview?date=2026-11-01"); plan.data = plan.data.replace(b"\xc2\xa0", b" ")
    assert b"For all the saints" in plan.data and b"Come to me" not in plan.data


def test_pptx_applies_weekly_concept_for_every_week(app):
    c = setup_admin(app)
    t = tok(c, "/slides")
    c.post("/slides/songs", data={"csrf": t, "title": "For All the Saints", "lyrics": "For all the saints\n\nwho rest"})
    for day in range(1, 43, 7):
        d = f"2026-{11 + (day - 1) // 31:02d}-{(day - 1) % 31 + 1:02d}" if day < 32 else "2026-12-%02d" % (day - 31)
        c.post("/slides/outline", data={"csrf": t, "date": d, "outline_text": OUTLINE})
        r = c.get(f"/slides/pptx?date={d}")
        assert r.status_code == 200 and r.data[:2] == b"PK", d


def test_design_overrides_saved(app):
    c = setup_admin(app)
    t = tok(c, "/slides")
    c.post("/slides/songs", data={"csrf": t, "title": "A Song", "lyrics": "la la"})
    c.post("/slides/outline", data={"csrf": t, "date": "2026-11-08", "outline_text": OUTLINE})
    c.post("/slides/design", data={"csrf": t, "date": "2026-11-08", "layout": "centered text with a thin gold border",
                                   "accent": "rope border", "color": "#336699", "scale": "120"})
    page = c.get("/slides?date=2026-11-08").data
    assert b'value="#336699"' in page and b'value="120"' in page
    assert c.get("/slides/pptx?date=2026-11-08").data[:2] == b"PK"
    c.post("/slides/design", data={"csrf": t, "date": "2026-11-08", "reset": "1"})
    assert b'value="120"' not in c.get("/slides?date=2026-11-08").data


def test_slide_library_status(app):
    c = setup_admin(app)
    t = tok(c, "/slides")
    assert b"Not started" in c.get("/slides/library").data
    c.post("/slides/songs", data={"csrf": t, "title": "For All the Saints", "lyrics": "For all the saints"})
    c.post("/slides/outline", data={"csrf": t, "date": "2026-11-01", "outline_text": OUTLINE})
    page = c.get("/slides/library").data
    assert b"2026-11-01" in page and b"In progress" in page
    c.post("/slides/status", data={"csrf": t, "date": "2026-11-01", "ready": "1"})
    assert b"Ready to use" in c.get("/slides/library").data
    assert b"Ready to use" in c.get("/slides?date=2026-11-01").data


def test_starter_pptx_for_not_started_week(app):
    c = setup_admin(app)
    r = c.get("/slides/pptx?date=2026-12-06")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_refrain_repeats_after_each_verse():
    from bulletin_designer.outline import repeat_refrain
    parts = ["R. Lord, hear us", "Verse one", "Verse two", "Verse three"]
    assert repeat_refrain(parts) == ["R. Lord, hear us", "Verse one", "R. Lord, hear us", "Verse two",
                                     "R. Lord, hear us", "Verse three", "R. Lord, hear us"]
    already = ["Refrain: Amen", "Verse one", "Refrain: Amen", "Verse two"]
    assert repeat_refrain(already) == ["Refrain: Amen", "Verse one", "Refrain: Amen", "Verse two", "Refrain: Amen"]
    assert repeat_refrain(["One", "Two"]) == ["One", "Two"]


def test_black_slide_between_songs_and_readings(app):
    import io
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    c = setup_admin(app)
    t = tok(c, "/slides")
    d = "2026-11-08"
    for kind, name in (("entrance", "a.txt"), ("readings", "b.txt"), ("communion", "c.txt")):
        c.post("/slides", data={"csrf": t, "kind": kind, "date": d, "files": (io.BytesIO(b"hello"), name)},
               content_type="multipart/form-data")
    prs = Presentation(io.BytesIO(c.get(f"/slides/pptx?date={d}").data))
    black = [i for i, s in enumerate(prs.slides) if s.background.fill.fore_color.rgb == RGBColor(0, 0, 0)]
    assert len(black) == 2
    assert all(0 < i < len(prs.slides) - 1 for i in black)


def test_word_announcements_become_slides(app):
    import io
    import zipfile
    from pptx import Presentation
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>Food drive Sunday</w:t></w:r></w:p><w:p><w:r><w:t>Choir practice Thursday</w:t></w:r></w:p>'
           '</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    buf.seek(0)
    c = setup_admin(app)
    t = tok(c, "/slides")
    d = "2026-11-01"
    c.post("/slides", data={"csrf": t, "kind": "announcements", "date": d, "files": (buf, "Announcements.docx")},
           content_type="multipart/form-data")
    prs = Presentation(io.BytesIO(c.get(f"/slides/pptx?date={d}").data))
    text = " ".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame).replace("\u00a0", " ")
    assert "Food drive Sunday" in text and "Choir practice Thursday" in text


def test_long_reading_split_and_cp1252_decoded(tmp_path):
    from bulletin_designer.outline import read_text, split_long
    long = " ".join(f"Sentence number {i} is here." for i in range(30))
    pieces = split_long(long)
    assert len(pieces) > 1 and all(len(p) <= 240 for p in pieces)
    assert " ".join(p.replace("\n", " ") for p in pieces) == long
    assert split_long("Short") == ["Short"]
    f = tmp_path / "r.txt"
    f.write_bytes("saw \u201crising\u201d \u2014 east".encode("cp1252"))
    assert read_text(str(f)) == "saw \u201crising\u201d \u2014 east"
    f.write_bytes(b"\xef\xbb\xbfhello")
    assert read_text(str(f)) == "hello"


def test_no_orphans_joins_last_two_words():
    from bulletin_designer.outline import no_orphans
    assert no_orphans("one two three four\nHi there") == "one two three\u00a0four\nHi there"
