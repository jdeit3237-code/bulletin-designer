# St. Peter the Fisherman Bulletin Designer

Standalone Flask app for building the weekly parish bulletin (web + print, Letter, multiples of 4 pages).

## Run
```
pip install -r requirements.txt
BULLETIN_ADMIN_PASSWORD=choose-one python3 bulletin_designer/app.py   # http://localhost:5050
```
Without `BULLETIN_ADMIN_PASSWORD` a one-time admin password is printed at first start; the admin must change it at first login.

## Features
- **Sections from the Oct 2026 bulletins**: cover, staff, welcome, This Week in Our Parish (calendar table), Mass schedule/intentions/ministers (submission form), readings, pastor, prayer list, announcements, food pantry, ministries, sacraments, giving, sponsors.
- **Standing vs. weekly**: standing sections (staff, welcome, pantry...) carry forward and layout is remembered; weekly sections start empty and are flagged "Needs this week's content".
- **Extra sections each week**: add, reorder, set pages and 1/2 columns per section; "Remove" for week-only sections.
- **User levels**: admin, editor, contributor (assigned sections, needs approval), viewer.
- **Assistant**: type a request (add section, move, columns/pages, remove). Changes are previewed and applied only on confirmation. Set `BULLETIN_AI_URL` (OpenAI-compatible chat endpoint), `BULLETIN_AI_KEY`, `BULLETIN_AI_MODEL` for natural-language AI; bulletin titles and your request are then sent to that provider. Without them a built-in offline parser handles basic commands.
- **Document upload**: .docx, .doc (needs antiword or macOS textutil on the server) and .txt files fill a section's text box for review.
- **Photo library**: upload photos once (title, alt text, tags), search them, and pick them in any section; in-use photos cannot be deleted.
- **Print/PDF**: Preview > "Print or save as PDF" (Letter, headers/footers off). TOC on the cover, page numbers, overflow warnings, images never distorted, alt text required, 12pt minimum font.

See the in-app **Style guide** page. Tests: `python3 -m pytest bulletin_designer/tests` from the repo root.
- **PDF downloads**: Web PDF (96 dpi, tagged PDF/UA) and Print PDF (300 dpi), via WeasyPrint (needs Pango; see DEPLOY_PYTHONANYWHERE.md).
- **Backup / move**: Admin > Users downloads one zip (database + uploads) that restores on any other server.
