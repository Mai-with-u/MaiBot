from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import json

from json_repair import repair_json
from sqlalchemy import update
from sqlmodel import select

from src.chat.message_receive.message import SessionMessage
from src.chat.replyer.expression_vector_index import expression_vector_index
from src.common.database.database import get_db_session
from src.common.database.database_model import Expression, ModifiedBy
from src.common.logger import get_logger
from src.common.utils.utils_config import ChatConfigUtils, ExpressionConfigUtils
from src.config.config import global_config, model_config
from src.learners.expression_style_utils import (
    is_prompt_example_expression_style,
    normalize_expression_style_for_learning,
)
from src.learners.learner_utils_old import weighted_sample
from src.maisaka.context.messages import LLMContextMessage

logger = get_logger("maisaka_expression_selector")

SubAgentRunner = Callable[[str], Awaitable[str]]


@dataclass
class MaisakaExpressionSelectionResult:
    """Maisaka replyer 的表达方式选择结果。"""

    expression_habits: str = ""
    selected_expression_ids: List[int] = field(default_factory=list)
    selected_expressions: List[Dict[str, Any]] = field(default_factory=list)


class MaisakaExpressionSelector:
    """负责在 replyer 侧完成表达方式筛选与子代理二次选择。"""

    _VECTOR_CLUSTER_POOL_SIZE = 16

    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器。"""

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    def _can_use_expressions(self, session_id: str) -> bool:
        try:
            use_expression, _ = ExpressionConfigUtils.get_expression_config_for_chat(session_id)
            return use_expression
        except Exception as exc:
            logger.error(f"检查表达方式使用开关失败: {exc}")
            return False

    @staticmethod
    def _is_global_expression_group_marker(platform: str, item_id: str) -> bool:
        return platform == "*" and item_id == "*"

    def _resolve_expression_group_scope(self, session_id: str) -> tuple[set[str], bool]:
        related_session_ids = {session_id}
        has_global_share = False
        expression_groups = global_config.expression.expression_groups

        for expression_group in expression_groups:
            target_items = expression_group.targets
            group_session_ids: set[str] = set()
            contains_current_session = False
            contains_global_share_marker = False

            for target_item in target_items:
                platform = target_item.platform.strip()
                item_id = target_item.item_id.strip()
                if not platform or not item_id:
                    continue

                target_session_ids = ChatConfigUtils.get_target_session_ids_with_wildcards(target_item)
                group_session_ids.update(target_session_ids)
                if ChatConfigUtils.target_matches_session_with_wildcards(target_item, session_id):
                    contains_current_session = True
                    if self._is_global_expression_group_marker(platform, item_id):
                        contains_global_share_marker = True

            if contains_global_share_marker:
                has_global_share = True
            if contains_current_session:
                related_session_ids.update(group_session_ids)

        return related_session_ids, has_global_share

    def _load_all_expression_candidates(self, session_id: str) -> List[dict[str, Any]]:
        related_session_ids, has_global_share = self._resolve_expression_group_scope(session_id)

        with get_db_session(auto_commit=False) as session:
            base_query = select(
                Expression.id,
                Expression.situation,
                Expression.style,
                Expression.count,
            )
            if has_global_share:
                scoped_query = base_query
            else:
                scoped_query = base_query.where(
                    (Expression.session_id.in_(related_session_ids)) | (Expression.session_id.is_(None))  # type: ignore[attr-defined]
                )
            if global_config.expression.expression_checked_only:
                scoped_query = scoped_query.where(
                    Expression.checked.is_(True),  # type: ignore[attr-defined]
                    Expression.modified_by == ModifiedBy.USER,
                )
            rows = session.exec(scoped_query).all()

        all_candidates: List[dict[str, Any]] = []
        for expression_id, situation, style, count in rows:
            normalized_style = normalize_expression_style_for_learning(str(style or ""))
            if (
                expression_id is None
                or not situation
                or not normalized_style
                or is_prompt_example_expression_style(normalized_style)
            ):
                continue
            all_candidates.append(
                {
                    "id": expression_id,
                    "situation": situation,
                    "style": normalized_style,
                    "count": count if count is not None else 1,
                }
            )
        return all_candidates

    def _sample_legacy_expression_candidates(self, all_candidates: List[dict[str, Any]]) -> List[dict[str, Any]]:
        if len(all_candidates) < 10:
            return []

        high_count_candidates = [item for item in all_candidates if (item.get("count", 1) or 1) > 1]
        selected_high = (
            weighted_sample(high_count_candidates, min(len(high_count_candidates), 5))
            if len(high_count_candidates) >= 10
            else []
        )
        selected_random = weighted_sample(all_candidates, min(len(all_candidates), 5))

        candidate_pool: List[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for candidate in [*selected_high, *selected_random]:
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, int) or candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            candidate_pool.append(candidate)

        return candidate_pool

    @staticmethod
    def _format_candidate_preview(candidates: List[dict[str, Any]]) -> str:
        """构建候选表达方式的简短日志预览。"""
        preview_items: List[str] = []
        for candidate in candidates[:5]:
            candidate_id = candidate.get("id")
            situation = str(candidate.get("situation") or "").strip()
            style = str(candidate.get("style") or "").strip()
            count = candidate.get("count")
            preview_items.append(
                f"id={candidate_id}, situation={situation!r}, style={style!r}, count={count}"
            )
        return "; ".join(preview_items)

    @staticmethod
    def _build_expression_habits_block(selected_expressions: List[dict[str, Any]]) -> str:
        if not selected_expressions:
            return ""
        lines = [
            f"""- 当"{expression['situation']}"时，可以用"{expression['style']}"来表达。"""
            for expression in selected_expressions
        ]
        return "【表达习惯参考，请视情况自然的使用】\n" + "\n".join(lines)

    @staticmethod
    def _normalize_history_line(message: LLMContextMessage) -> str:
        content = " ".join((message.processed_plain_text or "").split()).strip()
        if len(content) > 120:
            content = content[:120] + "..."
        timestamp = message.timestamp.strftime("%H:%M:%S") if isinstance(message.timestamp, datetime) else ""
        return f"- {timestamp} {message.role}: {content}".strip()

    @staticmethod
    def _format_expression_intent(reply_tool_args: Optional[dict[str, Any]]) -> str:
        """格式化 planner 传入的表达选择意图。"""

        if not isinstance(reply_tool_args, dict):
            return ""

        lines: List[str] = []
        raw_intent = reply_tool_args.get("expression_intent")
        if isinstance(raw_intent, dict):
            field_labels = {
                "focus": "贴合对象",
                "reply_act": "回复动作",
                "scene": "表达场景",
                "tone": "期望语气",
                "prefer": "优先表达",
                "avoid": "避免表达",
            }
            for field_name, label in field_labels.items():
                field_value = raw_intent.get(field_name)
                if isinstance(field_value, list):
                    normalized_value = "、".join(str(item).strip() for item in field_value if str(item).strip())
                else:
                    normalized_value = str(field_value or "").strip()
                if normalized_value:
                    lines.append(f"- {label}：{normalized_value}")
        elif raw_intent:
            normalized_intent = str(raw_intent).strip()
            if normalized_intent:
                lines.append(f"- 表达意图：{normalized_intent}")

        if not lines:
            return ""
        return "表达选择意图：\n" + "\n".join(lines)

    @staticmethod
    def _build_expression_query_text(
        reply_reason: str,
        reply_tool_args: Optional[dict[str, Any]],
        *,
        use_expression_intent: bool,
    ) -> str:
        """构建表达检索与精排共用的匹配依据文本。"""

        query_parts: List[str] = []
        expression_intent_block = (
            MaisakaExpressionSelector._format_expression_intent(reply_tool_args)
            if use_expression_intent
            else ""
        )
        if expression_intent_block:
            query_parts.append(expression_intent_block)

        guide_parts: List[str] = []
        if isinstance(reply_tool_args, dict):
            reply_guide = str(reply_tool_args.get("reply_guide") or "").strip()
            if reply_guide:
                guide_parts.append(f"回复指引：\n{reply_guide}")
            reference_info = str(reply_tool_args.get("reference_info") or "").strip()
            if reference_info:
                guide_parts.append(f"关键信息参考：\n{reference_info}")
        if guide_parts:
            query_parts.extend(guide_parts)
        else:
            normalized_reply_reason = str(reply_reason or "").strip()
            if normalized_reply_reason:
                query_parts.append(f"Planner 推理：\n{normalized_reply_reason}")

        return "\n\n".join(query_parts)

    @staticmethod
    def _use_vector_candidate_pool() -> bool:
        return global_config.expression.expression_selection_mode in {"vector", "vector_intent"}

    @staticmethod
    def _has_embedding_model_configured() -> bool:
        return any(model_name.strip() for model_name in model_config.model_task_config.embedding.model_list)

    @staticmethod
    def _use_expression_intent() -> bool:
        return global_config.expression.expression_selection_mode == "vector_intent"

    def _build_selector_prompt(
        self,
        *,
        candidates: List[dict[str, Any]],
    ) -> str:
        candidate_lines = [
            f"{candidate['id']}: 情景={candidate['situation']} | 风格={candidate['style']}"
            for candidate in candidates
        ]

        return (
            "你是 Maisaka 的表达方式选择子代理。\n"
            "你只负责根据下方真实聊天上下文，为这一次可见回复挑选最合适的表达方式。\n"
            "请只从下面候选中选择 0 到 5 条最适合当前语境的表达方式。\n"
            "优先考虑自然、贴合上下文、不生硬、不模板化。\n"
            "如果没有明显合适的，就返回空数组。\n"
            '严格只输出 JSON，对象格式为 {"selected_ids":[123,456]}。\n\n'
            f"候选表达方式：\n{chr(10).join(candidate_lines)}"
        )

    def _parse_selected_ids(self, raw_response: str, candidates: List[dict[str, Any]]) -> List[int]:
        if not raw_response.strip():
            return []
        try:
            parsed_result = json.loads(repair_json(raw_response))
        except Exception:
            logger.warning(f"表达方式选择结果解析失败: {raw_response!r}")
            return []

        raw_selected_ids = parsed_result.get("selected_ids", []) if isinstance(parsed_result, dict) else []
        if not isinstance(raw_selected_ids, list):
            return []

        candidate_map = {
            candidate["id"]: candidate
            for candidate in candidates
            if isinstance(candidate.get("id"), int)
        }
        selected_ids: List[int] = []
        for candidate_id in raw_selected_ids:
            if not isinstance(candidate_id, int):
                continue
            if candidate_id not in candidate_map or candidate_id in selected_ids:
                continue
            selected_ids.append(candidate_id)
            if len(selected_ids) >= 3:
                break
        return selected_ids

    def _build_direct_selection_result(
        self,
        *,
        session_id: str,
        candidates: List[dict[str, Any]],
    ) -> MaisakaExpressionSelectionResult:
        selected_ids = [
            candidate["id"]
            for candidate in candidates
            if isinstance(candidate.get("id"), int)
        ]
        selected_expressions = [
            candidate
            for candidate in candidates
            if candidate.get("id") in selected_ids
        ]
        self._update_last_active_time(selected_ids)
        logger.debug(
            f"表达方式直接注入：session_id={session_id} 已选数={len(selected_ids)} "
            f"selected_ids={selected_ids!r} 已选预览={self._format_candidate_preview(selected_expressions)}"
        )
        return MaisakaExpressionSelectionResult(
            expression_habits=self._build_expression_habits_block(selected_expressions),
            selected_expression_ids=selected_ids,
            selected_expressions=list(selected_expressions),
        )

    @staticmethod
    def _build_chat_info(chat_history: List[LLMContextMessage]) -> str:
        history_lines = [
            MaisakaExpressionSelector._normalize_history_line(message)
            for message in chat_history[-10:]
            if (message.processed_plain_text or "").strip()
        ]
        return "\n".join(history_lines)

    @staticmethod
    def _serialize_context_message(message: LLMContextMessage) -> dict[str, Any]:
        timestamp = message.timestamp.isoformat() if isinstance(message.timestamp, datetime) else ""
        return {
            "role": message.role,
            "text": message.processed_plain_text or "",
            "timestamp": timestamp,
            "source_kind": message.source_kind,
        }

    @staticmethod
    def _serialize_reply_message(reply_message: Optional[SessionMessage]) -> dict[str, Any] | None:
        if reply_message is None:
            return None
        return {
            "message_id": str(reply_message.message_id or ""),
            "text": reply_message.processed_plain_text or "",
        }

    @staticmethod
    def _normalize_candidate_list(raw_candidates: Any, fallback: List[dict[str, Any]]) -> List[dict[str, Any]]:
        if not isinstance(raw_candidates, list):
            return fallback

        normalized_candidates: List[dict[str, Any]] = []
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                continue
            candidate_id = raw_candidate.get("id")
            if not isinstance(candidate_id, int):
                continue
            situation = str(raw_candidate.get("situation") or "").strip()
            style = normalize_expression_style_for_learning(str(raw_candidate.get("style") or "").strip())
            if not situation or not style or is_prompt_example_expression_style(style):
                continue
            normalized_candidates.append(
                {
                    "id": candidate_id,
                    "situation": situation,
                    "style": style,
                    "count": raw_candidate.get("count", 1) or 1,
                }
            )
        return normalized_candidates

    @staticmethod
    def _normalize_selected_ids(raw_selected_ids: Any, candidates: List[dict[str, Any]]) -> List[int]:
        if not isinstance(raw_selected_ids, list):
            return []

        candidate_ids = {
            candidate["id"]
            for candidate in candidates
            if isinstance(candidate.get("id"), int)
        }
        selected_ids: List[int] = []
        for raw_id in raw_selected_ids:
            if not isinstance(raw_id, int):
                continue
            if raw_id not in candidate_ids or raw_id in selected_ids:
                continue
            selected_ids.append(raw_id)
        return selected_ids

    @staticmethod
    def _normalize_selected_expressions(raw_selected_expressions: Any) -> List[dict[str, Any]]:
        if not isinstance(raw_selected_expressions, list):
            return []

        selected_expressions: List[dict[str, Any]] = []
        for raw_expression in raw_selected_expressions:
            if not isinstance(raw_expression, dict):
                continue
            situation = str(raw_expression.get("situation") or "").strip()
            style = normalize_expression_style_for_learning(str(raw_expression.get("style") or "").strip())
            if not situation or not style or is_prompt_example_expression_style(style):
                continue
            normalized_expression = {
                "situation": situation,
                "style": style,
                "count": raw_expression.get("count", 1) or 1,
            }
            expression_id = raw_expression.get("id")
            if isinstance(expression_id, int):
                normalized_expression["id"] = expression_id
            selected_expressions.append(normalized_expression)
        return selected_expressions

    def _build_selection_result_from_ids(
        self,
        *,
        candidates: List[dict[str, Any]],
        selected_ids: List[int],
    ) -> MaisakaExpressionSelectionResult:
        candidate_map = {
            candidate["id"]: candidate
            for candidate in candidates
            if isinstance(candidate.get("id"), int)
        }
        selected_expressions = [
            candidate_map[expression_id]
            for expression_id in selected_ids
            if expression_id in candidate_map
        ]
        self._update_last_active_time(selected_ids)
        return MaisakaExpressionSelectionResult(
            expression_habits=self._build_expression_habits_block(selected_expressions),
            selected_expression_ids=selected_ids,
            selected_expressions=list(selected_expressions),
        )

    def _build_selection_result_from_expressions(
        self,
        selected_expressions: List[dict[str, Any]],
    ) -> MaisakaExpressionSelectionResult:
        selected_ids = [
            expression["id"]
            for expression in selected_expressions
            if isinstance(expression.get("id"), int)
        ]
        if selected_ids:
            self._update_last_active_time(selected_ids)
        return MaisakaExpressionSelectionResult(
            expression_habits=self._build_expression_habits_block(selected_expressions),
            selected_expression_ids=selected_ids,
            selected_expressions=list(selected_expressions),
        )

    async def _build_expression_candidate_pool(
        self,
        *,
        session_id: str,
        reply_reason: str,
        reply_tool_args: Optional[dict[str, Any]],
        all_candidates: List[dict[str, Any]],
    ) -> List[dict[str, Any]]:
        """按配置构建本次回复的表达候选池。"""

        if self._use_vector_candidate_pool() and self._has_embedding_model_configured():
            expression_query_text = self._build_expression_query_text(
                reply_reason,
                reply_tool_args,
                use_expression_intent=self._use_expression_intent(),
            )
            vector_candidates = await expression_vector_index.select_candidates(
                index_path=global_config.expression.expression_vector_index_path,
                session_id=session_id,
                query_text=expression_query_text,
                scoped_candidates=all_candidates,
                candidate_pool_size=global_config.expression.expression_vector_candidate_pool_size,
                cluster_pool_size=self._VECTOR_CLUSTER_POOL_SIZE,
            )
            if vector_candidates:
                logger.debug(
                    f"表达方式向量候选池完成：session_id={session_id} "
                    f"候选数={len(vector_candidates)} 候选预览={self._format_candidate_preview(vector_candidates)}"
                )
                return vector_candidates
            logger.info(f"表达方式向量候选池为空，回退随手候选：session_id={session_id}")
        elif self._use_vector_candidate_pool():
            logger.info("表达方式向量候选池需要配置 embedding 模型，已回退随手候选")

        return self._sample_legacy_expression_candidates(all_candidates)

    async def _build_default_selection_result(
        self,
        *,
        session_id: str,
        candidates: List[dict[str, Any]],
        sub_agent_runner: Optional[SubAgentRunner],
    ) -> MaisakaExpressionSelectionResult:
        if sub_agent_runner is None:
            logger.info("表达方式 LLM 选择已跳过：缺少子代理执行器，回退为直接注入")
            return self._build_direct_selection_result(
                session_id=session_id,
                candidates=candidates,
            )

        selector_prompt = self._build_selector_prompt(
            candidates=candidates,
        )
        try:
            raw_response = await sub_agent_runner(selector_prompt)
        except Exception as exc:
            logger.warning(f"表达方式 LLM 选择子代理执行失败，回退为直接注入: {exc}")
            return self._build_direct_selection_result(
                session_id=session_id,
                candidates=candidates,
            )

        selected_ids = self._parse_selected_ids(raw_response, candidates)
        logger.debug(
            f"表达方式 LLM 选择完成：session_id={session_id} selected_ids={selected_ids!r} "
            f"候选预览={self._format_candidate_preview(candidates)}"
        )
        return self._build_selection_result_from_ids(
            candidates=candidates,
            selected_ids=selected_ids,
        )

    def _update_last_active_time(self, selected_ids: List[int]) -> None:
        if not selected_ids:
            return
        with get_db_session() as session:
            now = datetime.now()
            session.execute(
                update(Expression)
                .where(Expression.id.in_(selected_ids))  # type: ignore[attr-defined]
                .values(last_active_time=now)
            )

    async def select_for_reply(
        self,
        *,
        session_id: str,
        chat_history: List[LLMContextMessage],
        reply_message: Optional[SessionMessage],
        reply_reason: str,
        reply_tool_args: Optional[dict[str, Any]] = None,
        sub_agent_runner: Optional[SubAgentRunner],
    ) -> MaisakaExpressionSelectionResult:
        if not session_id:
            logger.info("表达方式选择已跳过：缺少 session_id")
            return MaisakaExpressionSelectionResult()
        if not self._can_use_expressions(session_id):
            logger.info(f"表达方式选择已跳过：当前会话未启用表达方式，session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        all_candidates = self._load_all_expression_candidates(session_id)
        if len(all_candidates) < 10:
            logger.info(f"表达方式选择已跳过：本地候选不足，session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        candidates = await self._build_expression_candidate_pool(
            session_id=session_id,
            reply_reason=reply_reason,
            reply_tool_args=reply_tool_args,
            all_candidates=all_candidates,
        )
        if not candidates:
            logger.info(f"表达方式选择已跳过：本地候选不足，session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        chat_info = self._build_chat_info(chat_history)
        target_message = (reply_message.processed_plain_text or "").strip() if reply_message is not None else ""
        hook_kwargs = {
            "chat_id": session_id,
            "session_id": session_id,
            "chat_info": chat_info,
            "chat_history": [self._serialize_context_message(message) for message in chat_history[-10:]],
            "reply_message": self._serialize_reply_message(reply_message),
            "reply_tool_args": dict(reply_tool_args or {}),
            "target_message": target_message,
            "reply_reason": reply_reason,
            "max_num": len(candidates),
            "think_level": 0,
            "candidates": candidates,
        }

        before_select_result = await self._get_runtime_manager().invoke_hook(
            "expression.select.before_select",
            **hook_kwargs,
        )
        if before_select_result.aborted:
            logger.info(f"表达方式选择在开始前被 Hook 中止：session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        before_kwargs = before_select_result.kwargs
        candidates = self._normalize_candidate_list(before_kwargs.get("candidates"), candidates)
        max_num = int(before_kwargs.get("max_num") or len(candidates))
        if max_num >= 0:
            candidates = candidates[:max_num]
        if not candidates:
            logger.info(f"表达方式选择已跳过：Hook 过滤后候选为空，session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        selection_result = await self._build_default_selection_result(
            session_id=session_id,
            candidates=candidates,
            sub_agent_runner=sub_agent_runner,
        )
        selected_ids = list(selection_result.selected_expression_ids)
        selected_expressions = list(selection_result.selected_expressions)
        if not selected_expressions and selected_ids:
            selected_expressions = [
                candidate
                for candidate in candidates
                if candidate.get("id") in selected_ids
            ]

        after_selection_result = await self._get_runtime_manager().invoke_hook(
            "expression.select.after_selection",
            **{
                **before_kwargs,
                "candidates": candidates,
                "selected_expressions": selected_expressions,
                "selected_expression_ids": selected_ids,
            },
        )
        if after_selection_result.aborted:
            logger.info(f"表达方式选择结果被 Hook 中止：session_id={session_id}")
            return MaisakaExpressionSelectionResult()

        after_kwargs = after_selection_result.kwargs
        raw_selected_ids = after_kwargs.get("selected_expression_ids")
        raw_selected_expressions = after_kwargs.get("selected_expressions")
        hook_selected_expressions = self._normalize_selected_expressions(raw_selected_expressions)
        if hook_selected_expressions and raw_selected_expressions != selected_expressions:
            return self._build_selection_result_from_expressions(hook_selected_expressions)

        hook_selected_ids = self._normalize_selected_ids(raw_selected_ids, candidates)
        if hook_selected_ids or raw_selected_ids == []:
            return self._build_selection_result_from_ids(
                candidates=candidates,
                selected_ids=hook_selected_ids,
            )

        return selection_result


maisaka_expression_selector = MaisakaExpressionSelector()
