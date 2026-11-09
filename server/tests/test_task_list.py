"""Tests for the paginated dashboard task list."""

from __future__ import annotations

import json

import pytest

from api.tasks import list as task_list
from api.tasks.video_task import GenerationTask


class Manager:
    def __init__(self, tasks):
        self.tasks = {task.task_id: task for task in tasks}

    def list_all(self):
        return dict(self.tasks)

    def restore(self, task):
        self.tasks[task.task_id] = task


class Persistence:
    def list_active(self):
        return {}


def _task(task_id, *, modality="video", status="completed", model="m", prompt="p", created=1):
    return GenerationTask(
        task_id=task_id,
        status=status,
        prompt=prompt,
        task_type="t2va" if modality == "video" else "t2i",
        width=512,
        height=512,
        length=1,
        seed=1,
        model_id=model,
        modality=modality,
        created_at=created,
        completed_at=created + 1 if status == "completed" else None,
        output_path="/tmp/out" if status == "completed" else None,
        output_paths=["/tmp/out"] if status == "completed" else [],
        output_mime="video/mp4" if modality == "video" else "image/png",
    )


@pytest.fixture
def tasks(monkeypatch):
    values = [
        _task("video_done", model="m1", created=10),
        _task("video_active", status="running", model="m2", created=5),
        _task("image_done", modality="image", model="m1", created=20),
    ]
    monkeypatch.setattr(task_list, "get_video_manager", lambda: Manager(values))
    monkeypatch.setattr(task_list, "get_video_persistence", lambda: Persistence())
    return values


class Request:
    def __init__(self, **query):
        self.query = query


@pytest.mark.asyncio
async def test_task_list_paginates_and_reports_totals(tasks):
    response = await task_list.list_tasks(Request(limit="2", offset="0"))
    body = json.loads(response.text)
    assert body["total"] == 3
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert body["has_more"] is True
    assert [item["id"] for item in body["data"]] == ["video_active", "image_done"]
    assert body["stats"] == {
        "video_total": 2,
        "video_active": 1,
        "image_total": 1,
        "image_active": 0,
        "layers_total": 0,
        "layers_active": 0,
    }
    assert body["models"] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_task_list_filters_before_paginating(tasks):
    response = await task_list.list_tasks(Request(kind="image", limit="1"))
    body = json.loads(response.text)
    assert body["total"] == 1
    assert body["data"][0]["id"] == "image_done"
    assert body["data"][0]["kind"] == "image"


@pytest.mark.asyncio
async def test_task_list_search_and_sort(tasks):
    response = await task_list.list_tasks(Request(q="m2", sort="newest"))
    body = json.loads(response.text)
    assert [item["id"] for item in body["data"]] == ["video_active"]
    response = await task_list.list_tasks(Request(sort="oldest"))
    body = json.loads(response.text)
    assert [item["id"] for item in body["data"]] == [
        "video_active",
        "video_done",
        "image_done",
    ]
