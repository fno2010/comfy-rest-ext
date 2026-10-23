"""End-to-end unit test for the generic generation task runner."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from api.tasks.video_task import GenerationTask, submit_generation_task


class FakeQueue:
    def __init__(self, prompt_id_box):
        self.prompt_id_box = prompt_id_box
        self.history = {}
        self.item = None

    def put(self, item):
        self.item = item
        prompt_id = item[1]
        self.history[prompt_id] = {
            "status": {"status_str": "success"},
            "outputs": {
                "10": {
                    "images": [{
                        "filename": "out.png",
                        "subfolder": "image/comfy-rest-ext",
                        "type": "output",
                    }]
                }
            },
        }

    def get_history(self):
        return self.history


@pytest.mark.asyncio
async def test_anima_task_completes_and_resolves_output(monkeypatch):
    import server
    import folder_paths
    import api.tasks.video_task as vt

    queue = FakeQueue(None)
    server.PromptServer.instance.prompt_queue = queue
    server.PromptServer.instance.number = 0

    async def fake_validate(prompt_id, workflow, partial):
        return True, None, [], {}

    execution = types.ModuleType("execution")
    execution.validate_prompt = fake_validate
    monkeypatch.setitem(sys.modules, "execution", execution)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(vt.asyncio, "sleep", no_sleep)
    monkeypatch.setenv("COMFY_REST_UNLOAD_ON_FAMILY_SWITCH", "0")

    task = GenerationTask(
        task_id="image_unit_success",
        status="queued",
        prompt="a cat",
        task_type="t2i",
        width=1024,
        height=1024,
        length=1,
        seed=1,
        model_id="anima-base",
        modality="image",
        created_at=1.0,
    )

    result = await submit_generation_task(task)

    assert result.status == "completed"
    assert result.prompt_id in queue.history
    assert result.output_paths == [
        folder_paths.get_output_directory() + "/image/comfy-rest-ext/out.png"
    ]
    assert result.output_path == result.output_paths[0]
    assert result.output_mime == "image/png"
    assert result.progress == 1.0
    assert result.done.is_set()


def test_acceleration_snapshot_persists():
    from api.tasks.video_task import _record_to_task, _task_to_record

    task = GenerationTask(
        task_id="t", status="queued", prompt="p", task_type="t2va",
        width=512, height=512, length=39, seed=1,
        acceleration={"steps": 4, "turbo": {"method": "x", "params": {}}},
    )
    restored = _record_to_task(_task_to_record(task))
    assert restored.acceleration == task.acceleration
