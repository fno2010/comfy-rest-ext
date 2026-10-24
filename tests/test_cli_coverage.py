"""Tests for the multi-level CLI coverage added in #14."""

from __future__ import annotations

import json

import pytest

from cli import commands
from cli.__main__ import _build_parser, _rewrite_dot_params
from cli.client import ComfyRestClient


class Args:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


_SCHEMA = {
    "dimensions": [
        {
            "id": "block_cache",
            "exclusive": True,
            "methods": [
                {"id": "none", "implemented": True, "available": True, "params": {}},
                {
                    "id": "fbc",
                    "implemented": True,
                    "available": True,
                    "required_nodes": ["H3FirstBlockCache"],
                    "missing_nodes": [],
                    "params": {
                        "threshold": {"type": "float", "default": 0.08, "min": 0, "max": 1},
                        "start_step": {"type": "int", "default": 2, "min": 0, "max": 100},
                    },
                },
            ],
        },
    ],
    "presets": {"auto": "auto", "sol-stack": "sol-stack"},
}


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def acceleration_schema(self):
        self.calls.append(("schema",))
        return _SCHEMA

    def create_video(self, payload):
        self.calls.append(("video-json", payload))
        return {"id": "video_new", "status": "queued", **payload}

    def create_video_multipart(self, payload, files):
        self.calls.append(("video-multipart", payload, files))
        return {"id": "video_new", "status": "queued", **payload}

    def get_model(self, model_id):
        return {
            "id": model_id,
            "object": "model",
            "modality": "image",
            "tasks": ["t2i"],
            "capabilities": ["seed"],
            "description": "test model",
        }

    def list_all_models(self, include_hash=False, include_metadata=False, folder=None):
        self.calls.append(("all", include_hash, include_metadata, folder))
        return {"models": [{"folder": "diffusion_models", "path": "a.safetensors",
                            "size": 10}], "total": 1, "folders": ["diffusion_models"]}

    def get_model_info(self, path):
        self.calls.append(("info", path))
        return {"name": "a.safetensors", "path": path, "size": 10}

    def delete_model(self, path, force=False):
        self.calls.append(("delete", path, force))
        return {"deleted": True, "path": path}

    def get_video_content(self, video_id):
        return b"video-bytes"

    def delete_video(self, video_id):
        self.calls.append(("video-delete", video_id))
        return {"deleted": True}

    def get_image_content(self, image_id, index=0):
        self.calls.append(("image-content", image_id, index))
        return b"image-bytes"

    def list_downloads(self, status="all"):
        self.calls.append(("downloads-list", status))
        return [{"task_id": "dl_1", "status": "completed", "progress": 1,
                 "url": "https://example.invalid/m"}]

    def cancel_download(self, task_id):
        self.calls.append(("download-cancel", task_id))
        return {"task_id": task_id, "status": "cancelled"}

    def health(self):
        return {"status": "ok", "extension": "comfy-rest-ext"}

    def install_workflow_deps(self, workflow, async_install=True):
        self.calls.append(("deps-install", workflow, async_install))
        return {"task_id": "deps_1", "status": "queued",
                "packages_to_install": ["numpy"]}

    def get_deps_status(self, task_id):
        return {"task_id": task_id, "status": "completed", "progress": 1.0,
                "installed": ["numpy"], "failed": [], "restart_required": False}

    def check_workflow_deps(self, workflow):
        self.calls.append(("deps-check", workflow))
        return {"missing": ["numpy"], "already_satisfied": ["torch"],
                "can_run": False, "gpu_type": "cuda"}

    def check_node_deps(self, node):
        self.calls.append(("deps-node", node))
        return {"node": node, "required_packages": ["numpy>=1"]}

    def restore_node_deps(self, nodes, async_mode=True):
        self.calls.append(("deps-restore", nodes, async_mode))
        return {"task_id": "deps_2", "status": "queued"}

    def export_snapshot(self, snapshot_id, format="tarball", include_models=False):
        self.calls.append(("snapshot-export", snapshot_id, format, include_models))
        return {"success": True, "snapshot_id": snapshot_id, "path": "/tmp/snap"}

    def import_snapshot(self, path, restore_models=True, restore_nodes=True):
        self.calls.append(("snapshot-import", path, restore_models, restore_nodes))
        return {"success": True, "stdout": "imported\n"}

    def diff_snapshots(self, snapshot_a, snapshot_b):
        self.calls.append(("snapshot-diff", snapshot_a, snapshot_b))
        return {"custom_nodes": {"added": ["a"], "removed": [], "common": []},
                "pip_packages": {"added": [], "removed": ["b"], "common": []}}

    def list_snapshots(self):
        return {"snapshots": [{"name": "s.tar.gz", "size": 10, "path": "/tmp/s"}]}

    def list_nodes(self):
        return {"nodes": [{"name": "node-a", "path": "/tmp/node-a",
                           "is_git": True, "has_init": True}], "count": 1}

    def pack_node(self, node_name, respect_comfyignore=True):
        self.calls.append(("node-pack", node_name, respect_comfyignore))
        return {"success": True, "path": "/tmp/node-a.zip", "size": 10,
                "files_included": 3}

    def validate_node(self, node_name):
        self.calls.append(("node-validate", node_name))
        return {"node_name": node_name, "valid": True, "errors": [],
                "warnings": [], "files_checked": 2}

    def init_node(self, path):
        self.calls.append(("node-init", path))
        return {"success": True, "path": path, "node_name": "node-a",
                "files_created": ["__init__.py"]}

    def list_pr_cache(self):
        return {"cache": [{"name": "u-1-main", "size": 10}], "count": 1,
                "total_size": 10}

    def get_pr_cache_size(self):
        return {"count": 1, "total_size": 10}

    def delete_pr_cache_item(self, pr):
        self.calls.append(("pr-delete", pr))
        return {"deleted": True, "name": pr}

    def clear_pr_cache(self, confirm=True):
        self.calls.append(("pr-clear", confirm))
        return {"cleared": True, "entries_removed": 1}


def test_parser_groups_and_aliases():
    parser = _build_parser()
    args = parser.parse_args(["models", "info", "a.safetensors"])
    assert args.func is commands.cmd_model_info
    assert args.path == "a.safetensors"

    args = parser.parse_args(["videos", "content", "v1", "--out", "x.mp4"])
    assert args.func is commands.cmd_video_content
    assert (args.video_id, args.out) == ("v1", "x.mp4")

    args = parser.parse_args(["--timeout", "5", "health"])
    assert args.http_timeout == 5.0

    args = parser.parse_args(["wait", "v1", "--timeout", "3"])
    assert args.timeout == 3.0

    assert parser.parse_args(["models"]).func is commands.cmd_models
    assert parser.parse_args(["images"]).func is commands.cmd_images


def test_client_query_encoding(monkeypatch):
    client = ComfyRestClient(base_url="http://127.0.0.1:1")
    calls = []

    def fake_request(method, path, body=None, headers=None):
        calls.append((method, path))
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.get_model_info("dir/a b.safetensors")
    client.delete_model("dir/a.safetensors", force=True)
    client.clear_pr_cache()
    client.get_image_content("img 1", index=2)
    client.check_node_deps("KSampler")
    client.list_downloads()

    assert calls == [
        ("GET", "/v2/extension/models/info?path=dir%2Fa+b.safetensors"),
        ("DELETE", "/v2/extension/models/dir/a.safetensors?force=true"),
        ("DELETE", "/v2/extension/frontend/pr-cache?confirm=true"),
        ("GET", "/v2/extension/image/img%201/content?index=2"),
        ("GET", "/v2/extension/dependencies/check?node=KSampler"),
        ("GET", "/v2/extension/model/download?status=all"),
    ]


def test_model_commands(capsys):
    client = FakeClient()
    assert commands.cmd_model_get(client, Args(json=True, model_id="m")) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "m"

    assert commands.cmd_models_all(
        client, Args(json=False, include_hash=True,
                     include_metadata=False, folder="diffusion_models")
    ) == 0
    assert "diffusion_models" in capsys.readouterr().out

    assert commands.cmd_model_info(client, Args(json=True, path="a")) == 0
    assert json.loads(capsys.readouterr().out)["path"] == "a"

    assert commands.cmd_model_delete(
        client, Args(json=False, path="a", force=True)
    ) == 0
    assert "deleted" in capsys.readouterr().out
    assert client.calls[-1] == ("delete", "a", True)


def test_content_commands(tmp_path, capsys):
    client = FakeClient()
    video = tmp_path / "v.mp4"
    assert commands.cmd_video_content(
        client, Args(json=False, video_id="v1", out=str(video))
    ) == 0
    assert video.read_bytes() == b"video-bytes"

    image = tmp_path / "i.png"
    assert commands.cmd_image_content(
        client, Args(json=False, image_id="i1", index=3, out=str(image))
    ) == 0
    assert image.read_bytes() == b"image-bytes"
    assert client.calls[-1] == ("image-content", "i1", 3)

    capsys.readouterr()
    assert commands.cmd_video_delete(
        client, Args(json=True, video_id="v1")
    ) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] is True


def test_download_health_commands(capsys):
    client = FakeClient()
    assert commands.cmd_downloads_list(client, Args(json=True)) == 0
    assert json.loads(capsys.readouterr().out)[0]["task_id"] == "dl_1"
    assert commands.cmd_download_cancel(
        client, Args(json=False, task_id="dl_1")
    ) == 0
    assert "cancelled" in capsys.readouterr().out
    assert commands.cmd_health(client, Args(json=False)) == 0
    assert "comfy-rest-ext" in capsys.readouterr().out


def _workflow(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"1": {"class_type": "KSampler", "inputs": {}}}))
    return path


def test_deps_commands(tmp_path, capsys):
    client = FakeClient()
    wf = _workflow(tmp_path)

    assert commands.cmd_deps_install(
        client, Args(json=True, workflow=str(wf), sync=False)
    ) == 0
    assert json.loads(capsys.readouterr().out)["task_id"] == "deps_1"
    assert client.calls[-1][2] is True

    assert commands.cmd_deps_check_workflow(
        client, Args(json=False, workflow=str(wf))
    ) == 0
    assert "can_run" in capsys.readouterr().out

    assert commands.cmd_deps_status(
        client, Args(json=False, task_id="deps_1")
    ) == 0
    assert "completed" in capsys.readouterr().out

    assert commands.cmd_deps_check_node(
        client, Args(json=True, node="KSampler")
    ) == 0
    assert json.loads(capsys.readouterr().out)["node"] == "KSampler"

    assert commands.cmd_deps_restore(
        client, Args(json=False, node=["a", "b"], sync=True)
    ) == 0
    assert client.calls[-1] == ("deps-restore", ["a", "b"], False)


def test_snapshot_commands(tmp_path, capsys):
    client = FakeClient()
    assert commands.cmd_snapshot_export(
        client, Args(json=True, snapshot_id="s", format="tarball",
                     include_models=True)
    ) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True

    assert commands.cmd_snapshot_import(
        client, Args(json=False, path="/tmp/s", no_restore_models=True,
                     no_restore_nodes=False)
    ) == 0
    assert "imported" in capsys.readouterr().out
    assert client.calls[-1] == ("snapshot-import", "/tmp/s", False, True)

    assert commands.cmd_snapshot_diff(
        client, Args(json=False, snapshot_a="a", snapshot_b="b")
    ) == 0
    assert "custom_nodes" in capsys.readouterr().out

    assert commands.cmd_snapshot_list(client, Args(json=False)) == 0
    assert "s.tar.gz" in capsys.readouterr().out


def test_node_commands(capsys):
    client = FakeClient()
    assert commands.cmd_nodes_list(client, Args(json=True)) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1

    assert commands.cmd_nodes_pack(
        client, Args(json=False, node_name="node-a", no_comfyignore=True)
    ) == 0
    assert "files" in capsys.readouterr().out
    assert client.calls[-1] == ("node-pack", "node-a", False)

    assert commands.cmd_nodes_validate(
        client, Args(json=False, node_name="node-a")
    ) == 0
    assert "valid" in capsys.readouterr().out

    assert commands.cmd_nodes_init(
        client, Args(json=True, path="/tmp/node-a")
    ) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_pr_cache_commands(capsys):
    client = FakeClient()
    assert commands.cmd_pr_cache_list(client, Args(json=False)) == 0
    assert "u-1-main" in capsys.readouterr().out

    assert commands.cmd_pr_cache_size(client, Args(json=True)) == 0
    assert json.loads(capsys.readouterr().out)["total_size"] == 10

    assert commands.cmd_pr_cache_delete(
        client, Args(json=False, pr="u-1-main")
    ) == 0
    assert "deleted" in capsys.readouterr().out

    assert commands.cmd_pr_cache_clear(
        client, Args(json=False, confirm=False)
    ) == 1
    assert "confirm" in capsys.readouterr().err

    assert commands.cmd_pr_cache_clear(
        client, Args(json=False, confirm=True)
    ) == 0
    assert "cleared" in capsys.readouterr().out


def test_request_returns_binary_payload(monkeypatch):
    import cli.client as client_mod

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"\x89PNG\r\n\x1a\nbinary"

    monkeypatch.setattr(client_mod.urllib.request, "urlopen",
                        lambda *a, **k: Response())
    client = ComfyRestClient(base_url="http://127.0.0.1:1")
    assert client.get_image_content("img_1") == b"\x89PNG\r\n\x1a\nbinary"


def test_parser_accepts_all_groups():
    parser = _build_parser()
    argv_list = [
        ["models", "list"],
        ["models", "get", "m"],
        ["models", "all"],
        ["models", "info", "a"],
        ["models", "delete", "a"],
        ["videos", "generate", "p"],
        ["videos", "status", "v"],
        ["videos", "list"],
        ["videos", "wait", "v"],
        ["videos", "content", "v"],
        ["videos", "delete", "v"],
        ["images", "generate", "p"],
        ["images", "edit", "p"],
        ["images", "list"],
        ["images", "status", "i"],
        ["images", "content", "i"],
        ["downloads", "create", "https://example.invalid/m"],
        ["downloads", "status", "d"],
        ["downloads", "list"],
        ["downloads", "cancel", "d"],
        ["deps", "install", "--workflow", "w.json"],
        ["deps", "status", "d"],
        ["deps", "check-workflow", "--workflow", "w.json"],
        ["deps", "check-node", "--node", "KSampler"],
        ["deps", "restore", "--node", "KSampler"],
        ["snapshot", "export", "s"],
        ["snapshot", "import", "--path", "/tmp/s"],
        ["snapshot", "diff", "a", "b"],
        ["snapshot", "list"],
        ["nodes", "list"],
        ["nodes", "pack", "n"],
        ["nodes", "validate", "n"],
        ["nodes", "init", "/tmp/n"],
        ["pr-cache", "list"],
        ["pr-cache", "size"],
        ["pr-cache", "delete", "p"],
        ["pr-cache", "clear", "--confirm"],
        ["health"],
    ]
    for argv in argv_list:
        args = parser.parse_args(argv)
        assert callable(args.func), argv


COVERED_ROUTES = {
    ("GET", "/v1/models"),
    ("GET", "/v1/models/{model_id}"),
    ("POST", "/v1/videos"),
    ("GET", "/v1/videos"),
    ("GET", "/v1/videos/{video_id}"),
    ("DELETE", "/v1/videos/{video_id}"),
    ("GET", "/v1/videos/{video_id}/content"),
    ("POST", "/v1/images/generations"),
    ("POST", "/v1/images/edits"),
    ("GET", "/v2/extension/image"),
    ("GET", "/v2/extension/image/{task_id}"),
    ("GET", "/v2/extension/image/{task_id}/content"),
    ("POST", "/v2/extension/workflow/dependencies"),
    ("GET", "/v2/extension/workflow/dependencies/{task_id}"),
    ("POST", "/v2/extension/workflow/dependencies/check"),
    ("GET", "/v2/extension/dependencies/check"),
    ("POST", "/v2/extension/dependencies/restore"),
    ("GET", "/v2/extension/snapshot/diff"),
    ("POST", "/v2/extension/snapshot/export"),
    ("POST", "/v2/extension/snapshot/import"),
    ("GET", "/v2/extension/snapshot/list"),
    ("GET", "/v2/extension/nodes/list"),
    ("POST", "/v2/extension/nodes/pack"),
    ("POST", "/v2/extension/nodes/validate"),
    ("POST", "/v2/extension/nodes/init"),
    ("GET", "/v2/extension/frontend/pr-cache"),
    ("DELETE", "/v2/extension/frontend/pr-cache"),
    ("GET", "/v2/extension/frontend/pr-cache/size"),
    ("DELETE", "/v2/extension/frontend/pr-cache/{pr}"),
    ("GET", "/v2/extension/health"),
    ("GET", "/v2/extension/model/download"),
    ("POST", "/v2/extension/model/download"),
    ("GET", "/v2/extension/model/download/{task_id}"),
    ("DELETE", "/v2/extension/model/download/{task_id}"),
    ("GET", "/v2/extension/models/all"),
    ("GET", "/v2/extension/models/info"),
    ("DELETE", "/v2/extension/models/{path:.*}"),
}

EXCLUDED_ROUTES = {
    ("GET", "/ui"),
    # Process config endpoint has no CLI command yet.
    ("GET", "/v2/extension/config"),
    ("POST", "/v2/extension/config"),
    # Consumed by the dashboard/CLI schema work; no CLI command yet.
    ("GET", "/v2/extension/acceleration/schema"),
    ("GET", "/v2/extension/ui"),
    ("GET", "/v2/extension/video"),
    ("POST", "/v2/extension/video/generate"),
    ("GET", "/v2/extension/video/{task_id}"),
    ("GET", "/v2/extension/video/{video_id}/assets/{kind}/{index}"),
    ("POST", "/v2/extension/image/generate"),
}


def test_every_registered_route_is_covered_or_excluded():
    import ast
    from pathlib import Path

    routes = set()
    for path in Path("api").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "routes"
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    continue
                routes.add((dec.func.attr.upper(), dec.args[0].value))

    assert routes == COVERED_ROUTES | EXCLUDED_ROUTES
    assert not (COVERED_ROUTES & EXCLUDED_ROUTES)


def test_cmd_generate_multipart(tmp_path, capsys):
    client = FakeClient()
    image = tmp_path / "ref.png"
    image.write_bytes(b"PNGDATA")
    args = Args(
        json=True, prompt="p", image=[str(image)], model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
        steps=None, turbo=None, block_cache=None, step_cache=None,
        attention=None, vae_decode=None, multipart=True,
    )
    assert commands.cmd_generate(client, args) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "video_new"
    kind, payload, files = client.calls[-1]
    assert kind == "video-multipart"
    assert files == [("input_reference", "ref.png", b"PNGDATA")]
    assert payload["prompt"] == "p"


def test_cmd_generate_multipart_rejects_composable_speed(tmp_path, capsys):
    client = FakeClient()
    image = tmp_path / "ref.png"
    image.write_bytes(b"PNGDATA")
    args = Args(
        json=False, prompt="p", image=[str(image)], model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
        steps=8, turbo=None, block_cache=None, step_cache=None,
        attention=None, vae_decode=None, multipart=True,
    )
    assert commands.cmd_generate(client, args) == 1
    assert "multipart" in capsys.readouterr().err
    assert not client.calls


def test_deps_install_sync_polls_to_completion(monkeypatch, capsys):
    client = FakeClient()
    calls = []
    original_install = client.install_workflow_deps
    original_status = client.get_deps_status

    def install(workflow, async_install=True):
        calls.append(("install", async_install))
        return {"task_id": "deps_1", "status": "queued"}

    def status(task_id):
        calls.append(("status", task_id))
        return {"task_id": task_id, "status": "completed", "progress": 1.0}

    monkeypatch.setattr(client, "install_workflow_deps", install)
    monkeypatch.setattr(client, "get_deps_status", status)
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0)

    assert commands.cmd_deps_install(
        client, Args(json=True, workflow="unused", sync=True)
    ) == 1  # missing workflow file fails before calling the client
    assert calls == []

    with __import__("tempfile").NamedTemporaryFile("w", suffix=".json") as f:
        f.write('{"1": {"class_type": "KSampler"}}')
        f.flush()
        assert commands.cmd_deps_install(
            client, Args(json=True, workflow=f.name, sync=True)
        ) == 0
    assert ("install", False) in calls
    assert ("status", "deps_1") in calls


def test_deps_install_reports_failed_status(monkeypatch, capsys):
    client = FakeClient()

    def install(workflow, async_install=True):
        return {"task_id": "deps_1", "status": "queued"}

    def status(task_id):
        return {"task_id": task_id, "status": "failed", "failed": ["numpy"]}

    monkeypatch.setattr(client, "install_workflow_deps", install)
    monkeypatch.setattr(client, "get_deps_status", status)
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0)
    with __import__("tempfile").NamedTemporaryFile("w", suffix=".json") as f:
        f.write('{"1": {"class_type": "KSampler"}}')
        f.flush()
        assert commands.cmd_deps_install(
            client, Args(json=False, workflow=f.name, sync=True)
        ) == 1
    assert "failed" in capsys.readouterr().out


def test_deps_restore_sync_response_exit_code(capsys):
    client = FakeClient()

    def restore(nodes, async_mode=True):
        return {"returncode": 1, "stdout": "partial", "stderr": "boom"}

    client.restore_node_deps = restore
    assert commands.cmd_deps_restore(
        client, Args(json=False, node=["a"], sync=True)
    ) == 1
    captured = capsys.readouterr()
    assert "partial" in captured.out
    assert "boom" in captured.err


def test_nodes_validate_json_invalid_exit_code(capsys):
    client = FakeClient()

    def validate(node_name):
        return {"node_name": node_name, "valid": False, "errors": ["bad"],
                "warnings": [], "files_checked": 1}

    client.validate_node = validate
    assert commands.cmd_nodes_validate(
        client, Args(json=True, node_name="node-a")
    ) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_pr_cache_clear_partial_errors_exit_code(capsys):
    client = FakeClient()

    def clear(confirm=True):
        return {"cleared": True, "entries_removed": 1,
                "errors": [{"item": "x", "error": "denied"}]}

    client.clear_pr_cache = clear
    assert commands.cmd_pr_cache_clear(
        client, Args(json=False, confirm=True)
    ) == 1
    captured = capsys.readouterr()
    assert "denied" in captured.err
    assert "cleared" in captured.out


def test_models_all_prints_hash_and_metadata(monkeypatch, capsys):
    client = FakeClient()

    def all_models(include_hash=False, include_metadata=False, folder=None):
        return {"models": [{"folder": "diffusion_models", "path": "a",
                            "size": 1, "hash": "abc",
                            "metadata": {"format": "pt"}}],
                "total": 1, "folders": ["diffusion_models"]}

    monkeypatch.setattr(client, "list_all_models", all_models)
    assert commands.cmd_models_all(
        client, Args(json=False, include_hash=True,
                     include_metadata=True, folder=None)
    ) == 0
    out = capsys.readouterr().out
    assert "hash: abc" in out
    assert '"format": "pt"' in out


@pytest.mark.parametrize("result", [
    {"task_id": "deps_1", "status": "completed", "failed": ["numpy"]},
    {"task_id": "deps_1", "status": "cancelled", "failed": []},
])
def test_deps_install_sync_non_success_exit(monkeypatch, result):
    client = FakeClient()

    def install(workflow, async_install=True):
        return {"task_id": "deps_1", "status": "queued"}

    def status(task_id):
        return result

    monkeypatch.setattr(client, "install_workflow_deps", install)
    monkeypatch.setattr(client, "get_deps_status", status)
    monkeypatch.setattr(commands, "POLL_INTERVAL", 0)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        f.write('{"1": {"class_type": "KSampler"}}')
        f.flush()
        assert commands.cmd_deps_install(
            client, Args(json=True, workflow=f.name, sync=True)
        ) == 1


def test_build_video_request_video_reference(tmp_path):
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 32)
    req = commands._build_video_request(Args(
        prompt="use <Video 1>", image=None, video=[str(video)], model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
    ))
    assert req["input"][0] == {"type": "input_text", "text": "use <Video 1>"}
    item = req["input"][1]
    assert item["type"] == "input_video"
    assert item["video_url"].startswith("data:video/mp4;base64,")


def test_cmd_generate_multipart_video(tmp_path, capsys):
    client = FakeClient()
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 32)
    args = Args(
        json=True, prompt="p", image=None, video=[str(video)], model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
        steps=None, turbo=None, block_cache=None, step_cache=None,
        attention=None, vae_decode=None, multipart=True,
    )
    assert commands.cmd_generate(client, args) == 0
    kind, payload, files = client.calls[-1]
    assert kind == "video-multipart"
    assert files[0][0] == "video"
    assert files[0][1] == "ref.mp4"


def test_media_type_guessing():
    from cli.client import _guess_media_type
    assert _guess_media_type("a.mp4") == "video/mp4"
    assert _guess_media_type("a.webm") == "video/webm"
    assert _guess_media_type("a.png") == "image/png"
    assert _guess_media_type("a.m4v", "video") == "video/mp4"
    assert _guess_media_type("a.tiff", "input_reference") == "image/png"


def test_build_video_request_single_image_with_video_is_reference(tmp_path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"PNGDATA")
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 16)
    req = commands._build_video_request(Args(
        prompt="<Picture 1> <Video 1>", image=[str(image)],
        video=[str(video)], model=None, width=None, height=None,
        seconds=None, seed=None, speed=None,
    ))
    image_item = next(i for i in req["input"] if i["type"] == "input_image")
    assert image_item["role"] == "reference_image"


def test_build_video_payload_speed_json():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_json='{"steps":4,"block_cache":{"method":"fbc","params":{"threshold":0.12}}}',
    )
    payload = commands._build_video_payload(args)
    assert payload["speed"] == {
        "steps": 4,
        "block_cache": {"method": "fbc", "params": {"threshold": 0.12}},
    }


def test_build_video_payload_speed_json_conflict():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=8, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_json='{"steps":4}',
    )
    with pytest.raises(commands.ApiError):
        commands._build_video_payload(args)


def test_build_video_payload_speed_json_invalid():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_json='{not-json}',
    )
    with pytest.raises(commands.ApiError):
        commands._build_video_payload(args)


def test_parser_accepts_speed_json():
    parser = _build_parser()
    args = parser.parse_args([
        "videos", "generate", "p", "--speed-json", '{"steps":4}',
    ])
    assert args.speed_json == '{"steps":4}'


def test_rewrite_dot_params():
    assert _rewrite_dot_params([
        "--block-cache.threshold", "0.12",
        "--turbo.strength=0.8",
        "--width", "512",
    ]) == [
        "--dotted-param", "block_cache.threshold=0.12",
        "--dotted-param", "turbo.strength=0.8",
        "--width", "512",
    ]


def test_build_video_payload_dot_params():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=8, turbo=None, block_cache="fbc",
        step_cache=None, attention=None, vae_decode=None,
        accel_params=["block_cache.threshold=0.12", "block_cache.start_step=3"],
    )
    payload = commands._build_video_payload(args, schema=_SCHEMA)
    assert payload["speed"] == {
        "steps": 8,
        "block_cache": {
            "method": "fbc",
            "params": {"threshold": 0.12, "start_step": 3},
        },
    }


def test_build_video_payload_dot_param_requires_method():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        accel_params=["block_cache.threshold=0.12"],
    )
    with pytest.raises(commands.ApiError, match="must select a method"):
        commands._build_video_payload(args, schema=_SCHEMA)


def test_build_video_payload_speed_file(tmp_path):
    path = tmp_path / "speed.json"
    path.write_text('{"steps": 4, "block_cache": "fbc"}')
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_file=str(path),
    )
    payload = commands._build_video_payload(args)
    assert payload["speed"] == {"steps": 4, "block_cache": "fbc"}


def test_build_video_payload_speed_json_at_file(tmp_path):
    path = tmp_path / "speed.json"
    path.write_text('{"attention": "sol"}')
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_json="@" + str(path),
    )
    payload = commands._build_video_payload(args)
    assert payload["speed"] == {"attention": "sol"}


def test_build_video_payload_preset():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        preset="sol-stack",
    )
    payload = commands._build_video_payload(args, schema=_SCHEMA)
    assert payload["speed"] == "sol-stack"


def test_cmd_generate_fetches_schema_for_preset():
    client = FakeClient()
    args = Args(
        json=True, prompt="p", image=None, video=None, model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
        steps=None, turbo=None, block_cache=None, step_cache=None,
        attention=None, vae_decode=None, preset="auto", multipart=False,
    )
    assert commands.cmd_generate(client, args) == 0
    assert ("schema",) in client.calls
    kind, payload = client.calls[-1]
    assert kind == "video-json"
    assert payload["speed"] == "auto"


def test_parser_accepts_rewritten_dot_params():
    parser = _build_parser()
    args = parser.parse_args(_rewrite_dot_params([
        "videos", "generate", "p",
        "--block-cache", "fbc",
        "--block-cache.threshold", "0.15",
    ]))
    assert args.accel_params == ["block_cache.threshold=0.15"]


def test_rewrite_rejects_option_as_value():
    with pytest.raises(ValueError, match="requires a value"):
        _rewrite_dot_params([
            "videos", "generate", "p",
            "--turbo.sampler", "--width", "512",
        ])


def test_rewrite_accepts_negative_numeric_value():
    assert _rewrite_dot_params([
        "videos", "generate", "p",
        "--turbo.strength", "-0.5",
    ])[-1] == "turbo.strength=-0.5"


def test_speed_file_rejects_non_utf8(tmp_path):
    path = tmp_path / "speed.json"
    path.write_bytes(b"\xff\xfe\x00")
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo=None, block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
        speed_file=str(path),
    )
    with pytest.raises(commands.ApiError, match="not valid UTF-8"):
        commands._build_video_payload(args)


def test_coerce_accel_param_types_ranges_choices():
    method = {
        "id": "demo",
        "params": {
            "mode": {"type": "str", "choices": ["a", "b"]},
            "count": {"type": "int", "min": 1, "max": 2},
            "ratio": {"type": "float", "min": 0, "max": 1},
            "flag": {"type": "bool"},
        },
    }
    assert commands._coerce_accel_param("x", method, "mode", "a") == "a"
    assert commands._coerce_accel_param("x", method, "count", "2") == 2
    assert commands._coerce_accel_param("x", method, "ratio", "0.5") == 0.5
    assert commands._coerce_accel_param("x", method, "flag", "yes") is True
    with pytest.raises(commands.ApiError, match="must be one of"):
        commands._coerce_accel_param("x", method, "mode", "c")
    with pytest.raises(commands.ApiError, match="must be >= 1"):
        commands._coerce_accel_param("x", method, "count", "0")
    with pytest.raises(commands.ApiError, match="must be <= 1"):
        commands._coerce_accel_param("x", method, "ratio", "2")
    with pytest.raises(commands.ApiError, match="must be a boolean"):
        commands._coerce_accel_param("x", method, "flag", "maybe")
    with pytest.raises(commands.ApiError, match="not a parameter"):
        commands._coerce_accel_param("x", method, "missing", "1")


def test_flat_method_is_schema_validated_not_argparse_choice():
    parser = _build_parser()
    args = parser.parse_args([
        "videos", "generate", "p", "--block-cache", "future-method",
    ])
    assert args.block_cache == "future-method"
    with pytest.raises(commands.ApiError, match="not registered"):
        commands._build_video_payload(args, schema=_SCHEMA)


def test_flat_default_is_schema_rejected():
    args = Args(
        prompt="p", model=None, width=None, height=None, seconds=None,
        seed=None, speed=None, steps=None, turbo="default", block_cache=None,
        step_cache=None, attention=None, vae_decode=None,
    )
    with pytest.raises(commands.ApiError, match="not registered"):
        commands._build_video_payload(args, schema=_SCHEMA)


def test_vae_decode_does_not_fetch_schema():
    client = FakeClient()
    client.calls.clear()
    args = Args(
        json=True, prompt="p", image=None, video=None, model=None,
        width=None, height=None, seconds=None, seed=None, speed=None,
        steps=None, turbo=None, block_cache=None, step_cache=None,
        attention=None, vae_decode="batched", preset=None, multipart=False,
    )
    assert commands.cmd_generate(client, args) == 0
    assert ("schema",) not in client.calls
    kind, payload = client.calls[-1]
    assert kind == "video-json"
    assert payload["speed"] == {"vae_decode": "batched"}
