from imageio_ffmpeg import get_ffmpeg_exe
from typing import Tuple

import io
import subprocess
import wave

from src.common.utils.audio_format import detect_audio_format


def transcription_filename(data: bytes) -> str:
    """根据聊天附件的文件头为转写接口还原文件名。"""
    extension, _ = detect_audio_format(data)
    return f"audio.{extension}" if extension else "audio"


def prepare_deepseek_audio(data: bytes) -> Tuple[str, bytes, str]:
    """准备 DeepSeek 接受的 Ogg Opus 或 16 位 PCM WAV 文件。"""
    if data.startswith(b"OggS") and len(data) > 27 and data.startswith(b"OpusHead", 27 + data[26]):
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("DeepSeek ASR 音频超过 8 MiB")
        return "audio.ogg", data, "audio/ogg"

    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(data)) as audio:
                if audio.getnframes() > 120 * audio.getframerate():
                    raise ValueError("DeepSeek ASR 音频超过 120 秒")
                if (
                    audio.getsampwidth() == 2
                    and audio.getnchannels() in (1, 2)
                    and audio.getframerate() in (8000, 16000, 22050, 24000, 32000, 44100, 48000)
                    and len(data) <= 8 * 1024 * 1024
                ):
                    return "audio.wav", data, "audio/wav"
        except wave.Error:
            # 非 PCM WAV 交给 FFmpeg 解码。
            pass

    # 多读一秒以识别超长录音，避免把截断的音频当作完整消息发送。
    result = subprocess.run(
        [
            get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-t",
            "121",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        input=data,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode or not result.stdout:
        raise ValueError(f"语音解码失败：{result.stderr.decode(errors='replace').strip()}")
    if len(result.stdout) > 120 * 16000 * 2:
        raise ValueError("DeepSeek ASR 音频超过 120 秒")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(result.stdout)
    return "audio.wav", output.getvalue(), "audio/wav"
