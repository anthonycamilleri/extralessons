"""Optimize uploaded class images: bounded size, EXIF-corrected, JPEG-compressed."""
import io
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_DIMENSION = 1600
JPEG_QUALITY = 82


def optimize_image(uploaded_file):
    """Return an optimized ContentFile for an uploaded image.

    Resizes so the longest side is at most MAX_DIMENSION, fixes EXIF rotation,
    flattens transparency onto white, and re-encodes as JPEG. Keeps uploads of
    any size/format from bloating the media volume and slowing the catalogue.
    """
    image = Image.open(uploaded_file)
    image = ImageOps.exif_transpose(image)

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    new_name = Path(uploaded_file.name).stem + ".jpg"
    return ContentFile(buffer.getvalue(), name=new_name)
