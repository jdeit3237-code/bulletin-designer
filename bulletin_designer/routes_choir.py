"""Choir sheet-music tab: weekly packets from a Liturgy.com outline, plus a simple Find Music page."""
import io
import os
import uuid
from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

try:
    from . import choir
except ImportError:  # run as a script
    import choir


def register(app, need, need_area):
    manage = lambda fn: need("admin", "editor")(fn)  # noqa: E731

    @app.after_request
    def _choir_private(response):
        if request.endpoint and request.endpoint.startswith("choir_"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def share_message(meta):
        sunday = meta.get("sunday")
        label = date.fromisoformat(sunday).strftime("%B %d").replace(" 0", " ") if sunday else meta.get("title", "")
        return f"Sheet music for {label}: " + url_for("choir_download", token=meta["token"], _external=True)

    @app.route("/choir")
    @manage
    def choir_index():
        packets = choir.list_packets(current_app)
        today = date.today().isoformat()
        for p in packets:
            p["share"] = share_message(p)
            p["live"] = not p.get("revoked") and p["expires"] >= today
        return render_template("choir_index.html", packets=packets)

    @app.route("/choir/outline", methods=["POST"])
    @manage
    def choir_outline():
        text = (request.form.get("outline_text") or "").strip()
        upload = request.files.get("outline_pdf")
        if upload and upload.filename:
            try:
                text = choir.extract_pdf_text(upload.stream)
            except Exception:
                flash("That PDF could not be read. Try pasting the outline text instead.", "error")
                return redirect(url_for("choir_index"))
        if not text:
            flash("Upload the outline PDF or paste its text.", "error")
            return redirect(url_for("choir_index"))
        parsed = choir.parse_outline(text)
        if not parsed["songs"]:
            flash("No songs were found in that outline.", "error")
            return redirect(url_for("choir_index"))
        songs = choir.match_songs(choir.load_library(current_app), parsed["songs"], parsed["header"]["date"])
        prev = choir.previous_packet(current_app, parsed["header"]["date"])
        changes = None
        if prev:
            added, removed = choir.diff_songs(prev["songs"], songs)
            changes = {"added": added, "removed": removed}
            added_keys = {choir._song_key(s) for s in added}
            for song in songs:
                song["changed"] = choir._song_key(song) in added_keys
        return redirect(url_for("choir_draft", draft_id=choir.save_draft(current_app, parsed["header"], songs, changes)))

    @app.route("/choir/draft/<draft_id>")
    @manage
    def choir_draft(draft_id):
        data = choir.load_draft(current_app, draft_id)
        if not data:
            abort(404)
        return render_template("choir_draft.html", draft_id=draft_id, **data)

    @app.route("/choir/draft/<draft_id>/build", methods=["POST"])
    @manage
    def choir_build(draft_id):
        data = choir.load_draft(current_app, draft_id)
        if not data:
            abort(404)
        for i, song in enumerate(data["songs"]):
            song["include"] = request.form.get(f"include_{i}") == "1" and bool(song["primary"])
            song["guitar_on"] = request.form.get(f"guitar_{i}", "1") == "1"
        pdf, missing = choir.build_packet(current_app, data["header"], data["songs"], request.form.get("include_guitar") == "1")
        choir.publish_packet(current_app, data["header"], pdf, data["songs"])
        if missing:
            flash("Left out (not in the library): " + "; ".join(missing), "error")
        flash("Packet ready. Copy the link below into your Liturgy.com message.")
        return redirect(url_for("choir_index"))

    @app.route("/choir/packet/<token>/revoke", methods=["POST"])
    @manage
    def choir_revoke(token):
        if choir.revoke_packet(current_app, token):
            flash("Link revoked.")
        return redirect(url_for("choir_index"))

    @app.route("/choir/get/<token>")
    def choir_download(token):
        """Unguessable, expiring link shared with the choir; no login needed."""
        found = choir.get_packet(current_app, token)
        if not found:
            abort(404)
        meta, path = found
        return send_file(path, mimetype="application/pdf", download_name=f"St-Peter-Choir-{meta.get('sunday') or 'packet'}.pdf")

    @app.route("/choir/find")
    @need()
    @need_area("choir")
    def choir_find():
        q, book_id = request.args.get("q", "").strip(), request.args.get("book", "")
        lib = choir.load_library(current_app)
        searched = bool(q or book_id)
        results = choir.search_library(current_app, q, book_id) if searched else []
        return render_template("choir_find.html", books=lib["books"], q=q, book_id=book_id, searched=searched, results=results)

    @app.route("/choir/find/download/<book_id>/<int:idx>")
    @need()
    @need_area("choir")
    def choir_find_download(book_id, idx):
        found = choir.extract_song(current_app, book_id, idx)
        if not found:
            abort(404)
        pdf, name = found
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=name)

    @app.route("/choir/library")
    @manage
    def choir_library():
        books = []
        for b in choir.load_library(current_app)["books"]:
            b = dict(b)
            b["entries_text"] = choir.entries_to_text(b.get("entries", []))
            b["file_ok"] = os.path.exists(choir.book_path(current_app, b))
            books.append(b)
        return render_template("choir_library.html", books=books, editions=choir.EDITIONS)

    @app.route("/choir/library/add", methods=["POST"])
    @manage
    def choir_library_add():
        name, code = request.form.get("name", "").strip(), request.form.get("code", "").strip().upper()
        edition = request.form.get("edition", "piano")
        if not name or edition not in choir.EDITIONS:
            flash("Book name and edition are required.", "error")
            return redirect(url_for("choir_library"))
        try:
            offset = int(request.form.get("page_offset") or 0)
        except ValueError:
            flash("Page offset must be a whole number.", "error")
            return redirect(url_for("choir_library"))
        entries, errors = choir.parse_entries(request.form.get("entries", ""))
        if errors:
            flash(" ".join(errors), "error")
            return redirect(url_for("choir_library"))
        folder = os.path.join(choir.library_dir(current_app), "books")
        upload = request.files.get("pdf")
        existing = secure_filename(request.form.get("existing_file", ""))
        if upload and upload.filename:
            if not upload.filename.lower().endswith(".pdf"):
                flash("Only PDF files are accepted.", "error")
                return redirect(url_for("choir_library"))
            filename = f"{uuid.uuid4().hex[:8]}-{secure_filename(upload.filename)}"
            upload.save(os.path.join(folder, filename))
        elif existing and os.path.exists(os.path.join(folder, existing)):
            filename = existing
        else:
            flash("Upload a PDF, or name a file already placed in the books folder.", "error")
            return redirect(url_for("choir_library"))
        lib = choir.load_library(current_app)
        lib["books"].append({"id": uuid.uuid4().hex[:8], "name": name, "code": code, "edition": edition,
                             "file": filename, "page_offset": offset, "entries": entries})
        choir.save_library(current_app, lib)
        flash(f"Added {name} ({edition}).")
        return redirect(url_for("choir_library"))

    @app.route("/choir/library/<book_id>/entries", methods=["POST"])
    @manage
    def choir_library_entries(book_id):
        lib = choir.load_library(current_app)
        book = next((b for b in lib["books"] if b["id"] == book_id), None)
        if not book:
            abort(404)
        entries, errors = choir.parse_entries(request.form.get("entries", ""))
        if errors:
            flash(" ".join(errors), "error")
            return redirect(url_for("choir_library"))
        try:
            book["page_offset"] = int(request.form.get("page_offset") or 0)
        except ValueError:
            flash("Page offset must be a whole number.", "error")
            return redirect(url_for("choir_library"))
        book["entries"] = entries
        choir.save_library(current_app, lib)
        flash(f"Saved {len(entries)} entries for {book['name']}.")
        return redirect(url_for("choir_library"))

    @app.route("/choir/library/<book_id>/delete", methods=["POST"])
    @manage
    def choir_library_delete(book_id):
        lib = choir.load_library(current_app)
        book = next((b for b in lib["books"] if b["id"] == book_id), None)
        if not book:
            abort(404)
        lib["books"].remove(book)
        choir.save_library(current_app, lib)
        path = choir.book_path(current_app, book)
        if os.path.exists(path):
            os.remove(path)
        flash("Book removed.")
        return redirect(url_for("choir_library"))
