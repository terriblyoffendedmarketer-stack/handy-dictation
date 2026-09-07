#!/usr/bin/env python3
# finetune.py — LoRA fine-tune whisper-medium on user's audio-correction pairs
# Usage: python3 scripts/finetune.py
# Requires: mlx-tune, mlx-audio, soundfile (in .venv)
#
# Uses ground-truth transcriptions + user corrections as training data.
# Outputs LoRA adapters to ~/.dictation/whisper-finetuned/
# After training, merge adapters and use as the daemon's model.
#
# Gotchas:
# - mlx-tune uses mlx-audio for whisper loading, not mlx-whisper
# - HF datasets has a pickle bug on Python 3.14, so we use a plain list dataset
# - Max audio length 30s — longer recordings must be chunked or skipped

import json
import os
import sys
import numpy as np
import soundfile as sf

DICTATION_DIR = os.path.expanduser("~/.dictation")
GT_PATH = os.path.join(DICTATION_DIR, "ground-truth.json")
CORRECTIONS_PATH = os.path.join(DICTATION_DIR, "corrections.json")
RECORDINGS_DIR = os.path.join(DICTATION_DIR, "recordings")
OUTPUT_DIR = os.path.join(DICTATION_DIR, "whisper-finetuned")
SAMPLE_RATE = 16000
MAX_AUDIO_S = 30.0


def load_training_data():
    """Load audio-text pairs from ground truth and corrections."""
    gt = {}
    if os.path.exists(GT_PATH):
        with open(GT_PATH) as f:
            gt = json.load(f)

    corrections = {}
    if os.path.exists(CORRECTIONS_PATH):
        with open(CORRECTIONS_PATH) as f:
            corrections = json.load(f)

    rows = []
    for wav_name, text in gt.items():
        wav_path = os.path.join(RECORDINGS_DIR, wav_name)
        if not os.path.exists(wav_path) or not text.strip():
            continue
        if wav_name in corrections:
            text = corrections[wav_name]["corrected"]
        audio, sr = sf.read(wav_path, dtype="float32")
        if sr != SAMPLE_RATE:
            continue
        duration = len(audio) / SAMPLE_RATE
        if duration > MAX_AUDIO_S:
            continue
        rows.append({
            "audio": {"array": audio, "sampling_rate": SAMPLE_RATE},
            "sentence": text.strip(),
        })

    for wav_name, entry in corrections.items():
        if wav_name in gt:
            continue
        wav_path = os.path.join(RECORDINGS_DIR, wav_name)
        if not os.path.exists(wav_path):
            continue
        text = entry.get("corrected", "").strip()
        if not text:
            continue
        audio, sr = sf.read(wav_path, dtype="float32")
        if sr != SAMPLE_RATE:
            continue
        duration = len(audio) / SAMPLE_RATE
        if duration > MAX_AUDIO_S:
            continue
        rows.append({
            "audio": {"array": audio, "sampling_rate": SAMPLE_RATE},
            "sentence": text,
        })

    return rows


def main():
    from mlx_tune import FastSTTModel, STTSFTConfig, STTSFTTrainer, STTDataCollator

    print("Loading training data...")
    data = load_training_data()
    print(f"  {len(data)} samples (max {MAX_AUDIO_S}s each)")

    if len(data) < 5:
        print("Not enough training data. Need at least 5 samples.")
        print("Correct more transcriptions in the History tab (http://localhost:9876)")
        sys.exit(1)

    print("\nLoading whisper-medium with LoRA adapters...")
    model, processor = FastSTTModel.from_pretrained(
        "mlx-community/whisper-medium-mlx",
        max_seq_length=448,
    )
    model = FastSTTModel.get_peft_model(model, r=16, lora_alpha=16)

    config = STTSFTConfig(
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        warmup_steps=5,
        max_steps=len(data) * 3,
        num_train_epochs=3,
        learning_rate=1e-5,
        logging_steps=5,
        output_dir=OUTPUT_DIR,
        sample_rate=SAMPLE_RATE,
        language="en",
        max_audio_length=MAX_AUDIO_S,
    )

    collator = STTDataCollator(
        model=model,
        processor=processor,
        language="en",
    )

    trainer = STTSFTTrainer(
        model=model,
        processor=processor,
        data_collator=collator,
        train_dataset=data,
        args=config,
    )

    print(f"\nStarting fine-tuning ({len(data)} samples, {config.max_steps} steps)...")
    trainer.train()

    print(f"\nLoRA adapters saved to {OUTPUT_DIR}")
    print("To merge into a full model for the daemon, run:")
    print("  python3 scripts/finetune.py --merge")


def merge():
    """Merge LoRA adapters into the base model by computing W + B@A * scale."""
    from mlx_tune import FastSTTModel
    import mlx.core as mx
    from mlx.utils import tree_flatten

    LORA_RANK = 16
    LORA_ALPHA = 16
    LORA_SCALE = LORA_ALPHA / LORA_RANK

    print("Loading base model...")
    model, processor = FastSTTModel.from_pretrained(
        "mlx-community/whisper-medium-mlx",
        max_seq_length=448,
    )

    adapter_path = None
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            if f.endswith(".safetensors"):
                adapter_path = os.path.join(root, f)
                break
        if adapter_path:
            break
    if not adapter_path:
        print(f"No adapter weights found in {OUTPUT_DIR}")
        sys.exit(1)

    print(f"Loading adapters from {adapter_path}")
    adapter_weights = mx.load(adapter_path)

    actual_model = model.model if hasattr(model, "model") else model
    model_weights = dict(tree_flatten(actual_model.parameters()))

    lora_pairs = {}
    for key in adapter_weights:
        if key.endswith(".lora_a"):
            base_key = key.rsplit(".lora_a", 1)[0]
            lora_pairs[base_key] = lora_pairs.get(base_key, {})
            lora_pairs[base_key]["a"] = adapter_weights[key]
        elif key.endswith(".lora_b"):
            base_key = key.rsplit(".lora_b", 1)[0]
            lora_pairs[base_key] = lora_pairs.get(base_key, {})
            lora_pairs[base_key]["b"] = adapter_weights[key]

    merged_count = 0
    for base_key, ab in lora_pairs.items():
        weight_key = base_key + ".weight"
        if weight_key not in model_weights:
            print(f"  Warning: {weight_key} not in base model, skipping")
            continue
        if "a" not in ab or "b" not in ab:
            print(f"  Warning: incomplete LoRA pair for {base_key}, skipping")
            continue
        W = model_weights[weight_key]
        A = ab["a"]
        B = ab["b"]
        delta = mx.transpose(A @ B)
        model_weights[weight_key] = W + delta * LORA_SCALE
        merged_count += 1

    print(f"  Merged {merged_count} LoRA layers into base weights")

    merged_dir = os.path.join(DICTATION_DIR, "whisper-medium-finetuned")
    os.makedirs(merged_dir, exist_ok=True)

    fp16_weights = {k: v.astype(mx.float16) for k, v in model_weights.items()}
    mx.save_safetensors(os.path.join(merged_dir, "weights.safetensors"), fp16_weights)

    import shutil
    cache_dir = os.path.expanduser(
        "~/.cache/huggingface/hub/models--mlx-community--whisper-medium-mlx"
    )
    snapshot_dirs = []
    if os.path.exists(cache_dir):
        snap_dir = os.path.join(cache_dir, "snapshots")
        if os.path.exists(snap_dir):
            for d in os.listdir(snap_dir):
                snapshot_dirs.append(os.path.join(snap_dir, d))

    if snapshot_dirs:
        src_dir = snapshot_dirs[0]
        for fname in ["config.json", "vocab.json", "generation_config.json"]:
            src = os.path.join(src_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, merged_dir)

    print(f"\nMerged model saved to {merged_dir}")
    print("To use it, update ~/.dictation/config.json:")
    print(f'  "model": "{merged_dir}"')


if __name__ == "__main__":
    if "--merge" in sys.argv:
        merge()
    else:
        main()
