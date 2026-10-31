import re
from tests.test_bulletin import setup_admin, create_bulletin, tok, app, register_user

def test_bands_alone_keep_their_page(app):
    c = setup_admin(app); create_bulletin(c); t = tok(c, "/bulletins/1")
    for sid in range(9, 17):
        c.post(f"/bulletins/1/section/{sid}", data={"csrf": t, "body": f"S{sid} text", "alt": "x"})
    for sid, area, pg in ((12, "above", 5), (13, "below", 5), (10, "full", 6), (11, "full", 7)):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": str(pg), "pages": "1", "cols": "1", "csrf": t})
    sheets = re.split(r'<section[^>]*class="sheet', c.get("/bulletins/1/preview").text)[1:]
    on = lambda n: re.findall(r'S\d+ text', sheets[n - 1])
    assert on(5) == ["S12 text", "S13 text"]
    assert on(6) == ["S10 text"] and on(7) == ["S11 text"]
    assert "could not be used" not in c.get("/bulletins/1").text


def test_exact_user_case(app):
    c = setup_admin(app); create_bulletin(c); t = tok(c, "/bulletins/1")
    for sid in range(9, 18):
        c.post(f"/bulletins/1/section/{sid}", data={"csrf": t, "body": f"S{sid} text", "alt": "x"})
    for sid, area, pg in ((14, "above", 7), (15, "below", 7), (16, "full", 8), (17, "full", 9)):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": str(pg), "pages": "1", "cols": "3" if sid == 16 else "1", "csrf": t})
    sheets = re.split(r'<section[^>]*class="sheet', c.get("/bulletins/1/preview").text)[1:]
    on = [re.findall(r'S\d+ text', x) for x in sheets]
    assert on[6] == ["S14 text", "S15 text"] and 'data-sid="16"' in sheets[7] and on[8] == ["S17 text"]
    assert "could not be used" not in c.get("/bulletins/1").text


def test_new_bulletins_have_no_underlines(app):
    c = setup_admin(app); create_bulletin(c)
    c.post("/bulletins/1/underlines", data={"csrf": tok(c, "/bulletins/1")})
    create_bulletin(c, "2026-11-01")
    assert 'no-ul' in c.get("/bulletins/2/preview").text


def test_copy_next_custom_section(app):
    c = setup_admin(app); create_bulletin(c); t = tok(c, "/bulletins/1")
    r = c.post("/bulletins/1/add-section", data={"title": "Most needed items", "csrf": t})
    sid = int(r.headers["Location"].rsplit("/", 1)[1])
    c.post(f"/bulletins/1/section/{sid}", data={"csrf": t, "body": "Rice and beans UNIQ", "alt": "x"})
    c.post(f"/bulletins/1/section/{sid}/copy-next", data={"csrf": t})
    c.post(f"/bulletins/1/section/{sid}/copy-next", data={"csrf": t})
    h = c.get("/bulletins/2/preview").text
    assert h.count("UNIQ") == 1 and "Most needed items" in c.get("/bulletins/2").text


def test_full_image_plus_band_image_both_in_pdf(app):
    """A full-page photo with a photo 'under' it must show both in the server PDF (it used to drop the second one)."""
    import io
    import pypdfium2
    from tests.test_bulletin import _card
    c = setup_admin(app); create_bulletin(c); t = tok(c, "/bulletins/1")
    for sid, col, w, h in ((9, (255, 0, 0), 1400, 900), (10, (0, 0, 255), 1000, 1000)):
        c.post(f"/bulletins/1/section/{sid}", data={"csrf": t, "body": "", "alt": "pic", "img_pos": "only", "hide_title": "1",
               "image": (_card(w, h, col), "a.png")}, content_type="multipart/form-data")
    for sid, area in ((9, "full"), (10, "below")):
        c.post(f"/bulletins/1/layout/{sid}", data={"area": area, "page_no": "2", "pages": "1", "cols": "1", "csrf": t, "hide_title": "1"})
    pdf = pypdfium2.PdfDocument(c.get("/bulletins/1/download/web").data)
    img = pdf[1].render(scale=0.5).to_pil().convert("RGB")
    px = list(img.getdata())
    assert sum(1 for p in px if p[0] > 200 and p[1] < 60 and p[2] < 60) > 2000
    assert sum(1 for p in px if p[2] > 200 and p[0] < 60 and p[1] < 60) > 2000


def test_slides_guide_page(app):
    c = setup_admin(app)
    r = c.get("/slides-guide")
    assert r.status_code == 200 and "bible.usccb.org/readings/calendar" in r.text
    assert "Slides guide" in c.get("/style-guide").text


def test_mass_slides_package(app):
    import io, zipfile
    c = setup_admin(app); t = tok(c, "/slides")
    c.post("/slides", data={"csrf": t, "kind": "standard", "files": (io.BytesIO(b"x"), "welcome.pptx")}, content_type="multipart/form-data")
    c.post("/slides", data={"csrf": t, "kind": "songs", "date": "2026-11-01", "files": (io.BytesIO(b"y"), "entrance.docx")}, content_type="multipart/form-data")
    z = zipfile.ZipFile(io.BytesIO(c.get("/slides/package?date=2026-11-01").data))
    assert {"00_Master_Prompt.txt", "standard/welcome.pptx", "songs/entrance.docx"} <= set(z.namelist())
    assert c.get("/slides/package?date=bad").status_code == 404


def test_slides_area_for_av_ministry(app):
    c = setup_admin(app); create_bulletin(c)
    y = register_user(app, c, "av@x.org", "contributor")
    assert y.get("/slides").status_code == 403 and y.get("/slides/package?date=2026-11-01").status_code == 403
    assert "Mass slides" not in y.get("/").text
    c.post("/admin/users", data={"action": "role", "uid": "2", "role": "contributor", "areas": ["slides"], "csrf": tok(c, "/admin/users")})
    assert y.get("/slides").status_code == 200 and "Mass slides" in y.get("/").text
    assert y.get("/library").status_code == 403 and y.get("/ads").status_code == 403


def test_slides_package_includes_bulletin_graphics(app):
    import io, zipfile, os, sqlite3
    c = setup_admin(app); create_bulletin(c)
    with open(os.path.join(app.config["UPLOAD_DIR"], "g.png"), "wb") as f:
        f.write(b"\x89PNG fake")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("INSERT INTO photos(id, filename, title) VALUES (901, 'g.png', 'Food Drive')")
    conn.commit(); conn.close()
    page = c.get("/slides?date=2026-11-01")
    assert page.status_code == 200 and 'name="photo" value="901"' in page.text
    z = zipfile.ZipFile(io.BytesIO(c.get("/slides/package?date=2026-11-01&photo=901").data))
    assert any(n.startswith("graphics/Food_Drive_901") for n in z.namelist())
