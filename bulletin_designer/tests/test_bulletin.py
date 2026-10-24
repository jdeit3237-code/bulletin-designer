import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from bulletin_designer.app import create_app  # noqa: E402

CSS = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BULLETIN_ADMIN_PASSWORD", "temporary-pass-1")
    return create_app({"DATABASE": str(tmp_path / "t.db"), "UPLOAD_DIR": str(tmp_path / "up")})


def login(client, user="admin", pw="temporary-pass-1"):
    page = client.get("/login")
    token = re.search(r'name="csrf" value="(\w+)"', page.text).group(1)
    return client.post("/login", data={"username": user, "password": pw, "csrf": token}, follow_redirects=True)


def tok(client, path="/"):
    return re.search(r'name="csrf" value="(\w+)"', client.get(path).text).group(1)


def setup_admin(app):
    c = app.test_client()
    login(c)
    c.post("/password", data={"current": "temporary-pass-1", "new": "a-better-password", "csrf": tok(c, "/password")})
    return c


def test_font_floor():
    css = open(CSS).read()
    for m in re.finditer(r"font(?:-size)?\s*:[^;]*?(\d+(?:\.\d+)?)(pt|px)", css):
        size = float(m.group(1)) * (0.75 if m.group(2) == "px" else 1)
        if size < 12 and "line-height" not in m.group(0):
            pytest.fail(f"font below 12pt: {m.group(0)}")


def test_forced_password_reset(app):
    c = app.test_client()
    r = login(c)
    assert "must choose a new password" in r.text


def test_requires_login(app):
    assert app.test_client().get("/").status_code == 302


def create_bulletin(c, day="2026-10-25"):
    c.post("/bulletins/new", data={"sunday": day, "csrf": tok(c)})


def test_pages_multiple_of_four_and_toc(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "Hello", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert html.count('class="sheet') % 4 == 0
    assert "In this issue" in html and "Page 1 of 4" in html


def test_standing_carries_weekly_does_not(app):
    c = setup_admin(app)
    create_bulletin(c, "2026-10-18")
    c.post("/bulletins/1/section/3", data={"body": "Fr. Medina", "csrf": tok(c, "/bulletins/1")})
    c.post("/bulletins/1/section/7", data={"body": "Mon | 6pm | Zumba", "csrf": tok(c, "/bulletins/1")})
    create_bulletin(c, "2026-10-25")
    page = c.get("/bulletins/2/section/3").text
    assert "Fr. Medina" in page
    assert "Zumba" not in c.get("/bulletins/2/section/7").text


def test_roles(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/admin/users")
    for name, role in (("viewer1", "viewer"), ("contrib", "contributor")):
        c.post("/admin/users", data={"action": "create", "username": name, "role": role,
                                     "password": "password-12345", "csrf": t})
    c.post("/admin/users", data={"action": "role", "uid": "3", "role": "contributor", "sections": "5", "csrf": t})
    # contributor id 3 owns Welcome? section id 3 is mass? verify via behaviour below
    v = app.test_client()
    login(v, "viewer1", "password-12345")
    v.post("/password", data={"current": "password-12345", "new": "another-password", "csrf": tok(v, "/password")})
    assert v.post("/bulletins/1/section/1", data={"body": "x", "csrf": tok(v, "/bulletins/1")}).status_code == 403
    assert v.get("/admin/users").status_code == 403
    k = app.test_client()
    login(k, "contrib", "password-12345")
    k.post("/password", data={"current": "password-12345", "new": "another-password", "csrf": tok(k, "/password")})
    assert k.post("/bulletins/1/section/1", data={"body": "x", "csrf": tok(k, "/bulletins/1")}).status_code == 403
    assert k.post("/bulletins/1/section/5", data={"body": "Welcome!", "csrf": tok(k, "/bulletins/1")}).status_code == 302
    assert "Awaiting approval" in c.get("/bulletins/1").text


def test_alt_text_required(app):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    r = c.post("/bulletins/1/section/5", data={"body": "x", "csrf": tok(c, "/bulletins/1"),
               "image": (io.BytesIO(b"\x89PNG"), "a.png")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Alternative text is required" in r.text


def test_open_redirect_blocked(app):
    c = app.test_client()
    t = re.search(r'name="csrf" value="(\w+)"', c.get("/login").text).group(1)
    r = c.post("/login?next=//evil.com", data={"username": "admin", "password": "temporary-pass-1", "csrf": t})
    assert "evil.com" not in r.headers["Location"]


def test_assistant_and_custom_sections(app):
    c = setup_admin(app)
    create_bulletin(c)
    r = c.post("/bulletins/1/assistant", data={"request": "add section Harvest Dinner", "csrf": tok(c, "/bulletins/1")})
    assert "Harvest Dinner" in r.text and "Apply changes" in r.text
    c.post("/bulletins/1/assistant", data={"confirm": "1", "csrf": tok(c, "/bulletins/1")})
    assert "Harvest Dinner" in c.get("/bulletins/1").text
    r = c.post("/bulletins/1/assistant", data={"request": "make Harvest Dinner 2 columns", "csrf": tok(c, "/bulletins/1")})
    assert "cols = 2" in r.text
    create_bulletin(c, "2026-11-01")
    assert "Harvest Dinner" not in c.get("/bulletins/2").text


def test_photo_library_reuse(app):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/library", data={"title": "Church", "alt": "The church", "csrf": tok(c, "/library"),
           "photo": (io.BytesIO(b"\x89PNG"), "c.png")}, content_type="multipart/form-data")
    assert "Church" in c.get("/library").text
    c.post("/bulletins/1/section/5", data={"body": "x", "library_id": "1", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert 'alt="The church"' in html
    r = c.post("/library/1/delete", data={"csrf": tok(c, "/library")}, follow_redirects=True)
    assert "cannot be deleted" in r.text


def make_docx(paragraphs):
    import io
    import zipfile
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    buf.seek(0)
    return buf


def test_docx_upload(app):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "", "csrf": tok(c, "/bulletins/1"),
           "document": (make_docx(["Dear parishioners", "Peace be with you"]), "m.docx")},
           content_type="multipart/form-data")
    page = c.get("/bulletins/1/section/9").text
    assert "Dear parishioners" in page and "Peace be with you" in page
    r = c.post("/bulletins/1/section/9", data={"body": "", "csrf": tok(c, "/bulletins/1"),
               "document": (io.BytesIO(b"junk"), "bad.docx")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Could not read" in r.text


def test_txt_upload(app):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "", "csrf": tok(c, "/bulletins/1"),
           "document": (io.BytesIO("Plain note caf\u00e9".encode("utf-8")), "n.txt")}, content_type="multipart/form-data")
    assert "Plain note" in c.get("/bulletins/1/section/9").text


def test_pdf_downloads(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "Hello", "csrf": tok(c, "/bulletins/1")})
    assert c.get("/bulletins/1/download/bogus").status_code == 404
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        r = c.get("/bulletins/1/download/web", follow_redirects=True)
        assert "PDF export is not available" in r.text
        return
    web = c.get("/bulletins/1/download/web")
    prt = c.get("/bulletins/1/download/print")
    assert web.data.startswith(b"%PDF") and prt.data.startswith(b"%PDF")
    import io
    from pypdf import PdfReader
    root = PdfReader(io.BytesIO(web.data)).trailer["/Root"]
    assert root["/MarkInfo"]["/Marked"] and root["/Lang"] and "/StructTreeRoot" in root
    assert len(PdfReader(io.BytesIO(web.data)).pages) % 4 == 0


def test_backup_and_restore(app, tmp_path, monkeypatch):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "Moved content", "csrf": tok(c, "/bulletins/1")})
    data = c.get("/admin/backup").data
    other = create_app({"DATABASE": str(tmp_path / "new.db"), "UPLOAD_DIR": str(tmp_path / "newup")})
    n = setup_admin(other)
    r = n.post("/admin/restore", data={"backup": (io.BytesIO(data), "b.zip"), "csrf": tok(n, "/password")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    n2 = other.test_client()
    login(n2, "admin", "a-better-password")
    assert "Moved content" in n2.get("/bulletins/1/section/9").text
    bad = n2.post("/admin/restore", data={"backup": (io.BytesIO(b"nope"), "b.zip"), "csrf": tok(n2, "/admin/users")},
                  content_type="multipart/form-data", follow_redirects=True)
    assert "not a valid bulletin backup" in bad.text


def test_group_user_for_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/admin/users").text
    assert "Youth Group Calendar" in page
    sid = re.search(r'value="(\d+)">\s*Youth Group Calendar', page).group(1)
    c.post("/admin/users", data={"action": "create", "username": "youth", "role": "contributor",
           "password": "password-12345", "sections": sid, "csrf": tok(c, "/admin/users")})
    y = app.test_client()
    login(y, "youth", "password-12345")
    y.post("/password", data={"current": "password-12345", "new": "another-password", "csrf": tok(y, "/password")})
    ok = y.post(f"/bulletins/1/section/{sid}", data={"body": "Fri | 6pm | Game night", "csrf": tok(y, "/bulletins/1")})
    assert ok.status_code == 302
    assert y.post("/bulletins/1/section/1", data={"body": "x", "csrf": tok(y, "/bulletins/1")}).status_code == 403
    assert "Game night" in c.get("/bulletins/1/preview").text or "Awaiting" in c.get("/bulletins/1").text


def test_mass_times_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1/section/4").text
    assert "Time of Mass" in page and "Web address" in page
    c.post("/bulletins/1/section/4", data={"times_count": "2", "t0_title": "Weekend Masses",
           "t0_days": "Saturday, Sunday", "t0_time": "5:00pm", "t0_web": "https://example.org/live",
           "t1_web": "javascript:alert(1)", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Weekend Masses" in html and "5:00pm" in html and 'href="https://example.org/live"' in html
    assert "javascript:" not in html
    assert html.index("Mass &amp; Service Times") < html.index("Welcome Message") if "Welcome Message" in html else True


def test_page_numbers_shown(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "Hello", "csrf": tok(c, "/bulletins/1")})
    page = c.get("/bulletins/1").text
    assert "Printed on" in page and "Page 1" in page and "would start on page" in page
    assert "Printed on page 1" in c.get("/bulletins/1/section/9").text


def test_mass_intentions_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "Intention for" in c.get("/bulletins/1/section/2").text
    c.post("/bulletins/1/section/1", data={"body": "Cover", "csrf": tok(c, "/bulletins/1")})
    c.post("/bulletins/1/section/2", data={"intent_count": "3", "header": "Mass Intentions Week",
           "n0_day": "Monday", "n0_date": "Oct 26", "n0_time": "8:00am", "n0_for_whom": "John Smith", "n0_by_whom": "Mary Smith",
           "n1_day": "Monday", "n1_date": "Oct 26", "n1_time": "6:00pm", "n1_for_whom": "Ann Lee", "n1_by_whom": "Bob Lee",
           "n2_day": "Tuesday", "n2_date": "Oct 27", "n2_time": "8:00am", "n2_for_whom": "Rosa Diaz",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert html.count("Monday, Oct 26") == 1 and "Tuesday, Oct 27" in html
    assert "John Smith" in html and "Bob Lee" in html and "Mass Intentions Week" in html
    assert "Printed on page 2" in c.get("/bulletins/1/section/2").text
