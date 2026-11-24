#!/usr/bin/env python3
"""Generate crisp 88x88 1-bpp launcher icon for Z-Books (book + download arrow)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    im = Image.new("1", (88, 88), 1)
    d = ImageDraw.Draw(im)

    # Outer book cover
    d.rounded_rectangle((14, 8, 74, 80), radius=7, fill=1, outline=0, width=4)
    # Book spine line
    d.line((26, 10, 26, 78), fill=0, width=3)
    # Bottom page lines accent
    d.line((26, 68, 72, 68), fill=0, width=2)

    # Bold downward download arrow centered on cover (x=50)
    d.line((50, 22, 50, 50), fill=0, width=5)
    d.polygon([(36, 44), (64, 44), (50, 60)], fill=0)

    out = ROOT / "app/assets/app_icon_s.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    print(f"Wrote {out} ({im.size}, mode={im.mode})")


if __name__ == "__main__":
    main()
