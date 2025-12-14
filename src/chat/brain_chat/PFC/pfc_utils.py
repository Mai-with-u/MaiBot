import json
import re
from typing import Dict, Any, Optional, Tuple, List, Union
from src.common.logger import get_module_logger

logger = get_module_logger("pfc_utils")


def get_items_from_json(
    content: str,
    private_name: str,
    *items: str,
    default_values: Optional[Dict[str, Any]] = None,
    required_types: Optional[Dict[str, type]] = None,
    allow_array: bool = True,
) -> Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]:
    """从文本中提取JSON内容并获取指定字段

    Args:
        content: 包含JSON的文本
        private_name: 私聊名称
        *items: 要提取的字段名
        default_values: 字段的默认值，格式为 {字段名: 默认值}
        required_types: 字段的必需类型，格式为 {字段名: 类型}
        allow_array: 是否允许解析JSON数组

    Returns:
        Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]: (是否成功, 提取的字段字典或字典列表)
    """
    content = content.strip()
    result = {}

    # 设置默认值
    if default_values:
        result.update(default_values)

    # 首先尝试解析为JSON数组
    if allow_array:
        try:
            # 尝试找到文本中的JSON数组
            array_pattern = r"\[[\s\S]*\]"
            array_match = re.search(array_pattern, content)
            if array_match:
                array_content = array_match.group()
                json_array = json.loads(array_content)

                # 确认是数组类型
                if isinstance(json_array, list):
                    # 验证数组中的每个项目是否包含所有必需字段
                    valid_items = []
                    for item in json_array:
                        if not isinstance(item, dict):
                            continue

                        # 检查是否有所有必需字段
                        if all(field in item for field in items):
                            # 验证字段类型
                            if required_types:
                                type_valid = True
                                for field, expected_type in required_types.items():
                                    if field in item and not isinstance(item[field], expected_type):
                                        type_valid = False
                                        break

                                if not type_valid:
                                    continue

                            # 验证字符串字段不为空
                            string_valid = True
                            for field in items:
                                if isinstance(item[field], str) and not item[field].strip():
                                    string_valid = False
                                    break

                            if not string_valid:
                                continue

                            valid_items.append(item)

                    if valid_items:
                        return True, valid_items
        except json.JSONDecodeError:
            logger.debug(f"[私聊][{private_name}]JSON数组解析失败，尝试解析单个JSON对象")
        except Exception as e:
            logger.debug(f"[私聊][{private_name}]尝试解析JSON数组时出错: {str(e)}")

    # 尝试解析JSON对象
    try:
        json_data = json.loads(content)
    except json.JSONDecodeError:
        # 如果直接解析失败，尝试查找和提取JSON部分
        json_pattern = r"\{[^{}]*\}"
        json_match = re.search(json_pattern, content)
        if json_match:
            try:
                json_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.error(f"[私聊][{private_name}]提取的JSON内容解析失败")
                return False, result
        else:
            logger.error(f"[私聊][{private_name}]无法在返回内容中找到有效的JSON")
            return False, result

    # 提取字段
    for item in items:
        if item in json_data:
            result[item] = json_data[item]

    # 验证必需字段
    if not all(item in result for item in items):
        logger.error(f"[私聊][{private_name}]JSON缺少必要字段，实际内容: {json_data}")
        return False, result

    # 验证字段类型
    if required_types:
        for field, expected_type in required_types.items():
            if field in result and not isinstance(result[field], expected_type):
                logger.error(f"[私聊][{private_name}]{field} 必须是 {expected_type.__name__} 类型")
                return False, result

    # 验证字符串字段不为空
    for field in items:
        if isinstance(result[field], str) and not result[field].strip():
            logger.error(f"[私聊][{private_name}]{field} 不能为空")
            return False, result

    return True, result
