"""Git 信息与下载源的公共接口；Runner 可用，只依赖标准库。

调用方分别选择信息源和下载源，本组件不轮询、不自动切换源。
"""

from dataclasses import dataclass
from typing import Any, Dict
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import hashlib
import json
import re


def cnb_repository_name(owner: str, repo: str) -> str:
    """与登记仓库的 CNB 同步规则一致，主程序使用已有的固定镜像。"""
    if not all(re.fullmatch(r"[\w.-]+", part, flags=re.ASCII) and part not in {".", ".."} for part in (owner, repo)):
        raise ValueError("无效的 GitHub 仓库路径")
    source = f"{owner.lower()}/{repo.lower().removesuffix('.git')}"
    if source in {"mai-with-u/plugin-repo", "mai-with-u/maibot"}:
        return source.split("/")[1]
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    readable = re.sub(r"[^a-z0-9-]", "-", source.replace("/", "--"))[:46]
    return f"p-{readable}-{digest}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class GitSource:
    id: str
    name: str
    clone_prefix: str
    raw_prefix: str

    @classmethod
    def select(cls, source_id: str, custom_prefix: str = "") -> "GitSource":
        if source_id == "github":
            return cls(source_id, "GitHub 官方源", "https://github.com", "https://raw.githubusercontent.com")
        if source_id == "cnb":
            return cls(source_id, "CNB 镜像", "https://cnb.cool/Mai-with-u", "https://cnb.cool/Mai-with-u")
        if source_id == "custom":
            prefix = custom_prefix.strip().rstrip("/")
            parsed = urlsplit(prefix)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("自定义源必须是无认证信息、查询参数和片段的 HTTPS 前缀")
            return cls(source_id, "自定义源", prefix, prefix)
        raise ValueError(f"不支持的 Git 源: {source_id}")

    def clone_url(self, owner: str, repo: str) -> str:
        path = cnb_repository_name(owner, repo) if self.id == "cnb" else f"{owner}/{repo}"
        return f"{self.clone_prefix}/{path}.git"

    def raw_url(self, owner: str, repo: str, branch: str, file_path: str) -> str:
        if any(part in {".", ".."} for part in file_path.split("/")):
            raise ValueError("无效的文件路径")
        path = f"{cnb_repository_name(owner, repo)}/-/git/raw" if self.id == "cnb" else f"{owner}/{repo}"
        return f"{self.raw_prefix}/{path}/{quote(branch, safe='')}/{quote(file_path, safe='/')}"

    def api_url(self, owner: str, repo: str, path: str) -> str:
        if self.id != "github":
            raise ValueError("仓库元数据 API 仅支持 GitHub 信息源")
        return f"https://api.github.com/repos/{owner}/{repo}/{path}"

    @staticmethod
    def read_text(url: str, timeout: float = 20, ssl_context=None) -> str:
        """同一 HTTP 入口，保留系统代理且禁止重定向绕过调用方的 URL 校验。"""
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "MaiBot"})
        opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl_context))
        with opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    @classmethod
    def read_json(cls, url: str, timeout: float = 20, ssl_context=None) -> Dict[str, Any]:
        payload = json.loads(cls.read_text(url, timeout, ssl_context))
        if not isinstance(payload, dict):
            raise ValueError("API 响应不是对象")
        return payload
