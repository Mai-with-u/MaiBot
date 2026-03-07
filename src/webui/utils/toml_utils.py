"""
TOML 工具函数

提供 TOML 文件的格式化保存功能，确保数组等元素以美观的多行格式输出。
"""

from typing import Any
import re
import tomlkit
from tomlkit.items import AoT, Array, Table


def _format_toml_value(obj: Any, threshold: int, depth: int = 0) -> Any:
    """递归格式化 TOML 值，将数组转换为多行格式"""
    # 处理 AoT (Array of Tables) - 保持原样，递归处理内部
    if isinstance(obj, AoT):
        for item in obj:
            _format_toml_value(item, threshold, depth)
        return obj

    # 处理字典类型 (dict 或 Table)
    if isinstance(obj, (dict, Table)):
        for k, v in obj.items():
            obj[k] = _format_toml_value(v, threshold, depth)
        return obj

    # 处理列表类型 (list 或 Array)
    if isinstance(obj, (list, Array)):
        if isinstance(obj, list) and not isinstance(obj, Array) and obj and isinstance(obj[0], (dict, Table)):
            for i, item in enumerate(obj):
                obj[i] = _format_toml_value(item, threshold, depth)
            return obj

        should_multiline = depth == 0 and len(obj) > threshold

        if isinstance(obj, Array):
            obj.multiline(should_multiline)
            for i, item in enumerate(obj):
                obj[i] = _format_toml_value(item, threshold, depth + 1)
            return obj

        arr = tomlkit.array()
        arr.multiline(should_multiline)

        for item in obj:
            arr.append(_format_toml_value(item, threshold, depth + 1))
        return arr

    return obj


def _update_toml_doc(target: Any, source: Any) -> None:
    """
    递归合并字典，将 source 的值更新到 target 中，保留 target 的注释和格式。
    """
    if isinstance(source, list) or not isinstance(source, dict) or not isinstance(target, dict):
        return

    for key, value in source.items():
        if key == "version":
            continue
        if key in target:
            target_value = target[key]
            if isinstance(value, dict) and isinstance(target_value, dict):
                _update_toml_doc(target_value, value)
            else:
                try:
                    target[key] = tomlkit.item(value)
                except (TypeError, ValueError):
                    target[key] = value
        else:
            try:
                target[key] = tomlkit.item(value)
            except (TypeError, ValueError):
                target[key] = value


def save_toml_with_format(
    data: Any, file_path: str, multiline_threshold: int = 1, preserve_comments: bool = True
) -> None:
    """
    格式化 TOML 数据并保存到文件。

    Args:
        data: 要保存的数据（dict 或 tomlkit 文档）
        file_path: 保存路径
        multiline_threshold: 数组多行格式化阈值，-1 表示不格式化
        preserve_comments: 是否保留原文件的注释和格式
    """
    import os

    from tomlkit import TOMLDocument

    if preserve_comments and os.path.exists(file_path) and not isinstance(data, TOMLDocument):
        with open(file_path, "r", encoding="utf-8") as f:
            doc = tomlkit.load(f)
        _update_toml_doc(doc, data)
        data = doc

    formatted = _format_toml_value(data, multiline_threshold) if multiline_threshold >= 0 else data
    output = tomlkit.dumps(formatted)
    output = re.sub(r"\n{3,}", "\n\n", output)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)
