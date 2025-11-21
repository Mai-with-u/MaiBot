# 使用基于时间戳的文件处理器，简单的轮转份数限制

import logging
import json
import threading
import time
import structlog
import tomlkit

from pathlib import Path
from typing import Callable, Optional
from datetime import datetime, timedelta

# 创建logs目录
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
logger_file = Path(__file__).resolve()
PROJECT_ROOT = logger_file.parent.parent.parent.resolve()
# 全局handler实例，避免重复创建
_file_handler = None
_console_handler = None
_ws_handler = None


def get_file_handler():
    """获取文件handler单例"""
    global _file_handler
    if _file_handler is None:
        # 确保日志目录存在
        LOG_DIR.mkdir(exist_ok=True)

        # 检查现有handler，避免重复创建
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, TimestampedFileHandler):
                _file_handler = handler
                return _file_handler

        # 使用基于时间戳的handler，简单的轮转份数限制
        _file_handler = TimestampedFileHandler(
            log_dir=LOG_DIR,
            max_bytes=5 * 1024 * 1024,  # 5MB
            backup_count=30,
            encoding="utf-8",
        )
        # 设置文件handler的日志级别
        file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))
        _file_handler.setLevel(getattr(logging, file_level.upper(), logging.INFO))
    return _file_handler


def get_console_handler():
    """获取控制台handler单例"""
    global _console_handler
    if _console_handler is None:
        _console_handler = logging.StreamHandler()
        # 设置控制台handler的日志级别
        console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
        _console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    return _console_handler


def get_ws_handler():
    """获取 WebSocket handler 单例"""
    global _ws_handler
    if _ws_handler is None:
        _ws_handler = WebSocketLogHandler()
        # WebSocket handler 推送所有级别的日志
        _ws_handler.setLevel(logging.DEBUG)
    return _ws_handler


def initialize_ws_handler(loop):
    """初始化 WebSocket handler 的事件循环

    Args:
        loop: asyncio 事件循环
    """
    handler = get_ws_handler()
    handler.set_loop(loop)

    # 为 WebSocket handler 设置 JSON 格式化器（与文件格式相同）
    handler.setFormatter(file_formatter)

    # 添加到根日志记录器
    root_logger = logging.getLogger()
    if handler not in root_logger.handlers:
        root_logger.addHandler(handler)
        print("[日志系统] ✅ WebSocket 日志推送已启用")


class TimestampedFileHandler(logging.Handler):
    """基于时间戳的文件处理器，简单的轮转份数限制"""

    def __init__(self, log_dir, max_bytes=5 * 1024 * 1024, backup_count=30, encoding="utf-8"):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.encoding = encoding
        self._lock = threading.Lock()

        # 当前活跃的日志文件
        self.current_file = None
        self.current_stream = None
        self._init_current_file()

    def _init_current_file(self):
        """初始化当前日志文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_file = self.log_dir / f"app_{timestamp}.log.jsonl"
        self.current_stream = open(self.current_file, "a", encoding=self.encoding)

    def _should_rollover(self):
        """检查是否需要轮转"""
        if self.current_file and self.current_file.exists():
            return self.current_file.stat().st_size >= self.max_bytes
        return False

    def _do_rollover(self):
        """执行轮转：关闭当前文件，创建新文件"""
        if self.current_stream:
            self.current_stream.close()

        # 清理旧文件
        self._cleanup_old_files()

        # 创建新文件
        self._init_current_file()

    def _cleanup_old_files(self):
        """清理旧的日志文件，保留指定数量"""
        try:
            # 获取所有日志文件
            log_files = list(self.log_dir.glob("app_*.log.jsonl"))

            # 按修改时间排序
            log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

            # 删除超出数量限制的文件
            for old_file in log_files[self.backup_count :]:
                try:
                    old_file.unlink()
                    print(f"[日志清理] 删除旧文件: {old_file.name}")
                except Exception as e:
                    print(f"[日志清理] 删除失败 {old_file}: {e}")

        except Exception as e:
            print(f"[日志清理] 清理过程出错: {e}")

    def emit(self, record):
        """发出日志记录"""
        try:
            with self._lock:
                # 检查是否需要轮转
                if self._should_rollover():
                    self._do_rollover()

                # 写入日志
                if self.current_stream:
                    msg = self.format(record)
                    self.current_stream.write(msg + "\n")
                    self.current_stream.flush()

        except Exception:
            self.handleError(record)

    def close(self):
        """关闭处理器"""
        with self._lock:
            if self.current_stream:
                self.current_stream.close()
                self.current_stream = None
        super().close()


class WebSocketLogHandler(logging.Handler):
    """WebSocket 日志处理器 - 将日志实时推送到前端"""

    _log_counter = 0  # 类级别计数器,确保 ID 唯一性

    def __init__(self, loop=None):
        super().__init__()
        self.loop = loop
        self._initialized = False

    def set_loop(self, loop):
        """设置事件循环"""
        self.loop = loop
        self._initialized = True

    def emit(self, record):
        """发送日志到 WebSocket 客户端"""
        if not self._initialized or self.loop is None:
            return

        try:
            # 获取格式化后的消息
            # 对于 structlog,formatted message 包含完整的日志信息
            formatted_msg = self.format(record) if self.formatter else record.getMessage()

            # 如果是 JSON 格式(文件格式化器),解析它
            message = formatted_msg
            try:
                import json

                log_dict = json.loads(formatted_msg)
                message = log_dict.get("event", formatted_msg)
            except (json.JSONDecodeError, ValueError):
                # 不是 JSON,直接使用消息
                message = formatted_msg

            # 生成唯一 ID: 时间戳毫秒 + 自增计数器
            WebSocketLogHandler._log_counter += 1
            log_id = f"{int(record.created * 1000)}_{WebSocketLogHandler._log_counter}"

            # 格式化日志数据
            log_data = {
                "id": log_id,
                "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "module": record.name,
                "message": message,
            }

            # 异步广播日志(不阻塞日志记录)
            try:
                import asyncio
                from src.webui.logs_ws import broadcast_log

                asyncio.run_coroutine_threadsafe(broadcast_log(log_data), self.loop)
            except Exception:
                # WebSocket 推送失败不影响日志记录
                pass

        except Exception:
            # 不要让 WebSocket 错误影响日志系统
            self.handleError(record)


# 旧的轮转文件处理器已移除，现在使用基于时间戳的处理器


def close_handlers():
    """安全关闭所有handler"""
    global _file_handler, _console_handler, _ws_handler

    if _file_handler:
        _file_handler.close()
        _file_handler = None

    if _console_handler:
        _console_handler.close()
        _console_handler = None

    if _ws_handler:
        _ws_handler.close()
        _ws_handler = None


def remove_duplicate_handlers():  # sourcery skip: for-append-to-extend, list-comprehension
    """移除重复的handler，特别是文件handler"""
    root_logger = logging.getLogger()

    # 收集所有时间戳文件handler
    file_handlers = []
    for handler in root_logger.handlers[:]:
        if isinstance(handler, TimestampedFileHandler):
            file_handlers.append(handler)

    # 如果有多个文件handler，保留第一个，关闭其他的
    if len(file_handlers) > 1:
        print(f"[日志系统] 检测到 {len(file_handlers)} 个重复的文件handler，正在清理...")
        for i, handler in enumerate(file_handlers[1:], 1):
            print(f"[日志系统] 关闭重复的文件handler {i}")
            root_logger.removeHandler(handler)
            handler.close()

        # 更新全局引用
        global _file_handler
        _file_handler = file_handlers[0]


# 读取日志配置
def load_log_config():  # sourcery skip: use-contextlib-suppress
    """从配置文件加载日志设置"""
    config_path = Path("config/bot_config.toml")
    default_config = {
        "date_style": "m-d H:i:s",
        "log_level_style": "lite",
        "color_text": "full",
        "log_level": "INFO",  # 全局日志级别（向下兼容）
        "console_log_level": "INFO",  # 控制台日志级别
        "file_log_level": "DEBUG",  # 文件日志级别
        "suppress_libraries": [
            "faiss",
            "httpx",
            "urllib3",
            "asyncio",
            "websockets",
            "httpcore",
            "requests",
            "peewee",
            "openai",
            "uvicorn",
            "jieba",
        ],
        "library_log_levels": {"aiohttp": "WARNING"},
    }

    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = tomlkit.load(f)
                return config.get("log", default_config)
    except Exception as e:
        print(f"[日志系统] 加载日志配置失败: {e}")
    return default_config


LOG_CONFIG = load_log_config()


def get_timestamp_format():
    """将配置中的日期格式转换为Python格式"""
    date_style = LOG_CONFIG.get("date_style", "Y-m-d H:i:s")
    # 转换PHP风格的日期格式到Python格式
    format_map = {
        "Y": "%Y",  # 4位年份
        "m": "%m",  # 月份（01-12）
        "d": "%d",  # 日期（01-31）
        "H": "%H",  # 小时（00-23）
        "i": "%M",  # 分钟（00-59）
        "s": "%S",  # 秒数（00-59）
    }

    python_format = date_style
    for php_char, python_char in format_map.items():
        python_format = python_format.replace(php_char, python_char)

    return python_format


def configure_third_party_loggers():
    """配置第三方库的日志级别"""
    # 设置根logger级别为所有handler中最低的级别，确保所有日志都能被捕获
    console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
    file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))

    # 获取最低级别（DEBUG < INFO < WARNING < ERROR < CRITICAL）
    console_level_num = getattr(logging, console_level.upper(), logging.INFO)
    file_level_num = getattr(logging, file_level.upper(), logging.INFO)
    min_level = min(console_level_num, file_level_num)

    root_logger = logging.getLogger()
    root_logger.setLevel(min_level)

    # 完全屏蔽的库
    suppress_libraries = LOG_CONFIG.get("suppress_libraries", [])
    for lib_name in suppress_libraries:
        lib_logger = logging.getLogger(lib_name)
        lib_logger.setLevel(logging.CRITICAL + 1)  # 设置为比CRITICAL更高的级别，基本屏蔽所有日志
        lib_logger.propagate = False  # 阻止向上传播

    # 设置特定级别的库
    library_log_levels = LOG_CONFIG.get("library_log_levels", {})
    for lib_name, level_name in library_log_levels.items():
        lib_logger = logging.getLogger(lib_name)
        level = getattr(logging, level_name.upper(), logging.WARNING)
        lib_logger.setLevel(level)


def reconfigure_existing_loggers():
    """重新配置所有已存在的logger，解决加载顺序问题"""
    # 获取根logger
    root_logger = logging.getLogger()

    # 重新设置根logger的所有handler的格式化器
    for handler in root_logger.handlers:
        if isinstance(handler, TimestampedFileHandler):
            handler.setFormatter(file_formatter)
        elif isinstance(handler, logging.StreamHandler):
            handler.setFormatter(console_formatter)

    # 遍历所有已存在的logger并重新配置
    logger_dict = logging.getLogger().manager.loggerDict
    for name, logger_obj in logger_dict.items():
        if isinstance(logger_obj, logging.Logger):
            # 检查是否是第三方库logger
            suppress_libraries = LOG_CONFIG.get("suppress_libraries", [])
            library_log_levels = LOG_CONFIG.get("library_log_levels", {})

            # 如果在屏蔽列表中
            if any(name.startswith(lib) for lib in suppress_libraries):
                logger_obj.setLevel(logging.CRITICAL + 1)
                logger_obj.propagate = False
                continue

            # 如果在特定级别设置中
            for lib_name, level_name in library_log_levels.items():
                if name.startswith(lib_name):
                    level = getattr(logging, level_name.upper(), logging.WARNING)
                    logger_obj.setLevel(level)
                    break

            # 强制清除并重新设置所有handler
            original_handlers = logger_obj.handlers[:]
            for handler in original_handlers:
                # 安全关闭handler
                if hasattr(handler, "close"):
                    handler.close()
                logger_obj.removeHandler(handler)

            # 如果logger没有handler，让它使用根logger的handler（propagate=True）
            if not logger_obj.handlers:
                logger_obj.propagate = True

            # 如果logger有自己的handler，重新配置它们（避免重复创建文件handler）
            for handler in original_handlers:
                if isinstance(handler, TimestampedFileHandler):
                    # 不重新添加，让它使用根logger的文件handler
                    continue
                elif isinstance(handler, logging.StreamHandler):
                    handler.setFormatter(console_formatter)
                    logger_obj.addHandler(handler)


# 定义模块颜色映射
MODULE_COLORS = {
    # 发送
    # "\033[38;5;67m" 这个颜色代码的含义如下：
    # \033         ：转义序列的起始，表示后面是控制字符（ESC）
    # [38;5;67m    ：
    #   38         ：设置前景色（字体颜色），如果是背景色则用 48
    #   5          ：表示使用8位（256色）模式
    #   67         ：具体的颜色编号（0-255），这里是较暗的蓝色
    "sender": "\033[38;5;24m",  # 67号色，较暗的蓝色，适合不显眼的日志
    "send_api": "\033[38;5;24m",  # 208号色，橙色，适合突出显示
    # 生成
    "replyer": "\033[38;5;208m",  # 橙色
    "llm_api": "\033[38;5;208m",  # 橙色
    # 消息处理
    "chat": "\033[38;5;82m",  # 亮蓝色
    "chat_image": "\033[38;5;68m",  # 浅蓝色
    # emoji
    "emoji": "\033[38;5;214m",  # 橙黄色，偏向橙色
    "emoji_api": "\033[38;5;214m",  # 橙黄色，偏向橙色
    # 核心模块
    "main": "\033[1;97m",  # 亮白色+粗体 (主程序)
    "memory": "\033[38;5;34m",  # 天蓝色
    "memory_retrieval": "\033[38;5;34m",  # 天蓝色
    "config": "\033[93m",  # 亮黄色
    "common": "\033[95m",  # 亮紫色
    "tools": "\033[96m",  # 亮青色
    "lpmm": "\033[96m",
    "plugin_system": "\033[91m",  # 亮红色
    "person_info": "\033[32m",  # 绿色
    "manager": "\033[35m",  # 紫色
    "llm_models": "\033[36m",  # 青色
    "remote": "\033[38;5;242m",  # 深灰色，更不显眼
    "planner": "\033[36m",
    "relation": "\033[38;5;139m",  # 柔和的紫色，不刺眼
    # 聊天相关模块
    "hfc": "\033[38;5;175m",  # 柔和的粉色，不显眼但保持粉色系
    "bc": "\033[38;5;175m",  # 柔和的粉色，不显眼但保持粉色系
    "sub_heartflow": "\033[38;5;207m",  # 粉紫色
    "subheartflow_manager": "\033[38;5;201m",  # 深粉色
    "background_tasks": "\033[38;5;240m",  # 灰色
    "chat_message": "\033[38;5;45m",  # 青色
    "chat_stream": "\033[38;5;51m",  # 亮青色
    "message_storage": "\033[38;5;33m",  # 深蓝色
    "expressor": "\033[38;5;166m",  # 橙色
    # jargon相关
    "jargon": "\033[38;5;220m",  # 金黄色，突出显示
    # 插件系统
    "plugins": "\033[31m",  # 红色
    "plugin_api": "\033[33m",  # 黄色
    "plugin_manager": "\033[38;5;208m",  # 红色
    "base_plugin": "\033[38;5;202m",  # 橙红色
    "base_command": "\033[38;5;208m",  # 橙色
    "component_registry": "\033[38;5;214m",  # 橙黄色
    "stream_api": "\033[38;5;220m",  # 黄色
    "config_api": "\033[38;5;226m",  # 亮黄色
    "heartflow_api": "\033[38;5;154m",  # 黄绿色
    "action_apis": "\033[38;5;118m",  # 绿色
    "independent_apis": "\033[38;5;82m",  # 绿色
    "database_api": "\033[38;5;10m",  # 绿色
    "utils_api": "\033[38;5;14m",  # 青色
    "message_api": "\033[38;5;6m",  # 青色
    # 管理器模块
    "async_task_manager": "\033[38;5;129m",  # 紫色
    "mood": "\033[38;5;135m",  # 紫红色
    "local_storage": "\033[38;5;141m",  # 紫色
    "willing": "\033[38;5;147m",  # 浅紫色
    # 工具模块
    "tool_use": "\033[38;5;172m",  # 橙褐色
    "tool_executor": "\033[38;5;172m",  # 橙褐色
    "base_tool": "\033[38;5;178m",  # 金黄色
    # 工具和实用模块
    "prompt_build": "\033[38;5;105m",  # 紫色
    "chat_utils": "\033[38;5;111m",  # 蓝色
    "maibot_statistic": "\033[38;5;129m",  # 紫色
    # 特殊功能插件
    "mute_plugin": "\033[38;5;240m",  # 灰色
    "core_actions": "\033[38;5;117m",  # 深红色
    "tts_action": "\033[38;5;58m",  # 深黄色
    "doubao_pic_plugin": "\033[38;5;64m",  # 深绿色
    # Action组件
    "no_reply_action": "\033[38;5;214m",  # 亮橙色，显眼但不像警告
    "reply_action": "\033[38;5;46m",  # 亮绿色
    "base_action": "\033[38;5;250m",  # 浅灰色
    # 数据库和消息
    "database_model": "\033[38;5;94m",  # 橙褐色
    "maim_message": "\033[38;5;140m",  # 紫褐色
    # 日志系统
    "logger": "\033[38;5;8m",  # 深灰色
    "confirm": "\033[1;93m",  # 黄色+粗体
    # 模型相关
    "model_utils": "\033[38;5;164m",  # 紫红色
    "relationship_fetcher": "\033[38;5;170m",  # 浅紫色
    "relationship_builder": "\033[38;5;93m",  # 浅蓝色
    "conflict_tracker": "\033[38;5;82m",  # 柔和的粉色，不显眼但保持粉色系
}

# 定义模块别名映射 - 将真实的logger名称映射到显示的别名
MODULE_ALIASES = {
    # 示例映射
    "sender": "消息发送",
    "send_api": "消息发送API",
    "replyer": "言语",
    "llm_api": "生成API",
    "emoji": "表情包",
    "emoji_api": "表情包API",
    "chat": "所见",
    "chat_image": "识图",
    "action_manager": "动作",
    "memory_activator": "记忆",
    "tool_use": "工具",
    "expressor": "表达方式",
    "database_model": "数据库",
    "mood": "情绪",
    "memory": "记忆",
    "memory_retrieval": "回忆",
    "tool_executor": "工具",
    "hfc": "聊天节奏",
    "plugin_manager": "插件",
    "relationship_builder": "关系",
    "llm_models": "模型",
    "person_info": "人物",
    "chat_stream": "聊天流",
    "planner": "规划器",
    "config": "配置",
    "main": "主程序",
    "chat_history_summarizer": "聊天概括器",
}

RESET_COLOR = "\033[0m"


def convert_pathname_to_module(logger, method_name, event_dict):
    # sourcery skip: extract-method, use-string-remove-affix
    """将 pathname 转换为模块风格的路径"""
    if "logger_name" in event_dict and event_dict["logger_name"] == "maim_message":
        if "pathname" in event_dict:
            del event_dict["pathname"]
            event_dict["module"] = "maim_message"
        return event_dict
    if "pathname" in event_dict:
        pathname = event_dict["pathname"]
        try:
            # 使用绝对路径确保准确性
            pathname_path = Path(pathname).resolve()
            rel_path = pathname_path.relative_to(PROJECT_ROOT)

            # 转换为模块风格：移除 .py 扩展名，将路径分隔符替换为点
            module_path = str(rel_path).replace("\\", ".").replace("/", ".")
            if module_path.endswith(".py"):
                module_path = module_path[:-3]

            # 使用转换后的模块路径替换 module 字段
            event_dict["module"] = module_path
            # 移除原始的 pathname 字段
            del event_dict["pathname"]
        except Exception:
            # 如果转换失败，删除 pathname 但保留原始的 module（如果有的话）
            del event_dict["pathname"]
            # 如果没有 module 字段，使用文件名作为备选
            if "module" not in event_dict:
                event_dict["module"] = Path(pathname).stem

    return event_dict


class ModuleColoredConsoleRenderer:
    """自定义控制台渲染器，为不同模块提供不同颜色"""

    def __init__(self, colors=True):
        # sourcery skip: merge-duplicate-blocks, remove-redundant-if
        self._colors = colors
        self._config = LOG_CONFIG

        # 日志级别颜色
        self._level_colors = {
            "debug": "\033[38;5;208m",  # 橙色
            "info": "\033[38;5;117m",  # 天蓝色
            "success": "\033[32m",  # 绿色
            "warning": "\033[33m",  # 黄色
            "error": "\033[31m",  # 红色
            "critical": "\033[35m",  # 紫色
        }

        # 根据配置决定是否启用颜色
        color_text = self._config.get("color_text", "title")
        if color_text == "none":
            self._colors = False
        elif color_text == "title":
            self._enable_module_colors = True
            self._enable_level_colors = False
            self._enable_full_content_colors = False
        elif color_text == "full":
            self._enable_module_colors = True
            self._enable_level_colors = True
            self._enable_full_content_colors = True
        else:
            self._enable_module_colors = True
            self._enable_level_colors = False
            self._enable_full_content_colors = False

    def __call__(self, logger, method_name, event_dict):
        # sourcery skip: merge-duplicate-blocks
        """渲染日志消息"""
        # 获取基本信息
        timestamp = event_dict.get("timestamp", "")
        level = event_dict.get("level", "info")
        logger_name = event_dict.get("logger_name", "")
        event = event_dict.get("event", "")

        # 构建输出
        parts = []

        # 日志级别样式配置
        log_level_style = self._config.get("log_level_style", "lite")
        level_color = self._level_colors.get(level.lower(), "") if self._colors else ""

        # 时间戳（lite模式下按级别着色）
        if timestamp:
            if log_level_style == "lite" and level_color:
                timestamp_part = f"{level_color}{timestamp}{RESET_COLOR}"
            else:
                timestamp_part = timestamp
            parts.append(timestamp_part)

        # 日志级别显示（根据配置样式）
        if log_level_style == "full":
            # 显示完整级别名并着色
            level_text = level.upper()
            if level_color:
                level_part = f"{level_color}[{level_text:>8}]{RESET_COLOR}"
            else:
                level_part = f"[{level_text:>8}]"
            parts.append(level_part)

        elif log_level_style == "compact":
            # 只显示首字母并着色
            level_text = level.upper()[0]
            if level_color:
                level_part = f"{level_color}[{level_text:>8}]{RESET_COLOR}"
            else:
                level_part = f"[{level_text:>8}]"
            parts.append(level_part)

        # lite模式不显示级别，只给时间戳着色

        # 获取模块颜色，用于full模式下的整体着色
        module_color = ""
        if self._colors and self._enable_module_colors and logger_name:
            module_color = MODULE_COLORS.get(logger_name, "")

        # 模块名称（带颜色和别名支持）
        if logger_name:
            # 获取别名，如果没有别名则使用原名称
            display_name = MODULE_ALIASES.get(logger_name, logger_name)

            if self._colors and self._enable_module_colors:
                if module_color:
                    module_part = f"{module_color}[{display_name}]{RESET_COLOR}"
                else:
                    module_part = f"[{display_name}]"
            else:
                module_part = f"[{display_name}]"
            parts.append(module_part)

        # 消息内容（确保转换为字符串）
        event_content = ""
        if isinstance(event, str):
            event_content = event
        elif isinstance(event, dict):
            # 如果是字典，格式化为可读字符串
            try:
                event_content = json.dumps(event, ensure_ascii=False, indent=None)
            except (TypeError, ValueError):
                event_content = str(event)
        else:
            # 其他类型直接转换为字符串
            event_content = str(event)

        # 在full模式下为消息内容着色
        if self._colors and self._enable_full_content_colors and module_color:
            event_content = f"{module_color}{event_content}{RESET_COLOR}"

        parts.append(event_content)

        # 处理其他字段
        extras = []
        for key, value in event_dict.items():
            if key not in ("timestamp", "level", "logger_name", "event", "module", "lineno", "pathname"):
                # 确保值也转换为字符串
                if isinstance(value, (dict, list)):
                    try:
                        value_str = json.dumps(value, ensure_ascii=False, indent=None)
                    except (TypeError, ValueError):
                        value_str = str(value)
                else:
                    value_str = str(value)

                # 在full模式下为额外字段着色
                extra_field = f"{key}={value_str}"
                if self._colors and self._enable_full_content_colors and module_color:
                    extra_field = f"{module_color}{extra_field}{RESET_COLOR}"

                extras.append(extra_field)

        if extras:
            parts.append(" ".join(extras))

        return " ".join(parts)


# 配置标准logging以支持文件输出和压缩
# 使用单例handler避免重复创建
file_handler = get_file_handler()
console_handler = get_console_handler()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[file_handler, console_handler],
)


def configure_structlog():
    """配置structlog"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.PATHNAME,
                    structlog.processors.CallsiteParameter.LINENO,
                ]
            ),
            convert_pathname_to_module,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt=get_timestamp_format(), utc=False),
            # 根据输出类型选择不同的渲染器
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# 配置structlog
configure_structlog()

# 为文件输出配置JSON格式
file_formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(ensure_ascii=False),
    foreign_pre_chain=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.CallsiteParameterAdder(
            parameters=[structlog.processors.CallsiteParameter.PATHNAME, structlog.processors.CallsiteParameter.LINENO]
        ),
        convert_pathname_to_module,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ],
)

# 为控制台输出配置可读格式
console_formatter = structlog.stdlib.ProcessorFormatter(
    processor=ModuleColoredConsoleRenderer(colors=True),
    foreign_pre_chain=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt=get_timestamp_format(), utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ],
)

# 获取根logger并配置格式化器
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, TimestampedFileHandler):
        handler.setFormatter(file_formatter)
    else:
        handler.setFormatter(console_formatter)


# 立即配置日志系统，确保最早期的日志也使用正确格式
def _immediate_setup():
    """立即设置日志系统，在模块导入时就生效"""
    # 重新配置structlog
    configure_structlog()

    # 清除所有已有的handler，重新配置
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 使用单例handler避免重复创建
    file_handler = get_file_handler()
    console_handler = get_console_handler()

    # 重新添加配置好的handler
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 设置格式化器
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)

    # 清理重复的handler
    remove_duplicate_handlers()

    # 配置第三方库日志
    configure_third_party_loggers()

    # 重新配置所有已存在的logger
    reconfigure_existing_loggers()


# 立即执行配置
_immediate_setup()

raw_logger: structlog.stdlib.BoundLogger = structlog.get_logger()

binds: dict[str, Callable] = {}


def get_logger(name: Optional[str]) -> structlog.stdlib.BoundLogger:
    """获取logger实例，支持按名称绑定"""
    if name is None:
        return raw_logger
    logger = binds.get(name)  # type: ignore
    if logger is None:
        logger: structlog.stdlib.BoundLogger = structlog.get_logger(name).bind(logger_name=name)
        binds[name] = logger
    return logger


def initialize_logging():
    """手动初始化日志系统，确保所有logger都使用正确的配置

    在应用程序的早期调用此函数，确保所有模块都使用统一的日志配置
    """
    global LOG_CONFIG
    LOG_CONFIG = load_log_config()
    # print(LOG_CONFIG)
    configure_third_party_loggers()
    reconfigure_existing_loggers()

    # 启动日志清理任务
    start_log_cleanup_task()

    # 输出初始化信息
    logger = get_logger("logger")
    console_level = LOG_CONFIG.get("console_log_level", LOG_CONFIG.get("log_level", "INFO"))
    file_level = LOG_CONFIG.get("file_log_level", LOG_CONFIG.get("log_level", "INFO"))

    logger.info("日志系统已初始化:")
    logger.info(f"  - 控制台级别: {console_level}")
    logger.info(f"  - 文件级别: {file_level}")
    logger.info("  - 轮转份数: 30个文件|自动清理: 30天前的日志")


def cleanup_old_logs():
    """清理过期的日志文件"""
    try:
        cleanup_days = 30  # 硬编码30天
        cutoff_date = datetime.now() - timedelta(days=cleanup_days)
        deleted_count = 0
        deleted_size = 0

        # 遍历日志目录
        for log_file in LOG_DIR.glob("*.log*"):
            try:
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_time < cutoff_date:
                    file_size = log_file.stat().st_size
                    log_file.unlink()
                    deleted_count += 1
                    deleted_size += file_size
            except Exception as e:
                logger = get_logger("logger")
                logger.warning(f"清理日志文件 {log_file} 时出错: {e}")

        if deleted_count > 0:
            logger = get_logger("logger")
            logger.info(f"清理了 {deleted_count} 个过期日志文件，释放空间 {deleted_size / 1024 / 1024:.2f} MB")

    except Exception as e:
        logger = get_logger("logger")
        logger.error(f"清理旧日志文件时出错: {e}")


def start_log_cleanup_task():
    """启动日志清理任务"""

    def cleanup_task():
        while True:
            cleanup_old_logs()
            time.sleep(24 * 60 * 60)  # 每24小时执行一次

    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()

    logger = get_logger("logger")
    logger.info("已启动日志清理任务，将自动清理30天前的日志文件（轮转份数限制: 30个文件）")


def shutdown_logging():
    """优雅关闭日志系统，释放所有文件句柄"""
    # 先输出到控制台，避免日志系统关闭后无法输出
    print("[logger] 正在关闭日志系统...")

    # 关闭所有handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if hasattr(handler, "close"):
            handler.close()
        root_logger.removeHandler(handler)

    # 关闭全局handler
    close_handlers()

    # 关闭所有其他logger的handler
    logger_dict = logging.getLogger().manager.loggerDict
    for _name, logger_obj in logger_dict.items():
        if isinstance(logger_obj, logging.Logger):
            for handler in logger_obj.handlers[:]:
                if hasattr(handler, "close"):
                    handler.close()
                logger_obj.removeHandler(handler)

    # 使用 print 而不是 logger，因为 logger 已经关闭
    print("[logger] 日志系统已关闭")
