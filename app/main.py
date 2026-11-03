"""Handwrite desktop app: a native window (pywebview) around the handwriting renderer.

    python app/main.py          # native window
    python app/main.py --web    # same UI served at http://127.0.0.1:8791 for a browser
"""
import base64
import io
import json
import os
import random
import secrets
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from renderer import engine  # noqa: E402

if getattr(sys, "frozen", False):  # PyInstaller bundle: data files sit next to the executable
    BASE = Path(sys._MEIPASS)
else:
    BASE = HERE
UI_DIR, FONT_DIR = BASE / "ui", BASE / "fonts"

FONTS = [
    ("ReenieBeanie.ttf", "Reenie Beanie", "随手快写"),
    ("Kalam.ttf", "Kalam", "工整略斜"),
    ("EduSABeginner.ttf", "Edu SA", "学校教的标准手写"),
    ("PatrickHand.ttf", "Patrick Hand", "圆润清楚"),
    ("NanumPenScript.ttf", "Nanum Pen", "细笔尖"),
    ("Caveat.ttf", "Caveat", "带点连笔"),
]
PREVIEW_WIDTH = 1240


def new_code():
    """A short handwriting ID like K7F2-9Q; it seeds the writer's letter habits."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(2))


def code_to_seed(code):
    return int.from_bytes(code.encode(), "big") % (2 ** 31)


class Api:
    """Everything the page can call. Each call runs in its own thread, so a render can be in
    progress while the page polls progress()."""

    def __init__(self):
        self.text = ""
        self.pages = []
        self.lock = threading.Lock()
        self.job = 0
        self.progress_pages = 0
        self.window = None

    # ---- document -------------------------------------------------------------------------
    def load_file(self, name, data_b64):
        """Takes a dropped file (base64) and returns its editable text markup."""
        raw = base64.b64decode(data_b64)
        ext = Path(name).suffix.lower()
        if ext == ".docx":
            path = Path(tempfile.gettempdir()) / f"handwrite_{os.getpid()}.docx"
            path.write_bytes(raw)
            blocks = engine.read_docx(str(path))
            text = engine.blocks_to_markup(blocks)
        elif ext == ".pdf":
            import pymupdf
            doc = pymupdf.open(stream=raw, filetype="pdf")
            text = "\n".join(line for page in doc for line in page.get_text().splitlines())
        else:
            text = raw.decode("utf-8", errors="replace")
        self.text = text
        return {"text": text}

    def settings(self):
        return {
            "fonts": [{"file": f, "name": n, "note": c} for f, n, c in FONTS],
            "code": new_code(),
            "seed": random.randrange(1, 10 ** 6),
            "platform": sys.platform,
        }

    def new_code(self):
        return new_code()

    # ---- rendering ------------------------------------------------------------------------
    def render(self, text, opts):
        """Renders the text with the given options. Returns previews of every page, or
        {"stale": true} if a newer render was started meanwhile."""
        with self.lock:
            self.job += 1
            job = self.job
        self.progress_pages = 0
        font = FONT_DIR / opts.get("font", FONTS[0][0])
        o = dict(opts)
        o["habit_seed"] = code_to_seed(o.pop("code", "AAAA-AA"))
        o.pop("font", None)
        blocks = engine.parse_markup(text or "")
        if not any(b[0] != "blank" for b in blocks):
            self.pages = []
            return {"pages": [], "count": 0}

        def progress(n):
            self.progress_pages = n

        pages = engine.render_pages(blocks, str(font), progress=progress, **o)
        if job != self.job:
            return {"stale": True}
        self.text, self.pages = text, pages
        previews = []
        for p in pages:
            small = p.resize((PREVIEW_WIDTH, round(p.height * PREVIEW_WIDTH / p.width)), engine.Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=82)
            previews.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        return {"pages": previews, "count": len(pages)}

    def progress(self):
        return {"pages": self.progress_pages}

    # ---- export ---------------------------------------------------------------------------
    def export_pdf(self, suggested="handwritten.pdf"):
        """Asks where to save and writes the last rendered pages as a PDF."""
        if not self.pages:
            return {"error": "没有可导出的页面"}
        if self.window is None:  # browser mode: hand the bytes back for download
            buf = io.BytesIO()
            engine.save_pdf(self.pages, buf)
            return {"download": "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()}
        import webview
        result = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=suggested,
                                                file_types=("PDF (*.pdf)",))
        if not result:
            return {"cancelled": True}
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        engine.save_pdf(self.pages, path)
        return {"saved": path}

    def export_pngs(self):
        if not self.pages:
            return {"error": "没有可导出的页面"}
        if self.window is None:
            return {"error": "浏览器模式只支持导出 PDF"}
        import webview
        result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"cancelled": True}
        folder = Path(result if isinstance(result, str) else result[0])
        for i, p in enumerate(self.pages, 1):
            p.save(folder / f"page_{i}.png")
        return {"saved": str(folder), "count": len(self.pages)}


# ---- browser mode -----------------------------------------------------------------------------
class WebHandler(SimpleHTTPRequestHandler):
    api = None

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI_DIR), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        name = self.path[5:]
        length = int(self.headers.get("Content-Length", 0))
        args = json.loads(self.rfile.read(length) or b"[]")
        method = getattr(self.api, name, None)
        if method is None or name.startswith("_"):
            self.send_error(404)
            return
        try:
            body = json.dumps(method(*args)).encode()
        except Exception as e:  # noqa: BLE001
            body = json.dumps({"error": str(e)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_web(port=8791):
    WebHandler.api = Api()
    server = ThreadingHTTPServer(("127.0.0.1", port), WebHandler)
    print(f"Handwrite running at http://127.0.0.1:{port}")
    server.serve_forever()


def run_native():
    import webview
    api = Api()
    window = webview.create_window("Handwrite", str(UI_DIR / "index.html"), js_api=api,
                                   width=1280, height=820, min_size=(960, 640),
                                   background_color="#f5f5f7")
    api.window = window
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    if "--web" in sys.argv:
        run_web()
    else:
        run_native()
