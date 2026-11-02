"""
Tests for URL parsing (CivitAI, HuggingFace).
"""

import pytest
from api.tasks.download_task import (
    check_civitai_url,
    check_huggingface_url,
)


class TestCivitaiUrlParsing:
    """Tests for CivitAI URL parsing."""

    def test_model_url(self, sample_civitai_url):
        is_model, is_api, model_id, version_id = check_civitai_url(sample_civitai_url)
        assert is_model is True
        assert is_api is False
        assert model_id == "12345"
        assert version_id is None

    def test_model_url_with_version(self, sample_civitai_version_url):
        is_model, is_api, model_id, version_id = check_civitai_url(sample_civitai_version_url)
        assert is_model is True
        assert is_api is False
        assert model_id == "12345"
        assert version_id == "67890"

    def test_api_url(self):
        url = "https://api.civitai.com/v1/models/12345"
        is_model, is_api, model_id, version_id = check_civitai_url(url)
        assert is_model is False
        assert is_api is True
        assert model_id == "12345"

    def test_invalid_url(self):
        url = "https://example.com/model.safetensors"
        is_model, is_api, model_id, version_id = check_civitai_url(url)
        assert is_model is False
        assert is_api is False
        assert model_id is None
        assert version_id is None


class TestHuggingfaceUrlParsing:
    """Tests for HuggingFace URL parsing."""

    def test_base_url(self, sample_huggingface_url):
        is_hf, repo_id, filename, folder, branch = check_huggingface_url(sample_huggingface_url)
        assert is_hf is True
        assert repo_id == "runwayml/stable-diffusion-v1-5"
        assert filename is None
        assert branch == "main"

    def test_blob_url(self):
        url = "https://huggingface.co/runwayml/stable-diffusion-v1-5/blob/main/README.md"
        is_hf, repo_id, filename, folder, branch = check_huggingface_url(url)
        assert is_hf is True
        assert repo_id == "runwayml/stable-diffusion-v1-5"
        assert filename == "README.md"
        assert folder is None
        assert branch == "main"

    def test_tree_url(self):
        url = "https://huggingface.co/runwayml/stable-diffusion-v1-5/tree/main"
        is_hf, repo_id, filename, folder, branch = check_huggingface_url(url)
        assert is_hf is True
        assert repo_id == "runwayml/stable-diffusion-v1-5"
        assert filename is None
        assert branch == "main"

    def test_invalid_url(self, sample_direct_url):
        is_hf, repo_id, filename, folder, branch = check_huggingface_url(sample_direct_url)
        assert is_hf is False
        assert repo_id is None
