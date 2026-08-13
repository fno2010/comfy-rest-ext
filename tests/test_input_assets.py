"""Unit tests for content-addressed input asset storage."""

from __future__ import annotations

import hashlib
import importlib.util
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
    data = b"\x89PNG fake image bytes"
    filename = input_assets.save_input_asset(data, "ref_image")
    expected = f"ref_image_{hashlib.sha256(data).hexdigest()[:16]}.png"
    assert filename == expected
    path = os.path.join(fake_fp._input_dir, filename)
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == data


def test_save_dedupes_identical_bytes(fake_fp):
    data = b"same image bytes"
    f1 = input_assets.save_input_asset(data, "ref_image")
    f2 = input_assets.save_input_asset(data, "ref_image")
    assert f1 == f2
    # Only one file on disk
    files = os.listdir(fake_fp._input_dir)
    assert len(files) == 1


def test_save_different_prefix_same_hash(fake_fp):
    """Same bytes with different prefix stay separate (distinct roles)."""
    data = b"shared image bytes"
    f1 = input_assets.save_input_asset(data, "ref_image")
    f2 = input_assets.save_input_asset(data, "first_frame")
    assert f1 != f2
    assert len(os.listdir(fake_fp._input_dir)) == 2


def test_save_empty_data_returns_none(fake_fp):
    assert input_assets.save_input_asset(b"", "ref_image") is None


def test_save_different_bytes_distinct_files(fake_fp):
    a = input_assets.save_input_asset(b"image A", "ref_image")
    b = input_assets.save_input_asset(b"image B", "ref_image")
    assert a != b
    assert len(os.listdir(fake_fp._input_dir)) == 2
