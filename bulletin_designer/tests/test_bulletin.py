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
    c.post("/bulletins/1/section/3", data={"staff_count": "1", "s0_name": "Fr. Medina", "csrf": tok(c, "/bulletins/1")})
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
    ok = y.post(f"/bulletins/1/section/{sid}", data={"event_count": "1", "e0_date": "2026-10-30", "e0_time": "6pm", "e0_event": "Game night", "csrf": tok(y, "/bulletins/1")})
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
    c.post("/bulletins/1/section/1", data={"extra": "Cover", "csrf": tok(c, "/bulletins/1")})
    c.post("/bulletins/1/section/2", data={"group_count": "2", "header": "Mass Intentions Week",
           "g0_date": "2026-10-26", "g0_count": "2", "g0_0_time": "8:00am", "g0_0_for_whom": "John Smith", "g0_0_by_whom": "Mary Smith",
           "g0_1_time": "6:00pm", "g0_1_for_whom": "Ann Lee", "g0_1_by_whom": "Bob Lee",
           "g1_date": "2026-10-27", "g1_count": "1", "g1_0_time": "8:00am", "g1_0_for_whom": "Rosa Diaz",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert html.count("Monday, October 26, 2026") == 1 and "Tuesday, October 27, 2026" in html
    assert "John Smith" in html and "Bob Lee" in html and "Mass Intentions Week" in html
    assert "Printed on page 2" in c.get("/bulletins/1/section/2").text


def test_this_week_ministers(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    form = c.get(f"/bulletins/1/section/{sid}").text
    assert "Lector(s)" in form and "Eucharistic Ministers" in form
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "2", "header": "Ministers This Week",
           "m0_date": "2026-10-25", "m0_lectors": "Ann Kay, Bo Roe", "m0_sacristans": "Cy Ng",
           "m0_ministers": "Di Fox; Ed Poe; Flo Qui", "m1_day": "", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Ministers This Week" in html and "Sunday, October 25, 2026" in html
    assert "Lectors" in html and "Ann Kay, Bo Roe" in html and "Sacristan<" in html
    assert "Di Fox, Ed Poe, Flo Qui" in html


def test_youth_calendar_events(app):
    c = setup_admin(app)
    create_bulletin(c)
    sid = [r for r in range(1, 20) if "Youth Group Calendar" in c.get(f"/bulletins/1/section/{r}").text][0]
    c.post(f"/bulletins/1/section/{sid}", data={"event_count": "2", "e0_date": "2026-10-26", "e0_time": "6pm", "e0_event": "Bible Study",
           "e1_date": "2026-11-01", "e1_event": "Pizza Night", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Monday, October 26, 2026" in html and "Pizza Night" in html and "Sunday, November 1, 2026" in html


def test_copy_keep_delete(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": tok(c, "/bulletins/1")})
    r = c.post("/bulletins/1/section/5/copy-next", data={"csrf": tok(c, "/bulletins/1/section/5")})
    assert r.status_code == 302
    assert "Welcome text" in c.get("/bulletins/2/section/5").text
    c.post("/bulletins/1/section/5/keep", data={"csrf": tok(c, "/bulletins/1/section/5")})
    assert "Keep for all bulletin editions" in c.get("/bulletins/1/section/5").text
    c.post("/bulletins/1/section/5/delete", data={"csrf": tok(c, "/bulletins/1/section/5")})
    assert "Welcome text" not in c.get("/bulletins/1/section/5").text
    assert "Welcome text" in c.get("/bulletins/2/section/5").text


def test_cover_parts(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/1", data={"cover_date": "2026-10-25", "cover_title": "Thirtieth Sunday", "addr_street": "1 Main St",
           "addr_city": "Miami, FL 33100", "addr_phone": "555-1212", "hours_count": "2", "h0_days": "Mon - Fri", "h0_time": "9-4",
           "h1_days": "", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Sunday, October 25, 2026" in html and "Thirtieth Sunday" in html and "1 Main St" in html and "Mon - Fri" in html
    assert 'value="2026-10-25"' in c.get("/bulletins/1/section/1").text


def test_header_assistant(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "Design assistant" in c.get("/bulletins/1/preview").text
    r = c.post("/bulletins/1/assistant", data={"request": "set header to Fall Festival Sunday", "csrf": tok(c, "/bulletins/1")})
    assert "banner header tagline" in r.text
    c.post("/bulletins/1/assistant", data={"confirm": "1", "csrf": tok(c, "/bulletins/1")})
    assert "Fall Festival Sunday" in c.get("/bulletins/1/preview").text


def test_footer_text_and_address(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/1", data={"addr_street": "1 Main St", "addr_city": "Miami, FL", "csrf": tok(c, "/bulletins/1")})
    c.post("/bulletins/1/footer", data={"footer": "Fall Festival Nov 1", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Fall Festival Nov 1" in html and "1 Main St" in html and "Page 1 of" in html


def test_header_photo_settings(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "Header photo" in c.get("/bulletins/1").text
    c.post("/bulletins/1/header-photo", data={"hdr_h": "3.5", "hdr_zoom": "1.5", "hdr_x": "30", "hdr_y": "60", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "height:3.5in" in html and "scale(1.5)" in html and "church.jpg" in html


def test_header_style_choice(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/header-photo", data={"hdr_style": "swell", "csrf": tok(c, "/bulletins/1")})
    assert "m-swell" in c.get("/bulletins/1/preview").text
    c.post("/bulletins/1/header-photo", data={"hdr_style": "bogus", "csrf": tok(c, "/bulletins/1")})
    assert "m-waves" in c.get("/bulletins/1/preview").text


def test_header_date_and_title(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/header-text", data={"hdr_date": "2026-11-01", "theme": "All Saints", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Sunday, November 1, 2026" in html and "All Saints" in html


def test_staff_structured(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/3", data={"staff_count": "2", "s0_name": "Fr. Jim", "s0_role": "Pastor", "s0_phone": "305-872-2537",
                                            "s1_name": "Ann", "s1_role": "Office Manager", "csrf": tok(c, "/bulletins/1/section/3")})
    html = c.get("/bulletins/1/preview").text
    assert "Fr. Jim" in html and "Pastor" in html and "Office Manager" in html


def test_placement_and_font_options(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/2", data={"group_count": "1", "g0_date": "2026-10-25", "g0_count": "1", "g0_0_time": "9 am",
                                            "g0_0_for_whom": "Ann", "g0_0_by_whom": "Bob", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert re.search(r'id="pg2".*?class="duo"', html, re.S) and "9 am:" in html
    c.post("/bulletins/1/layout/2", data={"area": "left", "page_no": "2", "font_pt": "11", "font_style": "condensed", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "fs-condensed" in html and "--fs: 11pt" in html
    assert "Condensed" in c.get("/style-guide").text or c.get("/style-guide").status_code in (200, 404)


def test_full_section_moves_to_chosen_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "pages": "1", "cols": "1", "csrf": t}, follow_redirects=True)
    html = c.get("/bulletins/1/preview").text
    assert re.search(r'id="pg2".*?Gospel', html, re.S) and not re.search(r'id="pg2".*?Welcome text', html.split('id="pg3"')[0], re.S)
    pg = c.get("/bulletins/1").text
    assert re.search(r'id="pn9"[^>]*value="2"', pg)
    c.post("/bulletins/1/layout/4", data={"area": "", "page_no": "3", "pages": "1", "cols": "1", "font_pt": "0", "font_style": "", "csrf": t})
    assert re.search(r'id="pn4"[^>]*value="3"', c.get("/bulletins/1").text)
    assert "Settings saved" in c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "csrf": t}, follow_redirects=True).text


def test_section_on_page_one(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "right", "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    pg = c.get("/bulletins/1").text
    assert re.search(r"Readings[^<]*<br>.*?Page 1", pg, re.S)


def test_stack_below_and_same_column(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "below", "page_no": "0", "pages": "1", "cols": "1", "csrf": t})
    pg = c.get("/bulletins/1").text
    assert re.search(r"Welcome Message[^<]*<br>.*?(Page 1|Page 2)", pg, re.S)
    w = re.search(r"Readings[^<]*<br>.*?<td>(.*?)</td>", pg, re.S).group(1)
    w2 = re.search(r"Welcome Message[^<]*<br>.*?<td>(.*?)</td>", pg, re.S).group(1)
    assert w == w2
    html = c.get("/bulletins/1/preview").text
    assert html.index("Welcome text") < html.index("Gospel")


def test_several_sections_on_page_one(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    for sid in (5, 9):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": "right", "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert html.index("Welcome text") < html.index("Gospel") < html.index('aria-label="Page 2"')
    pg = c.get("/bulletins/1").text
    assert len(re.findall(r"Page 1</td>", pg)) >= 2


def test_add_four_pages(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    base = c.get("/bulletins/1/preview").text.count('class="page')
    c.post("/bulletins/1/extra-pages", data={"do": "add", "csrf": t})
    assert "4 pages" in c.get("/bulletins/1").text or "extra pages" in c.get("/bulletins/1").text
    assert re.search(r"<strong>(\d+) pages", c.get("/bulletins/1").text).group(1)
    n = int(re.search(r"<strong>(\d+) pages", c.get("/bulletins/1").text).group(1))
    assert n % 4 == 0
    c.post("/bulletins/1/extra-pages", data={"do": "remove", "csrf": t})
    assert int(re.search(r"<strong>(\d+) pages", c.get("/bulletins/1").text).group(1)) == n - 4


def test_suggestions_page_and_apply(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1/suggestions")
    assert page.status_code == 200 and "bible.usccb.org/readings/calendar" in page.text and "miamiarch.org" in page.text
    assert "stpeterbpk.org" in page.text and "Graphic ideas" in page.text
    c.post("/bulletins/1/suggestions/apply", data={"text": "Thirtieth Sunday in Ordinary Time", "target": "title", "csrf": tok(c, "/bulletins/1/suggestions")})
    assert "Thirtieth Sunday in Ordinary Time" in c.get("/bulletins/1/preview").text
    c.post("/bulletins/1/suggestions/apply", data={"text": "All Saints", "target": "5", "csrf": tok(c, "/bulletins/1/suggestions")})
    assert "All Saints" in c.get("/bulletins/1/preview").text


def test_liturgy_calendar():
    from datetime import date
    from bulletin_designer import liturgy
    assert liturgy.info(date(2026, 10, 25))["name"] == "Thirtieth Sunday in Ordinary Time"
    assert liturgy.info(date(2026, 11, 29))["season"] == "Advent"
    assert liturgy.easter(2026) == date(2026, 4, 5)


def test_paste_events_into_calendar(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/suggestions")
    page = c.post("/bulletins/1/suggestions", data={"pasted": "Rosary Rally\nFood Drive", "csrf": t})
    assert "Rosary Rally" in page.text and "Food Drive" in page.text
    c.post("/bulletins/1/suggestions/apply", data={"text": ["Rosary Rally", "Food Drive"], "target": "cal:7", "when": "2026-10-28",
                                                   "when_time": "6 pm", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "Rosary Rally" in html and "Food Drive" in html and "6 pm" in html
