"""外接 ACP Agent 连接配置。"""

from typing import Dict, List, Literal

from src.config.config_base import ConfigBase, Field


class AgentConfig(ConfigBase):
    __ui_parent__ = "experimental"
    __ui_label__ = "外接 Agent"

    enabled: bool = Field(default=False, json_schema_extra={"label": {"zh_CN": "启用 Agent连接"}})
    """启用与普通聊天并行的外接 Agent 接口，通过 ACP 连接 dsh 等外部程序。"""

    allow_users: List[str] = Field(default_factory=list, json_schema_extra={
        "label": {"zh_CN": "允许指挥 Agent 的私聊用户"},
    })
    """填写后只允许这些用户私聊指挥外接 Agent，格式为 platform:user_id，例如 qq:123456789。
    群聊不区分具体成员，需要放开某个群时改用 allow_groups。
    allow_users 与 allow_groups 都为空时不限制任何聊天；填写任意一个后，未命中的聊天不能指挥 Agent。
    """

    allow_groups: List[str] = Field(default_factory=list, json_schema_extra={
        "label": {"zh_CN": "允许指挥 Agent 的群聊"},
    })
    """填写后只允许这些群聊指挥外接 Agent，格式为 platform:group_id，例如 qq:987654321。
    allow_users 与 allow_groups 都为空时不限制任何聊天；填写任意一个后按白名单放行，未命中的聊天不再向 Planner 暴露 Agent 工具。
    只包含空白项的配置会被视为白名单为空并拒绝指挥，不会退回不限制。
    """

    planner_enabled: bool = Field(default=False, json_schema_extra={
        "label": {"zh_CN": "启用外接 Planner（实验）"}, "x-widget": "switch",
    })
    """实验性外接 Planner 总开关，默认关闭。开启后仅对下方名单中的聊天流生效，无需启用独立 Agent 连接；关闭时保留名单。"""

    planner_sessions: List[str] = Field(default_factory=list, json_schema_extra={
        "label": {"zh_CN": "实验：外接 Planner 聊天流"}, "advanced": True,
    })
    """填写真实聊天流 session_id，开启外接 Planner 总开关后，仅这些聊天的主 Planner 使用 ACP；空列表不启用任何聊天流。
    复用下方连接配置，每个聊天流使用持久会话并增量同步上下文；重启恢复，清空聊天后重置。
    工具由原聊天运行时执行。无需启用独立 Agent 队列。
    连接失败直接报错，不回退本体 Planner；外部 Agent 自带工具仍受其自身配置控制。
    """

    connection: Literal["dsh", "dsh_desktop", "custom"] = Field(default="dsh", json_schema_extra={
        "label": {"zh_CN": "连接方式"}, "x-widget": "select",
        "options": ["dsh", "dsh_desktop", "custom"], "x-option-labels": {"dsh": "dsh CLI", "dsh_desktop": "dsh Desktop", "custom": "自定义 ACP"},
    })
    """dsh CLI 自动使用 --profile acp；dsh Desktop 连接正在运行的桌面应用，在界面中显示同一会话；自定义用于其他 ACP 程序。模型与凭据使用外部 Agent 自身配置。"""

    desktop_manifest: str = Field(default="", json_schema_extra={"label": {"zh_CN": "Desktop 桥接文件"}, "advanced": True})
    """dsh Desktop 模式使用，留空读取 ~/.dsh/maibot-desktop.json。先在 Desktop 安装 dsh-maibot-bridge 插件；不启动第二个 dsh 进程，不关闭 Desktop 应用。"""

    acp_command: str = Field(default="dsh", json_schema_extra={"label": {"zh_CN": "ACP 启动程序"}})
    """外部 Agent 可执行程序或绝对路径，不是整段 shell 命令；Windows 可填写 dsh.cmd。"""

    acp_args: List[str] = Field(default_factory=list, json_schema_extra={
        "label": {"zh_CN": "自定义启动参数"}, "advanced": True,
    })
    """仅自定义 ACP 使用，逐项填写参数；{mcp_port} 替换为本次工具服务端口，可用于 SSH -R 反向转发。dsh 预设忽略此项并自动生成 --profile acp。"""

    acp_cwd: str = Field(default="", json_schema_extra={"label": {"zh_CN": "ACP 主机工作目录"}})
    """麦麦所在主机上启动 ACP 程序（或 ssh）的工作目录，留空使用 data/agent/workspace；不是沙盒 C:\\MaiWorkspace。同一会话恢复时保持不变。"""

    acp_remote_cwd: str = Field(default="", json_schema_extra={
        "label": {"zh_CN": "ACP 远程会话工作目录"}, "advanced": True,
    })
    """远程自定义 ACP 或经 SSH 隧道连接 Desktop 时填写远端已存在的绝对路径，原样传给外部会话，不在本机创建或展开。留空使用本机工作目录；远程工具回连需配置 SSH 反向转发，详见 docs/agent-connections.md。"""

    mcp_port: int = Field(default=0, ge=0, le=65535, json_schema_extra={
        "label": {"zh_CN": "Agent 工具回连端口"}, "advanced": True,
    })
    """默认 0 自动分配本机回环端口；Desktop SSH 隧道可设固定端口并自行反向转发。同一端口只能用于一个连接；多聊天外接 Planner 应保留 0 并使用 {mcp_port}。"""

    acp_env: Dict[str, str] = Field(default_factory=dict, json_schema_extra={"label": {"zh_CN": "ACP 环境变量"}, "advanced": True})
    """追加或覆盖外部进程环境变量，例如 DSH_HOME；模型凭据建议保存在 dsh 自身配置中。"""

    acp_model: str = Field(default="", json_schema_extra={"label": {"zh_CN": "ACP 模型选项"}, "advanced": True})
    """留空使用外部 Agent 自身模型配置；填写时必须是其 model 配置选项公布的值，不使用本体 planner 回退。"""

    acp_timeout: float = Field(default=60, gt=0, json_schema_extra={"label": {"zh_CN": "ACP 连接与关停超时（秒）"}, "advanced": True})
    """握手、会话恢复和取消确认的等待时间，不限制任务执行时长。"""

