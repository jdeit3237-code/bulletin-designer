"""St. Peter the Fisherman weekly bulletin designer (standalone Flask app)."""
import io
import json
import os
import re
import secrets
import sqlite3
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from xml.etree import ElementTree
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash as _gen


def generate_password_hash(pw):
    return _gen(pw, method="pbkdf2:sha256:600000")
from werkzeug.utils import secure_filename

try:
    from . import liturgy, help_content
except ImportError:  # run as a script
    import liturgy
    import help_content

BULLETS = {"disc": "\u2022 Round dot", "circle": "\u25e6 Open circle", "square": "\u25aa Square", "dash": "\u2013 Dash", "check": "\u2713 Check mark", "arrow": "\u27a4 Arrow", "cross": "\u271d Cross", "star": "\u2605 Star"}

ROLES = ("admin", "editor", "contributor", "viewer")
ROLE_HELP = {
    "admin": "Manage users, sections and layout; everything an editor can do.",
    "editor": "Edit every section, approve contributions, publish the bulletin.",
    "contributor": "Edit only assigned sections; changes wait for editor approval.",
    "viewer": "Read-only access to drafts and published bulletins.",
}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

# Sections found in the Oct 11/18/25 2026 bulletins. weekly=1 means new content is
# needed every week; weekly=0 means "standing" content carried forward automatically.
FONT_STYLES = {
    "condensed": "Condensed (fits more text)",
    "serif": "Classic serif",
    "sans": "Clean sans-serif",
    "humanist": "Humanist (easy to read)",
    "rounded": "Friendly rounded",
}

DEFAULT_SECTIONS = [
    ("cover", "Parish Information & Cover", 0, 1, "Sunday date and title, parish address, office hours"),
    ("mass-intentions", "Mass Intentions", 1, 1, "Weekly form: day, date, time, who the Mass is for, who requested it (usually page 2)"),
    ("staff", "Parish Staff", 0, 1, "Standing list; edit only when staff change (carried forward)"),
    ("mass-times", "Mass & Service Times", 0, 1, "Standing list: title, days, name of Mass, time, notes, web address (blank fields are left out)"),
    ("welcome", "Welcome Message", 0, 1, "Standing invitation and welcome"),
    ("youth-calendar", "Youth Group Calendar", 1, 1, "One event per line: Date | Time | Event (assign to the youth group leaders)"),
    ("calendar", "This Week in Our Parish (Calendar of Events)", 1, 1, "One event per line: Date | Time | Event (shown as a table)"),
    ("mass-schedule", "This Week in Our Parish: Lectors, Sacristans & Ministers", 1, 1, "Weekly form: header, then day, date, lectors, sacristans and Eucharistic ministers (several names allowed)"),
    ("readings", "Readings & Gospel", 1, 1, "usccb.org daily readings link or text"),
    ("pastor", "Pastor's Message", 1, 1, "Weekly reflection from the pastor"),
    ("sick", "Pray for Those Who Are Sick", 1, 1, "Prayer list; review weekly"),
    ("announcements", "Parish Announcements", 1, 1, "Events, bake sales, fundraisers"),
    ("food-pantry", "St. Peter Community Food Pantry", 0, 1, "Hours, tours, donation info"),
    ("ministries", "Ministries & Faith Formation", 0, 1, "Schedules, RCIA, youth group"),
    ("sacraments", "Sacraments & Office Information", 0, 1, "Baptism, marriage, reconciliation"),
    ("giving", "Stewardship & Giving", 1, 1, "Offertory totals and online giving"),
    ("sponsors", "Community Sponsors", 0, 1, "Business advertisements"),
]


AD_W, AD_H, AD_GAP_X, AD_GAP_Y = 7.9, 8.0, 0.25, 0.2


def pack_ads(cards, scale):
    """Shelf-pack cards (long edge 3.5in x scale, true aspect). Horizontal cards first, then vertical. Returns pages of rows."""
    def size(c):
        ratio = min(c["w"], c["h"]) / max(c["w"], c["h"]) if c["w"] and c["h"] else 0.57
        long_edge = 3.5 * scale
        return (long_edge, long_edge * ratio) if c["w"] >= c["h"] else (long_edge * ratio, long_edge)
    ordered = [c for c in cards if c["w"] >= c["h"]] + [c for c in cards if c["w"] < c["h"]]
    rows, row, row_w = [], [], 0.0
    for c in ordered:
        w, h = size(c)
        item = {"name": c["name"], "image": c["image"], "w": round(w, 3), "h": round(h, 3)}
        if row and (row_w + AD_GAP_X + w > AD_W + 0.001 or (row[0]["w"] >= row[0]["h"]) != (c["w"] >= c["h"])):
            rows.append(row)
            row, row_w = [], 0.0
        row_w += (AD_GAP_X if row else 0) + w
        row.append(item)
    if row:
        rows.append(row)
    pages, cur, used = [], [], 0.0
    for r in rows:
        h = max(i["h"] for i in r)
        if cur and used + AD_GAP_Y + h > AD_H:
            pages.append(cur)
            cur, used = [], 0.0
        used += (AD_GAP_Y if cur else 0) + h
        cur.append(r)
    if cur:
        pages.append(cur)
    return pages


def ad_pages(cards):
    """Fit all cards on the last two pages (largest uniform scale that works); more pages only if it cannot fit."""
    if not cards:
        return []
    for scale in [round(1.0 - 0.01 * k, 2) for k in range(0, 51)]:
        pages = pack_ads(cards, scale)
        if len(pages) <= 2:
            break
    else:
        pages = pack_ads(cards, 0.5)
    if len(pages) == 1 and len(pages[0]) > 1:
        rows = pages[0]
        mid = (len(rows) + 1) // 2
        pages = [rows[:mid], rows[mid:]]
    elif len(pages) == 1:
        pages = [pages[0], []]
    out = []
    for rows in pages:
        used = sum(max(i["h"] for i in r) for r in rows)
        gap = min(0.6, max(AD_GAP_Y, (AD_H - used) / (len(rows) + 1))) if rows else 0
        out.append({"rows": rows, "gap": round(gap, 3)})
    return out


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(fileobj):
    """Extract paragraph text from a .docx (blank line between paragraphs, tabs kept as ' | ')."""
    with zipfile.ZipFile(fileobj) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > 20 * 1024 * 1024:
            raise ValueError("Document is too large.")
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(W_NS + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W_NS + "t":
                parts.append(node.text or "")
            elif node.tag == W_NS + "tab":
                parts.append(" | ")
            elif node.tag in (W_NS + "br", W_NS + "cr"):
                parts.append("\n")
        paras.append("".join(parts).strip())
    return "\n\n".join(x for x in paras if x)


def doc_text(path):
    """Legacy .doc via antiword or macOS textutil when installed."""
    for cmd in (["antiword", path], ["textutil", "-convert", "txt", "-stdout", path]):
        if shutil.which(cmd[0]):
            out = subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            return out.stdout.decode("utf-8", "replace").strip()
    raise ValueError("Old .doc files cannot be read on this server. Please save the file as .docx and upload again.")


BULLETIN_CARRY = ("footer", "hdr_image", "hdr_alt", "hdr_h", "hdr_zoom", "hdr_x", "hdr_y", "hdr_style",
                  "toc_title", "toc_title_gap", "toc_item_gap", "toc_start", "toc_show", "toc_pt", "toc_style",
                  "toc_place", "toc_cols", "no_stragglers", "no_underline")


def gap_value(raw):
    """Blank = default (stored 0); a typed 0 = no space (stored as a tiny value)."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    try:
        v = max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0001


def gap_display(v):
    if not v:
        return ""
    return "0" if v < 0.001 else ("%g" % v)


def create_app(config=None):
    app = Flask(__name__)
    base = os.path.dirname(os.path.abspath(__file__))
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get("BULLETIN_DB", os.path.join(base, "bulletin.db")),
        UPLOAD_DIR=os.environ.get("BULLETIN_UPLOADS", os.path.join(base, "uploads")),
        MAX_CONTENT_LENGTH=40 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config:
        app.config.update(config)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn:
            conn.close()

    def init_db():
        conn = db()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE,
            display_name TEXT, pw_hash TEXT, role TEXT, must_reset INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sections(id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT,
            weekly INTEGER, pages INTEGER, position INTEGER, hint TEXT, bulletin_id INTEGER);
        CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, filename TEXT, title TEXT, alt TEXT,
            tags TEXT DEFAULT '', uploaded_by TEXT, decorative INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY, name TEXT, image TEXT, w INTEGER, h INTEGER,
            position INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, asked_by TEXT, question TEXT, answer TEXT DEFAULT '',
            answered_by TEXT DEFAULT '', created TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, bulletin_id INTEGER, label TEXT, snapshot TEXT, created TEXT, by TEXT);
        CREATE TABLE IF NOT EXISTS user_sections(user_id INTEGER, section_id INTEGER,
            PRIMARY KEY(user_id, section_id));
        CREATE TABLE IF NOT EXISTS bulletins(id INTEGER PRIMARY KEY, sunday TEXT UNIQUE,
            theme TEXT DEFAULT '', status TEXT DEFAULT 'draft');
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, bulletin_id INTEGER, section_id INTEGER,
            body TEXT DEFAULT '', image TEXT DEFAULT '', alt TEXT DEFAULT '', decorative INTEGER DEFAULT 0,
            caption TEXT DEFAULT '', source TEXT DEFAULT '', status TEXT DEFAULT 'approved',
            carried INTEGER DEFAULT 0, updated_by TEXT DEFAULT '', position REAL DEFAULT 0,
            pages INTEGER DEFAULT 1, cols INTEGER DEFAULT 1, UNIQUE(bulletin_id, section_id));
        """)
        if not conn.execute("SELECT 1 FROM sections").fetchone():
            for i, (slug, title, weekly, pages, hint) in enumerate(DEFAULT_SECTIONS):
                conn.execute("INSERT INTO sections(slug,title,weekly,pages,position,hint) VALUES(?,?,?,?,?,?)",
                             (slug, title, weekly, pages, i, hint))
        for slug, title, weekly, pages, hint in DEFAULT_SECTIONS:
            if not conn.execute("SELECT 1 FROM sections WHERE slug=?", (slug,)).fetchone():
                top = conn.execute("SELECT COALESCE(MAX(position),0)+1 m FROM sections").fetchone()["m"]
                after = {"mass-times": "staff", "mass-intentions": "cover"}.get(slug)
                anchor = conn.execute("SELECT position FROM sections WHERE slug=?", (after,)).fetchone() if after else None
                if anchor:
                    top = anchor["position"] + 1
                    conn.execute("UPDATE sections SET position=position+1 WHERE position>=?", (top,))
                conn.execute("INSERT INTO sections(slug,title,weekly,pages,position,hint) VALUES(?,?,?,?,?,?)",
                             (slug, title, weekly, pages, top, hint))
        if "footer" not in {r[1] for r in conn.execute("PRAGMA table_info(bulletins)")}:
            conn.execute("ALTER TABLE bulletins ADD COLUMN footer TEXT DEFAULT ''")
        bcols = {r[1] for r in conn.execute("PRAGMA table_info(bulletins)")}
        for col, ddl in (("hdr_image", "TEXT DEFAULT ''"), ("hdr_alt", "TEXT DEFAULT ''"), ("hdr_h", "REAL DEFAULT 2.4"),
                         ("hdr_zoom", "REAL DEFAULT 1"), ("hdr_x", "REAL DEFAULT 50"), ("hdr_y", "REAL DEFAULT 50"), ("hdr_style", "TEXT DEFAULT 'waves'"), ("hdr_date", "TEXT DEFAULT ''"), ("extra_pages", "INTEGER DEFAULT 0"), ("toc_title", "TEXT DEFAULT ''"), ("toc_title_gap", "REAL DEFAULT 0"), ("no_underline", "INTEGER DEFAULT 0"), ("toc_item_gap", "REAL DEFAULT 0"), ("toc_start", "INTEGER DEFAULT 2"), ("toc_show", "INTEGER DEFAULT 1"), ("toc_pt", "INTEGER DEFAULT 0"), ("toc_style", "TEXT DEFAULT ''"), ("toc_place", "TEXT DEFAULT 'left'"), ("toc_cols", "INTEGER DEFAULT 1"), ("no_stragglers", "INTEGER DEFAULT 1")):
            if col not in bcols:
                conn.execute(f"ALTER TABLE bulletins ADD COLUMN {col} {ddl}")
        have = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        for col, default in (("crop_zoom", 1), ("crop_x", 50), ("crop_y", 50), ("crop_h", 0), ("min_h", 0)):
            if col not in have:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} REAL DEFAULT {default}")
        if "img_pos" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN img_pos TEXT DEFAULT ''")
        if "gallery" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN gallery TEXT DEFAULT ''")
        if "bullet_style" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN bullet_style TEXT DEFAULT ''")
        if "space_before" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN space_before REAL DEFAULT 0")
        if "hide_title" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN hide_title INTEGER DEFAULT 0")
        if "para_gap" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN para_gap REAL DEFAULT 0")
        if "item_gap" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN item_gap REAL DEFAULT 0")
        if "title_gap" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN title_gap REAL DEFAULT 0")
        if "title" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN title TEXT DEFAULT ''")
        if "area" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN area TEXT DEFAULT ''")
        if "font_pt" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN font_pt INTEGER DEFAULT 0")
        if "font_style" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN font_style TEXT DEFAULT ''")
        if "page_no" not in have:
            conn.execute("ALTER TABLE items ADD COLUMN page_no INTEGER DEFAULT 0")
        conn.execute("UPDATE sections SET title=?, hint=? WHERE slug='mass-schedule' AND title=?",
                     ("This Week in Our Parish: Lectors, Sacristans & Ministers",
                      "Weekly form: header, then day, date, lectors, sacristans and Eucharistic ministers (several names allowed)",
                      "Mass Schedule, Intentions & Ministers"))
        if not conn.execute("SELECT 1 FROM users").fetchone():
            pw = os.environ.get("BULLETIN_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            conn.execute("INSERT INTO users(username,display_name,pw_hash,role,must_reset) VALUES(?,?,?,?,1)",
                         ("admin", "Administrator", generate_password_hash(pw), "admin"))
            if not os.environ.get("BULLETIN_ADMIN_PASSWORD"):
                print(f"[bulletin] One-time admin password: {pw}  (change it at first login)")
        conn.commit()

    with app.app_context():
        init_db()

    # ---------- auth helpers ----------
    HISTORY_LABELS = {"layout": "Moved or resized a section", "reset-layout": "Reset layout", "section": "Saved a section",
                      "add-section": "Added a section", "delete": "Deleted section content", "extra-pages": "Added or removed pages",
                      "assistant": "Assistant changes", "approve": "Approved a section", "copy": "Copied a section", "keep": "Changed keep/weekly",
                      "header": "Changed header", "footer": "Changed footer"}

    def snapshot_bulletin(bid, label):
        conn = db()
        b = conn.execute("SELECT * FROM bulletins WHERE id=?", (bid,)).fetchone()
        if not b:
            return
        snap = {"bulletin": dict(b),
                "items": [dict(r) for r in conn.execute("SELECT * FROM items WHERE bulletin_id=?", (bid,))],
                "sections": [dict(r) for r in conn.execute("SELECT * FROM sections WHERE bulletin_id=?", (bid,))]}
        data = json.dumps(snap)
        last = conn.execute("SELECT snapshot FROM history WHERE bulletin_id=? ORDER BY id DESC LIMIT 1", (bid,)).fetchone()
        if last and last["snapshot"] == data:
            return
        conn.execute("INSERT INTO history(bulletin_id,label,snapshot,created,by) VALUES(?,?,?,?,?)",
                     (bid, label, data, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), g.user["username"] if g.user else ""))
        conn.execute("DELETE FROM history WHERE bulletin_id=? AND id NOT IN (SELECT id FROM history WHERE bulletin_id=? ORDER BY id DESC LIMIT 40)", (bid, bid))
        conn.commit()

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not session.get("csrf"):
            session["csrf"] = secrets.token_hex(16)
        if request.method == "POST":
            if not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
                abort(400, "Invalid form token")
        if (g.user and g.user["must_reset"] and request.endpoint
                not in ("password", "logout", "static")):
            return redirect(url_for("password"))

    app.jinja_env.globals["csrf"] = lambda: session.get("csrf", "")

    def calendar_rows(body):
        rows = []
        for line in (body or "").splitlines():
            if line.strip():
                parts = [p.strip() for p in line.split("|")]
                rows.append((parts + ["", "", ""])[:3] if len(parts) >= 3 else [parts[0], "", " ".join(parts[1:])])
        return rows

    app.jinja_env.globals["calendar_rows"] = calendar_rows

    CAL_MIN = ("lectors", "sacristans", "ministers")

    def calendar_entries(body):
        """Calendar rows as dicts, with optional weekend Mass ministers (lectors, sacristans, Eucharistic ministers)."""
        out = []
        for line in (body or "").splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                parts = (parts + [""] * 6)[:6]
                out.append(dict(zip(("date", "time", "event") + CAL_MIN, parts)))
            else:
                out.append(dict(zip(("date", "time", "event") + CAL_MIN, [parts[0], "", " ".join(parts[1:]), "", "", ""])))
        return out

    app.jinja_env.globals["calendar_entries"] = calendar_entries

    def long_date(iso):
        try:
            d = date.fromisoformat((iso or "").strip())
        except ValueError:
            return ""
        return f"{d:%A, %B} {d.day}, {d.year}"

    def iso_date(text):
        try:
            return datetime.strptime((text or "").strip(), "%A, %B %d, %Y").date().isoformat()
        except ValueError:
            return ""

    def form_vals(prefix, i, fields):
        vals = []
        for k in fields:
            v = request.form.get(f"{prefix}{i}_{k}", "").replace("|", "/").strip()
            if k == "notes":
                v = "\\n".join(l.strip() for l in request.form.get(f"{prefix}{i}_{k}", "").replace("|", "/").splitlines() if l.strip())
            vals.append("" if k == "day" else long_date(v) if k == "date" else v)
        return vals

    app.jinja_env.globals["iso_date"] = iso_date

    def styled(esc):
        import re
        from markupsafe import Markup
        s = str(esc)
        for _ in range(3):
            s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s, flags=re.S)
            s = re.sub(r"~~(.+?)~~", r"<em>\1</em>", s, flags=re.S)
            s = re.sub(r"__(.+?)__", r'<span class="ul">\1</span>', s, flags=re.S)
            s = re.sub(r"\{(\d{1,2})pt\|(.+?)\}", lambda m: '<span style="font-size:%dpt">%s</span>' % (min(40, max(8, int(m.group(1)))), m.group(2)), s, flags=re.S)
            s = re.sub(r"\{(#[0-9a-fA-F]{6})\|(.+?)\}", r'<span style="color:\1">\2</span>', s, flags=re.S)
        s = re.sub(r"(?<![\w#])(\d+)(st|nd|rd|th)\b", r"\1<sup>\2</sup>", s)
        return Markup(s)

    def ocr_text(path):
        """Read text from a picture with the tesseract program; None when it is not installed or fails."""
        import shutil
        exe = shutil.which("tesseract")
        if not exe:
            return None
        try:
            out = subprocess.run([exe, path, "stdout"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return " ".join(out.stdout.split()) if out.returncode == 0 else None

    def gallery_data(raw):
        try:
            d = json.loads(raw or "")
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        d.setdefault("h", 1.5)
        d["dir"] = "stack" if d.get("dir") == "stack" else "row"
        d["wrap"] = d.get("wrap") if d.get("wrap") in ("left", "right") else ""
        d["align"] = d.get("align") if d.get("align") in ("left", "right") else "center"
        d["gap"] = d.get("gap", 0.15) if isinstance(d.get("gap"), (int, float)) else 0.15
        d["photos"] = [p for p in d.get("photos", []) if isinstance(p, dict) and p.get("f")]
        for p in d["photos"]:
            try:
                from PIL import Image
                with Image.open(os.path.join(app.config["UPLOAD_DIR"], p["f"])) as im:
                    p["ar"] = max(0.2, min(5.0, im.width / im.height))
            except Exception:
                p["ar"] = 1.5
        return d

    app.jinja_env.globals["gallery_data"] = gallery_data

    def tight(text, on=True):
        """With no-stragglers on, bind the last two words with a no-break space so a paragraph never ends with one word alone on a line."""
        from markupsafe import Markup, escape
        text = str(text or "")
        if on is not None and not on:
            return styled(escape(text))
        words = text.split(" ")
        if len(words) < 3:
            return styled(escape(text))
        out = escape(words[0])
        for i in range(1, len(words)):
            first_of_sentence = i >= 2 and words[i - 2][-1:] in ".!?"
            sep = Markup("&nbsp;") if (i == len(words) - 1 or first_of_sentence) else " "
            out += sep + escape(words[i])
        return styled(out)
    def soft_paras(body):
        """Split text into paragraphs; a line ending in // is a soft return (line break, no paragraph gap)."""
        out, cur = [], []
        for line in (body or "").split("\n"):
            t = line.strip()
            if t[:2] in ("- ", "* ", "\u2022 "):
                if cur:
                    out.append(cur)
                    cur = []
                if out and isinstance(out[-1], tuple):
                    out[-1][1].append(t[2:].strip())
                else:
                    out.append(("ul", [t[2:].strip()]))
                continue
            if t.endswith("//"):
                t = t[:-2].rstrip()
                if t:
                    cur.append(t)
                continue
            if t:
                cur.append(t)
            if cur:
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return out

    app.jinja_env.globals["soft_paras"] = soft_paras
    app.jinja_env.globals["BULLETS"] = BULLETS
    app.jinja_env.globals["tight"] = tight
    app.jinja_env.globals["gap_display"] = gap_display
    app.jinja_env.globals["long_sunday"] = long_date

    MASS_FIELDS = ("day", "date", "time", "lectors", "sacristans", "ministers", "notes")

    def split_names(text):
        return [n.strip() for n in re.split(r"[;,\n]", text or "") if n.strip()]

    def mass_data(body):
        """Return (header, rows); each row has its name fields split into lists."""
        header, rows = "", []
        for line in (body or "").splitlines():
            if line.startswith("#"):
                header = line[1:].strip()
            elif line.strip():
                parts = [p.strip() for p in line.split("|")]
                row = dict(zip(MASS_FIELDS, (parts + [""] * 7)[:7]))
                row["notes"] = row["notes"].replace("\\n", "\n")
                for k in ("lectors", "sacristans", "ministers"):
                    row[k + "_list"] = split_names(row[k])
                rows.append(row)
        return header, rows

    app.jinja_env.globals["mass_data"] = mass_data

    ADDR_FIELDS = ("street", "city", "phone", "email", "web")

    def cover_data(body):
        """Parse the cover: @date | date | title, @address | ..., @hours | days | time | notes; other lines are extra text."""
        out = {"date": "", "title": "", "addr": dict.fromkeys(ADDR_FIELDS, ""), "hours": [], "extra": ""}
        extra = []
        for line in (body or "").splitlines():
            parts = [x.strip() for x in line.split("|")]
            tag = parts[0].lower()
            if tag == "@date":
                out["date"], out["title"] = (parts + ["", ""])[1], (parts + ["", ""])[2]
            elif tag == "@address":
                out["addr"] = dict(zip(ADDR_FIELDS, (parts[1:] + [""] * 5)[:5]))
                if not out["addr"]["web"].lower().startswith(("http://", "https://")):
                    out["addr"]["web"] = ""
            elif tag == "@hours":
                out["hours"].append(dict(zip(("days", "time", "notes"), (parts[1:] + ["", "", ""])[:3])))
            else:
                extra.append(line)
        out["extra"] = "\n".join(extra).strip()
        out["has_addr"] = any(out["addr"].values())
        return out

    app.jinja_env.globals["cover_data"] = cover_data

    def cover_prefilled(body, sunday):
        cv = cover_data(body)
        if not cv["date"]:
            cv["date"] = long_date(sunday)
        if not cv["has_addr"]:
            cv["addr"].update(street="31300 Overseas Hwy", city="Big Pine Key, FL 33043", phone="305-872-2537")
        if not cv["hours"]:
            cv["hours"] = [{"days": "Monday-Friday", "time": "9:30am-3:30pm", "notes": ""}]
        return cv

    TIMES_FIELDS = ("title", "days", "name", "time", "notes", "web")

    def times_rows(body):
        rows = []
        for line in (body or "").splitlines():
            if line.strip():
                parts = [p.strip() for p in line.split("|")]
                row = dict(zip(TIMES_FIELDS, (parts + [""] * 6)[:6]))
                if not row["web"].lower().startswith(("http://", "https://")):
                    row["web"] = ""
                rows.append(row)
        return rows

    app.jinja_env.globals["times_rows"] = times_rows

    STAFF_FIELDS = ("name", "role", "phone", "email")

    def staff_rows(body):
        rows = []
        for line in (body or "").splitlines():
            if line.strip():
                parts = [p.strip() for p in line.split("|")]
                rows.append(dict(zip(STAFF_FIELDS, (parts + [""] * 4)[:4])))
        return rows

    app.jinja_env.globals["staff_rows"] = staff_rows

    INTENT_FIELDS = ("day", "date", "time", "for_whom", "by_whom")

    def intention_data(body):
        """Return (header, days) where days groups intentions by (day, date), in entered order."""
        header, days = "", []
        for line in (body or "").splitlines():
            if line.startswith("#"):
                header = line[1:].strip()
            elif line.strip():
                parts = [p.strip() for p in line.split("|")]
                row = dict(zip(INTENT_FIELDS, (parts + [""] * 5)[:5]))
                if not days or (days[-1]["day"], days[-1]["date"]) != (row["day"], row["date"]):
                    days.append({"day": row["day"], "date": row["date"], "items": []})
                days[-1]["items"].append(row)
        return header, days

    app.jinja_env.globals["intention_data"] = intention_data

    def need(*roles):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                if not g.user:
                    return redirect(url_for("login", next=request.path))
                if roles and g.user["role"] not in roles:
                    abort(403)
                return fn(*a, **kw)
            return wrapper
        return deco

    def can_edit_section(section_id):
        r = g.user["role"]
        if r in ("admin", "editor"):
            return True
        if r == "contributor":
            return bool(db().execute("SELECT 1 FROM user_sections WHERE user_id=? AND section_id=?",
                                     (g.user["id"], section_id)).fetchone())
        return False

    def safe_next(target):
        return target if target and target.startswith("/") and not target.startswith("//") else None

    # ---------- bulletin helpers ----------
    def next_sunday(d=None):
        d = d or date.today()
        return d + timedelta(days=(6 - d.weekday()) % 7)

    def week_free(body):
        """Drop the week-specific @date line so a carried cover does not repeat last week's date and title."""
        return "\n".join(l for l in (body or "").splitlines() if not l.strip().lower().startswith("@date"))

    def ensure_items(bulletin_id, sunday):
        """Create items: carry standing sections (and their layout) from the previous bulletin;
        weekly sections start empty. Custom sections belong to one bulletin only."""
        conn = db()
        prev = conn.execute("SELECT id FROM bulletins WHERE sunday<? ORDER BY sunday DESC LIMIT 1",
                            (sunday,)).fetchone()
        secs = conn.execute("SELECT * FROM sections WHERE bulletin_id IS NULL OR bulletin_id=? ORDER BY position",
                            (bulletin_id,)).fetchall()
        fresh = False
        for s in secs:
            if conn.execute("SELECT 1 FROM items WHERE bulletin_id=? AND section_id=?",
                            (bulletin_id, s["id"])).fetchone():
                continue
            fresh = True
            old = None
            if prev and s["bulletin_id"] is None:
                old = conn.execute("SELECT * FROM items WHERE bulletin_id=? AND section_id=?",
                                   (prev["id"], s["id"])).fetchone()
            pos, pages, cols = (old["position"], old["pages"], old["cols"]) if old else (s["position"], s["pages"], 1)
            if old and not s["weekly"]:
                conn.execute("""INSERT INTO items(bulletin_id,section_id,body,image,alt,decorative,caption,source,carried,
                    position,pages,cols,crop_zoom,crop_x,crop_y,crop_h,title,title_gap,item_gap,area,page_no,font_pt,font_style,min_h,img_pos,para_gap,hide_title,space_before,bullet_style)
                    VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (bulletin_id, s["id"], week_free(old["body"]) if s["slug"] == "cover" else old["body"], old["image"], old["alt"], old["decorative"],
                              old["caption"], old["source"], pos, pages, cols,
                              old["crop_zoom"], old["crop_x"], old["crop_y"], old["crop_h"], old["title"], old["title_gap"], old["item_gap"],
                              old["area"], old["page_no"], old["font_pt"], old["font_style"], old["min_h"], old["img_pos"], old["para_gap"], old["hide_title"], old["space_before"], old["bullet_style"]))
                conn.execute("UPDATE items SET gallery=? WHERE bulletin_id=? AND section_id=?", (old["gallery"] or "", bulletin_id, s["id"]))
            else:
                conn.execute("INSERT INTO items(bulletin_id,section_id,position,pages,cols,area,page_no,font_pt,font_style,title,title_gap,item_gap,min_h,img_pos,para_gap,hide_title,space_before,bullet_style) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (bulletin_id, s["id"], pos, pages, cols, old["area"] if old else "", old["page_no"] if old else 0,
                              old["font_pt"] if old else 0, old["font_style"] if old else "", old["title"] if old else "",
                              old["title_gap"] if old else 0, old["item_gap"] if old else 0, old["min_h"] if old else 0, old["img_pos"] if old else "", old["para_gap"] if old else 0, old["hide_title"] if old else 0, old["space_before"] if old else 0, old["bullet_style"] if old else ""))
        if fresh:
            fill_mass_week(conn, bulletin_id, sunday, prev)
        conn.commit()

    def fill_mass_week(conn, bulletin_id, sunday, prev):
        """Date the weekly Mass schedule: Saturday before the issue through the following Sunday.
        The previous issue's last Saturday and Sunday (with their names and notes) become the first two days."""
        item = conn.execute("""SELECT i.id, i.body FROM items i JOIN sections s ON s.id=i.section_id
                               WHERE i.bulletin_id=? AND s.slug='mass-schedule'""", (bulletin_id,)).fetchone()
        if not item or (item["body"] or "").strip():
            return
        try:
            sun = date.fromisoformat(sunday)
        except ValueError:
            return
        head, old = "", {}
        if prev:
            pb = conn.execute("""SELECT i.body FROM items i JOIN sections s ON s.id=i.section_id
                                 WHERE i.bulletin_id=? AND s.slug='mass-schedule'""", (prev["id"],)).fetchone()
            for line in ((pb["body"] if pb else "") or "").splitlines():
                if line.startswith("#"):
                    head = line[1:].strip()
                elif line.strip():
                    parts = [x.strip() for x in line.split("|")]
                    parts = (parts + [""] * 7)[:7]
                    d = iso_date(parts[1])
                    if d:
                        old[d] = parts
        lines = ["# " + head] if head else []
        for n in range(-1, 8):
            d = sun + timedelta(days=n)
            parts = old.get(d.isoformat()) if n in (-1, 0) else None
            parts = list(parts) if parts else ["", long_date(d.isoformat()), "", "", "", "", ""]
            parts[0] = ""
            lines.append(" | ".join(parts))
        conn.execute("UPDATE items SET body=? WHERE id=?", ("\n".join(lines), item["id"]))

    def area_of(r):
        """Where a section sits: full page width, or the left/right half. Mass Intentions defaults to a left half."""
        a = r["area"] or ("left" if r["slug"] == "mass-intentions" else "full")
        return a if a in ("left", "right", "below", "above", "left3", "mid3", "right3", "left23", "right23") and r["slug"] != "cover" and r["pages"] == 1 else "full"

    def rows_on(p):
        d = p.get("duo")
        rows = []
        if d:
            for k in (("left3", "mid3", "right3", "left23", "right23") if d.get("third") else ("left", "right")):
                rows += [d[k], *d[k + "_more"]]
        else:
            rows = [p.get("row")]
        return list(p.get("above", [])) + rows + list(p.get("right_more", [])) + list(p.get("p1_below", [])) + list(p.get("more", []))

    def layout(bulletin_id, include_pending=False):
        """Return (pages, toc). Full-width sections take whole pages; left/right half sections share a page
        (placed on their chosen page number, else the first free half). Total padded to a multiple of 4."""
        rows = db().execute("""SELECT s.id, s.slug, COALESCE(NULLIF(i.title, ''), s.title) AS title, s.weekly, s.hint, s.bulletin_id,
            i.position, i.pages, i.cols, i.area, i.page_no, i.font_pt, i.font_style, i.id item_id, i.body, i.image, i.alt, i.decorative, i.caption,
            i.source, i.status, i.carried, i.crop_zoom, i.crop_x, i.crop_y, i.crop_h, i.min_h, i.title_gap, i.item_gap, i.img_pos, i.para_gap, i.hide_title, i.space_before, i.bullet_style, i.gallery FROM sections s JOIN items i ON i.section_id=s.id
            WHERE i.bulletin_id=? ORDER BY i.position, s.id""", (bulletin_id,)).fetchall()
        printed = [r for r in rows
                   if (bool((cover_data(r["body"])["extra"] if r["slug"] == "cover" else (r["body"] or "").strip()) or r["image"]))
                   and not (r["status"] == "pending" and not include_pending)]
        pages = []
        def blocks(r):
            return [{"row": r, "part": n + 1, "of": r["pages"]} for n in range(r["pages"])]
        cover_printed = any(r["slug"] == "cover" for r in printed)
        p1_all = [] if cover_printed else sorted((r for r in printed if r["page_no"] == 1 and r["slug"] != "cover"),
                                                 key=lambda r: r["position"])
        brow = db().execute("SELECT toc_place, toc_show FROM bulletins WHERE id=?", (bulletin_id,)).fetchone()
        toc_full = bool(brow and brow["toc_place"] == "full" and (brow["toc_show"] is None or brow["toc_show"]))
        slot_keys = ("left", "right", "left3", "mid3", "right3", "left23", "right23")
        if toc_full and p1_all:
            duo = {"third": None, **{k: None for k in slot_keys}, **{k + "_more": [] for k in slot_keys}}
            under = []
            for r in p1_all:
                a = area_of(r)
                if a not in slot_keys:
                    under.append(r)
                    continue
                third = a.endswith("3")
                if duo["third"] is None:
                    duo["third"] = third
                if duo["third"] != third:
                    under.append(r)
                elif duo[a] is None:
                    duo[a] = r
                else:
                    duo[a + "_more"].append(r)
            first = next((duo[k] for k in slot_keys if duo[k]), None)
            if first is None:
                first, under = under[0], under[1:]
                duo = None
            pages.append({"row": first, "part": 1, "of": first["pages"], "duo": duo, "p1_full": True, "p1_below": under})
        else:
            p1_side = [r for r in p1_all if area_of(r) != "below"]
            p1_under = [r for r in p1_all if area_of(r) == "below"]
            if not p1_side and p1_under:
                p1_side, p1_under = p1_under[:1], p1_under[1:]
            p1 = p1_side[0] if p1_side else None
            if p1 is not None:
                pages.extend(blocks(p1))
                pages[0]["right_more"] = p1_side[1:]
                pages[0]["p1_below"] = p1_under
        fulls = [r for r in printed if area_of(r) == "full" and r not in p1_all]
        for r in fulls:
            if not (r["page_no"] and r["page_no"] >= 2 and r["slug"] != "cover"):
                pages.extend(blocks(r))
        for r in sorted((r for r in fulls if r["page_no"] and r["page_no"] >= 2 and r["slug"] != "cover"),
                        key=lambda r: (r["page_no"], r["position"])):
            at = max(1, min(r["page_no"] - 1, len(pages)))
            pages[at:at] = blocks(r)
        if not pages:
            pages.append({"row": None})
        halves = [r for r in printed if area_of(r) in ("left", "right", "left3", "mid3", "right3", "left23", "right23") and r not in p1_all]
        def page_wanted(r):
            return r["page_no"] or (2 if r["slug"] == "mass-intentions" else 0)

        for r in sorted(halves, key=lambda r: (page_wanted(r) or 99, r["position"])):
            side, want = area_of(r), max(2, page_wanted(r))
            third = side.endswith("3")
            def fits(d):
                if bool(d.get("third")) != third or d[side] is not None:
                    return False
                if not third:
                    return True
                wide_l, wide_r = d["left23"] is not None, d["right23"] is not None
                clash = {"left23": wide_r or d["left3"] or d["mid3"], "right23": wide_l or d["right3"] or d["mid3"],
                         "left3": wide_l or wide_r and False, "right3": wide_r, "mid3": wide_l or wide_r}[side]
                return not clash
            idx = None
            if page_wanted(r) and 1 <= want - 1 < len(pages) and pages[want - 1].get("duo") is not None \
                    and bool(pages[want - 1]["duo"].get("third")) == third and pages[want - 1]["duo"][side] is not None:
                pages[want - 1]["duo"][side + "_more"].append(r)
                continue
            for k in range(max(1, want - 1) if page_wanted(r) else 1, len(pages)):
                if pages[k].get("duo") is not None and fits(pages[k]["duo"]):
                    idx = k
                    break
            if idx is None:
                new = {"row": None, "duo": {"third": third, **{k: None for k in ("left", "right", "left3", "mid3", "right3", "left23", "right23")},
                                                   **{k + "_more": [] for k in ("left", "right", "left3", "mid3", "right3", "left23", "right23")}}}
                at = max(1, min(want - 1, len(pages))) if page_wanted(r) else len(pages)
                while at < len(pages) and pages[at].get("duo") is not None:
                    at += 1
                pages.insert(at, new)
                idx = at
            pages[idx]["duo"][side] = r
            d = pages[idx]["duo"]
            pages[idx]["row"] = next(d[k] for k in ("left", "right", "left3", "mid3", "right3", "left23", "right23") if d[k])
        def band_pos(pg):
            ps = [x["position"] for x in rows_on(pg) if x is not None]
            return (min(ps), max(ps)) if ps else None
        for r in sorted((r for r in printed if area_of(r) in ("below", "above") and r not in p1_all), key=lambda r: r["position"]):
            target = None
            for k, pg in enumerate(pages):
                bp = band_pos(pg)
                if bp is None or pg.get("row") is None:
                    continue
                if area_of(r) == "below" and bp[1] < r["position"] and (target is None or bp[1] >= band_pos(target)[1]):
                    target = pg
                if area_of(r) == "above" and k > 0 and bp[0] > r["position"] and (target is None or bp[0] < band_pos(target)[0]):
                    target = pg
            if target is None:
                pages.append({"row": r, "part": 1, "of": 1})
            else:
                target.setdefault("more" if area_of(r) == "below" else "above", []).append(r)
        toc = []
        for n, p in enumerate(pages, 1):
            p["number"] = n
        seen = set()
        for p in pages:
            for r in rows_on(p):
                if r is not None and r["id"] not in seen:
                    seen.add(r["id"])
                    toc.append({"title": r["title"], "page": p["number"], "pos": r["position"]})
        toc.sort(key=lambda t: (t["page"], t["pos"]))
        cur = len(pages) + 1
        extra = db().execute("SELECT extra_pages FROM bulletins WHERE id=?", (bulletin_id,)).fetchone()
        extra = (extra["extra_pages"] or 0) if extra else 0
        ads = [dict(r) for r in db().execute("SELECT name,image,w,h FROM ads WHERE active=1 ORDER BY position, id").fetchall()]
        ad_pg = ad_pages(ads)
        for _ in range(((-(len(pages) + len(ad_pg))) % 4 if pages else 4) + extra):
            pages.append({"row": None, "number": cur})
            cur += 1
        for k, pg_ in enumerate(ad_pg):
            pages.append({"row": None, "ad_rows": pg_["rows"], "ad_gap": pg_["gap"], "ad_first": k == 0, "ad_last": k == len(ad_pg) - 1, "number": cur})
            if k == 0:
                toc.append({"title": "Our Advertisers", "page": cur, "pos": 99999})
            cur += 1
        return pages, toc

    def page_spans(rows, bid=None):
        """Map section id -> (first, last, filled) page, from the real layout. Empty sections show where they would start."""
        placed = {}
        for p in layout(bid, include_pending=True)[0]:
            for r in rows_on(p):
                if r is not None:
                    lo, hi = placed.get(r["id"], (p["number"], p["number"]))
                    placed[r["id"]] = (min(lo, p["number"]), max(hi, p["number"]))
        spans, cur = {}, 1
        for r in rows:
            if r["id"] in placed:
                spans[r["id"]] = (*placed[r["id"]], True)
            else:
                spans[r["id"]] = (cur, cur + r["pages"] - 1, False)
        return spans

    PLAIN_SLUGS_EXCLUDED = ("mass-schedule", "mass-times", "mass-intentions", "calendar", "youth-calendar", "cover", "staff")

    # ---------- routes ----------
    @app.before_request
    def auto_history():
        if request.method != "POST" or not g.get("user") or not request.view_args:
            return
        bid = request.view_args.get("bid")
        path = request.path
        if not bid or "/history" in path or "/check" in path:
            return
        label = "Changed the bulletin"
        for key, text in HISTORY_LABELS.items():
            if f"/{key}" in path:
                label = text
                break
        try:
            snapshot_bulletin(bid, label)
        except Exception:
            pass

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = db().execute("SELECT * FROM users WHERE username=?",
                             (request.form.get("username", "").strip().lower(),)).fetchone()
            if u and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                session.clear()
                session["uid"] = u["id"]
                session["csrf"] = secrets.token_hex(16)
                return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
            flash("Incorrect username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/password", methods=["GET", "POST"])
    @need()
    def password():
        if request.method == "POST":
            new = request.form.get("new", "")
            if len(new) < 10 or not check_password_hash(g.user["pw_hash"], request.form.get("current", "")):
                flash("Current password must be correct and the new one at least 10 characters.", "error")
            else:
                db().execute("UPDATE users SET pw_hash=?, must_reset=0 WHERE id=?",
                             (generate_password_hash(new), g.user["id"]))
                db().commit()
                flash("Password updated.", "ok")
                return redirect(url_for("dashboard"))
        return render_template("password.html")

    @app.route("/")
    @need()
    def dashboard():
        rows = db().execute("SELECT * FROM bulletins ORDER BY sunday DESC").fetchall()
        return render_template("dashboard.html", bulletins=rows, suggested=next_sunday().isoformat())

    @app.route("/bulletins/new", methods=["POST"])
    @need("admin", "editor")
    def new_bulletin():
        try:
            sunday = date.fromisoformat(request.form.get("sunday", "")).isoformat()
        except ValueError:
            flash("Enter a valid date.", "error")
            return redirect(url_for("dashboard"))
        conn = db()
        created = conn.execute("INSERT OR IGNORE INTO bulletins(sunday) VALUES(?)", (sunday,)).rowcount == 1
        b = conn.execute("SELECT id FROM bulletins WHERE sunday=?", (sunday,)).fetchone()
        prev = conn.execute("SELECT * FROM bulletins WHERE sunday<? AND id<>? ORDER BY sunday DESC LIMIT 1", (sunday, b["id"])).fetchone() if created else None
        if prev:
            keep = [c for c in BULLETIN_CARRY if c in prev.keys()]
            conn.execute("UPDATE bulletins SET " + ", ".join(c + "=?" for c in keep) + " WHERE id=?",
                         [prev[c] for c in keep] + [b["id"]])
        conn.commit()
        ensure_items(b["id"], sunday)
        return redirect(url_for("edit", bid=b["id"]))

    def get_bulletin(bid):
        b = db().execute("SELECT * FROM bulletins WHERE id=?", (bid,)).fetchone()
        if not b:
            abort(404)
        return b

    @app.route("/bulletins/<int:bid>")
    @need()
    def edit(bid):
        b = get_bulletin(bid)
        ensure_items(bid, b["sunday"])
        secs = db().execute("""SELECT s.id, s.slug, COALESCE(NULLIF(i.title, ''), s.title) AS title, s.weekly, s.hint, s.bulletin_id, i.position, i.pages,
            i.cols, i.area, i.page_no, i.font_pt, i.font_style, i.title_gap, i.item_gap, i.status, i.carried, i.body, i.image, i.updated_by
            FROM sections s JOIN items i ON i.section_id=s.id WHERE i.bulletin_id=? ORDER BY i.position, s.id""",
                            (bid,)).fetchall()
        pages, _ = layout(bid, include_pending=True)
        editable = {s["id"]: can_edit_section(s["id"]) for s in secs}
        return render_template("edit.html", font_styles=FONT_STYLES, photos=db().execute("SELECT * FROM photos ORDER BY title").fetchall(), b=b, secs=secs, total=len(pages), editable=editable,
                               where=page_spans(secs, bid))

    @app.route("/bulletins/<int:bid>/section/<int:sid>", methods=["GET", "POST"])
    @need()
    def section(bid, sid):
        b = get_bulletin(bid)
        ensure_items(bid, b["sunday"])
        s = db().execute("SELECT * FROM sections WHERE id=?", (sid,)).fetchone() or abort(404)
        item = db().execute("SELECT * FROM items WHERE bulletin_id=? AND section_id=?", (bid, sid)).fetchone()
        if request.method == "POST":
            if not can_edit_section(sid) or b["status"] == "published":
                abort(403)
            body = request.form.get("body", "")
            if s["slug"] == "mass-schedule":
                lines = []
                head = request.form.get("header", "").replace("|", "/").replace("\n", " ").strip()
                if head:
                    lines.append("# " + head)
                for i in range(min(int(request.form.get("mass_count", 0)), 40)):
                    vals = form_vals("m", i, MASS_FIELDS)
                    if any(vals):
                        lines.append(" | ".join(vals))
                body = "\n".join(lines)
            elif s["slug"] == "mass-intentions":
                lines = []
                head = request.form.get("header", "").replace("|", "/").replace("\n", " ").strip()
                if head:
                    lines.append("# " + head)
                for gi in range(min(int(request.form.get("group_count", 0)), 100)):
                    gdate = long_date(request.form.get(f"g{gi}_date", ""))
                    for r in range(min(int(request.form.get(f"g{gi}_count", 0)), 100)):
                        vals = [request.form.get(f"g{gi}_{r}_{k}", "").replace("|", "/").strip() for k in ("time", "for_whom", "by_whom")]
                        if any(vals):
                            lines.append(" | ".join(["", gdate] + vals))
                body = "\n".join(lines)
            elif s["slug"] == "cover":
                clean = lambda k: request.form.get(k, "").replace("|", "/").replace("\n", " ").strip()
                lines = []
                cd, ct = long_date(request.form.get("cover_date", "")) or clean("cover_date_old"), clean("cover_title")
                if cd or ct:
                    lines.append(f"@date | {cd} | {ct}")
                av = [clean("addr_" + k) for k in ADDR_FIELDS]
                if any(av):
                    lines.append("@address | " + " | ".join(av))
                for i in range(min(int(request.form.get("hours_count", 0)), 40)):
                    hv = [clean(f"h{i}_{k}") for k in ("days", "time", "notes")]
                    if any(hv):
                        lines.append("@hours | " + " | ".join(hv))
                extra = request.form.get("extra", "").strip()
                if extra:
                    lines.append(extra)
                body = "\n".join(lines)
            elif s["slug"] in ("calendar", "youth-calendar"):
                lines = []
                for i in range(min(int(request.form.get("event_count", 0)), 300)):
                    vals = [request.form.get(f"e{i}_{k}", "").replace("|", "/").strip() for k in ("date", "time", "event") + CAL_MIN]
                    vals[0] = long_date(vals[0]) or request.form.get(f"e{i}_old", "").replace("|", "/").strip()
                    if any(vals):
                        lines.append(" | ".join(vals).rstrip(" |"))
                body = "\n".join(lines)
            elif s["slug"] == "staff":
                lines = []
                for i in range(min(int(request.form.get("staff_count", 0)), 80)):
                    vals = [request.form.get(f"s{i}_{k}", "").replace("|", "/").strip() for k in STAFF_FIELDS]
                    if any(vals):
                        lines.append(" | ".join(vals))
                body = "\n".join(lines)
            elif s["slug"] == "mass-times":
                lines = []
                for i in range(min(int(request.form.get("times_count", 0)), 40)):
                    vals = [request.form.get(f"t{i}_{k}", "").replace("|", "/").strip() for k in TIMES_FIELDS]
                    if any(vals):
                        lines.append(" | ".join(vals))
                body = "\n".join(lines)
            doc = request.files.get("document")
            doc_added = False
            if doc and doc.filename:
                dext = doc.filename.rsplit(".", 1)[-1].lower()
                if s["slug"] in ("mass-schedule", "mass-times", "mass-intentions", "calendar", "youth-calendar", "cover", "staff"):
                    flash("Mass schedules use the form; paste details into the fields instead of uploading a document.", "error")
                    return redirect(request.url)
                try:
                    if dext == "docx":
                        text = docx_text(io.BytesIO(doc.stream.read(25 * 1024 * 1024)))
                    elif dext == "doc":
                        with tempfile.NamedTemporaryFile(suffix=".doc") as tmp:
                            doc.save(tmp.name)
                            text = doc_text(tmp.name)
                    elif dext in ("txt", "text", "md"):
                        raw = doc.stream.read(2 * 1024 * 1024)
                        try:
                            text = raw.decode("utf-8-sig").strip()
                        except UnicodeDecodeError:
                            text = raw.decode("cp1252", "replace").strip()
                    else:
                        raise ValueError("Documents must be .doc, .docx or .txt.")
                except (ValueError, zipfile.BadZipFile, KeyError, ElementTree.ParseError, subprocess.SubprocessError):
                    flash("Could not read that document. Please upload a valid .docx file (.docx, .doc or .txt).", "error")
                    return redirect(request.url)
                if not text.strip():
                    flash("No text was found in that document (text inside pictures cannot be read).", "error")
                    return redirect(request.url)
                body = (body.rstrip() + "\n\n" + text).strip() if body.strip() else text
                doc_added = True
            image = item["image"]
            f = request.files.get("image")
            if f and f.filename:
                ext = f.filename.rsplit(".", 1)[-1].lower()
                if ext not in IMAGE_EXT:
                    flash("Images must be PNG, JPG, GIF or WebP.", "error")
                    return redirect(request.url)
                image = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                f.save(os.path.join(app.config["UPLOAD_DIR"], image))
            lib = request.form.get("library_id")
            lib_photo = db().execute("SELECT * FROM photos WHERE id=?", (lib,)).fetchone() if lib else None
            if lib_photo and not (f and f.filename):
                image = lib_photo["filename"]
                if not request.form.get("alt", "").strip():
                    request.form = request.form.copy()
                    request.form["alt"] = lib_photo["alt"]
            if request.form.get("remove_image"):
                image = ""
            decorative = 1 if request.form.get("decorative") else 0
            alt = request.form.get("alt", "").strip()
            if request.form.get("ocr") and image:
                found = ocr_text(os.path.join(app.config["UPLOAD_DIR"], image))
                if found:
                    alt = found[:2000]
                    decorative = 0
                    flash("The text in the photo was read and put in the alternative text. Please check it for mistakes.", "ok")
                elif found is None:
                    flash("Text reading (OCR) is not available on this server yet. Please type the words from the photo into the alternative text.", "error")
                else:
                    flash("No text was found in the photo.", "ok")
            if image and not decorative and not alt:
                flash("Alternative text is required for images (or mark the image decorative).", "error")
                return redirect(request.url)
            gal = gallery_data(item["gallery"])
            keep = []
            for n, p in enumerate(gal["photos"]):
                if request.form.get(f"gp_rm_{n}"):
                    continue
                p["alt"] = request.form.get(f"gp_alt_{n}", p.get("alt", "")).strip()[:300]
                p["w"] = "above" if request.form.get(f"gp_w_{n}") == "above" else "below"
                keep.append(p)
            for n in range(4):
                gf = request.files.get(f"ng_file_{n}")
                glib = db().execute("SELECT * FROM photos WHERE id=?", (request.form.get(f"ng_lib_{n}"),)).fetchone() if request.form.get(f"ng_lib_{n}") else None
                galt = request.form.get(f"ng_alt_{n}", "").strip()[:300]
                fname = ""
                if gf and gf.filename:
                    if gf.filename.rsplit(".", 1)[-1].lower() not in IMAGE_EXT:
                        flash("Images must be PNG, JPG, GIF or WebP.", "error")
                        return redirect(request.url)
                    fname = f"{uuid.uuid4().hex}_{secure_filename(gf.filename)}"
                    gf.save(os.path.join(app.config["UPLOAD_DIR"], fname))
                elif glib:
                    fname = glib["filename"]
                    galt = galt or glib["alt"] or ""
                if fname:
                    if not galt:
                        flash("Please type a caption for each extra photo (it also describes the photo for screen readers).", "error")
                        return redirect(request.url)
                    keep.append({"f": fname, "alt": galt, "w": "above" if request.form.get(f"ng_w_{n}") == "above" else "below"})
            try:
                gal_h = max(0.5, min(6.0, float(request.form.get("gal_h") or gal["h"])))
            except ValueError:
                gal_h = 1.5
            try:
                gal_gap = max(0.0, min(1.0, float(request.form.get("gal_gap") or 0.15)))
            except ValueError:
                gal_gap = 0.15
            gal_align = request.form.get("gal_align") if request.form.get("gal_align") in ("left", "right") else "center"
            gal_json = json.dumps({"h": gal_h, "gap": gal_gap, "align": gal_align,
                                   "dir": "stack" if request.form.get("gal_dir") == "stack" else "row",
                                   "wrap": request.form.get("gal_wrap") if request.form.get("gal_wrap") in ("left", "right") else "", "photos": keep[:12]}) if keep else ""
            status = "approved" if g.user["role"] in ("admin", "editor") else "pending"
            def num(key, lo, hi, default):
                try:
                    return max(lo, min(hi, float(request.form.get(key, default))))
                except ValueError:
                    return default
            new_title = request.form.get("section_title", s["title"]).strip()[:120]
            db().execute("""UPDATE items SET body=?, image=?, alt=?, decorative=?, caption=?, source=?,
                crop_zoom=?, crop_x=?, crop_y=?, crop_h=?, title=?, title_gap=?, item_gap=?, img_pos=?, para_gap=?, hide_title=?, space_before=?, bullet_style=?, gallery=?,
                status=?, carried=0, updated_by=? WHERE id=?""",
                         (body, image, alt, decorative,
                          request.form.get("caption", "").strip(), request.form.get("source", "").strip(),
                          num("crop_zoom", 1, 4, 1), num("crop_x", 0, 100, 50), num("crop_y", 0, 100, 50), num("crop_h", 0, 9, 0),
                          "" if new_title == s["title"] else new_title,
                          gap_value(request.form.get("title_gap")), gap_value(request.form.get("item_gap")),
                          request.form.get("img_pos") if request.form.get("img_pos") in ("above", "inset-left", "inset-right", "only") else "",
                          gap_value(request.form.get("para_gap")),
                          1 if request.form.get("hide_title") else 0,
                          gap_value(request.form.get("space_before")),
                          request.form.get("bullet_style") if request.form.get("bullet_style") in BULLETS else "",
                          gal_json,
                          status, g.user["username"], item["id"]))
            db().commit()
            if doc_added:
                flash("The document's text is now in the box below. Please review it, then press Save section when it looks right.", "ok")
                return redirect(url_for("section", bid=bid, sid=sid))
            flash("Saved." if status == "approved" else "Saved - waiting for editor approval.", "ok")
            return redirect(url_for("edit", bid=bid))
        rows = db().execute("""SELECT s.id, s.slug, i.pages, i.body, i.image FROM sections s JOIN items i ON i.section_id=s.id
            WHERE i.bulletin_id=? ORDER BY i.position, s.id""", (bid,)).fetchall()
        return render_template("section.html", where=page_spans(rows, bid).get(sid), photos=db().execute("SELECT * FROM photos ORDER BY title").fetchall(), b=b, s=s, item=item, can=can_edit_section(sid), mass_header=mass_data(item["body"])[0], mass=mass_data(item["body"])[1] + [{}],
                               times=times_rows(item["body"]) + [{}],
                               staff=staff_rows(item["body"]) + [{}],
                               events=calendar_entries(item["body"]) + [dict(date="", time="", event="", lectors="", sacristans="", ministers="")] * 2,
                               cover=cover_prefilled(item["body"], b["sunday"]),
                               intent_header=intention_data(item["body"])[0],
                               groups=[dict(d, items=d["items"] + [{}]) for d in intention_data(item["body"])[1]] + [{"day": "", "date": "", "items": [{}, {}]}])

    COPY_COLS = ("body", "image", "alt", "decorative", "caption", "source", "crop_zoom", "crop_x", "crop_y", "crop_h", "title", "title_gap", "item_gap", "img_pos", "para_gap", "hide_title", "space_before", "bullet_style", "gallery")

    @app.route("/bulletins/<int:bid>/section/<int:sid>/copy-next", methods=["POST"])
    @need()
    def copy_next(bid, sid):
        b = get_bulletin(bid)
        if not can_edit_section(sid):
            abort(403)
        conn = db()
        nxt = (date.fromisoformat(b["sunday"]) + timedelta(days=7)).isoformat()
        conn.execute("INSERT OR IGNORE INTO bulletins(sunday) VALUES(?)", (nxt,))
        conn.commit()
        nb = conn.execute("SELECT * FROM bulletins WHERE sunday=?", (nxt,)).fetchone()
        if nb["status"] == "published":
            flash(f"The {nxt} bulletin is already published.", "error")
            return redirect(url_for("section", bid=bid, sid=sid))
        ensure_items(nb["id"], nxt)
        src = conn.execute("SELECT * FROM items WHERE bulletin_id=? AND section_id=?", (bid, sid)).fetchone()
        is_cover = conn.execute("SELECT slug FROM sections WHERE id=?", (sid,)).fetchone()["slug"] == "cover"
        conn.execute("UPDATE items SET " + ",".join(f"{c}=?" for c in COPY_COLS) +
                     ", status='approved', carried=0, updated_by=? WHERE bulletin_id=? AND section_id=?",
                     [(week_free(src[c]) if c == "body" and is_cover else src[c]) for c in COPY_COLS] + [g.user["username"], nb["id"], sid])
        conn.commit()
        flash(f"Copied to the {nxt} bulletin.", "ok")
        return redirect(url_for("section", bid=bid, sid=sid))

    @app.route("/bulletins/<int:bid>/section/<int:sid>/keep", methods=["POST"])
    @need("admin", "editor")
    def keep_section(bid, sid):
        get_bulletin(bid)
        conn = db()
        s = conn.execute("SELECT * FROM sections WHERE id=?", (sid,)).fetchone() or abort(404)
        conn.execute("UPDATE sections SET weekly=? WHERE id=?", (0 if s["weekly"] else 1, sid))
        conn.commit()
        flash("This section will now be kept for all bulletin editions." if s["weekly"]
              else "This section will now start blank each week.", "ok")
        return redirect(url_for("section", bid=bid, sid=sid))

    @app.route("/bulletins/<int:bid>/section/<int:sid>/delete", methods=["POST"])
    @need()
    def delete_content(bid, sid):
        b = get_bulletin(bid)
        if not can_edit_section(sid) or b["status"] == "published":
            abort(403)
        conn = db()
        conn.execute("UPDATE items SET body='', image='', alt='', decorative=0, caption='', source='', gallery='', carried=0, updated_by=? "
                     "WHERE bulletin_id=? AND section_id=?", (g.user["username"], bid, sid))
        conn.commit()
        flash("Section content deleted.", "ok")
        return redirect(url_for("section", bid=bid, sid=sid))

    def footer_address(bid):
        row = db().execute("""SELECT i.body FROM items i JOIN sections s ON s.id=i.section_id
            WHERE i.bulletin_id=? AND s.slug='cover'""", (bid,)).fetchone()
        cv = cover_data(row["body"] if row else "")
        a = cv["addr"]
        line = ", ".join(x for x in (a["street"], a["city"]) if x)
        phone = a["phone"]
        if not line and not phone:
            line, phone = "31300 Overseas Hwy, Big Pine Key, FL 33043", "305-872-2537"
        h = cv["hours"][0] if cv["hours"] else None
        g.cover_dt = (cv["date"], cv["title"])
        hours = " ".join(x for x in (h["days"], h["time"]) if x) if h else ""
        return line, phone, hours or "Monday-Friday 9:30am-3:30pm"

    def open_bulletin(bid):
        b = get_bulletin(bid)
        if b["status"] == "published":
            abort(403)
        return b

    @app.route("/bulletins/<int:bid>/add-section", methods=["POST"])
    @need("admin", "editor")
    def add_section(bid):
        open_bulletin(bid)
        conn = db()
        title = request.form.get("title", "").strip() or "New section"
        last = conn.execute("SELECT COALESCE(MAX(position),0) m FROM items WHERE bulletin_id=?", (bid,)).fetchone()["m"]
        cur = conn.execute("""INSERT INTO sections(slug,title,weekly,pages,position,hint,bulletin_id)
            VALUES(?,?,1,1,?,?,?)""", (f"custom-{uuid.uuid4().hex[:8]}", title, last + 1,
                                      "Added for this week only", bid))
        conn.execute("INSERT INTO items(bulletin_id,section_id,position,pages,cols) VALUES(?,?,?,1,1)",
                     (bid, cur.lastrowid, last + 1))
        conn.commit()
        return redirect(url_for("section", bid=bid, sid=cur.lastrowid))

    @app.route("/bulletins/<int:bid>/history")
    @need("admin", "editor")
    def history_page(bid):
        b = get_bulletin(bid)
        rows = db().execute("SELECT id,label,created,by FROM history WHERE bulletin_id=? ORDER BY id DESC", (bid,)).fetchall()
        return render_template("history.html", b=b, rows=rows)

    @app.route("/bulletins/<int:bid>/history/<int:hid>/restore", methods=["POST"])
    @need("admin", "editor")
    def history_restore(bid, hid):
        open_bulletin(bid)
        conn = db()
        row = conn.execute("SELECT snapshot FROM history WHERE id=? AND bulletin_id=?", (hid, bid)).fetchone()
        if not row:
            abort(404)
        snapshot_bulletin(bid, "Before going back")
        snap = json.loads(row["snapshot"])
        keep = {"id", "sunday", "status"}
        cols = [k for k in snap["bulletin"] if k not in keep]
        if cols:
            conn.execute("UPDATE bulletins SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?",
                         [snap["bulletin"][k] for k in cols] + [bid])
        conn.execute("DELETE FROM items WHERE bulletin_id=?", (bid,))
        have = {r["id"] for r in conn.execute("SELECT id FROM sections WHERE bulletin_id=?", (bid,))}
        want = {r["id"] for r in snap["sections"]}
        for sid in have - want:
            conn.execute("DELETE FROM sections WHERE id=?", (sid,))
        for sec in snap["sections"]:
            if sec["id"] not in have:
                conn.execute(f"INSERT INTO sections({','.join(sec)}) VALUES({','.join('?' * len(sec))})", list(sec.values()))
        for it in snap["items"]:
            conn.execute(f"INSERT INTO items({','.join(it)}) VALUES({','.join('?' * len(it))})", list(it.values()))
        conn.commit()
        flash("Went back to how the bulletin looked at that point.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/reset-layout", methods=["POST"])
    @need("admin", "editor")
    def reset_layout(bid):
        open_bulletin(bid)
        conn = db()
        try:
            sid = int(request.form.get("sid") or 0)
        except ValueError:
            sid = 0
        sql = ("UPDATE items SET position=(SELECT position FROM sections WHERE id=items.section_id), "
               "pages=(SELECT pages FROM sections WHERE id=items.section_id), cols=1, area='', page_no=0, font_pt=0, "
               "font_style='', min_h=0, title_gap=0, item_gap=0, para_gap=0, img_pos='', hide_title=0, space_before=0, bullet_style='', "
               "crop_zoom=1, crop_x=50, crop_y=50, crop_h=0 WHERE bulletin_id=?")
        args = [bid]
        if sid:
            sql += " AND section_id=?"
            args.append(sid)
        conn.execute(sql, args)
        conn.commit()
        flash("Layout reset to the standard settings. Your text and photos were not changed.", "ok")
        return redirect(url_for("section", bid=bid, sid=sid) if sid else url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/layout/<int:sid>", methods=["POST"])
    @need("admin", "editor")
    def configure(bid, sid):
        open_bulletin(bid)
        conn = db()
        rows = conn.execute("SELECT section_id FROM items WHERE bulletin_id=? ORDER BY position, section_id",
                            (bid,)).fetchall()
        order = [r["section_id"] for r in rows]
        if sid not in order:
            abort(404)
        move = request.form.get("move")
        i = order.index(sid)
        if move == "up" and i > 0:
            order[i - 1], order[i] = order[i], order[i - 1]
        elif move == "down" and i < len(order) - 1:
            order[i + 1], order[i] = order[i], order[i + 1]
        try:
            want = int(request.form.get("order") or 0)
        except ValueError:
            want = 0
        if want and not move:
            order.remove(sid)
            order.insert(max(0, min(len(order), want - 1)), sid)
        for n, section_id in enumerate(order):
            conn.execute("UPDATE items SET position=? WHERE bulletin_id=? AND section_id=?", (n, bid, section_id))
        if request.form.get("pages"):
            conn.execute("UPDATE items SET pages=?, cols=? WHERE bulletin_id=? AND section_id=?",
                         (max(1, min(8, int(request.form["pages"]))),
                          2 if request.form.get("cols") == "2" else 1, bid, sid))
        if "area" in request.form:
            area = request.form["area"] if request.form["area"] in ("full", "left", "right", "below", "above", "left3", "mid3", "right3", "left23", "right23") else ""
            try:
                page_no = max(0, min(12, int(request.form.get("page_no") or 0)))
            except ValueError:
                page_no = 0
            try:
                font_pt = int(request.form.get("font_pt") or 0)
            except ValueError:
                font_pt = 0
            font_pt = font_pt if font_pt == 0 or 8 <= font_pt <= 16 else 0
            conn.execute("UPDATE items SET area=?, page_no=?, font_pt=?, font_style=?, title_gap=?, item_gap=? WHERE bulletin_id=? AND section_id=?",
                         (area, page_no, font_pt, request.form.get("font_style") if request.form.get("font_style") in FONT_STYLES else "",
                          gap_value(request.form.get("title_gap")), gap_value(request.form.get("item_gap")), bid, sid))
        if move == "remove":
            sec = conn.execute("SELECT bulletin_id FROM sections WHERE id=?", (sid,)).fetchone()
            if sec["bulletin_id"] == bid:
                conn.execute("DELETE FROM items WHERE bulletin_id=? AND section_id=?", (bid, sid))
                conn.execute("DELETE FROM sections WHERE id=?", (sid,))
        conn.commit()
        if not move and "area" in request.form:
            flash("Settings saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/resize/<int:sid>", methods=["POST"])
    @need("admin", "editor")
    def resize(bid, sid):
        """Drag-to-resize: snap the dragged width to a layout width and store a minimum height (inches)."""
        open_bulletin(bid)
        conn = db()
        item = conn.execute("""SELECT i.area, s.slug FROM items i JOIN sections s ON s.id=i.section_id
                               WHERE i.bulletin_id=? AND i.section_id=?""", (bid, sid)).fetchone()
        if item is None:
            abort(404)
        try:
            frac = float(request.form.get("frac") or 0)
            height = float(request.form.get("height") or 0)
        except ValueError:
            abort(400)
        height = 0 if height < 0.5 else min(9.0, round(height, 2))
        cur = item["area"] or ("left" if item["slug"] == "mass-intentions" else "full")
        right = cur in ("right", "right3", "right23")
        if item["slug"] == "cover":
            area = cur
        elif frac >= 0.85:
            area = "full"
        elif frac >= 0.6:
            area = "right23" if right else "left23"
        elif frac >= 0.42:
            area = "right" if right else "left"
        else:
            area = "mid3" if cur == "mid3" else ("right3" if right else "left3")
        conn.execute("UPDATE items SET area=?, min_h=? WHERE bulletin_id=? AND section_id=?", (area, height, bid, sid))
        conn.commit()
        return jsonify(area=area, min_h=height)

    # ---------- AI assistant ----------
    def find_section(bid, name):
        name = (name or "").strip().lower()
        best = None
        for r in db().execute("""SELECT s.id, s.title FROM sections s JOIN items i ON i.section_id=s.id
                WHERE i.bulletin_id=?""", (bid,)):
            t = r["title"].lower()
            if t == name or (name and (name in t or t in name)):
                if best is None or t == name:
                    best = r
        return best

    def rule_based(bid, text):
        """Offline fallback understanding a few plain-English layout requests."""
        t = text.strip()
        m = re.search(r"(?:set|change|make) (?:the )?(?:header|banner|tagline|theme)(?: text| line)? (?:to|as|say) [\"']?(.+?)[\"']?$", t, re.I)
        if m:
            return [{"action": "set_header", "text": m.group(1)}]
        m = re.search(r"add (?:a |an )?(?:new )?section (?:called |named |titled )?[\"']?(.+?)[\"']?$", t, re.I)
        if m:
            return [{"action": "add_section", "title": m.group(1)}]
        m = re.search(r"move (.+?) (up|down)", t, re.I)
        if m:
            return [{"action": "move", "section": m.group(1), "direction": m.group(2).lower()}]
        m = re.search(r"(?:make|set) (.+?) (?:to |be )?(\d) (column|page)s?", t, re.I)
        if m:
            key = "cols" if m.group(3).lower() == "column" else "pages"
            return [{"action": "layout", "section": m.group(1), key: int(m.group(2))}]
        m = re.search(r"(?:remove|delete) (?:the )?(?:section )?(.+)$", t, re.I)
        if m:
            return [{"action": "remove", "section": m.group(1)}]
        return []

    def llm_actions(bid, text):
        url = os.environ.get("BULLETIN_AI_URL")
        key = os.environ.get("BULLETIN_AI_KEY")
        if not (url and key):
            return None
        titles = [r["title"] for r in db().execute(
            "SELECT s.title FROM sections s JOIN items i ON i.section_id=s.id WHERE i.bulletin_id=?", (bid,))]
        system = ("You edit a parish bulletin layout. Reply with ONLY a JSON array of actions. Allowed: "
                  '{"action":"add_section","title":str,"text":str?}, {"action":"move","section":str,"direction":"up|down"}, '
                  '{"action":"layout","section":str,"pages":1-8?,"cols":1|2?}, {"action":"set_text","section":str,"text":str}, '
                  '{"action":"remove","section":str}, {"action":"set_header","text":str} (the tagline in the banner header). Never invent facts, names, dates or Mass intentions. '
                  "Existing sections: " + json.dumps(titles))
        payload = json.dumps({"model": os.environ.get("BULLETIN_AI_MODEL", "gpt-4o-mini"),
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": text}]}).encode()
        req = urllib.request.Request(url, payload, {"Content-Type": "application/json",
                                                    "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = json.load(resp)["choices"][0]["message"]["content"]
            data = json.loads(re.search(r"\[.*\]", content, re.S).group(0))
            return data if isinstance(data, list) else []
        except Exception:
            return None

    def describe(bid, a):
        sec = find_section(bid, a.get("section")) if a.get("section") else None
        kind = a.get("action")
        if kind == "set_header":
            return f'Set the banner header tagline to "{str(a.get("text", ""))[:120]}"'
        if kind == "add_section":
            return f'Add section "{a.get("title", "New section")}"'
        if not sec:
            return None
        if kind == "move" and a.get("direction") in ("up", "down"):
            return f'Move "{sec["title"]}" {a["direction"]}'
        if kind == "layout":
            return f'Set "{sec["title"]}" to ' + ", ".join(f"{k} = {a[k]}" for k in ("pages", "cols") if k in a)
        if kind == "set_text":
            return f'Replace text of "{sec["title"]}" (needs your review)'
        if kind == "remove":
            return f'Remove "{sec["title"]}" (only sections added for this week can be removed)'
        return None

    def apply_action(bid, a):
        conn = db()
        kind = a.get("action")
        if kind == "set_header":
            conn.execute("UPDATE bulletins SET theme=? WHERE id=?", (str(a.get("text", ""))[:120], bid))
            return
        if kind == "add_section":
            title = str(a.get("title", "New section"))[:80]
            last = conn.execute("SELECT COALESCE(MAX(position),0) m FROM items WHERE bulletin_id=?", (bid,)).fetchone()["m"]
            cur = conn.execute("""INSERT INTO sections(slug,title,weekly,pages,position,hint,bulletin_id)
                VALUES(?,?,1,1,?,?,?)""", (f"custom-{uuid.uuid4().hex[:8]}", title, last + 1, "Added for this week only", bid))
            conn.execute("INSERT INTO items(bulletin_id,section_id,body,position,pages,cols,updated_by) VALUES(?,?,?,?,1,1,?)",
                         (bid, cur.lastrowid, str(a.get("text", "")), last + 1, "AI assistant"))
            return
        sec = find_section(bid, a.get("section"))
        if not sec:
            return
        sid = sec["id"]
        if kind == "move":
            order = [r["section_id"] for r in conn.execute(
                "SELECT section_id FROM items WHERE bulletin_id=? ORDER BY position, section_id", (bid,))]
            i = order.index(sid)
            j = i - 1 if a.get("direction") == "up" else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
            for n, x in enumerate(order):
                conn.execute("UPDATE items SET position=? WHERE bulletin_id=? AND section_id=?", (n, bid, x))
        elif kind == "layout":
            if "pages" in a:
                conn.execute("UPDATE items SET pages=? WHERE bulletin_id=? AND section_id=?",
                             (max(1, min(8, int(a["pages"]))), bid, sid))
            if "cols" in a:
                conn.execute("UPDATE items SET cols=? WHERE bulletin_id=? AND section_id=?",
                             (2 if int(a["cols"]) == 2 else 1, bid, sid))
        elif kind == "set_text":
            conn.execute("UPDATE items SET body=?, status='approved', carried=0, updated_by=? WHERE bulletin_id=? AND section_id=?",
                         (str(a.get("text", "")), "AI assistant", bid, sid))
        elif kind == "remove":
            row = conn.execute("SELECT bulletin_id FROM sections WHERE id=?", (sid,)).fetchone()
            if row["bulletin_id"] == bid:
                conn.execute("DELETE FROM items WHERE bulletin_id=? AND section_id=?", (bid, sid))
                conn.execute("DELETE FROM sections WHERE id=?", (sid,))

    @app.route("/bulletins/<int:bid>/assistant", methods=["POST"])
    @need("admin", "editor")
    def assistant(bid):
        open_bulletin(bid)
        if request.form.get("confirm"):
            for a in session.pop(f"ai{bid}", []):
                apply_action(bid, a)
            db().commit()
            flash("Assistant changes applied.", "ok")
            return redirect(url_for("edit", bid=bid))
        text = request.form.get("request", "").strip()[:1000]
        actions = llm_actions(bid, text)
        if actions is None:
            actions = rule_based(bid, text)
        actions = [a for a in actions if isinstance(a, dict) and describe(bid, a)][:10]
        if not actions:
            flash("I could not turn that into a change. Try: set header to Join us for Fall Festival, add section Pumpkin Patch, move Calendar down, "
                  "make Food Pantry 2 columns, or set a section to 2 pages.", "error")
            return redirect(url_for("edit", bid=bid))
        session[f"ai{bid}"] = actions
        return render_template("assistant.html", b=get_bulletin(bid),
                               steps=[describe(bid, a) for a in actions], request_text=text)

    @app.route("/bulletins/<int:bid>/approve/<int:sid>", methods=["POST"])
    @need("admin", "editor")
    def approve(bid, sid):
        db().execute("UPDATE items SET status='approved' WHERE bulletin_id=? AND section_id=?", (bid, sid))
        db().commit()
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/footer", methods=["POST"])
    @need("admin", "editor")
    def set_footer(bid):
        open_bulletin(bid)
        text = " ".join(request.form.get("footer", "").split())[:160]
        db().execute("UPDATE bulletins SET footer=? WHERE id=?", (text, bid))
        db().commit()
        flash("Footer saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/suggestions", methods=["GET", "POST"])
    @need()
    def suggestions(bid):
        b = get_bulletin(bid)
        days = liturgy.week(date.fromisoformat(b["sunday"]))
        targets = db().execute("""SELECT s.id, s.title FROM sections s JOIN items i ON i.section_id=s.id
            WHERE i.bulletin_id=? AND s.slug NOT IN (%s) ORDER BY i.position""" % ",".join("?" * len(PLAIN_SLUGS_EXCLUDED)),
                               (bid,) + PLAIN_SLUGS_EXCLUDED).fetchall()
        cals = db().execute("""SELECT s.id, s.title FROM sections s JOIN items i ON i.section_id=s.id
            WHERE i.bulletin_id=? AND s.slug IN ('calendar','youth-calendar') ORDER BY i.position""", (bid,)).fetchall()
        pasted = [" ".join(l.split()) for l in request.form.get("pasted", "").splitlines() if l.strip()][:200]
        return render_template("suggestions.html", b=b, days=days, targets=targets, cals=cals, pasted=pasted,
                               can=g.user["role"] in ("admin", "editor") and b["status"] != "published")

    @app.route("/bulletins/<int:bid>/suggestions/apply", methods=["POST"])
    @need("admin", "editor")
    def apply_suggestion(bid):
        open_bulletin(bid)
        lines = [" ".join(t.split())[:400] for t in request.form.getlist("text") if t.strip()]
        text = "\n".join(lines)
        target = request.form.get("target", "")
        if target.startswith("cal:") and lines:
            sid = target[4:]
            sec = db().execute("SELECT s.id, s.title FROM sections s JOIN items i ON i.section_id=s.id WHERE i.bulletin_id=? "
                               "AND s.id=? AND s.slug IN ('calendar','youth-calendar')", (bid, sid if sid.isdigit() else 0)).fetchone()
            if not sec:
                flash("Choose a calendar section.", "error")
                return redirect(url_for("suggestions", bid=bid))
            when = long_date(request.form.get("when", ""))
            tm = request.form.get("when_time", "").replace("|", "/").strip()[:30]
            add = "\n".join(f"{when} | {tm} | {l.replace('|', '/')}" for l in lines)
            ensure_items(bid, get_bulletin(bid)["sunday"])
            db().execute("UPDATE items SET body=CASE WHEN TRIM(body)='' THEN ? ELSE body || char(10) || ? END, carried=0, updated_by=? "
                         "WHERE bulletin_id=? AND section_id=?", (add, add, g.user["username"], bid, sec["id"]))
            db().commit()
            flash(f"Added {len(lines)} event(s) to {sec['title']}. Review them there.", "ok")
            return redirect(url_for("suggestions", bid=bid))
        if not text:
            flash("Nothing to add.", "error")
        elif target == "title":
            db().execute("UPDATE bulletins SET theme=? WHERE id=?", (" \u00b7 ".join(lines)[:120], bid))
            db().commit()
            flash("Added as the header title.", "ok")
        else:
            sec = db().execute("SELECT s.id, s.slug, s.title FROM sections s JOIN items i ON i.section_id=s.id "
                               "WHERE i.bulletin_id=? AND s.id=?", (bid, target if target.isdigit() else 0)).fetchone()
            if not sec or sec["slug"] in PLAIN_SLUGS_EXCLUDED:
                flash("Choose a text section to add to.", "error")
            else:
                ensure_items(bid, get_bulletin(bid)["sunday"])
                db().execute("UPDATE items SET body=CASE WHEN TRIM(body)='' THEN ? ELSE body || char(10) || char(10) || ? END, "
                             "carried=0, updated_by=? WHERE bulletin_id=? AND section_id=?",
                             (text, text, g.user["username"], bid, sec["id"]))
                db().commit()
                flash(f"Added to {sec['title']}. Review and edit it there.", "ok")
        return redirect(url_for("suggestions", bid=bid))

    @app.route("/bulletins/<int:bid>/toc-settings", methods=["POST"])
    @need("admin", "editor")
    def toc_settings(bid):
        open_bulletin(bid)
        try:
            start = max(1, min(12, int(request.form.get("toc_start") or 2)))
            pt = int(request.form.get("toc_pt") or 0)
        except ValueError:
            start, pt = 2, 0
        pt = pt if pt == 0 or 8 <= pt <= 16 else 0
        style = request.form.get("toc_style")
        db().execute("UPDATE bulletins SET toc_title=?, toc_start=?, toc_show=?, toc_pt=?, toc_style=?, toc_place=?, toc_cols=?, toc_title_gap=?, toc_item_gap=? WHERE id=?",
                     (request.form.get("toc_title", "").strip()[:60], start, 1 if request.form.get("toc_show") else 0,
                      pt, style if style in FONT_STYLES else "",
                      request.form.get("toc_place") if request.form.get("toc_place") in ("left", "right", "full") else "left",
                      request.form.get("toc_cols") if request.form.get("toc_cols") in ("1", "2", "3", "4") else 1,
                      gap_value(request.form.get("toc_title_gap")), gap_value(request.form.get("toc_item_gap")), bid))
        db().commit()
        flash("Contents settings saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/no-stragglers", methods=["POST"])
    @need("admin", "editor")
    def no_stragglers(bid):
        open_bulletin(bid)
        db().execute("UPDATE bulletins SET no_stragglers=? WHERE id=?", (1 if request.form.get("no_stragglers") else 0, bid))
        db().commit()
        flash("Straggler setting saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/underlines", methods=["POST"])
    @need("admin", "editor")
    def underlines(bid):
        open_bulletin(bid)
        db().execute("UPDATE bulletins SET no_underline=? WHERE id=?", (1 if request.form.get("no_underline") else 0, bid))
        db().commit()
        flash("Underline setting saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/extra-pages", methods=["POST"])
    @need("admin", "editor")
    def extra_pages(bid):
        open_bulletin(bid)
        cur = db().execute("SELECT extra_pages FROM bulletins WHERE id=?", (bid,)).fetchone()["extra_pages"] or 0
        new = cur + 4 if request.form.get("do") == "add" else max(0, cur - 4)
        db().execute("UPDATE bulletins SET extra_pages=? WHERE id=?", (new, bid))
        db().commit()
        flash(f"{new} extra pages added." if new else "Extra pages removed.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/header-text", methods=["POST"])
    @need("admin", "editor")
    def set_header_text(bid):
        open_bulletin(bid)
        d = request.form.get("hdr_date", "").strip()
        d = d if long_date(d) else ""
        title = " ".join(request.form.get("theme", "").split())[:120]
        db().execute("UPDATE bulletins SET hdr_date=?, theme=? WHERE id=?", (d, title, bid))
        db().commit()
        flash("Header date and title saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/header-photo", methods=["POST"])
    @need("admin", "editor")
    def set_header_photo(bid):
        b = open_bulletin(bid)
        image, alt = b["hdr_image"], request.form.get("hdr_alt", "").strip()[:300] or b["hdr_alt"]
        f = request.files.get("image")
        lib = request.form.get("library_id")
        if request.form.get("reset"):
            image, alt = "", ""
        elif f and f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower()
            if ext not in IMAGE_EXT:
                flash("Images must be PNG, JPG, GIF or WebP.", "error")
                return redirect(url_for("edit", bid=bid))
            if not request.form.get("hdr_alt", "").strip():
                flash("Alternative text is required for the header photo.", "error")
                return redirect(url_for("edit", bid=bid))
            image = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
            f.save(os.path.join(app.config["UPLOAD_DIR"], image))
        elif lib:
            photo = db().execute("SELECT * FROM photos WHERE id=?", (lib,)).fetchone()
            if photo:
                image, alt = photo["filename"], request.form.get("hdr_alt", "").strip()[:300] or photo["alt"] or "Parish photo"
        def num(key, lo, hi, default):
            try:
                return max(lo, min(hi, float(request.form.get(key, default))))
            except ValueError:
                return default
        db().execute("UPDATE bulletins SET hdr_image=?, hdr_alt=?, hdr_h=?, hdr_zoom=?, hdr_x=?, hdr_y=?, hdr_style=? WHERE id=?",
                     (image, alt, num("hdr_h", 0.5, 5, 2.4), num("hdr_zoom", 1, 4, 1), num("hdr_x", 0, 100, 50),
                      num("hdr_y", 0, 100, 50),
                      style if (style := request.form.get("hdr_style")) in ("waves", "swell", "gentle", "straight") else "waves", bid))
        db().commit()
        flash("Header photo saved.", "ok")
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/publish", methods=["POST"])
    @need("admin", "editor")
    def publish(bid):
        status = "draft" if request.form.get("unpublish") else "published"
        db().execute("UPDATE bulletins SET status=? WHERE id=?", (status, bid))
        db().commit()
        return redirect(url_for("edit", bid=bid))

    @app.route("/bulletins/<int:bid>/preview")
    @need()
    def preview(bid):
        b = get_bulletin(bid)
        pages, toc = layout(bid, include_pending=g.user["role"] != "viewer" or b["status"] == "published")
        return render_template("bulletin.html", b=b, pages=pages, toc=toc, foot_addr=footer_address(bid)[0], foot_phone=footer_address(bid)[1], foot_hours=footer_address(bid)[2], cover_dt=g.cover_dt)

    def save_ad_file(f):
        from PIL import Image, ImageOps
        ext = f.filename.rsplit(".", 1)[-1].lower() if f.filename and "." in f.filename else ""
        base = f"{uuid.uuid4().hex}"
        if ext == "pdf":
            try:
                import fitz
                doc = fitz.open(stream=f.read(), filetype="pdf")
                pix = doc[0].get_pixmap(dpi=300)
                name = base + ".png"
                pix.save(os.path.join(app.config["UPLOAD_DIR"], name))
                return name, pix.width, pix.height
            except Exception:
                return None
        if ext not in IMAGE_EXT:
            return None
        try:
            im = ImageOps.exif_transpose(Image.open(f.stream))
            png = im.mode in ("RGBA", "LA", "P")
            name = base + (".png" if png else ".jpg")
            im.save(os.path.join(app.config["UPLOAD_DIR"], name), **({} if png else {"quality": 92}))
            return name, im.width, im.height
        except Exception:
            return None

    @app.route("/ads", methods=["GET", "POST"])
    @need()
    def ads():
        conn = db()
        if request.method == "POST":
            if g.user["role"] not in ("admin", "editor"):
                abort(403)
            added, bad = 0, 0
            for f in request.files.getlist("cards"):
                if not f or not f.filename:
                    continue
                res = save_ad_file(f)
                if not res:
                    bad += 1
                    continue
                nxt = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM ads").fetchone()[0]
                title = os.path.splitext(secure_filename(f.filename))[0].replace("_", " ").replace("-", " ").strip()[:80]
                conn.execute("INSERT INTO ads(name,image,w,h,position,active) VALUES(?,?,?,?,?,1)", (title or "Advertiser", res[0], res[1], res[2], nxt))
                added += 1
            conn.commit()
            flash(f"Added {added} card(s)." + (f" {bad} file(s) could not be read (use JPG, PNG, GIF, WebP or PDF)." if bad else ""), "error" if bad and not added else "ok")
            return redirect(url_for("ads"))
        rows = conn.execute("SELECT * FROM ads ORDER BY position, id").fetchall()
        pg = ad_pages([dict(r) for r in rows if r["active"]])
        return render_template("ads.html", ads=rows, ad_page_count=len(pg))

    @app.route("/ads/save", methods=["POST"])
    @need("admin", "editor")
    def ads_save():
        conn = db()
        for r in conn.execute("SELECT id FROM ads").fetchall():
            i = r["id"]
            try:
                pos = int(request.form.get(f"pos_{i}", "0"))
            except ValueError:
                pos = 0
            conn.execute("UPDATE ads SET name=?, position=?, active=? WHERE id=?",
                         (request.form.get(f"name_{i}", "").strip()[:80] or "Advertiser", pos, 1 if request.form.get(f"active_{i}") else 0, i))
        conn.commit()
        flash("Advertisements saved.", "ok")
        return redirect(url_for("ads"))

    @app.route("/ads/<int:aid>/delete", methods=["POST"])
    @need("admin", "editor")
    def ads_delete(aid):
        conn = db()
        a = conn.execute("SELECT * FROM ads WHERE id=?", (aid,)).fetchone() or abort(404)
        conn.execute("DELETE FROM ads WHERE id=?", (aid,))
        conn.commit()
        try:
            os.remove(os.path.join(app.config["UPLOAD_DIR"], a["image"]))
        except OSError:
            pass
        flash("Card removed.", "ok")
        return redirect(url_for("ads"))

    @app.route("/library", methods=["GET", "POST"])
    @need()
    def library():
        conn = db()
        if request.method == "POST":
            if g.user["role"] not in ("admin", "editor", "contributor"):
                abort(403)
            f = request.files.get("photo")
            title = request.form.get("title", "").strip()
            alt = request.form.get("alt", "").strip()
            decorative = 1 if request.form.get("decorative") else 0
            ext = f.filename.rsplit(".", 1)[-1].lower() if f and f.filename and "." in f.filename else ""
            if ext not in IMAGE_EXT or not title or not (alt or decorative):
                flash("Choose a PNG, JPG, GIF or WebP image and give it a title and alternative text.", "error")
                return redirect(url_for("library"))
            name = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
            f.save(os.path.join(app.config["UPLOAD_DIR"], name))
            conn.execute("INSERT INTO photos(filename,title,alt,tags,uploaded_by,decorative) VALUES(?,?,?,?,?,?)",
                         (name, title, alt, request.form.get("tags", "").strip(), g.user["username"], decorative))
            conn.commit()
            flash("Photo added to the library.", "ok")
            return redirect(url_for("library"))
        q = request.args.get("q", "").strip()
        rows = conn.execute("SELECT * FROM photos WHERE title LIKE ? OR tags LIKE ? ORDER BY title",
                            (f"%{q}%", f"%{q}%")).fetchall()
        return render_template("library.html", photos=rows, q=q)

    @app.route("/library/<int:pid>/delete", methods=["POST"])
    @need("admin", "editor")
    def library_delete(pid):
        conn = db()
        p = conn.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone() or abort(404)
        in_use = conn.execute("SELECT 1 FROM items WHERE image=?", (p["filename"],)).fetchone()
        if in_use:
            flash("That photo is used in a bulletin and cannot be deleted.", "error")
        else:
            conn.execute("DELETE FROM photos WHERE id=?", (pid,))
            conn.commit()
            try:
                os.remove(os.path.join(app.config["UPLOAD_DIR"], p["filename"]))
            except OSError:
                pass
        return redirect(url_for("library"))

    PDF_BASE = "http://pdf.local/"

    def pdf_fetcher(url):
        from weasyprint import default_url_fetcher
        for prefix, folder in ((PDF_BASE + "uploads/", app.config["UPLOAD_DIR"]),
                               (PDF_BASE + "static/", app.static_folder)):
            if url.startswith(prefix):
                path = os.path.realpath(os.path.join(folder, url[len(prefix):]))
                if not path.startswith(os.path.realpath(folder) + os.sep):
                    raise ValueError("blocked path")
                import mimetypes
                return {"file_obj": open(path, "rb"),
                        "mime_type": mimetypes.guess_type(path)[0] or "application/octet-stream"}
        if url.startswith("data:"):
            return default_url_fetcher(url)
        raise ValueError("external resources are not allowed in PDFs")

    @app.route("/bulletins/<int:bid>/download/<kind>")
    @need()
    def download(bid, kind):
        """kind=web: small, tagged (PDF/UA) file for the website. kind=print: 300 dpi tagged file for printing."""
        if kind not in ("web", "print"):
            abort(404)
        b = get_bulletin(bid)
        if g.user["role"] == "viewer" and b["status"] != "published":
            abort(403)
        try:
            from weasyprint import HTML
        except (ImportError, OSError):
            flash("PDF export is not available on this server (WeasyPrint and its system libraries are not installed).", "error")
            return redirect(url_for("edit", bid=bid))
        pages, toc = layout(bid, include_pending=g.user["role"] != "viewer" or b["status"] == "published")
        html = render_template("bulletin.html", b=b, pages=pages, toc=toc, pdf=True, foot_addr=footer_address(bid)[0], foot_phone=footer_address(bid)[1], foot_hours=footer_address(bid)[2], cover_dt=g.cover_dt)
        html = html.replace('src="/uploads/', f'src="{PDF_BASE}uploads/').replace(
            'href="/static/', f'href="{PDF_BASE}static/').replace('src="/static/', f'src="{PDF_BASE}static/')
        opts = ({"dpi": 96, "jpeg_quality": 70} if kind == "web" else {"dpi": 300, "jpeg_quality": 95})
        pdf = HTML(string=html, base_url=PDF_BASE, url_fetcher=pdf_fetcher).write_pdf(
            pdf_variant="pdf/ua-1", optimize_images=True, **opts)
        name = f"St-Peter-Bulletin-{b['sunday']}-{kind}.pdf"
        return app.response_class(pdf, mimetype="application/pdf",
                                  headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.route("/uploads/<path:name>")
    @need()
    def uploads(name):
        return send_from_directory(app.config["UPLOAD_DIR"], name)

    def help_match(q):
        words = {w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2}
        scored = []
        for tid, title, kw, steps in help_content.TOPICS:
            hay = set(re.findall(r"[a-z0-9]+", (kw + " " + title).lower()))
            n = len(words & hay)
            if n:
                scored.append((n, tid))
        scored.sort(reverse=True)
        return [t for _, t in scored[:3]]

    @app.route("/bulletins/<int:bid>/check")
    @need()
    def bulletin_check(bid):
        get_bulletin(bid)
        rows = db().execute("""SELECT s.id sid, s.title, i.body, i.image, i.status, i.pages, i.font_pt, i.item_gap, i.para_gap, i.title_gap, i.min_h, i.hide_title, i.carried
            FROM items i JOIN sections s ON s.id=i.section_id WHERE i.bulletin_id=? ORDER BY i.position""", (bid,)).fetchall()
        issues = []
        for r in rows:
            name = r["title"]
            filled = bool((r["body"] or "").strip() or r["image"])
            if r["status"] == "pending":
                issues.append((r["sid"], name, "Waiting for approval, so it will not print yet.", "An editor should open it and approve it."))
            if filled and (r["pages"] or 1) > 1:
                issues.append((r["sid"], name, "Set to take %d pages. Its text prints only on the first page." % r["pages"], "Set Pages back to 1 unless you really want blank follow-on pages."))
            if r["font_pt"] and r["font_pt"] < 12:
                issues.append((r["sid"], name, "Text size is %s pt, which is hard to read." % r["font_pt"], "Use 12 pt or larger."))
            if max(r["item_gap"] or 0, r["para_gap"] or 0, r["title_gap"] or 0) > 0.4:
                issues.append((r["sid"], name, "A spacing value is very large.", "Use Reset layout to standard on this section."))
            if r["carried"] and filled:
                issues.append((r["sid"], name, "Text was carried over from last week.", "Check dates, names and amounts are updated."))
        return render_template("check.html", b=get_bulletin(bid), issues=issues)

    @app.route("/help", methods=["GET", "POST"])
    @need()
    def help_page():
        conn = db()
        q, found = "", []
        if request.method == "POST":
            q = request.form.get("question", "").strip()[:500]
            if q:
                found = help_match(q)
                conn.execute("INSERT INTO questions(asked_by,question,created) VALUES(?,?,?)",
                             (g.user["username"], q, datetime.now().strftime("%Y-%m-%d %H:%M")))
                conn.commit()
        mine = conn.execute("SELECT * FROM questions WHERE asked_by=? AND answer!='' ORDER BY id DESC LIMIT 10",
                            (g.user["username"],)).fetchall()
        open_q = conn.execute("SELECT * FROM questions WHERE answer='' ORDER BY id DESC LIMIT 50").fetchall() \
            if g.user["role"] in ("admin", "editor") else []
        return render_template("help.html", topics=help_content.TOPICS, q=q, found=found, mine=mine, open_q=open_q)

    @app.route("/help/answer/<int:qid>", methods=["POST"])
    @need("admin", "editor")
    def help_answer(qid):
        ans = request.form.get("answer", "").strip()[:2000]
        if ans:
            db().execute("UPDATE questions SET answer=?, answered_by=? WHERE id=?", (ans, g.user["display_name"] or g.user["username"], qid))
            db().commit()
            flash("Answer saved.", "ok")
        return redirect(url_for("help_page") + "#questions")

    @app.route("/style-guide")
    def style_guide():
        return render_template("style_guide.html", font_styles=FONT_STYLES)

    # ---------- admin ----------
    @app.route("/admin/users", methods=["GET", "POST"])
    @need("admin")
    def users():
        conn = db()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "create":
                role = request.form.get("role")
                name = request.form.get("username", "").strip().lower()
                pw = request.form.get("password", "")
                if role in ROLES and name and len(pw) >= 10:
                    try:
                        cur = conn.execute("""INSERT INTO users(username,display_name,pw_hash,role,must_reset)
                            VALUES(?,?,?,?,1)""", (name, request.form.get("display_name", name),
                                                  generate_password_hash(pw), role))
                        if role == "contributor":
                            for sid in request.form.getlist("sections"):
                                conn.execute("INSERT OR IGNORE INTO user_sections VALUES(?,?)",
                                             (cur.lastrowid, int(sid)))
                    except sqlite3.IntegrityError:
                        flash("That username exists.", "error")
                else:
                    flash("Username, valid role and a 10+ character password are required.", "error")
            elif action == "role":
                uid = int(request.form["uid"])
                if request.form.get("role") in ROLES and uid != g.user["id"]:
                    conn.execute("UPDATE users SET role=? WHERE id=?", (request.form["role"], uid))
                conn.execute("DELETE FROM user_sections WHERE user_id=?", (uid,))
                for sid in request.form.getlist("sections"):
                    conn.execute("INSERT OR IGNORE INTO user_sections VALUES(?,?)", (uid, int(sid)))
            conn.commit()
            return redirect(url_for("users"))
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        secs = conn.execute("SELECT * FROM sections WHERE bulletin_id IS NULL ORDER BY position").fetchall()
        assigned = {r["id"]: {x["section_id"] for x in conn.execute(
            "SELECT section_id FROM user_sections WHERE user_id=?", (r["id"],))} for r in rows}
        return render_template("users.html", users=rows, roles=ROLES, help=ROLE_HELP,
                               secs=secs, assigned=assigned)

    @app.route("/admin/backup")
    @need("admin")
    def backup():
        """One zip with the database and every upload; restore it on any other server to move the site."""
        buf = io.BytesIO()
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "bulletin.db")
            dest = sqlite3.connect(snap)
            db().backup(dest)
            dest.close()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(snap, "bulletin.db")
                for n in os.listdir(app.config["UPLOAD_DIR"]):
                    p = os.path.join(app.config["UPLOAD_DIR"], n)
                    if os.path.isfile(p):
                        z.write(p, "uploads/" + n)
        return app.response_class(buf.getvalue(), mimetype="application/zip", headers={
            "Content-Disposition": f'attachment; filename="bulletin-backup-{date.today().isoformat()}.zip"'})

    @app.route("/admin/restore", methods=["POST"])
    @need("admin")
    def restore():
        f = request.files.get("backup")
        try:
            z = zipfile.ZipFile(io.BytesIO(f.read())) if f and f.filename else None
            if z is None or "bulletin.db" not in z.namelist():
                raise ValueError
            names = [n for n in z.namelist() if n.startswith("uploads/") and not n.endswith("/")]
            if any(".." in n or n.startswith("/") for n in names):
                raise ValueError
            with tempfile.TemporaryDirectory() as tmp:
                snap = os.path.join(tmp, "bulletin.db")
                with open(snap, "wb") as out:
                    out.write(z.read("bulletin.db"))
                check = sqlite3.connect(snap)
                check.execute("SELECT 1 FROM users LIMIT 1")
                check.execute("SELECT 1 FROM bulletins LIMIT 1")
                db().commit()
                check.backup(db())
                check.close()
            for n in names:
                target = os.path.join(app.config["UPLOAD_DIR"], os.path.basename(n))
                with open(target, "wb") as out:
                    out.write(z.read(n))
        except (ValueError, zipfile.BadZipFile, sqlite3.Error):
            flash("That is not a valid bulletin backup file.", "error")
            return redirect(url_for("users"))
        session.clear()
        flash("Backup restored. Please sign in again.", "ok")
        return redirect(url_for("login"))

    @app.route("/admin/sections", methods=["GET", "POST"])
    @need("admin")
    def sections_admin():
        conn = db()
        if request.method == "POST":
            sid = int(request.form["sid"])
            pages = max(1, min(8, int(request.form.get("pages", 1))))
            conn.execute("UPDATE sections SET title=?, pages=?, weekly=?, position=? WHERE id=?",
                         (request.form.get("title", "").strip() or "Untitled", pages,
                          1 if request.form.get("weekly") else 0, int(request.form.get("position", 0)), sid))
            conn.commit()
            return redirect(url_for("sections_admin"))
        return render_template("sections.html",
                               secs=conn.execute("SELECT * FROM sections WHERE bulletin_id IS NULL ORDER BY position").fetchall())

    return app


if __name__ == "__main__":
    create_app().run(debug=False, port=5050)
