"""
Tests for task queue functionality.
"""

import asyncio
import pytest
from api.tasks.task_queue import TaskQueue, TaskStatus, TaskInfo


@pytest.fixture
def task_queue():
    return TaskQueue()


class TestTaskQueue:
    """Tests for TaskQueue."""

    @pytest.mark.asyncio
    async def test_create_task(self, task_queue):
        async def dummy_coro():
            return "result"

        task_id = task_queue.create_task(dummy_coro(), name="test-task")
        assert task_id is not None
        assert len(task_id) > 0

    @pytest.mark.asyncio
    async def test_task_completes(self, task_queue):
        async def slow_coro():
            await asyncio.sleep(0.01)
            return "done"

        task_id = task_queue.create_task(slow_coro(), name="slow-task")

        # Wait for completion
        await asyncio.sleep(0.1)

        info = task_queue.get_info(task_id)
        assert info is not None
        assert info.status == TaskStatus.COMPLETED
        assert info.result == "done"

    @pytest.mark.asyncio
    async def test_task_cancellation(self, task_queue):
        async def long_coro():
            while True:
                await asyncio.sleep(1)

        task_id = task_queue.create_task(long_coro(), name="long-task")
        task_queue.cancel(task_id)

        # Give it time to cancel
        await asyncio.sleep(0.01)

        info = task_queue.get_info(task_id)
        assert info.status == TaskStatus.CANCELLED

    def test_get_info(self, task_queue):
        async def dummy():
            pass

        task_id = task_queue.create_task(dummy(), name="test")
        info = task_queue.get_info(task_id)
        assert info is not None
        assert info.task_id == task_id

    def test_list_tasks(self, task_queue):
        async def dummy1():
            pass

        async def dummy2():
            pass

        task_queue.create_task(dummy1(), name="task1")
        task_queue.create_task(dummy2(), name="task2")

        tasks = task_queue.list_tasks()
        assert len(tasks) >= 2
