"""
HTTP client for the comfy-rest-ext REST API.

Pure-stdlib (urllib.request) implementation so the CLI runs on any
Python 3.10+ without any third-party dependency. The base URL can be
injected per-request (CLI flag) or read from the environment.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_BASE_URL = os.environ.get(
    "COMFY_REST_EXT_BASE_URL", "http://127.0.0.1:8188"
)
DEFAULT_TIMEOUT = float(os.environ.get("COMFY_REST_EXT_TIMEOUT", "60"))


_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


def _guess_image_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _IMAGE_TYPES.get(ext, "image/png")


def _guess_media_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _VIDEO_TYPES.get(ext) or _IMAGE_TYPES.get(ext, "application/octet-stream")


class ApiError(Exception):
    """Raised when the server returns a non-2xx response or connection fails."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class ComfyRestClient:
    """Minimal client for the comfy-rest-ext endpoints."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                message = json.loads(payload).get("error", str(payload))
            except (json.JSONDecodeError, AttributeError):
                message = payload.decode("utf-8", errors="replace") or e.reason
            raise ApiError(e.code, message) from e
        except urllib.error.URLError as e:
            raise ApiError(0, f"connection failed: {e.reason}") from e
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return data

    def _json_request(self, method: str, path: str, payload: Dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        return self._request(
            method, path, body=body, headers={"Content-Type": "application/json"}
        )

    @staticmethod
    def _query(params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return ""
        clean: Dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            clean[key] = value
        return "?" + urllib.parse.urlencode(clean, doseq=True) if clean else ""

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", f"{path}{self._query(params)}")

    def _delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("DELETE", f"{path}{self._query(params)}")

    def list_models(self) -> list:
        data = self._request("GET", "/v1/models")
        return data.get("data", []) if isinstance(data, dict) else []

    def create_video(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._json_request("POST", "/v1/videos", payload)

    def create_image(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._json_request("POST", "/v1/images/generations", payload)

    def create_image_edit(
        self,
        payload: Dict[str, Any],
        files: list,
    ) -> Dict[str, Any]:
        boundary = f"----comfyrestext{os.getpid()}{id(payload):x}"
        parts: list = []
        for key, value in payload.items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        for file_field, filename, file_bytes in files:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{filename}"\r\n'
                    f"Content-Type: {_guess_image_type(filename)}\r\n\r\n"
                ).encode("utf-8")
            )
            parts.append(file_bytes)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        return self._request(
            "POST",
            "/v1/images/edits",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def list_images(self) -> list:
        data = self._request("GET", "/v2/extension/image")
        return data.get("tasks", []) if isinstance(data, dict) else []

    def get_image(self, image_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v2/extension/image/{urllib.parse.quote(image_id)}"
        )

    def create_video_multipart(
        self,
        payload: Dict[str, Any],
        files: list,
    ) -> Dict[str, Any]:
        boundary = f"----comfyrestext{os.getpid()}{id(payload):x}"
        parts: list = []
        for key, value in payload.items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        for file_field, filename, file_bytes in files:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{filename}"\r\n'
                    f"Content-Type: {_guess_media_type(filename)}\r\n\r\n"
                ).encode("utf-8")
            )
            parts.append(file_bytes)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        return self._request(
            "POST",
            "/v1/videos",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def get_video(self, video_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/videos/{urllib.parse.quote(video_id)}")

    def list_videos(self) -> list:
        data = self._request("GET", "/v1/videos")
        return data.get("data", []) if isinstance(data, dict) else []

    def delete_video(self, video_id: str) -> Dict[str, Any]:
        return self._request(
            "DELETE", f"/v1/videos/{urllib.parse.quote(video_id)}"
        )

    def create_download(
        self,
        url: str,
        folder: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"url": url}
        if folder:
            payload["folder"] = folder
        if filename:
            payload["filename"] = filename
        return self._json_request(
            "POST", "/v2/extension/model/download", payload
        )

    def get_download(self, task_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v2/extension/model/download/{urllib.parse.quote(task_id)}"
        )

    def list_downloads(self, status: Optional[str] = "all") -> list:
        data = self._get(
            "/v2/extension/model/download", {"status": status}
        )
        return data.get("tasks", []) if isinstance(data, dict) else []

    def cancel_download(self, task_id: str) -> Dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v2/extension/model/download/{urllib.parse.quote(task_id)}",
        )

    # -- models ---------------------------------------------------------
    def get_model(self, model_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v1/models/{urllib.parse.quote(model_id, safe='')}"
        )

    def list_all_models(
        self,
        include_hash: bool = False,
        include_metadata: bool = False,
        folder: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._get("/v2/extension/models/all", {
            "include_hash": include_hash,
            "include_metadata": include_metadata,
            "folder": folder,
        })

    def get_model_info(self, path: str) -> Dict[str, Any]:
        return self._get("/v2/extension/models/info", {"path": path})

    def delete_model(self, path: str, force: bool = False) -> Dict[str, Any]:
        encoded = urllib.parse.quote(path, safe="/")
        return self._delete(f"/v2/extension/models/{encoded}", {"force": force})

    # -- media content ---------------------------------------------------
    def get_video_content(self, video_id: str) -> bytes:
        return self._request(
            "GET", f"/v1/videos/{urllib.parse.quote(video_id)}/content"
        )

    def get_image_content(self, image_id: str, index: int = 0) -> bytes:
        return self._get(
            f"/v2/extension/image/{urllib.parse.quote(image_id)}/content",
            {"index": index},
        )

    # -- workflow dependencies ------------------------------------------
    def install_workflow_deps(
        self, workflow: Dict[str, Any], async_install: bool = True,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/workflow/dependencies",
            {"workflow": workflow, "async_install": async_install},
        )

    def get_deps_status(self, task_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/v2/extension/workflow/dependencies/{urllib.parse.quote(task_id)}",
        )

    def check_workflow_deps(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/workflow/dependencies/check",
            {"workflow": workflow},
        )

    def check_node_deps(self, node: str) -> Dict[str, Any]:
        return self._get("/v2/extension/dependencies/check", {"node": node})

    def restore_node_deps(
        self, nodes: list, async_mode: bool = True,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/dependencies/restore",
            {"nodes": nodes, "async_mode": async_mode},
        )

    # -- snapshots -------------------------------------------------------
    def export_snapshot(
        self,
        snapshot_id: str,
        format: str = "tarball",
        include_models: bool = False,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/snapshot/export",
            {
                "snapshot_id": snapshot_id,
                "format": format,
                "include_models": include_models,
            },
        )

    def import_snapshot(
        self,
        path: str,
        restore_models: bool = True,
        restore_nodes: bool = True,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/snapshot/import",
            {
                "path": path,
                "restore_models": restore_models,
                "restore_nodes": restore_nodes,
            },
        )

    def diff_snapshots(
        self, snapshot_a: str, snapshot_b: str,
    ) -> Dict[str, Any]:
        return self._get("/v2/extension/snapshot/diff", {
            "snapshot_a": snapshot_a,
            "snapshot_b": snapshot_b,
        })

    def list_snapshots(self) -> Dict[str, Any]:
        return self._get("/v2/extension/snapshot/list")

    # -- custom nodes ----------------------------------------------------
    def list_nodes(self) -> Dict[str, Any]:
        return self._get("/v2/extension/nodes/list")

    def pack_node(
        self, node_name: str, respect_comfyignore: bool = True,
    ) -> Dict[str, Any]:
        return self._json_request(
            "POST",
            "/v2/extension/nodes/pack",
            {
                "node_name": node_name,
                "respect_comfyignore": respect_comfyignore,
            },
        )

    def validate_node(self, node_name: str) -> Dict[str, Any]:
        return self._json_request(
            "POST", "/v2/extension/nodes/validate", {"node_name": node_name}
        )

    def init_node(self, path: str) -> Dict[str, Any]:
        return self._json_request(
            "POST", "/v2/extension/nodes/init", {"path": path}
        )

    # -- frontend PR cache ----------------------------------------------
    def list_pr_cache(self) -> Dict[str, Any]:
        return self._get("/v2/extension/frontend/pr-cache")

    def get_pr_cache_size(self) -> Dict[str, Any]:
        return self._get("/v2/extension/frontend/pr-cache/size")

    def delete_pr_cache_item(self, pr: str) -> Dict[str, Any]:
        return self._delete(
            f"/v2/extension/frontend/pr-cache/{urllib.parse.quote(pr, safe='')}"
        )

    def clear_pr_cache(self, confirm: bool = True) -> Dict[str, Any]:
        return self._delete(
            "/v2/extension/frontend/pr-cache", {"confirm": confirm}
        )

    # -- health ----------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return self._get("/v2/extension/health")
