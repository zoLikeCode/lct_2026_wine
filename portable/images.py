"""Shared image decoding for HTTP and CLI, preserving the evaluated resolution."""
import io
import warnings
from PIL import Image, ImageOps

MAX_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 64_000_000


class ImageTooLarge(ValueError):
    pass


def load_image(payload):
    if len(payload) > MAX_BYTES:
        raise ImageTooLarge('Image exceeds 24 MiB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ImageTooLarge('Image exceeds 64 megapixels')
                return ImageOps.exif_transpose(source).convert('RGB')
    except ImageTooLarge:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('Unable to decode image; use JPEG, PNG or WebP') from exc
