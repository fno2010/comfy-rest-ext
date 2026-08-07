"""Unit tests for R2V (reference-to-video) support."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# Provide a fake `server` module before importing the api package.
server_mod = types.ModuleType("server")
server_mod.PromptServer = MagicMock()
sys.modules["server"] = server_mod

import folder_paths  # noqa: E402  (installed by tests/conftest.py)

from api.openai.v1 import (  # noqa: E402
    _model_for_task,
    _task_type_from_model,
    _infer_task_type,
    CANONICAL_MODEL_ID,
)
from api.tasks.video_task import (  # noqa: E402
    VideoTask,
    build_h3_workflow,
    frames_for_seconds,
)


def _find(wf, class_type):
    for nid, node in wf.items():
        if node["class_type"] == class_type:
            return nid
    raise AssertionError(f"node {class_type} not found in workflow")


def test_build_h3_workflow_t2v_uses_fl2va():
    wf = build_h3_workflow(
        prompt="a cat", width=1344, height=768, length=101, seed=0
    )
    assert wf["1"]["inputs"]["unet_name"] == "minimax_h3_fl2va_pruned_nvfp4.safetensors"
    assert "MiniMaxH3ReferenceToVideo" not in [
        n["class_type"] for n in wf.values()
    ]
    cond_id = _find(wf, "MiniMaxH3ImageToVideo")
    sampler_id = _find(wf, "KSampler")
    assert wf[sampler_id]["inputs"]["positive"] == [cond_id, 0]
    assert wf[sampler_id]["inputs"]["latent_image"] == [cond_id, 1]


def test_build_h3_workflow_i2v_uses_first_frame():
    wf = build_h3_workflow(
        prompt="a cat", width=1344, height=768, length=101, seed=0,
        first_frame_path="frame.png",
    )
    frame_id = _find(wf, "LoadImage")
    assert wf[frame_id]["inputs"]["image"] == "frame.png"
    cond_id = _find(wf, "MiniMaxH3ImageToVideo")
    assert wf[cond_id]["inputs"]["first_frame"] == [frame_id, 0]


def test_build_h3_workflow_r2v_uses_ref2va_and_reference_node():
    wf = build_h3_workflow(
        prompt="Use <Picture 1> as reference",
        width=1344, height=768, length=101, seed=0,
        ref_images=["ref1.png", "ref2.png"],
    )
    assert wf["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_nvfp4.safetensors"
    load_ids = [nid for nid, n in wf.items() if n["class_type"] == "LoadImage"]
    assert len(load_ids) == 2
    assert wf[load_ids[0]]["inputs"]["image"] == "ref1.png"
    assert wf[load_ids[1]]["inputs"]["image"] == "ref2.png"
    ref_id = _find(wf, "MiniMaxH3ReferenceToVideo")
    cond = wf[ref_id]["inputs"]
    assert cond["clip"] == ["2", 0]
    assert cond["vae"] == ["3", 0]
    assert cond["audio_vae"] == ["4", 0]
    assert cond["ref_image_size"] == "match"
    assert cond["ref_images"] == {
        "ref_image_0": [load_ids[0], 0],
        "ref_image_1": [load_ids[1], 0],
    }
    sampler_id = _find(wf, "KSampler")
    assert wf[sampler_id]["inputs"]["positive"] == [ref_id, 0]
    assert wf[sampler_id]["inputs"]["latent_image"] == [ref_id, 1]


def test_build_h3_workflow_r2v_single_reference():
    wf = build_h3_workflow(
        prompt="p", width=1344, height=768, length=101, seed=0,
        ref_images=["only.png"],
    )
    ref_id = _find(wf, "MiniMaxH3ReferenceToVideo")
    load_id = _find(wf, "LoadImage")
    assert wf[ref_id]["inputs"]["ref_images"] == {"ref_image_0": [load_id, 0]}


def test_model_for_task_returns_canonical():
    assert _model_for_task("r2v") == CANONICAL_MODEL_ID
    assert _model_for_task("fl2va") == CANONICAL_MODEL_ID
    assert _model_for_task("t2va") == CANONICAL_MODEL_ID


def test_task_type_from_model_aliases():
    assert _task_type_from_model("minimax-h3-r2v") == "r2v"
    assert _task_type_from_model("minimax-h3-i2v") == "fl2va"
    assert _task_type_from_model("minimax-h3-t2v") == "t2va"


def test_task_type_from_model_canonical_and_none():
    assert _task_type_from_model(CANONICAL_MODEL_ID) is None
    assert _task_type_from_model(None) is None


def test_task_type_from_model_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        _task_type_from_model("gpt-5")


def test_infer_task_type_no_image_is_t2v():
    assert _infer_task_type({}) == "t2va"


def test_infer_task_type_single_image_is_fl2va():
    assert _infer_task_type({"_ref_images_data": [b"x"]}) == "fl2va"
    assert _infer_task_type({"_first_frame_data": b"x"}) == "fl2va"
    assert _infer_task_type({"input_reference": "img.png"}) == "fl2va"


def test_infer_task_type_multiple_images_is_r2v():
    assert _infer_task_type({"_ref_images_data": [b"a", b"b"]}) == "r2v"
    assert _infer_task_type({"ref_images": ["a.png", "b.png"]}) == "r2v"


def test_video_response_includes_inference_time_s():
    task = VideoTask(
        task_id="video_x",
        status="completed",
        prompt="p",
        task_type="r2v",
        width=1344, height=768, length=101, seed=0,
        created_at=1000.0,
        completed_at=1030.0,
    )
    from api.openai.v1 import _video_response
    resp = _video_response(task)
    assert resp["inference_time_s"] == 30.0
    assert resp["model"] == CANONICAL_MODEL_ID


def test_video_response_inference_time_null_when_not_done():
    task = VideoTask(
        task_id="video_x",
        status="queued",
        prompt="p",
        task_type="t2va",
        width=1344, height=768, length=101, seed=0,
        created_at=1000.0,
        completed_at=None,
    )
    from api.openai.v1 import _video_response
    resp = _video_response(task)
    assert resp["inference_time_s"] is None


def test_frames_for_seconds_snaps_to_grid():
    assert frames_for_seconds(5.0) == 124
    assert frames_for_seconds(1.0) == 39
    assert frames_for_seconds(15.0) == 362


def test_build_h3_workflow_te_speed_node():
    wf = build_h3_workflow(
        prompt="p", width=1344, height=768, length=101, seed=0, te_speed=True
    )
    assert wf["1b"]["class_type"] == "TESpeedMiniMaxH3"
    assert wf["1b"]["inputs"]["cache_depth"] == 0.75
    # SigmaShift feeds from the TE-Speed output
    assert wf["5"]["inputs"]["model"] == ["1b", 0]


def test_build_h3_workflow_sol_stack_nodes():
    wf = build_h3_workflow(
        prompt="p", width=1344, height=768, length=101, seed=0, sol_stack=True
    )
    assert wf["1b"]["class_type"] == "SolAttnMiniMaxH3Patcher"
    assert wf["1c"]["class_type"] == "H3FirstBlockCache"
    assert wf["1c"]["inputs"]["model"] == ["1b", 0]
    assert wf["5"]["inputs"]["model"] == ["1c", 0]


def test_build_h3_workflow_no_speed_nodes():
    wf = build_h3_workflow(
        prompt="p", width=1344, height=768, length=101, seed=0
    )
    assert "TESpeedMiniMaxH3" not in [n["class_type"] for n in wf.values()]
    assert "SolAttnMiniMaxH3Patcher" not in [n["class_type"] for n in wf.values()]
    assert wf["5"]["inputs"]["model"] == ["1", 0]
