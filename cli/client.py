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


def _guess_image_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _IMAGE_TYPES.get(ext, "image/png")


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
        except json.JSONDecodeError:
            return data

    def _json_request(self, method: str, path: str, payload: Dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        return self._request(
            method, path, body=body, headers={"Content-Type": "application/json"}
        )

    def list_models(self) -> list:
        data = self._request("GET", "/v1/models")
        return data.get("data", []) if isinstance(data, dict) else []

    def create_video(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._json_request("POST", "/v1/videos", payload)

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
                    f"Content-Type: {_guess_image_type(filename)}\r\n\r\n"
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

    def list_downloads(self) -> list:
        data = self._request("GET", "/v2/extension/model/download")
        return data.get("data", []) if isinstance(data, dict) else []

    def cancel_download(self, task_id: str) -> Dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v2/extension/model/download/{urllib.parse.quote(task_id)}",
        )
