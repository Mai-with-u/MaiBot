from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, Optional, Protocol

import asyncio
import time

import numpy as np

from .fingerprints import build_image_embedding_fingerprint


class ImageEmbedder(Protocol):
    async def probe(self) -> Dict[str, Any]: ...

    async def embed(self, image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]: ...


def _probe_png(color: tuple[int, int, int]) -> bytes:
    from PIL import Image as PILImage

    output = BytesIO()
    PILImage.new("RGB", (24, 24), color).save(output, format="PNG")
    return output.getvalue()


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0


class HostImageEmbedder:
    """通过 MaiBot 的独立图片嵌入门面调用已配置模型。"""

    def __init__(
        self,
        *,
        task_name: str = "image_embedding",
        preprocess_version: str = "identity_v1",
        probe_retry_seconds: float = 60.0,
    ) -> None:
        self.task_name = str(task_name or "image_embedding")
        self.preprocess_version = str(preprocess_version or "identity_v1")
        self.probe_retry_seconds = max(0.0, float(probe_retry_seconds))
        self._probe_lock = asyncio.Lock()
        self._probe_report: Optional[Dict[str, Any]] = None
        self._next_probe_at = 0.0

    async def _request(self, image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        from src.services.embedding_service import EmbeddingServiceClient

        result = await EmbeddingServiceClient(
            task_name=self.task_name,
            request_type="A_Memorix.ImageEmbedding",
            session_id=session_id,
        ).embed_image(
            image_bytes,
            mime_type=mime_type,
            preprocess_version=self.preprocess_version,
            session_id=session_id,
        )
        vector = np.asarray(result.embedding, dtype=np.float32).reshape(-1)
        if vector.size == 0 or not np.isfinite(vector).all():
            raise RuntimeError("图片嵌入模型返回了空向量或非有限数值")
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-12:
            raise RuntimeError("图片嵌入模型返回了零向量")
        vector /= norm
        fingerprint = build_image_embedding_fingerprint(
            model=result.model_identifier or result.model_name,
            provider=result.api_provider,
            request_protocol_hash=result.request_protocol_hash,
            dimension=int(vector.size),
            preprocess_version=self.preprocess_version,
        )
        return {"embedding": vector, "fingerprint": fingerprint}

    async def probe(self) -> Dict[str, Any]:
        if self._probe_report is not None and (
            self._probe_report.get("available") or time.monotonic() < self._next_probe_at
        ):
            return dict(self._probe_report)
        async with self._probe_lock:
            if self._probe_report is not None and (
                self._probe_report.get("available") or time.monotonic() < self._next_probe_at
            ):
                return dict(self._probe_report)
            try:
                first = await self._request(_probe_png((20, 80, 210)), mime_type="image/png")
                repeat = await self._request(_probe_png((20, 80, 210)), mime_type="image/png")
                other = await self._request(_probe_png((220, 40, 30)), mime_type="image/png")
                if repeat["fingerprint"] != first["fingerprint"] or other["fingerprint"] != first["fingerprint"]:
                    raise RuntimeError("图片嵌入模型在探测期间返回了不同的嵌入空间")
                same_score = _cosine(first["embedding"], repeat["embedding"])
                different_score = _cosine(first["embedding"], other["embedding"])
                if same_score < 0.95:
                    raise RuntimeError(f"同图嵌入不稳定，余弦相似度为 {same_score:.4f}")
                if different_score >= 0.999:
                    raise RuntimeError("不同探测图片的向量无法区分")
                self._probe_report = {
                    "available": True,
                    "status": "ready",
                    "fingerprint": first["fingerprint"],
                    "dimension": int(first["embedding"].size),
                    "same_image_cosine": same_score,
                    "different_image_cosine": different_score,
                    "message": "图片嵌入模型已通过真实图片探测",
                }
            except Exception as exc:
                self._probe_report = {
                    "available": False,
                    "status": "unavailable",
                    "fingerprint": {},
                    "dimension": 0,
                    "reason": type(exc).__name__,
                    "message": str(exc),
                }
                self._next_probe_at = time.monotonic() + self.probe_retry_seconds
            return dict(self._probe_report)

    async def embed(self, image_bytes: bytes, *, mime_type: str, session_id: str = "") -> Dict[str, Any]:
        report = await self.probe()
        if not report.get("available"):
            raise RuntimeError(str(report.get("message") or "图片嵌入模型不可用"))
        result = await self._request(image_bytes, mime_type=mime_type, session_id=session_id)
        if result["fingerprint"] != report["fingerprint"]:
            raise RuntimeError("图片嵌入空间在运行期间发生变化")
        return result
