# PyInstaller build recipe, run by build.py (python build.py).
# -*- mode: python -*-
import sys

# pymupdf 2.x pulls in layout-analysis models, numpy and onnxruntime as dependencies. Handwrite
# only reads text from PDFs, and "import pymupdf" never loads them, so they stay out of the app
# (they would add about 45 MB). The same goes for the C headers and libs in mupdf-devel.
EXCLUDED_MODULES = ["numpy", "onnxruntime", "pymupdf.layout", "pymupdf_layout", "pymupdf4llm",
                    "networkx", "yaml", "tabulate", "psutil", "tkinter"]
EXCLUDED_PATHS = ("pymupdf/layout", "pymupdf/mupdf-devel", "pymupdf_layout", "pymupdf4llm",
                  "numpy", "onnxruntime", "networkx")


def keep(entries):
    return [e for e in entries
            if not any(p in e[0].replace("\\", "/") for p in EXCLUDED_PATHS)]


a = Analysis(
    ["app/main.py"],
    pathex=["app"],
    datas=[("app/ui", "ui"), ("app/fonts", "fonts")],
    excludes=EXCLUDED_MODULES,
)
a.datas = keep(a.datas)
a.binaries = keep(a.binaries)
pyz = PYZ(a.pure)

if sys.platform == "win32":  # one double-click file
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Handwrite", console=False,
              icon="build_icon.ico", version="version_info.txt", upx=False)
else:  # a folder that becomes Handwrite.app on macOS
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Handwrite", console=False,
              icon="build_icon.icns" if sys.platform == "darwin" else None, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="Handwrite", upx=False)
    if sys.platform == "darwin":
        app = BUNDLE(coll, name="Handwrite.app", icon="build_icon.icns",
                     bundle_identifier="app.handwrite")
