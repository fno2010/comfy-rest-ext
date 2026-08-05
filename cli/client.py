"""
HTTP client for the comfy-rest-ext REST API.

Thin httpx wrapper that talks to the ComfyUI host. The base URL can be
injected per-request (CLI flag) or read from the environment, so the CLI
works against any running ComfyUI without code changes.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx

DEFAULT_BASE_URL = os.environ.get(
    "COMFY_REST_EXT_BASE_URL", "http://127.0.0.1:8188"
)
DEFAULT_TIMEOUT = float(os.environ.get("COMFY_REST_EXT_TIMEOUT", "60"))


class ApiError(Exception):
    """Raised when the server returns a non-2xx response."""

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

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as e:
            raise ApiError(0, f"connection failed: {e}") from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("error", str(body))
            except json.JSONDecodeError:
                message = resp.text or resp.reason_phrase
            raise ApiError(resp.status_code, message)
        if not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.content

    def list_models(self) -> list:
        data = self._request("GET", "/v1/models")
        return data.get("data", []) if isinstance(data, dict) else []

    def create_video(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/videos", json=payload)

    def create_video_multipart(
        self, payload: Dict[str, Any], files: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._request(
            "POST", "/v1/videos", data=payload, files=files
        )

    def get_video(self, video_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/videos/{video_id}")

    def list_videos(self) -> list:
        data = self._request("GET", "/v1/videos")
        return data.get("data", []) if isinstance(data, dict) else []

    def delete_video(self, video_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/v1/videos/{video_id}")

    def create_download(self, url: str, folder: Optional[str] = None,
                        filename: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"url": url}
        if folder:
            payload["folder"] = folder
        if filename:
            payload["filename"] = filename
        return self._request("POST", "/v2/extension/model/download",
                             json=payload)

    def get_download(self, task_id: str) -> Dict[str, Any]:
        return self._request("GET",
                             f"/v2/extension/model/download/{task_id}")

    def list_downloads(self) -> list:
        data = self._request("GET", "/v2/extension/model/download")
        return data.get("data", []) if isinstance(data, dict) else []

    def cancel_download(self, task_id: str) -> Dict[str, Any]:
        return self._request("DELETE",
                             f"/v2/extension/model/download/{task_id}")
