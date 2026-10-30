import re
from tests.test_bulletin import setup_admin, create_bulletin, tok, app

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
