"""Generate the favicon set from the PTA logo.

Run once after replacing static/img/pta-logo.png; the outputs are committed to
the repo so nothing is generated at request time:

    python scripts/make_icons.py

The crop is derived, not hard-coded: the "PTA" mark is the saturated (coloured)
part of the logo, while the "European School Ljubljana" wordmark underneath is
black. Selecting the coloured pixels therefore isolates the mark on its own,
which is the only part that stays legible at 32px.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "img" / "pta-logo.png"
SIZES = {"icon-32.png": 32, "icon-180.png": 180}
PAD = 0.10  # share of the mark's longest side left as breathing room


def coloured_bbox(image, min_saturation=60):
    """Bounding box of the saturated pixels — the PTA mark, not the wordmark."""
    hsv = image.convert("RGB").convert("HSV")
    saturation = hsv.getchannel("S").point(lambda s: 255 if s >= min_saturation else 0)
    box = saturation.getbbox()
    if box is None:
        raise SystemExit(f"No coloured pixels found in {SOURCE}")
    return box


def main():
    logo = Image.open(SOURCE).convert("RGBA")
    left, top, right, bottom = coloured_bbox(logo)
    mark = logo.crop((left, top, right, bottom))

    side = int(max(mark.width, mark.height) * (1 + 2 * PAD))
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(mark, ((side - mark.width) // 2, (side - mark.height) // 2))

    for name, size in SIZES.items():
        target = ROOT / "static" / "img" / name
        canvas.resize((size, size), Image.LANCZOS).convert("RGB").save(
            target, "PNG", optimize=True
        )
        print(f"wrote {target.relative_to(ROOT)} ({size}x{size})")


if __name__ == "__main__":
    main()
