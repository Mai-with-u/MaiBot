# 本文件为测试文件，请忽略Lint error，内含大量的ignore标识

from typing import Any, Optional, Union, List
from pathlib import Path
from importlib import util
import sys
import pytest

TEST_ROOT = Path(__file__).parent.parent.absolute().resolve()
logger_file = TEST_ROOT / "logger.py"
spec = util.spec_from_file_location("src.common.logger", logger_file)
module = util.module_from_spec(spec)  # type: ignore
spec.loader.exec_module(module)  # type: ignore
sys.modules["src.common.logger"] = module

# 测试对象导入
PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.absolute().resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "config"))

from src.config.config_base import ConfigBase, Field  # noqa: E402


class IllegalConfig_Dict(ConfigBase):
    a: dict = Field(default_factory=dict)


class IllegalConfig_List(ConfigBase):
    b: list = Field(default_factory=list)


class IllegalConfig_Set(ConfigBase):
    c: set = Field(default_factory=set)


class IllegalConfig_Tuple(ConfigBase):
    d: tuple = Field(default_factory=tuple)


class IllegalConfig_Union(ConfigBase):
    e: Union[int, str] = Field(default_factory=str)


class IllegalConfig_Any(ConfigBase):
    f: Any = Field(default_factory=dict)


class IllegalConfig_NestedGeneric(ConfigBase):
    g: list[List[int]] = Field(default_factory=list)


class IllegalConfig_Any_suppress(ConfigBase):
    f: Any = Field(default_factory=dict)
    _validate_any: bool = False


class SubClass(ConfigBase):
    x: Optional[int] = Field(default=None)
    y: list[int] = [123]


class LegalConfig(ConfigBase):
    a: dict[str, list[int]] = Field(default_factory=dict)
    b: list[int] = Field(default_factory=list)
    c: set[str] = Field(default_factory=set)
    d: Optional[str] = Field(default=None)
    e: SubClass = Field(default_factory=SubClass)


@pytest.mark.parametrize(
    "config_class, expected_exception, expected_message",
    [
        (IllegalConfig_Dict, TypeError, "必须指定键和值的类型参数"),
        (IllegalConfig_List, TypeError, "必须指定且仅指定一个类型参数"),
        (IllegalConfig_Set, TypeError, "必须指定且仅指定一个类型参数"),
        (IllegalConfig_Tuple, TypeError, "不允许使用 Tuple 类型注解"),
        (IllegalConfig_Union, TypeError, "不允许使用 Union 类型注解"),
        (IllegalConfig_Any, TypeError, "不允许使用 Any 类型注解"),
        (IllegalConfig_NestedGeneric, TypeError, "不允许嵌套泛型类型"),
        (IllegalConfig_Any_suppress, None, ""),
    ],
)
def test_illegal_config(config_class, expected_exception, expected_message):
    # sourcery skip: no-conditionals-in-tests
    if expected_exception:
        with pytest.raises(expected_exception) as exc_info:
            config_class()
        assert expected_message in str(exc_info.value)
        assert expected_exception == exc_info.type
    else:
        config_instance = config_class()
        assert isinstance(config_instance, config_class)


def test_legal_config():
    config_instance = LegalConfig()
    assert isinstance(config_instance, LegalConfig)
    assert isinstance(config_instance.a, dict)
    assert isinstance(config_instance.b, list)
    assert isinstance(config_instance.c, set)
    assert config_instance.d is None
    assert isinstance(config_instance.e, SubClass)
    assert config_instance.e.x is None
    assert isinstance(config_instance.e.y, list)
    assert config_instance.e.y == [123]
