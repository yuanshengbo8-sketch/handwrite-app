"""Builds a double-click app with PyInstaller.

    python build.py            # dist/Handwrite.exe on Windows, dist/Handwrite.app on macOS
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# What goes into the app (and what is left out) is in handwrite.spec.
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "handwrite.spec"]
if platform.system() == "Darwin":
    if not (ROOT / "build_icon.icns").exists():  # make the .icns from the 1024px PNG with macOS tools
        iconset = ROOT / "build_icon.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):  # iconutil only accepts these names
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = size * scale
                subprocess.run(["sips", "-z", str(px), str(px), "app/ui/icon.png", "--out",
                                str(iconset / f"icon_{size}x{size}{suffix}.png")], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", "build_icon.icns"], check=True)
        shutil.rmtree(iconset)
subprocess.run(args, cwd=ROOT, check=True)
print("built:", [p.name for p in (ROOT / "dist").iterdir()])
