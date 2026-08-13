"""
Content-addressed input asset storage.

All image uploads (multipart, data URL, http download) converge here.
Files are named `{prefix}_{sha256(data)[:16]}.png`, so identical bytes
reuse the existing file instead of creating unbounded duplicates in
ComfyUI's input directory. ComfyUI's LoadImage references by filename,
so content-identical reuse hits automatically.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional


def _hash_bytes(data: bytes) -> str:
    """Return the first 16 hex chars of the sha256 of the image bytes."""
    return hashlib.sha256(data).hexdigest()[:16]


def _input_dir() -> str:
    """Return (and create) ComfyUI's input directory."""
    import folder_paths
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    return input_dir


def save_input_asset(data: bytes, prefix: str) -> Optional[str]:
    """Write image bytes to the input dir, deduplicated by content hash.

    Returns the input filename. If a file with the same hash already
    exists, the existing file is reused and nothing is written.
    """
    if not data:
        return None
    filename = f"{prefix}_{_hash_bytes(data)}.png"
    path = os.path.join(_input_dir(), filename)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return filename
