"""
插件系统配置类型定义

提供插件配置的类型定义，支持 WebUI 可视化配置编辑。
"""

from typing import Any, Optional, List, Dict, Union
from dataclasses import dataclass, field


@dataclass
class ConfigField:
    """
    配置字段定义

    用于定义插件配置项的元数据，支持类型验证、UI 渲染等功能。

    基础示例:
        ConfigField(type=str, default="", description="API密钥")

    完整示例:
        ConfigField(
            type=str,
            default="",
            description="API密钥",
            input_type="password",
            placeholder="请输入API密钥",
            required=True,
            hint="从服务商控制台获取",
            order=1
        )
    """

    # === 基础字段（必需） ===
    type: type  # 字段类型: str, int, float, bool, list, dict
    default: Any  # 默认值
    description: str  # 字段描述（也用作默认标签）

    # === 验证相关 ===
    example: Optional[str] = None  # 示例值（用于生成配置文件注释）
    required: bool = False  # 是否必需
    choices: Optional[List[Any]] = field(default_factory=list)  # 可选值列表（用于下拉选择）
    min: Optional[float] = None  # 最小值（数字类型）
    max: Optional[float] = None  # 最大值（数字类型）
    step: Optional[float] = None  # 步进值（数字类型）
    pattern: Optional[str] = None  # 正则验证（字符串类型）
    max_length: Optional[int] = None  # 最大长度（字符串类型）

    # === UI 显示控制 ===
    label: Optional[str] = None  # 显示标签（默认使用 description）
    placeholder: Optional[str] = None  # 输入框占位符
    hint: Optional[str] = None  # 字段下方的提示文字
    icon: Optional[str] = None  # 字段图标名称
    hidden: bool = False  # 是否在 UI 中隐藏
    disabled: bool = False  # 是否禁用编辑
    order: int = 0  # 排序权重（数字越小越靠前）

    # === 输入控件类型 ===
    # 可选值: text, password, textarea, number, color, code, file, json
    # 不指定时根据 type 和 choices 自动推断
    input_type: Optional[str] = None

    # === textarea 专用 ===
    rows: int = 3  # 文本域行数

    # === 分组与布局 ===
    group: Optional[str] = None  # 字段分组（在 section 内再细分）

    # === 条件显示 ===
    depends_on: Optional[str] = None  # 依赖的字段路径，如 "section.field"
    depends_value: Any = None  # 依赖字段需要的值（当依赖字段等于此值时显示）

    def get_ui_type(self) -> str:
        """
        获取 UI 控件类型

        如果指定了 input_type 则直接返回，否则根据 type 和 choices 自动推断。

        Returns:
            控件类型字符串
        """
        if self.input_type:
            return self.input_type

        # 根据 type 和 choices 自动推断
        if self.type is bool:
            return "switch"
        elif self.type in (int, float):
            if self.min is not None and self.max is not None:
                return "slider"
            return "number"
        elif self.type is str:
            if self.choices:
                return "select"
            return "text"
        elif self.type is list:
            return "list"
        elif self.type is dict:
            return "json"
        else:
            return "text"

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为可序列化的字典（用于 API 传输）

        Returns:
            包含所有配置信息的字典
        """
        return {
            "type": self.type.__name__ if isinstance(self.type, type) else str(self.type),
            "default": self.default,
            "description": self.description,
            "example": self.example,
            "required": self.required,
            "choices": self.choices if self.choices else None,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "pattern": self.pattern,
            "max_length": self.max_length,
            "label": self.label or self.description,
            "placeholder": self.placeholder,
            "hint": self.hint,
            "icon": self.icon,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "order": self.order,
            "input_type": self.input_type,
            "ui_type": self.get_ui_type(),
            "rows": self.rows,
            "group": self.group,
            "depends_on": self.depends_on,
            "depends_value": self.depends_value,
        }


@dataclass
class ConfigSection:
    """
    配置节定义

    用于描述配置文件中一个 section 的元数据。

    示例:
        ConfigSection(
            title="API配置",
            description="外部API连接参数",
            icon="cloud",
            order=1
        )
    """

    title: str  # 显示标题
    description: Optional[str] = None  # 详细描述
    icon: Optional[str] = None  # 图标名称
    collapsed: bool = False  # 默认是否折叠
    order: int = 0  # 排序权重

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "collapsed": self.collapsed,
            "order": self.order,
        }


@dataclass
class ConfigTab:
    """
    配置标签页定义

    用于将多个 section 组织到一个标签页中。

    示例:
        ConfigTab(
            id="general",
            title="通用设置",
            icon="settings",
            sections=["plugin", "api"]
        )
    """

    id: str  # 标签页 ID
    title: str  # 显示标题
    sections: List[str] = field(default_factory=list)  # 包含的 section 名称列表
    icon: Optional[str] = None  # 图标名称
    order: int = 0  # 排序权重
    badge: Optional[str] = None  # 角标文字（如 "Beta", "New"）

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "id": self.id,
            "title": self.title,
            "sections": self.sections,
            "icon": self.icon,
            "order": self.order,
            "badge": self.badge,
        }


@dataclass
class ConfigLayout:
    """
    配置页面布局定义

    用于定义插件配置页面的整体布局结构。

    布局类型:
        - "auto": 自动布局，sections 作为折叠面板显示
        - "tabs": 标签页布局
        - "pages": 分页布局（左侧导航 + 右侧内容）

    简单示例（标签页布局）:
        ConfigLayout(
            type="tabs",
            tabs=[
                ConfigTab(id="basic", title="基础", sections=["plugin", "api"]),
                ConfigTab(id="advanced", title="高级", sections=["debug"]),
            ]
        )
    """

    type: str = "auto"  # 布局类型: auto, tabs, pages
    tabs: List[ConfigTab] = field(default_factory=list)  # 标签页列表

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典"""
        return {
            "type": self.type,
            "tabs": [tab.to_dict() for tab in self.tabs],
        }


def section_meta(
    title: str, description: Optional[str] = None, icon: Optional[str] = None, collapsed: bool = False, order: int = 0
) -> Union[str, ConfigSection]:
    """
    便捷函数：创建 section 元数据

    可以在 config_section_descriptions 中使用，提供比纯字符串更丰富的信息。

    Args:
        title: 显示标题
        description: 详细描述
        icon: 图标名称
        collapsed: 默认是否折叠
        order: 排序权重

    Returns:
        ConfigSection 实例

    示例:
        config_section_descriptions = {
            "api": section_meta("API配置", icon="cloud", order=1),
            "debug": section_meta("调试设置", collapsed=True, order=99),
        }
    """
    return ConfigSection(title=title, description=description, icon=icon, collapsed=collapsed, order=order)
