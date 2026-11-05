"""Render assets/flowclone.ico and assets/flowclone-dark.ico from the tray glyph.

flowclone.ico (light mic) is used with the dark theme and as the EXE icon;
flowclone-dark.ico (dark mic) is used with the light theme.

Run once after editing tray.py's _build_icon_image() to regenerate the bundled
Windows icons. The CI release workflow also runs this before PyInstaller builds,
so the EXE always gets the current glyph.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tray import GLYPH_DARK, GLYPH_LIGHT, _build_icon_image  # noqa: E402

ICONS = {
    REPO_ROOT / "assets" / "flowclone.ico": GLYPH_LIGHT,
    REPO_ROOT / "assets" / "flowclone-dark.ico": GLYPH_DARK,
}
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    for path, color in ICONS.items():
        img = _build_icon_image(256, color)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, format="ICO", sizes=SIZES)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
