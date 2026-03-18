"""
Tests for model management functionality.
"""

import os
import pytest
from api.models.management import (
    check_civitai_url,
    check_huggingface_url,
    parse_safetensors_header,
    get_file_hash,
)


class TestUrlParsing:
    """Tests for URL parsing utilities."""

    def test_civitai_parsing(self):
        is_model, is_api, model_id, version_id = check_civitai_url(
            "https://civitai.com/models/123"
        )
        assert is_model is True
        assert model_id == "123"

    def test_huggingface_parsing(self):
        is_hf, repo_id, *_ = check_huggingface_url(
            "https://huggingface.co/org/repo"
        )
        assert is_hf is True
        assert repo_id == "org/repo"


class TestSafetensorsParsing:
    """Tests for safetensors header parsing."""

    def test_parse_invalid_file(self, tmp_path):
        # Create a non-safetensors file
        test_file = tmp_path / "test.txt"
        test_file.write_text("not a safetensors file")

        result = parse_safetensors_header(str(test_file))
        assert result is None

    def test_parse_nonexistent_file(self):
        result = parse_safetensors_header("/nonexistent/file.safetensors")
        assert result is None


class TestFileHash:
    """Tests for file hashing."""

    def test_hash_nonexistent(self):
        result = get_file_hash("/nonexistent/file.bin")
        assert result is None

    def test_hash_sha256(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        result = get_file_hash(str(test_file), algorithm="sha256")
        assert result is not None
        assert len(result) == 64  # SHA256 hex length

    def test_hash_same_content(self, tmp_path):
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("same content")
        file2.write_text("same content")

        hash1 = get_file_hash(str(file1), algorithm="sha256")
        hash2 = get_file_hash(str(file2), algorithm="sha256")
        assert hash1 == hash2
