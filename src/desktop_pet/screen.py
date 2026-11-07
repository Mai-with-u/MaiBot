"""本机主屏截图；只在工作线程中捕获和编码，不写截图文件。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import List
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageGrab
import hashlib
import sys


@dataclass(frozen=True)
class DesktopScreenshot:
    data: bytes
    captured_at: str
    width: int
    height: int
    digest: str


def capture_desktop() -> DesktopScreenshot:
    if sys.platform != "win32":
        raise RuntimeError("本机桌面读取目前仅支持 Windows")
    with ImageGrab.grab(all_screens=False) as original:
        captured_at = datetime.now(timezone.utc).isoformat()
        image = original.convert("RGB")
    try:
        # 限制多模态输入尺寸，保留常见桌面文字；缩放和编码不占用事件循环。
        image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        return DesktopScreenshot(data, captured_at, image.width, image.height, hashlib.sha256(data).hexdigest())
    finally:
        image.close()


def screen_change_ratio(previous: DesktopScreenshot, current: DesktopScreenshot) -> float:
    """在缩略图上计算明显变化的像素比例，过滤光标、时钟等小面积变化。"""
    if (previous.width, previous.height) != (current.width, current.height):
        return 1.0
    if previous.digest == current.digest:
        return 0.0
    with Image.open(BytesIO(previous.data)) as before, Image.open(BytesIO(current.data)) as after:
        with before.convert("RGB") as rgb_before, after.convert("RGB") as rgb_after:
            with rgb_before.resize((320, 180)) as small_before, rgb_after.resize((320, 180)) as small_after:
                with ImageChops.difference(small_before, small_after) as diff:
                    with diff.convert("L") as gray:
                        histogram = gray.histogram()
    # 单像素亮度差不足 20/255 视为压缩、抗锯齿或轻微动画噪声。
    return sum(histogram[20:]) / (320 * 180)


class ScreenTimeline:
    """保留上一批末帧作起点和最新变化帧；队列有界，比较基准是上次保留的帧。"""

    def __init__(self) -> None:
        self.frames: List[DesktopScreenshot] = []
        self.changed = False

    def add(self, shot: DesktopScreenshot, threshold: float) -> bool:
        if self.frames and screen_change_ratio(self.frames[-1], shot) < threshold:
            return False
        self.frames.append(shot)
        if len(self.frames) > 6:
            # 保留时间段起点和最近五个变化，舍弃过早的中间帧。
            del self.frames[1]
        self.changed = True
        return True

    def delivered(self) -> None:
        self.frames = self.frames[-1:]
        self.changed = False


def compose_screen_timeline(frames: List[DesktopScreenshot]) -> DesktopScreenshot:
    """从左到右、从上到下拼接变化帧，编号和时间均写入图片。"""
    if not 1 <= len(frames) <= 6:
        raise ValueError("屏幕时间线需要 1～6 帧")
    columns = min(2, len(frames))
    tile_width, tile_height, label_height = 960, 540, 32
    rows = (len(frames) + columns - 1) // columns
    with Image.new("RGB", (columns * tile_width, rows * (tile_height + label_height)), "#fff8ec") as canvas:
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=18)
        for index, frame in enumerate(frames):
            left = index % columns * tile_width
            top = index // columns * (tile_height + label_height)
            # 标签使用 UTC ISO 时间，跨日期和时区时也不会误解先后。
            draw.text((left + 10, top + 8), f"#{index + 1}  {frame.captured_at}", fill="#1d1d1b", font=font)
            with Image.open(BytesIO(frame.data)) as source:
                source.thumbnail((tile_width - 8, tile_height - 8), Image.Resampling.LANCZOS)
                canvas.paste(source, (left + (tile_width - source.width) // 2,
                                     top + label_height + (tile_height - source.height) // 2))
            draw.rectangle((left, top, left + tile_width - 1, top + tile_height + label_height - 1), outline="#dcc4a3")
        buffer = BytesIO()
        canvas.save(buffer, format="PNG")
        data = buffer.getvalue()
        return DesktopScreenshot(data, frames[-1].captured_at, canvas.width, canvas.height,
                                 hashlib.sha256(data).hexdigest())
