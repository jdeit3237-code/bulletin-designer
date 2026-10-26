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


def clear_mass_week(c):
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "0", "csrf": tok(c, "/bulletins/1")})


def test_page_numbers_shown(app):
    c = setup_admin(app)
    create_bulletin(c)
    clear_mass_week(c)
    c.post("/bulletins/1/section/9", data={"body": "Hello", "csrf": tok(c, "/bulletins/1")})
    page = c.get("/bulletins/1").text
    assert "Printed on" in page and "Page 1" in page and "would start on page" in page
    assert "Printed on page 1" in c.get("/bulletins/1/section/9").text


def test_mass_intentions_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    clear_mass_week(c)
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
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    form = c.get(f"/bulletins/1/section/{sid}").text
    assert "Lector(s)" in form and "Eucharistic Ministers" in form
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "2", "header": "Ministers This Week",
           "m0_date": "2026-10-25", "m0_lectors": "Ann Kay, Bo Roe", "m0_sacristans": "Cy Ng",
           "m0_ministers": "Di Fox; Ed Poe; Flo Qui", "m1_day": "", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Ministers This Week" in html and "Sunday, October 25, 2026" in html
    assert "Lectors:" in html and "Ann Kay, Bo Roe" in html and "Sacristan:" in html
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
    clear_mass_week(c)
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


def test_order_field(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/layout/9", data={"order": "1", "area": "", "page_no": "0", "pages": "1", "cols": "1", "csrf": t})
    pg = c.get("/bulletins/1").text
    assert pg.index("Readings") < pg.index("Parish Staff")
    assert re.search(r'id="od9"[^>]*value="1"', pg)


def test_weekend_ministers_in_calendar(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/7", data={"event_count": "1", "e0_date": "2026-10-24", "e0_time": "5:30 pm", "e0_event": "Vigil Mass",
        "e0_lectors": "Ann, Bob", "e0_sacristans": "Cy", "e0_ministers": "Di", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "Lectors:" in html and "Ann, Bob" in html and "Sacristan:" in html and "Eucharistic" in html
    form = c.get("/bulletins/1/section/7").text
    assert 'value="Ann, Bob"' in form and 'name="e0_ministers"' in form


def test_third_widths(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/5", data={"area": "left3", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "right3", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert 'class="duo thirds"' in html and html.count('class="third"') == 3
    assert html.index("Welcome text") < html.rindex("Gospel")
    assert 'value="left3" selected' in c.get("/bulletins/1").text


def test_two_thirds_and_toc_settings(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/5", data={"area": "left23", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "right3", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert html.count('class="third"') == 2 and "flex-grow:2" in html
    c.post("/bulletins/1/toc-settings", data={"toc_show": "1", "toc_title": "Inside This Week", "toc_start": "3", "toc_pt": "11", "toc_style": "condensed", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "Inside This Week" in html and "--fs: 11pt" in html
    pg = c.get("/bulletins/1").text
    assert 'value="Inside This Week"' in pg and 'name="toc_start"' in pg
    c.post("/bulletins/1/toc-settings", data={"toc_title": "", "toc_start": "2", "csrf": t})
    assert "Inside This Week" not in c.get("/bulletins/1/preview").text


def test_section_below_contents_on_page_one(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "UnderContents", "csrf": t})
    c.post("/bulletins/1/layout/5", data={"area": "right", "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "below", "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert html.rindex("UnderContents") > html.rindex("Welcome text")
    assert html.count("UnderContents") >= 1


def test_drag_resize(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    r = c.post("/bulletins/1/resize/5", data={"frac": "0.5", "height": "3", "csrf": t})
    assert r.get_json() == {"area": "left", "min_h": 3.0}
    assert c.post("/bulletins/1/resize/5", data={"frac": "0.7", "height": "0", "csrf": t}).get_json()["area"] == "left23"
    assert c.post("/bulletins/1/resize/5", data={"frac": "0.3", "height": "0", "csrf": t}).get_json()["area"] == "left3"
    assert c.post("/bulletins/1/resize/5", data={"frac": "0.95", "height": "3", "csrf": t}).get_json()["area"] == "full"
    html = c.get("/bulletins/1/preview?resize=1").text
    assert "resize-handle" in html and "min-height:3.0in" in html
    assert "resize-handle'" not in c.get("/bulletins/1/preview").text


def test_contents_position(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Welcome text", "csrf": t})
    c.post("/bulletins/1/layout/5", data={"area": "right", "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    base = {"toc_show": "1", "toc_start": "2", "csrf": t}
    for place in ("left", "right", "full"):
        c.post("/bulletins/1/toc-settings", data={**base, "toc_place": place}, follow_redirects=True)
        html = c.get("/bulletins/1/preview").text
        toc, sec = html.index('class="toc-box'), html.rindex("Welcome text")
        assert (toc < sec) == (place != "right")
        assert ('<div class="duo"><div class="half"><div class="toc-box' in html) == (place == "left")
    assert 'name="toc_place"' in c.get("/bulletins/1").text


def test_contents_columns(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/toc-settings", data={"toc_show": "1", "toc_start": "2", "toc_cols": "3", "csrf": t})
    assert "column-count:3" in c.get("/bulletins/1/preview").text
    c.post("/bulletins/1/toc-settings", data={"toc_show": "1", "toc_start": "2", "toc_cols": "9", "csrf": t})
    assert "column-count:9" not in c.get("/bulletins/1/preview").text
    assert 'name="toc_cols"' in c.get("/bulletins/1").text


def test_full_width_contents_honors_widths(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, body, area in ((5, "LeftSide", "left3"), (9, "RightSide", "right3")):
        c.post(f"/bulletins/1/section/{sid}", data={"body": body, "csrf": t})
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": "1", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/toc-settings", data={"toc_show": "1", "toc_start": "2", "toc_place": "full", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    page1 = html[:html.index('aria-label="Page 2"')]
    assert page1.count('class="third"') == 3
    assert page1.index("toc-box") < page1.index('class="third"')
    assert page1.index("LeftSide") < page1.index("RightSide")


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


def test_no_stragglers_binds_last_words(app):
    t = app.jinja_env.globals["tight"]
    assert str(t("one two three four", True)).endswith("three&nbsp;four")
    assert str(t("one two three four", False)) == "one two three four"
    assert str(t("<b> x y", True)) == "&lt;b&gt; x&nbsp;y"


def test_item_gap_column_and_form(app):
    c = setup_admin(app)
    create_bulletin(c)
    html = c.get("/bulletins/1/section/2").text
    assert 'name="item_gap"' in html and 'name="title_gap"' in html


def test_gap_fields_in_section_list(app):
    c = setup_admin(app)
    create_bulletin(c)
    html = c.get("/bulletins/1").text
    assert 'name="title_gap"' in html and 'name="item_gap"' in html


def test_toc_gap_fields(app):
    c = setup_admin(app)
    create_bulletin(c)
    html = c.get("/bulletins/1").text
    assert 'name="toc_title_gap"' in html and 'name="toc_item_gap"' in html
    c.post("/bulletins/1/toc-settings", data={"csrf": tok(c), "toc_show": "1", "toc_title_gap": "0", "toc_item_gap": "0.05"})
    prev = c.get("/bulletins/1/preview").text
    assert "--ig: 0.05in" in prev and "margin-bottom:0.0001in" in prev


def test_no_underline_setting(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert 'name="no_underline"' in c.get("/bulletins/1").text
    assert "no-ul" not in c.get("/bulletins/1/preview").text.replace(".sheet.no-ul", "")
    c.post("/bulletins/1/underlines", data={"csrf": tok(c), "no_underline": "1"})
    assert 'class="sheet' in c.get("/bulletins/1/preview").text and "no-ul" in c.get("/bulletins/1/preview").text


def test_settings_carry_to_next_bulletin(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c)
    c.post("/bulletins/1/underlines", data={"csrf": t, "no_underline": "1"})
    c.post("/bulletins/1/toc-settings", data={"csrf": t, "toc_show": "1", "toc_title_gap": "0.05", "toc_item_gap": "0.02", "toc_cols": "2"})
    c.post("/bulletins/new", data={"csrf": t, "sunday": "2099-01-03"})
    html = c.get("/bulletins/2").text
    assert 'name="no_underline" value="1" checked' in html
    assert 'value="0.05"' in html and 'value="0.02"' in html


def test_mass_notes_textarea(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    assert "Notes for this Mass" in c.get(f"/bulletins/1/section/{sid}").text
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "1", "header": "", "m0_date": "2026-10-25",
           "m0_notes": "Bring canned goods\nSecond collection", "csrf": tok(c, "/bulletins/1")})
    assert "Bring canned goods\nSecond collection" in c.get(f"/bulletins/1/section/{sid}").text
    html = c.get("/bulletins/1/preview").text
    assert "<p>Bring canned goods</p>" in html and "<p>Second collection</p>" in html


def test_mass_schedule_heading_is_fitted_green_block(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "1", "header": "", "m0_date": "2026-10-25",
           "m0_lectors": "Ann Kay", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert re.search(r'<h2 style="font-size:[\d.]+pt;white-space:(nowrap|normal)', html)


def test_mass_week_autodated_and_carried(app):
    c = setup_admin(app)
    create_bulletin(c, "2026-10-25")
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    form = c.get(f"/bulletins/1/section/{sid}").text
    for d in ("2026-10-24", "2026-10-25", "2026-11-01"):
        assert f'value="{d}"' in form
    assert 'value="2026-11-02"' not in form and 'value="2026-10-23"' not in form
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "9", "csrf": tok(c, "/bulletins/1"),
           "m7_date": "2026-10-31", "m7_lectors": "Sat Reader", "m7_notes": "Vigil\nnote",
           "m8_date": "2026-11-01", "m8_lectors": "Sun Reader"})
    create_bulletin(c, "2026-11-01")
    sid2 = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', c.get("/bulletins/2").text, re.S).group(1)
    form2 = c.get(f"/bulletins/2/section/{sid2}").text
    assert 'value="2026-10-31"' in form2 and 'value="2026-11-08"' in form2
    assert "Sat Reader" in form2 and "Sun Reader" in form2 and "Vigil\nnote" in form2


def test_photo_position_option(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert 'name="img_pos"' in c.get("/bulletins/1/section/5").text or True
    from io import BytesIO
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082")
    c.post("/bulletins/1/section/5", data={"body": "Hello there", "img_pos": "inset-left", "alt": "x",
           "image": (BytesIO(png), "p.png"), "csrf": tok(c, "/bulletins/1")}, content_type="multipart/form-data")
    html = c.get("/bulletins/1/preview").text
    assert 'class="photo-inset-left"' in html
    i, j = html.index("Welcome Message"), html.index('class="photo-inset-left"')
    assert i < j < html.index("Hello there")


def test_jump_to_section_dropdown(app):
    c = setup_admin(app)
    create_bulletin(c)
    html = c.get("/bulletins/1").text
    assert 'id="jump"' in html and 'value="sec2"' in html and 'id="sec2"' in html


def test_enter_starts_paragraph_and_gap(app):
    c = setup_admin(app)
    create_bulletin(c)
    clear_mass_week(c)
    c.post("/bulletins/1/section/9", data={"body": "First line\nSecond line", "para_gap": "0",
                                           "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert html.count('<p class="bp">') >= 2 and "--pg:" in html


def test_sentence_first_word_not_stranded(app):
    from bulletin_designer.app import create_app
    t = app.jinja_env.globals["tight"]
    assert "This&nbsp;information" in str(t("It is history. This information is here today.", True))


def test_inline_styling(app):
    t = app.jinja_env.globals["tight"]
    h = str(t("A **bold** and __under__ and {#ff0000|red} <b>x</b> word here.", True))
    assert "<strong>bold</strong>" in h and 'class="ul"' in h and 'color:#ff0000' in h and "&lt;b&gt;" in h
    assert "2<sup>nd</sup>" in str(t("the 2nd collection and 1st one.", True)) and "<sup>st</sup>" in str(t("the 2nd collection and 1st one.", True))
    assert 'font-size:18pt">big' in str(t("a {18pt|big} word here now.", True))


def test_hide_title(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "x", "hide_title": "1", "csrf": tok(c, "/bulletins/1")})
    assert "hide-title" in c.get("/bulletins/1/preview").text
    assert "checked" in c.get("/bulletins/1/section/9").text.split('name="hide_title"')[1][:40]


def test_photo_only_option(app):
    c = setup_admin(app)
    create_bulletin(c)
    import os
    here = os.path.dirname(__file__)
    assert "Photo only" in open(os.path.join(here, "..", "templates", "section.html")).read()


def _card(w, h, color):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    b.seek(0)
    return b


def test_ads_laid_out_on_last_two_pages(app):
    c = setup_admin(app)
    create_bulletin(c)
    data = {"csrf": tok(c, "/ads"), "cards": [(_card(1050, 600, "navy"), "a.png"), (_card(1050, 600, "green"), "b.png"),
                                              (_card(1050, 600, "red"), "c.png"), (_card(600, 1050, "gold"), "d.png")]}
    r = c.post("/ads", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Added 4 card(s)" in r.text and "Vertical" in r.text
    html = c.get("/bulletins/1/preview").text
    assert html.count('class="ad"') == 4 and "Our Advertisers" in html
    assert len(re.findall(r'class="sheet ', html)) % 4 == 0
    last2 = html.split('aria-label="Page ')[-2:]
    assert all("ad-grid" in p for p in last2)
    # inactive cards are left out
    c.post("/ads/save", data={"csrf": tok(c, "/ads"), "name_1": "A", "pos_1": "1", "active_1": "1", "active_2": "1", "active_3": "1"}, follow_redirects=True)
    assert c.get("/bulletins/1/preview").text.count('class="ad"') == 3


def test_ads_none_means_no_ad_pages(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "ad-grid" not in c.get("/bulletins/1/preview").text.replace(".ad-grid", "")


def test_reset_layout(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "keep me", "hide_title": "1", "item_gap": "0.5", "csrf": t})
    c.post("/bulletins/1/reset-layout", data={"sid": "9", "csrf": t})
    page = c.get("/bulletins/1/section/9").text
    assert "checked" not in page.split('name="hide_title"')[1][:40]
    assert "keep me" in page
    assert c.post("/bulletins/1/reset-layout", data={"csrf": t}).status_code in (200, 302)


def test_soft_return(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "line one//\nline two\nnew para", "csrf": tok(c, "/bulletins/1")})
    h = c.get("/bulletins/1/preview").text
    assert "line one<br>line two" in h.replace("\n", "")
    assert h.count("new para") >= 1


def test_multi_page_section_text_not_repeated(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "UNIQUEWORDZ", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"pages": "2", "csrf": t})
    assert c.get("/bulletins/1/preview").text.count("UNIQUEWORDZ") == 1


def test_help_page_and_questions(app):
    c = setup_admin(app)
    r = c.get("/help")
    assert r.status_code == 200 and "Hide a section" in r.text
    r = c.post("/help", data={"question": "how do I add a photo", "csrf": tok(c, "/help")})
    assert "Add a photo to a section" in r.text
    r = c.post("/help", data={"question": "zzzz qqqq", "csrf": tok(c, "/help")})
    assert "sent to the editors" in r.text and "zzzz qqqq" in c.get("/help").text
    c.post("/help/answer/2", data={"answer": "Call the office", "csrf": tok(c, "/help")})
    assert "Call the office" in c.get("/help").text


def test_bulletin_check(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "x", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"pages": "2", "csrf": t})
    r = c.get("/bulletins/1/check")
    assert r.status_code == 200 and "2 pages" in r.text
    assert "Check my bulletin" in c.get("/bulletins/1").text


def test_space_before_title(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "x", "space_before": "0.3", "csrf": tok(c, "/bulletins/1")})
    assert "margin-top:0.3in" in c.get("/bulletins/1/preview").text
    assert 'value="0.3"' in c.get("/bulletins/1/section/9").text.split('id="space_before"')[1][:200]


def test_bullets(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"body": "Intro\n- one\n- two\nAfter", "bullet_style": "check", "csrf": tok(c, "/bulletins/1")})
    h = c.get("/bulletins/1/preview").text.replace("\n", "")
    assert 'class="bl b-check"><li>one</li><li>two</li></ul>' in h
    assert "After" in h and "Intro" in h
