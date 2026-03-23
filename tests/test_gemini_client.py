import types
import unittest

from src.config.api_ada_configs import APIProvider, ModelInfo
from src.llm_models.model_client.base_client import APIResponse, BaseClient
from src.llm_models.model_client.gemini_client import GeminiClient
from src.llm_models.payload_content.message import Message, RoleType


class FakeModels:
    def __init__(self):
        self.normal_calls = []
        self.stream_calls = []
        self.stream_result = object()

    async def generate_content(self, *, model, contents, config):
        self.normal_calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        return object()

    async def generate_content_stream(self, *, model, contents, config):
        self.stream_calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        return self.stream_result


class GeminiClientSearchSuffixTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_client():
        api_provider = APIProvider(
            name="provider",
            base_url="",
            api_key="test-key",
            client_type="gemini",
        )
        client = object.__new__(GeminiClient)
        BaseClient.__init__(client, api_provider)
        fake_models = FakeModels()
        client.client = types.SimpleNamespace(
            aio=types.SimpleNamespace(models=fake_models),
        )
        return client, fake_models

    @staticmethod
    def _build_model_info(force_stream_mode: bool = False):
        return ModelInfo(
            model_identifier="gemini-3-flash-preview-search",
            name="gemini-search",
            api_provider="provider",
            force_stream_mode=force_stream_mode,
        )

    @staticmethod
    def _build_messages():
        return [Message(role=RoleType.User, content="hello")]

    @staticmethod
    def _has_google_search_tool(config) -> bool:
        tools = getattr(config, "tools", None) or []
        return any(getattr(tool, "google_search", None) is not None for tool in tools)

    @staticmethod
    def _parse_response(_raw_response):
        return APIResponse(content="ok"), None

    @staticmethod
    async def _handle_stream(_raw_stream, _interrupt_flag):
        return APIResponse(content="ok"), None

    async def test_search_suffix_is_scoped_to_each_non_stream_request(self):
        client, fake_models = self._build_client()
        model_info = self._build_model_info()

        for _ in range(2):
            response = await client.get_response(
                model_info=model_info,
                message_list=self._build_messages(),
                async_response_parser=self._parse_response,
            )
            self.assertEqual("ok", response.content)
            self.assertEqual("gemini-3-flash-preview-search", model_info.model_identifier)

        self.assertEqual(
            ["gemini-3-flash-preview", "gemini-3-flash-preview"],
            [call["model"] for call in fake_models.normal_calls],
        )
        self.assertTrue(
            all(self._has_google_search_tool(call["config"]) for call in fake_models.normal_calls)
        )
        self.assertEqual([], fake_models.stream_calls)

    async def test_search_suffix_is_scoped_to_each_stream_request(self):
        client, fake_models = self._build_client()
        model_info = self._build_model_info(force_stream_mode=True)

        for _ in range(2):
            response = await client.get_response(
                model_info=model_info,
                message_list=self._build_messages(),
                stream_response_handler=self._handle_stream,
            )
            self.assertEqual("ok", response.content)
            self.assertEqual("gemini-3-flash-preview-search", model_info.model_identifier)

        self.assertEqual(
            ["gemini-3-flash-preview", "gemini-3-flash-preview"],
            [call["model"] for call in fake_models.stream_calls],
        )
        self.assertTrue(
            all(self._has_google_search_tool(call["config"]) for call in fake_models.stream_calls)
        )
        self.assertEqual([], fake_models.normal_calls)
