"""Tests for persistent CLI profiles and API-key propagation."""

from __future__ import annotations

import json
import urllib.request

import pytest

from comfy_rest_ext_cli import config
from comfy_rest_ext_cli.client import ComfyRestClient
from comfy_rest_ext_cli.__main__ import main


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for key in (
        "COMFY_REST_EXT_CONFIG", "COMFY_REST_EXT_PROFILE",
        "COMFY_REST_EXT_BASE_URL", "COMFY_REST_EXT_API_KEY",
        "COMFY_REST_EXT_TIMEOUT", "COMFY_REST_EXT_DEFAULT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_config_round_trip_and_redaction(isolated_config):
    data = config.load_config()
    data["profiles"]["dev"] = {
        "base_url": "http://example:8188",
        "api_key": "secret",
        "timeout": 12.5,
        "default_model": "m",
    }
    data["default_profile"] = "dev"
    path = config.save_config(data)
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = config.load_config()
    assert loaded["profiles"]["dev"]["api_key"] == "secret"
    assert config.redact(loaded)["profiles"]["dev"]["api_key"] == "***"


def test_resolve_precedence(isolated_config, monkeypatch):
    data = {
        "default_profile": "dev",
        "profiles": {"dev": {
            "base_url": "http://profile", "api_key": "profile-key",
            "timeout": 10, "default_model": "profile-model",
        }},
    }
    monkeypatch.setenv("COMFY_REST_EXT_BASE_URL", "http://env")
    monkeypatch.setenv("COMFY_REST_EXT_API_KEY", "env-key")
    resolved = config.resolve(data, base_url="http://flag", timeout=3)
    assert resolved["base_url"] == "http://flag"
    assert resolved["api_key"] == "env-key"
    assert resolved["timeout"] == 3
    assert resolved["default_model"] == "profile-model"


def test_config_commands_main(isolated_config, capsys):
    assert main(["config", "set", "dev", "base_url", "http://example"]) == 0
    assert main(["config", "set", "dev", "timeout", "15"]) == 0
    assert main(["config", "use", "dev"]) == 0
    capsys.readouterr()
    assert main(["--json", "config", "list"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["default_profile"] == "dev"
    assert output["profiles"]["dev"]["base_url"] == "http://example"
    assert output["profiles"]["dev"]["timeout"] == 15.0


def test_client_sends_bearer_token(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"status": "ok"}'

    def fake_urlopen(request, timeout=None):
        seen["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = ComfyRestClient("http://example", api_key="token")
    assert client.health() == {"status": "ok"}
    assert seen["authorization"] == "Bearer token"


def test_invalid_config_returns_error(isolated_config, capsys):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[profiles.dev]\nunknown = true\n", encoding="utf-8")
    assert main(["config", "list"]) == 2
    assert "unknown profile key" in capsys.readouterr().err
