"""声明式皮肤包校验与安装。仅接收本地图片，不执行皮肤内代码。"""

from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List
from PIL import Image
import base64
import json
import re
import shutil
import tempfile
import zipfile

MAX_BYTES = 32 * 1024 * 1024
BUNDLED_SKIN_ROOT = Path(__file__).with_name("presets")
STATES = ("idle", "thinking", "talking", "dragging")
EVENTS = ("click", "happy", "error", "greeting")
MOTIONS = {"none", "bob", "sway", "hop", "shake", "pulse"}
BUILTIN = {
    "schema_version": 1, "id": "builtin", "name": "麦麦（内置）", "version": "1",
    "canvas": {"width": 150, "height": 107, "anchor": [75, 107], "bubble_anchor": [75, 0], "hitbox": [0, 0, 150, 107]},
    "states": {name: name for name in STATES},
    "events": {"click": "squish", "happy": "happy", "error": "shake", "greeting": "happy"},
    "animations": {name: {"duration_ms": duration} for name, duration in {
        "idle": 3200, "thinking": 1100, "talking": 1200, "dragging": 1000,
        "squish": 400, "happy": 650, "shake": 500, "blink": 200,
    }.items()},
}


def skin_directories(root: Path) -> List[Path]:
    return [*sorted(BUNDLED_SKIN_ROOT.glob("*/skin.json")),
            *(root / "data" / "desktop_pet" / "skins").glob("*/skin.json"),
            *(root / "plugins").glob("*/skins/*/skin.json")]


def _integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"整数应在 {low} 至 {high} 之间")
    return value


def load_skin(path: Path, *, assets: bool = True) -> Dict[str, Any]:
    if path.stat().st_size > 256 * 1024:
        raise ValueError("skin.json 不能超过 256 KiB")
    data = json.loads(path.read_text("utf-8"))
    if data.get("schema_version") != 1 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", data.get("id", "")):
        raise ValueError("无效皮肤版本或 ID")
    if data["id"] == "builtin":
        raise ValueError("builtin 是保留 ID")
    for field in ("name", "version"):
        if not isinstance(data.get(field), str) or not 1 <= len(data[field]) <= 100:
            raise ValueError(f"缺少或无效的 {field}")
    canvas = data["canvas"]
    width = _integer(canvas["width"], 64, 220)
    height = _integer(canvas["height"], 64, 240)
    for key in ("anchor", "bubble_anchor"):
        point = canvas[key]
        if len(point) != 2:
            raise ValueError("锚点必须包含两个坐标")
        _integer(point[0], 0, width)
        _integer(point[1], 0, height)
    if not width - 130 <= canvas["anchor"][0] <= 130 or height - canvas["anchor"][1] > 4:
        raise ValueError("落脚锚点需在底部 4 像素内，且形象不能超出窗口左右边界")
    box = canvas["hitbox"]
    if len(box) != 4:
        raise ValueError("点击区域必须包含 x,y,width,height")
    x, y, w, h = box
    _integer(x, 0, width)
    _integer(y, 0, height)
    _integer(w, 1, width - x)
    _integer(h, 1, height - y)
    animations = data["animations"]
    if not isinstance(animations, dict) or not 1 <= len(animations) <= 64:
        raise ValueError("动画数量应为 1 至 64")
    resources: Dict[str, str] = {}
    dimensions: Dict[str, Any] = {}
    total = 0

    def resource(filename: str):
        nonlocal total
        if filename in dimensions:
            return dimensions[filename]
        rel = PurePosixPath(filename)
        if rel.is_absolute() or ".." in rel.parts or "\\" in filename or ":" in filename:
            raise ValueError("皮肤资源必须使用包内相对路径")
        file = (path.parent / filename).resolve()
        if not file.is_relative_to(path.parent.resolve()) or file.suffix.lower() not in {".png", ".webp"}:
            raise ValueError("仅允许包内 PNG/WebP 图片")
        size = file.stat().st_size
        total += size
        if size > MAX_BYTES or total > MAX_BYTES:
            raise ValueError("皮肤图片总大小不能超过 32 MiB")
        raw = file.read_bytes()
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 16_000_000 or image.format not in {"PNG", "WEBP"}:
                raise ValueError("图片格式或尺寸不支持")
            if image.is_animated:
                raise ValueError("请使用静态图片帧或精灵图，不支持动图文件")
            dimensions[filename] = image.size
            mime = Image.MIME[image.format]
            image.verify()
        if assets:
            resources[filename] = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return dimensions[filename]

    normalized = {}
    for name, animation in animations.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("无效动画名")
        motion = animation.get("motion", "none")
        if motion not in MOTIONS:
            raise ValueError("未知动作效果")
        frames = animation.get("sequence")
        if frames is None:
            filename = animation["file"]
            iw, ih = resource(filename)
            fw = _integer(animation.get("frame_width", iw), 1, iw)
            fh = _integer(animation.get("frame_height", ih), 1, ih)
            count = _integer(animation.get("frames", 1), 1, 240)
            fps = _integer(animation.get("fps", 8), 1, 60)
            columns = iw // fw
            frames = [{"file": filename, "rect": [(i % columns) * fw, (i // columns) * fh, fw, fh],
                       "duration_ms": round(1000 / fps)} for i in range(count)]
        if not isinstance(frames, list) or not 1 <= len(frames) <= 240:
            raise ValueError("每个动画应为 1 至 240 帧")
        clean_frames = []
        for frame in frames:
            filename = frame["file"]
            iw, ih = resource(filename)
            rect = frame.get("rect", [0, 0, iw, ih])
            if len(rect) != 4:
                raise ValueError("帧区域必须包含四个坐标")
            x, y, w, h = rect
            _integer(x, 0, iw - 1)
            _integer(y, 0, ih - 1)
            _integer(w, 1, iw - x)
            _integer(h, 1, ih - y)
            clean_frames.append({"file": filename, "rect": rect,
                                 "duration_ms": _integer(frame.get("duration_ms", 125), 16, 10000)})
        duration = sum(frame["duration_ms"] for frame in clean_frames)
        if duration > 30000:
            raise ValueError("单个动画不能超过 30 秒")
        normalized[name] = {"sequence": clean_frames, "duration_ms": duration, "motion": motion}
    states, events = data["states"], data.get("events", {})
    if set(states) != set(STATES) or not set(events).issubset(EVENTS):
        raise ValueError("必须显式映射 idle/thinking/talking/dragging，事件名需使用标准事件")
    if any(name not in normalized for name in [*states.values(), *events.values()]):
        raise ValueError("状态或事件引用了不存在的动画")
    return {"schema_version": 1, "id": data["id"], "name": data["name"], "version": data["version"],
            "author": str(data.get("author", ""))[:100], "canvas": canvas,
            "animations": normalized, "states": states, "events": events, "resources": resources}


def find_skin(root: Path, skin_id: str, *, assets: bool = True) -> Dict[str, Any]:
    if skin_id == "builtin":
        return json.loads(json.dumps(BUILTIN))
    matches = []
    for path in skin_directories(root):
        # 候选由清单 ID 决定，不允许把任意路径作为皮肤 ID。
        if path.stat().st_size <= 256 * 1024:
            try:
                if json.loads(path.read_text("utf-8")).get("id") == skin_id:
                    matches.append(path)
            except (ValueError, OSError):
                continue
    if len(matches) != 1:
        raise ValueError(f"皮肤 {skin_id} 不存在或 ID 重复")
    return load_skin(matches[0], assets=assets)


def list_skins(root: Path) -> Dict[str, Any]:
    skins = [find_skin(root, "builtin", assets=False)]
    errors = []
    for path in skin_directories(root):
        try:
            skin = load_skin(path, assets=False)
            find_skin(root, skin["id"], assets=False)
            skins.append(skin)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({"package": str(path.parent.name), "error": str(exc)})
    return {"skins": skins, "errors": errors}


def install_zip(root: Path, payload: bytes) -> Dict[str, Any]:
    if len(payload) > MAX_BYTES:
        raise ValueError("压缩包不能超过 32 MiB")
    destination = root / "data" / "desktop_pet" / "skins"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skin-", dir=destination.parent) as temporary:
        folder = Path(temporary)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 512 or sum(entry.file_size for entry in entries) > MAX_BYTES:
                raise ValueError("皮肤包解压后超过限制")
            for entry in entries:
                rel = PurePosixPath(entry.filename)
                if rel.is_absolute() or ".." in rel.parts or "\\" in entry.filename or ":" in entry.filename:
                    raise ValueError("压缩包包含不安全路径")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("皮肤包不能包含符号链接")
                if entry.is_dir():
                    continue
                if rel.suffix.lower() not in {".json", ".png", ".webp"}:
                    raise ValueError("皮肤包只能包含清单和 PNG/WebP 图片")
                target = folder.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        manifests = list(folder.rglob("skin.json"))
        if len(manifests) != 1:
            raise ValueError("皮肤包必须恰好包含一个 skin.json")
        skin = load_skin(manifests[0], assets=False)
        if skin["id"] in {item["id"] for item in list_skins(root)["skins"]}:
            raise ValueError("皮肤 ID 已存在，请使用不同 ID")
        target = destination / skin["id"]
        if target.exists():
            raise ValueError("目标皮肤目录已存在")
        manifests[0].parent.rename(target)
        return skin
