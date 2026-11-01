from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.llm_models.exceptions import RespNotOkException


source = Path(__file__).resolve().parents[1] / "core/embedding/long_input.py"
if not source.exists():
    source = Path(__file__).resolve().parents[2] / "src/A_memorix/core/embedding/long_input.py"
spec = spec_from_file_location("embedding_long_input_under_test", source)
module = module_from_spec(spec)
spec.loader.exec_module(module)


def test_utf8_chunks_preserve_all_text_and_character_boundaries():
    text = ("中文🙂 English\n" * 3000)
    chunks = module.split_utf8_text(text)
    assert "".join(chunks) == text
    assert all(0 < len(part.encode("utf-8")) <= 8190 for part in chunks)


@pytest.mark.asyncio
async def test_rejected_long_input_encodes_every_chunk_and_normalizes():
    text = "中" * 7000
    seen = []
    params = {"dimensions": 2}

    async def request(**kwargs):
        assert kwargs["extra_params"] is params
        seen.append(kwargs["text"])
        if len(seen) == 1:
            raise RespNotOkException(400, "input too long")
        return SimpleNamespace(embedding=[3.0, 4.0])

    response = await module.request_embedding_with_long_input(
        request, client=object(), model_info=SimpleNamespace(model_identifier="BAAI/bge-m3"),
        text=text, extra_params=params,
    )
    assert seen[0] == text
    assert "".join(seen[1:]) == text
    assert np.allclose(response.embedding, [0.6, 0.8])


@pytest.mark.asyncio
async def test_successful_large_request_is_returned_unchanged():
    response = SimpleNamespace(embedding=[1.0, 2.0])
    async def request(**kwargs):
        return response
    result = await module.request_embedding_with_long_input(
        request, client=None, model_info=SimpleNamespace(model_identifier="BAAI/bge-m3"),
        text="中" * 7000, extra_params={},
    )
    assert result is response


@pytest.mark.asyncio
@pytest.mark.parametrize("status,model,text", [
    (401, "BAAI/bge-m3", "中" * 7000),
    (400, "other-model", "中" * 7000),
    (400, "BAAI/bge-m3", "short"),
])
async def test_unrelated_errors_propagate_without_additional_requests(status, model, text):
    calls = []
    error = RespNotOkException(status, "failed")
    async def request(**kwargs):
        calls.append(kwargs)
        raise error
    with pytest.raises(RespNotOkException) as caught:
        await module.request_embedding_with_long_input(
            request, client=None, model_info=SimpleNamespace(model_identifier=model), text=text, extra_params={},
        )
    assert caught.value is error
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("vectors", [[[], []], [[1.0], [1.0, 2.0]], [[float("nan")], [1.0]], [[0.0], [0.0]]])
async def test_invalid_segment_vectors_are_rejected(vectors):
    calls = 0
    async def request(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RespNotOkException(400, "input too long")
        return SimpleNamespace(embedding=vectors[min(calls - 2, len(vectors) - 1)])
    with pytest.raises(ValueError):
        await module.request_embedding_with_long_input(
            request, client=None, model_info=SimpleNamespace(model_identifier="BAAI/bge-m3"),
            text="中" * 7000, extra_params={},
        )
