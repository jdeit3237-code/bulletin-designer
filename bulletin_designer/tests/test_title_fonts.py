import os
from tests.test_bulletin import setup_admin, create_bulletin, tok, app  # noqa: F401


def test_fun_title_fonts(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "font_style": "title-pacifico", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "fs-title-pacifico" in html
    css = c.get("/static/style.css").text
    assert 'font-family: "Pacifico"' in css and ".fs-title-pacifico h2" in css
    for f in ("Pacifico-Regular", "KaushanScript-Regular", "Lobster-Regular", "GreatVibes-Regular"):
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "static", "fonts", f + ".ttf"))
    pdf = c.get("/bulletins/1/download/web")
    assert pdf.status_code == 200
    import io
    from pypdf import PdfReader
    names = set()
    for pg in PdfReader(io.BytesIO(pdf.data)).pages:
        for f in (pg["/Resources"].get("/Font") or {}).values():
            names.add(str(f.get_object().get("/BaseFont")))
    assert any("Pacifico" in n for n in names), names


def test_condensed_serif(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "Gospel", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "font_style": "condensed-serif", "csrf": t})
    assert "fs-condensed-serif" in c.get("/bulletins/1/preview").text
    assert c.get("/bulletins/1/download/web").status_code == 200


def test_ordinals_superscript_everywhere(app):
    from app import create_app  # noqa: F401
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "Hi on the 3rd day", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert "3<sup>rd</sup>" in html and "<sup><sup>" not in html
    sup = app.jinja_env.globals["sup_ordinals"]
    assert sup("<p>Fr. Bob 21st &amp; 2nd</p><script>1st</script><sup>th</sup>") == "<p>Fr. Bob 21<sup>st</sup> &amp; 2<sup>nd</sup></p><script>1st</script><sup>th</sup>"
    assert sup("<p>$5th 1st-class</p>") == "<p>$5th 1st-class</p>"


def test_column_wrap_chrome_reflow_hooks(app):
    from io import BytesIO
    import sqlite3
    c = setup_admin(app)
    create_bulletin(c)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082")
    c.post("/bulletins/1/section/5", data={"body": "Hello there", "img_pos": "cols-right", "alt": "x",
           "image": (BytesIO(png), "p.png"), "csrf": tok(c, "/bulletins/1")}, content_type="multipart/form-data")
    db = sqlite3.connect(app.config["DATABASE"])
    db.execute("UPDATE items SET cols=3 WHERE section_id=5")
    db.commit()
    html = c.get("/bulletins/1/preview").text
    assert 'class="cs-wrap"' in html and 'data-n="2"' in html and "function flow(wrap)" in html


def test_ministers_names_stay_together(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    sid = [x for x in range(1, 40) if c.get(f"/bulletins/1/section/{x}").status_code == 200 and "m0_ministers" in c.get(f"/bulletins/1/section/{x}").text]
    assert sid
    c.post(f"/bulletins/1/section/{sid[0]}", data={"mass_count": "1", "m0_date": "2026-10-25", "m0_ministers": "Di Fox; Ed Poe", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert '<span class="nm">Di Fox,</span> <span class="nm">Ed Poe</span>' in html
    assert ".sec .nm" in c.get("/static/style.css").text


def test_vcenter_option_and_space_note(app):
    c = setup_admin(app)
    create_bulletin(c)
    t = tok(c, "/bulletins/1")
    c.post("/bulletins/1/section/9", data={"body": "Hello", "csrf": t})
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "toc_ui": "1", "in_toc": "1", "vcenter": "1", "csrf": t})
    html = c.get("/bulletins/1/preview").text
    assert 'class="pg-body vc"' in html and 'class="vc-in"' in html and "empty space" in html
    assert 'name="vcenter"' in c.get("/bulletins/1").text
    c.post("/bulletins/1/layout/9", data={"area": "full", "page_no": "2", "toc_ui": "1", "in_toc": "1", "csrf": t})
    assert 'class="pg-body vc"' not in c.get("/bulletins/1/preview").text
    assert c.get("/bulletins/1/download/web").status_code == 200
