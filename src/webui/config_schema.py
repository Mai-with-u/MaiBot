"""
配置架构生成器 - 自动从配置类生成前端表单架构
"""

import inspect
from dataclasses import fields, MISSING
from typing import Any, get_origin, get_args, Literal, Optional
from enum import Enum

from src.config.config_base import ConfigBase


class FieldType(str, Enum):
    """字段类型枚举"""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    SELECT = "select"
    ARRAY = "array"
    OBJECT = "object"
    TEXTAREA = "textarea"


class FieldSchema:
    """字段架构"""

    def __init__(
        self,
        name: str,
        type: FieldType,
        label: str,
        description: str = "",
        default: Any = None,
        required: bool = True,
        options: Optional[list[str]] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        items: Optional[dict] = None,
        properties: Optional[dict] = None,
    ):
        self.name = name
        self.type = type
        self.label = label
        self.description = description
        self.default = default
        self.required = required
        self.options = options
        self.min_value = min_value
        self.max_value = max_value
        self.items = items
        self.properties = properties

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "name": self.name,
            "type": self.type.value,
            "label": self.label,
            "description": self.description,
            "required": self.required,
        }

        if self.default is not None:
            result["default"] = self.default

        if self.options is not None:
            result["options"] = self.options

        if self.min_value is not None:
            result["minValue"] = self.min_value

        if self.max_value is not None:
            result["maxValue"] = self.max_value

        if self.items is not None:
            result["items"] = self.items

        if self.properties is not None:
            result["properties"] = self.properties

        return result


class ConfigSchemaGenerator:
    """配置架构生成器"""

    @staticmethod
    def _extract_field_description(config_class: type, field_name: str) -> str:
        """
        从类定义中提取字段的文档字符串描述

        Args:
            config_class: 配置类
            field_name: 字段名

        Returns:
            str: 字段描述
        """
        try:
            # 获取源代码
            source = inspect.getsource(config_class)
            lines = source.split("\n")

            # 查找字段定义
            field_found = False
            description_lines = []

            for i, line in enumerate(lines):
                # 匹配字段定义行，例如: platform: str
                if f"{field_name}:" in line and "=" in line:
                    field_found = True
                    # 查找下一行的文档字符串
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line.startswith('"""') or next_line.startswith("'''"):
                            # 单行文档字符串
                            if next_line.count('"""') == 2 or next_line.count("'''") == 2:
                                description_lines.append(next_line.replace('"""', "").replace("'''", "").strip())
                            else:
                                # 多行文档字符串
                                quote = '"""' if next_line.startswith('"""') else "'''"
                                description_lines.append(next_line.strip(quote).strip())
                                for j in range(i + 2, len(lines)):
                                    if quote in lines[j]:
                                        description_lines.append(lines[j].split(quote)[0].strip())
                                        break
                                    description_lines.append(lines[j].strip())
                    break
                elif f"{field_name}:" in line and "=" not in line:
                    # 没有默认值的字段
                    field_found = True
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if next_line.startswith('"""') or next_line.startswith("'''"):
                            if next_line.count('"""') == 2 or next_line.count("'''") == 2:
                                description_lines.append(next_line.replace('"""', "").replace("'''", "").strip())
                            else:
                                quote = '"""' if next_line.startswith('"""') else "'''"
                                description_lines.append(next_line.strip(quote).strip())
                                for j in range(i + 2, len(lines)):
                                    if quote in lines[j]:
                                        description_lines.append(lines[j].split(quote)[0].strip())
                                        break
                                    description_lines.append(lines[j].strip())
                    break

            if field_found and description_lines:
                return " ".join(description_lines)

        except Exception:
            pass

        return ""

    @staticmethod
    def _get_field_type_and_options(field_type: type) -> tuple[FieldType, Optional[list[str]], Optional[dict]]:
        """
        获取字段类型和选项

        Args:
            field_type: 字段类型

        Returns:
            tuple: (FieldType, options, items)
        """
        origin = get_origin(field_type)
        args = get_args(field_type)

        # 处理 Literal 类型（枚举选项）
        if origin is Literal:
            return FieldType.SELECT, [str(arg) for arg in args], None

        # 处理 list 类型
        if origin is list:
            item_type = args[0] if args else str
            if item_type is str:
                items = {"type": "string"}
            elif item_type is int:
                items = {"type": "integer"}
            elif item_type is float:
                items = {"type": "number"}
            elif item_type is bool:
                items = {"type": "boolean"}
            elif item_type is dict:
                items = {"type": "object"}
            else:
                items = {"type": "string"}
            return FieldType.ARRAY, None, items

        # 处理 set 类型（与 list 类似）
        if origin is set:
            item_type = args[0] if args else str
            if item_type is str:
                items = {"type": "string"}
            else:
                items = {"type": "string"}
            return FieldType.ARRAY, None, items

        # 处理基本类型
        if field_type is bool:
            return FieldType.BOOLEAN, None, None
        elif field_type is int:
            return FieldType.INTEGER, None, None
        elif field_type is float:
            return FieldType.NUMBER, None, None
        elif field_type is str:
            return FieldType.STRING, None, None
        elif field_type is dict or origin is dict:
            return FieldType.OBJECT, None, None

        # 默认为字符串
        return FieldType.STRING, None, None

    @staticmethod
    def _format_field_name(name: str) -> str:
        """
        格式化字段名为可读的标签

        Args:
            name: 原始字段名

        Returns:
            str: 格式化后的标签
        """
        # 将下划线替换为空格，并首字母大写
        return " ".join(word.capitalize() for word in name.split("_"))

    @staticmethod
    def generate_schema(config_class: type[ConfigBase], include_nested: bool = True) -> dict:
        """
        从配置类生成前端表单架构

        Args:
            config_class: 配置类（必须继承自 ConfigBase）
            include_nested: 是否包含嵌套的配置对象

        Returns:
            dict: 前端表单架构
        """
        if not issubclass(config_class, ConfigBase):
            raise ValueError(f"{config_class.__name__} 必须继承自 ConfigBase")

        schema_fields = []
        nested_schemas = {}

        for field in fields(config_class):
            # 跳过私有字段和内部字段
            if field.name.startswith("_") or field.name in ["MMC_VERSION"]:
                continue

            # 提取字段描述
            description = ConfigSchemaGenerator._extract_field_description(config_class, field.name)

            # 判断是否必填
            required = field.default is MISSING and field.default_factory is MISSING

            # 获取默认值
            default_value = None
            if field.default is not MISSING:
                default_value = field.default
            elif field.default_factory is not MISSING:
                try:
                    default_value = field.default_factory()
                except Exception:
                    default_value = None

            # 检查是否为嵌套的 ConfigBase
            if isinstance(field.type, type) and issubclass(field.type, ConfigBase):
                if include_nested:
                    # 递归生成嵌套配置的架构
                    nested_schema = ConfigSchemaGenerator.generate_schema(field.type, include_nested=True)
                    nested_schemas[field.name] = nested_schema

                    field_schema = FieldSchema(
                        name=field.name,
                        type=FieldType.OBJECT,
                        label=ConfigSchemaGenerator._format_field_name(field.name),
                        description=description or field.type.__doc__ or "",
                        default=default_value,
                        required=required,
                        properties=nested_schema,
                    )
                else:
                    continue
            else:
                # 获取字段类型和选项
                field_type, options, items = ConfigSchemaGenerator._get_field_type_and_options(field.type)

                # 特殊处理：长文本使用 textarea
                if field_type == FieldType.STRING and field.name in [
                    "personality",
                    "reply_style",
                    "interest",
                    "plan_style",
                    "visual_style",
                    "private_plan_style",
                    "reaction",
                    "filtration_prompt",
                ]:
                    field_type = FieldType.TEXTAREA

                field_schema = FieldSchema(
                    name=field.name,
                    type=field_type,
                    label=ConfigSchemaGenerator._format_field_name(field.name),
                    description=description,
                    default=default_value,
                    required=required,
                    options=options,
                    items=items,
                )

            schema_fields.append(field_schema.to_dict())

        return {
            "className": config_class.__name__,
            "classDoc": config_class.__doc__ or "",
            "fields": schema_fields,
            "nested": nested_schemas if nested_schemas else None,
        }

    @staticmethod
    def generate_config_schema(config_class: type[ConfigBase]) -> dict:
        """
        生成完整的配置架构（包含所有嵌套的子配置）

        Args:
            config_class: 配置类

        Returns:
            dict: 完整的配置架构
        """
        return ConfigSchemaGenerator.generate_schema(config_class, include_nested=True)
