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


def test_unsupported_capability_has_no_silent_fallback():
    report = capabilities.capability_report(capability_ids=["step_cache.teacache"])
    item = report["capabilities"][0]
    assert item["status"] == "unsupported"
    assert item["actions"] == []
