"""Builds a double-click app with PyInstaller.

    python build.py            # dist/Handwrite.exe on Windows, dist/Handwrite.app on macOS
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sep = ";" if platform.system() == "Windows" else ":"
args = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Handwrite",
    "--add-data", f"app/ui{sep}ui",
    "--add-data", f"app/fonts{sep}fonts",
    "--paths", "app",
]
if platform.system() == "Windows":
    args += ["--onefile", "--icon", "build_icon.ico"]
elif platform.system() == "Darwin":
    args += ["--icon", "build_icon.icns", "--osx-bundle-identifier", "app.handwrite"]
    if not (ROOT / "build_icon.icns").exists():  # make the .icns from the PNG with macOS tools
        iconset = ROOT / "build_icon.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 64, 128, 256, 512):
            subprocess.run(["sips", "-z", str(size), str(size), "build_icon_512.png", "--out",
                            str(iconset / f"icon_{size}x{size}.png")], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", "build_icon.icns"], check=True)
        shutil.rmtree(iconset)
args.append("app/main.py")
subprocess.run(args, cwd=ROOT, check=True)
print("built:", [p.name for p in (ROOT / "dist").iterdir()])
