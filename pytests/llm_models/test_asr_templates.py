from email.parser import BytesParser
from email.policy import default
from imageio_ffmpeg import get_ffmpeg_exe
from typing import Literal, Type

import base64
import io
import subprocess
import wave

import httpx
import pytest

from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.model_client.asr_audio import prepare_deepseek_audio, transcription_filename
from src.llm_models.model_client.base_client import AudioTranscriptionRequest
from src.llm_models.model_client.openai_client import OpenaiClient
from src.llm_models.model_client.openai_responses_client import OpenAIResponsesClient


def make_wav(sample_width: int = 2, seconds: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, sample_width, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(8000 * seconds * sample_width))
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_cls", "base_path", "kind", "identifier", "endpoint"),
    [
        (OpenaiClient, "", "asr", "deepseek-asr", "/v1/tools/asr"),
        (OpenAIResponsesClient, "/v1/", "asr", "deepseek-asr", "/v1/tools/asr"),
        (OpenaiClient, "/v1", "asr", "gpt-transcribe", "/v1/audio/transcriptions"),
        (OpenaiClient, "/v1", "asr", "FunAudioLLM/SenseVoiceSmall", "/v1/audio/transcriptions"),
        (OpenaiClient, "/v1", "llm", "whisper-1", "/v1/audio/transcriptions"),
    ],
)
async def test_asr_multipart(
    client_cls: Type[OpenaiClient],
    base_path: str,
    kind: Literal["llm", "asr"],
    identifier: str,
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wav = make_wav()

    def receive(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == endpoint
        assert request.headers["Authorization"] == "Bearer test-key"
        content_type = request.headers["Content-Type"]
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + request.content
        )
        fields = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        expected = {"file": wav}
        if endpoint.endswith("/transcriptions"):
            expected["model"] = identifier.encode()
        assert fields == expected
        return httpx.Response(200, json={"text": "你好"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http_client:
        monkeypatch.setattr(
            "src.llm_models.model_client.openai_client.DefaultAsyncHttpxClient", lambda **kw: http_client
        )
        client = client_cls(APIProvider(name="test", api_key="test-key", base_url="https://asr.test" + base_path))
        model = ModelInfo(name="test-asr", api_provider="test", model_type=kind, model_identifier=identifier)
        result = await client.get_audio_transcriptions(AudioTranscriptionRequest(model, base64.b64encode(wav).decode()))
        assert result.content == "你好"


def test_deepseek_converts_pcm_and_rejects_long_recordings() -> None:
    name, converted, mime = prepare_deepseek_audio(make_wav(sample_width=1))
    assert (name, mime) == ("audio.wav", "audio/wav")
    with wave.open(io.BytesIO(converted)) as wav:
        assert (wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 16000, 16000)
    with pytest.raises(ValueError, match="120 秒"):
        prepare_deepseek_audio(make_wav(seconds=121))


def test_ogg_opus_can_be_uploaded_without_transcoding() -> None:
    opus = subprocess.run(
        [get_ffmpeg_exe(), "-v", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        input=make_wav(),
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout
    assert prepare_deepseek_audio(opus) == ("audio.ogg", opus, "audio/ogg")
    assert transcription_filename(opus) == "audio.ogg"
