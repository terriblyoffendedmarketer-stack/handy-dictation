#!/usr/bin/env python3
# benchmark.py — Transcription accuracy benchmark
# Usage: python3 scripts/benchmark.py
#
# 1. Generates ground truth using whisper-large-v3 (most accurate Whisper, slow)
# 2. Benchmarks multiple models against ground truth using WER
# 3. Saves results to ~/.dictation/benchmark-results.json
#
# Models tested:
#   - whisper-medium (mlx-whisper) — current default
#   - whisper-large-v3-turbo (mlx-whisper) — faster large variant
#   - distil-whisper-large-v3 (mlx-whisper) — distilled, English-only
#   - Qwen3-ASR 0.6B (mlx-qwen3-asr) — state-of-the-art open-source
#   - Qwen3-ASR 1.7B (mlx-qwen3-asr) — larger, highest accuracy expected
#
# Gotchas:
# - First run downloads all models (several GB total).
# - Ground truth is saved so re-runs skip the slow large-v3 pass.
# - WER is computed with simple word-level edit distance (no jiwer needed).
# - Skips recordings shorter than 1 second (likely empty/accidental).
# - Qwen3-ASR transcribe() returns TranscriptionResult with .text attribute.
# - Parakeet not supported by mlx-audio (no model_type mapping). Skip it.
# - Moonshine produces garbage on casual speech (100%+ WER). Skip it.

import json
import os
import sys
import time
import traceback
import wave
import numpy as np

RECORDINGS_DIR = os.path.expanduser("~/.dictation/recordings")
RESULTS_PATH = os.path.expanduser("~/.dictation/benchmark-results.json")
GROUND_TRUTH_PATH = os.path.expanduser("~/.dictation/ground-truth.json")
SAMPLE_RATE = 16000

GROUND_TRUTH_MODEL = "mlx-community/whisper-large-v3-mlx"

MODELS = [
    {
        "name": "medium",
        "engine": "mlx-whisper",
        "repo": "mlx-community/whisper-medium-mlx",
    },
    {
        "name": "turbo",
        "engine": "mlx-whisper",
        "repo": "mlx-community/whisper-large-v3-turbo",
    },
    {
        "name": "distil-large-v3",
        "engine": "mlx-whisper",
        "repo": "mlx-community/distil-whisper-large-v3",
    },
    {
        "name": "qwen3-asr-0.6b",
        "engine": "qwen3-asr",
        "repo": "Qwen/Qwen3-ASR-0.6B",
    },
]


def load_wav(path):
    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, w.getnframes() / w.getframerate()


def word_error_rate(reference, hypothesis):
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    d = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])

    return d[len(ref_words)][len(hyp_words)] / len(ref_words)


def transcribe_mlx_whisper(audio, repo):
    import mlx_whisper
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=repo,
        language="en",
        condition_on_previous_text=False,
    )
    return result["text"].strip()


def transcribe_qwen3_asr(filepath, repo):
    import mlx_qwen3_asr
    result = mlx_qwen3_asr.transcribe(filepath, model=repo, language="en")
    return result.text.strip()


def transcribe(model_info, filepath, audio):
    engine = model_info["engine"]
    repo = model_info["repo"]

    if engine == "mlx-whisper":
        return transcribe_mlx_whisper(audio, repo)
    elif engine == "qwen3-asr":
        return transcribe_qwen3_asr(filepath, repo)
    else:
        raise ValueError(f"Unknown engine: {engine}")


def get_recordings():
    if not os.path.isdir(RECORDINGS_DIR):
        print(f"No recordings directory: {RECORDINGS_DIR}")
        sys.exit(1)

    files = sorted([
        os.path.join(RECORDINGS_DIR, f)
        for f in os.listdir(RECORDINGS_DIR)
        if f.endswith(".wav")
    ])

    valid = []
    for f in files:
        audio, duration = load_wav(f)
        if duration >= 1.0:
            valid.append((f, audio, duration))

    return valid


def load_ground_truth():
    if os.path.exists(GROUND_TRUTH_PATH):
        with open(GROUND_TRUTH_PATH) as f:
            return json.load(f)
    return {}


def save_ground_truth(gt):
    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(gt, f, indent=2)


def main():
    recordings = get_recordings()
    print(f"\nBenchmark: {len(recordings)} recordings found\n")

    if not recordings:
        print("No recordings to benchmark.")
        return

    # Step 1: Generate ground truth with large-v3
    print("=" * 60)
    print("STEP 1: Ground truth (whisper-large-v3)")
    print("=" * 60)

    ground_truth = load_ground_truth()
    new_gt = 0

    for filepath, audio, duration in recordings:
        fname = os.path.basename(filepath)
        if fname in ground_truth:
            print(f"  [cached] {fname} ({duration:.1f}s)")
            continue

        print(f"  Transcribing {fname} ({duration:.1f}s)...", end="", flush=True)
        t0 = time.time()
        text = transcribe_mlx_whisper(audio, GROUND_TRUTH_MODEL)
        elapsed = time.time() - t0
        ground_truth[fname] = text
        new_gt += 1
        print(f" {elapsed:.1f}s")
        print(f"    -> {text[:100]}{'...' if len(text) > 100 else ''}")
        save_ground_truth(ground_truth)

    if new_gt:
        print(f"\n  Generated {new_gt} new ground truth transcriptions.\n")
    else:
        print(f"\n  All ground truth cached.\n")

    # Step 2: Benchmark each model
    results = {}

    for model_info in MODELS:
        model_name = model_info["name"]
        print("=" * 60)
        print(f"Benchmarking: {model_name} ({model_info['repo']})")
        print(f"Engine: {model_info['engine']}")
        print("=" * 60)

        model_results = []
        total_wer = 0
        count = 0
        errors = 0

        for filepath, audio, duration in recordings:
            fname = os.path.basename(filepath)
            if fname not in ground_truth:
                continue

            ref = ground_truth[fname]
            if not ref.strip():
                continue

            print(f"  {fname} ({duration:.1f}s)...", end="", flush=True)
            try:
                t0 = time.time()
                hyp = transcribe(model_info, filepath, audio)
                elapsed = time.time() - t0

                wer = word_error_rate(ref, hyp)
                total_wer += wer
                count += 1

                model_results.append({
                    "file": fname,
                    "duration": round(duration, 1),
                    "time": round(elapsed, 1),
                    "wer": round(wer, 4),
                    "reference": ref,
                    "hypothesis": hyp,
                })

                status = "OK" if wer < 0.1 else "DIFF" if wer < 0.3 else "BAD"
                print(f" {elapsed:.1f}s | WER={wer:.1%} [{status}]")

                if wer > 0.05:
                    print(f"    REF: {ref[:80]}")
                    print(f"    HYP: {hyp[:80]}")
            except Exception as e:
                errors += 1
                print(f" ERROR: {e}")
                traceback.print_exc()
                if errors >= 3:
                    print(f"  Too many errors for {model_name}, skipping.")
                    break

        avg_wer = total_wer / count if count else 0
        avg_time = (
            sum(r["time"] for r in model_results) / len(model_results)
            if model_results else 0
        )
        results[model_name] = {
            "model_repo": model_info["repo"],
            "engine": model_info["engine"],
            "avg_wer": round(avg_wer, 4),
            "avg_time": round(avg_time, 2),
            "files_tested": count,
            "errors": errors,
            "details": model_results,
        }

        print(f"\n  {model_name} average WER: {avg_wer:.1%} | avg time: {avg_time:.1f}s ({count} files, {errors} errors)\n")

    # Save results
    with open(RESULTS_PATH, "w") as f:
        json.dump(results, f, indent=2)

    # Summary
    print("=" * 60)
    print("SUMMARY — sorted by WER (lower is better)")
    print("=" * 60)
    ranked = sorted(results.items(), key=lambda x: x[1]["avg_wer"])
    for i, (model_name, data) in enumerate(ranked, 1):
        err_note = f" ({data['errors']} errors)" if data["errors"] else ""
        print(f"  {i}. {model_name:20s} | WER={data['avg_wer']:6.1%} | avg={data['avg_time']:.1f}s | {data['files_tested']} files{err_note}")
    print(f"\nResults saved to {RESULTS_PATH}")
    print(f"Ground truth saved to {GROUND_TRUTH_PATH}")


if __name__ == "__main__":
    main()
