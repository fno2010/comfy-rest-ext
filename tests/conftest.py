"""
Test fixtures for comfy-rest-ext tests.
"""

import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_civitai_url():
    return "https://civitai.com/models/12345"


@pytest.fixture
def sample_civitai_version_url():
    return "https://civitai.com/models/12345?modelVersion=67890"


@pytest.fixture
def sample_huggingface_url():
    return "https://huggingface.co/runwayml/stable-diffusion-v1-5"


@pytest.fixture
def sample_direct_url():
    return "https://example.com/model.safetensors"
