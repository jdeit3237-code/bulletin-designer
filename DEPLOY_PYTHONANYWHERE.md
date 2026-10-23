# Hosting the Bulletin Designer on PythonAnywhere

Host this app on its own PythonAnywhere web app. Free accounts allow one web app; a paid plan is needed if you run more than one. Replace `YOURUSERNAME` throughout.

## 1. Get the code
In a Bash console:
```bash
git clone https://github.com/jdeit3237-code/bulletin-designer.git ~/bulletin-designer
mkdir -p ~/bulletin_data/uploads
```

## 2. Virtualenv and dependencies
```bash
mkvirtualenv bulletin --python=python3.10
pip install -r ~/bulletin-designer/requirements.txt
```
WeasyPrint creates the accessible (PDF/UA tagged) PDFs. PythonAnywhere images normally include Pango; if a PDF download reports that export is unavailable, run `python -c "import weasyprint"` in the console and send the error to PythonAnywhere support or this project.

## 3. Create the web app
Web tab > **Add a new web app** > **Manual configuration** > Python 3.10. Then set:
- Source code: `/home/YOURUSERNAME/bulletin-designer`
- Virtualenv: `/home/YOURUSERNAME/.virtualenvs/bulletin`
- Force HTTPS: **on**

## 4. WSGI file
Replace the file contents with:
```python
import os, sys
sys.path.insert(0, '/home/YOURUSERNAME/bulletin-designer')
os.environ['SECRET_KEY'] = 'paste-a-long-random-string'      # python -c "import secrets;print(secrets.token_hex(32))"
os.environ['BULLETIN_DB'] = '/home/YOURUSERNAME/bulletin_data/bulletin.db'
os.environ['BULLETIN_UPLOADS'] = '/home/YOURUSERNAME/bulletin_data/uploads'
os.environ['BULLETIN_ADMIN_PASSWORD'] = 'choose-a-strong-temporary-password'
# Optional natural-language AI assistant (OpenAI-compatible):
# os.environ['BULLETIN_AI_URL'] = 'https://api.openai.com/v1/chat/completions'
# os.environ['BULLETIN_AI_KEY'] = '...'
from bulletin_designer.app import create_app
application = create_app()
```
Keep `SECRET_KEY` fixed; otherwise everyone is signed out on each restart. The data lives in `~/bulletin_data`, outside the code folder, so updating the code never overwrites it.

## 5. First login
Reload the web app and open `https://YOURUSERNAME.pythonanywhere.com/`. Sign in as `admin` with the temporary password; you will be forced to change it. Then add editors and contributors under **Users**.

## 6. Updating and backups
- Update: `cd ~/bulletin-designer && git pull`, then press **Reload** on the Web tab.
- Back up `~/bulletin_data` regularly (download `bulletin.db` and `uploads/` from the Files tab).
- Free-tier files and outbound internet are limited; the optional AI assistant needs outbound access to your provider.

## Moving to another host later
1. Admin > Users > **Download backup (.zip)** (database, users, photos, documents in one file).
2. Deploy the app on the new host (any Python 3.9+ host; set `SECRET_KEY`, `BULLETIN_DB`, `BULLETIN_UPLOADS`, install `weasyprint`).
3. Sign in with the temporary admin password, then Admin > Users > **Restore backup** and choose the zip. Everything, including accounts and photos, is restored.
All settings are environment variables and nothing depends on PythonAnywhere, so the same code runs on Railway, Render, a VPS, or Docker.
