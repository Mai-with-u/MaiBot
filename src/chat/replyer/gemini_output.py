"""兼容 Gemini 混入可见正文的显式思考格式。"""

import re


class GeminiReplyFormatError(ValueError):
    """已识别分析输出，但无法可靠提取正式回复。"""


_HEADER = r"(?:thinking\s+process|thought\s+process|思考过程|思维链)"
_ANGLE_START = re.compile(rf"⌊\s*{_HEADER}\s*(?:⌉|[:：])", re.IGNORECASE)
_PLAIN_START = re.compile(rf"(?:#{{1,6}}\s+)?(?:\*\*)?{_HEADER}(?:\*\*)?\s*[:：]", re.IGNORECASE)
_XML_START = re.compile(r"<(think|thinking|analysis)>", re.IGNORECASE)
_BRACKET_START = re.compile(r"\[:thought\b", re.IGNORECASE)


def normalize_gemini_reply(text: str) -> str:
    """只剥离开头有明确边界的思考块；不猜测草稿中的哪一句是正文。

    引用、代码示例和正文中提到思考的普通文本不触发。角括号格式只接受
    独立行的正文分界，避免误把分析里引用的 `⌊...⌉` 当成结束标记。
    方括号思考块只接受独立行的 `]` 闭合，避免把草稿中的方括号当成正文边界。
    """
    remaining = text.strip()
    changed = False
    while remaining:
        angle = _ANGLE_START.match(remaining)
        xml = _XML_START.match(remaining)
        if angle:
            boundary = re.search(r"(?m)^\s*⌈", remaining[angle.end():])
            if boundary is None:
                raise GeminiReplyFormatError("Gemini 思考块缺少明确正文分界，已阻止发送。")
            remaining = remaining[angle.end() + boundary.end():].strip()
        elif xml:
            # 配对扫描处理连续、嵌套块，未闭合时绝不把分析当正文。
            tags = re.finditer(r"</?(?:think|thinking|analysis)>", remaining, re.IGNORECASE)
            stack = []
            end = None
            for tag in tags:
                name = tag.group().strip("</>").lower()
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
        elif _BRACKET_START.match(remaining):
            # 思考中可能引用未闭合的 `[:thought`，不能按嵌套括号计数。
            boundary = re.search(r"(?m)^[ \t]*\][ \t]*\r?$", remaining)
            if boundary is None:
                raise GeminiReplyFormatError("Gemini 方括号思考块缺少可靠闭合边界，已阻止发送。")
            remaining = remaining[boundary.end():].strip()
        elif _PLAIN_START.match(remaining):
            raise GeminiReplyFormatError("Gemini 分析标题后缺少可靠正文边界，已阻止发送。")
        else:
            break
        changed = True
    if changed and not remaining:
        raise GeminiReplyFormatError("Gemini 输出仅包含思考内容，已阻止发送。")
    return remaining if changed else text
