"""
哈希工具模块

提供文本哈希计算功能，用于唯一标识和去重。
"""

import hashlib
import re
from typing import Union


def compute_hash(text: str, hash_type: str = "sha256") -> str:
    """
    计算文本的哈希值

    Args:
        text: 输入文本
        hash_type: 哈希算法类型（sha256, md5等）

    Returns:
        哈希值字符串
    """
    if hash_type == "sha256":
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    elif hash_type == "md5":
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    else:
        raise ValueError(f"不支持的哈希算法: {hash_type}")


def normalize_text(text: str) -> str:
    """
    规范化文本用于哈希计算

    执行以下操作：
    - 去除首尾空白
    - 统一换行符为\\n
    - 压缩多个连续空格
    - 去除不可见字符（保留换行和制表符）

    Args:
        text: 输入文本

    Returns:
        规范化后的文本
    """
    # 去除首尾空白
    text = text.strip()

    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 压缩多个连续空格为一个（但保留换行和制表符）
    text = re.sub(r"[^\S\n]+", " ", text)

    return text


def compute_paragraph_hash(paragraph: str) -> str:
    """
    计算段落的哈希值

    Args:
        paragraph: 段落文本

    Returns:
        段落哈希值（用于paragraph-前缀）
    """
    normalized = normalize_text(paragraph)
    return compute_hash(normalized)


def compute_entity_hash(entity: str) -> str:
    """
    计算实体的哈希值

    Args:
        entity: 实体名称

    Returns:
        实体哈希值（用于entity-前缀）
    """
    normalized = entity.strip().lower()
    return compute_hash(normalized)


def compute_relation_hash(relation: tuple) -> str:
    """
    计算关系的哈希值

    Args:
        relation: 关系元组 (subject, predicate, object)

    Returns:
        关系哈希值（用于relation-前缀）
    """
    # 将关系元组转为字符串
    relation_str = str(tuple(relation))
    return compute_hash(relation_str)


def format_hash_key(hash_type: str, hash_value: str) -> str:
    """
    格式化哈希键

    Args:
        hash_type: 类型前缀（paragraph, entity, relation）
        hash_value: 哈希值

    Returns:
        格式化的键（如 paragraph-abc123...）
    """
    return f"{hash_type}-{hash_value}"


def parse_hash_key(key: str) -> tuple[str, str]:
    """
    解析哈希键

    Args:
        key: 格式化的键（如 paragraph-abc123...）

    Returns:
        (类型, 哈希值) 元组
    """
    parts = key.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"无效的哈希键格式: {key}")
    return parts[0], parts[1]
