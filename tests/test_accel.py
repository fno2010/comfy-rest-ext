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


class TestMethodRegistry:
    def test_registry_covers_dimension_options(self):
        for dimension, options in (
            ("turbo", accel.TURBO_OPTIONS),
            ("attention", accel.ATTENTION_OPTIONS),
            ("block_cache", accel.BLOCK_CACHE_OPTIONS),
            ("step_cache", accel.STEP_CACHE_OPTIONS),
        ):
            assert options[0] == "none"
            for method_id in options[1:]:
                method = accel.get_method(method_id)
                assert method is not None
                assert method.dimension == dimension

    def test_nested_params_override_defaults(self):
        cfg = accel.parse_accel_config({
            "block_cache": {
                "method": "fbc",
                "params": {"threshold": 0.2, "start_step": 3},
            },
        })
        assert cfg.block_cache == "fbc"
        assert cfg.params == {"block_cache": {"threshold": 0.2, "start_step": 3}}
        wf = build_h3_workflow(
            prompt="p", width=512, height=512, length=124, seed=0, accel=cfg,
        )
        node = wf[_find(wf, "H3FirstBlockCache")]["inputs"]
        assert node["threshold"] == 0.2
        assert node["start_step"] == 3
        # Unspecified parameters keep the registry defaults.
        assert node["end_dense_steps"] == 2
        assert cfg.as_dict()["params"]["block_cache"]["threshold"] == 0.2

    def test_nested_none_has_no_params(self):
        cfg = accel.parse_accel_config({"attention": {"method": "none"}})
        assert cfg.attention == "none"
        assert cfg.params == {}

    def test_unknown_param_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({
                "block_cache": {"method": "fbc", "params": {"bogus": 1}},
            })

    def test_wrong_dimension_method_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({
                "attention": {"method": "fbc"},
            })

    def test_param_type_and_range_rejected(self):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({
                "block_cache": {"method": "fbc", "params": {"threshold": -1}},
            })
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({
                "block_cache": {"method": "fbc", "params": {"start_step": 1.5}},
            })
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({
                "attention": {"method": "sol", "params": {"enabled": "yes"}},
            })

    def test_unimplemented_method_is_rejected_at_build(self):
        cfg = accel.parse_accel_config({"block_cache": "cachedit"})
        assert "not implemented" in accel.check_option_available(
            "block_cache", "cachedit"
        )
        with pytest.raises(ValueError):
            build_h3_workflow(
                prompt="p", width=512, height=512, length=124, seed=0,
                accel=cfg,
            )

    def test_new_method_registers_without_builder_change(self):
        import api.accel as api_accel

        saved = dict(api_accel._METHODS)
        try:
            def apply(workflow, add, model_out, params, alloc, context):
                nid = alloc()
                add(nid, "TestCacheNode", {"model": model_out, **params})
                return [nid, 0]

            api_accel.register_method(api_accel.AccelMethod(
                id="test-cache",
                dimension="step_cache",
                required_nodes=("TestCacheNode",),
                params_schema={
                    "gain": api_accel.ParamSpec(float, 0.5, 0.0, 1.0)
                },
                apply=apply,
            ))
            cfg = api_accel.parse_accel_config({
                "step_cache": {
                    "method": "test-cache",
                    "params": {"gain": 0.75},
                },
            })
            wf = build_h3_workflow(
                prompt="p", width=512, height=512, length=124, seed=0,
                accel=cfg,
            )
            node_id = _find(wf, "TestCacheNode")
            assert wf[node_id]["inputs"]["gain"] == 0.75
            assert wf["5"]["inputs"]["model"] == [node_id, 0]
        finally:
            api_accel._METHODS.clear()
            api_accel._METHODS.update(saved)


class TestParamSchemaConstraints:
    @pytest.mark.parametrize("layer,method,params", [
        ("block_cache", "fbc", {"threshold": 5.0}),
        ("block_cache", "fbc", {"max_consecutive_skips": 0}),
        ("block_cache", "te-speed", {"device": "cuda"}),
        ("attention", "sol", {"thresh_type": "full"}),
        ("attention", "sol", {"tau": 0.0}),
        ("attention", "sage", {"sage_attention": "sageattn_bogus"}),
        ("turbo", "turbo-v4", {"strength": 100}),
        ("step_cache", "easycache", {"reuse_threshold": 3.5}),
        ("step_cache", "spectrum", {"degree": 0}),
        ("step_cache", "spectrum", {"window_size": 0.5}),
    ])
    def test_node_incompatible_values_rejected(self, layer, method, params):
        with pytest.raises(accel.AccelConfigError):
            accel.parse_accel_config({layer: {"method": method, "params": params}})

    def test_valid_edge_values_accepted(self):
        cfg = accel.parse_accel_config({
            "block_cache": {
                "method": "te-speed",
                "params": {"device": "gpu", "mcs": 0, "cache_depth": 0.95},
            },
            "attention": {"method": "sage", "params": {"sage_attention": "sageattn3"}},
        })
        assert cfg.params["block_cache"]["device"] == "gpu"
        assert cfg.params["attention"]["sage_attention"] == "sageattn3"


class TestLightX2V:
    def test_registered_as_turbo_variant(self):
        assert "lightx2v-4step-v1.3" in accel.TURBO_OPTIONS
        method = accel.get_method("lightx2v-4step-v1.3")
        assert method.dimension == "turbo"
        assert method.params_schema["shift_video"].default == 6.0
        assert method.params_schema["sampler"].default == "euler"

    def test_build_task_uses_lightx2v_lora_and_shifts(self, monkeypatch):
        import api.workflows.h3 as h3_mod

        calls = []
        monkeypatch.setattr(
            h3_mod, "require_asset",
            lambda folder, name: calls.append((folder, name)),
        )
        task = type("Task", (), {
            "task_id": "t", "prompt": "p", "width": 512, "height": 512,
            "length": 39, "seed": 1, "first_frame": None, "last_frame": None,
            "ref_images": None, "ref_videos": None, "assets": {},
            "params": {}, "speed": {"steps": 4, "turbo": "lightx2v-4step-v1.3"},
        })()
        built = h3_mod.build_h3_task(task)
        wf = built.workflow
        turbo_id = _find(wf, "MiniMaxH3TurboLoRA")
        assert wf[turbo_id]["inputs"]["lora_name"] == (
            "minimax_h3_fl2v_turbo_4step_v1.3_768p_comfyui_bf16.safetensors"
        )
        assert wf["5"]["inputs"]["shift_video"] == 6.0
        assert wf["5"]["inputs"]["shift_audio"] == 3.0
        sampler = wf[_find(wf, "KSampler")]["inputs"]
        assert sampler["steps"] == 4
        assert sampler["sampler_name"] == "euler"
        assert sampler["scheduler"] == "simple"
        assert (
            "loras",
            "minimax_h3_fl2v_turbo_4step_v1.3_768p_comfyui_bf16.safetensors",
        ) in calls


def test_lightx2v_defaults_to_four_steps(monkeypatch):
    import api.workflows.h3 as h3_mod

    monkeypatch.setattr(h3_mod, "require_asset", lambda *a, **k: None)
    speed = {"turbo": "lightx2v-4step-v1.3"}
    cfg = accel.parse_accel_config(speed)
    assert cfg.steps == 4
    task = type("Task", (), {
        "task_id": "t", "prompt": "p", "width": 512, "height": 512,
        "length": 39, "seed": 1, "first_frame": None, "last_frame": None,
        "ref_images": None, "ref_videos": None, "assets": {},
        "params": {}, "speed": speed,
    })()
    wf = h3_mod.build_h3_task(task).workflow
    assert wf[_find(wf, "KSampler")]["inputs"]["steps"] == 4
