"""Unit tests for the comfy-rest-ext CLI commands."""

from __future__ import annotations

import json

import pytest

from cli import commands


class FakeClient:
    """In-memory stand-in for ComfyRestClient."""

    def __init__(self) -> None:
        self.models = [
            {"id": "minimax-h3-t2v", "owned_by": "comfy-rest-ext"},
            {"id": "minimax-h3-i2v", "owned_by": "comfy-rest-ext"},
        ]
        self.videos = {
            "video_1": {
                "id": "video_1",
                "status": "completed",
                "model": "minimax-h3-t2v",
                "prompt": "a cat",
                "progress": 100,
                "size": "1344x768",
                "seconds": "5.0",
                "view_url": "http://localhost:8188/view?filename=x.mp4",
            }
        }
        self.created = []

    def list_models(self):
        return self.models

    def create_video(self, payload):
        task = {"id": "video_new", "status": "queued", **payload}
        self.created.append(task)
        return task

    def create_video_multipart(self, payload, file_field, filename, file_bytes):
        return self.create_video(payload)

    def get_video(self, video_id):
        if video_id not in self.videos:
            raise commands.ApiError(404, "Video not found")
        return self.videos[video_id]

    def list_videos(self):
        return list(self.videos.values())

    def create_download(self, url, folder=None, filename=None):
        return {"task_id": "dl_1", "status": "queued", "url": url}

    def get_download(self, task_id):
        return {"task_id": task_id, "status": "completed"}


class Args:
    """Namespace with the attributes a command reads."""

    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_cmd_models_json(capsys):
    client = FakeClient()
    assert commands.cmd_models(client, Args(json=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert out[0]["id"] == "minimax-h3-t2v"


def test_cmd_models_text(capsys):
    client = FakeClient()
    assert commands.cmd_models(client, Args(json=False)) == 0
    out = capsys.readouterr().out
    assert "minimax-h3-t2v" in out


def test_cmd_generate_json(capsys):
    client = FakeClient()
    args = Args(
        json=True, prompt="a dog", model=None, width=None, height=None,
        seconds=None, seed=None, image=None,
    )
    assert commands.cmd_generate(client, args) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "queued"
    assert task["prompt"] == "a dog"
    assert client.created


def test_cmd_generate_missing_image(capsys):
    client = FakeClient()
    args = Args(
        json=False, prompt="p", model=None, width=None, height=None,
        seconds=None, seed=None, image="/nonexistent/x.png",
    )
    assert commands.cmd_generate(client, args) == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_status_json(capsys):
    client = FakeClient()
    args = Args(json=True, video_id="video_1")
    assert commands.cmd_status(client, args) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "completed"
    assert task["view_url"].endswith("x.mp4")


def test_cmd_status_missing(capsys):
    client = FakeClient()
    args = Args(json=False, video_id="nope")
    assert commands.cmd_status(client, args) == 1
    assert "404" in capsys.readouterr().err


def test_cmd_list_json(capsys):
    client = FakeClient()
    args = Args(json=True)
    assert commands.cmd_list(client, args) == 0
    tasks = json.loads(capsys.readouterr().out)
    assert len(tasks) == 1


def test_cmd_wait_completed(monkeypatch, capsys):
    client = FakeClient()
    monkeypatch.setattr(commands.time, "time", lambda: 0.0)
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0.0)
    args = Args(json=True, video_id="video_1", timeout=60, quiet=False)
    assert commands.cmd_wait(client, args) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "completed"


def test_cmd_wait_failed(monkeypatch, capsys):
    client = FakeClient()
    client.videos["video_1"]["status"] = "failed"
    monkeypatch.setattr(commands.time, "time", lambda: 0.0)
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0.0)
    args = Args(json=True, video_id="video_1", timeout=60, quiet=True)
    assert commands.cmd_wait(client, args) == 1


def test_cmd_wait_no_timeout_waits_until_done(monkeypatch, capsys):
    client = FakeClient()
    client.videos["video_1"]["status"] = "in_progress"
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0.0)

    poll_count = {"n": 0}
    original_get = client.get_video

    def get_video_transitioning(video_id):
        poll_count["n"] += 1
        if poll_count["n"] >= 2:
            client.videos[video_id]["status"] = "completed"
        return original_get(video_id)

    monkeypatch.setattr(client, "get_video", get_video_transitioning)
    args = Args(json=True, video_id="video_1", timeout=None, quiet=True)
    assert commands.cmd_wait(client, args) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "completed"


def test_cmd_wait_timeout_elapsed(monkeypatch, capsys):
    client = FakeClient()
    client.videos["video_1"]["status"] = "in_progress"
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0.0)

    clock = {"t": 0.0}
    monkeypatch.setattr(commands.time, "time", lambda: clock["t"])
    monkeypatch.setattr(
        commands.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + 1.0)
    )
    args = Args(json=True, video_id="video_1", timeout=5, quiet=True)
    assert commands.cmd_wait(client, args) == 1
    assert "timed out" in capsys.readouterr().err


def test_cmd_download_json(capsys):
    client = FakeClient()
    args = Args(json=True, url="https://civitai.com/models/123",
                folder=None, filename=None)
    assert commands.cmd_download(client, args) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["task_id"] == "dl_1"


def test_cmd_download_status_text(capsys):
    client = FakeClient()
    args = Args(json=False, task_id="dl_1")
    assert commands.cmd_download_status(client, args) == 0
    out = capsys.readouterr().out
    assert "completed" in out
