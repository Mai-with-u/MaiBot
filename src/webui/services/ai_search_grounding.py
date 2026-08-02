"""WebUI AI 搜索回答的技术项提取与证据校验。"""

from typing import List, Protocol
import re


class GroundableOutput(Protocol):
    """证据校验所需的最小回答结构。"""

    answer: str
    suggestions: List[str]


class AISearchGroundingError(ValueError):
    """AI 回答包含未被检索资料支持的可验证技术项。"""


def normalize_verifiable_claim(claim: str) -> str:
    """规范化模型在 JSON Markdown 中遗留的转义引号，不改动路径等其他字符。"""

    normalized_claim = re.sub(r"""\\(["'])""", r"\1", claim.strip())
    while (
        len(normalized_claim) >= 2
        and normalized_claim[0] == normalized_claim[-1]
        and normalized_claim[0] in {'"', "'"}
    ):
        normalized_claim = normalized_claim[1:-1].strip()
    return normalized_claim


def extract_verifiable_claims(text: str) -> List[str]:
    """提取配置项、路径、命令参数等应能在已读资料中找到的技术内容。"""

    claims: List[str] = []
    for match in re.finditer(r"(?<!`)`([^`\n]+)`(?!`)", text):
        claim = normalize_verifiable_claim(match.group(1))
        if claim and re.search(r"[_./\[\]=]|\d|^--", claim) and claim not in claims:
            claims.append(claim)

    patterns = (
        r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b",
        r"\b([\w.-]+\.toml)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = normalize_verifiable_claim(match.group(1))
            if claim and claim not in claims:
                claims.append(claim)
    return claims


def validate_model_output_evidence(model_output: GroundableOutput, evidence: str) -> None:
    """拒绝展示本次检索或读取证据中未出现的配置项、路径、命令或数值代码。"""

    normalized_evidence = evidence.casefold()
    unsupported_claims: List[str] = []
    for content in [model_output.answer, *model_output.suggestions]:
        for claim in extract_verifiable_claims(content):
            normalized_claim = claim.casefold()
            is_supported = _is_claim_supported_by_evidence(normalized_claim, normalized_evidence)
            if not is_supported and claim not in unsupported_claims:
                unsupported_claims.append(claim)

    if unsupported_claims:
        claims = "、".join(f"`{claim}`" for claim in unsupported_claims[:6])
        raise AISearchGroundingError(
            f"回答包含未在本次 AI 获取到的检索或读取证据中找到依据的技术项：{claims}；"
            "这不代表它们在整个项目中一定不存在"
        )


def _is_claim_supported_by_evidence(claim: str, evidence: str) -> bool:
    """按通配符、赋值和 HTTP 方法拆分技术声明，避免只做整段字符串匹配。"""

    if claim in evidence:
        return True
    if claim.endswith("*") and claim.count("*") == 1:
        return claim[:-1] in evidence

    assignment_match = re.fullmatch(r"(.+?)\s*=\s*(.+)", claim)
    if assignment_match:
        field_name = assignment_match.group(1).strip()
        field_value = assignment_match.group(2).strip()
        return _is_config_field_supported(field_name, evidence) and field_value in evidence

    http_match = re.fullmatch(r"(get|post|put|patch|delete)\s+(\S+)", claim)
    if http_match:
        method, path = http_match.groups()
        return method in evidence and path in evidence

    return _is_config_field_supported(claim, evidence)


def _is_config_field_supported(field_name: str, evidence: str) -> bool:
    """兼容 TOML 的 `[section]` 与 `section.field` 两种等价字段表示。"""

    if field_name in evidence:
        return True
    if "." not in field_name:
        return False
    section, _, leaf_name = field_name.rpartition(".")
    return f"[{section}]" in evidence and leaf_name in evidence
