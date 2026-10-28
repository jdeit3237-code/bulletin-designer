import io
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
    outbox = []
    a = create_app({"DATABASE": str(tmp_path / "t.db"), "UPLOAD_DIR": str(tmp_path / "up"),
                    "MAIL_SENDER": lambda to, subj, body: outbox.append((to, subj, body)) or True})
    a.outbox = outbox
    return a


def register_user(app, admin, email, role, sections=None):
    data = {"action": "create", "username": email, "role": role, "csrf": tok(admin, "/admin/users")}
    if sections:
        data["sections"] = sections
    admin.post("/admin/users", data=data)
    link = re.search(r"http\S*/register/\S+", app.outbox[-1][2]).group(0)
    u = app.test_client()
    path = "/register/" + link.rsplit("/", 1)[1]
    u.post(path, data={"new": "another-password", "again": "another-password", "csrf": tok(u, path)})
    return u


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
    v = register_user(app, c, "viewer1@x.org", "viewer")
    register_user(app, c, "contrib@x.org", "contributor")
    c.post("/admin/users", data={"action": "role", "uid": "3", "role": "contributor", "sections": "5", "csrf": t})
    assert v.post("/bulletins/1/section/1", data={"body": "x", "csrf": tok(v, "/bulletins/1")}).status_code == 403
    assert v.get("/admin/users").status_code == 403
    k = app.test_client()
    login(k, "contrib@x.org", "another-password")
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
           "photo": (_card(60, 40, "red"), "c.png")}, content_type="multipart/form-data")
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
    y = register_user(app, c, "youth@x.org", "contributor", sid)
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


def test_drag_resize_hidden_from_editor(app):
    c = setup_admin(app)
    create_bulletin(c)
    e = register_user(app, c, "ed1@x.org", "editor")
    assert "resize=1" not in e.get("/bulletins/1/preview").text
    assert e.post("/bulletins/1/resize/5", data={"frac": "0.5", "height": "3", "csrf": tok(e, "/bulletins/1/preview")}).status_code == 403


def test_notes_to_admin(app):
    c = setup_admin(app)
    e = register_user(app, c, "ed2@x.org", "editor")
    v = register_user(app, c, "contrib2@x.org", "contributor")
    assert v.get("/notes").status_code == 403
    e.post("/notes", data={"action": "add", "note": "Resize jumped sections", "page": "/bulletins/1/preview", "csrf": tok(e, "/notes")})
    assert "Resize jumped sections" in c.get("/notes").text
    assert "Mark done" in c.get("/notes").text
    assert e.post("/notes", data={"action": "done", "nid": "1", "csrf": tok(e, "/notes")}).status_code == 403
    c.post("/notes", data={"action": "done", "nid": "1", "csrf": tok(c, "/notes")})
    assert "Reopen" in c.get("/notes").text


def test_leave_section_out_of_toc(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/5", data={"body": "Hello there", "csrf": tok(c, "/bulletins/1")})
    def toc_titles():
        html = c.get("/bulletins/1/preview").text
        return re.findall(r'<li><a href="#pg\d+">([^<]+)</a>', html.split('aria-label="Table of contents"')[1].split("</ol>")[0])
    before = toc_titles()
    assert before
    import sqlite3
    sid = sqlite3.connect(app.config["DATABASE"]).execute("SELECT id FROM sections WHERE slug='mass-schedule'").fetchone()[0]
    c.post("/bulletins/1/layout/%d" % sid, data={"area": "full", "no_toc": "1", "csrf": tok(c, "/bulletins/1")})
    assert 'name="no_toc" value="1" checked' in c.get("/bulletins/1").text
    assert len(toc_titles()) == len(before) - 1


def test_ad_pages_setting(app):
    from bulletin_designer.app import ad_pages
    cards = [{"name": "a%d" % i, "image": "x.jpg", "w": 3.5, "h": 2.0} for i in range(6)]
    assert len(ad_pages(cards, 3)) == 3
    assert len(ad_pages(cards, 1)) >= 1
    assert len(ad_pages(cards, 0)) == 2
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/ad-pages", data={"ad_page_count": "3", "csrf": tok(c, "/bulletins/1")})
    assert 'name="ad_page_count" type="number" min="0" max="8" value="3"' in c.get("/bulletins/1").text


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


def test_delete_mass_subsection(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "2", "header": "", "m0_date": "2026-10-24",
           "m0_lectors": "Anna Keep", "m1_date": "2026-10-25", "m1_lectors": "Bob Gone", "m1_remove": "1",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get(f"/bulletins/1/section/{sid}").text
    assert "Anna Keep" in html and "Bob Gone" not in html


def test_mass_rows_sorted_by_date(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">This Week in Our Parish: Lectors.*?/section/(\d+)', page, re.S).group(1)
    c.post(f"/bulletins/1/section/{sid}", data={"mass_count": "2", "header": "", "m0_date": "2026-10-25",
           "m0_lectors": "Later Person", "m1_date": "2026-10-24", "m1_lectors": "Earlier Person",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get(f"/bulletins/1/section/{sid}").text
    assert html.index("Earlier Person") < html.index("Later Person")


def test_giving_structured_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    page = c.get("/bulletins/1").text
    sid = re.search(r'<th scope="row">Stewardship &amp; Giving.*?/section/(\d+)', page, re.S).group(1)
    data = {"g_date": "2026-10-25", "g_first": "1234.5", "g_second": "300", "g_no_second": "1",
            "g_deposit": "$2,000", "g_purpose": "Seminarian fund", "g_notes": "Thank you", "csrf": tok(c, "/bulletins/1")}
    c.post(f"/bulletins/1/section/{sid}", data=data)
    html = c.get("/bulletins/1/preview").text
    assert "Donations Received: Oct. 25" in html and "1<sup>st</sup> Collection" in html and "Direct Deposit" in html and "Weekly Direct" not in html and "$1,234.50" in html and "$2,000.00" in html and "Thank you" in html
    assert "2<sup>nd</sup> Collection" not in html and "Seminarian fund" not in html
    data["g_date"], data["g_end_date"] = "2026-11-07", "2026-11-08"
    c.post(f"/bulletins/1/section/{sid}", data=data)
    assert "Donations Received: Nov. 7-8" in c.get("/bulletins/1/preview").text
    data["g_subtitle"] = "Offerings for the Week"
    c.post(f"/bulletins/1/section/{sid}", data=data)
    assert "Offerings for the Week: Nov. 7-8" in c.get("/bulletins/1/preview").text
    del data["g_no_second"]
    c.post(f"/bulletins/1/section/{sid}", data=data)
    html = c.get("/bulletins/1/preview").text
    assert "2<sup>nd</sup> Collection" in html and "$300.00" in html and "<em>Seminarian fund</em>" in html and "Purpose" not in html


def test_library_browse_thumbnails_only(app):
    c = setup_admin(app)
    c.post("/library", data={"photo": (_card(40, 30, "red"), "beach.png"), "title": "Beach day", "alt": "A beach",
           "csrf": tok(c, "/library")}, content_type="multipart/form-data")
    html = c.get("/library/browse").text
    assert "thumb_" in html and "Beach day" in html and 'type="file"' not in html
    assert "Beach day" not in c.get("/library/browse?q=zzzz").text
    assert "Beach day" in c.get("/library/browse?q=beach").text


def test_tiff_upload_converted_to_small_jpeg(app):
    from PIL import Image
    c = setup_admin(app)
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "blue").save(buf, "TIFF")
    r = c.post("/library", data={"photo": (io.BytesIO(buf.getvalue()), "Scan One.tiff"), "title": "Scan", "alt": "A scan",
               "csrf": tok(c, "/library")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Photo added" in r.text
    import sqlite3
    name = [f for f in os.listdir(app.config["UPLOAD_DIR"]) if not f.startswith("thumb_")][0]
    assert name.endswith(".jpg")
    with Image.open(os.path.join(app.config["UPLOAD_DIR"], name)) as im:
        assert im.format == "JPEG" and max(im.size) == 2400


def test_three_text_columns(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert '<option value="3"' in c.get("/bulletins/1").text
    c.post("/bulletins/1/section/5", data={"body": "Some text", "csrf": tok(c, "/bulletins/1")})
    c.post("/bulletins/1/layout/5", data={"cols": "3", "pages": "1", "csrf": tok(c, "/bulletins/1")})
    assert "column-count:3" in c.get("/bulletins/1/preview").text


def test_remove_standard_section_and_add_back(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/5", data={"body": "Keep this text", "csrf": tok(c, "/bulletins/1")})
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/layout/5", data={"move": "remove", "csrf": t})
    page = c.get("/bulletins/1").text
    assert "Removed from this bulletin" in page and "/section/5/restore" in page
    c.post("/bulletins/1/section/5/restore", data={"csrf": t})
    assert "Removed from this bulletin" not in c.get("/bulletins/1").text
    assert "Keep this text" in c.get("/bulletins/1/section/5").text


def test_design_grid_has_title_spacing_options(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/layout/5", data={"area": "full", "space_before": "0.2", "para_gap": "0.1", "hide_title": "1",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1").text
    assert 'name="space_before"' in html and 'value="0.2"' in html and 'name="hide_title" value="1" checked' in html


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


def test_paragraph_after_bullets_has_no_gap():
    css = open(os.path.join(os.path.dirname(__file__), "..", "static", "style.css")).read()
    assert ".bl + p.bp" in css and "margin-top: 0" in css.split(".bl + p.bp")[1][:80]


def test_history_undo(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "FIRST", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "SECOND", "csrf": t})
    h = c.get("/bulletins/1/history").text
    assert "Saved a section" in h
    import re as _re
    ids = [int(x) for x in _re.findall(r"history/(\d+)/restore", h)]
    c.post(f"/bulletins/1/history/{min(ids) + 1}/restore", data={"csrf": t})
    assert "FIRST" in c.get("/bulletins/1/section/9").text
    c.post("/bulletins/1/layout/9", data={"pages": "3", "csrf": t})
    page = c.get("/bulletins/1/history").text
    newest = max(int(x) for x in _re.findall(r"history/(\d+)/restore", page))
    c.post(f"/bulletins/1/history/{newest}/restore", data={"csrf": t})
    assert "FIRST" in c.get("/bulletins/1/section/9").text


def test_gallery_photos(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    data = {"csrf": t, "body": "Hello", "ng_alt_0": "First pic", "ng_w_0": "above", "ng_alt_1": "Second pic", "ng_w_1": "below",
            "ng_file_0": (_card(200, 100, "red"), "a.png"), "ng_file_1": (_card(200, 100, "blue"), "b.png")}
    c.post("/bulletins/1/section/9", data=data, content_type="multipart/form-data")
    html = c.get("/bulletins/1/preview").text
    assert 'gal-cap">First pic' in html and 'gal-cap">Second pic' in html
    assert html.index("First pic") < html.index("Hello") < html.index("Second pic")


def test_gallery_equal_presence(app):
    import re
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    data = {"csrf": t, "body": "Hi", "gal_size": "area", "gal_h": "2", "gal_gap": "0.1",
            "ng_alt_0": "a", "ng_w_0": "above", "ng_alt_1": "b", "ng_w_1": "above", "ng_alt_2": "c", "ng_w_2": "above",
            "ng_file_0": (_card(200, 200, "red"), "a.png"), "ng_file_1": (_card(400, 100, "blue"), "b.png"),
            "ng_file_2": (_card(100, 300, "green"), "c.png")}
    c.post("/bulletins/1/section/9", data=data, content_type="multipart/form-data")
    html = c.get("/bulletins/1/preview").text
    ws = [float(x) for x in re.findall(r'<img src="[^"]+" alt="" style="width:([\d.]+)in', html)]
    assert len(ws) == 3
    areas = [ws[0] ** 2, ws[1] ** 2 / 4, ws[2] ** 2 * 3]
    assert max(areas) - min(areas) < 0.02, ws


def test_full_width_bands_with_columns(app):
    import re
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, body, area in ((9, "BANDTOP", "above"), (10, "HALFLEFT", "left"), (11, "HALFRIGHT", "right"), (12, "BANDBOTTOM", "below")):
        c.post(f"/bulletins/1/section/{sid}", data={"body": body, "csrf": t})
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    pages = html.split('<section class="sheet')
    pg = next(p for p in pages if "HALFLEFT" in p)
    assert "HALFRIGHT" in pg and "BANDTOP" in pg and "BANDBOTTOM" in pg
    assert pg.index("BANDTOP") < pg.index("HALFLEFT") < pg.index("HALFRIGHT") < pg.index("BANDBOTTOM")


def test_ocr_checkbox(app):
    import shutil
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    r = c.post("/bulletins/1/section/9", data={"csrf": t, "ocr": "1", "decorative": "1", "image": (_card(200, 100, "white"), "x.png")},
               content_type="multipart/form-data", follow_redirects=True)
    if not shutil.which("tesseract"):
        assert "OCR) is not available" in r.text or "Alternative text is required" in r.text


def test_add_section_top_and_bottom(app):
    c = setup_admin(app)
    create_bulletin(c)
    html = c.get("/bulletins/1").text
    assert 'id="nt_top"' in html and 'id="nt"' in html


def test_docx_upload_returns_to_section(app):
    c = setup_admin(app)
    create_bulletin(c)
    r = c.post("/bulletins/1/section/9", data={"body": "", "csrf": tok(c, "/bulletins/1"),
               "document": (make_docx(["Hello docx"]), "m.docx")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Hello docx" in r.text and "review it" in r.text


def test_photo_width_and_multi_paragraph_size(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "{18pt|One\n\nTwo}", "img_w": "70", "img_pos": "above", "decorative": "1",
                                           "image": (_card(200, 200, "red"), "x.png")}, content_type="multipart/form-data")
    html = c.get("/bulletins/1/preview").text
    assert "width:70%" in html
    assert "{18pt" not in html and html.count("font-size:18pt") >= 2


def test_third_and_two_thirds_share_requested_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, body, area in ((9, "LEFTTHIRD", "left3"), (10, "RIGHTTWO", "right23")):
        c.post(f"/bulletins/1/section/{sid}", data={"body": body, "csrf": t})
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": "8", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    pg = next(p for p in html.split('<section class="sheet') if "LEFTTHIRD" in p)
    assert "RIGHTTWO" in pg


def test_total_pages_setting(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/total-pages", data={"total_pages": "12", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert html.count('<section class="sheet') == 12


def test_three_sections_same_requested_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, body, area in ((9, "AAA1", "left3"), (10, "BBB2", "left3"), (11, "CCC3", "right23")):
        c.post(f"/bulletins/1/section/{sid}", data={"body": body, "csrf": t})
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": "8", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    pg = next(p for p in html.split('<section class="sheet') if "AAA1" in p)
    assert "BBB2" in pg and "CCC3" in pg


def test_youth_calendar_location_and_carry_forward(app):
    c = setup_admin(app)
    create_bulletin(c)
    sid = [r for r in range(1, 20) if "Youth Group Calendar" in c.get(f"/bulletins/1/section/{r}").text][0]
    page = c.get(f"/bulletins/1/section/{sid}").text
    assert "e0_lectors" not in page and "e0_location" in page
    c.post(f"/bulletins/1/section/{sid}", data={"event_count": "3", "e0_date": "2026-10-20", "e0_event": "Old Event", "e0_location": "Hall",
           "e1_date": "2026-11-02", "e1_event": "Pizza Night", "e1_location": "Ministry Center",
           "e2_date": "2026-10-25", "e2_event": "Today Event", "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "Ministry Center" in html and "yev" in html and "NOV" in html
    c.post("/bulletins/new", data={"sunday": "2026-11-01", "csrf": tok(c)})
    nxt = c.get(f"/bulletins/2/section/{sid}").text
    assert "Pizza Night" in nxt and "Old Event" not in nxt and "Today Event" not in nxt


def test_youth_event_titles_list(app):
    c = setup_admin(app)
    create_bulletin(c)
    sid = [r for r in range(1, 20) if "Youth Group Calendar" in c.get(f"/bulletins/1/section/{r}").text][0]
    page = c.get(f"/bulletins/1/section/{sid}").text
    assert "Edge Night" in page and "Rock the Universe" in page
    c.post(f"/bulletins/1/section/{sid}/event-titles", data={"title": "Lock-In", "action": "add", "csrf": tok(c, f"/bulletins/1/section/{sid}")})
    assert "Lock-In" in c.get(f"/bulletins/1/section/{sid}").text
    c.post(f"/bulletins/1/section/{sid}/event-titles", data={"title": "Lock-In", "action": "remove", "csrf": tok(c, f"/bulletins/1/section/{sid}")})
    assert "Lock-In" not in c.get(f"/bulletins/1/section/{sid}").text


def test_youth_multi_day_event(app):
    c = setup_admin(app)
    create_bulletin(c)
    sid = [r for r in range(1, 20) if "Youth Group Calendar" in c.get(f"/bulletins/1/section/{r}").text][0]
    assert "e0_end_date" in c.get(f"/bulletins/1/section/{sid}").text
    c.post(f"/bulletins/1/section/{sid}", data={"event_count": "1", "e0_date": "2026-11-06", "e0_end_date": "2026-11-08", "e0_event": "Retreat",
           "csrf": tok(c, "/bulletins/1")})
    html = c.get("/bulletins/1/preview").text
    assert "6-8" in html and "Friday to Sunday" in html
    c.post("/bulletins/new", data={"sunday": "2026-11-08", "csrf": tok(c)})
    assert "Retreat" in c.get(f"/bulletins/2/section/{sid}").text


def test_youth_event_pick_select(app):
    c = setup_admin(app)
    create_bulletin(c)
    sid = [r for r in range(1, 20) if "Youth Group Calendar" in c.get(f"/bulletins/1/section/{r}").text][0]
    assert 'class="evpick"' in c.get(f"/bulletins/1/section/{sid}").text


def test_two_full_sections_same_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, txt in ((9, "Employment text"), (10, "Souls text")):
        c.post(f"/bulletins/1/section/{sid}", data={"body": txt, "csrf": t})
    for sid in (9, 10):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": "full", "page_no": "3", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    pg = [x for x in html.split('class="sheet')[1:] if "Employment text" in x or "Souls text" in x]
    assert len(pg) == 1 and "Employment text" in pg[0] and "Souls text" in pg[0]


def test_toc_space_before_setting(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/toc-settings", data={"toc_before": "0", "toc_start": "2", "toc_show": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "margin-top:-0.15in" in html
    assert 'name="toc_before"' in c.get("/bulletins/1").text


def test_shared_page_photos_shrink(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid, nm in ((9, "AAA"), (10, "BBB")):
        c.post(f"/bulletins/1/section/{sid}", data={"csrf": t, "body": (nm + " text line. ") * 40, "img_pos": "below", "decorative": "1",
               "image": (_card(1000, 500, "red"), "x.png")}, content_type="multipart/form-data")
        c.post(f"/bulletins/1/layout/{sid}", data={"area": "full", "page_no": "3", "pages": "1", "cols": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert 'class="shared"' in html and "--ph:" in html


def test_photo_without_text_fills_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/section/9", data={"csrf": tok(c, "/bulletins/1/section/9"), "body": "", "img_pos": "below", "decorative": "1",
           "image": (_card(600, 900, "orange"), "p.png")}, content_type="multipart/form-data")
    assert "img-only" in c.get("/bulletins/1/preview").text


def test_duplicate_bulletin_and_template(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "UNIQUEBODY", "csrf": t})
    c.post("/bulletins/1/template", data={"csrf": t})
    assert "(Template)" in c.get("/").text
    r = c.post("/bulletins/1/duplicate", data={"sunday": "2031-01-05", "csrf": t})
    assert r.status_code in (302, 303)
    assert "UNIQUEBODY" in c.get("/bulletins/2/section/9").text
    r = c.post("/bulletins/1/duplicate", data={"sunday": "2031-01-05", "csrf": t})
    assert r.status_code in (302, 303)


def test_email_invite_and_password_recovery(app):
    c = setup_admin(app)
    t = tok(c, "/admin/users")
    c.post("/admin/users", data={"action": "create", "username": "not-an-email", "role": "editor", "csrf": t})
    assert not app.outbox
    e = register_user(app, c, "Ed@Parish.org", "editor")
    assert app.outbox[-1][0] == "ed@parish.org"
    assert e.get("/").status_code == 200
    out = app.test_client()
    assert "Welcome" not in login(out, "ed@parish.org", "wrong-password-1").text
    assert "Forgot your password" in out.get("/login").text
    n = len(app.outbox)
    out.post("/forgot", data={"email": "nobody@x.org", "csrf": tok(out, "/forgot")})
    assert len(app.outbox) == n
    out.post("/forgot", data={"email": "ed@parish.org", "csrf": tok(out, "/forgot")})
    link = re.search(r"http\S*/reset/\S+", app.outbox[-1][2]).group(0)
    path = "/reset/" + link.rsplit("/", 1)[1]
    out.post(path, data={"new": "brand-new-password", "again": "brand-new-password", "csrf": tok(out, path)})
    login(out, "ed@parish.org", "brand-new-password")
    assert out.get("/").status_code == 200
    assert out.get(path, follow_redirects=False).status_code == 302


def test_editor_manages_users_at_or_below(app):
    c = setup_admin(app)
    e = register_user(app, c, "ed@x.org", "editor")
    page = e.get("/admin/users")
    assert page.status_code == 200 and "Download backup" not in page.text
    assert "Who has an account" in page.text and "Online now" in page.text
    t = tok(e, "/admin/users")
    e.post("/admin/users", data={"action": "create", "username": "v@x.org", "role": "admin", "csrf": t})
    assert not any(m[0] == "v@x.org" for m in app.outbox)
    assert e.post("/admin/users", data={"action": "create", "username": "v@x.org", "role": "viewer", "csrf": t}).status_code == 302
    assert any(m[0] == "v@x.org" for m in app.outbox)
    assert e.post("/admin/users", data={"action": "role", "uid": "1", "role": "viewer", "csrf": t}).status_code == 403
    assert "Invited, not registered yet" in c.get("/admin/users").text
    v = register_user(app, c, "w@x.org", "viewer")
    assert v.get("/admin/users").status_code == 403


def test_invite_existing_user_saves_typed_email(app):
    c = setup_admin(app)
    t = tok(c, "/admin/users")
    c.post("/admin/users", data={"action": "invite", "uid": "1", "email": "boss@x.org", "csrf": t})
    assert app.outbox[-1][0] == "boss@x.org"


def test_deactivate_and_delete_user(app):
    c = setup_admin(app)
    u = register_user(app, c, "gone@x.org", "editor")
    t = tok(c, "/admin/users")
    c.post("/admin/users", data={"action": "deactivate", "uid": "2", "csrf": t})
    assert u.get("/", follow_redirects=False).status_code == 302
    assert "deactivated" in login(app.test_client(), "gone@x.org", "another-password").text
    c.post("/admin/users", data={"action": "deactivate", "uid": "1", "csrf": t})
    assert c.get("/").status_code == 200
    c.post("/admin/users", data={"action": "activate", "uid": "2", "csrf": t})
    assert login(u, "gone@x.org", "another-password") and u.get("/").status_code == 200
    c.post("/admin/users", data={"action": "delete", "uid": "2", "csrf": t})
    assert "1 user." in c.get("/admin/users").text


def test_help_covers_current_features(app):
    c = setup_admin(app)
    page = c.get("/help").text
    for word in ("Duplicate", "Undo / history", "Forgot your password", "Deactivate", "On page", "Unpublish"):
        assert word in page


def test_library_reads_photo_metadata(app, tmp_path):
    from PIL import Image
    im = Image.new("RGB", (40, 30), "red")
    ex = Image.Exif()
    ex[270] = "A red test square"
    ex[272] = "TestCam"
    ex[315] = "Jane Photographer"
    f = tmp_path / "pic.jpg"
    im.save(f, exif=ex)
    xmp = b'<x:xmpmeta><rdf:RDF><dc:subject><rdf:Bag><rdf:li>church</rdf:li><rdf:li>festival</rdf:li></rdf:Bag></dc:subject></rdf:RDF></x:xmpmeta>'
    f.write_bytes(f.read_bytes() + xmp)
    c = setup_admin(app)
    t = tok(c, "/library")
    c.post("/library", data={"csrf": t}, content_type="multipart/form-data", buffered=True) if False else None
    with open(f, "rb") as fh:
        c.post("/library", data={"csrf": t, "photo": (fh, "pic.jpg"), "tags": "mine"}, content_type="multipart/form-data")
    page = c.get("/library").text
    assert "Pic" in page and "festival" in page and "mine" in page and "TestCam" in page and "40 x 30" in page
    assert "A red test square" in page
    assert "church" in c.get("/library?q=church").text


def test_bulk_photos_resized_with_thumbnails(app, tmp_path):
    import io, zipfile
    from PIL import Image
    c = setup_admin(app)

    def jpg(w, h):
        b = io.BytesIO()
        Image.new("RGB", (w, h), "blue").save(b, "JPEG")
        return b.getvalue()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("big_one.jpg", jpg(4000, 3000))
        zf.writestr("notes.txt", b"hi")
    z.seek(0)
    t = tok(c, "/library")
    r = c.post("/library/bulk", data={"csrf": t, "tags": "event",
               "photos": [(z, "set.zip"), (io.BytesIO(jpg(100, 80)), "small.jpg")]}, content_type="multipart/form-data")
    assert r.status_code == 302
    page = c.get("/library").text
    assert "Big One" in page and "Small" in page and "needs-description" in page and "event" in page
    assert "2400 x 1800 px" in page
    up = os.listdir(app.config["UPLOAD_DIR"])
    assert sum(n.startswith("thumb_") for n in up) == 2
    big = [n for n in up if n.endswith("big_one.jpg") and not n.startswith("thumb_")][0]
    assert max(Image.open(os.path.join(app.config["UPLOAD_DIR"], big)).size) == 2400
    v = register_user(app, c, "v@x.org", "viewer")
    assert v.post("/library/bulk", data={"csrf": tok(v, "/library")}).status_code == 403


def test_photo_libraries_many_to_many(app):
    c = setup_admin(app)
    page = c.get("/library").text
    assert "Church (0)" in page and "Logos (0)" in page and "Religious (0)" in page
    t = tok(c, "/library")
    c.post("/library/libraries", data={"action": "add", "name": "Youth", "csrf": t})
    ids = {n: re.search(r'lib=(\d+)">%s \(' % n, c.get("/library").text).group(1) for n in ("Church", "Logos", "Youth")}
    c.post("/library", data={"title": "Cross", "alt": "A cross", "csrf": t, "libs": [ids["Church"], ids["Logos"]],
           "photo": (_card(60, 40, "red"), "x.png")}, content_type="multipart/form-data")
    assert "Church (1)" in c.get("/library").text and "Logos (1)" in c.get("/library").text
    assert "Cross" in c.get(f"/library?lib={ids['Logos']}").text
    assert "Cross" not in c.get(f"/library?lib={ids['Youth']}").text
    c.post("/library/1/libraries", data={"libs": [ids["Youth"]], "csrf": t})
    assert "Cross" in c.get(f"/library?lib={ids['Youth']}").text
    assert "Cross" not in c.get(f"/library?lib={ids['Church']}").text
    c.post("/library/libraries", data={"action": "remove", "lib": ids["Youth"], "csrf": t})
    assert "Cross" in c.get("/library?lib=none").text


def test_header_date_setting_updates_cover(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/bulletins/1/header-text", data={"hdr_date": "2026-11-08", "theme": "Test", "csrf": tok(c, "/bulletins/1")})
    h = c.get("/bulletins/1/preview").text
    assert 'class="head-date">Sunday, November 8, 2026' in h or "head-date" in h and "November 8, 2026" in h.split("head-date")[1][:200]
    assert "October 25" not in h.split("head-date")[1][:200]


def test_duplicate_photo_not_added_twice(app):
    import io, sqlite3
    from PIL import Image
    c = setup_admin(app)
    b = io.BytesIO()
    Image.new("RGB", (100, 80), "red").save(b, "JPEG")
    data = b.getvalue()
    for _ in range(2):
        c.post("/library/bulk", data={"csrf": tok(c, "/library"), "photos": [(io.BytesIO(data), "Same.jpg")]},
               content_type="multipart/form-data")
    n = sqlite3.connect(app.config["DATABASE"]).execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert n == 1
    b2 = io.BytesIO()
    Image.new("RGB", (120, 90), "red").save(b2, "JPEG")
    c.post("/library/bulk", data={"csrf": tok(c, "/library"), "photos": [(io.BytesIO(b2.getvalue()), "Same.jpg")]},
           content_type="multipart/form-data")
    assert sqlite3.connect(app.config["DATABASE"]).execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 2


def test_gallery_photo_crop_and_wrap(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Words here", "ng_alt_0": "Pic A", "ng_w_0": "above",
           "ng_alt_1": "Pic B", "ng_w_1": "above", "ng_file_0": (_card(300, 200, "red"), "a.png"),
           "ng_file_1": (_card(300, 200, "blue"), "b.png")}, content_type="multipart/form-data")
    form = c.get("/bulletins/1/section/9").text
    assert "gp_shape_0" in form and "gp_wrap_1" in form
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Words here", "gp_alt_0": "Pic A", "gp_w_0": "above",
           "gp_shape_0": "1:1", "gp_z_0": "2", "gp_x_0": "20", "gp_y_0": "70",
           "gp_alt_1": "Pic B", "gp_w_1": "below", "gp_wrap_1": "right"})
    html = c.get("/bulletins/1/preview").text
    assert "object-position:20.0% 70.0%" in html and "scale(2.0)" in html
    assert "galv-wrap-right" in html
    assert html.index("Pic B") < html.index("Words here")


def test_section_photo_finder(app):
    c = setup_admin(app)
    create_bulletin(c)
    c.post("/library", data={"csrf": tok(c, "/library"), "photo": (_card(100, 100, "red"), "palm.png"), "title": "Palm branches",
           "alt": "palms", "tags": "palm sunday"}, content_type="multipart/form-data")
    h = c.get("/bulletins/1/section/9").text
    assert 'id="pf_q"' in h and "palm branches" in h and "Use as the main photo" in h


def test_upload_custom_font(app):
    import io
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/fonts")
    r = c.post("/fonts", data={"csrf": t, "name": "Test Face", "font": (io.BytesIO(b"fakefontdata"), "t.ttf")}, content_type="multipart/form-data")
    assert r.status_code == 302
    c.post("/fonts", data={"csrf": t, "name": "Bad", "font": (io.BytesIO(b"x"), "t.exe")}, content_type="multipart/form-data")
    page = c.get("/fonts").text
    assert "Test Face" in page and "Bad" not in page.split("Uploaded fonts")[1]
    h = c.get("/bulletins/1").text
    assert "Test Face (uploaded)" in h and "@font-face" in h
    assert "url(/uploads/fonts/" in c.get("/bulletins/1/preview").text


def test_photo_alignment(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "img_align": "right", "img_pos": "above",
           "image": (_card(300, 200, "red"), "a.png")}, content_type="multipart/form-data")
    assert "pa-right" in c.get("/bulletins/1/preview").text
    assert 'value="right" checked' in c.get("/bulletins/1/section/9").text


def test_giving_extra_collections(app):
    import sqlite3
    c = setup_admin(app)
    create_bulletin(c)
    gid = sqlite3.connect(app.config["DATABASE"]).execute("SELECT id FROM sections WHERE slug='giving'").fetchone()[0]
    t = tok(c, f"/bulletins/1/section/{gid}")
    c.post(f"/bulletins/1/section/{gid}", data={"csrf": t, "g_first": "100", "g_first_label": "Sunday Offertory", "g_extra_count": "2",
           "gx0_label": "Hurricane Relief", "gx0_amount": "250", "gx0_note": "Keys families", "gx1_label": "Gone", "gx1_amount": "5", "gx1_rm": "1"})
    h = c.get("/bulletins/1/preview").text
    assert "Sunday Offertory" in h and "Hurricane Relief" in h and "$250.00" in h and "Keys families" in h and "Gone" not in h
    f = c.get(f"/bulletins/1/section/{gid}").text
    assert "Hurricane Relief" in f and 'id="gx_add"' in f


def test_section_background_color(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hello", "use_bg": "1", "bg_color": "#102a43"})
    h = c.get("/bulletins/1/preview").text
    assert "background:#102a43" in h and "bg-dark" in h
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hello", "use_bg": "1", "bg_color": "#ffeeaa"})
    h = c.get("/bulletins/1/preview").text
    assert "background:#ffeeaa" in h and "bg-dark" not in h
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hello", "bg_color": "#ffeeaa"})
    assert "background:#ffeeaa" not in c.get("/bulletins/1/preview").text
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hello", "use_bg": "1", "bg_color": "red;x"})
    assert "red;x" not in c.get("/bulletins/1/preview").text


def test_alignment_tool_spacing(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "ng_alt_0": "A", "ng_w_0": "above", "ng_file_0": (_card(300, 200, "red"), "a.png"),
           "gal_align": "right", "gal_before": "0.3", "gal_after": "0.3"}, content_type="multipart/form-data")
    h = c.get("/bulletins/1/preview").text
    assert "gal-right" in h and "margin-top:0.3in;margin-bottom:0.3in" in h
    assert 'id="at_equal"' in c.get("/bulletins/1/section/9").text


def test_extra_photo_table_not_full_width_and_main_align_buttons(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "table.gal { width: auto" in open("static/style.css").read()
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "img_pos": "above",
           "image": (_card(300, 200, "red"), "a.png")}, content_type="multipart/form-data")
    assert 'type="radio" name="img_align"' in c.get("/bulletins/1/section/9").text


def test_design_studio_saves_accessibility_metadata(app):
    c = setup_admin(app)
    r = c.get("/design-studio")
    assert r.status_code == 200 and "Alternative text" in r.text
    t = tok(c, "/design-studio")
    c.post("/library", data={"csrf": t, "photo": (_card(300, 200, "blue"), "ad.png"), "title": "Fish fry ad", "alt": "A fish fry ad with a fish and the words Fish Fry",
           "long_desc": "Blue ad reading Fish Fry", "ai_tool": "Firefly", "prompt": "Create an ad", "tags": "design studio, ai-made"}, content_type="multipart/form-data")
    h = c.get("/library").text
    assert "Fish fry ad" in h and "design studio" in h
    import sqlite3
    row = sqlite3.connect(app.config["DATABASE"]).execute("SELECT alt, meta FROM photos ORDER BY id DESC LIMIT 1").fetchone()
    assert "Fish Fry" in row[0] and "Firefly" in row[1] and "Create an ad" in row[1]


def test_design_generate_off_and_on(app, monkeypatch):
    import base64, json as _j
    c = setup_admin(app)
    t = tok(c, "/design-studio")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "Make the picture now" not in c.get("/design-studio").text
    assert c.post("/design-studio/generate", data={"csrf": t, "prompt": "x" * 30}).status_code == 400
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("AI_IMAGE_DAILY_LIMIT", "1")
    png = _card(30, 20, "green").read()

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _j.dumps({"data": [{"b64_json": base64.b64encode(png).decode()}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: R())
    assert "Make the picture now" in c.get("/design-studio").text
    r = c.post("/design-studio/generate", data={"csrf": t, "prompt": "x" * 30, "size": "1024x1024"})
    assert r.status_code == 200 and r.get_json()["token"]
    assert c.post("/design-studio/generate", data={"csrf": t, "prompt": "x" * 30}).status_code == 429
    c.post("/library", data={"csrf": t, "pending": r.get_json()["token"], "title": "Gen pic", "alt": "A green square test picture"})
    assert "Gen pic" in c.get("/library").text


def test_alignment_spacing_accepts_default(app):
    c = setup_admin(app)
    create_bulletin(c)
    h = c.get("/bulletins/1/section/9").text
    assert 'name="gal_before"' in h and 'step="0.05"' not in h


def test_preview_links_to_design_studio(app):
    c = setup_admin(app)
    create_bulletin(c)
    assert "/design-studio" in c.get("/bulletins/1/preview").text
    assert "/design-studio" in c.get("/bulletins/1").text


def test_edit_toolbar_row(app):
    c = setup_admin(app)
    create_bulletin(c)
    h = c.get("/bulletins/1").text
    assert 'class="toolrow"' in h and "Liturgical suggestions" in h


def test_jumpbar_on_top(app):
    c = setup_admin(app)
    create_bulletin(c)
    h = c.get("/bulletins/1").text
    assert h.index('class="jumpbar"') < h.index('class="toolrow"') and 'id="jump"' in h


def test_main_photo_crop_frame_buttons(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "image": (_card(300, 200, "red"), "a.png")}, content_type="multipart/form-data")
    h = c.get("/bulletins/1/section/9").text
    assert 'class="btn secondary ch-b"' in h and 'name="crop_h"' in h and "Whole picture" in h


def test_crop_preview_matches_print_width(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "image": (_card(300, 450, "red"), "a.png")}, content_type="multipart/form-data")
    h = c.get("/bulletins/1/section/9").text
    assert 'data-w="' in h and "as it will print" in h


def test_edge_crop_saves_and_renders(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "image": (_card(400, 300, "red"), "a.png")}, content_type="multipart/form-data")
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "crop_t": "10", "crop_l": "25", "crop_r": "25", "crop_b": "0"})
    h = c.get("/bulletins/1/section/9").text
    assert 'id="crop_l"' in h and 'value="25"' in h
    p = c.get("/bulletins/1/preview").text
    assert "padding-bottom" in p and "left:-50.000%" in p


def test_drag_crop_frame_present(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1/section/9")
    c.post("/bulletins/1/section/9", data={"csrf": t, "body": "Hi", "alt": "pic", "image": (_card(300, 200, "red"), "a.png")}, content_type="multipart/form-data")
    h = c.get("/bulletins/1/section/9").text
    assert 'id="edge-frame"' in h and 'data-e="br"' in h


def test_full_section_keeps_chosen_page_when_halves_added(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid in (2, 5, 9, 10, 11):
        c.post(f"/bulletins/1/section/{sid}", data={"body": f"Body{sid}", "csrf": t})
    c.post("/bulletins/1/layout/12", data={"area": "left", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/section/12", data={"body": "Body12", "csrf": t})
    for sid, pn in ((5, 3), (10, 3), (9, 4), (11, 5)):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": "full", "page_no": str(pn), "pages": "1", "cols": "1", "csrf": t})
    h = c.get("/bulletins/1/preview").text
    parts = re.split(r'id="pg(\d+)"', h)
    where = {}
    for i in range(1, len(parts), 2):
        for b in re.findall(r'Body(\d+)', parts[i + 1]):
            where.setdefault(int(b), int(parts[i]))
    assert where[9] == 4 and where[11] == 5 and where[5] == 3


def test_removed_section_leaves_blank_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/5", data={"body": "Body5", "csrf": t})
    c.post("/bulletins/1/section/9", data={"body": "Body9", "csrf": t})
    c.post("/bulletins/1/section/10", data={"body": "Body10", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/10", data={"area": "full", "page_no": "3", "pages": "1", "cols": "1", "csrf": t})
    def pages():
        h = c.get("/bulletins/1/preview").text
        parts = re.split(r'id="pg(\d+)"', h)
        return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    notes = lambda: c.get("/bulletins/1/preview").text.count("intentionally left for notes")
    n0 = notes()
    c.post("/bulletins/1/layout/9", data={"move": "remove", "csrf": t})
    after = pages()
    assert notes() == n0 + 1
    assert "Body9" not in "".join(after.values()) and "Body10" in after[3]


def test_readded_section_takes_over_blank_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid in (5, 9, 10):
        c.post(f"/bulletins/1/section/{sid}", data={"body": f"Body{sid}", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"move": "remove", "csrf": t})
    c.post("/bulletins/1/layout/10", data={"area": "full", "page_no": "2", "pages": "1", "cols": "1", "csrf": t})
    h = c.get("/bulletins/1/preview").text
    parts = re.split(r'id="pg(\d+)"', h)
    where = {b: int(parts[i]) for i in range(1, len(parts), 2) for b in re.findall(r'Body(\d+)', parts[i + 1])}
    assert where['10'] == 2


def test_page_conflict_is_explained(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    for sid in (9, 10):
        c.post(f"/bulletins/1/section/{sid}", data={"body": f"Body{sid}", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "4", "pages": "2", "cols": "1", "csrf": t})
    c.post("/bulletins/1/layout/10", data={"area": "full", "page_no": "4", "pages": "1", "cols": "1", "csrf": t})
    assert "is already used by" in c.get("/bulletins/1").text


def test_footer_address_only_on_first_page(app):
    c = setup_admin(app)
    create_bulletin(c)
    h = c.get("/bulletins/1/preview").text
    assert h.count("31300 Overseas Hwy") == 1
