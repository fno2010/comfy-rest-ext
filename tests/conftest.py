"""
Test fixtures for comfy-rest-ext tests.

Injects a minimal fake `server` module so the api package can import
without a running ComfyUI (aiohttp routes container only). Also fakes
folder_paths for modules that touch the state directory lazily.
"""

import sys
import os
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_fake_server():
    """Install a fake `server` module with a routes container."""
    if "server" in sys.modules:
        return sys.modules["server"]

    import aiohttp.web

    server_mod = types.ModuleType("server")

    class _FakeRoutes:
        def __init__(self):
            self._routes = []

        def get(self, path):
            def deco(fn):
                self._routes.append(("GET", path, fn))
                return fn
            return deco

        def post(self, path):
            def deco(fn):
                self._routes.append(("POST", path, fn))
                return fn
            return deco

        def delete(self, path):
            def deco(fn):
                self._routes.append(("DELETE", path, fn))
                return fn
            return deco

    class _FakePromptServer:
        instance = None

    fake_routes = _FakeRoutes()
    _FakePromptServer.instance = types.SimpleNamespace(routes=fake_routes)
    server_mod.PromptServer = _FakePromptServer
    server_mod.web = aiohttp.web

    sys.modules["server"] = server_mod
    return server_mod


def _install_fake_folder_paths():
    """Install a fake folder_paths module for pure-unit tests."""
    if "folder_paths" in sys.modules:
        return sys.modules["folder_paths"]

    import tempfile

    fp = types.ModuleType("folder_paths")
    fp._state_dir = tempfile.mkdtemp(prefix="comfy-rest-ext-test-")

    def get_system_user_directory(name):
        return os.path.join(fp._state_dir, name)

    def get_output_directory():
        return os.path.join(fp._state_dir, "output")

    fp.get_system_user_directory = get_system_user_directory
    fp.get_output_directory = get_output_directory
    sys.modules["folder_paths"] = fp
    return fp





_install_fake_server()
_install_fake_folder_paths()


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
