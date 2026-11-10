"""Handwrite desktop app: a native window (pywebview) around the handwriting renderer.

    python app/main.py          # native window
    python app/main.py --web    # same UI served at http://127.0.0.1:8791 for a browser

The page talks to Python over a local HTTP server in both modes, so previews are plain image
URLs instead of megabytes pushed through the JavaScript bridge. Rendering runs in a separate
process so the window never freezes while the pen is busy.
"""
import base64
import io
import json
import multiprocessing
import os
import random
import secrets
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from renderer import engine  # noqa: E402

if getattr(sys, "frozen", False):  # PyInstaller bundle: data files sit in the unpacked folder
    BASE = Path(sys._MEIPASS)
else:
    BASE = HERE
UI_DIR, FONT_DIR = BASE / "ui", BASE / "fonts"
WORK_DIR = Path(tempfile.gettempdir()) / f"handwrite-{os.getpid()}"


def config_path():
    """Where the last-used options and handwriting ID are kept between launches."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "Handwrite" / "settings.json"


def load_config():
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


FONTS = [
    ("ReenieBeanie.ttf", "Reenie Beanie", "随手快写"),
    ("Kalam.ttf", "Kalam", "工整略斜"),
    ("EduSABeginner.ttf", "Edu SA", "学校教的标准手写"),
    ("PatrickHand.ttf", "Patrick Hand", "圆润清楚"),
    ("NanumPenScript.ttf", "Nanum Pen", "细笔尖"),
    ("Caveat.ttf", "Caveat", "带点连笔"),
    ("IndieFlower.ttf", "Indie Flower", "圆圆的大字"),
    ("GochiHand.ttf", "Gochi Hand", "粗马克笔"),
    ("ShadowsIntoLight.ttf", "Shadows", "瘦长快写"),
    ("CoveredByYourGrace.ttf", "Grace", "潦草小字"),
]
# Chinese hands (SIL OFL fonts); the first is the default
ZH_FONTS = [
    ("LongCang.ttf", "龙藏体", "行楷钢笔字"),
    ("ZhiMangXing.ttf", "智莽行", "快写行书"),
    ("ZCOOLKuaiLe.ttf", "站酷快乐体", "圆圆的大字"),
]
PREVIEW_WIDTH = 1240


def new_code():
    """A short handwriting ID like K7F2-9Q; it seeds the writer's letter habits."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(2))


def clean_code(code):
    """Accepts a typed handwriting ID: trimmed, upper-cased, at most 16 characters."""
    return "".join(str(code or "").split()).upper()[:16]


def code_to_seed(code):
    return int.from_bytes(code.encode(), "big") % (2 ** 31)


def render_job(text, opts, font, out_dir):
    """Runs in the worker process: renders every page into out_dir as page_N.png (full size,
    for export) and page_N.jpg (preview). Returns the page count."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    blocks = engine.parse_markup(text or "")
    if not any(b[0] != "blank" for b in blocks):
        return {"count": 0, "missing": []}
    missing = engine.missing_chars(blocks, font, opts.get("zh_font"))
    count = 0

    def save(page, n):
        page.save(out / f"page_{n}.png")
        small = page.resize((PREVIEW_WIDTH, round(page.height * PREVIEW_WIDTH / page.width)),
                            engine.Image.Resampling.LANCZOS)
        small.save(out / f"page_{n}.tmp", "JPEG", quality=82)
        os.replace(out / f"page_{n}.tmp", out / f"page_{n}.jpg")  # appears only once complete

    # finish each page as soon as it is drawn, so the window can show progress
    pages = []
    original_close = engine.finish

    def finish_and_save(*a, **kw):
        nonlocal count
        page = original_close(*a, **kw)
        count += 1
        save(page, count)
        pages.append(page)
        return page

    engine.finish = finish_and_save
    try:
        engine.render_pages(blocks, font, **opts)
    finally:
        engine.finish = original_close
    return {"count": count, "missing": missing}


class Api:
    """Everything the page can call, over HTTP (and the two dialogs over the JS bridge).

    State is kept in underscore attributes on purpose: pywebview walks every public attribute
    of the js_api object, and walking into the window from its own thread can hang the app.
    """

    def __init__(self):
        self._text = ""
        self._job = 0
        self._job_dir = None   # the render in progress (for the progress counter)
        self._done_dir = None  # the last render that finished; exports read only from here
        self._pages = 0
        self._lock = threading.Lock()
        self._window = None
        self._pool = ProcessPoolExecutor(max_workers=1)
        WORK_DIR.mkdir(parents=True, exist_ok=True)

    # ---- document -------------------------------------------------------------------------
    def load_file(self, name, data_b64):
        """Takes a dropped file (base64) and returns its editable text markup."""
        raw = base64.b64decode(data_b64)
        ext = Path(name).suffix.lower()
        if ext == ".docx":
            path = WORK_DIR / "upload.docx"
            path.write_bytes(raw)
            text = engine.blocks_to_markup(engine.read_docx(str(path)))
        elif ext == ".pdf":
            text = engine.read_pdf(raw)
            if not text.strip():
                return {"error": "这个 PDF 里没有可选中的文字（可能是扫描件或图片），请换 Word 或文本文件"}
        else:
            # Chinese .txt files saved on Windows are often GBK rather than UTF-8
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("utf-8", errors="replace")
        self._text = text
        return {"text": text}

    def settings(self):
        saved = load_config()
        code = clean_code(saved.get("code")) or new_code()
        if not saved.get("code"):  # keep the first ID from the start, so it never changes by itself
            self.save_settings({"code": code, "options": saved.get("options") or {}})
        return {
            "fonts": [{"file": f, "name": n, "note": c} for f, n, c in FONTS],
            "zh_fonts": [{"file": f, "name": n, "note": c} for f, n, c in ZH_FONTS],
            "code": code,
            "options": saved.get("options") if isinstance(saved.get("options"), dict) else {},
            "seed": random.randrange(1, 10 ** 6),
            "platform": sys.platform,
            "native": self._window is not None,
        }

    def save_settings(self, data):
        """Remembers the handwriting ID and the chosen options for the next launch."""
        data = data if isinstance(data, dict) else {}
        out = {"code": clean_code(data.get("code")),
               "options": data.get("options") if isinstance(data.get("options"), dict) else {}}
        path = config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return {"saved": False}
        return {"saved": True}

    def new_code(self):
        return {"code": new_code()}

    def check_code(self, code):
        return {"code": clean_code(code)}

    # ---- rendering ------------------------------------------------------------------------
    def render(self, text, opts):
        """Renders in the worker process; returns preview URLs, or {"stale": true} if a newer
        render was requested while this one ran."""
        self._job += 1
        job = self._job
        job_dir = WORK_DIR / f"job{job}"
        font = FONT_DIR / opts.get("font", FONTS[0][0])
        o = dict(opts)
        o["habit_seed"] = code_to_seed(clean_code(o.pop("code", "")) or "AAAA-AA")
        o.pop("font", None)
        zh = o.get("zh_font")
        o["zh_font"] = str(FONT_DIR / (zh if zh in {f for f, _, _ in ZH_FONTS} else ZH_FONTS[0][0]))
        self._job_dir = job_dir
        result = self._pool.submit(render_job, text, o, str(font), str(job_dir)).result()
        count = result["count"]
        with self._lock:  # exports hold this lock, so they never read a folder being removed
            if job != self._job:
                shutil.rmtree(job_dir, ignore_errors=True)
                return {"stale": True}
            # the previous job's files are no longer needed
            for old in WORK_DIR.glob("job*"):
                if old != job_dir:
                    shutil.rmtree(old, ignore_errors=True)
            self._text, self._pages, self._done_dir = text, count, job_dir
        stamp = int(time.time() * 1000)
        return {"count": count, "missing": result["missing"],
                "pages": [f"/preview/job{job}/page_{n}.jpg?t={stamp}" for n in range(1, count + 1)]}

    def progress(self):
        if self._job_dir is None or not self._job_dir.exists():
            return {"pages": 0}
        return {"pages": len(list(self._job_dir.glob("page_*.jpg")))}

    def _page_images(self):
        """Loads the finished pages into memory (under the lock, so a render that finishes
        meanwhile can't delete them half-read)."""
        from PIL import Image
        with self._lock:
            if not self._pages or self._done_dir is None:
                raise RuntimeError("没有可导出的页面")
            return [Image.open(self._done_dir / f"page_{n}.png").convert("RGB")
                    for n in range(1, self._pages + 1)]

    # ---- export ---------------------------------------------------------------------------
    def pdf_bytes(self):
        buf = io.BytesIO()
        engine.save_pdf(self._page_images(), buf)
        return buf.getvalue()

    def export_pdf(self, suggested="handwritten.pdf"):
        """Native mode: asks where to save and writes the PDF. (Browser mode downloads /export.pdf.)"""
        if not self._pages:
            return {"error": "没有可导出的页面"}
        if self._window is None:
            return {"download": "/export.pdf"}
        import webview
        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=suggested,
                                                file_types=("PDF (*.pdf)",))
        if not result:
            return {"cancelled": True}
        path = result if isinstance(result, str) else result[0]
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        engine.save_pdf(self._page_images(), path)
        return {"saved": path}

    def export_pngs(self):
        if not self._pages:
            return {"error": "没有可导出的页面"}
        if self._window is None:
            return {"error": "浏览器模式只支持导出 PDF"}
        import webview
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"cancelled": True}
        folder = Path(result if isinstance(result, str) else result[0])
        with self._lock:
            for n in range(1, self._pages + 1):
                shutil.copy(self._done_dir / f"page_{n}.png", folder / f"page_{n}.png")
            return {"saved": str(folder), "count": self._pages}


# ---- local server -----------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    api = None

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI_DIR), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean.startswith("/preview/"):
            return str(WORK_DIR / clean[len("/preview/"):])
        return super().translate_path(path)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/export.pdf":
            try:
                body = self.api.pdf_bytes()
            except Exception as e:  # noqa: BLE001
                self.send_error(500, str(e))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="handwritten.pdf"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

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


def start_server(api, port=0):
    Handler.api = api
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_web(port=8791):
    api = Api()
    server = start_server(api, port)
    print(f"Handwrite running at http://127.0.0.1:{server.server_address[1]}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def run_native():
    import webview
    api = Api()
    server = start_server(api)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    window = webview.create_window("Handwrite", url, js_api=api, width=1280, height=820,
                                   min_size=(960, 640), background_color="#f5f5f7")
    api._window = window
    try:
        webview.start(debug="--debug" in sys.argv)
    finally:
        api._pool.shutdown(cancel_futures=True)
        shutil.rmtree(WORK_DIR, ignore_errors=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--web" in sys.argv:
        run_web()
    else:
        run_native()
