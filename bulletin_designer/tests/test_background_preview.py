import os
import threading
from tests.test_bulletin import setup_admin, create_bulletin, tok, app

def test_bg(app, monkeypatch):
    class T:
        def __init__(s, d, f, a): s.f, s.a = f, a
        def start(s): s.f(*s.a)
        def cancel(s): pass
        daemon = True
    monkeypatch.setattr(threading, "Timer", T)
    c = setup_admin(app); create_bulletin(c)
    os.environ["BULLETIN_BG_PREVIEW"] = "1"
    c.post("/bulletins/1/section/9", data={"body": "Hello bg", "csrf": tok(c, "/bulletins/1")})
    r = c.get("/bulletins/1/download/web?inline=1")
    assert r.data[:4] == b"%PDF" and "X-Build-Seconds" not in r.headers
