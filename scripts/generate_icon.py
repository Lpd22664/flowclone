"""Render assets/flowclone.ico from the tray glyph.

Run once after editing tray.py's _build_icon_image() to regenerate the bundled
Windows icon. The CI release workflow also runs this before PyInstaller builds,
so the EXE always gets the current glyph.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tray import _build_icon_image  # noqa: E402

ICON_PATH = REPO_ROOT / "assets" / "flowclone.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    img = _build_icon_image(256)
    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.save(ICON_PATH, format="ICO", sizes=SIZES)
    print(f"Wrote {ICON_PATH}")


if __name__ == "__main__":
    main()
