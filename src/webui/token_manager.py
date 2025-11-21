"""
WebUI Token 管理模块
负责生成、保存、验证和更新访问令牌
"""

import json
import secrets
from pathlib import Path
from typing import Optional

from src.common.logger import get_logger

logger = get_logger("webui")


class TokenManager:
    """Token 管理器"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        初始化 Token 管理器

        Args:
            config_path: 配置文件路径，默认为项目根目录的 data/webui.json
        """
        if config_path is None:
            # 获取项目根目录 (src/webui -> src -> 根目录)
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "data" / "webui.json"

        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        # 确保配置文件存在并包含有效的 token
        self._ensure_config()

    def _ensure_config(self):
        """确保配置文件存在且包含有效的 token"""
        if not self.config_path.exists():
            logger.info(f"WebUI 配置文件不存在，正在创建: {self.config_path}")
            self._create_new_token()
        else:
            # 验证配置文件格式
            try:
                config = self._load_config()
                if not config.get("access_token"):
                    logger.warning("WebUI 配置文件中缺少 access_token，正在重新生成")
                    self._create_new_token()
                else:
                    logger.info(f"WebUI Token 已加载: {config['access_token'][:8]}...")
            except Exception as e:
                logger.error(f"读取 WebUI 配置文件失败: {e}，正在重新创建")
                self._create_new_token()

    def _load_config(self) -> dict:
        """加载配置文件"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载 WebUI 配置失败: {e}")
            return {}

    def _save_config(self, config: dict):
        """保存配置文件"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"WebUI 配置已保存到: {self.config_path}")
        except Exception as e:
            logger.error(f"保存 WebUI 配置失败: {e}")
            raise

    def _create_new_token(self) -> str:
        """生成新的 64 位随机 token"""
        # 生成 64 位十六进制字符串 (32 字节 = 64 hex 字符)
        token = secrets.token_hex(32)

        config = {
            "access_token": token,
            "created_at": self._get_current_timestamp(),
            "updated_at": self._get_current_timestamp(),
            "first_setup_completed": False,  # 标记首次配置未完成
        }

        self._save_config(config)
        logger.info(f"新的 WebUI Token 已生成: {token[:8]}...")

        return token

    def _get_current_timestamp(self) -> str:
        """获取当前时间戳字符串"""
        from datetime import datetime

        return datetime.now().isoformat()

    def get_token(self) -> str:
        """获取当前有效的 token"""
        config = self._load_config()
        return config.get("access_token", "")

    def verify_token(self, token: str) -> bool:
        """
        验证 token 是否有效

        Args:
            token: 待验证的 token

        Returns:
            bool: token 是否有效
        """
        if not token:
            return False

        current_token = self.get_token()
        if not current_token:
            logger.error("系统中没有有效的 token")
            return False

        # 使用 secrets.compare_digest 防止时序攻击
        is_valid = secrets.compare_digest(token, current_token)

        if is_valid:
            logger.debug("Token 验证成功")
        else:
            logger.warning("Token 验证失败")

        return is_valid

    def update_token(self, new_token: str) -> tuple[bool, str]:
        """
        更新 token

        Args:
            new_token: 新的 token (最少 10 位，必须包含大小写字母和特殊符号)

        Returns:
            tuple[bool, str]: (是否更新成功, 错误消息)
        """
        # 验证新 token 格式
        is_valid, error_msg = self._validate_custom_token(new_token)
        if not is_valid:
            logger.error(f"Token 格式无效: {error_msg}")
            return False, error_msg

        try:
            config = self._load_config()
            old_token = config.get("access_token", "")[:8]

            config["access_token"] = new_token
            config["updated_at"] = self._get_current_timestamp()

            self._save_config(config)
            logger.info(f"Token 已更新: {old_token}... -> {new_token[:8]}...")

            return True, "Token 更新成功"
        except Exception as e:
            logger.error(f"更新 Token 失败: {e}")
            return False, f"更新失败: {str(e)}"

    def regenerate_token(self) -> str:
        """
        重新生成 token

        Returns:
            str: 新生成的 token
        """
        logger.info("正在重新生成 WebUI Token...")
        return self._create_new_token()

    def _validate_token_format(self, token: str) -> bool:
        """
        验证 token 格式是否正确（旧的 64 位十六进制验证，保留用于系统生成的 token）

        Args:
            token: 待验证的 token

        Returns:
            bool: 格式是否正确
        """
        if not token or not isinstance(token, str):
            return False

        # 必须是 64 位十六进制字符串
        if len(token) != 64:
            return False

        # 验证是否为有效的十六进制字符串
        try:
            int(token, 16)
            return True
        except ValueError:
            return False

    def _validate_custom_token(self, token: str) -> tuple[bool, str]:
        """
        验证自定义 token 格式

        要求:
        - 最少 10 位
        - 包含大写字母
        - 包含小写字母
        - 包含特殊符号

        Args:
            token: 待验证的 token

        Returns:
            tuple[bool, str]: (是否有效, 错误消息)
        """
        if not token or not isinstance(token, str):
            return False, "Token 不能为空"

        # 检查长度
        if len(token) < 10:
            return False, "Token 长度至少为 10 位"

        # 检查是否包含大写字母
        has_upper = any(c.isupper() for c in token)
        if not has_upper:
            return False, "Token 必须包含大写字母"

        # 检查是否包含小写字母
        has_lower = any(c.islower() for c in token)
        if not has_lower:
            return False, "Token 必须包含小写字母"

        # 检查是否包含特殊符号
        special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?/"
        has_special = any(c in special_chars for c in token)
        if not has_special:
            return False, f"Token 必须包含特殊符号 ({special_chars})"

        return True, "Token 格式正确"

    def is_first_setup(self) -> bool:
        """
        检查是否为首次配置

        Returns:
            bool: 是否为首次配置
        """
        config = self._load_config()
        return not config.get("first_setup_completed", False)

    def mark_setup_completed(self) -> bool:
        """
        标记首次配置已完成

        Returns:
            bool: 是否标记成功
        """
        try:
            config = self._load_config()
            config["first_setup_completed"] = True
            config["setup_completed_at"] = self._get_current_timestamp()
            self._save_config(config)
            logger.info("首次配置已标记为完成")
            return True
        except Exception as e:
            logger.error(f"标记首次配置完成失败: {e}")
            return False

    def reset_setup_status(self) -> bool:
        """
        重置首次配置状态，允许重新进入配置向导

        Returns:
            bool: 是否重置成功
        """
        try:
            config = self._load_config()
            config["first_setup_completed"] = False
            if "setup_completed_at" in config:
                del config["setup_completed_at"]
            self._save_config(config)
            logger.info("首次配置状态已重置")
            return True
        except Exception as e:
            logger.error(f"重置首次配置状态失败: {e}")
            return False


# 全局单例
_token_manager_instance: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    """获取 TokenManager 单例"""
    global _token_manager_instance
    if _token_manager_instance is None:
        _token_manager_instance = TokenManager()
    return _token_manager_instance
