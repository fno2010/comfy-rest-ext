"""Tests for the process-level config endpoint helpers."""

from __future__ import annotations

import os

import pytest

from api import config


@pytest.fixture
def isolated_env(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in config.SETTINGS:
        os.environ.pop(key, None)
    return os.environ


def test_config_payload_exposes_values_and_restart_scope(isolated_env):
    payload = config.config_payload()
    steps = payload["settings"]["H3_STEPS"]
    assert steps["value"] == 20
    assert steps["min"] == 1 and steps["max"] == 200
    assert steps["editable"] is True
    assert payload["settings"]["H3_SAMPLER"]["value"] == "euler"
    vae = payload["settings"]["H3_VAE_BATCHED"]
    assert vae["editable"] is False
    assert vae["restart_required"] is True


def test_apply_settings_updates_request_time_env(isolated_env):
    result = config.apply_settings({
        "settings": {"H3_STEPS": 12, "H3_TE_SPEED": True},
    })
    assert result["updated"] == ["H3_STEPS", "H3_TE_SPEED"]
    assert isolated_env["H3_STEPS"] == "12"
    assert isolated_env["H3_TE_SPEED"] == "1"
    assert result["settings"]["H3_STEPS"]["value"] == 12


def test_apply_settings_rejects_unknown_and_bad_types(isolated_env):
    with pytest.raises(config.ConfigError) as exc:
        config.apply_settings({"settings": {"NOPE": 1}})
    assert exc.value.status == 400
    with pytest.raises(config.ConfigError):
        config.apply_settings({"settings": {"H3_STEPS": True}})
    with pytest.raises(config.ConfigError):
        config.apply_settings({"settings": {"H3_STEPS": 0}})


def test_apply_settings_rejects_restart_required_atomically(isolated_env):
    with pytest.raises(config.ConfigError) as exc:
        config.apply_settings({
            "settings": {"H3_STEPS": 9, "H3_VAE_BATCHED": False},
        })
    assert exc.value.status == 409
    assert "H3_STEPS" not in isolated_env
    assert "H3_VAE_BATCHED" not in isolated_env


def test_bool_env_values_match_consumer_semantics(isolated_env):
    isolated_env["COMFY_REST_UNLOAD_ON_FAMILY_SWITCH"] = "false"
    isolated_env["H3_TE_SPEED"] = "true"
    payload = config.config_payload()["settings"]
    # Default-true consumer enables for any value except "0".
    assert payload["COMFY_REST_UNLOAD_ON_FAMILY_SWITCH"]["value"] is True
    # Default-false consumer requires the literal "1".
    assert payload["H3_TE_SPEED"]["value"] is False


def test_apply_settings_rejects_nul_atomically(isolated_env):
    with pytest.raises(config.ConfigError) as exc:
        config.apply_settings({
            "settings": {"H3_STEPS": 17, "H3_SAMPLER": "\x00evil"},
        })
    assert exc.value.status == 400
    assert "H3_STEPS" not in isolated_env
    assert "H3_SAMPLER" not in isolated_env
