"""Tests for reference-video input, H3 wiring, and persistence."""

from __future__ import annotations

import base64

import pytest

from api.openai.v1 import (
    _infer_task_type,
    _parse_content_items,
    _resolve_ref_video,
)
from api.tasks.video_task import VideoTask, _record_to_task, _task_to_record
from api.workflows import h3
from api.workflows.h3 import build_h3_workflow


def _find(wf, class_type):
    return next(nid for nid, node in wf.items() if node["class_type"] == class_type)


def test_infer_video_reference_is_r2v():
    assert _infer_task_type({"_ref_videos_data": [b"v"]}) == "r2v"
    assert _infer_task_type({"ref_videos": ["v.mp4"]}) == "r2v"
    assert _infer_task_type({"ref_video_urls": ["https://v"]}) == "r2v"


def test_parse_content_items_video_reference():
    result = _parse_content_items({
        "input": [
            {"type": "input_text", "text": "use <Video 1>"},
            {"type": "input_video", "video_url": "https://example.com/ref.mp4"},
            {"type": "input_video", "video_url": {"url": "https://example.com/ref2.mp4"}},
        ],
    })
    assert result["prompt"] == "use <Video 1>"
    assert result["ref_video_urls"] == [
        "https://example.com/ref.mp4",
        "https://example.com/ref2.mp4",
    ]


def test_h3_workflow_reference_video_nodes():
    wf = build_h3_workflow(
        prompt="use <Video 1>", width=1344, height=768, length=101, seed=0,
        ref_videos=["ref.mp4"],
    )
    assert wf["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_nvfp4.safetensors"
    load_id = _find(wf, "LoadVideo")
    assert wf[load_id]["inputs"]["file"] == "ref.mp4"
    comp_id = _find(wf, "GetVideoComponents")
    assert wf[comp_id]["inputs"]["video"] == [load_id, 0]
    ref_id = _find(wf, "MiniMaxH3ReferenceToVideo")
    cond = wf[ref_id]["inputs"]
    assert cond["ref_videos.ref_video_0"] == [comp_id, 0]
    assert cond["ref_video_audios.ref_video_audio_0"] == [comp_id, 1]


def test_h3_workflow_mixes_image_and_video_refs():
    wf = build_h3_workflow(
        prompt="<Picture 1> <Video 1>", width=1344, height=768, length=101,
        seed=0, ref_images=["ref.png"], ref_videos=["ref.mp4"],
    )
    ref_id = _find(wf, "MiniMaxH3ReferenceToVideo")
    cond = wf[ref_id]["inputs"]
    image_id = _find(wf, "LoadImage")
    video_id = _find(wf, "LoadVideo")
    assert cond["ref_images.ref_image_0"] == [image_id, 0]
    assert cond["ref_videos.ref_video_0"] == [_find(wf, "GetVideoComponents"), 0]
    assert wf[video_id]["inputs"]["file"] == "ref.mp4"


def test_h3_workflow_last_frame():
    wf = build_h3_workflow(
        prompt="p", width=1344, height=768, length=101, seed=0,
        last_frame_path="last.png",
    )
    cond_id = _find(wf, "MiniMaxH3ImageToVideo")
    load_id = _find(wf, "LoadImage")
    assert wf[load_id]["inputs"]["image"] == "last.png"
    assert wf[cond_id]["inputs"]["last_frame"] == [load_id, 0]


def test_build_h3_task_selects_ref2va_for_video(monkeypatch):
    monkeypatch.setattr(h3, "require_asset", lambda *a, **k: None)
    task = VideoTask(
        task_id="video_ref", status="queued", prompt="p", task_type="r2v",
        width=512, height=512, length=39, seed=1, ref_videos=["ref.mp4"],
    )
    built = h3.build_h3_task(task)
    assert built.workflow["1"]["inputs"]["unet_name"] == (
        "minimax_h3_ref2va_pruned_nvfp4.safetensors"
    )
    assert _find(built.workflow, "LoadVideo")


def test_reference_video_and_last_frame_persist():
    task = VideoTask(
        task_id="video_p", status="queued", prompt="p", task_type="r2v",
        width=512, height=512, length=39, seed=1,
        ref_images=["img.png"], ref_videos=["vid.mp4"], last_frame="last.png",
    )
    restored = _record_to_task(_task_to_record(task))
    assert restored.ref_images == ["img.png"]
    assert restored.ref_videos == ["vid.mp4"]
    assert restored.last_frame == "last.png"


def test_h3_advertises_reference_video():
    from api.catalog import get_model
    assert "reference_video" in get_model("minimax-h3").capabilities


@pytest.mark.asyncio
async def test_resolve_data_url_video(monkeypatch):
    calls = {}

    def fake_save(data, prefix):
        calls["data"] = data
        calls["prefix"] = prefix
        return "ref_video_saved.mp4"

    monkeypatch.setattr("api.input_assets.save_input_video", fake_save)
    payload = b"\x00\x00\x00\x18ftypmp42" + b"video"
    url = "data:video/mp4;base64," + base64.b64encode(payload).decode()
    assert await _resolve_ref_video(url) == "ref_video_saved.mp4"
    assert calls == {"data": payload, "prefix": "ref_video"}


class _Request:
    def __init__(self, body):
        self.headers = {"Content-Type": "application/json"}
        self._body = body

    async def json(self):
        return self._body


class _Manager:
    def __init__(self):
        self.task = None

    def create(self, task):
        self.task = task


@pytest.mark.asyncio
async def test_route_accepts_reference_video(monkeypatch):
    import api.openai.v1 as v1

    manager = _Manager()

    async def noop(task):
        return task

    async def resolve(ref):
        return "ref.mp4"

    monkeypatch.setattr(v1, "get_video_manager", lambda: manager)
    monkeypatch.setattr(v1, "submit_video_task", noop)
    monkeypatch.setattr(v1, "_resolve_ref_video", resolve)

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "<Video 1>",
        "task": "r2v",
        "ref_videos": ["data:video/mp4;base64,AAAA"],
    }))
    assert response.status == 200
    assert manager.task.ref_videos == ["ref.mp4"]


@pytest.mark.asyncio
async def test_route_rejects_video_refs_without_capability(monkeypatch):
    import dataclasses
    import api.openai.v1 as v1
    from api.catalog import get_model

    spec = dataclasses.replace(
        get_model("minimax-h3"), capabilities=frozenset()
    )
    monkeypatch.setattr(v1, "get_model", lambda model: spec)

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "p",
        "task": "r2v",
        "ref_videos": ["ref.mp4"],
    }))
    assert response.status == 400
    assert "does not support reference video" in response.text


@pytest.mark.asyncio
async def test_route_rejects_video_refs_for_non_r2v(monkeypatch):
    import api.openai.v1 as v1

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "p",
        "task": "fl2va",
        "ref_videos": ["ref.mp4"],
    }))
    assert response.status == 400
    assert "require task 'r2v'" in response.text


@pytest.mark.asyncio
async def test_route_limits_reference_videos(monkeypatch):
    import api.openai.v1 as v1

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "p",
        "task": "r2v",
        "ref_videos": ["a.mp4", "b.mp4", "c.mp4", "d.mp4"],
    }))
    assert response.status == 400
    assert "at most 3" in response.text


@pytest.mark.asyncio
async def test_route_wires_last_frame(monkeypatch):
    import api.openai.v1 as v1

    manager = _Manager()

    async def noop(task):
        return task

    async def resolve(ref):
        return "last.png"

    monkeypatch.setattr(v1, "get_video_manager", lambda: manager)
    monkeypatch.setattr(v1, "submit_video_task", noop)
    monkeypatch.setattr(v1, "_resolve_ref_image", resolve)

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "p",
        "task": "t2va",
        "last_frame_url": "data:image/png;base64,AAAA",
    }))
    assert response.status == 200
    assert manager.task.last_frame == "last.png"


@pytest.mark.asyncio
async def test_route_accepts_ref_video_urls_alias(monkeypatch):
    import api.openai.v1 as v1

    manager = _Manager()

    async def noop(task):
        return task

    async def resolve(ref):
        return "ref.mp4"

    monkeypatch.setattr(v1, "get_video_manager", lambda: manager)
    monkeypatch.setattr(v1, "submit_video_task", noop)
    monkeypatch.setattr(v1, "_resolve_ref_video", resolve)

    response = await v1.create_video(_Request({
        "model": "minimax-h3",
        "prompt": "p",
        "task": "r2v",
        "ref_video_urls": ["https://example.com/ref.mp4"],
    }))
    assert response.status == 200
    assert manager.task.ref_videos == ["ref.mp4"]


def test_video_response_exposes_reference_urls():
    from api.openai.v1 import _video_response
    task = VideoTask(
        task_id="video_refs", status="completed", prompt="p", task_type="r2v",
        width=512, height=512, length=39, seed=1,
        ref_images=["a.png"], ref_videos=["b.mp4"],
        first_frame="first.png", last_frame="last.png",
        output_path="/tmp/out.mp4", output_paths=["/tmp/out.mp4"],
    )
    resp = _video_response(task)
    assert resp["reference_images"] == ["a.png"]
    assert resp["reference_videos"] == ["b.mp4"]
    assert resp["reference_image_urls"] == [
        "/v2/extension/video/video_refs/assets/image/0"
    ]
    assert resp["reference_video_urls"] == [
        "/v2/extension/video/video_refs/assets/video/0"
    ]
    assert resp["first_frame_url"].endswith("/assets/first_frame/0")
    assert resp["last_frame_url"].endswith("/assets/last_frame/0")


@pytest.mark.asyncio
async def test_video_asset_endpoint_serves_reference(monkeypatch, tmp_path):
    import api.openai.v1 as v1
    import folder_paths

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "ref.mp4").write_bytes(b"video-bytes")
    monkeypatch.setattr(folder_paths, "get_input_directory", lambda: str(input_dir), raising=False)
    task = VideoTask(
        task_id="video_asset", status="completed", prompt="p", task_type="r2v",
        width=512, height=512, length=39, seed=1, ref_videos=["ref.mp4"],
    )
    monkeypatch.setattr(v1, "_get_task_or_restore", lambda _: task)

    class Request:
        match_info = {
            "video_id": "video_asset", "kind": "video", "index": "0",
        }

    response = await v1.get_video_asset(Request())
    assert response.status == 200
    assert str(response._path) == str(input_dir / "ref.mp4")


@pytest.mark.asyncio
async def test_video_asset_endpoint_rejects_traversal(monkeypatch, tmp_path):
    import api.openai.v1 as v1
    import folder_paths

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (tmp_path / "secret.mp4").write_bytes(b"secret")
    monkeypatch.setattr(folder_paths, "get_input_directory", lambda: str(input_dir), raising=False)
    task = VideoTask(
        task_id="video_asset", status="completed", prompt="p", task_type="r2v",
        width=512, height=512, length=39, seed=1, ref_videos=["../secret.mp4"],
    )
    monkeypatch.setattr(v1, "_get_task_or_restore", lambda _: task)

    class Request:
        match_info = {
            "video_id": "video_asset", "kind": "video", "index": "0",
        }

    response = await v1.get_video_asset(Request())
    assert response.status == 404
