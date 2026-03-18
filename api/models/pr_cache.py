"""
Frontend PR cache management endpoints (Phase 7).

Provides listing and deletion of PR cache directories.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import List, Optional

from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes

logger = logging.getLogger("comfy-rest-ext.pr_cache")


def get_pr_cache_dir() -> str:
    """Get the PR cache directory path."""
    return os.path.join(
        os.path.expanduser("~"),
        ".config",
        "comfy-cli",
        "pr-cache",
        "frontend",
    )


def parse_pr_cache_name(name: str) -> Optional[dict]:
    """
    Parse a PR cache directory name.

    Expected format: {user}-{pr_number}-{branch}
    """
    parts = name.rsplit("-", 2)
    if len(parts) >= 3:
        # Last two parts are pr_number and branch
        # But pr_number could have dashes, so we use rsplit with maxsplit=2
        pass

    # Try a different approach: look for GitHub PR pattern
    # Format: username-pr_number-branch
    import re
    match = re.match(r"^(.+)-(\d+)-(.+)$", name)
    if match:
        return {
            "user": match.group(1),
            "pr_number": int(match.group(2)),
            "branch": match.group(3),
        }
    return None


@routes.get("/v2/extension/frontend/pr-cache")
async def list_pr_cache(request: web.Request) -> web.Response:
    """
    List all PR cache entries.

    Returns cached frontend PR builds with metadata.
    """
    cache_dir = get_pr_cache_dir()

    if not os.path.exists(cache_dir):
        return web.json_response({"cache": [], "total_size": 0})

    entries = []
    total_size = 0

    for item in os.listdir(cache_dir):
        item_path = os.path.join(cache_dir, item)
        if not os.path.isdir(item_path):
            continue

        try:
            stat = os.stat(item_path)
            size = sum(
                os.path.getsize(os.path.join(root, f))
                for root, _, files in os.walk(item_path)
                for f in files
            )

            parsed = parse_pr_cache_name(item)

            entries.append({
                "name": item,
                "path": item_path,
                "size": size,
                "modified": stat.st_mtime,
                "user": parsed.get("user") if parsed else None,
                "pr_number": parsed.get("pr_number") if parsed else None,
                "branch": parsed.get("branch") if parsed else None,
            })

            total_size += size

        except Exception as e:
            logger.warning(f"Error reading cache entry {item}: {e}")

    # Sort by modified time, newest first
    entries.sort(key=lambda x: x["modified"], reverse=True)

    return web.json_response({
        "cache": entries,
        "total_size": total_size,
        "count": len(entries),
    })


@routes.delete("/v2/extension/frontend/pr-cache/{pr}")
async def delete_pr_cache_item(request: web.Request) -> web.Response:
    """
    Delete a specific PR cache entry.

    Path params:
    - pr: str - the PR cache directory name (URL-encoded)
    """
    pr_name = request.match_info["pr"]

    # URL decode
    import urllib.parse
    pr_name = urllib.parse.unquote(pr_name)

    cache_dir = get_pr_cache_dir()
    pr_path = os.path.join(cache_dir, pr_name)

    if not os.path.exists(pr_path):
        return web.json_response(
            {"error": "PR cache entry not found"},
            status=404,
        )

    if not os.path.isdir(pr_path):
        return web.json_response(
            {"error": "Not a directory"},
            status=400,
        )

    try:
        shutil.rmtree(pr_path)
        logger.info(f"Deleted PR cache: {pr_name}")
        return web.json_response({
            "deleted": True,
            "name": pr_name,
        })
    except Exception as e:
        logger.exception(f"Error deleting PR cache {pr_name}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


@routes.delete("/v2/extension/frontend/pr-cache")
async def clear_pr_cache(request: web.Request) -> web.Response:
    """
    Clear all PR cache entries.

    Query params:
    - confirm: bool - must be "true" to actually clear (safety)
    """
    confirm = request.query.get("confirm", "false").lower() == "true"

    if not confirm:
        return web.json_response(
            {"error": "Must set confirm=true to clear cache"},
            status=400,
        )

    cache_dir = get_pr_cache_dir()

    if not os.path.exists(cache_dir):
        return web.json_response({
            "cleared": True,
            "entries_removed": 0,
        })

    entries = os.listdir(cache_dir)
    removed = 0
    errors = []

    for item in entries:
        item_path = os.path.join(cache_dir, item)
        if os.path.isdir(item_path):
            try:
                shutil.rmtree(item_path)
                removed += 1
            except Exception as e:
                errors.append({"item": item, "error": str(e)})
                logger.warning(f"Error removing {item}: {e}")

    return web.json_response({
        "cleared": True,
        "entries_removed": removed,
        "errors": errors if errors else None,
    })


@routes.get("/v2/extension/frontend/pr-cache/size")
async def get_pr_cache_size(request: web.Request) -> web.Response:
    """Get total size of PR cache."""
    cache_dir = get_pr_cache_dir()

    if not os.path.exists(cache_dir):
        return web.json_response({"total_size": 0, "count": 0})

    total_size = 0
    count = 0

    for item in os.listdir(cache_dir):
        item_path = os.path.join(cache_dir, item)
        if os.path.isdir(item_path):
            count += 1
            try:
                total_size += sum(
                    os.path.getsize(os.path.join(root, f))
                    for root, _, files in os.walk(item_path)
                    for f in files
                )
            except Exception:
                pass

    return web.json_response({
        "total_size": total_size,
        "count": count,
    })
