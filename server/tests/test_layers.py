"""Tests for the See-through layers adapter without running inference."""

from __future__ import annotations

import json

import pytest

from api.catalog import get_model
from api.layers import _exclude_tags, _summary
from api.tasks.video_task import GenerationTask
from api.workflows.see_through import build_see_through_task


def _task(**kwargs):
    data = dict(
        task_id="layers_12345678",
        status="completed",
        prompt="",
        task_type="decompose",
        width=512,
        height=512,
        length=1,
        seed=1,
        model_id="see-through",
        modality="layers",
        ref_images=["input.png"],
        params={"resolution": 512},
        created_at=1000.0,
        completed_at=1010.0,
        started_at=1002.0,
    )
    data.update(kwargs)
    return GenerationTask(**data)


def test_see_through_model_is_registered():
    spec = get_model("see-through")
    assert spec.modality == "layers"
    assert spec.tasks == ("decompose",)
    assert "SeeThrough_GenerateLayers" in spec.required_nodes


def test_build_see_through_workflow():
    task = _task()
    result = build_see_through_task(task)
    classes = [node["class_type"] for node in result.workflow.values()]
    assert classes == [
        "LoadImage",
        "SeeThrough_LoadLayerDiffModel",
        "SeeThrough_LoadDepthModel",
        "SeeThrough_GenerateLayers",
        "SeeThrough_GenerateDepth",
        "SeeThrough_PostProcess",
        "SeeThrough_SavePSD",
        "SaveImage",
    ]
    assert result.output_prefix == "comfy-rest-ext_layers_layers_1"
    assert result.output_mime == "application/zip"


def test_build_passes_exclude_tags():
    task = _task(params={"resolution": 512, "exclude_tags": "wings,tail"})
    result = build_see_through_task(task)
    assert result.workflow["4"]["inputs"]["exclude_tags"] == "wings,tail"


def test_exclude_tags_validation():
    assert _exclude_tags("wings, tail") == "tail,wings"
    assert _exclude_tags("") == ""
    with pytest.raises(ValueError, match="optional body tags"):
        _exclude_tags("wings,head")


def test_build_requires_exactly_one_input():
    with pytest.raises(ValueError, match="exactly one"):
        build_see_through_task(_task(ref_images=[]))
    with pytest.raises(ValueError, match="exactly one"):
        build_see_through_task(_task(ref_images=["a.png", "b.png"]))


def test_layers_summary_reports_manifest_count(tmp_path):
    manifest = tmp_path / "task_layers.json"
    manifest.write_text(json.dumps({"layers": [{"name": "face"}, {"name": "hair"}]}))
    preview = tmp_path / "task_preview_00001_.png"
    preview.write_bytes(b"png")
    task = _task(output_paths=[str(manifest), str(preview)])
    summary = _summary(task)
    assert summary["layer_count"] == 2
    assert summary["preview_url"].endswith("/preview")
    assert summary["archive_url"].endswith("/content")
    assert summary["psd_url"].endswith("/psd")
    assert summary["input_image_url"].endswith("/assets/image/0")


@pytest.mark.asyncio
async def test_layers_decompose_requires_multipart():
    from api import layers

    class Request:
        headers = {}

    response = await layers.decompose(Request())
    assert response.status == 400
