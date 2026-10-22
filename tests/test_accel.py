"""Unit tests for composable acceleration config (api/accel.py + workflow pipeline)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_module(name, rel_path):
    path = PROJECT_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


accel = _load_module("comfy_rest_ext_accel", "api/accel.py")

from api.tasks.video_task import build_h3_workflow  # noqa: E402


def _find(wf, class_type):
    for nid, node in wf.items():
        if node["class_type"] == class_type:
            return nid
    return None


class TestParseAccelConfig:
    def test_legacy_strings_backward_compatible(self):
        cfg = accel.parse_accel_config("te-speed")
        assert cfg.block_cache == "te-speed"
        assert cfg.attention == "none"
        cfg = accel.parse_accel_config("sol-stack")
        assert cfg.block_cache == "fbc"
        assert cfg.attention == "sol"
        cfg = accel.parse_accel_config("none")
        assert cfg.block_cache == "none"

    def test_object_config(self):
        cfg = accel.parse_accel_config({
            "steps": 8,
            "turbo": "turbo-v4",
            "block_cache": "fbc",
            "attention": "sol",
            "vae_decode": "batched",
        })
        assert cfg.steps == 8
        assert cfg.turbo == "turbo-v4"
        assert cfg.block_cache == "fbc"
        assert cfg.attention == "sol"
        assert cfg.vae_decode == "batched"

    def test_invalid_layer_value_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({"block_cache": "bogus"})

    def test_invalid_steps_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({"steps": 0})

    def test_invalid_precision_key_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({"precision": {"bogus": "int8"}})

    def test_turbo_step_cache_combo_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({"turbo": "turbo-v4", "step_cache": "easycache"})

    def test_non_string_non_dict_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config(42)


class TestAcceleratedWorkflow:
    def test_te_speed_lands_on_1b(self):
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0,
            accel=accel.parse_accel_config("te-speed"),
        )
        assert wf["1b"]["class_type"] == "TESpeedMiniMaxH3"
        assert wf["5"]["inputs"]["model"] == ["1b", 0]

    def test_sol_stack_maps_to_sol_plus_fbc(self):
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0,
            accel=accel.parse_accel_config("sol-stack"),
        )
        assert wf["1b"]["class_type"] == "SolAttnMiniMaxH3Patcher"
        assert wf["1c"]["class_type"] == "H3FirstBlockCache"
        assert wf["5"]["inputs"]["model"] == ["1c", 0]

    def test_full_combo_pipeline_order(self):
        cfg = accel.parse_accel_config({
            "steps": 8,
            "turbo": "turbo-v4",
            "block_cache": "fbc",
            "attention": "sol",
        })
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0, accel=cfg,
        )
        # Layer order: turbo(1b) -> sol(1c) -> fbc(1d)
        assert wf["1b"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["1c"]["class_type"] == "SolAttnMiniMaxH3Patcher"
        assert wf["1d"]["class_type"] == "H3FirstBlockCache"
        # Chain: fbc -> sigma shift -> KSampler
        assert wf["1d"]["inputs"]["model"] == ["1c", 0]
        assert wf["5"]["inputs"]["model"] == ["1d", 0]

    def test_legacy_boolean_params_still_work(self):
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0,
            te_speed=True,
        )
        assert wf["1b"]["class_type"] == "TESpeedMiniMaxH3"

    def test_chain_connections_correct(self):
        cfg = accel.parse_accel_config({
            "block_cache": "te-speed", "attention": "sage",
        })
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0, accel=cfg,
        )
        # sage(1b) -> te-speed(1c) -> sigma shift
        assert wf["1b"]["class_type"] == "PathchSageAttentionKJ"
        assert wf["1c"]["class_type"] == "TESpeedMiniMaxH3"
        assert wf["1c"]["inputs"]["model"] == ["1b", 0]
        assert wf["5"]["inputs"]["model"] == ["1c", 0]


class TestNodeAvailability:
    """Node-availability enforcement (runtime only; skipped in test env)."""

    def test_option_node_requirement_map_complete(self):
        """Every non-none option value must have a node requirement entry."""
        for value in accel.TURBO_OPTIONS + accel.BLOCK_CACHE_OPTIONS + \
                     accel.STEP_CACHE_OPTIONS + accel.ATTENTION_OPTIONS:
            if value in ("none", "default"):
                continue
            assert value in accel._OPTION_NODE_REQUIREMENTS, \
                f"missing node requirement for option {value!r}"

    def test_requirement_map_nodes_exist_in_enum(self):
        """Requirement node names are plausible (not typos)."""
        for value, nodes in accel._OPTION_NODE_REQUIREMENTS.items():
            assert isinstance(nodes, tuple) and len(nodes) >= 1
            for n in nodes:
                assert isinstance(n, str) and n
