from typing import Tuple


def detect_audio_format(data: bytes) -> Tuple[str, str]:
    """根据音频文件头返回扩展名和 MIME 类型。"""
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav", "audio/wav"
    if data.startswith(b"OggS"):
        return "ogg", "audio/ogg"
    if data.startswith(b"fLaC"):
        return "flac", "audio/flac"
    if data[4:8] == b"ftyp":
        return "mp4", "audio/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm", "audio/webm"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE6 == 0xE2):
        return "mp3", "audio/mpeg"
    if data.startswith((b"\xff\xf1", b"\xff\xf9")):
        return "aac", "audio/aac"
    return "", "application/octet-stream"
