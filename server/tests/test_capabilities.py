"""Tests for capability discovery and consented provisioning."""

from __future__ import annotations

import pytest

from api import capabilities, provisioning


def _find(report, capability_id):
    return next(item for item in report["capabilities"] if item["id"] == capability_id)


def test_capability_report_exposes_install_plan():
    report = capabilities.capability_report()
    flash = _find(report, "attention.flash")
    cachedit = _find(report, "block_cache.cachedit")

    assert flash["status"] in ("installable", "available")
    if flash["status"] == "installable":
        assert flash["missing_packages"]
        assert flash["actions"][0]["kind"] == "python_package"
    assert cachedit["status"] == "unsupported"
    assert cachedit["implemented"] is False
    assert "summary" in report
    spectrum = _find(report, "step_cache.spectrum")
    assert spectrum["install_spec"]["kind"] == "custom_node"
    assert len(spectrum["install_spec"]["commit"]) == 40


@pytest.mark.asyncio
async def test_provision_requires_authorization(monkeypatch):
    monkeypatch.delenv("COMFY_REST_PROVISION_MODE", raising=False)
    with pytest.raises(provisioning.ProvisionError) as exc:
        await provisioning.provision_capabilities(["attention.flash"], authorized=False)
    assert exc.value.status == 409
    assert exc.value.plan["status"] == "authorization_required"


@pytest.mark.asyncio
async def test_provision_never_mode_refuses(monkeypatch):
    monkeypatch.setenv("COMFY_REST_PROVISION_MODE", "never")
    with pytest.raises(provisioning.ProvisionError) as exc:
        await provisioning.provision_capabilities(["attention.flash"], authorized=True)
    assert exc.value.status == 409
    assert exc.value.plan["mode"] == "never"


@pytest.mark.asyncio
async def test_provision_authorized_installs_declared_package(monkeypatch):
    monkeypatch.setenv("COMFY_REST_PROVISION_MODE", "ask")
    calls = []
    select_calls = {"count": 0}

    async def fake_install(spec):
        calls.append(spec)
        return {"kind": "python_package", "package": spec["package"]}

    def fake_select(ids):
        select_calls["count"] += 1
        if select_calls["count"] == 1:
            return [{
                "id": "attention.flash",
                "status": "installable",
                "available": False,
                "install_spec": {
                    "kind": "python_package",
                    "package": "flash-attn",
                    "requires_restart": False,
                },
                "requires_restart": False,
            }]
        return [{
            "id": "attention.flash",
            "status": "available",
            "available": True,
            "install_spec": {},
            "requires_restart": False,
        }]

    monkeypatch.setattr(provisioning, "_install_python_package", fake_install)
    monkeypatch.setattr(provisioning, "_select_capabilities", fake_select)
    result = await provisioning.provision_capabilities(
        ["attention.flash"], authorized=True
    )
    assert calls and calls[0]["package"] == "flash-attn"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_custom_node_installer_rejects_unpinned_source(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYUI_CUSTOM_NODES", str(tmp_path))
    with pytest.raises(provisioning.ProvisionError, match="allowlisted host"):
        await provisioning._install_custom_node({
            "kind": "custom_node",
            "repo": "https://example.com/untrusted.git",
            "commit": "0" * 40,
            "target": "x",
        })
    with pytest.raises(provisioning.ProvisionError, match="40-character"):
        await provisioning._install_custom_node({
            "kind": "custom_node",
            "repo": "https://github.com/a/b.git",
            "commit": "main",
            "target": "x",
        })


def test_unsupported_capability_has_no_silent_fallback():
    report = capabilities.capability_report(capability_ids=["step_cache.teacache"])
    item = report["capabilities"][0]
    assert item["status"] == "unsupported"
    assert item["actions"] == []


@pytest.mark.asyncio
async def test_video_task_blocks_and_retries_after_provisioning(monkeypatch):
    import asyncio
    import json
    import api.openai.v1 as v1

    created = {}

    class Manager:
        def create(self, task):
            created["task"] = task

        def get(self, task_id):
            return created.get("task")

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(created["task"], key, value)
            return True

    submitted = []

    async def fake_submit(task):
        submitted.append(task)
        return task

    import api.tasks.resume as resume_mod
    manager = Manager()
    monkeypatch.setattr(v1, "get_video_manager", lambda: manager)
    monkeypatch.setattr(v1, "submit_video_task", fake_submit)
    monkeypatch.setattr(v1, "check_config_available", lambda cfg: "missing capability")
    monkeypatch.setattr(resume_mod, "get_video_manager", lambda: manager)
    monkeypatch.setattr(resume_mod, "submit_video_task", fake_submit)
    monkeypatch.setattr(resume_mod, "check_config_available", lambda cfg: "missing capability")

    class Request:
        headers = {}

        async def json(self):
            return {
                "model": "minimax-h3",
                "prompt": "p",
                "seconds": 1,
                "speed": {"attention": "flash"},
            }

    response = await v1.create_video(Request())
    assert response.status == 409
    body = json.loads(response.text)
    assert body["task"]["status"] == "blocked_on_provisioning"
    assert body["plan"]["capabilities"]
    assert created["task"].status == "blocked_on_provisioning"
    assert not submitted

    # After provisioning succeeds, retry resumes the original task.
    monkeypatch.setattr(v1, "check_config_available", lambda cfg: None)
    monkeypatch.setattr(resume_mod, "check_config_available", lambda cfg: None)

    class RetryRequest:
        match_info = {"video_id": created["task"].task_id}

    response = await v1.retry_video(RetryRequest())
    await asyncio.sleep(0)
    assert response.status == 200
    assert created["task"].status == "queued"
    assert created["task"].capability_plan is None
    assert submitted == [created["task"]]


@pytest.mark.asyncio
async def test_empty_deployment_capabilities_are_empty(monkeypatch):
    from api import capabilities as caps

    monkeypatch.setattr(caps, "available_models", lambda: ())
    class Request:
        query = {}
    response = await caps.get_capabilities(Request())
    import json
    body = json.loads(response.text)
    assert body["capabilities"] == []
    assert body["summary"] == {}


@pytest.mark.asyncio
async def test_empty_deployment_can_request_provisioning_inventory(monkeypatch):
    from api import capabilities as caps

    monkeypatch.setattr(caps, "available_models", lambda: ())
    class Request:
        query = {"include_unavailable": "1"}
    response = await caps.get_capabilities(Request())
    import json
    body = json.loads(response.text)
    assert body["capabilities"]
    assert "summary" in body
