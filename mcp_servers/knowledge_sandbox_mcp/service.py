"""Qdrant Runbooks RAG (local multilingual fastembed) + isolated Docker Python sandbox."""

from __future__ import annotations

import ast
import hashlib
import os
import threading
import uuid
from typing import Any

import docker
from docker.errors import ContainerError, ImageNotFound
from fastembed import TextEmbedding
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from mcp_servers.knowledge_sandbox_mcp.runbooks_data import RUNBOOKS

COLLECTION_NAME = "sadaops_runbooks"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SANDBOX_IMAGE = "sadaops-python-sandbox:latest"

_EMBEDDER: TextEmbedding | None = None
_EMBEDDER_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()


def _runbooks_digest() -> str:
    joined = "|".join(f"{r['id']}:{r['title']}:{r['content']}" for r in RUNBOOKS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _read_capped_logs(container: Any, *, stdout: bool, stderr: bool, max_bytes: int) -> str:
    buf = bytearray()
    stream = container.logs(stdout=stdout, stderr=stderr, stream=True)
    try:
        for chunk in stream:
            remaining = max_bytes - len(buf)
            if remaining <= 0:
                break
            buf.extend(chunk[:remaining])
            if len(buf) >= max_bytes:
                break
    finally:
        close_fn = getattr(stream, "close", None)
        if callable(close_fn):
            close_fn()
    return buf.decode("utf-8", errors="replace")


def get_embedder() -> TextEmbedding:
    """Lazy-load multilingual fastembed model once per process."""
    global _EMBEDDER
    with _EMBEDDER_LOCK:
        if _EMBEDDER is None:
            _EMBEDDER = TextEmbedding(model_name=EMBED_MODEL)
    return _EMBEDDER


class PythonExecInput(BaseModel):
    """Validated code and timeout parameters for sandboxed Python execution."""

    code: str = Field(min_length=1, max_length=4000)
    timeout_s: int = Field(default=5, ge=1, le=10)


def _wrap_trailing_expression(code: str) -> str:
    """Auto-print a trailing non-print expression like a REPL so bare math returns stdout."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return code
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return code
    last_expr = tree.body[-1].value
    if (
        isinstance(last_expr, ast.Call)
        and isinstance(last_expr.func, ast.Name)
        and last_expr.func.id == "print"
    ):
        return code
    target = ast.Name(id="_sada_last_expr", ctx=ast.Store())
    assign = ast.Assign(targets=[target], value=last_expr)
    check = ast.If(
        test=ast.Compare(
            left=ast.Name(id="_sada_last_expr", ctx=ast.Load()),
            ops=[ast.IsNot()],
            comparators=[ast.Constant(value=None)],
        ),
        body=[
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="print", ctx=ast.Load()),
                    args=[ast.Name(id="_sada_last_expr", ctx=ast.Load())],
                    keywords=[],
                )
            )
        ],
        orelse=[],
    )
    tree.body[-1] = assign
    tree.body.append(check)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


class KnowledgeSandboxService:
    """Provides bilingual runbook retrieval in Qdrant and isolated Docker Python execution."""

    def __init__(
        self,
        qdrant_url: str | None = None,
        collection_name: str = COLLECTION_NAME,
        sandbox_image: str = SANDBOX_IMAGE,
    ) -> None:
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        self.collection_name = collection_name
        self.sandbox_image = sandbox_image
        self._indexed_digest: str | None = None

    def _new_qdrant_client(self) -> QdrantClient:
        return QdrantClient(url=self.qdrant_url, timeout=10, check_compatibility=False)

    def ensure_runbooks_indexed(self) -> int:
        """Create Qdrant collection idempotently and upsert runbooks if absent or updated."""
        digest = _runbooks_digest()
        with _INDEX_LOCK:
            client = self._new_qdrant_client()
            try:
                embedder = get_embedder()
                existing = [c.name for c in client.get_collections().collections]
                if self.collection_name not in existing:
                    sample_vec = next(embedder.embed(["passage: init"]))
                    try:
                        client.create_collection(
                            collection_name=self.collection_name,
                            vectors_config=VectorParams(
                                size=len(sample_vec), distance=Distance.COSINE
                            ),
                        )
                    except UnexpectedResponse as exc:
                        if getattr(exc, "status_code", None) != 409:
                            raise

                info = client.get_collection(self.collection_name)
                if self._indexed_digest == digest and (info.points_count or 0) >= len(RUNBOOKS):
                    return int(info.points_count or 0)

                if (info.points_count or 0) >= len(RUNBOOKS):
                    sample, _ = client.scroll(
                        collection_name=self.collection_name, limit=1, with_payload=True
                    )
                    if sample and (sample[0].payload or {}).get("digest") == digest:
                        self._indexed_digest = digest
                        return int(info.points_count or 0)

                passages = [f"passage: {rb['title']} — {rb['content']}" for rb in RUNBOOKS]
                vectors = list(embedder.embed(passages))
                points = [
                    PointStruct(
                        id=idx + 1,
                        vector=vec.tolist(),
                        payload={**rb, "digest": digest},
                    )
                    for idx, (rb, vec) in enumerate(zip(RUNBOOKS, vectors, strict=True))
                ]
                client.upsert(collection_name=self.collection_name, points=points)
                self._indexed_digest = digest
                return len(points)
            finally:
                client.close()

    def search_runbooks(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Semantic search over bilingual engineering runbooks."""
        self.ensure_runbooks_indexed()
        client = self._new_qdrant_client()
        try:
            embedder = get_embedder()
            q_vec = next(embedder.embed([f"query: {query}"])).tolist()

            res = client.query_points(
                collection_name=self.collection_name,
                query=q_vec,
                limit=max(1, min(top_k, 8)),
            )
            return [
                {
                    "id": hit.payload.get("id"),
                    "title": hit.payload.get("title"),
                    "service": hit.payload.get("service"),
                    "score": round(float(hit.score), 4),
                    "content": hit.payload.get("content"),
                }
                for hit in res.points
                if hit.payload
            ]
        finally:
            client.close()

    def run_python_calc(self, code: str, timeout_s: int = 5) -> dict[str, Any]:
        """Execute Python calculation inside an ephemeral, network-disabled Docker container.

        Enforces non-root execution, read-only rootfs, 128MB RAM (no swap), 32 PID cap, and timeout.
        """
        params = PythonExecInput(code=code, timeout_s=timeout_s)
        exec_code = _wrap_trailing_expression(params.code)
        client = docker.from_env()
        container_name = f"sadaops-sbx-{uuid.uuid4().hex[:10]}"
        container = None
        try:
            container = client.containers.create(
                image=self.sandbox_image,
                name=container_name,
                command=["python3", "-I", "-c", exec_code],
                network_mode="none",
                mem_limit="128m",
                memswap_limit="128m",
                nano_cpus=500_000_000,
                pids_limit=32,
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            container.start()
            result = container.wait(timeout=params.timeout_s)
            exit_code = int(result.get("StatusCode", -1))
            stdout = _read_capped_logs(container, stdout=True, stderr=False, max_bytes=4096)
            stderr = _read_capped_logs(container, stdout=False, stderr=True, max_bytes=2048)
            return {
                "exit_code": exit_code,
                "stdout": stdout[:4000],
                "stderr": stderr[:2000],
                "timed_out": False,
            }
        except ImageNotFound as exc:
            raise RuntimeError(
                f"Sandbox image '{self.sandbox_image}' not built. Run docker build first."
            ) from exc
        except ContainerError as exc:
            return {
                "exit_code": exc.exit_status,
                "stdout": "",
                "stderr": str(exc)[:2000],
                "timed_out": False,
            }
        except Exception as exc:
            if "timed out" in str(exc).lower() or "read timeout" in str(exc).lower():
                try:
                    if container is not None:
                        container.kill()
                except Exception:
                    pass
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Execution exceeded {params.timeout_s}s timeout and was killed.",
                    "timed_out": True,
                }
            raise
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass
