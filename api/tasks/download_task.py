"""
Download task implementation for model downloads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Tuple

import httpx

logger = logging.getLogger("comfy-rest-ext.download")


@dataclass
class DownloadTask:
    """Download task state."""
    task_id: str
    status: Literal["queued", "downloading", "completed", "failed", "cancelled"]
    url: str
    local_path: Optional[str] = None
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    error: Optional[str] = None
    created_at: float = 0.0
    resolved_url: Optional[str] = None
    filename: Optional[str] = None


def check_civitai_url(url: str) -> Tuple[bool, bool, Optional[str], Optional[str]]:
    """
    Check if URL is a CivitAI model URL.

    Returns: (is_model_url, is_api_url, model_id, version_id)
    """
    # Match civitai.com/models/{id} or civitai.com/model/{id}
    model_match = re.match(
        r"https?://(?:www\.)?civitai\.com/(?:models|model)/(\d+)",
        url,
        re.IGNORECASE,
    )
    if model_match:
        model_id = model_match.group(1)
        version_match = re.search(r"version/(\d+)", url, re.IGNORECASE)
        version_id = version_match.group(1) if version_match else None
        return True, False, model_id, version_id

    # Match api.civitai.com/v1/models/{id}
    api_match = re.match(
        r"https?://api\.civitai\.com/v1/models/(\d+)",
        url,
        re.IGNORECASE,
    )
    if api_match:
        return False, True, api_match.group(1), None

    return False, False, None, None


def check_huggingface_url(url: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Check if URL is a HuggingFace model/page URL.

    Returns: (is_hf, repo_id, filename, folder, branch)
    """
    # Match huggingface.co/{org}/{repo}/blob/{branch}/{path}
    blob_match = re.match(
        r"https?://(?:www\.)?huggingface\.co/([^/]+)/([^/]+)/blob/([^/]+)/(.+)",
        url,
        re.IGNORECASE,
    )
    if blob_match:
        repo_id = f"{blob_match.group(1)}/{blob_match.group(2)}"
        return True, repo_id, blob_match.group(4), None, blob_match.group(3)

    # Match huggingface.co/{org}/{repo}/tree/{branch}
    tree_match = re.match(
        r"https?://(?:www\.)?huggingface\.co/([^/]+)/([^/]+)/tree/([^/]+)/?",
        url,
        re.IGNORECASE,
    )
    if tree_match:
        repo_id = f"{tree_match.group(1)}/{tree_match.group(2)}"
        return True, repo_id, None, None, tree_match.group(3)

    # Match huggingface.co/{org}/{repo}
    hf_base_match = re.match(
        r"https?://(?:www\.)?huggingface\.co/([^/]+)/([^/]+)/?$",
        url,
        re.IGNORECASE,
    )
    if hf_base_match:
        repo_id = f"{hf_base_match.group(1)}/{hf_base_match.group(2)}"
        return True, repo_id, None, None, "main"

    return False, None, None, None, None


async def resolve_civitai_download_url(
    model_id: str,
    version_id: Optional[str] = None,
    token: Optional[str] = None,
) -> Tuple[str, str, int]:
    """
    Resolve CivitAI model URL to download URL.

    Returns: (url, filename, file_size)
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Get model info
        resp = await client.get(
            f"https://civitai.com/api/v1/models/{model_id}",
            headers=headers,
        )
        resp.raise_for_status()
        model_info = resp.json()

        # Find the version to download
        if version_id:
            version_resp = await client.get(
                f"https://civitai.com/api/v1/model-versions/{version_id}",
                headers=headers,
            )
            version_resp.raise_for_status()
            version_info = version_resp.json()
        else:
            # Find primary version
            versions = model_info.get("modelVersions", [])
            version_info = None
            for v in versions:
                if v.get("primary"):
                    version_info = v
                    break
            if not versions:
                raise ValueError(f"No versions found for model {model_id}")
            version_info = versions[-1] if not version_info else version_info

        # Find primary file
        files = version_info.get("files", [])
        primary_file = None
        for f in files:
            if f.get("primary"):
                primary_file = f
                break
        if not files:
            raise ValueError(f"No files found for model {model_id} version {version_info.get('id')}")
        if not primary_file:
            primary_file = files[0]

        download_url = primary_file.get("downloadUrl")
        if not download_url:
            raise ValueError("No download URL found")

        filename = primary_file.get("name", "model.safetensors")
        size = primary_file.get("sizeKB", 0) * 1024

        return download_url, filename, size


async def resolve_huggingface_download_url(
    repo_id: str,
    filename: Optional[str] = None,
    folder: Optional[str] = None,
    branch: str = "main",
    token: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Resolve HuggingFace file to download URL.

    Returns: (url, filename)
    """
    # Try using huggingface_hub if available
    try:
        from huggingface_hub import hf_hub_download
        if filename:
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                folder=folder,
                revision=branch,
                token=token,
            )
            return f"file://{local_path}", filename
    except ImportError:
        pass
    except Exception:
        pass

    # Fall back to direct URL construction
    if filename:
        url = f"https://huggingface.co/{repo_id}/resolve/{branch}/{filename}"
        if folder:
            url = f"https://huggingface.co/{repo_id}/resolve/{branch}/{folder}/{filename}"
        return url, filename

    raise ValueError("filename is required for HuggingFace downloads without hf_hub_download")


async def download_file(
    task_id: str,
    url: str,
    output_path: str,
    chunk_size: int = 1024 * 1024,  # 1MB chunks
    cancellation_event: Optional[asyncio.Event] = None,
    resume_offset: int = 0,
) -> Tuple[int, str]:
    """
    Download a file with progress tracking and resume support.

    Args:
        task_id: Task ID for progress updates
        url: Download URL
        output_path: Local file path
        chunk_size: Download chunk size
        cancellation_event: Event to check for cancellation
        resume_offset: Bytes already downloaded (for resume)

    Returns: (total_bytes, local_path)
    """
    headers = {}
    mode = "wb"

    # Check for resume support
    if resume_offset > 0:
        headers["Range"] = f"bytes={resume_offset}-"
        mode = "ab"  # Append mode for resume

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as response:
            # Handle resume response
            if response.status_code == 206:
                # Partial content - resume supported
                content_range = response.headers.get("Content-Range", "")
                logger.info(f"Resuming download from byte {resume_offset}")
                # Parse total from Content-Range header if available
                # Format: "bytes {start}-{end}/{total}"
                if "/" in content_range:
                    total_str = content_range.split("/")[-1]
                    if total_str.isdigit():
                        total_bytes = int(total_str)
            elif response.status_code == 200:
                # Full content - starting fresh or server doesn't support resume
                total_bytes = int(response.headers.get("Content-Length", 0))
            elif response.status_code == 416:
                # Range Not Satisfiable - file is already complete
                # (server doesn't support Range or we've already downloaded everything)
                logger.info(f"File already complete at {output_path}")
                return resume_offset, output_path
            else:
                response.raise_for_status()
                total_bytes = 0

            downloaded = resume_offset
            total_header = response.headers.get("Content-Length")
            if total_header:
                file_total = int(total_header)
                if resume_offset > 0:
                    total_bytes = resume_offset + file_total

            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with open(output_path, mode) as f:
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    if cancellation_event and cancellation_event.is_set():
                        raise asyncio.CancelledError("Download cancelled")

                    f.write(chunk)
                    downloaded += len(chunk)

                    # Update progress via task queue
                    if total_bytes > 0:
                        progress = downloaded / total_bytes
                        from . import get_task_queue
                        get_task_queue().update_progress(task_id, progress)

            return downloaded, output_path


async def run_download_task(
    task_id: str,
    url: str,
    output_dir: str,
    filename: Optional[str] = None,
    cancellation_event: Optional[asyncio.Event] = None,
) -> str:
    """
    Execute a download task.

    Returns the local path of the downloaded file.
    """
    from . import get_task_queue

    queue = get_task_queue()
    is_civitai, is_api, model_id, version_id = check_civitai_url(url)
    is_hf, repo_id, hf_filename, folder, branch = check_huggingface_url(url)

    resolved_url = url
    resolved_filename = filename

    if is_civitai:
        logger.info(f"Resolving CivitAI model {model_id} version {version_id}")
        resolved_url, resolved_filename, _ = await resolve_civitai_download_url(
            model_id, version_id
        )
    elif is_hf:
        logger.info(f"Resolving HuggingFace file {repo_id}/{hf_filename}")
        resolved_url, resolved_filename = await resolve_huggingface_download_url(
            repo_id, hf_filename or filename, folder, branch
        )

    if not resolved_filename:
        # Extract filename from URL
        resolved_filename = url.split("/")[-1].split("?")[0] or "download"

    output_path = os.path.join(output_dir, resolved_filename)

    # Check for existing partial file (resume support)
    resume_offset = 0
    if os.path.exists(output_path):
        resume_offset = os.path.getsize(output_path)
        if resume_offset > 0:
            logger.info(f"Found partial file {output_path} ({resume_offset} bytes), will resume")

    logger.info(f"Downloading {resolved_url} to {output_path}")

    try:
        downloaded, path = await download_file(
            task_id,
            resolved_url,
            output_path,
            cancellation_event=cancellation_event,
            resume_offset=resume_offset,
        )
        logger.info(f"Download complete: {downloaded} bytes -> {path}")
        return path
    except asyncio.CancelledError:
        # Keep partial file for resume
        logger.info(f"Download cancelled, partial file kept at {output_path}")
        raise