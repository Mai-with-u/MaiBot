import logging
import sys
from importlib import util
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pytest
from pydantic import BaseModel, Field

# -------------------------------------------------------------
# 测试环境准备：补全 logger 和 AttrDocBase 依赖
# -------------------------------------------------------------

TEST_ROOT = Path(__file__).parent.parent.absolute().resolve()
logger_file = TEST_ROOT / "logger.py"
spec = util.spec_from_file_location("src.common.logger", logger_file)
module = util.module_from_spec(spec)  # type: ignore
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)  # type: ignore
sys.modules["src.common.logger"] = module

PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.absolute().resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "config"))

from src.config.config_base import ConfigBase  # noqa: E402
import src.config.config_base as config_base_module  # noqa: E402


class AttrDocBase:
    """用于测试的轻量级 AttrDocBase 替身"""

    def __post_init__(self) -> None:
        # 被 ConfigBase.model_post_init 调用
        self.__post_init_called__ = True


# 打补丁，让 ConfigBase 使用测试替身
@pytest.fixture(autouse=True)
def patch_attrdoc_post_init():
    orig = config_base_module.AttrDocBase.__post_init__
    config_base_module.AttrDocBase.__post_init__ = AttrDocBase.__post_init__  # type: ignore
    yield
    config_base_module.AttrDocBase.__post_init__ = orig


config_base_module.logger = logging.getLogger("config_base_test_logger")

class SimpleClass(ConfigBase):
    a: int = 1
    b: str = "test"


class TestConfigBase:
    # ---------------------------------------------------------
    # happy path：整体 model_post_init 测试
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "model_cls, init_kwargs, expected_fields",
        [
            pytest.param(
                # 简单原子类型字段
                type(
                    "SimpleAtomic",
                    (ConfigBase,),
                    {
                        "__annotations__": {
                            "a": int,
                            "b": str,
                            "c": bool,
                            "d": float,
                        },
                        "a": Field(default=1),
                        "b": Field(default="x"),
                        "c": Field(default=True),
                        "d": Field(default=1.5),
                    },
                ),
                {},
                {"a", "b", "c", "d"},
                id="happy-simple-atomic-fields",
            ),
            pytest.param(
                # list/set/dict 泛型 + 原子内部类型
                type(
                    "AtomicContainers",
                    (ConfigBase,),
                    {
                        "__annotations__": {
                            "ints": List[int],
                            "names": Set[str],
                            "mapping": Dict[str, int],
                        },
                        "ints": Field(default_factory=lambda: [1, 2]),
                        "names": Field(default_factory=lambda: {"a", "b"}),
                        "mapping": Field(default_factory=lambda: {"x": 1}),
                    },
                ),
                {},
                {"ints", "names", "mapping"},
                id="happy-atomic-containers",
            ),
            pytest.param(
                # Optional 原子和 Optional 容器
                type(
                    "OptionalFields",
                    (ConfigBase,),
                    {
                        "__annotations__": {
                            "maybe_int": Optional[int],
                            "maybe_str_list": Optional[List[str]],
                        },
                        "maybe_int": Field(default=None),
                        "maybe_str_list": Field(default=None),
                    },
                ),
                {},
                {"maybe_int", "maybe_str_list"},
                id="happy-optional-fields",
            ),
        ],
    )
    def test_model_post_init_happy_paths(self, model_cls, init_kwargs, expected_fields):
        # Act
        instance = model_cls(**init_kwargs)

        # Assert
        for field_name in expected_fields:
            assert field_name in type(instance).model_fields
            _ = getattr(instance, field_name)
        assert getattr(instance, "__post_init_called__", False) is True

    # ---------------------------------------------------------
    # _get_real_type
    # ---------------------------------------------------------
    def test_get_real_type_non_generic_and_generic(self):
        class Sample(ConfigBase):
            x: int = 1
            y: List[int] = Field(default_factory=list)

        instance = Sample()

        # Act
        origin_x, args_x = instance._get_real_type(int)

        # Assert
        assert origin_x is int
        assert args_x == ()

        # Act
        origin_y, args_y = instance._get_real_type(List[int])

        # Assert
        assert origin_y in (list, List)
        assert args_y == (int,)

    # ---------------------------------------------------------
    # _validate_union_type
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "annotation, expect_error, error_fragment, expected_origin_type",
        [
            pytest.param(
                int,
                False,
                None,
                int,
                id="union-validation-atomic-non-union",
            ),
            pytest.param(
                Optional[int],
                False,
                None,
                int,
                id="union-validation-optional-atomic",
            ),
            pytest.param(
                Optional[List[int]],
                False,
                None,
                list,
                id="union-validation-optional-container",
            ),
            pytest.param(
                Union[int, str],
                True,
                "不允许使用 Union 类型注解",
                None,
                id="union-validation-disallow-non-optional-union",
            ),
            pytest.param(
                int | str,
                True,
                "不允许使用 Union 类型注解",
                None,
                id="union-validation-pep604-disallow-non-optional-union",
            ),
            pytest.param(
                Union[int, None, str],
                True,
                "不允许使用 Union 类型注解",
                None,
                id="union-validation-disallow-union-more-than-two",
            ),
            pytest.param(
                Optional[Union[int, str]],
                True,
                "不允许使用 Union 类型注解",
                None,
                id="union-validation-disallow-nested-optional-union",
            ),
        ],
    )
    def test_validate_union_type(self, annotation, expect_error, error_fragment, expected_origin_type):
        # 这里我们不实例化 Sample，以避免在 __init__/model_post_init 阶段触发验证。
        # 直接通过一个“哑实例”调用受测方法，仅测试类型注解逻辑。

        class Dummy(ConfigBase):
            pass

        dummy = Dummy()  # 最小初始化，避免字段校验

        field_name = "v"

        if expect_error:
            # Act / Assert
            with pytest.raises(TypeError) as exc_info:
                dummy._validate_union_type(annotation, field_name)
            assert error_fragment in str(exc_info.value)
        else:
            # Act
            origin, args, other = dummy._validate_union_type(annotation, field_name)

            # Assert
            assert origin is expected_origin_type
            assert other is not None

    # ---------------------------------------------------------
    # _validate_list_set_type
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "annotation, expect_error, error_fragment",
        [
            pytest.param(
                List[int],
                False,
                None,
                id="listset-validation-list-happy",
            ),
            pytest.param(
                Set[str],
                False,
                None,
                id="listset-validation-set-happy",
            ),
            pytest.param(
                list,
                True,
                "必须指定且仅指定一个类型参数",
                id="listset-validation-missing-type-arg",
            ),
            pytest.param(
                List[int | None],
                True,
                "不允许嵌套泛型类型",
                id="listset-validation-nested-generic-inner-union",
            ),
            pytest.param(
                List[List[int]],
                True,
                "不允许嵌套泛型类型",
                id="listset-validation-nested-generic-inner-list",
            ),
            pytest.param(
                List[SimpleClass],
                False,
                None,
                id="listset-validation-list-configbase-element_allow",
            ),
            pytest.param(
                Set[SimpleClass],
                True,
                "ConfigBase is not Hashable",
                id="listset-validation-set-configbase-element_reject",
            )
        ],
    )
    def test_validate_list_set_type(self, annotation, expect_error, error_fragment):
        # 不实例化带有这些字段的模型，避免在 __init__/model_post_init 阶段就失败，
        # 只测试 _validate_list_set_type 本身的逻辑。

        class Dummy(ConfigBase):
            pass

        dummy = Dummy()

        field_name = "items"

        if expect_error:
            # Act / Assert
            with pytest.raises(TypeError) as exc_info:
                dummy._validate_list_set_type(annotation, field_name)
            assert error_fragment in str(exc_info.value)
        else:
            # Act
            dummy._validate_list_set_type(annotation, field_name)

    # ---------------------------------------------------------
    # _validate_dict_type
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "annotation, expect_error, error_fragment",
        [
            pytest.param(
                Dict[str, int],
                False,
                None,
                id="dict-validation-happy-atomic",
            ),
            pytest.param(
                Dict[str, Any],
                True,
                "不允许使用 Any 类型注解",
                id="dict-validation-any-value-disallowed",
            ),
            pytest.param(
                Dict[str, Dict[str, int]],
                True,
                "不允许嵌套泛型类型",
                id="dict-validation-optional-nested-list",
            ),
            pytest.param(
                Dict,
                True,
                "必须指定键和值的类型参数",
                id="dict-validation-missing-args",
            ),
            pytest.param(
                Dict[str, SimpleClass],
                False,
                None,
                id="dict-validation-happy-configbase-value",
            )
        ],
    )
    def test_validate_dict_type(self, annotation, expect_error, error_fragment):
        # 同样不通过字段定义来触发 model_post_init，只测试 _validate_dict_type 本身。

        class Dummy(ConfigBase):
            _validate_any: bool = True

        dummy = Dummy()
        field_name = "mapping"

        if expect_error:

            # Act / Assert
            with pytest.raises(TypeError) as exc_info:
                dummy._validate_dict_type(annotation, field_name)
            assert error_fragment in str(exc_info.value)
        else:

            # Act
            dummy._validate_dict_type(annotation, field_name)

    # ---------------------------------------------------------
    # _discourage_any_usage
    # ---------------------------------------------------------
    def test_discourage_any_usage_raises_when_validate_any_true(self, caplog):
        class Sample(ConfigBase):
            _validate_any: bool = True

        instance = Sample()

        # Act / Assert
        with pytest.raises(TypeError) as exc_info:
            instance._discourage_any_usage("field_x")
        assert "不允许使用 Any 类型注解" in str(exc_info.value)
        assert "建议避免使用" not in caplog.text

    def test_discourage_any_usage_logs_when_validate_any_false(self, caplog):
        class Sample(ConfigBase):
            _validate_any: bool = False

        instance = Sample()

        # Arrange
        caplog.set_level(logging.WARNING, logger="config_base_test_logger")

        # Act
        instance._discourage_any_usage("field_y")

        # Assert
        assert "字段'field_y'中使用了 Any 类型注解" in caplog.text
    
    def test_discourage_any_usage_suppressed_warning(self, caplog):
        class Sample(ConfigBase):
            _validate_any: bool = False
            suppress_any_warning: bool = True

        instance = Sample()

        # Arrange
        caplog.set_level(logging.WARNING, logger="config_base_test_logger")

        # Act
        instance._discourage_any_usage("field_z")

        # Assert
        assert "字段'field_z'中使用了 Any 类型注解" not in caplog.text

    # ---------------------------------------------------------
    # model_post_init 规则覆盖（错误与边界情况）
    # ---------------------------------------------------------
    @pytest.mark.parametrize(
        "field_annotation, expect_error, error_fragment, test_id",
        [
            (
                Tuple[int, int],
                True,
                "不允许使用 Tuple 类型注解",
                "model-post-init-disallow-tuple-typing-tuple",
            ),
            (
                tuple[int, int],
                True,
                "不允许使用 Tuple 类型注解",
                "model-post-init-disallow-pep604-tuple",
            ),
            (
                Union[int, str],
                True,
                "不允许使用 Union 类型注解",
                "model-post-init-disallow-union-field",
            ),
            (
                list,
                True,
                "必须指定且仅指定一个类型参数",
                "model-post-init-list-missing-type-arg",
            ),
            (
                List[List[int]],
                True,
                "不允许嵌套泛型类型",
                "model-post-init-list-nested-generic",
            ),
            (
                Dict[str, Any],
                True,
                "不允许使用 Any 类型注解",
                "model-post-init-dict-value-any",
            ),
            (
                Any,
                True,
                "不允许使用 Any 类型注解",
                "model-post-init-field-any-disallowed",
            ),
            (
                Set[int],
                False,
                None,
                "model-post-init-allow-set-int",
            ),
            (
                Dict[str, Optional[int]],
                False,
                None,
                "model-post-init-allow-dict-optional-int",
            ),
        ],
        ids=lambda v: v[3] if isinstance(v, tuple) else v,
    )
    def test_model_post_init_type_rules(self, field_annotation, expect_error, error_fragment, test_id):
        # Arrange
        attrs = {
            "__annotations__": {"f": field_annotation},
            "f": Field(default=None),
        }
        model_cls = type("DynamicModel" + test_id.replace("-", "_"), (ConfigBase,), attrs)

        if expect_error:
            # Act / Assert
            with pytest.raises(TypeError) as exc_info:
                model_cls()
            assert error_fragment in str(exc_info.value)
        else:
            # Act
            instance = model_cls()

            # Assert
            assert hasattr(instance, "f")

    # ---------------------------------------------------------
    # 嵌套 ConfigBase & 非支持泛型 origin
    # ---------------------------------------------------------
    def test_model_post_init_allows_configbase_nested_class(self):
        class Child(ConfigBase):
            value: int = 1

        class Parent(ConfigBase):
            child: Child = Field(default_factory=Child)

        # Act
        parent = Parent()

        # Assert
        assert isinstance(parent.child, Child)

    def test_model_post_init_disallow_non_supported_generic_origin(self):
        class CustomGeneric(BaseModel):
            pass

        class Sample(ConfigBase):
            f: CustomGeneric = Field(default_factory=CustomGeneric)

        # Arrange / Act / Assert
        with pytest.raises(TypeError) as exc_info:
            Sample()
        assert "仅允许使用list, set, dict三种泛型类型注解" in str(exc_info.value)

    # ---------------------------------------------------------
    # super().model_post_init 和 AttrDocBase.__post_init__ 调用
    # ---------------------------------------------------------
    def test_super_model_post_init_and_attrdoc_post_init_called(self):
        class Sample(ConfigBase):
            value: int = 1

        # Act
        instance = Sample()

        # Assert
        assert getattr(instance, "__post_init_called__", False) is True
