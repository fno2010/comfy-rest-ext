"""Unit tests for content-addressed input asset storage."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_module(name, rel_path):
    """Load a project module by file path, bypassing package import."""
    path = PROJECT_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


input_assets = _load_module(
    "comfy_rest_ext_input_assets", "api/input_assets.py"
)


def _image_bytes(fmt: str) -> bytes:
    """Encode a tiny solid-color image in the given PIL format."""
    from PIL import Image
    img = Image.new("RGB", (8, 8), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class FakeFolderPaths:
    """Minimal folder_paths with an in-memory input dir."""

    def __init__(self, tmp_path):
        self._input_dir = str(tmp_path / "input")

    def get_input_directory(self):
        return self._input_dir


@pytest.fixture
def fake_fp(monkeypatch, tmp_path):
    fp = FakeFolderPaths(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", fp)
    return fp


def test_save_creates_hash_named_file(fake_fp):
    data = _image_bytes("PNG")
    filename = input_assets.save_input_asset(data, "ref_image")
    expected = f"ref_image_{hashlib.sha256(data).hexdigest()[:16]}.png"
    assert filename == expected
    path = os.path.join(fake_fp._input_dir, filename)
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == data


def test_save_sniffs_real_format(fake_fp):
    """JPEG bytes get a .jpg extension, not hardcoded .png."""
    data = _image_bytes("JPEG")
    filename = input_assets.save_input_asset(data, "ref_image")
    expected = f"ref_image_{hashlib.sha256(data).hexdigest()[:16]}.jpg"
    assert filename == expected
    assert os.path.exists(os.path.join(fake_fp._input_dir, filename))


def test_save_webp_and_gif_formats(fake_fp):
    webp = input_assets.save_input_asset(_image_bytes("WEBP"), "ref_image")
    gif = input_assets.save_input_asset(_image_bytes("GIF"), "ref_image")
    assert webp.endswith(".webp")
    assert gif.endswith(".gif")


def test_save_sniff_fallback_png_for_unknown(fake_fp):
    """Bytes PIL can't open fall back to .png."""
    data = b"not a real image at all"
    filename = input_assets.save_input_asset(data, "ref_image")
    expected = f"ref_image_{hashlib.sha256(data).hexdigest()[:16]}.png"
    assert filename == expected


def test_save_dedupes_identical_bytes(fake_fp):
    data = _image_bytes("PNG")
    f1 = input_assets.save_input_asset(data, "ref_image")
    f2 = input_assets.save_input_asset(data, "ref_image")
    assert f1 == f2
    files = os.listdir(fake_fp._input_dir)
    assert len(files) == 1


def test_save_different_prefix_same_hash(fake_fp):
    """Same bytes with different prefix stay separate (distinct roles)."""
    data = _image_bytes("PNG")
    f1 = input_assets.save_input_asset(data, "ref_image")
    f2 = input_assets.save_input_asset(data, "first_frame")
    assert f1 != f2
    assert len(os.listdir(fake_fp._input_dir)) == 2


def test_save_empty_data_returns_none(fake_fp):
    assert input_assets.save_input_asset(b"", "ref_image") is None


def test_save_different_bytes_distinct_files(fake_fp):
    a = input_assets.save_input_asset(_image_bytes("PNG"), "ref_image")
    b = input_assets.save_input_asset(_image_bytes("JPEG"), "ref_image")
    assert a != b
    assert len(os.listdir(fake_fp._input_dir)) == 2
