"""Transcribe a fixed selection of figure crops with a local LightOnOCR model.

The selection is a JSON list containing id, image and image_sha256. The empty
prompt requests transcription; chart interpretation and device attribution
are deliberately left to extraction. Raw responses are diagnostic candidates,
not verified source facts. No provider API is called.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import time
from pathlib import Path

from examples.parsing.figures import digest, read_json, write_json

MODEL = "lightonai/LightOnOCR-3-1B"
REVISION = "23d544085c886bbdace647bcfab91203299417e2"
# This checkpoint predates Transformers' flattened Qwen base-model namespace.
# Rename keys only; never accept newly initialized or unmatched parameters.
KEY_MAPPING = {r"^language_model\.model\.": "model.language_model."}


def require_complete_checkpoint(info: dict) -> None:
    failures = {
        k: info[k]
        for k in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")
        if info.get(k)
    }
    if failures:
        raise RuntimeError(f"Incomplete checkpoint load: {failures}")


def run(selection: Path, snapshot: Path, output: Path, device: str) -> None:
    import torch
    from PIL import Image
    from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("Apple MPS is unavailable; refusing a silent CPU fallback")
    if snapshot.name != REVISION:
        raise ValueError("Use the pinned model snapshot recorded in this script")
    crops = read_json(selection)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "model": MODEL,
            "revision": REVISION,
            "snapshot": str(snapshot),
            "selection_sha256": digest(selection),
            "script_sha256": digest(Path(__file__)),
            "device": device,
            "dtype": "float32",
            "prompt": "",
            "do_sample": False,
            "max_new_tokens": 2048,
            "max_time_seconds": 180,
            "versions": {
                p: importlib.metadata.version(p)
                for p in ("torch", "transformers", "pillow")
            },
            "provider_requests": 0,
            "checkpoint_key_mapping": KEY_MAPPING,
            "warning": "Transcription screening, not extraction precision/recall. Inferred chart values are not evidence.",
        },
    )
    start = time.perf_counter()
    model, loading_info = LightOnOcrForConditionalGeneration.from_pretrained(
        snapshot,
        dtype=torch.float32,
        local_files_only=True,
        key_mapping=KEY_MAPPING,
        output_loading_info=True,
    )
    write_json(
        output / "loading-info.json",
        {k: sorted(v) if isinstance(v, set) else v for k, v in loading_info.items()},
    )
    require_complete_checkpoint(loading_info)
    model = model.to(device).eval()
    processor = LightOnOcrProcessor.from_pretrained(snapshot, local_files_only=True)
    print(
        f"Model ready after {time.perf_counter() - start:.1f}s on {device}", flush=True
    )
    rows = []
    for crop in crops:
        image_path = Path(crop["image"])
        if digest(image_path) != crop["image_sha256"]:
            raise ValueError(f"Crop changed: {image_path}")
        image = Image.open(image_path).convert("RGB")
        conversation = [
            {"role": "user", "content": [{"type": "image", "image": image}]}
        ]
        inputs = processor.apply_chat_template(
            conversation,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {
            k: v.to(device=device, dtype=torch.float32)
            if v.is_floating_point()
            else v.to(device)
            for k, v in inputs.items()
        }
        start = time.perf_counter()
        with torch.inference_mode():
            tokens = model.generate(
                **inputs, do_sample=False, max_new_tokens=2048, max_time=180
            )
        if device == "mps":
            torch.mps.synchronize()
        generated = tokens[0, inputs["input_ids"].shape[1] :]
        eos = model.generation_config.eos_token_id
        eos_ids = eos if isinstance(eos, list) else [eos]
        complete = len(generated) < 2048 and int(generated[-1]) in eos_ids
        rows.append(
            {
                "id": crop["id"],
                "image_sha256": crop["image_sha256"],
                "seconds": time.perf_counter() - start,
                "output_tokens": len(generated),
                "complete": complete,
                "last_token_id": int(generated[-1]),
                "text": processor.decode(generated, skip_special_tokens=True),
            }
        )
        write_json(output / "results.json", rows)
        print(
            f"{crop['id']}: {rows[-1]['seconds']:.1f}s, {len(generated)} tokens, complete={complete}",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    args = parser.parse_args()
    run(
        args.selection.resolve(),
        args.snapshot.resolve(),
        args.output.resolve(),
        args.device,
    )
