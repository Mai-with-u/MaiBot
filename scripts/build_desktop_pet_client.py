"""从共用桌宠源码构建轻量独立 wheel 与 Windows 启动 ZIP。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List
from zipfile import ZIP_DEFLATED, ZipFile
import argparse
import ast
import hashlib
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "__init__", "remote_client", "pet_window", "screen", "app_conditions",
    "attachments", "clipboard", "todos",
)


def copy_verified(source: Path, target: Path) -> None:
    """按字节复制，避免换行或编码变化污染独立客户端。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if source.read_bytes() != target.read_bytes():
        raise RuntimeError(f"复制校验失败: {source}")


def stage_client(stage: Path) -> str:
    """只打包独立桌宠所需模块，禁止引入主程序或遗漏相对导入。"""
    metadata_dir = ROOT / "desktop-pet-client"
    metadata = tomllib.loads((metadata_dir / "pyproject.toml").read_text("utf-8"))
    dependencies = metadata["project"]["dependencies"]
    requirements = [line.strip() for line in (metadata_dir / "requirements.txt").read_text("utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    if dependencies != requirements:
        raise ValueError("客户端 requirements.txt 必须与 pyproject.toml 的依赖一致")
    for name in ("pyproject.toml", "requirements.txt", "README.md", "start.cmd"):
        copy_verified(metadata_dir / name, stage / name)
    source_dir = ROOT / "src" / "desktop_pet"
    package_dir = stage / "maibot_desktop_pet"
    for name in MODULES:
        source = source_dir / f"{name}.py"
        module = ast.parse(source.read_text("utf-8-sig"), filename=str(source))
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if imported == "src" or imported.startswith("src.") or node.level > 1:
                    raise ValueError(f"独立客户端不能导入主程序: {source}:{node.lineno}")
                if node.level == 1 and imported.split(".")[0] not in MODULES:
                    raise ValueError(f"独立客户端缺少模块 {imported}: {source}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name == "src" or alias.name.startswith("src.") for alias in node.names):
                    raise ValueError(f"独立客户端不能导入主程序: {source}:{node.lineno}")
        copy_verified(source, package_dir / source.name)
    for source in (source_dir / "web").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts:
            copy_verified(source, package_dir / "web" / source.relative_to(source_dir / "web"))
    copy_verified(source_dir / "LICENSE", package_dir / "LICENSE")
    copy_verified(source_dir / "LICENSE", stage / "LICENSE")
    return metadata["project"]["version"]


def build_executable(stage: Path, output_dir: Path, version: str, uv: str) -> Path:
    """在隔离依赖环境构建 Windows 文件夹版，包含 Python 运行时。"""
    if sys.platform != "win32":
        raise RuntimeError("Windows 可执行客户端必须在 Windows 上构建")
    launcher = stage / "launch.py"
    launcher.write_text("from maibot_desktop_pet.remote_client import main\n\nif __name__ == '__main__':\n    main()\n",
                        encoding="utf-8")
    bundle_dir = stage.parent / "executable"
    subprocess.run([
        uv, "run", "--project", str(stage), "--python", "3.12", "--with", "pyinstaller>=6.11,<7",
        "python", "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--log-level", "WARN",
        "--name", "MaiBotDesktopPet",
        "--distpath", str(bundle_dir), "--workpath", str(stage.parent / "pyinstaller-work"),
        "--specpath", str(stage.parent), "--collect-data", "maibot_desktop_pet", "--collect-data", "webview",
        "--hidden-import", "webview.platforms.winforms", "--recursive-copy-metadata", "maibot-desktop-pet-client",
        str(launcher),
    ], check=True, cwd=stage)
    application = bundle_dir / "MaiBotDesktopPet"
    if not (application / "MaiBotDesktopPet.exe").is_file():
        raise RuntimeError("未生成 Windows 桌宠可执行文件")
    copy_verified(stage / "README.md", application / "README.md")
    copy_verified(stage / "LICENSE", application / "LICENSE")
    archive = output_dir / f"maibot-desktop-pet-client-{version}-windows-standalone.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
        for source in sorted(application.rglob("*")):
            if source.is_file():
                zipped.write(source, Path(application.name, source.relative_to(application)).as_posix())
    return archive


def build(output_dir: Path, *, executable: bool = False) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="maibot-pet-build-") as temporary:
        stage = Path(temporary) / "client"
        version = stage_client(stage)
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("请先安装 uv 后构建桌宠客户端")
        subprocess.run([uv, "build", "--wheel", "--out-dir", str(output_dir), str(stage)], check=True, cwd=stage)
        wheel = output_dir / f"maibot_desktop_pet_client-{version}-py3-none-any.whl"
        if not wheel.is_file():
            raise RuntimeError("构建未生成预期的客户端 wheel")
        archive = output_dir / f"maibot-desktop-pet-client-{version}-windows.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
            for source in sorted(stage.rglob("*")):
                if source.is_file() and not any(part.endswith(".egg-info") or part in {"build", "__pycache__"}
                                                for part in source.relative_to(stage).parts):
                    zipped.write(source, source.relative_to(stage).as_posix())
        artifacts = [wheel, archive]
        if executable:
            artifacts.append(build_executable(stage, output_dir, version, uv))
        checksums = output_dir / "SHA256SUMS.txt"
        checksums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                                     for path in artifacts), encoding="utf-8")
        return [*artifacts, checksums]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "desktop-pet-client")
    parser.add_argument("--executable", action="store_true", help="额外生成包含 Python 的 Windows 可执行客户端 ZIP")
    args = parser.parse_args()
    for artifact in build(args.output_dir.resolve(), executable=args.executable):
        print(artifact)


if __name__ == "__main__":
    main()
