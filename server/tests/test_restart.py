"""Tests for the controlled restart adapter chain."""

from __future__ import annotations

import pytest

from api import restart


def _safe_available(token="tok"):
    async def status():
        return {
            "id": "safe-restart",
            "available": True,
            "token": token,
            "boot_id": "old",
            "restart_in_progress": False,
        }
    return status


def _manager_available():
    async def status():
        return {"id": "manager", "available": True, "prefix": "/manager", "version": "3.1"}
    return status


@pytest.mark.asyncio
async def test_restart_capabilities_safe_restart(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")
    monkeypatch.setattr(restart, "_safe_restart_status", _safe_available())
    monkeypatch.setattr(restart, "manager_status", _manager_available())
    monkeypatch.setattr(restart, "_active_task_count", lambda: 0)
    caps = await restart.restart_capabilities()
    assert caps["mode"] == "ask"
    assert caps["allowed"] is True
    assert caps["restart_safe"] is True
    assert caps["backends"][0]["id"] == "safe-restart"
    assert "token" not in caps["backends"][0]
    assert caps["backends"][2]["id"] == "external"


@pytest.mark.asyncio
async def test_trigger_safe_restart_uses_token_and_origin(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")
    monkeypatch.setattr(restart, "_safe_restart_status", _safe_available())
    monkeypatch.setattr(restart, "_active_task_count", lambda: 0)
    monkeypatch.setattr(restart, "_base_url", lambda: "http://127.0.0.1:8188")
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 202, {"accepted": True}, ""

    monkeypatch.setattr(restart, "_request", fake_request)
    result = await restart.trigger_restart(
        authorized=True, confirm="restart", reason="provisioning"
    )
    assert result["backend"] == "safe-restart"
    assert result["accepted"] is True
    assert calls[0][1] == "/safe-restart/restart"
    assert calls[0][2]["headers"]["X-ComfyUI-Safe-Restart-Token"] == "tok"
    assert calls[0][2]["headers"]["Origin"] == "http://127.0.0.1:8188"


@pytest.mark.asyncio
async def test_trigger_manager_fallback(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")

    async def no_safe():
        return {"id": "safe-restart", "available": False, "reason": "http_404"}

    async def reboot():
        return {"backend": "manager", "accepted": True}

    monkeypatch.setattr(restart, "_safe_restart_status", no_safe)
    monkeypatch.setattr(restart, "manager_status", _manager_available())
    monkeypatch.setattr(restart, "manager_reboot", reboot)
    monkeypatch.setattr(restart, "_active_task_count", lambda: 0)
    result = await restart.trigger_restart(authorized=True, confirm="restart")
    assert result["backend"] == "manager"
    await __import__("asyncio").sleep(0)


@pytest.mark.asyncio
async def test_trigger_external_plan(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")

    async def no_safe():
        return {"id": "safe-restart", "available": False, "reason": "http_404"}

    async def no_manager():
        return {"id": "manager", "available": False, "reason": "http_404"}

    monkeypatch.setattr(restart, "_safe_restart_status", no_safe)
    monkeypatch.setattr(restart, "manager_status", no_manager)
    monkeypatch.setattr(restart, "_active_task_count", lambda: 0)
    with pytest.raises(restart.RestartError) as exc:
        await restart.trigger_restart(authorized=True, confirm="restart")
    assert exc.value.status == 501
    assert exc.value.plan["backend"] == "external"


@pytest.mark.asyncio
async def test_trigger_refuses_active_tasks(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")
    monkeypatch.setattr(restart, "_active_task_count", lambda: 2)
    with pytest.raises(restart.RestartError) as exc:
        await restart.trigger_restart(authorized=True, confirm="restart")
    assert exc.value.status == 409
    assert exc.value.plan["active_tasks"] == 2


@pytest.mark.asyncio
async def test_trigger_requires_authorization(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "ask")
    with pytest.raises(restart.RestartError) as exc:
        await restart.trigger_restart(authorized=False, confirm="restart")
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_trigger_disabled_mode(monkeypatch):
    monkeypatch.setenv("COMFY_REST_RESTART_MODE", "off")
    with pytest.raises(restart.RestartError) as exc:
        await restart.trigger_restart(authorized=True, confirm="restart")
    assert exc.value.status == 409
    assert exc.value.plan["mode"] == "off"
