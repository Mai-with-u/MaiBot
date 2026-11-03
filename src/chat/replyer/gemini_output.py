"""兼容 Gemini 混入可见正文的显式思考格式。"""

import re


class GeminiReplyFormatError(ValueError):
    """已识别分析输出，但无法可靠提取正式回复。"""


_HEADER = r"(?:thinking\s+process|thought\s+process|思考过程|思维链)"
_ANGLE_START = re.compile(rf"⌊\s*{_HEADER}\s*(?:⌉|[:：])", re.IGNORECASE)
_PLAIN_START = re.compile(rf"(?:#{{1,6}}\s+)?(?:\*\*)?{_HEADER}(?:\*\*)?\s*[:：]", re.IGNORECASE)
_XML_START = re.compile(r"<\s*(?:[:_~-]\s*)?(think|thinking|thought|analysis)\s*>", re.IGNORECASE)
_XML_TAG = re.compile(r"</?\s*(?:[:_~-]\s*)?(think|thinking|thought|analysis)\s*>", re.IGNORECASE)

# 内联 thought 定界块：纯字面量正则，零回溯写法。
_INLINE_BRACKET_START = re.compile(r"^\[\s*(?:[:_~-]\s*)?thought(?![A-Za-z])", re.IGNORECASE)
_INLINE_BRACKET_END = re.compile(r"(?:[:_~-]\s*\]|\]\s*[:_~-])")
_INLINE_FLOOR_START = re.compile(r"^(?!⌊[^⌊⌋]*⌉)⌊\s*(?:[:_~-]\s*)?thought(?![A-Za-z])(?!\s+process\b)", re.IGNORECASE)
_INLINE_FLOOR_END = re.compile(r"⌋")
_INLINE_CN_START = re.compile(r"^【\s*(?:[:_~-]\s*)?thought(?![A-Za-z])", re.IGNORECASE)
_INLINE_CN_END = re.compile(r"(?:[:_~-]\s*】|】\s*[:_~-])")

_INLINE_STARTS = (
    (_INLINE_BRACKET_START, _INLINE_BRACKET_END),
    (_INLINE_FLOOR_START, _INLINE_FLOOR_END),
    (_INLINE_CN_START, _INLINE_CN_END),
)


def _split_inline_thought_block(text: str) -> str | None:
    """命中内联 thought 开标签时，返回闭合定界符之后的正文。

    Args:
        text: 已 strip 的待检查文本。

    Returns:
        Optional[str]: 命中且闭合时返回剩余正文；未命中返回 None。

    Raises:
        GeminiReplyFormatError: 命中开标签但缺少闭合定界符。
    """
    for start_re, end_re in _INLINE_STARTS:
        start = start_re.match(text)
        if not start:
            continue
        body = text[start.end() :]
        end = end_re.search(body)
        if end is None:
            raise GeminiReplyFormatError("Gemini 思考标签未闭合，已阻止发送。")
        return body[end.end() :]
    return None


def normalize_gemini_reply(text: str) -> str:
    """只剥离开头有明确边界的思考块；不猜测草稿中的哪一句是正文。

    引用、代码示例和正文中提到思考的普通文本不触发。角括号格式只接受
    独立行的正文分界，避免误把分析里引用的 `⌊...⌉` 当成结束标记。

    Args:
        text: 模型返回的原始文本。

    Returns:
        str: 剥离思考块后的正文。

    Raises:
        GeminiReplyFormatError: 思考块未闭合或配对错误，无法可靠提取正文。
    """
    remaining = text.strip()
    changed = False
    while remaining:
        angle = _ANGLE_START.match(remaining)
        xml = _XML_START.match(remaining)
        if angle:
            boundary = re.search(r"(?m)^\s*⌈", remaining[angle.end() :])
            if boundary is None:
                raise GeminiReplyFormatError("Gemini 思考块缺少明确正文分界，已阻止发送。")
            remaining = remaining[angle.end() + boundary.end() :].strip()
        elif xml:
            # 配对扫描处理连续、嵌套块，未闭合时绝不把分析当正文。
            stack = []
            end = None
            for tag in _XML_TAG.finditer(remaining):
                name = tag.group(1).lower()
                if tag.group().startswith("</"):
                    if not stack or stack.pop() != name:
                        raise GeminiReplyFormatError("Gemini 思考标签配对错误，已阻止发送。")
                    if not stack:
                        end = tag.end()
                        break
                else:
                    stack.append(name)
            if end is None:
                raise GeminiReplyFormatError("Gemini 思考标签未闭合，已阻止发送。")
            remaining = remaining[end:].strip()
        else:
            inline_body = _split_inline_thought_block(remaining)
            if inline_body is not None:
                remaining = inline_body.strip()
            elif _PLAIN_START.match(remaining):
                raise GeminiReplyFormatError("Gemini 分析标题后缺少可靠正文边界，已阻止发送。")
            else:
                break
        changed = True
    if changed and not remaining:
        raise GeminiReplyFormatError("Gemini 输出仅包含思考内容，已阻止发送。")
    return remaining if changed else text
