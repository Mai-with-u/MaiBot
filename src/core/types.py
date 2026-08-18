from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional

import copy
import warnings

from maim_message import Seg

from src.llm_models.payload_content.tool_option import ToolCall
# from src.common.data_models.message_data_model import ReplyContentType as ReplyContentType
# from src.common.data_models.message_data_model import ReplyContent as ReplyContent
# from src.common.data_models.message_data_model import ForwardNode as ForwardNode
# from src.common.data_models.message_data_model import ReplySetModel as ReplySetModel


# 组件类型枚举
class ComponentType(Enum):
    """Host 内部使用的组件类型枚举。"""

    ACTION = "action"  # 动作组件
    COMMAND = "command"  # 命令组件
    TOOL = "tool"  # 工具组件

    def __str__(self) -> str:
        """返回枚举值字符串。

        Returns:
            str: 当前组件类型对应的字符串值。
        """
        return self.value


# 动作激活类型枚举
class ActionActivationType(Enum):
    """动作激活类型枚举。"""

    NEVER = "never"  # 从不激活（默认关闭）
    ALWAYS = "always"  # 默认参与到planner
    RANDOM = "random"  # 随机启用action到planner
    KEYWORD = "keyword"  # 关键词触发启用action到planner

    def __str__(self) -> str:
        """返回枚举值字符串。

        Returns:
            str: 当前激活类型对应的字符串值。
        """
        return self.value


# 事件类型枚举
class EventType(Enum):
    """事件类型枚举。"""

    ON_START = "on_start"  # 启动事件，用于调用按时任务
    ON_STOP = "on_stop"  # 停止事件，用于调用按时任务
    ON_MESSAGE_PRE_PROCESS = "on_message_pre_process"
    ON_MESSAGE = "on_message"
    ON_PLAN = "on_plan"
    POST_LLM = "post_llm"
    AFTER_LLM = "after_llm"
    POST_SEND_PRE_PROCESS = "post_send_pre_process"
    POST_SEND = "post_send"
    AFTER_SEND = "after_send"
    UNKNOWN = "unknown"  # 未知事件类型

    def __str__(self) -> str:
        """返回枚举值字符串。

        Returns:
            str: 当前事件类型对应的字符串值。
        """
        return self.value


@dataclass(slots=True)
class ComponentInfo:
    """Host 内部使用的组件信息快照。"""

    name: str
    """组件名称。"""

    description: str = ""
    """组件描述。"""

    enabled: bool = True
    """组件是否启用。"""

    plugin_name: str = ""
    """所属插件 ID。"""

    component_type: ComponentType = field(init=False)
    """组件类型。"""


@dataclass(slots=True)
class ActionInfo(ComponentInfo):
    """供 Planner 与回复链使用的动作信息快照。"""

    action_parameters: Dict[str, str] = field(
        default_factory=dict
    )  # 动作参数与描述，例如 {"param1": "描述1", "param2": "描述2"}
    action_require: List[str] = field(default_factory=list)  # 动作需求说明
    associated_types: List[str] = field(default_factory=list)  # 关联的消息类型
    activation_type: ActionActivationType = ActionActivationType.ALWAYS
    random_activation_probability: float = 0.0
    activation_keywords: List[str] = field(default_factory=list)  # 激活关键词列表
    keyword_case_sensitive: bool = False
    parallel_action: bool = False
    component_type: ComponentType = field(init=False, default=ComponentType.ACTION)
    """组件类型。"""

    def __post_init__(self) -> None:
        """归一化动作快照中的集合字段。"""
        self.action_parameters = dict(self.action_parameters or {})
        self.action_require = list(self.action_require or [])
        self.associated_types = list(self.associated_types or [])
        self.activation_keywords = list(self.activation_keywords or [])


@dataclass(slots=True)
class CommandInfo(ComponentInfo):
    """供命令处理链使用的命令信息快照。"""

    permission: str = "public"
    """命令权限级别；public 为公开，operator 为受保护。"""

    component_type: ComponentType = field(init=False, default=ComponentType.COMMAND)
    """组件类型。"""


@dataclass(slots=True)
class ToolInfo(ComponentInfo):
    """供工具执行链使用的工具信息快照。"""

    parameters_schema: Dict[str, Any] | None = None
    """对象级工具参数 Schema。"""

    component_type: ComponentType = field(init=False, default=ComponentType.TOOL)
    """组件类型。"""

    def get_llm_definition(self) -> Dict[str, Any]:
        """生成供 LLM 使用的规范化工具定义。

        Returns:
            Dict[str, Any]: 统一工具定义字典。
        """
        definition: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.parameters_schema is not None:
            definition["parameters_schema"] = copy.deepcopy(self.parameters_schema)
        return definition


@dataclass(slots=True)
class ModifyFlag:
    """消息修改标记集合。"""

    modify_message_segments: bool = False
    modify_plain_text: bool = False
    modify_llm_prompt: bool = False
    modify_llm_response_content: bool = False
    modify_llm_response_reasoning: bool = False


@dataclass(slots=True)
class MaiMessages:
    """核心事件系统使用的统一消息模型。"""

    message_segments: List[Seg] = field(default_factory=list)
    """消息段列表，支持多段消息"""

    message_base_info: Dict[str, Any] = field(default_factory=dict)
    """消息基本信息，包含平台，用户信息等数据"""

    plain_text: str = ""
    """纯文本消息内容"""

    raw_message: Optional[str] = None
    """原始消息内容"""

    is_group_message: bool = False
    """是否为群组消息"""

    is_private_message: bool = False
    """是否为私聊消息"""

    stream_id: Optional[str] = None
    """流ID，用于标识消息流"""

    llm_prompt: Optional[str] = None
    """LLM提示词"""

    llm_response_content: Optional[str] = None
    """LLM响应内容"""

    llm_response_reasoning: Optional[str] = None
    """LLM响应推理内容"""

    llm_response_model: Optional[str] = None
    """LLM响应模型名称"""

    llm_response_tool_call: Optional[List[ToolCall]] = None
    """LLM使用的工具调用"""

    action_usage: Optional[List[str]] = None
    """使用的Action"""

    additional_data: Dict[Any, Any] = field(default_factory=dict)
    """附加数据，可以存储额外信息"""

    _modify_flags: ModifyFlag = field(default_factory=ModifyFlag)

    def __post_init__(self) -> None:
        """归一化消息段列表。"""
        if self.message_segments is None:
            self.message_segments = []

    def deepcopy(self) -> "MaiMessages":
        """深拷贝当前消息对象。

        Returns:
            MaiMessages: 深拷贝后的消息对象。
        """
        return copy.deepcopy(self)

    def to_transport_dict(self) -> Dict[str, Any]:
        """将消息转换为可通过 IPC 传输的纯字典。"""
        return {
            field_info.name: self._serialize_transport_value(getattr(self, field_info.name))
            for field_info in fields(MaiMessages)
            if field_info.name != "_modify_flags"
        }

    def apply_transport_update(self, modified_dict: Dict[str, Any]) -> "MaiMessages":
        """将 IPC 返回的消息字典回写到当前消息对象。"""
        updated_message = self.deepcopy()
        valid_fields = {field_info.name for field_info in fields(MaiMessages) if field_info.name != "_modify_flags"}

        for key, value in modified_dict.items():
            if key not in valid_fields:
                continue
            deserialized = self._deserialize_transport_field(key, value)
            setattr(updated_message, key, deserialized)

            if key == "message_segments":
                updated_message._modify_flags.modify_message_segments = True
            elif key == "plain_text":
                updated_message._modify_flags.modify_plain_text = True
            elif key == "llm_prompt":
                updated_message._modify_flags.modify_llm_prompt = True
            elif key == "llm_response_content":
                updated_message._modify_flags.modify_llm_response_content = True
            elif key == "llm_response_reasoning":
                updated_message._modify_flags.modify_llm_response_reasoning = True

        return updated_message

    @staticmethod
    def _serialize_transport_value(value: Any) -> Any:
        """递归序列化字段值为可传输结构。

        Args:
            value: 任意字段值。

        Returns:
            Any: 可用于 IPC 传输的纯 Python 值。
        """
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list):
            return [MaiMessages._serialize_transport_value(item) for item in value]
        if isinstance(value, tuple):
            return [MaiMessages._serialize_transport_value(item) for item in value]
        if isinstance(value, dict):
            return {key: MaiMessages._serialize_transport_value(item) for key, item in value.items()}
        if hasattr(value, "__dict__"):
            return {
                key: MaiMessages._serialize_transport_value(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return value

    @staticmethod
    def _deserialize_transport_field(field_name: str, value: Any) -> Any:
        """反序列化特定字段的传输值。

        Args:
            field_name: 字段名称。
            value: 传输层返回的字段值。

        Returns:
            Any: 反序列化后的字段值。
        """
        if field_name == "message_segments" and isinstance(value, list):
            deserialized_segments: List[Seg] = []
            for segment in value:
                if isinstance(segment, Seg):
                    deserialized_segments.append(segment)
                elif isinstance(segment, dict) and "type" in segment:
                    deserialized_segments.append(Seg(type=segment.get("type", "text"), data=segment.get("data", "")))
            return deserialized_segments

        if field_name == "llm_response_tool_call" and isinstance(value, list):
            deserialized_tool_calls: List[ToolCall] = []
            for tool_call in value:
                if isinstance(tool_call, ToolCall):
                    deserialized_tool_calls.append(tool_call)
                elif isinstance(tool_call, dict):
                    deserialized_tool_calls.append(
                        ToolCall(
                            call_id=str(tool_call.get("call_id", "")),
                            func_name=str(tool_call.get("func_name", "")),
                            args=tool_call.get("args"),
                            extra_content=tool_call.get("extra_content")
                            if isinstance(tool_call.get("extra_content"), dict)
                            else None,
                        )
                    )
            return deserialized_tool_calls

        return value

    def modify_message_segments(self, new_segments: List[Seg], suppress_warning: bool = False) -> None:
        """修改消息段列表。

        Warning:
            在生成了 ``plain_text`` 的情况下调用此方法，可能会导致文本与消息段不一致。

        Args:
            new_segments: 新的消息段列表。
            suppress_warning: 是否抑制潜在不一致警告。
        """
        if self.plain_text and not suppress_warning:
            warnings.warn(
                "修改消息段后，plain_text可能与消息段内容不一致，建议同时更新plain_text",
                UserWarning,
                stacklevel=2,
            )
        self.message_segments = new_segments
        self._modify_flags.modify_message_segments = True

    def modify_llm_prompt(self, new_prompt: str, suppress_warning: bool = False) -> None:
        """修改 LLM 提示词。

        Warning:
            在没有生成 ``llm_prompt`` 的情况下调用此方法，可能会导致修改无效。

        Args:
            new_prompt: 新的提示词内容。
            suppress_warning: 是否抑制潜在无效修改警告。
        """
        if self.llm_prompt is None and not suppress_warning:
            warnings.warn(
                "当前llm_prompt为空，此时调用方法可能导致修改无效",
                UserWarning,
                stacklevel=2,
            )
        self.llm_prompt = new_prompt
        self._modify_flags.modify_llm_prompt = True

    def modify_plain_text(self, new_text: str, suppress_warning: bool = False) -> None:
        """修改生成的纯文本内容。

        Warning:
            在未生成 ``plain_text`` 的情况下调用此方法，可能会导致修改无效。

        Args:
            new_text: 新的纯文本内容。
            suppress_warning: 是否抑制潜在无效修改警告。
        """
        if not self.plain_text and not suppress_warning:
            warnings.warn(
                "当前plain_text为空，此时调用方法可能导致修改无效",
                UserWarning,
                stacklevel=2,
            )
        self.plain_text = new_text
        self._modify_flags.modify_plain_text = True

    def modify_llm_response_content(self, new_content: str, suppress_warning: bool = False) -> None:
        """修改生成的 LLM 响应正文。

        Warning:
            在未生成 ``llm_response_content`` 的情况下调用此方法，可能会导致修改无效。

        Args:
            new_content: 新的 LLM 响应内容。
            suppress_warning: 是否抑制潜在无效修改警告。
        """
        if not self.llm_response_content and not suppress_warning:
            warnings.warn(
                "当前llm_response_content为空，此时调用方法可能导致修改无效",
                UserWarning,
                stacklevel=2,
            )
        self.llm_response_content = new_content
        self._modify_flags.modify_llm_response_content = True

    def modify_llm_response_reasoning(self, new_reasoning: str, suppress_warning: bool = False) -> None:
        """修改生成的 LLM 推理内容。

        Warning:
            在未生成 ``llm_response_reasoning`` 的情况下调用此方法，可能会导致修改无效。

        Args:
            new_reasoning: 新的 LLM 推理内容。
            suppress_warning: 是否抑制潜在无效修改警告。
        """
        if not self.llm_response_reasoning and not suppress_warning:
            warnings.warn(
                "当前llm_response_reasoning为空，此时调用方法可能导致修改无效",
                UserWarning,
                stacklevel=2,
            )
        self.llm_response_reasoning = new_reasoning
        self._modify_flags.modify_llm_response_reasoning = True
