"""
配置管理API路由
"""

import os
import tomlkit
from fastapi import APIRouter, HTTPException, Body
from typing import Any, Annotated

from src.common.logger import get_logger
from src.common.toml_utils import save_toml_with_format
from src.config.config import Config, APIAdapterConfig, CONFIG_DIR, PROJECT_ROOT
from src.config.official_configs import (
    BotConfig,
    PersonalityConfig,
    RelationshipConfig,
    ChatConfig,
    MessageReceiveConfig,
    EmojiConfig,
    ExpressionConfig,
    KeywordReactionConfig,
    ChineseTypoConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    ExperimentalConfig,
    MaimMessageConfig,
    LPMMKnowledgeConfig,
    ToolConfig,
    MemoryConfig,
    DebugConfig,
    MoodConfig,
    VoiceConfig,
    JargonConfig,
)
from src.config.api_ada_configs import (
    ModelTaskConfig,
    ModelInfo,
    APIProvider,
)
from src.webui.config_schema import ConfigSchemaGenerator

logger = get_logger("webui")

# 模块级别的类型别名（解决 B008 ruff 错误）
ConfigBody = Annotated[dict[str, Any], Body()]
SectionBody = Annotated[Any, Body()]
RawContentBody = Annotated[str, Body(embed=True)]
PathBody = Annotated[dict[str, str], Body()]

router = APIRouter(prefix="/config", tags=["config"])


# ===== 辅助函数 =====


def _update_dict_preserve_comments(target: Any, source: Any) -> None:
    """
    递归合并字典，保留 target 中的注释和格式
    将 source 的值更新到 target 中（仅更新已存在的键）

    Args:
        target: 目标字典（tomlkit 对象，包含注释）
        source: 源字典（普通 dict 或 list）
    """
    # 如果 source 是列表，直接替换（数组表没有注释保留的意义）
    if isinstance(source, list):
        return  # 调用者需要直接赋值

    # 如果都是字典，递归合并
    if isinstance(source, dict) and isinstance(target, dict):
        for key, value in source.items():
            if key == "version":
                continue  # 跳过版本号
            if key in target:
                target_value = target[key]
                # 递归处理嵌套字典
                if isinstance(value, dict) and isinstance(target_value, dict):
                    _update_dict_preserve_comments(target_value, value)
                else:
                    # 使用 tomlkit.item 保持类型
                    try:
                        target[key] = tomlkit.item(value)
                    except (TypeError, ValueError):
                        target[key] = value


# ===== 架构获取接口 =====


@router.get("/schema/bot")
async def get_bot_config_schema():
    """获取麦麦主程序配置架构"""
    try:
        # Config 类包含所有子配置
        schema = ConfigSchemaGenerator.generate_config_schema(Config)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置架构失败: {str(e)}") from e


@router.get("/schema/model")
async def get_model_config_schema():
    """获取模型配置架构（包含提供商和模型任务配置）"""
    try:
        schema = ConfigSchemaGenerator.generate_config_schema(APIAdapterConfig)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取模型配置架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取模型配置架构失败: {str(e)}") from e


# ===== 子配置架构获取接口 =====


@router.get("/schema/section/{section_name}")
async def get_config_section_schema(section_name: str):
    """
    获取指定配置节的架构

    支持的section_name:
    - bot: BotConfig
    - personality: PersonalityConfig
    - relationship: RelationshipConfig
    - chat: ChatConfig
    - message_receive: MessageReceiveConfig
    - emoji: EmojiConfig
    - expression: ExpressionConfig
    - keyword_reaction: KeywordReactionConfig
    - chinese_typo: ChineseTypoConfig
    - response_post_process: ResponsePostProcessConfig
    - response_splitter: ResponseSplitterConfig
    - telemetry: TelemetryConfig
    - experimental: ExperimentalConfig
    - maim_message: MaimMessageConfig
    - lpmm_knowledge: LPMMKnowledgeConfig
    - tool: ToolConfig
    - memory: MemoryConfig
    - debug: DebugConfig
    - mood: MoodConfig
    - voice: VoiceConfig
    - jargon: JargonConfig
    - model_task_config: ModelTaskConfig
    - api_provider: APIProvider
    - model_info: ModelInfo
    """
    section_map = {
        "bot": BotConfig,
        "personality": PersonalityConfig,
        "relationship": RelationshipConfig,
        "chat": ChatConfig,
        "message_receive": MessageReceiveConfig,
        "emoji": EmojiConfig,
        "expression": ExpressionConfig,
        "keyword_reaction": KeywordReactionConfig,
        "chinese_typo": ChineseTypoConfig,
        "response_post_process": ResponsePostProcessConfig,
        "response_splitter": ResponseSplitterConfig,
        "telemetry": TelemetryConfig,
        "experimental": ExperimentalConfig,
        "maim_message": MaimMessageConfig,
        "lpmm_knowledge": LPMMKnowledgeConfig,
        "tool": ToolConfig,
        "memory": MemoryConfig,
        "debug": DebugConfig,
        "mood": MoodConfig,
        "voice": VoiceConfig,
        "jargon": JargonConfig,
        "model_task_config": ModelTaskConfig,
        "api_provider": APIProvider,
        "model_info": ModelInfo,
    }

    if section_name not in section_map:
        raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

    try:
        config_class = section_map[section_name]
        schema = ConfigSchemaGenerator.generate_schema(config_class, include_nested=False)
        return {"success": True, "schema": schema}
    except Exception as e:
        logger.error(f"获取配置节架构失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置节架构失败: {str(e)}") from e


# ===== 配置读取接口 =====


@router.get("/bot")
async def get_bot_config():
    """获取麦麦主程序配置"""
    try:
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        return {"success": True, "config": config_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


@router.get("/model")
async def get_model_config():
    """获取模型配置（包含提供商和模型任务配置）"""
    try:
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        return {"success": True, "config": config_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


# ===== 配置更新接口 =====


@router.post("/bot")
async def update_bot_config(config_data: ConfigBody):
    """更新麦麦主程序配置"""
    try:
        # 验证配置数据
        try:
            Config.from_dict(config_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件（格式化数组为多行）
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        save_toml_with_format(config_data, config_path)

        logger.info("麦麦主程序配置已更新")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


@router.post("/model")
async def update_model_config(config_data: ConfigBody):
    """更新模型配置"""
    try:
        # 验证配置数据
        try:
            APIAdapterConfig.from_dict(config_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件（格式化数组为多行）
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        save_toml_with_format(config_data, config_path)

        logger.info("模型配置已更新")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


# ===== 配置节更新接口 =====


@router.post("/bot/section/{section_name}")
async def update_bot_config_section(section_name: str, section_data: SectionBody):
    """更新麦麦主程序配置的指定节（保留注释和格式）"""
    try:
        # 读取现有配置
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        # 更新指定节
        if section_name not in config_data:
            raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

        # 使用递归合并保留注释（对于字典类型）
        # 对于数组类型（如 platforms, aliases），直接替换
        if isinstance(section_data, list):
            # 列表直接替换
            config_data[section_name] = section_data
        elif isinstance(section_data, dict) and isinstance(config_data[section_name], dict):
            # 字典递归合并
            _update_dict_preserve_comments(config_data[section_name], section_data)
        else:
            # 其他类型直接替换
            config_data[section_name] = section_data

        # 验证完整配置
        try:
            Config.from_dict(config_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置（格式化数组为多行，保留注释）
        save_toml_with_format(config_data, config_path)

        logger.info(f"配置节 '{section_name}' 已更新（保留注释）")
        return {"success": True, "message": f"配置节 '{section_name}' 已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新配置节失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置节失败: {str(e)}") from e


# ===== 原始 TOML 文件操作接口 =====


@router.get("/bot/raw")
async def get_bot_config_raw():
    """获取麦麦主程序配置的原始 TOML 内容"""
    try:
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            raw_content = f.read()

        return {"success": True, "content": raw_content}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {str(e)}") from e


@router.post("/bot/raw")
async def update_bot_config_raw(raw_content: RawContentBody):
    """更新麦麦主程序配置（直接保存原始 TOML 内容，会先验证格式）"""
    try:
        # 验证 TOML 格式
        try:
            config_data = tomlkit.loads(raw_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        # 验证配置数据结构
        try:
            Config.from_dict(config_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置文件
        config_path = os.path.join(CONFIG_DIR, "bot_config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(raw_content)

        logger.info("麦麦主程序配置已更新（原始模式）")
        return {"success": True, "message": "配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}") from e


@router.post("/model/section/{section_name}")
async def update_model_config_section(section_name: str, section_data: SectionBody):
    """更新模型配置的指定节（保留注释和格式）"""
    try:
        # 读取现有配置
        config_path = os.path.join(CONFIG_DIR, "model_config.toml")
        if not os.path.exists(config_path):
            raise HTTPException(status_code=404, detail="配置文件不存在")

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = tomlkit.load(f)

        # 更新指定节
        if section_name not in config_data:
            raise HTTPException(status_code=404, detail=f"配置节 '{section_name}' 不存在")

        # 使用递归合并保留注释（对于字典类型）
        # 对于数组表（如 [[models]], [[api_providers]]），直接替换
        if isinstance(section_data, list):
            # 列表直接替换
            config_data[section_name] = section_data
        elif isinstance(section_data, dict) and isinstance(config_data[section_name], dict):
            # 字典递归合并
            _update_dict_preserve_comments(config_data[section_name], section_data)
        else:
            # 其他类型直接替换
            config_data[section_name] = section_data

        # 验证完整配置
        try:
            APIAdapterConfig.from_dict(config_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"配置数据验证失败: {str(e)}") from e

        # 保存配置（格式化数组为多行，保留注释）
        save_toml_with_format(config_data, config_path)

        logger.info(f"配置节 '{section_name}' 已更新（保留注释）")
        return {"success": True, "message": f"配置节 '{section_name}' 已保存"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新配置节失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置节失败: {str(e)}") from e


# ===== 适配器配置管理接口 =====


def _normalize_adapter_path(path: str) -> str:
    """将路径转换为绝对路径（如果是相对路径，则相对于项目根目录）"""
    if not path:
        return path

    # 如果已经是绝对路径，直接返回
    if os.path.isabs(path):
        return path

    # 相对路径，转换为相对于项目根目录的绝对路径
    return os.path.normpath(os.path.join(PROJECT_ROOT, path))


def _to_relative_path(path: str) -> str:
    """尝试将绝对路径转换为相对于项目根目录的相对路径，如果无法转换则返回原路径"""
    if not path or not os.path.isabs(path):
        return path

    try:
        # 尝试获取相对路径
        rel_path = os.path.relpath(path, PROJECT_ROOT)
        # 如果相对路径不是以 .. 开头（说明文件在项目目录内），则返回相对路径
        if not rel_path.startswith(".."):
            return rel_path
    except (ValueError, TypeError):
        # 在 Windows 上，如果路径在不同驱动器，relpath 会抛出 ValueError
        pass

    # 无法转换为相对路径，返回绝对路径
    return path


@router.get("/adapter-config/path")
async def get_adapter_config_path():
    """获取保存的适配器配置文件路径"""
    try:
        # 从 data/webui.json 读取路径偏好
        webui_data_path = os.path.join("data", "webui.json")
        if not os.path.exists(webui_data_path):
            return {"success": True, "path": None}

        import json

        with open(webui_data_path, "r", encoding="utf-8") as f:
            webui_data = json.load(f)

        adapter_config_path = webui_data.get("adapter_config_path")
        if not adapter_config_path:
            return {"success": True, "path": None}

        # 将路径规范化为绝对路径
        abs_path = _normalize_adapter_path(adapter_config_path)

        # 检查文件是否存在并返回最后修改时间
        if os.path.exists(abs_path):
            import datetime

            mtime = os.path.getmtime(abs_path)
            last_modified = datetime.datetime.fromtimestamp(mtime).isoformat()
            # 返回相对路径（如果可能）
            display_path = _to_relative_path(abs_path)
            return {"success": True, "path": display_path, "lastModified": last_modified}
        else:
            # 文件不存在，返回原路径
            return {"success": True, "path": adapter_config_path, "lastModified": None}

    except Exception as e:
        logger.error(f"获取适配器配置路径失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取配置路径失败: {str(e)}") from e


@router.post("/adapter-config/path")
async def save_adapter_config_path(data: PathBody):
    """保存适配器配置文件路径偏好"""
    try:
        path = data.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="路径不能为空")

        # 保存到 data/webui.json
        webui_data_path = os.path.join("data", "webui.json")
        import json

        # 读取现有数据
        if os.path.exists(webui_data_path):
            with open(webui_data_path, "r", encoding="utf-8") as f:
                webui_data = json.load(f)
        else:
            webui_data = {}

        # 将路径规范化为绝对路径
        abs_path = _normalize_adapter_path(path)

        # 尝试转换为相对路径保存（如果文件在项目目录内）
        save_path = _to_relative_path(abs_path)

        # 更新路径
        webui_data["adapter_config_path"] = save_path

        # 保存
        os.makedirs("data", exist_ok=True)
        with open(webui_data_path, "w", encoding="utf-8") as f:
            json.dump(webui_data, f, ensure_ascii=False, indent=2)

        logger.info(f"适配器配置路径已保存: {save_path}（绝对路径: {abs_path}）")
        return {"success": True, "message": "路径已保存"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存适配器配置路径失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存路径失败: {str(e)}") from e


@router.get("/adapter-config")
async def get_adapter_config(path: str):
    """从指定路径读取适配器配置文件"""
    try:
        if not path:
            raise HTTPException(status_code=400, detail="路径参数不能为空")

        # 将路径规范化为绝对路径
        abs_path = _normalize_adapter_path(path)

        # 检查文件是否存在
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail=f"配置文件不存在: {path}")

        # 检查文件扩展名
        if not abs_path.endswith(".toml"):
            raise HTTPException(status_code=400, detail="只支持 .toml 格式的配置文件")

        # 读取文件内容
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()

        logger.info(f"已读取适配器配置: {path} (绝对路径: {abs_path})")
        return {"success": True, "content": content}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取适配器配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取配置失败: {str(e)}") from e


@router.post("/adapter-config")
async def save_adapter_config(data: PathBody):
    """保存适配器配置到指定路径"""
    try:
        path = data.get("path")
        content = data.get("content")

        if not path:
            raise HTTPException(status_code=400, detail="路径不能为空")
        if content is None:
            raise HTTPException(status_code=400, detail="配置内容不能为空")

        # 将路径规范化为绝对路径
        abs_path = _normalize_adapter_path(path)

        # 检查文件扩展名
        if not abs_path.endswith(".toml"):
            raise HTTPException(status_code=400, detail="只支持 .toml 格式的配置文件")

        # 验证 TOML 格式
        try:
            tomlkit.loads(content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        # 确保目录存在
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # 保存文件
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"适配器配置已保存: {path} (绝对路径: {abs_path})")
        return {"success": True, "message": "配置已保存"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存适配器配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}") from e
