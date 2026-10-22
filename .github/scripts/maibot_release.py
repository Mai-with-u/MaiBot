"""主程序发布检查：构建产物在验证、重试和上传阶段保持相同字节。"""

from email.parser import BytesParser
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from packaging.requirements import Requirement
from packaging.version import Version

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
import tomllib
import zipfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pypi_files(name: str, version: str) -> Dict[str, str]:
    request = Request(f"https://pypi.org/pypi/{name}/{version}/json", headers={"User-Agent": "MaiBot-release"})
    try:
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    return {file["filename"]: file["digests"]["sha256"] for file in data["urls"]}


def release_notes(changelog: str, version: str) -> str:
    match = re.search(rf"^# \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^# \[|\Z)", changelog, re.M | re.S)
    if match is None or not match["body"].strip():
        raise ValueError(f"changelog.md 中缺少 {version} 的发布说明")
    return match["body"].strip() + "\n"


def prepare(source: Path, destination: Path, version: str) -> None:
    parsed = Version(version)
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None or str(parsed) != version:
        raise ValueError("此流程只发布规范的正式版本号")
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"].lower() != "maibot" or project["version"] != version:
        raise ValueError("输入版本号必须与主程序 pyproject.toml 一致")
    dashboard = next(Requirement(item) for item in project["dependencies"] if Requirement(item).name == "maibot-dashboard")
    specs = list(dashboard.specifier)
    if len(specs) != 1 or specs[0].operator != "==" or Version(specs[0].version).is_prerelease:
        raise ValueError("请先运行 Prepare Main Release，固定已发布的 Dashboard 正式版本")
    if not pypi_files("maibot-dashboard", specs[0].version):
        raise ValueError("主程序依赖的 Dashboard 正式版本尚未发布到 PyPI")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"version": version, "tag": f"v{version}", "commit": commit, "files": {}}
    (destination / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    notes = release_notes((source / "changelogs/changelog.md").read_text(encoding="utf-8"), version)
    (destination / "notes.md").write_text(notes, encoding="utf-8")


def audit_names(names: List[str], prefix: str = "") -> None:
    required = {"src/runtime.py", "src/cli/tool.py", "src/_resources/pyproject.toml", "src/_resources/EULA.md",
                "src/_resources/PRIVACY.md", "src/A_memorix/config_schema.json", "src/plugins/built_in/plugin_management/_manifest.json"}
    paths = {name.removeprefix(prefix) for name in names}
    for name in paths:
        parts = Path(name).parts
        if "__pycache__" in parts or ".." in parts or name.startswith(("config/", "data/", "logs/", "temp/", "plugins/", "src/data/")):
            raise ValueError(f"构建产物包含非发布文件: {name}")
    if not prefix:
        if not required.issubset(paths):
            raise ValueError(f"wheel 缺少运行资源: {sorted(required - paths)}")
        for directory in ("prompts/zh-CN", "prompts/en-US", "prompts/ja-JP", "locales/zh-CN"):
            if not any(name.startswith(f"src/_resources/{directory}/") for name in paths):
                raise ValueError(f"wheel 缺少资源目录: {directory}")


def read_manifest(directory: Path) -> Dict[str, Any]:
    manifest = json.loads((directory / "release.json").read_text(encoding="utf-8"))
    if re.fullmatch(r"[0-9a-f]{40}", manifest["commit"]) is None or manifest["tag"] != f"v{manifest['version']}":
        raise ValueError("发布清单中的提交或 tag 无效")
    return manifest


def distribution_files(directory: Path) -> List[Path]:
    files = list((directory / "dist").iterdir())
    # uv 生成 .gitignore；PyPA 上传时生成对应发行文件的签名证明，它们不属于包清单。
    attestations = {
        f"{path.name}.publish.attestation"
        for path in files
        if path.name.endswith((".whl", ".tar.gz"))
    }
    return sorted(path for path in files if path.name != ".gitignore" and path.name not in attestations)


def audit(directory: Path) -> None:
    manifest = read_manifest(directory)
    distributions = distribution_files(directory)
    if len(distributions) != 2:
        raise ValueError("必须恰好包含一个 wheel 和一个源码包")
    wheel = next(path for path in distributions if path.suffix == ".whl")
    sdist = next(path for path in distributions if path.name.endswith(".tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        audit_names(archive.namelist())
        metadata_path = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        if metadata["Name"].lower() != "maibot" or metadata["Version"] != manifest["version"]:
            raise ValueError("wheel 版本与发布清单不一致")
    with tarfile.open(sdist) as archive:
        audit_names(archive.getnames(), prefix=archive.getnames()[0].split("/")[0] + "/")
    manifest["files"] = {path.name: digest(path) for path in distributions}
    (directory / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify(directory: Path) -> Dict[str, Any]:
    manifest = read_manifest(directory)
    actual = {path.name: digest(path) for path in distribution_files(directory)}
    if len(actual) != 2 or actual != manifest["files"]:
        raise ValueError("发布产物校验和不一致；禁止重新构建后覆盖已上传的版本")
    return manifest


def compare_pypi(local: Dict[str, str], remote: Dict[str, str], complete: bool = False) -> None:
    for filename, checksum in remote.items():
        if local.get(filename) != checksum:
            raise ValueError(f"PyPI 已存在不同内容的文件: {filename}；禁止覆盖该版本")
    if complete and remote != local:
        raise ValueError("PyPI 尚未包含全部发布文件")


def check_pypi(directory: Path, complete: bool) -> None:
    manifest = verify(directory)
    # 上传后 PyPI 查询可能延迟可见；立即查询一次，再每 10 秒查询，最多等待 3 分钟。
    for attempt in range(19 if complete else 1):
        remote = pypi_files("maibot", manifest["version"])
        compare_pypi(manifest["files"], remote)
        if not complete or remote == manifest["files"]:
            return
        if attempt < 18:
            time.sleep(10)
    compare_pypi(manifest["files"], remote, complete=True)


def smoke(directory: Path) -> None:
    verify(directory)
    work = Path(os.environ["RUNNER_TEMP"]) / "maibot-smoke"
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("MAIBOT_INSTANCE_DIR", None)
    env.pop("MAIBOT_INITIALIZE_ONLY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["UV_TOOL_DIR"] = str(work / "tools")
    env["UV_TOOL_BIN_DIR"] = str(work / "bin")
    wheel = next((directory / "dist").glob("*.whl")).resolve()
    subprocess.run(["uv", "tool", "install", "--python", "3.13", str(wheel)], cwd=work, env=env, check=True)
    executable = work / "bin" / ("maibot.exe" if os.name == "nt" else "maibot")
    instance = work / "instance"
    for args in (["--help"], ["--version"], ["init", "-d", str(instance)]):
        subprocess.run([str(executable), *args], cwd=work, env=env, check=True, timeout=60)
    config_files = sorted((instance / "config").glob("*.toml"))
    if {path.name for path in config_files} != {"bot_config.toml", "model_config.toml"}:
        raise ValueError("init 未生成预期配置")
    original = {path.name: digest(path) for path in config_files}
    subprocess.run([str(executable), "init", "-d", str(instance)], cwd=work, env=env, check=True, timeout=60)
    if original != {path.name: digest(path) for path in config_files} or (instance / "data" / "MaiBot.db").exists():
        raise ValueError("重复初始化修改了配置或创建了数据库")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "audit", "verify", "smoke", "pypi-check", "pypi-verify"))
    parser.add_argument("--source", type=Path, default=Path("source"))
    parser.add_argument("--directory", type=Path, default=Path("release-dist"))
    parser.add_argument("--version")
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.version:
            parser.error("prepare 需要 --version")
        prepare(args.source, args.directory, args.version)
    elif args.command == "audit":
        audit(args.directory)
    elif args.command == "smoke":
        smoke(args.directory)
    elif args.command.startswith("pypi-"):
        check_pypi(args.directory, complete=args.command == "pypi-verify")
    else:
        manifest = verify(args.directory)
        if args.version and manifest["version"] != args.version:
            raise ValueError("复用产物的版本与本次输入不一致")
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                for key in ("version", "tag", "commit"):
                    output.write(f"{key}={manifest[key]}\n")


if __name__ == "__main__":
    main()
