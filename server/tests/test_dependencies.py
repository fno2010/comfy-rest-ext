"""Tests for the generic dependency probe."""

from __future__ import annotations

from api.dependencies import DependencySpec, evaluate_dependency


def test_available_node_dependency():
    dep = DependencySpec(
        id="node_pack",
        kind="custom_node",
        required_nodes=("NodeA", "NodeB"),
        install_spec={"kind": "custom_node", "repo": "https://github.com/a/b.git"},
    )
    report = evaluate_dependency(dep, nodes={"NodeA", "NodeB"})
    assert report["status"] == "available"
    assert report["missing_nodes"] == []
    assert report["actions"] == []


def test_missing_dependency_is_installable_only_with_install_spec():
    dep = DependencySpec(
        id="node_pack",
        kind="custom_node",
        required_nodes=("NodeA",),
        install_spec={"kind": "custom_node", "repo": "https://github.com/a/b.git"},
    )
    report = evaluate_dependency(dep, nodes=set())
    assert report["status"] == "installable"
    assert report["actions"][0]["kind"] == "custom_node"

    plain = DependencySpec(id="plain", kind="custom_node", required_nodes=("NodeA",))
    report = evaluate_dependency(plain, nodes=set())
    assert report["status"] == "unsupported"


def test_commit_mismatch_is_installable(monkeypatch):
    import importlib
    dependencies = importlib.import_module("api.dependencies")

    dep = DependencySpec(
        id="exclude_tags",
        kind="custom_node",
        required_nodes=("NodeA",),
        check_commit=True,
        install_spec={
            "kind": "custom_node",
            "target": "ComfyUI-Test",
            "commit": "a" * 40,
        },
    )
    monkeypatch.setattr(dependencies, "_installed_commit", lambda target: "b" * 40)
    report = evaluate_dependency(dep, nodes={"NodeA"})
    assert report["status"] == "installable"
    assert report["missing_version"] == {"current": "b" * 40, "expected": "a" * 40}
