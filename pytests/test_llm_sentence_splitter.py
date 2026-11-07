from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import json
import sys

import pytest


@pytest.fixture
def splitter(monkeypatch):
    client = SimpleNamespace(generate_response=AsyncMock())
    modules = {
        "src.common.logger": {"get_logger": Mock()},
        "src.services.llm_service": {"LLMServiceClient": Mock(return_value=client)},
    }
    # 隔离宿主配置、日志及真实 LLM 服务，测试只加载断句器本身。
    for name, exports in modules.items():
        module = ModuleType(name)
        module.__dict__.update(exports)
        monkeypatch.setitem(sys.modules, name, module)

    path = Path(__file__).resolve().parents[1] / "src/chat/utils/llm_sentence_splitter.py"
    spec = spec_from_file_location("isolated_llm_sentence_splitter", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, client


@pytest.mark.asyncio
async def test_prompt_preserves_punctuation_and_delegates_trailing_commas(splitter):
    module, client = splitter
    text = "先跟 Alice 打招呼，再确认时间。"
    client.generate_response.return_value = SimpleNamespace(response="no_split")

    await module.split_text_with_llm(text)

    module.LLMServiceClient.assert_called_once_with(task_name="fast_model", request_type="response.splitter")
    client.generate_response.assert_awaited_once_with(module._SPLITTER_PROMPT + text)
    prompt = module._SPLITTER_PROMPT
    assert "保留原文的所有字符和标点" in prompt
    assert "中英文逗号必须保留在前一段末尾" in prompt
    assert "段尾逗号由程序处理，不要自行删除" in prompt
    assert "不要在在结尾留逗号" not in prompt
    assert "不要在结尾留逗号" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["， \n", ", \t"])
async def test_five_valid_segments_preserve_original_separators(splitter, separator):
    module, client = splitter
    contents = ["先跟 Alice 打招呼", "然后确认时间", "接着整理资料", "再核对安排", "最后一起出发。"]
    text = separator.join(contents)
    sentences = [content + separator for content in contents[:-1]] + contents[-1:]
    client.generate_response.return_value = SimpleNamespace(response=json.dumps(sentences, ensure_ascii=False))

    result = await module.split_text_with_llm(text)

    assert result == [(content, separator) for content in contents[:-1]] + [(contents[-1], "")]
    assert "".join(content + trailing for content, trailing in result) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("comma", ["，", ","])
async def test_leading_model_whitespace_is_kept_in_original_separator(splitter, comma):
    module, client = splitter
    text = f"Hello{comma} world"
    client.generate_response.return_value = SimpleNamespace(response=json.dumps([f"Hello{comma}", " world"]))

    result = await module.split_text_with_llm(text)

    assert result == [("Hello", comma + " "), ("world", "")]
    assert "".join(content + separator for content, separator in result) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("comma", ["，", ","])
async def test_deleted_comma_is_rejected(splitter, comma):
    module, client = splitter
    text = f"Alice 先确认时间{comma}Bob 再准备资料。"
    client.generate_response.return_value = SimpleNamespace(
        response=json.dumps(["Alice 先确认时间", "Bob 再准备资料。"], ensure_ascii=False)
    )

    with pytest.raises(ValueError, match="未完整保留原文"):
        await module.split_text_with_llm(text)


@pytest.mark.asyncio
async def test_changed_english_name_is_rejected(splitter):
    module, client = splitter
    client.generate_response.return_value = SimpleNamespace(
        response=json.dumps(["Florian Kupfeu confirmed, ", "Bob agreed."])
    )

    with pytest.raises(ValueError, match="未完整保留原文"):
        await module.split_text_with_llm("Florian Kupfer confirmed, Bob agreed.")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["no_split", '"no_split"', '["no_split"]'])
@pytest.mark.parametrize("separator", ["", "， \t", ", \n"])
async def test_no_split_keeps_existing_trailing_comma_handling(splitter, payload, separator):
    module, client = splitter
    text = "完整代码 `Alice()`。"
    client.generate_response.return_value = SimpleNamespace(response=payload)

    assert await module.split_text_with_llm(text + separator) == [(text, separator)]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["not json", '"普通字符串"', "[]", "{}", "[123]"])
async def test_invalid_payload_is_rejected(splitter, payload):
    module, client = splitter
    client.generate_response.return_value = SimpleNamespace(response=payload)

    with pytest.raises(ValueError):
        await module.split_text_with_llm("Alice confirmed, Bob agreed.")


@pytest.mark.asyncio
async def test_empty_text_does_not_call_llm(splitter):
    module, client = splitter

    assert await module.split_text_with_llm("") == []
    module.LLMServiceClient.assert_not_called()
    client.generate_response.assert_not_awaited()
