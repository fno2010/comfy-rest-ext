"""Tests for the optional models.json config and per-model assets."""

from __future__ import annotations

import json

import pytest

import api.catalog as catalog
from api.tasks.video_task import GenerationTask
from api.workflows.anima import build_anima_task


@pytest.fixture(autouse=True)
def restore_catalog():
    specs = dict(catalog._SPECS)
    aliases = dict(catalog._ALIASES)
    yield
    catalog._SPECS.clear()
    catalog._SPECS.update(specs)
    catalog._ALIASES.clear()
    catalog._ALIASES.update(aliases)


def _write(tmp_path, models):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": models}))
    return path


def _image_size(**overrides):
    data = {
        "default_width": 768,
        "default_height": 768,
        "min_width": 512,
        "min_height": 512,
        "max_width": 1536,
        "max_height": 1536,
        "step": 16,
        "max_pixels": 1536 * 1536,
        "default_megapixels": 1.0,
    }
    data.update(overrides)
    return data


def _task(**overrides):
    data = dict(
        task_id="image_cfg",
        status="queued",
        prompt="a cat",
        task_type="t2i",
        width=768,
        height=768,
        length=1,
        seed=1,
        model_id="anima-base",
        modality="image",
    )
    data.update(overrides)
    return GenerationTask(**data)


def test_new_model_from_config(tmp_path):
    path = _write(tmp_path, {
        "my-anima": {
            "builder": "anima",
            "modality": "image",
            "tasks": ["t2i"],
            "aliases": ["my-anima-alt"],
            "alias_tasks": {"my-anima-alt": "t2i"},
            "defaults": {"steps": 12, "cfg": 2.0},
            "capabilities": ["negative_prompt", "n"],
            "assets": {
                "unet": "custom-anima.safetensors",
                "text_encoder": "custom-te.safetensors",
                "vae": "custom-vae.safetensors",
            },
            "size": _image_size(),
            "max_n": 2,
            "timeout_s": 600,
            "description": "custom model",
        }
    })
    loaded = catalog.load_model_config(path)
    assert loaded == ("my-anima",)
    spec = catalog.get_model("my-anima-alt")
    assert spec.id == "my-anima"
    assert spec.family == "anima"
    assert spec.defaults["steps"] == 12
    assert spec.defaults["cfg"] == 2.0
    assert spec.assets["unet"] == "custom-anima.safetensors"
    assert spec.max_n == 2
    assert spec.timeout_s == 600
    assert catalog.alias_task_type("my-anima-alt") == "t2i"


def test_override_existing_model_inherits_unspecified_fields(tmp_path):
    path = _write(tmp_path, {
        "anima-base": {
            "assets": {"unet": "override.safetensors"},
            "defaults": {"steps": 5},
            "aliases": ["anima-custom"],
        }
    })
    catalog.load_model_config(path)
    spec = catalog.get_model("anima-custom")
    assert spec.id == "anima-base"
    assert spec.modality == "image"
    assert spec.family == "anima"
    assert spec.tasks == ("t2i",)
    assert spec.size is not None
    assert spec.assets["unet"] == "override.safetensors"
    assert spec.defaults["steps"] == 5
    assert spec.defaults["sampler"] == "euler"
    assert spec.capabilities == frozenset({"negative_prompt", "n", "seed"})
    assert catalog.get_model("anima-base") is spec


def test_invalid_config_rolls_back(tmp_path):
    path = _write(tmp_path, {
        "broken": {
            "builder": "does-not-exist",
            "modality": "image",
            "tasks": ["t2i"],
        }
    })
    before = catalog.all_model_ids()
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)
    assert catalog.all_model_ids() == before


def test_alias_collision_rejected(tmp_path):
    path = _write(tmp_path, {
        "new-model": {
            "builder": "anima",
            "modality": "image",
            "tasks": ["t2i"],
            "aliases": ["anima-base"],
            "size": _image_size(),
        }
    })
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)


def test_builder_uses_task_assets():
    built = build_anima_task(_task(assets={
        "unet": "asset-model.safetensors",
        "text_encoder": "asset-te.safetensors",
        "vae": "asset-vae.safetensors",
    }))
    nodes = built.workflow
    unet_id = next(nid for nid, n in nodes.items() if n["class_type"] == "UNETLoader")
    assert nodes[unet_id]["inputs"]["unet_name"] == "asset-model.safetensors"
    vae_id = next(nid for nid, n in nodes.items() if n["class_type"] == "VAELoader")
    assert nodes[vae_id]["inputs"]["vae_name"] == "asset-vae.safetensors"


def test_env_asset_override_wins(monkeypatch):
    monkeypatch.setenv("ANIMA_UNET", "env-model.safetensors")
    built = build_anima_task(_task(assets={"unet": "config-model.safetensors"}))
    nodes = built.workflow
    unet_id = next(nid for nid, n in nodes.items() if n["class_type"] == "UNETLoader")
    assert nodes[unet_id]["inputs"]["unet_name"] == "env-model.safetensors"
