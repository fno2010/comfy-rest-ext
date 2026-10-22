"""Unit tests for OpenAI image request normalization."""

from __future__ import annotations

import base64
import json
import os

import pytest

from api.catalog import get_model
from api.openai.images import BadRequest, _normalize


def test_anima_defaults():
    norm = _normalize(get_model("anima-base"), {"prompt": "a cat"}, edit=False)
    assert norm["width"] == 1024
    assert norm["height"] == 1024
    assert norm["n"] == 1
    assert norm["steps"] == 30
    assert norm["cfg"] == 4.0
    assert norm["response_format"] == "b64_json"
    assert norm["negative_prompt"].startswith("worst quality")


def test_anima_rejects_edit():
    with pytest.raises(BadRequest):
        _normalize(get_model("anima-base"), {"prompt": "x"}, edit=True)


def test_n_limit_per_model():
    with pytest.raises(BadRequest):
        _normalize(get_model("anima-base"), {"prompt": "x", "n": 9}, edit=False)
    with pytest.raises(BadRequest):
        _normalize(get_model("qwen-image-2.1"), {"prompt": "x", "n": 5}, edit=False)


def test_size_and_aspect_ratio_normalization():
    norm = _normalize(
        get_model("qwen-image-2.1"),
        {"prompt": "x", "size": "2048x2048"},
        edit=False,
    )
    assert (norm["width"], norm["height"]) == (2048, 2048)

    norm = _normalize(
        get_model("qwen-image-2.1"),
        {"prompt": "x", "aspect_ratio": "16:9", "megapixels": 1.0},
        edit=False,
    )
    assert norm["width"] % 8 == 0
    assert norm["height"] % 8 == 0


@pytest.mark.parametrize("body", [
    {"prompt": "x", "size": "4096x4096"},
    {"prompt": "x", "response_format": "bytes"},
    {"prompt": "x", "output_format": "jpeg"},
    {"prompt": "x", "stream": True},
    {"prompt": "x", "steps": "many"},
])
def test_invalid_fields_raise(body):
    with pytest.raises(BadRequest):
        _normalize(get_model("qwen-image-2.1"), body, edit=False)


def test_qwen_edit_is_allowed_by_normalize():
    norm = _normalize(
        get_model("qwen-image-2.1"),
        {"prompt": "put <image2> on <image1>", "resolution": 1024},
        edit=True,
    )
    assert norm["resolution"] == 1024


def test_model_card_exposes_modality():
    from api.openai.v1 import _model_card
    card = _model_card("anima-base", 1)
    assert card["modality"] == "image"
    assert card["canonical"] == "anima-base"
    assert "negative_prompt" in card["capabilities"]


@pytest.mark.asyncio
async def test_v1_videos_excludes_image_tasks(monkeypatch):
    import api.openai.v1 as v1
    from api.tasks.video_task import GenerationTask

    def task(task_id, model_id, modality):
        return GenerationTask(
            task_id=task_id,
            status="completed",
            prompt="p",
            task_type="t2va" if modality == "video" else "t2i",
            width=1024,
            height=1024,
            length=101 if modality == "video" else 1,
            seed=1,
            model_id=model_id,
            modality=modality,
            created_at=1.0,
        )

    video = task("video_1", "minimax-h3", "video")
    image = task("image_1", "anima-base", "image")

    class Manager:
        def __init__(self):
            self.tasks = {video.task_id: video, image.task_id: image}

        def list_all(self):
            return dict(self.tasks)

        def restore(self, t):
            self.tasks[t.task_id] = t

    class Persistence:
        def list_active(self):
            return {}

    manager = Manager()
    monkeypatch.setattr(v1, "get_video_manager", lambda: manager)
    monkeypatch.setattr(v1, "get_video_persistence", lambda: Persistence())

    response = await v1.list_videos(None)
    data = json.loads(response.text)["data"]
    assert [item["id"] for item in data] == ["video_1"]
    assert data[0]["object"] == "video"

    class Request:
        match_info = {"video_id": "image_1"}

    monkeypatch.setattr(v1, "_get_task_or_restore", lambda _: image)
    missing = await v1.retrieve_video(Request())
    assert missing.status == 404


@pytest.mark.asyncio
async def test_generations_returns_b64(monkeypatch):
    import folder_paths
    import api.openai.images as images

    output_dir = folder_paths.get_output_directory()
    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, "unit-image.png")
    png_bytes = b"\x89PNG\r\n\x1a\nunit-test"
    with open(png_path, "wb") as f:
        f.write(png_bytes)

    async def fake_submit(task):
        task.status = "completed"
        task.completed_at = task.created_at + 1
        task.progress = 1.0
        task.output_paths = [png_path]
        task.output_path = png_path
        task.output_mime = "image/png"
        task.done.set()
        return task

    monkeypatch.setattr(images, "submit_generation_task", fake_submit)

    class Request:
        async def json(self):
            return {
                "model": "anima-base",
                "prompt": "a cat",
                "size": "1024x1024",
                "n": 1,
            }

    response = await images.create_image(Request())
    assert response.status == 200
    body = json.loads(response.text)
    assert body["model"] == "anima-base"
    assert body["size"] == "1024x1024"
    assert base64.b64decode(body["data"][0]["b64_json"]) == png_bytes

