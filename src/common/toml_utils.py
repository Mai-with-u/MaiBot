"""
TOML 工具函数

提供 TOML 文件的格式化保存功能，确保数组等元素以美观的多行格式输出。
"""

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
        should_multiline = (depth == 0 and len(obj) > threshold)

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


def save_toml_with_format(data: Any, file_path: str, multiline_threshold: int = 1) -> None:
    """格式化 TOML 数据并保存到文件"""
    formatted = _format_toml_value(data, multiline_threshold) if multiline_threshold >= 0 else data
    with open(file_path, "w", encoding="utf-8") as f:
        tomlkit.dump(formatted, f)


def format_toml_string(data: Any, multiline_threshold: int = 1) -> str:
    """格式化 TOML 数据并返回字符串"""
    formatted = _format_toml_value(data, multiline_threshold) if multiline_threshold >= 0 else data
    return tomlkit.dumps(formatted)