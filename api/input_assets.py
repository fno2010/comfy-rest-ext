"""
Content-addressed input asset storage.

All image uploads (multipart, data URL, http download) converge here.
Files are named `{prefix}_{sha256(data)[:16]}.{ext}` (ext sniffed from
the image content), so identical bytes reuse the existing file instead
of creating unbounded duplicates in ComfyUI's input directory. ComfyUI's
LoadImage references by filename, so content-identical reuse hits
automatically.
"""

from __future__ import annotations

import hashlib
import io
import os
from typing import Optional

# PIL format name -> file extension. Order matters for the sniff: PIL
# returns the canonical format name, so this map is keyed by it.
_FORMAT_EXTENSIONS = {
    "PNG": "png",
    "JPEG": "jpg",
    "WEBP": "webp",
    "GIF": "gif",
    "BMP": "bmp",
    "TIFF": "tiff",
}


def _hash_bytes(data: bytes) -> str:
    """Return the first 16 hex chars of the sha256 of the image bytes."""
    return hashlib.sha256(data).hexdigest()[:16]


def _sniff_extension(data: bytes) -> str:
    """Detect the image format from content; fall back to png."""
    try:
        from PIL import Image
        fmt = Image.open(io.BytesIO(data)).format
        if fmt in _FORMAT_EXTENSIONS:
            return _FORMAT_EXTENSIONS[fmt]
    except Exception:
        pass
    return "png"


def _input_dir() -> str:
    """Return (and create) ComfyUI's input directory."""
    import folder_paths
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    return input_dir


def save_input_asset(data: bytes, prefix: str) -> Optional[str]:
    """Write image bytes to the input dir, deduplicated by content hash.

    The file extension is sniffed from the image content (png/jpg/webp/
    gif/bmp/tiff), so the stored filename matches what it actually is.
    Returns the input filename. If a file with the same hash already
    exists, the existing file is reused and nothing is written.
    """
    if not data:
        return None
    filename = f"{prefix}_{_hash_bytes(data)}.{_sniff_extension(data)}"
    path = os.path.join(_input_dir(), filename)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return filename
