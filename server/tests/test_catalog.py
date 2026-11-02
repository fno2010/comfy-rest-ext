"""Unit tests for the model catalog and size policy."""

from __future__ import annotations

import pytest

from api.catalog import (
    ModelSpec,
    SizePolicy,
    all_model_ids,
    alias_task_type,
    canonical_model_id,
    get_model,
    list_models,
)


def test_video_alias_resolution():
    assert get_model("minimax-h3").modality == "video"
    assert get_model("minimax-h3-i2v").id == "minimax-h3"
    assert canonical_model_id("minimax-h3-r2v") == "minimax-h3"
    assert alias_task_type("minimax-h3-i2v") == "fl2va"
    assert alias_task_type("minimax-h3") is None


def test_image_models_are_registered():
    ids = set(all_model_ids())
    assert {"qwen-image-2.1", "qwen-image-2.1-turbo", "anima-base", "anima-turbo"} <= ids
    assert get_model("qwen-image-2.1").modality == "image"
    assert "edit" in get_model("qwen-image-2.1").tasks
    assert "edit" not in get_model("anima-base").tasks
    assert len(list(list_models())) == 5


def test_unknown_model_raises_keyerror():
    with pytest.raises(KeyError):
        get_model("gpt-5")


def test_qwen_size_default_and_aspect_ratio():
    spec = get_model("qwen-image-2.1")
    assert spec.size is not None
    assert spec.size.normalize() == (1024, 1024)
    w, h = spec.size.normalize(aspect_ratio="16:9", megapixels=1.0)
    assert w % 8 == 0 and h % 8 == 0
    assert abs((w / h) - (16 / 9)) < 0.02


def test_size_policy_rejects_out_of_range():
    spec = get_model("qwen-image-2.1")
    with pytest.raises(ValueError):
        spec.size.normalize(size="4096x4096")
    with pytest.raises(ValueError):
        spec.size.normalize(size="100x100")
    with pytest.raises(ValueError):
        spec.size.normalize(width=1024)


def test_anima_size_step_is_16():
    spec = get_model("anima-base")
    w, h = spec.size.normalize(size="1000x1000")
    assert w % 16 == 0 and h % 16 == 0


def test_model_spec_is_frozen():
    spec = get_model("anima-base")
    with pytest.raises(Exception):
        spec.id = "other"  # type: ignore[misc]
