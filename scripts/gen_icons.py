#!/usr/bin/env python3
"""Kapak goruluntusunden PWA ikonlari uretir.

Kullanim: python3 scripts/gen_icons.py [cover_path]
"""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
DEFAULT_COVER = ROOT / "9781917986946-us.jpg"

# buyuk ikonlar (fit + arkaplan), kucuk ikonlar (cover crop)
ICONS = {
    "icon-512.png": (512, "fit"),
    "icon-192.png": (192, "fit"),
    "apple-touch-icon.png": (180, "fit"),
    "icon-48.png": (48, "fit"),
    "favicon-32.png": (32, "crop"),
    "favicon-16.png": (16, "crop"),
}


def average_color(img: Image.Image) -> tuple[int, int, int]:
    small = img.resize((16, 16))
    px = list(small.getdata())
    n = len(px)
    return tuple(sum(c[i] for c in px) // n for i in range(3))


def main() -> int:
    cover_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COVER
    if not cover_path.exists():
        print(f"Kapak bulunamadi: {cover_path}", file=sys.stderr)
        return 1

    src = Image.open(cover_path).convert("RGB")
    bg = average_color(src)

    PUBLIC.mkdir(exist_ok=True)
    for name, (size, mode) in ICONS.items():
        if mode == "fit":
            canvas = Image.new("RGB", (size, size), bg)
            thumb = src.copy()
            thumb.thumbnail((size, size), Image.LANCZOS)
            canvas.paste(thumb, ((size - thumb.width) // 2, (size - thumb.height) // 2))
            out = canvas
        else:
            ratio = max(size / src.width, size / src.height)
            w, h = int(src.width * ratio + 0.5), int(src.height * ratio + 0.5)
            thumb = src.resize((w, h), Image.LANCZOS)
            left, top = (w - size) // 2, (h - size) // 2
            out = thumb.crop((left, top, left + size, top + size))
        out.save(PUBLIC / name, optimize=True)
        print(f"{name} ({size}x{size})")

    print(f"BACKGROUND_RGB={bg[0]},{bg[1]},{bg[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
