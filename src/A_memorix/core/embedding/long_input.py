"""对接口拒绝的超长 BGE-M3 文本进行完整分段编码。"""

from typing import Any, Awaitable, Callable

import numpy as np

from src.llm_models.exceptions import RespNotOkException
from src.llm_models.model_client.base_client import APIResponse


# BGE-M3 上限为 8192 tokens；UTF-8 字节数提供保守预算，并给特殊 token 留余量。
_BGE_INPUT_BYTES = 8190
_BGE_MODELS = {"baai/bge-m3", "pro/baai/bge-m3"}


def split_utf8_text(text: str, budget: int = _BGE_INPUT_BYTES) -> list[str]:
    chunks: list[str] = []
    start = 0
    size = 0
    for index, char in enumerate(text):
        char_size = len(char.encode("utf-8"))
        if char_size > budget:
            raise ValueError("分段预算不能容纳一个完整字符")
        if size + char_size > budget:
            chunks.append(text[start:index])
            start = index
            size = 0
        size += char_size
    if start < len(text):
        chunks.append(text[start:])
    return chunks


async def request_embedding_with_long_input(
    request: Callable[..., Awaitable[Any]], *, client: Any, model_info: Any,
    text: str, extra_params: dict,
) -> Any:
    """保留成功响应；仅在 BGE-M3 超长文本被 400 拒绝后分段。

    每段仍使用原模型、维度参数及请求重试逻辑。任何分段失败均向上传递，
    不丢弃正文、不填充零向量，也不把其他模型或鉴权错误当作长度问题。
    """
    async def call(part: str) -> Any:
        return await request(client=client, model_info=model_info, text=part, extra_params=extra_params)

    try:
        return await call(text)
    except RespNotOkException as exc:
        if (
            exc.status_code != 400
            or str(model_info.model_identifier).lower() not in _BGE_MODELS
            or len(text.encode("utf-8")) <= _BGE_INPUT_BYTES
        ):
            raise

    weighted: np.ndarray | None = None
    for part in split_utf8_text(text):
        # 纯空白段不携带语义，部分 Provider 拒绝空白输入。
        if not part.strip():
            continue
        response = await call(part)
        vector = np.asarray(response.embedding, dtype=np.float64)
        if vector.ndim != 1 or not vector.size or not np.all(np.isfinite(vector)):
            raise ValueError("分段 embedding 必须是非空有限一维向量")
        if weighted is not None and weighted.shape != vector.shape:
            raise ValueError("分段 embedding 维度不一致")
        contribution = vector * len(part.encode("utf-8"))
        weighted = contribution if weighted is None else weighted + contribution
    if weighted is None:
        raise ValueError("超长 embedding 输入不包含非空文本")
    norm = np.linalg.norm(weighted)
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("分段 embedding 合并结果不能归一化")
    return APIResponse(embedding=(weighted / norm).astype(np.float32).tolist())
