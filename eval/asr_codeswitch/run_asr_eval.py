"""Layer 1 Eval: Benchmark Speechmatics vs faster-whisper on Arabic-English code-switching."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path
from typing import Any

import av
import httpx
import numpy as np
from datasets import Audio, load_dataset
from dotenv import load_dotenv
from faster_whisper import WhisperModel

from eval.metrics import bootstrap_ci, char_error_rate, english_token_recall, word_error_rate

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "eval" / "cache" / "asr_codeswitch"
RESULTS_PATH = REPO_ROOT / "eval" / "results" / "asr_codeswitch.jsonl"


def _write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim > 1:
        arr = np.mean(arr, axis=-1)
    pcm16 = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def _resample_to_16k_pcm16(audio_dict: dict[str, Any]) -> tuple[np.ndarray, float]:
    """Decode HF Audio dict (with bytes/path or array) to mono 16kHz float32 samples."""
    if "array" in audio_dict and audio_dict["array"] is not None:
        arr = np.asarray(audio_dict["array"], dtype=np.float32)
        sr = int(audio_dict.get("sampling_rate", 16000))
        if arr.ndim > 1:
            arr = np.mean(arr, axis=-1)
        if sr != 16000 and len(arr) > 0:
            new_len = max(1, int(round(len(arr) * 16000 / sr)))
            arr = np.interp(
                np.linspace(0, len(arr) - 1, new_len),
                np.arange(len(arr)),
                arr,
            ).astype(np.float32)
        return arr, len(arr) / 16000.0

    raw_bytes = audio_dict.get("bytes")
    raw_path = audio_dict.get("path")
    source = av.open(raw_path) if raw_path and not raw_bytes else None
    if raw_bytes:
        import io

        source = av.open(io.BytesIO(raw_bytes))
    assert source is not None
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks: list[np.ndarray] = []
    try:
        for frame in source.decode(audio=0):
            for out_frame in resampler.resample(frame):
                chunks.append(out_frame.to_ndarray().reshape(-1))
    finally:
        source.close()
    arr = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return arr, len(arr) / 16000.0


def prepare_clips(limit: int = 300) -> list[dict[str, Any]]:
    """Stream `limit` clips from MohamedRashad/arabic-english-code-switching and cache locally."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = CACHE_DIR / "manifest.jsonl"
    if manifest_path.exists():
        cached = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(cached) >= limit:
            return cached[:limit]

    # Disable torchcodec decoding issue by using Audio(decode=False) then decoding via PyAV
    ds = load_dataset(
        "MohamedRashad/arabic-english-code-switching",
        split="train",
        streaming=True,
    ).cast_column("audio", Audio(decode=False))

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(ds):
        if len(items) >= limit:
            break
        text = str(row.get("sentence") or row.get("text") or row.get("transcription") or "").strip()
        if not text:
            continue
        samples, duration_s = _resample_to_16k_pcm16(row["audio"])
        if duration_s < 0.4 or duration_s > 25.0:
            continue
        wav_path = CACHE_DIR / f"clip_{idx:04d}.wav"
        _write_pcm16_wav(wav_path, samples, 16000)
        items.append(
            {
                "clip_id": f"cs-{idx:04d}",
                "wav_path": str(wav_path),
                "reference": text,
                "duration_s": round(duration_s, 3),
            }
        )

    with manifest_path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return items


async def transcribe_speechmatics_batch(
    client: httpx.AsyncClient,
    wav_path: Path,
    api_key: str,
    language: str = "ar_en",
    additional_vocab: list[dict[str, Any]] | None = None,
) -> tuple[str, float]:
    """Submit a clip to Speechmatics Batch API (ar_en enhanced) and poll until done."""
    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    tx_cfg: dict[str, Any] = {"language": language, "operating_point": "enhanced"}
    if additional_vocab:
        tx_cfg["additional_vocab"] = additional_vocab
    cfg = {"type": "transcription", "transcription_config": tx_cfg}
    t0 = time.monotonic() * 1000.0
    for attempt in range(4):
        with wav_path.open("rb") as fh:
            resp = await client.post(
                "https://asr.api.speechmatics.com/v2/jobs",
                headers=headers,
                data={"config": json.dumps(cfg)},
                files={"data_file": (wav_path.name, fh, "audio/wav")},
            )
        if resp.status_code == 429 and attempt < 3:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        break

    job_id = resp.json()["id"]
    status = "running"
    for _ in range(60):
        await asyncio.sleep(0.6)
        jr = await client.get(f"https://asr.api.speechmatics.com/v2/jobs/{job_id}", headers=headers)
        jr.raise_for_status()
        status = jr.json()["job"]["status"]
        if status == "done":
            break
        if status in ("rejected", "deleted"):
            raise RuntimeError(f"Speechmatics job {job_id} ended with status={status}")
    if status != "done":
        raise TimeoutError(f"Speechmatics job {job_id} timed out with status={status}")

    tr = await client.get(
        f"https://asr.api.speechmatics.com/v2/jobs/{job_id}/transcript?format=txt",
        headers=headers,
    )
    tr.raise_for_status()
    elapsed_ms = round((time.monotonic() * 1000.0) - t0, 2)
    return tr.text.strip(), elapsed_ms


def transcribe_faster_whisper(model: WhisperModel, wav_path: Path) -> tuple[str, float]:
    """Transcribe a WAV file using local faster-whisper and return (text, latency_ms)."""
    t0 = time.monotonic() * 1000.0
    segments, _ = model.transcribe(str(wav_path), beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    elapsed_ms = round((time.monotonic() * 1000.0) - t0, 2)
    return text, elapsed_ms


async def run_eval(
    limit: int = 300, speechmatics_lang: str = "ar_en", skip_sm: bool = False
) -> None:
    """Run bilingual code-switching ASR evaluation against Whisper and Speechmatics."""
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    clips = prepare_clips(limit=limit)
    print(f"Prepared {len(clips)} real Arabic-English code-switched clips.")

    done_ids: set[str] = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["clip_id"])

    whisper = WhisperModel("small", device="cpu", compute_type="int8")
    sm_key = os.getenv("SPEECHMATICS_API_KEY", "").strip()
    pending = [c for c in clips if c["clip_id"] not in done_ids]
    sem = asyncio.Semaphore(4)

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        async def _eval_one(item: dict[str, Any]) -> dict[str, Any]:
            wav_path = Path(item["wav_path"])
            ref = item["reference"]
            fw_text, fw_ms = await asyncio.to_thread(transcribe_faster_whisper, whisper, wav_path)
            row: dict[str, Any] = {
                "clip_id": item["clip_id"],
                "duration_s": item["duration_s"],
                "reference": ref,
                "faster_whisper": {
                    "hypothesis": fw_text,
                    "wer": word_error_rate(ref, fw_text),
                    "cer": char_error_rate(ref, fw_text),
                    "en_recall": english_token_recall(ref, fw_text),
                    "latency_ms": fw_ms,
                },
            }
            if sm_key and not skip_sm:
                async with sem:
                    try:
                        sm_text, sm_ms = await transcribe_speechmatics_batch(
                            http_client, wav_path, sm_key, language=speechmatics_lang
                        )
                        row["speechmatics"] = {
                            "language": speechmatics_lang,
                            "hypothesis": sm_text,
                            "wer": word_error_rate(ref, sm_text),
                            "cer": char_error_rate(ref, sm_text),
                            "en_recall": english_token_recall(ref, sm_text),
                            "latency_ms": sm_ms,
                        }
                    except Exception as exc:
                        row["speechmatics"] = {"error": str(exc)}
            return row

        with RESULTS_PATH.open("a", encoding="utf-8") as out_fh:
            for start in range(0, len(pending), 10):
                batch = pending[start : start + 10]
                results = await asyncio.gather(*[_eval_one(item) for item in batch])
                for row in results:
                    out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_fh.flush()
                done_count = len(done_ids) + start + len(batch)
                print(f"  [{done_count}/{len(clips)}] processed")

    rows = [
        json.loads(line)
        for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fw_wers = [r["faster_whisper"]["wer"] for r in rows]
    print(f"faster-whisper mean WER (n={len(fw_wers)}):", bootstrap_ci(fw_wers))
    sm_wers = [
        r["speechmatics"]["wer"]
        for r in rows
        if isinstance(r.get("speechmatics"), dict) and "wer" in r["speechmatics"]
    ]
    if sm_wers:
        print(
            f"Speechmatics ({speechmatics_lang}) mean WER (n={len(sm_wers)}):",
            bootstrap_ci(sm_wers),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--sm-lang", type=str, default="ar_en")
    parser.add_argument("--skip-sm", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_eval(limit=args.limit, speechmatics_lang=args.sm_lang, skip_sm=args.skip_sm))
