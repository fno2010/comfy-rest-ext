"""Contract check: every registered server route is covered or explicitly excluded.

This is a server/contract test, not a CLI test; it must run with the server tree
available and must not make the standalone CLI test suite depend on server code.
"""

from __future__ import annotations

COVERED_ROUTES = {
    ("GET", "/v1/models"),
    ("GET", "/v1/models/{model_id}"),
    ("POST", "/v1/videos"),
    ("GET", "/v1/videos"),
    ("GET", "/v1/videos/{video_id}"),
    ("DELETE", "/v1/videos/{video_id}"),
    ("GET", "/v1/videos/{video_id}/content"),
    ("POST", "/v1/videos/{video_id}/retry"),
    ("POST", "/v1/images/generations"),
    ("POST", "/v1/images/edits"),
    ("GET", "/v2/extension/image"),
    ("GET", "/v2/extension/image/{task_id}"),
    ("GET", "/v2/extension/image/{task_id}/content"),
    ("GET", "/v2/extension/image/{task_id}/assets/{kind}/{index}"),
    ("POST", "/v2/extension/layers/decompose"),
    ("GET", "/v2/extension/layers"),
    ("GET", "/v2/extension/layers/{task_id}"),
    ("GET", "/v2/extension/layers/{task_id}/preview"),
    ("GET", "/v2/extension/layers/{task_id}/content"),
    ("GET", "/v2/extension/layers/{task_id}/psd"),
    ("GET", "/v2/extension/layers/{task_id}/assets/image/{index}"),
    ("GET", "/v2/extension/layers/{task_id}/layers"),
    ("GET", "/v2/extension/layers/{task_id}/layer/{index}/content"),
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
    ("GET", "/v2/extension/capabilities"),
    ("GET", "/v2/extension/tasks"),
    ("GET", "/v2/extension/provisioners"),
    ("POST", "/v2/extension/provision"),
    ("GET", "/v2/extension/restart/capabilities"),
    ("POST", "/v2/extension/restart"),
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
    for path in (Path(__file__).resolve().parents[1] / "api").rglob("*.py"):
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
