"""Tests for the self-contained task dashboard and extension media URLs."""

from __future__ import annotations

import json

import pytest

from api.tasks.video_task import GenerationTask


def _image_task(**kwargs) -> GenerationTask:
    data = dict(
        task_id="image_1",
        status="completed",
        prompt="a cat",
        task_type="t2i",
        width=1024,
        height=1024,
        length=1,
        seed=1,
        model_id="anima-base",
        modality="image",
        created_at=1000.0,
        completed_at=1010.0,
        output_paths=["/tmp/out.png"],
        output_path="/tmp/out.png",
        output_mime="image/png",
    )
    data.update(kwargs)
    return GenerationTask(**data)


@pytest.mark.asyncio
async def test_dashboard_serves_html():
    from api.ui import task_dashboard
    response = await task_dashboard(None)
    assert response.status == 200
    assert response.content_type == "text/html"
    assert "Comfy-REST-Ext" in response.text
    assert "/v2/extension/image" in response.text


def test_image_summary_uses_extension_content_urls():
    from api.openai.images import _image_task_summary
    summary = _image_task_summary(_image_task())
    assert summary["view_urls"] == [
        "/v2/extension/image/image_1/content?index=0"
    ]


def test_video_response_uses_extension_content_url():
    from api.openai.v1 import _video_response
    task = GenerationTask(
        task_id="video_1",
        status="completed",
        prompt="p",
        task_type="t2va",
        width=1024,
        height=768,
        length=101,
        seed=1,
        created_at=1000.0,
        completed_at=1010.0,
        output_path="/tmp/out.mp4",
        output_paths=["/tmp/out.mp4"],
    )
    assert _video_response(task)["view_url"] == "/v1/videos/video_1/content"


@pytest.mark.asyncio
async def test_image_content_requires_image_task(monkeypatch):
    import api.openai.images as images

    monkeypatch.setattr(images, "_get_image_task", lambda _: _image_task())

    class Request:
        match_info = {"task_id": "image_1"}
        query = {"index": "0"}

    response = await images.get_image_content(Request())
    assert response.status == 200
    assert str(response._path) == "/tmp/out.png"


@pytest.mark.asyncio
async def test_image_content_rejects_bad_index(monkeypatch):
    import api.openai.images as images

    monkeypatch.setattr(images, "_get_image_task", lambda _: _image_task())

    class Request:
        match_info = {"task_id": "image_1"}
        query = {"index": "3"}

    response = await images.get_image_content(Request())
    assert response.status == 404


@pytest.mark.asyncio
async def test_dashboard_exposes_reference_video_controls():
    from api.ui import task_dashboard
    response = await task_dashboard(None)
    html = response.text
    assert "gen-video-video-files" in html
    assert "reference_video" in html
    assert 'form.append("video"' in html
    assert "Reference video(s)" in html


@pytest.mark.asyncio
async def test_dashboard_exposes_reference_assets():
    from api.ui import task_dashboard
    response = await task_dashboard(None)
    html = response.text
    assert "reference_video_urls" in html
    assert "reference_image_urls" in html
    assert 'class="refs"' in html


def test_task_summaries_expose_params_and_acceleration():
    from api.openai.images import _image_task_summary
    from api.openai.v1 import _video_response
    from api.tasks.video_task import GenerationTask

    accel = {"steps": 4, "turbo": {"method": "lightx2v-4step-v1.3", "params": {}}}
    video = GenerationTask(
        task_id="v", status="completed", prompt="p", task_type="t2va",
        width=512, height=512, length=39, seed=1,
        params={"seconds": 1.625}, speed={"turbo": "lightx2v-4step-v1.3"},
        acceleration=accel,
    )
    resp = _video_response(video)
    assert resp["params"]["seconds"] == 1.625
    assert resp["params"]["width"] == 512
    assert resp["params"]["height"] == 512
    assert resp["params"]["steps"] == 4
    assert resp["speed"] == {"turbo": "lightx2v-4step-v1.3"}
    assert resp["acceleration"] == accel

    image = GenerationTask(
        task_id="i", status="completed", prompt="p", task_type="t2i",
        width=512, height=512, length=1, seed=1,
        params={"steps": 8, "cfg": 2.0}, acceleration=None,
    )
    summary = _image_task_summary(image)
    assert summary["params"] == {"steps": 8, "cfg": 2.0}
    assert summary["acceleration"] is None


def test_video_params_sampler_matches_resolved_acceleration():
    from api.openai.v1 import _video_response
    from api.tasks.video_task import GenerationTask

    task = GenerationTask(
        task_id="v", status="completed", prompt="p", task_type="t2va",
        width=512, height=512, length=39, seed=1,
        params={"sampler": "dpmpp_2m", "scheduler": "karras", "steps": 20},
        acceleration={"steps": 4, "sampler": "euler", "scheduler": "simple"},
    )
    params = _video_response(task)["params"]
    assert params["sampler"] == "euler"
    assert params["scheduler"] == "simple"
    assert params["steps"] == 4


def test_image_summary_sanitizes_legacy_nonfinite_params():
    import json
    from api.openai.images import _image_task_summary
    from api.tasks.video_task import GenerationTask

    task = GenerationTask(
        task_id="i", status="completed", prompt="p", task_type="t2i",
        width=512, height=512, length=1, seed=1,
        params={"cfg": float("inf"), "nested": {"x": float("nan")}},
    )
    summary = _image_task_summary(task)
    assert summary["params"]["cfg"] is None
    assert summary["params"]["nested"]["x"] is None
    json.dumps(summary, allow_nan=False)
