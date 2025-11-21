import random
from typing import Tuple

# 导入新插件系统
from src.plugin_system import BaseAction, ActionActivationType

# 导入依赖的系统组件
from src.common.logger import get_logger

# 导入API模块 - 标准Python包方式
from src.plugin_system.apis import emoji_api, llm_api, message_api

# NoReplyAction已集成到heartFC_chat.py中，不再需要导入
from src.config.config import global_config


logger = get_logger("emoji")


class EmojiAction(BaseAction):
    """表情动作 - 发送表情包"""

    activation_type = ActionActivationType.RANDOM
    random_activation_probability = global_config.emoji.emoji_chance
    parallel_action = True

    # 动作基本信息
    action_name = "emoji"
    action_description = "发送表情包辅助表达情绪"

    # 动作参数定义
    action_parameters = {}

    # 动作使用场景
    action_require = [
        "发送表情包辅助表达情绪",
        "表达情绪时可以选择使用",
        "不要连续发送，如果你已经发过[表情包]，就不要选择此动作",
    ]

    # 关联类型
    associated_types = ["emoji"]

    async def execute(self) -> Tuple[bool, str]:
        # sourcery skip: assign-if-exp, introduce-default-else, swap-if-else-branches, use-named-expression
        """执行表情动作"""
        try:
            # 1. 获取发送表情的原因
            # reason = self.action_data.get("reason", "表达当前情绪")
            reason = self.reasoning

            # 2. 随机获取20个表情包
            sampled_emojis = await emoji_api.get_random(30)
            if not sampled_emojis:
                logger.warning(f"{self.log_prefix} 无法获取随机表情包")
                return False, "无法获取随机表情包"

            # 3. 准备情感数据
            emotion_map = {}
            for b64, desc, emo in sampled_emojis:
                if emo not in emotion_map:
                    emotion_map[emo] = []
                emotion_map[emo].append((b64, desc))

            available_emotions = list(emotion_map.keys())
            available_emotions_str = ""
            for emotion in available_emotions:
                available_emotions_str += f"{emotion}\n"

            if not available_emotions:
                logger.warning(f"{self.log_prefix} 获取到的表情包均无情感标签, 将随机发送")
                emoji_base64, emoji_description, _ = random.choice(sampled_emojis)
            else:
                # 获取最近的5条消息内容用于判断
                recent_messages = message_api.get_recent_messages(chat_id=self.chat_id, limit=5)
                messages_text = ""
                if recent_messages:
                    # 使用message_api构建可读的消息字符串
                    messages_text = message_api.build_readable_messages(
                        messages=recent_messages,
                        timestamp_mode="normal_no_YMD",
                        truncate=False,
                        show_actions=False,
                    )

                # 4. 构建prompt让LLM选择情感
                prompt = f"""你正在进行QQ聊天，你需要根据聊天记录，选出一个合适的情感标签。
请你根据以下原因和聊天记录进行选择
原因：{reason}
聊天记录：
{messages_text}

这里是可用的情感标签：
{available_emotions_str}
请直接返回最匹配的那个情感标签，不要进行任何解释或添加其他多余的文字。
                """

                if global_config.debug.show_prompt:
                    logger.info(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")
                else:
                    logger.debug(f"{self.log_prefix} 生成的LLM Prompt: {prompt}")

                # 5. 调用LLM
                models = llm_api.get_available_models()
                chat_model_config = models.get("utils")  # 使用字典访问方式
                if not chat_model_config:
                    logger.error(f"{self.log_prefix} 未找到'utils'模型配置，无法调用LLM")
                    return False, "未找到'utils'模型配置"

                success, chosen_emotion, _, _ = await llm_api.generate_with_model(
                    prompt, model_config=chat_model_config, request_type="emoji.select"
                )

                if not success:
                    logger.error(f"{self.log_prefix} LLM调用失败: {chosen_emotion}")
                    return False, f"LLM调用失败: {chosen_emotion}"

                chosen_emotion = chosen_emotion.strip().replace('"', "").replace("'", "")
                logger.info(f"{self.log_prefix} LLM选择的情感: {chosen_emotion}")

                # 6. 根据选择的情感匹配表情包
                if chosen_emotion in emotion_map:
                    emoji_base64, emoji_description = random.choice(emotion_map[chosen_emotion])
                    logger.info(f"{self.log_prefix} 发送表情包[{chosen_emotion}]，原因: {reason}")
                else:
                    logger.warning(
                        f"{self.log_prefix} LLM选择的情感 '{chosen_emotion}' 不在可用列表中, 将随机选择一个表情包"
                    )
                    emoji_base64, emoji_description, _ = random.choice(sampled_emojis)

            # 7. 发送表情包
            success = await self.send_emoji(emoji_base64)

            if success:
                # 存储动作信息
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"你发送了表情包，原因：{reason}",
                    action_done=True,
                )
                return True, f"成功发送表情包:[表情包：{chosen_emotion}]"
            else:
                error_msg = "发送表情包失败"
                logger.error(f"{self.log_prefix} {error_msg}")

                await self.send_text("执行表情包动作失败")
                return False, error_msg

        except Exception as e:
            logger.error(f"{self.log_prefix} 表情动作执行失败: {e}", exc_info=True)
            return False, f"表情发送失败: {str(e)}"
