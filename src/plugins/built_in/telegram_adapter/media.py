"""媒体转换在线程执行；FFmpeg 使用随依赖安装的可执行文件，无需修改系统 PATH。"""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Tuple

from PIL import Image, ImageOps
from imageio_ffmpeg import get_ffmpeg_exe
from rlottie_python import LottieAnimation

import gzip
import json
import subprocess
import sys

MAX_CONVERTED_BYTES = 50 * 1024 * 1024


def _save_image(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    output = BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def _ffmpeg(binary: bytes, suffix: str, output_suffix: str, args: List[str], runtime: Path, timeout: int) -> bytes:
    runtime.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="tg-media-", dir=runtime) as directory:
        source = Path(directory) / f"input{suffix}"
        output = Path(directory) / f"output{output_suffix}"
        source.write_bytes(binary)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                [
                    get_ffmpeg_exe(),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-threads",
                    "1",
                    "-filter_threads",
                    "1",
                    "-i",
                    str(source),
                    *args,
                    "-fs",
                    str(MAX_CONVERTED_BYTES),
                    "-y",
                    str(output),
                ],
                capture_output=True,
                timeout=timeout,
                creationflags=flags,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Telegram 媒体转换超时") from None
        if completed.returncode != 0 or not output.is_file():
            detail = completed.stderr.decode("utf-8", errors="replace").replace(str(directory), "<临时目录>")[-400:]
            raise ValueError(f"FFmpeg 转换失败: {detail}")
        if output.stat().st_size >= MAX_CONVERTED_BYTES:
            raise ValueError("Telegram 媒体转换结果超过 50 MB")
        return output.read_bytes()


def sticker_to_image(binary: bytes, animated: bool, video: bool, runtime: Path, timeout: int) -> bytes:
    if video:
        return _ffmpeg(
            binary,
            ".webm",
            ".gif",
            # 普通 MP4 动图也走这条转换链；不放大小画面，并用共享调色板控制 GIF 体积。
            [
                "-an", "-filter_complex_threads", "1", "-filter_complex",
                "fps=15,scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease,"
                "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
            ],
            runtime,
            timeout,
        )
    if animated:
        # TGS 是 gzip 压缩的 Lottie JSON。限制解压大小与帧数，避免不受控的渲染工作。
        with gzip.GzipFile(fileobj=BytesIO(binary)) as source:
            decoded = source.read(1024 * 1024 + 1)
        if len(decoded) > 1024 * 1024:
            raise ValueError("TGS 解压结果超过 1 MB")
        model = json.loads(decoded)
        if not (0 < model["w"] <= 512 and 0 < model["h"] <= 512):
            raise ValueError("TGS 画布尺寸无效")
        with LottieAnimation.from_data(decoded.decode("utf-8")) as animation:
            fps = animation.lottie_animation_get_framerate()
            total = animation.lottie_animation_get_totalframe()
            if fps <= 0 or total <= 0 or total / fps > 3.1 or total > 200:
                raise ValueError("TGS 帧数或时长不符合贴纸规范")
            frames = [
                animation.render_pillow_frame(frame_num=frame) for frame in range(0, total, max(1, round(fps / 15)))
            ]
            return _save_image(
                frames[0], "GIF", save_all=True, append_images=frames[1:], duration=67, loop=0, disposal=2
            )
    with Image.open(BytesIO(binary)) as image:
        return _save_image(image.convert("RGBA"), "PNG")


def voice_to_wav(binary: bytes, runtime: Path, timeout: int) -> bytes:
    return _ffmpeg(binary, ".audio", ".wav", ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"], runtime, timeout)


def voice_to_opus(binary: bytes, runtime: Path, timeout: int) -> bytes:
    return _ffmpeg(
        binary,
        ".audio",
        ".ogg",
        ["-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k"],
        runtime,
        timeout,
    )


def prepare_image(binary: bytes, sticker: bool) -> Tuple[str, str, bytes, str, str]:
    """选择 TG 媒体方法；原生贴纸文件复用由插件负责，普通动图以动画发送。"""
    if sticker and binary.startswith(b"\x1f\x8b"):
        return "sendSticker", "sticker", binary, "sticker.tgs", "application/x-tgsticker"
    if sticker and binary.startswith(b"\x1aE\xdf\xa3"):
        return "sendSticker", "sticker", binary, "sticker.webm", "video/webm"
    with Image.open(BytesIO(binary)) as source:
        if source.format in {"GIF", "PNG", "WEBP"} and source.is_animated:
            if source.format == "GIF":
                return "sendAnimation", "animation", binary, "animation.gif", "image/gif"
            frames = []
            durations = []
            if source.n_frames > 200:
                raise ValueError("动画帧数超过 200")
            for index in range(source.n_frames):
                source.seek(index)
                frames.append(source.convert("RGBA"))
                durations.append(source.info.get("duration", 100))
            converted = _save_image(
                frames[0], "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2
            )
            return "sendAnimation", "animation", converted, "animation.gif", "image/gif"
        image = ImageOps.exif_transpose(source).convert("RGBA")
        if sticker:
            image.thumbnail((512, 512))
            canvas = Image.new("RGBA", (512, 512))
            canvas.alpha_composite(image, ((512 - image.width) // 2, (512 - image.height) // 2))
            converted = _save_image(canvas, "WEBP", quality=90)
            if len(converted) > 512 * 1024:
                raise ValueError("静态贴纸超过 512 KB")
            return "sendSticker", "sticker", converted, "sticker.webp", "image/webp"
        image.thumbnail((2560, 2560))
        # 极端长宽比超出 sendPhoto 约束时，加白边保留全部图片内容。
        width, height = image.size
        width = max(width, (height + 19) // 20)
        height = max(height, (width + 19) // 20)
        background = Image.new("RGB", (width, height), "white")
        background.paste(image, ((width - image.width) // 2, (height - image.height) // 2), image)
        converted = _save_image(background, "JPEG", quality=92)
        if len(converted) > 10 * 1024 * 1024:
            raise ValueError("图片超过 Telegram 的 10 MB 上限")
        return "sendPhoto", "photo", converted, "photo.jpg", "image/jpeg"
