"""独立构建桌宠后，把经过校验的安装包附加到运行开始时锁定的 Release。"""

from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True).strip()


def api(path: str) -> Dict[str, Any]:
    return json.loads(gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}"))


def tag_commit(tag: str) -> str:
    obj = api(f"git/ref/tags/{quote(tag, safe='')}")["object"]
    for _ in range(10):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            raise ValueError("Release tag 没有指向提交")
        obj = api(f"git/tags/{obj['sha']}")["object"]
    raise ValueError("Release tag 嵌套过深")


def resolve() -> None:
    release = api("releases/latest")
    if release["draft"] or release["prerelease"]:
        raise ValueError("需要已发布的正式 Release")
    tag = release["tag_name"]
    if not tag or any(char in tag for char in "\r\n"):
        raise ValueError("Release tag 无效")
    commit = tag_commit(tag)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"ready=true\ntag={tag}\ncommit={commit}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"目标 Release：[{tag}]({release['html_url']})\n\n源码提交：`{commit}`\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(source: Path, output: Path, platform: str) -> None:
    if platform == "windows":
        version = json.loads((source / "pet-clients/desktop/package.json").read_text(encoding="utf-8"))["version"]
        name = f"maibot-desktop-pet-{version}-win-x64-setup.exe"
        package = source / "dist/desktop-pet-client" / name
    elif platform == "android":
        gradle = (source / "pet-clients/android/app/build.gradle").read_text(encoding="utf-8")
        matches = re.findall(r"^\s*versionName\s+['\"]([^'\"]+)['\"]", gradle, re.M)
        if len(matches) != 1:
            raise ValueError("Android versionName 必须唯一")
        version = matches[0]
        name = f"maibot-android-pet-{version}.apk"
        package = source / "pet-clients/android/app/build/outputs/apk/release/app-release.apk"
    else:
        raise ValueError("未知平台")
    if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version) is None:
        raise ValueError("客户端版本号无效")
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    shutil.copyfile(package, target)
    (output / f"{name}.sha256").write_text(f"{digest(target)}  {name}\n", encoding="utf-8")


def verified_packages(output: Path) -> List[Path]:
    packages = sorted(path for path in output.iterdir() if path.suffix in {".exe", ".apk"})
    if not packages:
        raise ValueError("没有成功构建的桌宠安装包")
    files = []
    for package in packages:
        if not re.fullmatch(r"maibot-(?:desktop-pet-.*-win-x64-setup\.exe|android-pet-.*\.apk)", package.name):
            raise ValueError(f"非桌宠安装包：{package.name}")
        checksum = output / f"{package.name}.sha256"
        if checksum.read_text(encoding="utf-8").strip() != f"{digest(package)}  {package.name}":
            raise ValueError(f"校验和不符：{package.name}")
        files.extend([package, checksum])
    return files


def attach(output: Path) -> None:
    tag = os.environ["RELEASE_TAG"]
    release = api(f"releases/tags/{quote(tag, safe='')}")
    if release["draft"] or release["prerelease"] or tag_commit(tag) != os.environ["SOURCE_COMMIT"]:
        raise ValueError("目标 Release 状态或 tag 提交已变化，停止上传")
    files = verified_packages(output)
    # 先核对所有同名附件，发现不同内容时整批停止，不覆盖已发布的安装包。
    existing = json.loads(
        gh(
            "api",
            "--paginate",
            "--slurp",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/{release['id']}/assets?per_page=100",
        )
    )
    names = {asset["name"] for page in existing for asset in page}
    pending = []
    with tempfile.TemporaryDirectory() as directory:
        for path in files:
            if path.name in names:
                gh(
                    "release",
                    "download",
                    tag,
                    "--repo",
                    os.environ["GITHUB_REPOSITORY"],
                    "--pattern",
                    path.name,
                    "--dir",
                    directory,
                )
                if digest(Path(directory) / path.name) != digest(path):
                    raise ValueError(f"Release 已有不同内容的同名附件：{path.name}；请提升客户端版本号")
            else:
                pending.append(str(path))
    if pending:
        gh("release", "upload", tag, *pending, "--repo", os.environ["GITHUB_REPOSITORY"])
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"已挂到 [{tag}]({release['html_url']})：\n\n")
        for path in files:
            summary.write(f"- `{path.name}`\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["resolve", "collect", "attach"])
    parser.add_argument("--source", type=Path, default=Path("source"))
    parser.add_argument("--output", type=Path, default=Path("packages"))
    args = parser.parse_args()
    if args.action == "resolve":
        resolve()
    elif args.action == "collect":
        collect(args.source, args.output, os.environ["PET_PLATFORM"])
    else:
        attach(args.output)
