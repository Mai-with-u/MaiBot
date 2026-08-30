from __future__ import annotations

import pytest

from src.llm_models.model_client.openai_client import _detect_audio_file_name


@pytest.mark.parametrize(
    ("audio_bytes", "expected_file_name"),
    [
        (b"RIFF" + b"\x00" * 4 + b"WAVEfmt ", "audio.wav"),
        (b"fLaC" + b"\x00" * 12, "audio.flac"),
        (b"OggS" + b"\x00" * 12, "audio.ogg"),
        (b"\x1aE\xdf\xa3" + b"\x00" * 12, "audio.webm"),
        (b"ID3" + b"\x00" * 13, "audio.mp3"),
        (b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 4, "audio.m4a"),
        (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4, "audio.mp4"),
        (b"#!AMR\n" + b"\x00" * 10, "audio.amr"),
    ],
)
def test_detect_audio_file_name(audio_bytes: bytes, expected_file_name: str) -> None:
    assert _detect_audio_file_name(audio_bytes) == expected_file_name


def test_detect_audio_file_name_rejects_qq_silk() -> None:
    with pytest.raises(ValueError, match="QQ Silk"):
        _detect_audio_file_name(b"\x02#!SILK_V3" + b"\x00" * 8)


def test_detect_audio_file_name_rejects_unknown_data() -> None:
    with pytest.raises(ValueError, match="无法识别音频格式"):
        _detect_audio_file_name(b"not-audio")
