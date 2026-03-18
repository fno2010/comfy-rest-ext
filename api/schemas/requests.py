"""
Pydantic request/response schemas for REST API endpoints.
"""

from pydantic import BaseModel, HttpUrl
from typing import Optional


# =============================================================================
# Model Download
# =============================================================================

class ModelDownloadRequest(BaseModel):
    """Request body for POST /v2/model/download"""
    url: str
    folder: str = "checkpoints"
    filename: Optional[str] = None


# =============================================================================
# Model Management
# =============================================================================

class ModelDeleteRequest(BaseModel):
    """Request body for DELETE /v2/models/{path}"""
    force: bool = False


# =============================================================================
# Workflow Dependencies
# =============================================================================

class WorkflowDepsRequest(BaseModel):
    """Request body for POST /v2/workflow/dependencies"""
    workflow: dict
    async_install: bool = True


class WorkflowDepsCheckRequest(BaseModel):
    """Request body for POST /v2/workflow/dependencies/check"""
    workflow: dict


# =============================================================================
# Dependencies
# =============================================================================

class DepsCheckRequest(BaseModel):
    """Request body for GET /v2/dependencies/check"""
    node: str


class DepsRestoreRequest(BaseModel):
    """Request body for POST /v2/dependencies/restore"""
    nodes: list[str]
    async_mode: bool = True


# =============================================================================
# Snapshot
# =============================================================================

class SnapshotExportRequest(BaseModel):
    """Request body for POST /v2/snapshot/export"""
    snapshot_id: str
    format: str = "tarball"
    include_models: bool = False


class SnapshotImportRequest(BaseModel):
    """Request body for POST /v2/snapshot/import (multipart)"""
    restore_models: bool = True
    restore_nodes: bool = True


# =============================================================================
# Node Pack/Validate
# =============================================================================

class NodePackRequest(BaseModel):
    """Request body for POST /v2/nodes/pack"""
    node_name: str
    respect_comfyignore: bool = True


class NodeValidateRequest(BaseModel):
    """Request body for POST /v2/nodes/validate"""
    node_name: str


class NodeInitRequest(BaseModel):
    """Request body for POST /v2/nodes/init"""
    path: str
