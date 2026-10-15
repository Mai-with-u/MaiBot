"""表达向量的宿主接入层，负责配置通知和后台任务生命周期。"""

from typing import Any, Dict, Sequence

import json

from src.chat.replyer.expression_vector_index import expression_vector_index
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config, model_config


class ExpressionVectorService:
    """将宿主配置变化传递给表达索引，不参与其索引重建实现。"""

    def __init__(self) -> None:
        self._registered = False
        self._signature = ""

    @staticmethod
    def _configuration_signature() -> str:
        task = model_config.model_task_config.embedding
        models = {model.name: model for model in model_config.models}
        providers = {provider.name: provider for provider in model_config.api_providers}
        candidates = []
        for name in task.model_list:
            model = models[name]
            provider = providers[model.api_provider]
            candidates.append(
                {
                    "name": name,
                    "identifier": model.model_identifier,
                    "provider": model.api_provider,
                    "base_url": provider.base_url.rstrip("/"),
                    "client_type": provider.client_type,
                    "extra_params": model.extra_params,
                }
            )
        return json.dumps(
            {
                "task": task.model_dump(),
                "models": candidates,
                "use_vector_expression": global_config.expression.use_vector_expression,
                "path": global_config.expression.expression_vector_index_path,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def start(self) -> None:
        if self._registered:
            return
        self._signature = self._configuration_signature()
        config_manager.register_reload_callback(self.on_config_reload)
        self._registered = True
        self._notify_index()

    @staticmethod
    def _notify_index() -> None:
        config = global_config.expression
        expression_vector_index.request_history_backfill(
            index_path=config.expression_vector_index_path,
            enabled=config.use_vector_expression,
        )

    async def on_config_reload(self, changed_scopes: Sequence[str] = ()) -> None:
        if changed_scopes and not {"bot", "model"}.intersection(changed_scopes):
            return
        await run_on_main_loop(self._apply_config_reload())

    async def _apply_config_reload(self) -> None:
        signature = self._configuration_signature()
        if signature == self._signature:
            return
        self._signature = signature
        # 回调只刷新缓存和唤醒任务，不等待嵌入请求或索引重建。
        self._notify_index()

    async def stop(self) -> None:
        if self._registered:
            config_manager.unregister_reload_callback(self.on_config_reload)
            self._registered = False
        await expression_vector_index.stop_history_backfill()

    async def list_vector_spaces(self) -> Dict[str, Any]:
        return await run_on_main_loop(self._list_vector_spaces())

    async def _list_vector_spaces(self) -> Dict[str, Any]:
        config = global_config.expression
        return await expression_vector_index.list_vector_spaces(
            index_path=config.expression_vector_index_path,
            enabled=config.use_vector_expression,
        )

    async def delete_vector_space(self, space_id: str) -> Dict[str, Any]:
        return await run_on_main_loop(self._delete_vector_space(space_id))

    async def _delete_vector_space(self, space_id: str) -> Dict[str, Any]:
        config = global_config.expression
        return await expression_vector_index.delete_vector_space(
            index_path=config.expression_vector_index_path,
            space_id=space_id,
            enabled=config.use_vector_expression,
        )


expression_vector_service = ExpressionVectorService()
