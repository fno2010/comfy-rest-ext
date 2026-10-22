"""Unit tests for the Anima and Qwen-Image 2.1 workflow builders."""

from __future__ import annotations

from api.tasks.video_task import (
    GenerationTask,
    _record_to_task,
    _task_to_record,
)
from api.workflows.anima import build_anima_task
from api.workflows.qwen_image_21 import (
    QWEN_TURBO_SIGMAS,
    build_qwen_image_task,
)


def _task(**kwargs) -> GenerationTask:
    base = dict(
        task_id="image_x",
        status="queued",
        prompt="a cat",
        task_type="t2i",
        width=1024,
        height=1024,
        length=1,
        seed=7,
        model_id="anima-base",
        modality="image",
        params={},
        created_at=1000.0,
    )
    base.update(kwargs)
    return GenerationTask(**base)


def _find(wf, class_type):
    for nid, node in wf.items():
        if node["class_type"] == class_type:
            return nid
    raise AssertionError(f"node {class_type} not found")


def test_anima_base_defaults_and_graph():
    built = build_anima_task(_task(model_id="anima-base"))
    wf = built.workflow
    assert wf[_find(wf, "UNETLoader")]["inputs"]["unet_name"].startswith("anima-base")
    assert wf[_find(wf, "CLIPLoader")]["inputs"]["type"] == "stable_diffusion"
    sampler = wf[_find(wf, "KSampler")]["inputs"]
    assert sampler["steps"] == 30
    assert sampler["cfg"] == 4.0
    assert sampler["sampler_name"] == "euler"
    assert sampler["scheduler"] == "simple"
    assert sampler["latent_image"] == [_find(wf, "EmptyLatentImage"), 0]
    assert wf[_find(wf, "EmptyLatentImage")]["inputs"]["batch_size"] == 1
    assert built.output_mime == "image/png"


def test_anima_turbo_uses_8_steps_cfg1():
    built = build_anima_task(_task(model_id="anima-turbo"))
    wf = built.workflow
    assert wf[_find(wf, "UNETLoader")]["inputs"]["unet_name"].startswith("anima-turbo")
    sampler = wf[_find(wf, "KSampler")]["inputs"]
    assert sampler["steps"] == 8
    assert sampler["cfg"] == 1.0


def test_anima_negative_default_and_batch():
    built = build_anima_task(_task(params={"n": 3}))
    wf = built.workflow
    neg_id = [
        nid for nid, n in wf.items()
        if n["class_type"] == "CLIPTextEncode"
        and n["inputs"]["text"].startswith("worst quality")
    ]
    assert neg_id
    assert wf[_find(wf, "EmptyLatentImage")]["inputs"]["batch_size"] == 3


def test_qwen_t2i_base_graph():
    built = build_qwen_image_task(_task(model_id="qwen-image-2.1"))
    wf = built.workflow
    assert wf[_find(wf, "UNETLoader")]["inputs"]["unet_name"] \
        == "qwen_image_2.1_int8_convrot.safetensors"
    assert wf[_find(wf, "CLIPLoader")]["inputs"]["type"] == "qwen_image"
    enc = wf[_find(wf, "TextEncodeQwenImage21")]["inputs"]
    assert enc["prompt"] == "a cat"
    assert enc["negative_prompt"] == ""
    assert "vae" not in enc
    sampler = wf[_find(wf, "KSampler")]["inputs"]
    assert sampler["steps"] == 25
    assert sampler["cfg"] == 1.0
    assert sampler["sampler_name"] == "euler"
    assert sampler["scheduler"] == "simple"
    assert wf[_find(wf, "EmptyLatentImage")]["inputs"]["batch_size"] == 1
    assert "QwenImage21Cache" in [n["class_type"] for n in wf.values()]


def test_qwen_turbo_uses_manual_sigmas():
    built = build_qwen_image_task(_task(model_id="qwen-image-2.1-turbo"))
    wf = built.workflow
    assert "KSampler" not in [n["class_type"] for n in wf.values()]
    assert wf[_find(wf, "ManualSigmas")]["inputs"]["sigmas"] == QWEN_TURBO_SIGMAS
    assert wf[_find(wf, "SamplerCustomAdvanced")]
    assert built.step_class == "SamplerCustomAdvanced"


def test_qwen_edit_uses_reference_images_and_latent():
    task = _task(
        model_id="qwen-image-2.1",
        task_type="edit",
        params={"n": 2, "resolution": 1024},
        ref_images=["a.png", "b.png"],
    )
    built = build_qwen_image_task(task)
    wf = built.workflow
    loads = [nid for nid, n in wf.items() if n["class_type"] == "LoadImage"]
    assert len(loads) == 2
    enc_id = _find(wf, "TextEncodeQwenImage21")
    enc = wf[enc_id]["inputs"]
    assert enc["vae"] == ["3", 0]
    assert enc["images.image_1"] == [loads[0], 0]
    assert enc["images.image_2"] == [loads[1], 0]
    repeat_id = _find(wf, "RepeatLatentBatch")
    assert wf[repeat_id]["inputs"]["samples"] == [enc_id, 2]
    sampler = wf[_find(wf, "KSampler")]["inputs"]
    assert sampler["latent_image"] == [repeat_id, 0]
    assert "EmptyLatentImage" not in [n["class_type"] for n in wf.values()]


def test_qwen_edit_requires_reference():
    task = _task(model_id="qwen-image-2.1", task_type="edit")
    try:
        build_qwen_image_task(task)
    except ValueError as e:
        assert "reference" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_image_task_persistence_roundtrip():
    task = _task(
        model_id="qwen-image-2.1",
        task_type="edit",
        params={"n": 2, "response_format": "url"},
        ref_images=["a.png"],
        output_paths=["/out/a.png", "/out/b.png"],
        output_mime="image/png",
    )
    record = _task_to_record(task)
    restored = _record_to_task(record)
    assert restored.model_id == "qwen-image-2.1"
    assert restored.modality == "image"
    assert restored.params["n"] == 2
    assert restored.output_paths == ["/out/a.png", "/out/b.png"]
    assert restored.output_path == "/out/a.png"


def test_output_prefixes_are_per_task():
    from api.workflows.h3 import build_h3_task

    first = build_anima_task(_task(task_id="image_aaaa1111"))
    second = build_anima_task(_task(task_id="image_bbbb2222"))
    assert first.output_prefix != second.output_prefix
    assert "image_aa" in first.output_prefix

    qwen_first = build_qwen_image_task(_task(
        task_id="image_cccc3333", model_id="qwen-image-2.1"
    ))
    qwen_second = build_qwen_image_task(_task(
        task_id="image_dddd4444", model_id="qwen-image-2.1"
    ))
    assert qwen_first.output_prefix != qwen_second.output_prefix

    def video_task(task_id):
        return GenerationTask(
            task_id=task_id, status="queued", prompt="p", task_type="t2va",
            width=1024, height=768, length=39, seed=1,
            model_id="minimax-h3", modality="video", params={},
        )

    h3_first = build_h3_task(video_task("video_aaaa1111"))
    h3_second = build_h3_task(video_task("video_bbbb2222"))
    assert h3_first.output_prefix != h3_second.output_prefix
    save_id = _find(h3_first.workflow, "SaveVideo")
    assert h3_first.workflow[save_id]["inputs"]["filename_prefix"] == h3_first.output_prefix
