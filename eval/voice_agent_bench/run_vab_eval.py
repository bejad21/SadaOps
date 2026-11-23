"""Layer 2 Eval: Spoken tool-calling on krutrim-ai-labs/VoiceAgentBench (English split)."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from faster_whisper import WhisperModel
from huggingface_hub import hf_hub_download

from agent.pipeline.llm_router import GroqLLMRouter, load_groq_api_keys
from eval.metrics import bootstrap_ci, score_tool_calls

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "eval" / "cache" / "voice_agent_bench"
RESULTS_PATH = REPO_ROOT / "eval" / "results" / "voice_agent_bench.jsonl"
DATASET_REPO = "krutrim-ai-labs/VoiceAgentBench"

SUBSET_FILES: dict[str, str] = {
    "single_tool": "data/single_tool_data/english/single_tool_english.json",
    "single_tool_retrieval": (
        "data/single_tool_retrieval_data/english/single_tool_retrieval_english.json"
    ),
    "parallel_tool": "data/parallel_tool_data/english/parallel_tool_english.json",
    "seqdep_tool": "data/seqdep_tool_data/english/seqdep_tool_english.json",
    "multi_turn": "data/multi_turn_data/english/multi_turn_english.json",
    "safety": "data/safety_data/english/safety_english.json",
}

_TYPE_MAP: dict[str, str] = {
    "dict": "object",
    "dictionary": "object",
    "float": "number",
    "int": "integer",
    "bool": "boolean",
    "str": "string",
    "list": "array",
    "tuple": "array",
    "any": "string",
}


_VALID_JSON_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}


def _sanitize_json_schema(node: Any) -> Any:
    """Convert Pythonic BFCL/VoiceAgentBench types into JSON Schema Draft 2020-12."""
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    inferred_items: dict[str, Any] | None = None
    for k, v in node.items():
        if k in ("optional", "default"):
            continue
        if k == "type" and isinstance(v, str):
            low = v.strip().lower()
            if low.startswith(("list(", "list[", "array(", "array[")):
                cleaned[k] = "array"
                inner = low.split("(", 1)[-1].split("[", 1)[-1].rstrip(")]").strip()
                item_type = _TYPE_MAP.get(inner, inner)
                inferred_items = {
                    "type": item_type if item_type in _VALID_JSON_TYPES else "string"
                }
            else:
                mapped = _TYPE_MAP.get(low, low)
                cleaned[k] = mapped if mapped in _VALID_JSON_TYPES else "string"
        elif k == "properties" and isinstance(v, dict):
            cleaned[k] = {pk: _sanitize_json_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            cleaned[k] = _sanitize_json_schema(v)
        else:
            cleaned[k] = v
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"
    if cleaned.get("type") == "array" and "items" not in cleaned:
        cleaned["items"] = inferred_items or {"type": "string"}
    return cleaned


def _to_openai_tools(functions: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return (OpenAI tools list, sanitized_name -> original_name mapping)."""
    if isinstance(functions, str):
        functions = json.loads(functions)
    if not isinstance(functions, list):
        return [], {}
    tools: list[dict[str, Any]] = []
    name_map: dict[str, str] = {}
    for fn in functions:
        if not isinstance(fn, dict):
            continue
        inner = (
            dict(fn["function"])
            if fn.get("type") == "function" and "function" in fn
            else dict(fn)
        )
        orig_name = str(inner.get("name", ""))
        safe_name = orig_name.replace(".", "__")
        if not safe_name:
            continue
        name_map[safe_name] = orig_name
        inner["name"] = safe_name
        if "parameters" in inner:
            inner["parameters"] = _sanitize_json_schema(inner["parameters"])
        else:
            inner["parameters"] = {"type": "object", "properties": {}}
        tools.append({"type": "function", "function": inner})
    return tools, name_map


def _load_stratified_subset(per_category: int = 25, seed: int = 42) -> list[dict[str, Any]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []

    for category, json_rel in SUBSET_FILES.items():
        local_json = hf_hub_download(
            repo_id=DATASET_REPO,
            repo_type="dataset",
            filename=json_rel,
            local_dir=str(CACHE_DIR),
        )
        rows = json.loads(Path(local_json).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("data", list(rows.values()))
        indices = list(range(len(rows)))
        rng.shuffle(indices)
        chosen = [rows[i] for i in indices[:per_category]]

        for idx, row in enumerate(chosen):
            audio_rel = row.get("path") or row.get("audio_path")
            history = list(row.get("chat_history") or [])
            local_history: list[dict[str, Any]] = []
            wav_local: str | None = None

            if audio_rel:
                wav_local = hf_hub_download(
                    repo_id=DATASET_REPO,
                    repo_type="dataset",
                    filename=audio_rel,
                    local_dir=str(CACHE_DIR),
                )
            elif history:
                for turn in history:
                    t_copy = dict(turn)
                    if turn.get("path"):
                        t_copy["local_path"] = hf_hub_download(
                            repo_id=DATASET_REPO,
                            repo_type="dataset",
                            filename=turn["path"],
                            local_dir=str(CACHE_DIR),
                        )
                    local_history.append(t_copy)

            if not wav_local and not local_history:
                continue

            selected.append(
                {
                    "item_id": f"{category}-{row.get('id', idx)}",
                    "category": category,
                    "wav_path": wav_local,
                    "query_ref": row.get("query") or row.get("user_request") or "",
                    "functions": row.get("functions") or row.get("tools") or [],
                    "expected_tool_call": row.get("expected_tool_call") or [],
                    "chat_history": local_history if local_history else history,
                }
            )
    return selected


async def _call_llm_for_tools(
    router: GroqLLMRouter,
    transcript: str,
    chat_history: list[dict[str, Any]],
    functions: Any,
    max_steps: int = 3,
) -> tuple[list[dict[str, Any]], float]:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a tool-calling voice assistant. Call the appropriate tool(s) "
                "with exact arguments, or refuse unsafe requests without calling any tools. "
                "When multiple independent tools are needed, call ALL of them together in "
                "parallel in the same turn whenever their arguments are known."
            ),
        }
    ]
    for msg in chat_history:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": str(msg.get("content", ""))})
        elif role == "tool" and msg.get("tool_call_id"):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(msg["tool_call_id"]),
                    "content": str(msg.get("content", "")),
                }
            )
    if transcript:
        messages.append({"role": "user", "content": transcript})

    tools, name_map = _to_openai_tools(functions)
    t0 = time.monotonic() * 1000.0
    all_predicted: list[dict[str, Any]] = []

    for step in range(max_steps):
        accum: dict[int, dict[str, Any]] = {}
        async for chunk in router.stream_chat(
            messages=messages, tools=tools or None, temperature=0.0
        ):
            if not chunk.tool_calls:
                continue
            for tc in chunk.tool_calls:
                idx = int(tc.get("index", 0))
                slot = accum.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id") and not slot["id"]:
                    slot["id"] = str(tc["id"])
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

        step_calls = [
            (idx, v)
            for idx, v in sorted(accum.items())
            if v["name"]
        ]
        if not step_calls:
            break

        assistant_calls = []
        for idx, v in step_calls:
            call_id = v["id"] or f"vab_s{step}_{idx}"
            all_predicted.append(
                {
                    "name": name_map.get(v["name"], v["name"]),
                    "arguments": v["arguments"],
                }
            )
            assistant_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": v["name"], "arguments": v["arguments"] or "{}"},
                }
            )
        # Continue multi-step simulated loop only for multi-step categories (`parallel_tool`,
        # `seqdep_tool`) when caller asks for `max_steps > 1`.
        if max_steps <= 1 or len(tools) <= 1:
            break
        messages.append({"role": "assistant", "content": "", "tool_calls": assistant_calls})
        for ac in assistant_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": ac["id"],
                    "content": json.dumps(
                        {
                            "status": "ok",
                            "note": "Step executed; invoke remaining pending tools if needed.",
                        }
                    ),
                }
            )

    elapsed_ms = round((time.monotonic() * 1000.0) - t0, 2)
    return all_predicted, elapsed_ms


async def run_eval(per_category: int = 25, pace_s: float = 2.0) -> None:
    """Run stratified VoiceAgentBench audio tool-calling evaluation."""
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = _load_stratified_subset(per_category=per_category)
    print(f"Loaded {len(items)} stratified VoiceAgentBench English audio items.")

    done_ids: set[str] = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["item_id"])

    whisper = WhisperModel("base.en", device="cpu", compute_type="int8")
    groq_keys = load_groq_api_keys()
    if not groq_keys:
        raise RuntimeError("At least one GROQ_API_KEY is required for VoiceAgentBench evaluation.")
    print(f"Using {len(groq_keys)} Groq API key(s) with round-robin + failover.")
    router = GroqLLMRouter(api_key=groq_keys)

    with RESULTS_PATH.open("a", encoding="utf-8") as out_fh:
        for idx, item in enumerate(items, start=1):
            if item["item_id"] in done_ids:
                continue

            transcript = ""
            resolved_history: list[dict[str, Any]] = []
            if item["wav_path"]:
                segments, _ = whisper.transcribe(item["wav_path"], beam_size=1)
                transcript = " ".join(s.text.strip() for s in segments).strip()
                resolved_history = item["chat_history"]
            else:
                for turn in item["chat_history"]:
                    t_copy = dict(turn)
                    if turn.get("local_path"):
                        segs, _ = whisper.transcribe(turn["local_path"], beam_size=1)
                        t_copy["content"] = " ".join(s.text.strip() for s in segs).strip()
                    resolved_history.append(t_copy)
                transcript = ""

            try:
                predicted, llm_ms = await _call_llm_for_tools(
                    router,
                    transcript=transcript,
                    chat_history=resolved_history,
                    functions=item["functions"],
                    max_steps=3 if item["category"] in ("parallel_tool", "seqdep_tool") else 1,
                )
                scores = score_tool_calls(item["expected_tool_call"], predicted)
                row = {
                    "item_id": item["item_id"],
                    "category": item["category"],
                    "transcript": transcript
                    or " | ".join(
                        t.get("content", "") for t in resolved_history if t.get("role") == "user"
                    ),
                    "query_ref": item["query_ref"],
                    "expected": item["expected_tool_call"],
                    "predicted": predicted,
                    "llm_ms": llm_ms,
                    **scores,
                }
            except Exception as exc:
                row = {
                    "item_id": item["item_id"],
                    "category": item["category"],
                    "transcript": transcript,
                    "error": str(exc),
                    "tool_name_match": False,
                    "exact_match": False,
                    "arg_f1": 0.0,
                }

            out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_fh.flush()
            if idx % 15 == 0 or idx == len(items):
                print(f"  [{idx}/{len(items)}] evaluated")
            await asyncio.sleep(pace_s)

    rows = [
        json.loads(line)
        for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tm_ci = bootstrap_ci([1.0 if r["tool_name_match"] else 0.0 for r in rows])
    em_ci = bootstrap_ci([1.0 if r["exact_match"] else 0.0 for r in rows])
    print("VoiceAgentBench Tool Name Match:", tm_ci)
    print("VoiceAgentBench Exact Match:", em_ci)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=25)
    parser.add_argument("--pace-s", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(run_eval(per_category=args.per_category, pace_s=args.pace_s))
