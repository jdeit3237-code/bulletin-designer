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
