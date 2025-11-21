#!/usr/bin/env python3
"""
.mmipkg 表情包打包工具
用于导入/导出 MaiBot 已注册表情包

版本：1.0
日期：2025-11-13
"""

import hashlib
import io
import os
import struct
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

try:
    import msgpack
except ImportError:
    print("错误: 需要安装 msgpack 库")
    print("请运行: pip install msgpack")
    sys.exit(1)

try:
    import zstandard as zstd
except ImportError:
    print("警告: zstandard 库未安装，将不支持压缩功能")
    print("建议安装: pip install zstandard")
    zstd = None

try:
    from PIL import Image
except ImportError:
    print("错误: 需要安装 Pillow 库")
    print("请运行: pip install Pillow")
    sys.exit(1)

try:
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn
    from rich.console import Console
except ImportError:
    print("错误: 需要安装 rich 库")
    print("请运行: pip install rich")
    sys.exit(1)

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 延迟导入数据库模块（在添加路径之后）
# ruff: noqa: E402
from src.common.database.database import db
from src.common.database.database_model import Emoji

# 常量定义
MAGIC = b"MMIP"
FOOTER_MAGIC = b"MMFF"
VERSION = 1
FOOTER_VERSION = 1

# 安全限制
MAX_MANIFEST_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_PAYLOAD_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB

# 支持的图片格式
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}

# 创建控制台对象
console = Console()


class MMIPKGError(Exception):
    """MMIPKG 相关错误"""

    pass


def calculate_sha256(data: bytes) -> bytes:
    """计算 SHA256 并返回二进制结果"""
    return hashlib.sha256(data).digest()


def calculate_file_sha256(file_path: str) -> bytes:
    """计算文件的 SHA256"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.digest()


def get_image_info(file_path: str) -> Tuple[int, int, str]:
    """获取图片信息 (width, height, mime_type)"""
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            format_lower = img.format.lower() if img.format else "unknown"
            mime_map = {
                "jpeg": "image/jpeg",
                "jpg": "image/jpeg",
                "png": "image/png",
                "gif": "image/gif",
                "webp": "image/webp",
                "avif": "image/avif",
                "bmp": "image/bmp",
            }
            mime_type = mime_map.get(format_lower, f"image/{format_lower}")
            return width, height, mime_type
    except Exception as e:
        print(f"警告: 无法读取图片信息 {file_path}: {e}")
        return 0, 0, "image/unknown"


def reencode_image(file_path: str, output_format: str = "webp", quality: int = 80) -> bytes:
    """重新编码图片"""
    try:
        with Image.open(file_path) as img:
            # 转换为 RGB（如果需要）
            if img.mode in ("RGBA", "LA", "P"):
                if output_format.lower() == "jpeg":
                    # JPEG 不支持透明度，转为白色背景
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = background
                elif output_format.lower() == "webp":
                    # WebP 支持透明度
                    if img.mode == "P":
                        img = img.convert("RGBA")
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            # 编码图片
            output = io.BytesIO()
            save_kwargs = {"format": output_format.upper()}

            if output_format.lower() in {"jpeg", "jpg"}:
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
            elif output_format.lower() == "webp":
                save_kwargs["quality"] = quality
                save_kwargs["method"] = 6  # 更好的压缩
            elif output_format.lower() == "png":
                save_kwargs["optimize"] = True

            img.save(output, **save_kwargs)
            return output.getvalue()
    except Exception as e:
        raise MMIPKGError(f"重新编码图片失败 {file_path}: {e}") from e


class MMIPKGPacker:
    """MMIPKG 打包器"""

    def __init__(
        self,
        use_compression: bool = True,
        zstd_level: int = 3,
        reencode: Optional[str] = None,
        reencode_quality: int = 80,
    ):
        self.use_compression = use_compression and zstd is not None
        self.zstd_level = zstd_level
        self.reencode = reencode
        self.reencode_quality = reencode_quality

        if use_compression and zstd is None:
            print("警告: zstandard 未安装，将不使用压缩")
            self.use_compression = False

    def pack_from_db(
        self, output_path: str, pack_name: Optional[str] = None, custom_manifest: Optional[Dict] = None
    ) -> bool:
        """从数据库导出已注册的表情包

        Args:
            output_path: 输出文件路径
            pack_name: 包名称
            custom_manifest: 自定义 manifest 额外字段（可选）
        """
        try:
            # 连接数据库
            if db.is_closed():
                db.connect()

            # 查询所有已注册的表情包
            emojis = Emoji.select().where(Emoji.is_registered)
            emoji_count = emojis.count()

            if emoji_count == 0:
                print("错误: 数据库中没有已注册的表情包")
                return False

            print(f"找到 {emoji_count} 个已注册的表情包")

            # 准备 items
            items = []
            image_data_list = []

            # 使用进度条处理表情包
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]扫描表情包...", total=emoji_count)

                for idx, emoji in enumerate(emojis, 1):
                    progress.update(
                        task, description=f"[cyan]处理 {idx}/{emoji_count}: {os.path.basename(emoji.full_path)}"
                    )

                    # 检查文件是否存在
                    if not os.path.exists(emoji.full_path):
                        console.print("  [yellow]警告: 文件不存在，跳过[/yellow]")
                        progress.advance(task)
                        continue

                    # 读取或重新编码图片
                    if self.reencode:
                        try:
                            img_bytes = reencode_image(emoji.full_path, self.reencode, self.reencode_quality)
                        except Exception as e:
                            console.print(f"  [yellow]警告: 重新编码失败，使用原始文件: {e}[/yellow]")
                            with open(emoji.full_path, "rb") as f:
                                img_bytes = f.read()
                    else:
                        with open(emoji.full_path, "rb") as f:
                            img_bytes = f.read()

                    # 计算 SHA256
                    img_sha = calculate_sha256(img_bytes)

                    # 获取图片信息
                    width, height, mime_type = get_image_info(emoji.full_path)

                    # 构建 item（使用短字段名）
                    filename = os.path.basename(emoji.full_path)
                    item = {
                        "i": str(idx).zfill(5),  # id
                        "fn": filename,  # filename
                        "s": len(img_bytes),  # size
                        "h": img_sha,  # sha256 (binary)
                        "m": mime_type,  # mime
                        "w": width,  # width
                        "ht": height,  # height
                        "opt": {
                            # 存储 MaiBot 特有的元数据 - 完整的数据库信息
                            "desc": emoji.description or "",
                            "emotion": emoji.emotion or "",
                            "usage_count": emoji.usage_count or 0,
                            "last_used_time": emoji.last_used_time or time.time(),
                            "register_time": emoji.register_time or time.time(),
                            "record_time": emoji.record_time or time.time(),
                            "query_count": emoji.query_count or 0,
                            "format": emoji.format or "",
                            "emoji_hash": emoji.emoji_hash or "",
                            "is_registered": True,
                            "is_banned": emoji.is_banned or False,
                        },
                    }

                    items.append(item)
                    image_data_list.append(img_bytes)
                    progress.advance(task)

            if not items:
                print("错误: 没有有效的表情包可以打包")
                return False

            print(f"找到 {len(items)} 个表情包可以打包...")

            # 准备打包
            pack_id = str(uuid.uuid4())
            if pack_name is None:
                pack_name = f"MaiBot_Emojis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            manifest = {
                "p": pack_id,  # pack_id
                "n": pack_name,  # pack_name
                "t": datetime.now().isoformat(),  # created_at
                "a": items,  # items array
            }

            # 添加自定义字段
            if custom_manifest:
                for key, value in custom_manifest.items():
                    if key not in manifest:  # 不覆盖核心字段
                        manifest[key] = value

            # 序列化 manifest
            manifest_bytes = msgpack.packb(manifest, use_bin_type=True)
            manifest_len = len(manifest_bytes)

            # 计算 payload 大小
            payload_size = 4 + manifest_len  # manifest_len + manifest_bytes
            for img_bytes in image_data_list:
                payload_size += 4 + len(img_bytes)  # img_len + img_bytes

            print(f"Manifest 大小: {manifest_len / 1024:.2f} KB")
            print(f"Payload 未压缩大小: {payload_size / 1024 / 1024:.2f} MB")

            # 写入文件
            return self._write_package(output_path, manifest_bytes, image_data_list, payload_size)

        except Exception as e:
            print(f"打包失败: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            if not db.is_closed():
                db.close()

    def _write_package(
        self, output_path: str, manifest_bytes: bytes, image_data_list: List[bytes], payload_size: int
    ) -> bool:
        """写入打包文件"""
        try:
            with open(output_path, "wb") as f:
                # 写入 Header (32 bytes)
                flags = 0x01 if self.use_compression else 0x00
                header = MAGIC  # 4 bytes
                header += struct.pack("B", VERSION)  # 1 byte
                header += struct.pack("B", flags)  # 1 byte
                header += b"\x00\x00"  # 2 bytes reserved
                header += struct.pack(">Q", payload_size)  # 8 bytes
                header += struct.pack(">Q", len(manifest_bytes))  # 8 bytes
                header += b"\x00" * 8  # 8 bytes reserved

                assert len(header) == 32, f"Header size mismatch: {len(header)}"
                f.write(header)

                # 准备 payload 并计算 SHA256
                payload_sha = hashlib.sha256()

                # 写入 payload（可能压缩）
                if self.use_compression:
                    console.print(f"[cyan]使用 Zstd 压缩 (level={self.zstd_level})...[/cyan]")
                    compressor = zstd.ZstdCompressor(level=self.zstd_level)

                    with compressor.stream_writer(f, closefd=False) as writer:
                        # 写入 manifest
                        manifest_len_bytes = struct.pack(">I", len(manifest_bytes))
                        writer.write(manifest_len_bytes)
                        writer.write(manifest_bytes)
                        payload_sha.update(manifest_len_bytes)
                        payload_sha.update(manifest_bytes)

                        # 使用进度条写入所有图片
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            TimeRemainingColumn(),
                            console=console,
                        ) as progress:
                            task = progress.add_task("[green]压缩写入图片...", total=len(image_data_list))

                            for idx, img_bytes in enumerate(image_data_list, 1):
                                progress.update(task, description=f"[green]压缩写入 {idx}/{len(image_data_list)}")
                                img_len_bytes = struct.pack(">I", len(img_bytes))
                                writer.write(img_len_bytes)
                                writer.write(img_bytes)
                                payload_sha.update(img_len_bytes)
                                payload_sha.update(img_bytes)
                                progress.advance(task)
                else:
                    # 不压缩，直接写入
                    # 写入 manifest
                    manifest_len_bytes = struct.pack(">I", len(manifest_bytes))
                    f.write(manifest_len_bytes)
                    f.write(manifest_bytes)
                    payload_sha.update(manifest_len_bytes)
                    payload_sha.update(manifest_bytes)

                    # 使用进度条写入所有图片
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        TimeRemainingColumn(),
                        console=console,
                    ) as progress:
                        task = progress.add_task("[green]写入图片...", total=len(image_data_list))

                        for idx, img_bytes in enumerate(image_data_list, 1):
                            progress.update(task, description=f"[green]写入 {idx}/{len(image_data_list)}")
                            img_len_bytes = struct.pack(">I", len(img_bytes))
                            f.write(img_len_bytes)
                            f.write(img_bytes)
                            payload_sha.update(img_len_bytes)
                            payload_sha.update(img_bytes)
                            progress.advance(task)

                # 写入 Footer (40 bytes)
                file_sha256 = payload_sha.digest()
                footer = FOOTER_MAGIC  # 4 bytes
                footer += file_sha256  # 32 bytes
                footer += struct.pack("B", FOOTER_VERSION)  # 1 byte
                footer += b"\x00" * 3  # 3 bytes reserved

                assert len(footer) == 40, f"Footer size mismatch: {len(footer)}"
                f.write(footer)

                file_size = f.tell()
                print("\n打包完成!")
                print(f"输出文件: {output_path}")
                print(f"文件大小: {file_size / 1024 / 1024:.2f} MB")
                if self.use_compression:
                    ratio = (1 - file_size / (payload_size + 32 + 40)) * 100
                    print(f"压缩率: {ratio:.1f}%")

                return True

        except Exception as e:
            print(f"写入文件失败: {e}")
            import traceback

            traceback.print_exc()
            return False


class MMIPKGUnpacker:
    """MMIPKG 解包器"""

    def __init__(self, verify_sha: bool = True):
        self.verify_sha = verify_sha

    def import_to_db(
        self, package_path: str, output_dir: Optional[str] = None, replace_existing: bool = False, batch_size: int = 500
    ) -> bool:
        """导入到数据库"""
        try:
            if not os.path.exists(package_path):
                print(f"错误: 文件不存在: {package_path}")
                return False

            # 连接数据库
            if db.is_closed():
                db.connect()

            # 如果未指定输出目录，使用默认的已注册表情包目录
            if output_dir is None:
                output_dir = os.path.join(PROJECT_ROOT, "data", "emoji_registed")

            os.makedirs(output_dir, exist_ok=True)

            print(f"正在读取包: {package_path}")

            with open(package_path, "rb") as f:
                # 读取 Header
                header = f.read(32)
                if len(header) != 32:
                    raise MMIPKGError("Header 大小不正确")

                magic = header[:4]
                if magic != MAGIC:
                    raise MMIPKGError(f"无效的 MAGIC: {magic}")

                version = struct.unpack("B", header[4:5])[0]
                if version != VERSION:
                    print(f"警告: 包版本 {version} 与当前版本 {VERSION} 不匹配")

                flags = struct.unpack("B", header[5:6])[0]
                is_compressed = bool(flags & 0x01)

                payload_uncompressed_len = struct.unpack(">Q", header[8:16])[0]
                manifest_uncompressed_len = struct.unpack(">Q", header[16:24])[0]

                # 安全检查
                if manifest_uncompressed_len > MAX_MANIFEST_SIZE:
                    raise MMIPKGError(f"Manifest 过大: {manifest_uncompressed_len} bytes")
                if payload_uncompressed_len > MAX_PAYLOAD_SIZE:
                    raise MMIPKGError(f"Payload 过大: {payload_uncompressed_len} bytes")

                print(f"压缩: {'是' if is_compressed else '否'}")
                print(f"Payload 大小: {payload_uncompressed_len / 1024 / 1024:.2f} MB")

                # 读取 payload
                payload_start = f.tell()

                # 找到 footer 位置
                f.seek(-40, 2)  # 从文件末尾向前 40 bytes
                footer = f.read(40)

                if footer[:4] != FOOTER_MAGIC:
                    raise MMIPKGError("无效的 Footer MAGIC")

                expected_sha = footer[4:36]

                # 回到 payload 开始
                f.seek(payload_start)

                # 读取整个 payload（用于计算 SHA）
                footer_start = os.path.getsize(package_path) - 40
                payload_data_size = footer_start - payload_start

                # 解压或直接读取
                if is_compressed:
                    if zstd is None:
                        raise MMIPKGError("需要 zstandard 库来解压此包")

                    print("解压 payload...")
                    compressed_data = f.read(payload_data_size)

                    # 使用流式解压，不需要预知解压后大小
                    decompressor = zstd.ZstdDecompressor()
                    try:
                        # 方法1：使用 stream_reader（推荐）
                        dctx = zstd.ZstdDecompressor()
                        with io.BytesIO(compressed_data) as compressed_stream:
                            with dctx.stream_reader(compressed_stream) as reader:
                                payload_data = reader.read()
                    except Exception as e:
                        # 方法2：如果流式失败，尝试直接解压（兼容旧格式）
                        print(f"  流式解压失败，尝试直接解压: {e}")
                        try:
                            payload_data = decompressor.decompress(
                                compressed_data, max_output_size=payload_uncompressed_len
                            )
                        except Exception as e2:
                            raise MMIPKGError(f"解压失败: {e2}") from e2
                else:
                    payload_data = f.read(payload_data_size)

                # 验证 SHA256
                actual_sha = calculate_sha256(payload_data)
                if self.verify_sha and actual_sha != expected_sha:
                    raise MMIPKGError("SHA256 校验失败!")
                if self.verify_sha:
                    print("✓ SHA256 校验通过")

                # 解析 payload
                payload_stream = io.BytesIO(payload_data)

                # 读取 manifest
                manifest_len_bytes = payload_stream.read(4)
                manifest_len = struct.unpack(">I", manifest_len_bytes)[0]
                manifest_bytes = payload_stream.read(manifest_len)
                manifest = msgpack.unpackb(manifest_bytes, raw=False)

                pack_id = manifest.get("p", "unknown")
                pack_name = manifest.get("n", "unknown")
                created_at = manifest.get("t", "unknown")
                items = manifest.get("a", [])

                print("\n包信息:")
                print(f"  ID: {pack_id}")
                print(f"  名称: {pack_name}")
                print(f"  创建时间: {created_at}")
                print(f"  表情包数量: {len(items)}")

                # 导入表情包
                return self._import_items(payload_stream, items, output_dir, replace_existing, batch_size)

        except Exception as e:
            print(f"导入失败: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            if not db.is_closed():
                db.close()

    def _import_items(
        self, payload_stream: BinaryIO, items: List[Dict], output_dir: str, replace_existing: bool, batch_size: int
    ) -> bool:
        """导入 items 到数据库"""
        try:
            imported_count = 0
            skipped_count = 0
            error_count = 0

            # 开始事务，使用进度条
            with db.atomic():
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("[cyan]导入表情包...", total=len(items))

                    for idx, item in enumerate(items, 1):
                        try:
                            progress.update(task, description=f"[cyan]导入 {idx}/{len(items)}")

                            # 读取图片数据
                            img_len_bytes = payload_stream.read(4)
                            if len(img_len_bytes) != 4:
                                console.print(f"[red]错误: 读取图片长度失败 (item {idx})[/red]")
                                error_count += 1
                                progress.advance(task)
                                continue

                            img_len = struct.unpack(">I", img_len_bytes)[0]
                            img_bytes = payload_stream.read(img_len)

                            if len(img_bytes) != img_len:
                                console.print(f"[red]错误: 图片数据不完整 (item {idx})[/red]")
                                error_count += 1
                                progress.advance(task)
                                continue

                            # 验证图片 SHA
                            if self.verify_sha and (expected_sha := item.get("h")):
                                actual_sha = calculate_sha256(img_bytes)
                                if actual_sha != expected_sha:
                                    console.print(f"[yellow]警告: 图片 SHA256 不匹配 (item {idx}), 跳过[/yellow]")
                                    error_count += 1
                                    progress.advance(task)
                                    continue

                            # 获取元数据
                            opt = item.get("opt", {})
                            # 使用 or 提供回退值，如果 emoji_hash 为空则使用后续计算的值
                            emoji_hash = opt.get("emoji_hash") or calculate_sha256(img_bytes).hex()

                            # 检查是否已存在
                            existing = Emoji.get_or_none(Emoji.emoji_hash == emoji_hash)

                            if existing and not replace_existing:
                                skipped_count += 1
                                progress.advance(task)
                                continue

                            # 保存图片文件
                            filename = item.get("fn", f"{emoji_hash[:8]}.{opt.get('format', 'png')}")
                            file_path = os.path.join(output_dir, filename)

                            # 如果文件已存在且不替换，生成新文件名
                            if os.path.exists(file_path) and not replace_existing:
                                base, ext = os.path.splitext(filename)
                                counter = 1
                                while os.path.exists(file_path):
                                    filename = f"{base}_{counter}{ext}"
                                    file_path = os.path.join(output_dir, filename)
                                    counter += 1

                            with open(file_path, "wb") as img_file:
                                img_file.write(img_bytes)

                            # 准备数据库记录
                            current_time = time.time()
                            emotion_str = opt.get("emotion", "")

                            if existing and replace_existing:
                                # 更新现有记录 - 恢复完整的数据库信息
                                existing.full_path = file_path
                                existing.format = opt.get("format", "")
                                existing.description = opt.get("desc", "")
                                existing.emotion = emotion_str
                                existing.usage_count = opt.get("usage_count", 0)
                                existing.last_used_time = opt.get("last_used_time", current_time)
                                existing.register_time = opt.get("register_time", current_time)
                                existing.record_time = opt.get("record_time", current_time)
                                existing.query_count = opt.get("query_count", 0)
                                existing.is_registered = opt.get("is_registered", True)
                                existing.is_banned = opt.get("is_banned", False)
                                existing.save()
                            else:
                                # 创建新记录 - 恢复完整的数据库信息
                                Emoji.create(
                                    emoji_hash=emoji_hash,
                                    full_path=file_path,
                                    format=opt.get("format", ""),
                                    description=opt.get("desc", ""),
                                    emotion=emotion_str,
                                    usage_count=opt.get("usage_count", 0),
                                    last_used_time=opt.get("last_used_time", current_time),
                                    register_time=opt.get("register_time", current_time),
                                    record_time=opt.get("record_time", current_time),
                                    query_count=opt.get("query_count", 0),
                                    is_registered=opt.get("is_registered", True),
                                    is_banned=opt.get("is_banned", False),
                                )

                            imported_count += 1
                            progress.advance(task)

                        except Exception as e:
                            console.print(f"[red]处理 item {idx} 时出错: {e}[/red]")
                            error_count += 1
                            progress.advance(task)
                            continue

            # 输出统计

            console.print(f"\n[green]✓ 成功导入 {imported_count} 个表情包[/green]")
            console.print(f"  [yellow]跳过 {skipped_count} 个[/yellow]")
            if error_count > 0:
                console.print(f"  [red]错误 {error_count} 个[/red]")

            return error_count == 0

        except Exception as e:
            console.print(f"[red]导入 items 失败: {e}[/red]")
            import traceback

            traceback.print_exc()
            return False


def print_header():
    """打印欢迎信息"""
    console.print("\n[bold cyan]" + "=" * 70 + "[/bold cyan]")
    console.print("[bold cyan]" + " " * 20 + "MaiBot 表情包打包工具" + "[/bold cyan]")
    console.print("[bold cyan]" + " " * 25 + ".mmipkg 格式" + "[/bold cyan]")
    console.print("[bold cyan]" + "=" * 70 + "[/bold cyan]")


def print_menu():
    """打印主菜单"""
    console.print("\n[yellow]请选择操作:[/yellow]")
    console.print("  [1] [bold]导出表情包[/bold] (从数据库导出到 .mmipkg 文件)")
    console.print("  [2] [bold]导入表情包[/bold] (从 .mmipkg 文件导入到数据库)")
    console.print("  [0] [bold]退出[/bold]")
    console.print()


def get_input(prompt: str, default: Optional[str] = None, choices: Optional[List[str]] = None) -> str:
    """获取用户输入"""
    if default:
        prompt = f"{prompt} (默认: {default})"

    while True:
        try:
            value = input(f"{prompt}: ").strip()

            if not value:
                if default:
                    return default
                console.print("  [yellow]⚠ 输入不能为空，请重新输入[/yellow]")
                continue

            if choices and value not in choices:
                console.print(f"  [yellow]⚠ 无效的选择，请选择: {', '.join(choices)}[/yellow]")
                continue

            return value
        except KeyboardInterrupt:
            console.print("\n[yellow]操作已取消[/yellow]")
            raise
        except EOFError:
            console.print("\n[yellow]输入已结束[/yellow]")
            if default:
                return default
            raise KeyboardInterrupt from None


def get_yes_no(prompt: str, default: bool = False) -> bool:
    """获取是/否输入"""
    default_str = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"{prompt} ({default_str}): ").strip().lower()

            if not value:
                return default

            if value in ("y", "yes", "是"):
                return True
            elif value in ("n", "no", "否"):
                return False
            else:
                console.print("  [yellow]⚠ 请输入 y/yes/是 或 n/no/否[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[yellow]操作已取消[/yellow]")
            raise
        except EOFError:
            return default


def get_int(prompt: str, default: int, min_val: int = 1, max_val: int = 100) -> int:
    """获取整数输入"""
    while True:
        try:
            value = input(f"{prompt} (默认: {default}, 范围: {min_val}-{max_val}): ").strip()

            if not value:
                return default

            try:
                num = int(value)
                if min_val <= num <= max_val:
                    return num
                else:
                    console.print(f"  [yellow]⚠ 请输入 {min_val} 到 {max_val} 之间的数字[/yellow]")
            except ValueError:
                console.print("  [yellow]⚠ 请输入有效的数字[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[yellow]操作已取消[/yellow]")
            raise
        except EOFError:
            return default


def print_compression_level_info():
    """打印压缩级别说明"""
    console.print("\n  [cyan]压缩级别说明:[/cyan]")
    console.print("    1-3:  快速压缩，文件稍大")
    console.print("    4-9:  平衡模式（推荐）")
    console.print("    10-15: 高压缩，速度较慢")
    console.print("    16-22: 极限压缩，速度很慢")


def print_import_mode_selection():
    """打印导入模式选择菜单"""
    console.print("\n[yellow]请选择导入模式:[/yellow]")
    console.print("  [1] 自动扫描并导入 data/import_emoji 文件夹中的所有 .mmipkg 文件")
    console.print("  [2] 手动指定文件路径导入")


def interactive_export():
    """交互式导出"""
    console.print("\n[cyan]" + "-" * 70 + "[/cyan]")
    console.print("[bold]导出表情包到 .mmipkg 文件[/bold]")
    console.print("[cyan]" + "-" * 70 + "[/cyan]")

    # 检查数据库
    try:
        if db.is_closed():
            db.connect()

        emoji_count = Emoji.select().where(Emoji.is_registered).count()
        console.print(f"\n[green]✓ 找到 {emoji_count} 个已注册的表情包[/green]")

        if emoji_count == 0:
            console.print("[red]✗ 数据库中没有已注册的表情包，无法导出[/red]")
            return False
    except Exception as e:
        console.print(f"[red]✗ 数据库连接失败: {e}[/red]")
        return False
    finally:
        if not db.is_closed():
            db.close()

    # 获取输出文件路径
    console.print("\n[yellow]1. 输出文件设置[/yellow]")
    default_filename = f"maibot_emojis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mmipkg"
    output_path = get_input("  输出文件路径", default_filename)

    # 确保有 .mmipkg 扩展名
    if not output_path.endswith(".mmipkg"):
        output_path += ".mmipkg"

    # 获取包名称
    default_pack_name = f"MaiBot表情包_{datetime.now().strftime('%Y%m%d')}"
    pack_name = get_input("  包名称", default_pack_name)

    # 自定义 manifest
    console.print("\n[yellow]2. 包信息设置（可选）[/yellow]")
    if get_yes_no("  是否添加包的作者和介绍信息", False):
        custom_manifest = {"author": author} if (author := input("  作者名称（可选）: ").strip()) else {}

        # 介绍信息
        console.print("  包介绍（限制 100 字以内）:")
        if description := input("  > ").strip():
            if len(description) > 100:
                console.print(f"    [yellow]⚠ 介绍过长（{len(description)} 字），已截断至 100 字[/yellow]")
                description = description[:100]
            custom_manifest["description"] = description

        if not custom_manifest:
            custom_manifest = None
        else:
            console.print("    [green]✓ 已添加包信息[/green]")
    else:
        custom_manifest = None

    # 压缩设置
    console.print("\n[yellow]3. 压缩设置[/yellow]")
    use_compression = get_yes_no("  使用 Zstd 压缩", True)

    zstd_level = 3
    if use_compression:
        print_compression_level_info()
        zstd_level = get_int("  选择压缩级别", 3, 1, 22)

    # 重新编码设置
    console.print("\n[yellow]4. 图片编码设置[/yellow]")
    if get_yes_no("  是否重新编码图片（可显著减小文件大小）", False):
        console.print("\n  [cyan]可用格式:[/cyan]")
        console.print("    webp: 推荐，体积小且支持透明度")
        console.print("    jpeg: 最小体积，但不支持透明度")
        console.print("    png:  无损，文件较大")
        reencode = get_input("  选择格式", "webp", ["webp", "jpeg", "png"])

        quality = get_int("  编码质量", 80, 1, 100) if reencode in ("webp", "jpeg") else 80
    else:
        reencode = None
        quality = 80

    # 确认导出
    console.print("\n[cyan]" + "-" * 70 + "[/cyan]")
    console.print("[bold]导出配置:[/bold]")
    console.print(f"  输出文件: {output_path}")
    console.print(f"  包名称: {pack_name or '自动生成'}")
    if custom_manifest:
        if "author" in custom_manifest:
            console.print(f"  作者: {custom_manifest['author']}")
        if "description" in custom_manifest:
            console.print(f"  介绍: {custom_manifest['description']}")
    compression_info = f"是 (级别 {zstd_level})" if use_compression else "否"
    console.print(f"  压缩: {compression_info}")
    console.print(f"  重新编码: {reencode or '否'}")
    if reencode:
        console.print(f"  编码质量: {quality}")
    console.print(f"  表情包数量: {emoji_count}")
    console.print("[cyan]" + "-" * 70 + "[/cyan]")

    if not get_yes_no("\n确认导出", True):
        console.print("[red]✗ 已取消导出[/red]")
        return False

    # 开始导出
    console.print("\n[cyan]开始导出...[/cyan]")
    packer = MMIPKGPacker(
        use_compression=use_compression, zstd_level=zstd_level, reencode=reencode, reencode_quality=quality
    )

    success = packer.pack_from_db(output_path, pack_name, custom_manifest)

    if success:
        console.print(f"\n[green]✓ 导出成功: {output_path}[/green]")
    else:
        console.print("\n[red]✗ 导出失败[/red]")

    return success


def interactive_import():
    """交互式导入"""
    console.print("\n[cyan]" + "-" * 70 + "[/cyan]")
    console.print("[bold]从 .mmipkg 文件导入表情包[/bold]")
    console.print("[cyan]" + "-" * 70 + "[/cyan]")

    # 选择导入模式
    print_import_mode_selection()
    import_mode = get_input("请选择", "1", ["1", "2"])

    input_files = []

    if import_mode == "1":
        # 自动扫描模式
        import_dir = os.path.join(PROJECT_ROOT, "data", "import_emoji")
        os.makedirs(import_dir, exist_ok=True)

        console.print(f"\n[cyan]扫描目录: {import_dir}[/cyan]")

        # 查找所有 .mmipkg 文件
        for file in os.listdir(import_dir):
            if file.endswith(".mmipkg"):
                file_path = os.path.join(import_dir, file)
                if os.path.isfile(file_path):
                    input_files.append(file_path)

        if not input_files:
            console.print("[red]✗ 目录中没有找到 .mmipkg 文件[/red]")
            console.print(f"  请将表情包文件放入: {import_dir}")
            return False

        console.print(f"\n[green]找到 {len(input_files)} 个文件:[/green]")
        for i, file_path in enumerate(input_files, 1):
            file_size = os.path.getsize(file_path) / 1024 / 1024
            console.print(f"  [{i}] {os.path.basename(file_path)} ({file_size:.2f} MB)")

        if not get_yes_no(f"\n确认导入这 {len(input_files)} 个文件", True):
            console.print("[red]✗ 已取消导入[/red]")
            return False
    else:
        # 手动输入模式
        console.print("\n[yellow]1. 输入文件设置[/yellow]")
        input_path = get_input("  输入文件路径 (.mmipkg)")

        if not os.path.exists(input_path):
            console.print(f"[red]✗ 文件不存在: {input_path}[/red]")
            return False

        input_files.append(input_path)

    # 获取输出目录
    console.print("\n[yellow]2. 输出目录设置[/yellow]")
    default_output_dir = os.path.join(PROJECT_ROOT, "data", "emoji_registed")
    output_dir = get_input("  输出目录", default_output_dir)

    # 导入选项
    console.print("\n[yellow]3. 导入选项[/yellow]")
    replace_existing = get_yes_no("  替换已存在的表情包", False)
    verify_sha = get_yes_no("  验证 SHA256 完整性（推荐）", True)

    # 批量大小
    console.print("\n[yellow]4. 性能设置[/yellow]")
    console.print("  [cyan]批量大小说明:[/cyan]")
    console.print("    100-500:  默认，适合大多数情况")
    console.print("    500-1000: 快速导入大量表情包")
    console.print("    1000+:    极速模式，但内存占用更高")
    batch_size = get_int("  批量提交大小", 500, 100, 5000)

    # 确认导入
    console.print("\n[cyan]" + "-" * 70 + "[/cyan]")
    console.print("[bold]导入配置:[/bold]")
    console.print(f"  导入模式: {'自动扫描' if import_mode == '1' else '手动指定'}")
    console.print(f"  文件数量: {len(input_files)}")
    console.print(f"  输出目录: {output_dir}")
    console.print(f"  替换已存在: {'是' if replace_existing else '否'}")
    console.print(f"  SHA256 验证: {'是' if verify_sha else '否'}")
    console.print(f"  批量大小: {batch_size}")
    console.print("[cyan]" + "-" * 70 + "[/cyan]")

    if not get_yes_no("\n确认导入", True):
        console.print("[red]✗ 已取消导入[/red]")
        return False

    # 开始导入
    unpacker = MMIPKGUnpacker(verify_sha=verify_sha)

    total_success = 0
    total_failed = 0

    # 使用进度条处理多个文件
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]导入文件...", total=len(input_files))

        for i, input_path in enumerate(input_files, 1):
            progress.update(task, description=f"[cyan]导入 [{i}/{len(input_files)}]: {os.path.basename(input_path)}")

            console.print(f"\n[bold]{'=' * 70}[/bold]")
            console.print(f"[bold]导入文件 [{i}/{len(input_files)}]: {os.path.basename(input_path)}[/bold]")
            console.print(f"[bold]{'=' * 70}[/bold]")

            success = unpacker.import_to_db(
                input_path, output_dir=output_dir, replace_existing=replace_existing, batch_size=batch_size
            )

            if success:
                total_success += 1
            else:
                total_failed += 1

            progress.advance(task)

    # 总结
    console.print(f"\n[bold]{'=' * 70}[/bold]")
    console.print("[bold]导入总结:[/bold]")
    console.print(f"  [green]成功: {total_success} 个文件[/green]")
    if total_failed > 0:
        console.print(f"  [red]失败: {total_failed} 个文件[/red]")
    console.print(f"[bold]{'=' * 70}[/bold]")

    return total_failed == 0


def main():
    """主函数 - 交互式界面"""
    print_header()

    try:
        while True:
            print_menu()
            try:
                choice = get_input("请选择", "1", ["0", "1", "2"])
            except KeyboardInterrupt:
                console.print("\n[green]再见！[/green]")
                return 0

            if choice == "0":
                console.print("\n[green]再见！[/green]")
                return 0

            elif choice == "1":
                try:
                    interactive_export()
                except KeyboardInterrupt:
                    console.print("\n\n[yellow]✗ 操作已取消[/yellow]")
                except Exception as e:
                    console.print(f"\n[red]✗ 发生错误: {e}[/red]")
                    import traceback

                    traceback.print_exc()

                try:
                    input("\n按 Enter 键继续...")
                except (KeyboardInterrupt, EOFError):
                    pass

            elif choice == "2":
                try:
                    interactive_import()
                except KeyboardInterrupt:
                    console.print("\n\n[yellow]✗ 操作已取消[/yellow]")
                except Exception as e:
                    console.print(f"\n[red]✗ 发生错误: {e}[/red]")
                    import traceback

                    traceback.print_exc()

                try:
                    input("\n按 Enter 键继续...")
                except (KeyboardInterrupt, EOFError):
                    pass
    except KeyboardInterrupt:
        console.print("\n[green]再见！[/green]")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
