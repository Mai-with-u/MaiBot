from dataclasses import dataclass, fields, MISSING
from typing import TypeVar, Type, Any, get_origin, get_args, Literal, Union
import types

T = TypeVar("T", bound="ConfigBase")

TOML_DICT_TYPE = {
    int,
    float,
    str,
    bool,
    list,
    dict,
}


@dataclass
class ConfigBase:
    """配置类的基类"""

    @classmethod
    def from_dict(cls: Type[T], data: dict[str, Any]) -> T:
        """从字典加载配置字段"""
        if not isinstance(data, dict):
            raise TypeError(f"Expected a dictionary, got {type(data).__name__}")

        init_args: dict[str, Any] = {}

        for f in fields(cls):
            field_name = f.name

            if field_name.startswith("_"):
                # 跳过以 _ 开头的字段
                continue

            if field_name not in data:
                if f.default is not MISSING or f.default_factory is not MISSING:
                    # 跳过未提供且有默认值/默认构造方法的字段
                    continue
                else:
                    raise ValueError(f"Missing required field: '{field_name}'")

            value = data[field_name]
            field_type = f.type

            try:
                init_args[field_name] = cls._convert_field(value, field_type)  # type: ignore
            except TypeError as e:
                raise TypeError(f"Field '{field_name}' has a type error: {e}") from e
            except Exception as e:
                raise RuntimeError(f"Failed to convert field '{field_name}' to target type: {e}") from e

        return cls(**init_args)

    @classmethod
    def _convert_field(cls, value: Any, field_type: Type[Any]) -> Any:
        """
        转换字段值为指定类型

        1. 对于嵌套的 dataclass，递归调用相应的 from_dict 方法
        2. 对于泛型集合类型（list, set, tuple），递归转换每个元素
        3. 对于基础类型（int, str, float, bool），直接转换
        4. 对于其他类型，尝试直接转换，如果失败则抛出异常
        """

        # 如果是嵌套的 dataclass，递归调用 from_dict 方法
        if isinstance(field_type, type) and issubclass(field_type, ConfigBase):
            if not isinstance(value, dict):
                raise TypeError(f"Expected a dictionary for {field_type.__name__}, got {type(value).__name__}")
            return field_type.from_dict(value)

        # 处理泛型集合类型（list, set, tuple）
        field_origin_type = get_origin(field_type)
        field_type_args = get_args(field_type)

        if field_origin_type in {list, set, tuple}:
            # 检查提供的value是否为list
            if not isinstance(value, list):
                raise TypeError(f"Expected an list for {field_type.__name__}, got {type(value).__name__}")

            if field_origin_type is list:
                # 如果列表元素类型是ConfigBase的子类，则对每个元素调用from_dict
                if (
                    field_type_args
                    and isinstance(field_type_args[0], type)
                    and issubclass(field_type_args[0], ConfigBase)
                ):
                    return [field_type_args[0].from_dict(item) for item in value]
                return [cls._convert_field(item, field_type_args[0]) for item in value]
            elif field_origin_type is set:
                return {cls._convert_field(item, field_type_args[0]) for item in value}
            elif field_origin_type is tuple:
                # 检查提供的value长度是否与类型参数一致
                if len(value) != len(field_type_args):
                    raise TypeError(
                        f"Expected {len(field_type_args)} items for {field_type.__name__}, got {len(value)}"
                    )
                return tuple(cls._convert_field(item, arg) for item, arg in zip(value, field_type_args, strict=False))

        if field_origin_type is dict:
            # 检查提供的value是否为dict
            if not isinstance(value, dict):
                raise TypeError(f"Expected a dictionary for {field_type.__name__}, got {type(value).__name__}")

            # 检查字典的键值类型
            if len(field_type_args) != 2:
                raise TypeError(f"Expected a dictionary with two type arguments for {field_type.__name__}")
            key_type, value_type = field_type_args

            return {cls._convert_field(k, key_type): cls._convert_field(v, value_type) for k, v in value.items()}

        # 处理 Union/Optional 类型（包括 float | None 这种 Python 3.10+ 语法）    
        # 注意：
        # - Optional[float] 等价于 Union[float, None]，get_origin() 返回 typing.Union
        # - float | None 是 types.UnionType，get_origin() 返回 None
        is_union_type = (
            field_origin_type is Union  # typing.Optional / typing.Union
            or isinstance(field_type, types.UnionType)  # Python 3.10+ 的 | 语法
        )
        
        if is_union_type:
            union_args = field_type_args if field_type_args else get_args(field_type)
            
            # 安全检查：只允许 T | None 形式的 Optional 类型，禁止 float | str 这种多类型 Union
            non_none_types = [arg for arg in union_args if arg is not type(None)]
            if len(non_none_types) > 1:
                raise TypeError(
                    f"配置字段不支持多类型 Union（如 float | str），只支持 Optional 类型（如 float | None）。"
                    f"当前类型: {field_type}"
                )
            
            # 如果值是 None 且 None 在 Union 中，直接返回
            if value is None and type(None) in union_args:
                return None
            # 尝试转换为非 None 的类型
            for arg in union_args:
                if arg is not type(None):
                    try:
                        return cls._convert_field(value, arg)
                    except (ValueError, TypeError):
                        continue
            # 如果所有类型都转换失败，抛出异常
            raise TypeError("Cannot convert value to any type in Union")

        # 处理基础类型，例如 int, str 等
        if field_origin_type is type(None) and value is None:  # 处理Optional类型
            return None

        # 处理Literal类型
        if field_origin_type is Literal or get_origin(field_type) is Literal:
            # 获取Literal的允许值
            allowed_values = get_args(field_type)
            if value in allowed_values:
                return value
            else:
                raise TypeError(f"Value '{value}' is not in allowed values {allowed_values} for Literal type")

        if field_type is Any or isinstance(value, field_type):
            return value

        # 其他类型，尝试直接转换
        try:
            return field_type(value)
        except (ValueError, TypeError) as e:
            raise TypeError(f"Cannot convert {type(value).__name__} to {field_type.__name__}") from e

    def __str__(self):
        """返回配置类的字符串表示"""
        return f"{self.__class__.__name__}({', '.join(f'{f.name}={getattr(self, f.name)}' for f in fields(self))})"
