"""Tests for Manager-first provisioning and its explicit fallback rules."""

from __future__ import annotations

import pytest

from api import provisioners, provisioning


@pytest.fixture
def manager_server(monkeypatch):
    monkeypatch.setattr(provisioners, "_base_url", lambda: "http://127.0.0.1:8188")
    return monkeypatch


@pytest.mark.asyncio
async def test_manager_status_and_report(manager_server):
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/manager/version":
            return 200, None, "3.1.0"
        return 404, None, ""

    manager_server.setattr(provisioners, "_request", fake_request)
    status = await provisioners.manager_status()
    assert status == {
        "id": "manager",
        "available": True,
        "prefix": "/manager",
        "version": "3.1.0",
    }
    report = await provisioners.provisioner_report()
    assert report["backends"][0]["available"] is True
    assert report["backends"][1] == {
        "id": "native",
        "available": True,
        "pinned_only": True,
    }


@pytest.mark.asyncio
async def test_manager_install_queues_and_verifies(manager_server):
    calls = []
    installed = {"comfyui-restart-control": {"id": "comfyui-restart-control"}}

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/manager/version":
            return 200, None, "3.1.0"
        if path == "/manager/queue/task":
            assert kwargs["json_body"]["kind"] == "install"
            assert kwargs["json_body"]["params"]["id"] == "comfyui-restart-control"
            assert kwargs["json_body"]["params"]["selected_version"] == "0.1.5"
            return 200, None, ""
        if path == "/manager/queue/start":
            return 200, None, ""
        if path == "/manager/queue/status":
            return 200, {"is_processing": False}, ""
        if path == "/customnode/installed":
            return 200, installed, ""
        raise AssertionError(path)

    manager_server.setattr(provisioners, "_request", fake_request)
    manager_server.setattr(provisioners, "MANAGER_INSTALL_TIMEOUT", 1.0)
    manager_server.setattr(provisioners, "MANAGER_INSTALL_POLL", 0.0)
    action = await provisioners.install_custom_node({
        "kind": "custom_node",
        "manager_id": "comfyui-restart-control",
        "manager_version": "0.1.5",
        "requires_restart": True,
    })
    assert action["backend"] == "manager"
    assert action["manager_id"] == "comfyui-restart-control"
    assert any(path == "/manager/queue/task" for _, path, _ in calls)
    assert any(path == "/customnode/installed" for _, path, _ in calls)


@pytest.mark.asyncio
async def test_manager_spec_does_not_fallback_when_manager_missing(manager_server):
    async def missing():
        return {"id": "manager", "available": False, "reason": "http_404"}

    manager_server.setattr(provisioners, "manager_status", missing)
    with pytest.raises(provisioning.ProvisionError) as exc:
        await provisioning._install_custom_node({
            "kind": "custom_node",
            "manager_id": "some-node",
            "manager_version": "1.0.0",
            "target": "SomeNode",
        })
    assert exc.value.status == 409
    assert exc.value.plan["backend"] == "manager"
    assert exc.value.plan["manager"]["available"] is False


@pytest.mark.asyncio
async def test_manager_install_failure_is_not_reported_as_success(manager_server):
    async def fake_request(method, path, **kwargs):
        if path == "/manager/version":
            return 200, None, "3.1.0"
        if path == "/manager/queue/task":
            return 403, None, "security policy"
        raise AssertionError(path)

    manager_server.setattr(provisioners, "_request", fake_request)
    with pytest.raises(provisioners.BackendError) as exc:
        await provisioners.install_custom_node({
            "kind": "custom_node",
            "manager_id": "some-node",
            "manager_version": "1.0.0",
        })
    assert exc.value.plan["status"] == 403


@pytest.mark.asyncio
async def test_manager_legacy_queue_install_fallback(manager_server):
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(path)
        if path == "/manager/version":
            return 200, None, "3.1.0"
        if path == "/manager/queue/task":
            return 404, None, ""
        if path == "/manager/queue/install":
            assert kwargs["json_body"]["id"] == "legacy-node"
            return 200, None, ""
        if path == "/manager/queue/start":
            return 200, None, ""
        if path == "/manager/queue/status":
            return 200, {"is_processing": False}, ""
        if path == "/customnode/installed":
            return 200, {"LegacyNode": {"cnr_id": "legacy-node"}}, ""
        raise AssertionError(path)

    manager_server.setattr(provisioners, "_request", fake_request)
    manager_server.setattr(provisioners, "MANAGER_INSTALL_TIMEOUT", 1.0)
    manager_server.setattr(provisioners, "MANAGER_INSTALL_POLL", 0.0)
    action = await provisioners.install_custom_node({
        "kind": "custom_node",
        "manager_id": "legacy-node",
        "manager_version": "1.0.0",
    })
    assert action["backend"] == "manager"
    assert "/manager/queue/install" in calls


@pytest.mark.asyncio
async def test_manager_v2_prefix_detection(manager_server):
    async def fake_request(method, path, **kwargs):
        if path == "/manager/version":
            return 404, None, ""
        if path == "/v2/manager/version":
            return 200, None, "V4.2.2"
        raise AssertionError(path)

    manager_server.setattr(provisioners, "_request", fake_request)
    status = await provisioners.manager_status()
    assert status["available"] is True
    assert status["prefix"] == "/v2/manager"


@pytest.mark.asyncio
async def test_manager_install_rejects_unpinned_version(manager_server):
    async def available():
        return {"id": "manager", "available": True, "prefix": "/manager"}

    manager_server.setattr(provisioners, "manager_status", available)
    with pytest.raises(provisioners.BackendError, match="pin"):
        await provisioners.install_custom_node({
            "kind": "custom_node",
            "manager_id": "some-node",
            "manager_version": "latest",
        })


@pytest.mark.asyncio
async def test_manager_install_over_http(monkeypatch):
    from aiohttp import web

    calls = []

    async def version_manager(request):
        return web.Response(status=404)

    async def version_v2(request):
        return web.Response(text="V4.2.2")

    async def queue_task(request):
        calls.append(await request.json())
        return web.Response(status=200)

    async def queue_start(request):
        return web.Response(status=200)

    async def queue_status(request):
        return web.json_response({"is_processing": False})

    async def installed(request):
        return web.json_response({"ComfyUI-KJNodes": {"cnr_id": "comfyui-kjnodes"}})

    app = web.Application()
    app.router.add_get("/manager/version", version_manager)
    app.router.add_get("/v2/manager/version", version_v2)
    app.router.add_post("/v2/manager/queue/task", queue_task)
    app.router.add_post("/v2/manager/queue/start", queue_start)
    app.router.add_get("/v2/manager/queue/status", queue_status)
    app.router.add_get("/v2/customnode/installed", installed)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(provisioners, "_base_url", lambda: f"http://127.0.0.1:{port}")
    monkeypatch.setattr(provisioners, "MANAGER_INSTALL_TIMEOUT", 1.0)
    monkeypatch.setattr(provisioners, "MANAGER_INSTALL_POLL", 0.0)
    try:
        action = await provisioners.install_custom_node({
            "kind": "custom_node",
            "manager_id": "comfyui-kjnodes",
            "manager_version": "35e5956193769d18a13136cdedb73a36a05c73e6",
            "target": "ComfyUI-KJNodes",
        })
    finally:
        await runner.cleanup()
    assert action["backend"] == "manager"
    assert calls and calls[0]["kind"] == "install"
    assert calls[0]["params"]["id"] == "comfyui-kjnodes"


@pytest.mark.asyncio
async def test_manager_spec_with_native_pin_falls_back_when_manager_missing(
    manager_server, tmp_path, monkeypatch
):
    async def missing():
        return {"id": "manager", "available": False, "reason": "http_404"}

    monkeypatch.setattr(provisioners, "manager_status", missing)
    monkeypatch.setenv("COMFYUI_CUSTOM_NODES", str(tmp_path))
    with pytest.raises(provisioning.ProvisionError, match="allowlisted host"):
        await provisioning._install_custom_node({
            "kind": "custom_node",
            "manager_id": "some-node",
            "manager_version": "1.0.0",
            "repo": "https://example.com/not-github.git",
            "commit": "0" * 40,
            "target": "SomeNode",
        })


def test_git_hosts_are_configurable(monkeypatch):
    monkeypatch.setenv("COMFY_REST_GIT_ALLOW_HOSTS", "github.com,gitee.com")
    assert provisioning._validate_git_repo(
        "https://gitee.com/owner/repo.git"
    ) == "https://gitee.com/owner/repo.git"
    with pytest.raises(provisioning.ProvisionError, match="allowlisted"):
        provisioning._validate_git_repo("https://example.com/owner/repo.git")
    with pytest.raises(provisioning.ProvisionError, match="HTTPS"):
        provisioning._validate_git_repo("http://gitee.com/owner/repo.git")
