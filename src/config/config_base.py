import ast
import inspect
import types

from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from typing import Union, get_args, get_origin, Tuple, Any, List, Dict, Set

__all__ = ["ConfigBase", "Field"]

from src.common.logger import get_logger

logger = get_logger("ConfigBase")


class AttrDocBase:
    """解析字段说明的基类"""

    field_docs: dict[str, str] = {}

    def __post_init__(self):
        self.field_docs = self._get_field_docs()  # 全局仅获取一次并保留

    @classmethod
    def _get_field_docs(cls) -> dict[str, str]:
        """
        获取字段的说明字符串

        :param cls: 配置类
        :return: 字段说明字典，键为字段名，值为说明字符串
        """
        # 获取类的源代码文本
        class_source = cls._get_class_source()
        # 解析源代码，找到对应的类定义节点
        class_node = cls._find_class_node(class_source)
        # 从类定义节点中提取字段文档
        return cls._extract_field_docs(class_node)

    @classmethod
    def _get_class_source(cls) -> str:
        """获取类定义所在文件的完整源代码"""
        # 使用 inspect 模块获取类定义所在的文件路径
        class_file = inspect.getfile(cls)
        # 读取文件内容并以 UTF-8 编码返回
        return Path(class_file).read_text(encoding="utf-8")

    @classmethod
    def _find_class_node(cls, class_source: str) -> ast.ClassDef:
        """在源代码中找到类定义的AST节点"""
        tree = ast.parse(class_source)
        # 遍历 AST 中的所有节点
        for node in ast.walk(tree):
            # 查找类定义节点，且类名与当前类名匹配
            if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
                """类名匹配，返回节点"""
                return node
        # 如果没有找到匹配的类定义，抛出异常
        raise AttributeError(f"Class {cls.__name__} not found in source.")

    @classmethod
    def _extract_field_docs(cls, class_node: ast.ClassDef) -> dict[str, str]:
        """从类的 AST 节点中提取字段的文档字符串"""
        doc_dict: dict[str, str] = {}
        class_body = class_node.body  # 类属性节点列表
        for i in range(len(class_body)):
            body_item = class_body[i]

            # 检查是否有非 model_post_init 的方法定义，如果有则抛出异常
            # 这个限制确保 AttrDocBase 子类只包含字段定义和 model_post_init 方法
            if isinstance(body_item, ast.FunctionDef) and body_item.name != "model_post_init":
                """检验ConfigBase子类中是否有除model_post_init以外的方法，规范配置类的定义"""
                raise AttributeError(
                    f"Methods are not allowed in AttrDocBase subclasses except model_post_init, found {str(body_item.name)}"
                ) from None

            # 检查当前语句是否为带注解的赋值语句 (类型注解的字段定义)
            # 并且下一个语句存在
            if (
                i + 1 < len(class_body)
                and isinstance(body_item, ast.AnnAssign)  # 例如: field_name: int = 10
                and isinstance(body_item.target, ast.Name)  # 目标是一个简单的名称
            ):
                """字段定义后紧跟的字符串表达式即为字段说明"""
                expr_item = class_body[i + 1]

                # 检查下一个语句是否为字符串常量表达式 (文档字符串)
                if (
                    isinstance(expr_item, ast.Expr)  # 表达式语句
                    and isinstance(expr_item.value, ast.Constant)  # 常量值
                    and isinstance(expr_item.value.value, str)  # 字符串常量
                ):
                    doc_string = expr_item.value.value.strip()  # 获取说明字符串并去除首尾空白
                    processed_doc_lines = [line.strip() for line in doc_string.splitlines()]  # 多行处理

                    # 删除开头的所有空行
                    while processed_doc_lines and not processed_doc_lines[0]:
                        processed_doc_lines.pop(0)

                    # 删除结尾的所有空行
                    while processed_doc_lines and not processed_doc_lines[-1]:
                        processed_doc_lines.pop()

                    # 将处理后的行重新组合，并存入字典
                    # 键是字段名，值是清理后的文档字符串
                    doc_dict[body_item.target.id] = "\n".join(processed_doc_lines)

        return doc_dict


class ConfigBase(BaseModel, AttrDocBase):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    _validate_any: bool = True  # 是否验证 Any 类型的使用，默认为 True

    def _discourage_any_usage(self, field_name: str) -> None:
        """警告使用 Any 类型的字段（可被suppress）"""
        if self._validate_any:
            raise TypeError(f"字段'{field_name}'中不允许使用 Any 类型注解")
        else:
            logger.warning(f"字段'{field_name}'中使用了 Any 类型注解，建议避免使用。")

    def _get_real_type(self, annotation: type[Any] | Any | None):
        """获取真实类型，处理 dict 等没有参数的情况"""
        origin_type = get_origin(annotation)
        args_type = get_args(annotation)
        if origin_type is None:
            origin_type = annotation
            args_type = ()
        return origin_type, args_type

    def _validate_union_type(self, annotation: type[Any] | Any | None, field_name: str):
        """
        验证 Union 类型的使用（可被suppress）
        明确禁止 Union / PEP 604 的 | 表示法
        允许 Optional[T]（即 Union[T, None]）"""
        origin, args = self._get_real_type(annotation)
        other = annotation
        if origin in (Union, types.UnionType):
            if len(args) != 2 or all(a is not type(None) for a in args):
                raise TypeError(f"类'{type(self).__name__}'字段'{field_name}'中不允许使用 Union 类型注解")

            # 将注解替换为 Optional 的内部类型，继续后续校验（允许原子或容器类型）
            other = args[0] if args[1] is type(None) else args[1]
            origin, args = self._get_real_type(other)
        if origin in (Union, types.UnionType):
            raise TypeError(f"类'{type(self).__name__}'字段'{field_name}'中不允许嵌套使用 Union/Optional 类型注解")
        return origin, args, other

    def _validate_list_set_type(self, annotation: Any | None, field_name: str):
        """验证 list/set 类型的使用"""
        origin, args = self._get_real_type(annotation)

        if origin in (list, set, List, Set):
            if len(args) != 1:
                raise TypeError(
                    f"类'{type(self).__name__}'字段'{field_name}'中必须指定且仅指定一个类型参数，使用了: {annotation}"
                )
            elem = args[0]
            if elem is Any:
                self._discourage_any_usage(field_name)
            if get_origin(elem) is not None:
                raise TypeError(
                    f"类'{type(self).__name__}'字段'{field_name}'中不允许嵌套泛型类型: {annotation}，请使用自定义类代替。"
                )

    def _validate_dict_type(self, annotation: Any | None, field_name: str):
        """验证 dict 类型的使用"""
        _, args = self._get_real_type(annotation)
        if len(args) != 2:
            raise TypeError(f"类'{type(self).__name__}'字段'{field_name}'中必须指定键和值的类型参数: {annotation}")
        _, val_t = args
        if val_t is Any:
            self._discourage_any_usage(field_name)
        if get_origin(val_t):
            origin_type = get_origin(val_t)
            if origin_type is None:
                return
            origin_type, _, anno = self._validate_union_type(val_t, field_name)
            if origin_type in (list, set, List, Set):
                self._validate_list_set_type(anno, field_name)
            elif origin_type is Any:
                self._discourage_any_usage(field_name)
            else:
                raise TypeError(
                    f"类'{type(self).__name__}'字段'{field_name}'中不允许嵌套泛型类型: {annotation}，请使用自定义类代替。"
                )

    def model_post_init(self, context: Any = None) -> None:
        """验证字段的类型注解

        规则：
        - 允许原子注解（非泛型，且不为 Any）
        - 允许 list[T], set[T]，其中 T 为原子注解
        - 允许 dict[K, V]，其中 K、V 为原子注解
        - 禁止使用 Union（不包含 Optional）和 tuple（及 Tuple）
        - 禁止嵌套泛型（例如 list[list[int]]）和使用 Any
        """
        for field_name, field_info in type(self).model_fields.items():
            annotation = field_info.annotation
            origin_type, _ = self._get_real_type(annotation)
            # 处理 Union (含Optional) 类型
            origin_type, _, annotation = self._validate_union_type(annotation, field_name)
            # 禁止 tuple / Tuple
            if origin_type in (tuple, Tuple):
                raise TypeError(f"类'{type(self).__name__}'字段'{field_name}'中不允许使用 Tuple 类型注解")
            # 处理 Any 类型
            if origin_type is Any:
                self._discourage_any_usage(field_name)

            # 非泛型注解视为原子类型，允许
            if origin_type in (int, float, str, bool, complex, bytes, type(None), Any):
                continue
            # 允许嵌套的ConfigBase自定义类
            if inspect.isclass(origin_type) and issubclass(origin_type, ConfigBase):  # type: ignore
                continue
            # 只允许 list, set, dict 三类泛型
            if origin_type not in (list, set, dict, List, Set, Dict):
                raise TypeError(
                    f"仅允许使用list, set, dict三种泛型类型注解，类'{type(self).__name__}'字段'{field_name}'中使用了: {annotation}"
                )
            # list/set: 必须指定且仅指定一个类型参数，且参数为原子类型
            if origin_type in (list, set, List, Set):
                self._validate_list_set_type(annotation, field_name)
            # dict: 必须指定两个类型参数，且 key/value 为原子类型或者set/list类型
            if origin_type in (dict, Dict):
                self._validate_dict_type(annotation, field_name)

        super().model_post_init(context)
        super().__post_init__()  # 获取字段说明
