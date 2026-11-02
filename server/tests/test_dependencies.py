"""
Tests for workflow dependency functionality.
"""

import pytest
from api.tasks.deps_task import (
    detect_gpu,
    parse_workflow_dependencies,
    check_workflow_deps,
)


class TestDetectGpu:
    """Tests for GPU detection."""

    def test_detect_returns_string(self):
        result = detect_gpu()
        assert isinstance(result, str)
        assert result in ("cuda", "rocm", "cpu")


class TestParseWorkflowDependencies:
    """Tests for workflow dependency parsing."""

    def test_empty_workflow(self):
        result = parse_workflow_dependencies({})
        assert result == []

    def test_none_workflow(self):
        result = parse_workflow_dependencies(None)
        assert result == []

    def test_workflow_without_class_type(self):
        workflow = {
            "1": {
                "class_type": "NonExistentNode",
            }
        }
        result = parse_workflow_dependencies(workflow)
        # Should return empty list since node doesn't exist
        assert isinstance(result, list)


class TestCheckWorkflowDeps:
    """Tests for workflow dependency checking."""

    @pytest.mark.asyncio
    async def test_check_empty_workflow(self):
        result = await check_workflow_deps({})
        assert "missing" in result
        assert "already_satisfied" in result
        assert "can_run" in result
        assert isinstance(result["can_run"], bool)

    @pytest.mark.asyncio
    async def test_check_returns_gpu_type(self):
        result = await check_workflow_deps({})
        assert "gpu_type" in result
        assert result["gpu_type"] in ("cuda", "rocm", "cpu")
