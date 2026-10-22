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


def _new_image(model_id, **overrides):
    data = {
        "builder": "anima",
        "modality": "image",
        "tasks": ["t2i"],
        "defaults": {"steps": 1},
        "size": _image_size(),
    }
    data.update(overrides)
    return data


def test_batch_alias_conflict_is_order_independent(tmp_path):
    entries = {
        "cfg-a": _new_image("cfg-a", aliases=["cfg-b"]),
        "cfg-b": _new_image("cfg-b"),
    }
    for order in (["cfg-a", "cfg-b"], ["cfg-b", "cfg-a"]):
        path = _write(tmp_path, {key: entries[key] for key in order})
        with pytest.raises(catalog.ModelConfigError):
            catalog.load_model_config(path)
        assert catalog.get_model("anima-base").id == "anima-base"


def test_alias_reusable_after_override_drops_it(tmp_path):
    path = _write(tmp_path, {
        "minimax-h3": {"aliases": ["minimax-h3-t2v"]},
        "minimax-h3-i2v": {
            "builder": "h3",
            "modality": "video",
            "tasks": ["t2va"],
        },
    })
    catalog.load_model_config(path)
    assert catalog.get_model("minimax-h3-i2v").id == "minimax-h3-i2v"
    with pytest.raises(KeyError):
        catalog.get_model("minimax-h3-r2v")


def test_apply_phase_failure_leaves_registry_untouched(tmp_path):
    path = _write(tmp_path, {
        "temp-a": _new_image("temp-a", aliases=["temp-b"]),
        "temp-b": _new_image("temp-b"),
    })
    before = catalog.all_model_ids()
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)
    assert catalog.all_model_ids() == before
    with pytest.raises(KeyError):
        catalog.get_model("temp-a")


def test_empty_model_id_rejected(tmp_path):
    path = _write(tmp_path, {"": _new_image("")})
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)


def test_invalid_utf8_is_wrapped(tmp_path):
    path = tmp_path / "models.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)


def test_size_override_merges_and_validates(tmp_path):
    path = _write(tmp_path, {
        "anima-base": {"size": {"max_width": 2048}},
    })
    catalog.load_model_config(path)
    spec = catalog.get_model("anima-base")
    assert spec.size.max_width == 2048
    assert spec.size.default_width == 1024

    bad = _write(tmp_path, {
        "anima-base": {"size": {"default_width": "768"}},
    })
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(bad)


def test_builder_modality_and_tasks_validated(tmp_path):
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(_write(tmp_path, {
            "bad-video-anima": _new_image("bad-video-anima", modality="video"),
        }))
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(_write(tmp_path, {
            "bad-edit-anima": _new_image("bad-edit-anima", tasks=["edit"]),
        }))
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(_write(tmp_path, {
            "bad-image-h3": {
                "builder": "h3",
                "modality": "image",
                "tasks": ["t2va"],
                "size": _image_size(),
            },
        }))


def test_new_image_model_requires_steps(tmp_path):
    entry = _new_image("no-steps")
    entry.pop("defaults")
    path = _write(tmp_path, {"no-steps": entry})
    with pytest.raises(catalog.ModelConfigError):
        catalog.load_model_config(path)


def test_turbo_variant_uses_turbo_assets(tmp_path):
    path = _write(tmp_path, {
        "my-anima-turbo": _new_image(
            "my-anima-turbo",
            variant="turbo",
            defaults={"steps": 8},
            assets={
                "unet": "base.safetensors",
                "turbo_unet": "turbo.safetensors",
                "text_encoder": "te.safetensors",
                "vae": "vae.safetensors",
                "turbo_lora": "turbo-lora.safetensors",
            },
        ),
    })
    catalog.load_model_config(path)
    spec = catalog.get_model("my-anima-turbo")
    built = build_anima_task(_task(
        model_id="my-anima-turbo",
        variant=spec.variant,
        assets=spec.assets,
        params=spec.defaults,
    ))
    nodes = built.workflow
    unet_id = next(nid for nid, n in nodes.items() if n["class_type"] == "UNETLoader")
    assert nodes[unet_id]["inputs"]["unet_name"] == "turbo.safetensors"
    lora = [n for n in nodes.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert lora and lora[0]["inputs"]["lora_name"] == "turbo-lora.safetensors"


def test_h3_defaults_flow_into_sampler(monkeypatch):
    from api.workflows.h3 import build_h3_task

    for key in ("H3_STEPS", "H3_SAMPLER", "H3_SCHEDULER"):
        monkeypatch.delenv(key, raising=False)
    task = GenerationTask(
        task_id="video_cfg",
        status="queued",
        prompt="p",
        task_type="t2va",
        width=512,
        height=512,
        length=39,
        seed=1,
        model_id="minimax-h3",
        modality="video",
        params={"steps": 5, "sampler": "dpmpp_2m", "scheduler": "karras"},
        speed="auto",
    )
    built = build_h3_task(task)
    sampler = next(
        n for n in built.workflow.values() if n["class_type"] == "KSampler"
    )
    assert sampler["inputs"]["steps"] == 5
    assert sampler["inputs"]["sampler_name"] == "dpmpp_2m"
    assert sampler["inputs"]["scheduler"] == "karras"


@pytest.mark.asyncio
async def test_video_route_uses_spec_defaults(tmp_path, monkeypatch):
    path = _write(tmp_path, {
        "minimax-h3": {
            "defaults": {"width": 512, "height": 512, "seconds": 2.0},
        },
    })
    catalog.load_model_config(path)
    import api.openai.v1 as v1

    captured = {}

    async def fake_submit(task):
        captured["task"] = task
        return task

    monkeypatch.setattr(v1, "submit_video_task", fake_submit)

    class Request:
        headers = {}

        async def json(self):
            return {"model": "minimax-h3", "prompt": "a cat"}

    response = await v1.create_video(Request())
    assert response.status == 200
    body = json.loads(response.text)
    assert body["size"] == "512x512"
    assert body["seconds"] == str(56 / 24)
