"""
Model management endpoints (Phase 2).

Provides recursive model listing, metadata retrieval, and deletion.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from dataclasses import dataclass
from typing import Any, Optional

from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes

logger = logging.getLogger("comfy-rest-ext.management")


def get_model_folders() -> dict[str, str]:
    """Get ComfyUI model folder paths."""
    try:
        import sys
        sys.path.insert(0, '/comfy/mnt/ComfyUI')
        from folder_paths import get_folder_paths

        # get_folder_paths(folder_name: str) -> list[str]
        common_folders = [
            "checkpoints", "clip", "vae", "controlnet", "gligen",
            "ultralytics", "models", "loras", "embeddings", "upscale_models"
        ]
        result = {}
        for name in common_folders:
            try:
                paths = get_folder_paths(name)
                if paths:
                    result[name] = paths[0]  # Take first path
            except Exception:
                pass
        return result

    except ImportError:
        # Fallback paths
        base = os.path.join(os.path.expanduser("~"), "ComfyUI", "models")
        return {
            "checkpoints": os.path.join(base, "checkpoints"),
            "clip": os.path.join(base, "clip"),
            "vae": os.path.join(base, "vae"),
            "controlnet": os.path.join(base, "controlnet"),
            "gligen": os.path.join(base, "gligen"),
            "ultralytics": os.path.join(base, "ultralytics"),
        }


def get_protected_models() -> set[str]:
    """Get set of protected model paths from ComfyUI Manager if available."""
    protected = set()
    try:
        import main as comfy_main
        if hasattr(comfy_main, "manager"):
            manager = comfy_main.manager
            if hasattr(manager, "protected_models"):
                protected = set(manager.protected_models)
    except Exception:
        pass
    return protected


def is_model_in_use(model_path: str) -> bool:
    """Check if a model is currently being used by a running execution."""
    try:
        import execution
        if hasattr(execution, "current_exec"):
            cur = execution.current_exec
            if cur and hasattr(cur, "model_loaded"):
                # Rough check - a real implementation would track loaded models
                pass
    except Exception:
        pass
    return False


@dataclass
class ModelInfo:
    """Model file metadata."""
    path: str
    name: str
    size: int
    hash: Optional[str] = None
    metadata: Optional[dict] = None


def parse_safetensors_header(path: str) -> Optional[dict]:
    """Parse safetensors file header to extract metadata."""
    try:
        with open(path, "rb") as f:
            # Read header size (8 bytes little endian)
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return None

            header_size = struct.unpack("<Q", header_size_bytes)[0]

            # Read header JSON
            header_bytes = f.read(header_size)
            if len(header_bytes) < header_size:
                return None

            import json
            return json.loads(header_bytes.decode("utf-8"))
    except Exception as e:
        logger.debug(f"Could not parse safetensors header for {path}: {e}")
        return None


def parse_ckpt_metadata(path: str) -> Optional[dict]:
    """Parse checkpoint file for basic metadata."""
    try:
        import pickle
        with open(path, "rb") as f:
            # Skip the header and read the pickle
            # ckpt files have a specific format we won't fully parse here
            f.seek(0)
            # Just try to read basic info
            data = pickle.load(f)
            return {"format": "ckpt", "keys": len(data) if isinstance(data, dict) else 0}
    except Exception:
        return None


def get_file_hash(path: str, algorithm: str = "blake3") -> Optional[str]:
    """Calculate file hash."""
    try:
        if algorithm == "blake3":
            try:
                import blake3
                hasher = blake3.blake3()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)
                return hasher.hexdigest()
            except ImportError:
                algorithm = "sha256"

        if algorithm == "sha256":
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()

    except Exception as e:
        logger.debug(f"Could not hash {path}: {e}")
        return None


@routes.get("/v2/extension/models/all")
async def list_all_models(request: web.Request) -> web.Response:
    """
    Recursively list all models in ComfyUI model folders.

    Query params:
    - include_hash: bool - calculate file hash (slower)
    - folder: str - filter by specific folder type
    """
    include_hash = request.query.get("include_hash", "false").lower() == "true"
    folder_filter = request.query.get("folder", None)

    folders = get_model_folders()
    if folder_filter:
        if folder_filter not in folders:
            return web.json_response(
                {"error": f"Unknown folder type: {folder_filter}"},
                status=400,
            )
        folders = {folder_filter: folders[folder_filter]}

    models = []

    for folder_name, folder_path in folders.items():
        if not os.path.exists(folder_path):
            continue

        for root, _, files in os.walk(folder_path):
            for filename in files:
                if filename.startswith("."):
                    continue

                filepath = os.path.join(root, filename)
                try:
                    stat = os.stat(filepath)
                    size = stat.st_size

                    metadata = None
                    file_hash = None

                    if filename.endswith(".safetensors"):
                        metadata = parse_safetensors_header(filepath)
                    elif filename.endswith((".ckpt", ".pt", ".pth")):
                        metadata = parse_ckpt_metadata(filepath)

                    if include_hash:
                        file_hash = get_file_hash(filepath)

                    # Relative path from models base
                    rel_path = os.path.relpath(filepath, folder_path)

                    models.append({
                        "name": filename,
                        "path": rel_path,
                        "full_path": filepath,
                        "folder": folder_name,
                        "size": size,
                        "hash": file_hash,
                        "metadata": metadata,
                    })

                except Exception as e:
                    logger.warning(f"Error processing {filepath}: {e}")

    return web.json_response({
        "models": models,
        "total": len(models),
        "folders": list(folders.keys()),
    })


@routes.get("/v2/extension/models/info")
async def get_model_info(request: web.Request) -> web.Response:
    """
    Get metadata for a specific model file.

    Query params:
    - path: str - path to the model file (relative or absolute)
    """
    model_path = request.query.get("path")
    if not model_path:
        return web.json_response(
            {"error": "Missing 'path' query parameter"},
            status=400,
        )

    folders = get_model_folders()

    # Try as absolute path first
    if os.path.isabs(model_path) and os.path.exists(model_path):
        filepath = model_path
    else:
        # Search in model folders
        filepath = None
        for folder_path in folders.values():
            test_path = os.path.join(folder_path, model_path)
            if os.path.exists(test_path):
                filepath = test_path
                break

        if filepath is None:
            return web.json_response(
                {"error": f"Model not found: {model_path}"},
                status=404,
            )

    if not os.path.exists(filepath):
        return web.json_response(
            {"error": f"Model not found: {model_path}"},
            status=404,
        )

    try:
        stat = os.stat(filepath)
        filename = os.path.basename(filepath)

        metadata = None
        if filename.endswith(".safetensors"):
            metadata = parse_safetensors_header(filepath)
        elif filename.endswith((".ckpt", ".pt", ".pth")):
            metadata = parse_ckpt_metadata(filepath)

        file_hash = get_file_hash(filepath)

        return web.json_response({
            "name": filename,
            "path": model_path,
            "full_path": filepath,
            "size": stat.st_size,
            "hash": file_hash,
            "metadata": metadata,
            "created": stat.st_ctime,
            "modified": stat.st_mtime,
        })

    except Exception as e:
        logger.exception(f"Error getting model info for {model_path}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.delete("/v2/extension/models/{path:.*}")
async def delete_model(request: web.Request) -> web.Response:
    """
    Delete a model file.

    Path is URL-encoded path relative to model folder or absolute path.

    Query params:
    - force: bool - force deletion even if protected (default false)
    """
    model_rel_path = request.match_info["path"]
    force = request.query.get("force", "false").lower() == "true"

    folders = get_model_folders()
    protected = get_protected_models() if not force else set()

    # Find the file
    filepath = None
    folder_name = None

    if os.path.isabs(model_rel_path) and os.path.exists(model_rel_path):
        filepath = model_rel_path
    else:
        for fname, fpath in folders.items():
            test_path = os.path.join(fpath, model_rel_path)
            if os.path.exists(test_path):
                filepath = test_path
                folder_name = fname
                break

    if filepath is None or not os.path.exists(filepath):
        return web.json_response(
            {"error": "Model not found"},
            status=404,
        )

    # Check protection
    if not force and filepath in protected:
        return web.json_response(
            {"error": "Model is protected and cannot be deleted"},
            status=403,
        )

    # Check if in use
    if is_model_in_use(filepath):
        return web.json_response(
            {"error": "Model is currently in use by a running execution"},
            status=409,
        )

    try:
        os.remove(filepath)
        logger.info(f"Deleted model: {filepath}")
        return web.json_response({
            "deleted": True,
            "path": filepath,
        })
    except Exception as e:
        logger.exception(f"Error deleting model {filepath}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )
