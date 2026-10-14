"""Converting images browsers and model providers cannot take.

TIFF (often multi-page scans) and BMP are valid uploads, but no provider's
vision API and no browser besides Safari accepts them. Uploaded TIFF
documents become PDFs, so every page is viewable and goes through the normal
document pipeline; images headed for a model become PNG.
"""

import base64
from io import BytesIO
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageSequence, UnidentifiedImageError

TIFF_MIME_TYPE = "image/tiff"
TIFF_EXTENSIONS = frozenset({"tif", "tiff"})
PDF_MIME_TYPE = "application/pdf"
# The image formats every supported provider's vision API accepts.
PROVIDER_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_PDF_MODES = frozenset({"1", "L", "RGB", "CMYK"})
_DEFAULT_DPI = 200.0


class UnreadableImage(Exception):
    """The bytes are not an image Pillow can decode."""


def is_tiff(file_name: str, content_type: str | None) -> bool:
    extension = Path(file_name).suffix.lstrip(".").lower()
    return extension in TIFF_EXTENSIONS or content_type == TIFF_MIME_TYPE


def tiff_to_pdf(data: bytes) -> bytes:
    """Every page of a TIFF as one PDF, keeping its resolution."""
    try:
        with Image.open(BytesIO(data)) as image:
            dpi = image.info.get("dpi", (_DEFAULT_DPI,))[0] or _DEFAULT_DPI
            pages = [
                frame.copy() if frame.mode in _PDF_MODES else frame.convert("RGB")
                for frame in ImageSequence.Iterator(image)
            ]
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise UnreadableImage(str(error)) from error
    output = BytesIO()
    pages[0].save(
        output, "PDF", save_all=True, append_images=pages[1:], resolution=float(dpi)
    )
    return output.getvalue()


def to_provider_image(data: bytes, mime_type: str) -> Tuple[bytes, str]:
    """The image as-is if providers accept its type, otherwise its first frame as PNG.

    Bytes Pillow cannot decode (SVG, corrupt files) are returned unchanged so
    the provider handler can skip them as it always has.
    """
    if mime_type in PROVIDER_IMAGE_TYPES:
        return data, mime_type
    try:
        with Image.open(BytesIO(data)) as image:
            image.seek(0)
            has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
            frame = image.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return data, mime_type
    output = BytesIO()
    frame.save(output, "PNG")
    return output.getvalue(), "image/png"


def to_provider_data_url(data_url: str) -> str:
    """``to_provider_image`` for a ``data:<mime>;base64,...`` URL."""
    header, _, payload = data_url.partition(",")
    mime_type = header.removeprefix("data:").removesuffix(";base64")
    if not payload or mime_type in PROVIDER_IMAGE_TYPES:
        return data_url
    try:
        data = base64.b64decode(payload)
    except ValueError:
        return data_url
    converted, converted_type = to_provider_image(data, mime_type)
    if converted_type == mime_type:
        return data_url
    return f"data:{converted_type};base64,{base64.b64encode(converted).decode('ascii')}"
