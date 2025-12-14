"""
TOML 工具函数

提供 TOML 文件的格式化保存功能，确保数组等元素以美观的多行格式输出。
"""

import re
from typing import Any
import tomlkit
from tomlkit.items import AoT, Table, Array


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
        # 如果是纯 list (非 tomlkit Array) 且包含字典/表，视为 AoT 的列表形式
        # 保持结构递归处理，避免转换为 Inline Table Array (因为 Inline Table 必须单行，复杂对象不友好)
        if isinstance(obj, list) and not isinstance(obj, Array) and obj and isinstance(obj[0], (dict, Table)):
            for i, item in enumerate(obj):
                obj[i] = _format_toml_value(item, threshold, depth)
            return obj

        # 决定是否多行：仅在顶层且长度超过阈值时
        should_multiline = depth == 0 and len(obj) > threshold

        # 如果已经是 tomlkit Array，原地修改以保留注释
        if isinstance(obj, Array):
            obj.multiline(should_multiline)
            for i, item in enumerate(obj):
                obj[i] = _format_toml_value(item, threshold, depth + 1)
            return obj

        # 普通 list：转换为 tomlkit 数组
        arr = tomlkit.array()
        arr.multiline(should_multiline)

        for item in obj:
            arr.append(_format_toml_value(item, threshold, depth + 1))
        return arr

    # 其他基本类型直接返回
    return obj


def _update_toml_doc(target: Any, source: Any) -> None:
    """
    递归合并字典，将 source 的值更新到 target 中，保留 target 的注释和格式。
    - 已存在的键：更新值（递归处理嵌套字典）
    - 新增的键：添加到 target
    - 跳过 version 字段
    """
    if isinstance(source, list) or not isinstance(source, dict) or not isinstance(target, dict):
        return

    for key, value in source.items():
        if key == "version":
            continue
        if key in target:
            # 已存在的键：递归更新或直接赋值
            target_value = target[key]
            if isinstance(value, dict) and isinstance(target_value, dict):
                _update_toml_doc(target_value, value)
            else:
                try:
                    target[key] = tomlkit.item(value)
                except (TypeError, ValueError):
                    target[key] = value
        else:
            # 新增的键：添加到 target
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
        preserve_comments: 是否保留原文件的注释和格式（默认 True）
            若为 True 且文件已存在且 data 不是 tomlkit 文档，会先读取原文件，再将 data 合并进去
    """
    import os
    from tomlkit import TOMLDocument

    # 如果需要保留注释、文件存在、且 data 不是已有的 tomlkit 文档，先读取原文件再合并
    if preserve_comments and os.path.exists(file_path) and not isinstance(data, TOMLDocument):
        with open(file_path, "r", encoding="utf-8") as f:
            doc = tomlkit.load(f)
        _update_toml_doc(doc, data)
        data = doc

    formatted = _format_toml_value(data, multiline_threshold) if multiline_threshold >= 0 else data
    output = tomlkit.dumps(formatted)
    # 规范化：将 3+ 连续空行压缩为 1 个空行，防止空行累积
    output = re.sub(r"\n{3,}", "\n\n", output)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)


def format_toml_string(data: Any, multiline_threshold: int = 1) -> str:
    """格式化 TOML 数据并返回字符串"""
    formatted = _format_toml_value(data, multiline_threshold) if multiline_threshold >= 0 else data
    output = tomlkit.dumps(formatted)
    # 规范化：将 3+ 连续空行压缩为 1 个空行，防止空行累积
    return re.sub(r"\n{3,}", "\n\n", output)
