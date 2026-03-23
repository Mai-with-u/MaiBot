"""插件运行时包

定义 Host ↔ Runner 子进程间传递的环境变量名称常量。
这些环境变量用于子进程 IPC 通信，值在运行时动态生成。
"""

# Host 端在 spawn Runner 子进程时设置、Runner 端启动时读取的环境变量名
ENV_IPC_ADDRESS = "MAIBOT_IPC_ADDRESS"
"""IPC 传输层监听地址（UDS socket 路径或 TCP host:port）"""

ENV_SESSION_TOKEN = "MAIBOT_SESSION_TOKEN"
"""本次会话的认证令牌（每次 spawn / reload 重新生成）"""

ENV_PLUGIN_DIRS = "MAIBOT_PLUGIN_DIRS"
"""Runner 需要加载的插件目录列表（os.pathsep 分隔）"""

ENV_HOST_VERSION = "MAIBOT_HOST_VERSION"
"""Runner 读取的 Host 应用版本号，用于 manifest 兼容性校验"""

ENV_EXTERNAL_PLUGIN_IDS = "MAIBOT_EXTERNAL_PLUGIN_IDS"
"""Runner 启动时可视为已满足的外部插件依赖版本映射（JSON 对象）"""

ENV_GLOBAL_CONFIG_SNAPSHOT = "MAIBOT_GLOBAL_CONFIG_SNAPSHOT"
"""Runner 启动时注入的全局配置快照（JSON 对象）"""
