"""外接 ACP Agent 接口与可选电脑工具配置。"""

from typing import Dict, List, Literal

from src.config.config_base import ConfigBase, Field


class AgentConfig(ConfigBase):
    __ui_label__ = "Agent连接"

    enabled: bool = Field(default=False, json_schema_extra={"label": {"zh_CN": "启用 Agent连接"}})
    """启用与普通聊天并行的外接 Agent 接口，通过 ACP 连接 dsh 等外部程序。"""

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
    """仅自定义 ACP 使用，逐项填写参数；dsh 预设忽略此项并自动生成 --profile acp。"""

    acp_cwd: str = Field(default="", json_schema_extra={"label": {"zh_CN": "ACP 主机工作目录"}})
    """外部进程的主机工作目录，留空使用 data/agent/workspace；不是沙盒 C:\\MaiWorkspace。同一会话恢复时保持不变。"""

    acp_env: Dict[str, str] = Field(default_factory=dict, json_schema_extra={"label": {"zh_CN": "ACP 环境变量"}, "advanced": True})
    """追加或覆盖外部进程环境变量，例如 DSH_HOME；模型凭据建议保存在 dsh 自身配置中。"""

    acp_model: str = Field(default="", json_schema_extra={"label": {"zh_CN": "ACP 模型选项"}, "advanced": True})
    """留空使用外部 Agent 自身模型配置；填写时必须是其 model 配置选项公布的值，不使用本体 planner 回退。"""

    acp_timeout: float = Field(default=60, gt=0, json_schema_extra={"label": {"zh_CN": "ACP 连接与关停超时（秒）"}, "advanced": True})
    """握手、会话恢复和取消确认的等待时间，不限制任务执行时长。"""

    computer_tools: bool = Field(default=False, json_schema_extra={"label": {"zh_CN": "提供电脑工具"}})
    """是否向外接 Agent 提供沙盒桌面工具及 WebUI 接管界面；人工接管期间不执行 Agent 输入。"""

    networking: bool = Field(default=True, json_schema_extra={"label": {"zh_CN": "允许沙盒联网"}})
    """允许沙盒联网；修改后在下一次启动沙盒时生效。"""

    memory_mb: int = Field(default=4096, ge=2048, json_schema_extra={"label": {"zh_CN": "沙盒内存（MB）"}, "advanced": True})
    """沙盒内存 MB，下次启动沙盒时生效。"""

    startup_timeout: float = Field(default=120, gt=0, json_schema_extra={"label": {"zh_CN": "启动超时（秒）"}, "advanced": True})
    """等待沙盒启动的超时秒数。"""

    request_timeout: float = Field(default=60, gt=0, json_schema_extra={"label": {"zh_CN": "操作等待超时（秒）"}, "advanced": True})
    """等待代理回复的超时秒数；超时不重放操作，可继续领取原请求结果。"""

    screenshot_max_edge: int = Field(default=1600, ge=0, json_schema_extra={"label": {"zh_CN": "截图最长边（像素）"}, "advanced": True})
    """截图最长边，0 表示原始分辨率；鼠标坐标始终按返回截图换算。"""

