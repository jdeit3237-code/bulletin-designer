"""Choir sheet-music tab: weekly packets from a Liturgy.com outline, plus a simple Find Music page."""
import io
import os
import re
import uuid
from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from pypdf import PdfReader
from werkzeug.utils import secure_filename

try:
    from . import choir, lyric_check
except ImportError:  # run as a script
    import choir
    import lyric_check


def register(app, need, need_area):
    manage = lambda fn: need("admin", "editor")(fn)  # noqa: E731

    @app.after_request
    def _choir_private(response):
        if request.endpoint and request.endpoint.startswith("choir_"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["Cache-Control"] = "private, no-store"
        return response

    edition_names = dict(choir.EDITIONS)

    def share_message(packets):
        """One line per instrument, each with its own link."""
        lines = []
        for meta in packets:
            sunday = meta.get("sunday")
            label = date.fromisoformat(sunday).strftime("%B %d").replace(" 0", " ") if sunday else meta.get("title", "")
            kind = edition_names.get(meta.get("edition"), "")
            head = f"{kind} sheet music for {label}" if kind else f"Sheet music for {label}"
            lines.append(f"{head}: " + url_for("choir_download", token=meta["token"], _external=True))
        return "\n".join(lines)

    @app.route("/choir")
    @manage
    def choir_index():
        packets = choir.list_packets(current_app)
        today = date.today().isoformat()
        order = [e for e, _ in choir.EDITIONS]
        groups = {}
        for p in packets:
            p["live"] = not p.get("revoked") and p["expires"] >= today
            p["edition_name"] = edition_names.get(p.get("edition"), "")
            groups.setdefault(p.get("sunday") or p["token"], []).append(p)
        weeks = []
        for key, items in groups.items():
            items.sort(key=lambda x: order.index(x["edition"]) if x.get("edition") in order else -1)
            live = [x for x in items if x["live"]]
            weeks.append({"title": items[0].get("title", ""), "sunday": items[0].get("sunday"), "packets": items,
                          "share": share_message(live) if live else ""})
        return render_template("choir_index.html", weeks=weeks)

    @app.route("/choir/outline", methods=["POST"])
    @manage
    def choir_outline():
        text = (request.form.get("outline_text") or "").strip()
        upload = request.files.get("outline_pdf")
        outline_pdf = None
        if upload and upload.filename:
            try:
                outline_pdf = upload.stream.read()
                text = choir.extract_pdf_text(io.BytesIO(outline_pdf))
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
        songs = choir.match_songs(choir.load_library(current_app), parsed["songs"], parsed["header"]["date"],
                                  "other" if request.form.get("language") == "other" else "english")
        prev = choir.previous_packet(current_app, parsed["header"]["date"])
        changes = None
        if prev:
            added, removed = choir.diff_songs(prev["songs"], songs)
            changes = {"added": added, "removed": removed}
            added_keys = {choir._song_key(s) for s in added}
            for song in songs:
                song["changed"] = choir._song_key(song) in added_keys
        return redirect(url_for("choir_draft", draft_id=choir.save_draft(current_app, parsed["header"], songs, changes, outline_pdf)))

    @app.route("/choir/draft/<draft_id>")
    @manage
    def choir_draft(draft_id):
        data = choir.load_draft(current_app, draft_id)
        if not data:
            abort(404)
        results = {i: lyric_check.check_song(current_app, s) for i, s in enumerate(data["songs"]) if s.get("primary")}
        mismatches = sum(1 for r in results.values() if r["status"] == "review")
        return render_template("choir_draft.html", draft_id=draft_id, checks=results, mismatches=mismatches, **data)

    @app.route("/choir/draft/<draft_id>/build", methods=["POST"])
    @manage
    def choir_build(draft_id):
        data = choir.load_draft(current_app, draft_id)
        if not data:
            abort(404)
        for i, song in enumerate(data["songs"]):
            song["include"] = request.form.get(f"include_{i}") == "1" and bool(song["primary"])
            song["guitar_on"] = request.form.get(f"guitar_{i}", "1") == "1"
            song["solo_on"] = request.form.get(f"solo_{i}", "1") == "1"
        _, blocking = lyric_check.check_week(current_app, data["songs"])
        if blocking and request.form.get("override") != "1":
            flash("Not published: lyrics may not match the music for " + "; ".join(
                f"{b['title']} ({b['detail']})" for b in blocking), "error")
            return redirect(url_for("choir_draft", draft_id=draft_id))
        built = []
        outline_pdf = None
        if os.path.exists(choir.draft_outline_path(current_app, draft_id)):
            with open(choir.draft_outline_path(current_app, draft_id), "rb") as fh:
                outline_pdf = fh.read()
        for edition, name in choir.EDITIONS:
            pdf, missing = choir.build_packet(current_app, data["header"], data["songs"], False, edition=edition, outline_pdf=outline_pdf)
            if pdf is None:
                flash(f"No {name} pages found for this week, so no {name} packet was made.", "error")
                continue
            choir.publish_packet(current_app, data["header"], pdf, data["songs"], edition=edition)
            built.append(name)
            if missing:
                flash(f"{name}: left out (not in that book): " + "; ".join(missing), "error")
        if built:
            flash("Ready: " + ", ".join(built) + ". Copy the message below into your Liturgy.com email.")
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
        return send_file(path, mimetype="application/pdf", download_name=f"St-Peter-Choir-{meta.get('sunday') or 'packet'}{'-' + meta['edition'] if meta.get('edition') else ''}.pdf")

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
        lib = choir.load_library(current_app)
        generated = []
        library_changed = False
        for book in lib["books"]:
            if book.get("entries"):
                continue
            path = choir.book_path(current_app, book)
            if not os.path.exists(path):
                continue
            try:
                entries = choir.generate_book_entries(path, book.get("code", ""), book.get("name", ""))
            except Exception as exc:
                current_app.logger.warning("Could not generate choir book entries for %s: %s", book.get("file"), exc)
                continue
            if entries and not book.get("entries"):
                book["entries"] = entries
                book["index_version"] = choir.BOOK_INDEX_VERSION
                generated.append(f"{book['name']} ({len(entries)})")
                library_changed = True
        if generated:
            flash("Generated entries from the PDF for " + ", ".join(generated) + ".")
        if library_changed:
            choir.save_library(current_app, lib)
        books = []
        for b in lib["books"]:
            b = dict(b)
            b["entries_text"] = choir.entries_to_text(b.get("entries", []))
            b["file_ok"] = os.path.exists(choir.book_path(current_app, b))
            books.append(b)
        return render_template("choir_library.html", books=books, editions=choir.EDITIONS)

    @app.route("/choir/library/add", methods=["GET", "POST"])
    @manage
    def choir_library_add():
        if request.method == "GET":
            flash("The previous upload request ended. Check the library before uploading the book again.", "error")
            return redirect(url_for("choir_library"))
        name, code = request.form.get("name", "").strip(), request.form.get("code", "").strip().upper()
        edition = request.form.get("edition", "piano")
        if not name or edition not in dict(choir.EDITIONS):
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
            path = os.path.join(folder, filename)
            upload.save(path)
        elif existing and os.path.exists(os.path.join(folder, existing)):
            filename = existing
            path = os.path.join(folder, filename)
        else:
            flash("Upload a PDF, or name a file already placed in the books folder.", "error")
            return redirect(url_for("choir_library"))
        generated = False
        if not entries:
            try:
                entries = choir.generate_book_entries(path, code, name)
                generated = bool(entries)
            except Exception as exc:
                current_app.logger.warning("Could not generate choir book entries for %s: %s", filename, exc)
        lib = choir.load_library(current_app)
        lib["books"].append({"id": uuid.uuid4().hex[:8], "name": name, "code": code, "edition": edition,
                             "file": filename, "page_offset": offset, "entries": entries,
                             "index_version": choir.BOOK_INDEX_VERSION})
        choir.save_library(current_app, lib)
        if generated:
            flash(f"Added {name} ({edition}) with {len(entries)} entries read from the PDF.")
        elif entries:
            flash(f"Added {name} ({edition}).")
        else:
            flash(f"Added {name} ({edition}), but no entries were found automatically. Add them below.", "error")
        return redirect(url_for("choir_library"))

    @app.route("/choir/library/song", methods=["POST"])
    @manage
    def choir_library_song():
        """Store one song PDF as a one-entry book so normal matching and search apply."""
        title = request.form.get("title", "").strip()
        code = request.form.get("code", "").strip().upper()
        number = request.form.get("number", "").strip()
        accompaniment_number = request.form.get("accompaniment_number", "").strip()
        edition = request.form.get("edition", "keyboard")
        upload = request.files.get("pdf")
        if not title or edition not in dict(choir.EDITIONS):
            flash("Song title and edition are required.", "error")
            return redirect(url_for("choir_library"))
        if number and not number.isdigit():
            flash("BB/MI number must contain digits only.", "error")
            return redirect(url_for("choir_library"))
        if accompaniment_number and not accompaniment_number.isdigit():
            flash("Accompaniment number must contain digits only.", "error")
            return redirect(url_for("choir_library"))
        if not upload or not upload.filename or not upload.filename.lower().endswith(".pdf"):
            flash("Upload the song as a PDF.", "error")
            return redirect(url_for("choir_library"))

        folder = os.path.join(choir.library_dir(current_app), "books")
        filename = f"{uuid.uuid4().hex[:8]}-{secure_filename(upload.filename)}"
        path = os.path.join(folder, filename)
        upload.save(path)
        try:
            pages = len(PdfReader(path).pages)
        except Exception as exc:
            current_app.logger.warning("Could not read single-song PDF %s: %s", filename, exc)
            os.remove(path)
            flash("That PDF could not be read.", "error")
            return redirect(url_for("choir_library"))
        if not pages:
            os.remove(path)
            flash("That PDF has no pages.", "error")
            return redirect(url_for("choir_library"))

        lib = choir.load_library(current_app)
        catalog_numbers = {}
        prefix = "".join(re.findall(r"^[A-Z]+", code))
        if number and prefix:
            catalog_numbers[prefix] = number
            if prefix == "BB":
                catalog_numbers["MI"] = number
        if accompaniment_number:
            catalog_numbers["ACCOMP"] = accompaniment_number
        entry = {
            "number": accompaniment_number or number or "S",
            "title": title,
            "start": 1,
            "end": pages,
        }
        if catalog_numbers:
            entry["catalog_numbers"] = catalog_numbers
        prefix = re.match(r"^[A-Z]+", code)
        prefix = prefix.group(0) if prefix else ""

        def same_override(book):
            if not (book.get("single_song") or book.get("single")) or book.get("edition") != edition:
                return False
            if choir._norm(book.get("code")) != choir._norm(code):
                return False
            existing = (book.get("entries") or [{}])[0]
            if number:
                return existing.get("catalog_numbers", {}).get(prefix) == number \
                    or (not prefix and existing.get("number") == number)
            return choir._words(existing.get("title")) == choir._words(title)

        existing_book = next((book for book in lib["books"] if same_override(book)), None)
        old_path = choir.book_path(current_app, existing_book) if existing_book else ""
        replacement = {
            "name": title,
            "code": code,
            "edition": edition,
            "file": filename,
            "page_offset": 0,
            "entries": [entry],
            "index_version": choir.BOOK_INDEX_VERSION,
            "single": True,
            "single_song": True,
        }
        if existing_book:
            existing_book.update(replacement)
        else:
            replacement["id"] = uuid.uuid4().hex[:8]
            lib["books"].append(replacement)
        choir.save_library(current_app, lib)
        if old_path and old_path != path and os.path.exists(old_path):
            os.remove(old_path)
        action = "Replaced" if existing_book else "Added"
        flash(f"{action} {title} ({edition}) with {pages} page{'s' if pages != 1 else ''}.")
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
