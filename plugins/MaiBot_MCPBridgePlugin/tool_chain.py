"""
MCP Workflow 模块 v1.9.0
支持用户自定义工作流（硬流程），将多个 MCP 工具按顺序执行

双轨制架构:
- 软流程 (ReAct): LLM 自主决策，动态多轮调用工具，灵活但不可预测
- 硬流程 (Workflow): 用户预定义的工作流，固定流程，可靠可控

功能:
- Workflow 定义和管理
- 顺序执行多个工具（硬流程）
- 支持变量替换（使用前序工具的输出）
- 自动注册为组合工具供 LLM 调用
- 与 ReAct 软流程互补，用户可选择合适的执行方式
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.common.logger import get_logger

    logger = get_logger("mcp_tool_chain")
except ImportError:
    import logging

    logger = logging.getLogger("mcp_tool_chain")


@dataclass
class ToolChainStep:
    """工具链步骤"""

    tool_name: str  # 要调用的工具名（如 mcp_server_tool）
    args_template: Dict[str, Any] = field(default_factory=dict)  # 参数模板，支持变量替换
    output_key: str = ""  # 输出存储的键名，供后续步骤引用
    description: str = ""  # 步骤描述
    optional: bool = False  # 是否可选（失败时继续执行）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args_template": self.args_template,
            "output_key": self.output_key,
            "description": self.description,
            "optional": self.optional,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolChainStep":
        return cls(
            tool_name=data.get("tool_name", ""),
            args_template=data.get("args_template", {}),
            output_key=data.get("output_key", ""),
            description=data.get("description", ""),
            optional=data.get("optional", False),
        )


@dataclass
class ToolChainDefinition:
    """工具链定义"""

    name: str  # 工具链名称（将作为组合工具的名称）
    description: str  # 工具链描述（供 LLM 理解）
    steps: List[ToolChainStep] = field(default_factory=list)  # 执行步骤
    input_params: Dict[str, str] = field(default_factory=dict)  # 输入参数定义 {参数名: 描述}
    enabled: bool = True  # 是否启用

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [step.to_dict() for step in self.steps],
            "input_params": self.input_params,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolChainDefinition":
        steps = [ToolChainStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            steps=steps,
            input_params=data.get("input_params", {}),
            enabled=data.get("enabled", True),
        )


@dataclass
class ChainExecutionResult:
    """工具链执行结果"""

    success: bool
    final_output: str  # 最终输出（最后一个步骤的结果）
    step_results: List[Dict[str, Any]] = field(default_factory=list)  # 每个步骤的结果
    error: str = ""
    total_duration_ms: float = 0.0

    def to_summary(self) -> str:
        """生成执行摘要"""
        lines = []
        for i, step in enumerate(self.step_results):
            status = "✅" if step.get("success") else "❌"
            tool = step.get("tool_name", "unknown")
            duration = step.get("duration_ms", 0)
            lines.append(f"{status} 步骤{i + 1}: {tool} ({duration:.0f}ms)")
            if not step.get("success") and step.get("error"):
                lines.append(f"   错误: {step['error'][:50]}")
        return "\n".join(lines)


class ToolChainExecutor:
    """工具链执行器"""

    # 变量替换模式: ${step.output_key} 或 ${input.param_name} 或 ${prev}
    VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

    def __init__(self, mcp_manager):
        self._mcp_manager = mcp_manager

    def _resolve_tool_key(self, tool_name: str) -> Optional[str]:
        """解析工具名，返回有效的 tool_key

        支持:
        - 直接使用 tool_key（如 mcp_server_tool）
        - 使用注册后的工具名（会自动转换 - 和 . 为 _）
        """
        all_tools = self._mcp_manager.all_tools

        # 直接匹配
        if tool_name in all_tools:
            return tool_name

        # 尝试转换后匹配（用户可能使用了注册后的名称）
        normalized = tool_name.replace("-", "_").replace(".", "_")
        if normalized in all_tools:
            return normalized

        # 尝试查找包含该名称的工具
        for key in all_tools.keys():
            if key.endswith(f"_{tool_name}") or key.endswith(f"_{normalized}"):
                return key

        return None

    async def execute(
        self,
        chain: ToolChainDefinition,
        input_args: Dict[str, Any],
    ) -> ChainExecutionResult:
        """执行工具链

        Args:
            chain: 工具链定义
            input_args: 用户输入的参数

        Returns:
            ChainExecutionResult: 执行结果
        """
        start_time = time.time()
        step_results = []
        context = {
            "input": input_args or {},  # 用户输入，确保不为 None
            "step": {},  # 各步骤输出，按 output_key 存储
            "prev": "",  # 上一步的输出
        }

        final_output = ""

        # 验证必需的输入参数
        missing_params = []
        for param_name in chain.input_params.keys():
            if param_name not in context["input"]:
                missing_params.append(param_name)

        if missing_params:
            return ChainExecutionResult(
                success=False,
                final_output="",
                error=f"缺少必需参数: {', '.join(missing_params)}",
                total_duration_ms=(time.time() - start_time) * 1000,
            )

        for i, step in enumerate(chain.steps):
            step_start = time.time()
            step_result = {
                "step_index": i,
                "tool_name": step.tool_name,
                "success": False,
                "output": "",
                "error": "",
                "duration_ms": 0,
            }

            try:
                # 替换参数中的变量
                resolved_args = self._resolve_args(step.args_template, context)
                step_result["resolved_args"] = resolved_args

                # 解析工具名
                tool_key = self._resolve_tool_key(step.tool_name)
                if not tool_key:
                    step_result["error"] = f"工具 {step.tool_name} 不存在"
                    logger.warning(f"工具链步骤 {i + 1}: 工具 {step.tool_name} 不存在")

                    if not step.optional:
                        step_results.append(step_result)
                        return ChainExecutionResult(
                            success=False,
                            final_output="",
                            step_results=step_results,
                            error=f"步骤 {i + 1}: 工具 {step.tool_name} 不存在",
                            total_duration_ms=(time.time() - start_time) * 1000,
                        )
                    step_results.append(step_result)
                    continue

                logger.debug(f"工具链步骤 {i + 1}: 调用 {tool_key}，参数: {resolved_args}")

                # 调用工具
                result = await self._mcp_manager.call_tool(tool_key, resolved_args)

                step_duration = (time.time() - step_start) * 1000
                step_result["duration_ms"] = step_duration

                if result.success:
                    step_result["success"] = True
                    # 确保 content 不为 None
                    content = result.content if result.content is not None else ""
                    step_result["output"] = content

                    # 更新上下文
                    context["prev"] = content
                    if step.output_key:
                        context["step"][step.output_key] = content

                    final_output = content
                    content_preview = content[:100] if content else "(空)"
                    logger.debug(f"工具链步骤 {i + 1} 成功: {content_preview}...")
                else:
                    step_result["error"] = result.error or "未知错误"
                    logger.warning(f"工具链步骤 {i + 1} 失败: {result.error}")

                    if not step.optional:
                        step_results.append(step_result)
                        return ChainExecutionResult(
                            success=False,
                            final_output="",
                            step_results=step_results,
                            error=f"步骤 {i + 1} ({step.tool_name}) 失败: {result.error}",
                            total_duration_ms=(time.time() - start_time) * 1000,
                        )

            except Exception as e:
                step_duration = (time.time() - step_start) * 1000
                step_result["duration_ms"] = step_duration
                step_result["error"] = str(e)
                logger.error(f"工具链步骤 {i + 1} 异常: {e}")

                if not step.optional:
                    step_results.append(step_result)
                    return ChainExecutionResult(
                        success=False,
                        final_output="",
                        step_results=step_results,
                        error=f"步骤 {i + 1} ({step.tool_name}) 异常: {e}",
                        total_duration_ms=(time.time() - start_time) * 1000,
                    )

            step_results.append(step_result)

        total_duration = (time.time() - start_time) * 1000

        return ChainExecutionResult(
            success=True,
            final_output=final_output,
            step_results=step_results,
            total_duration_ms=total_duration,
        )

    def _resolve_args(self, args_template: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """解析参数模板，替换变量

        支持的变量格式:
        - ${input.param_name}: 用户输入的参数
        - ${step.output_key}: 某个步骤的输出
        - ${prev}: 上一步的输出
        - ${prev.field}: 上一步输出（JSON）的某个字段
        """
        resolved = {}

        for key, value in args_template.items():
            if isinstance(value, str):
                resolved[key] = self._substitute_vars(value, context)
            elif isinstance(value, dict):
                resolved[key] = self._resolve_args(value, context)
            elif isinstance(value, list):
                resolved[key] = [self._substitute_vars(v, context) if isinstance(v, str) else v for v in value]
            else:
                resolved[key] = value

        return resolved

    def _substitute_vars(self, template: str, context: Dict[str, Any]) -> str:
        """替换字符串中的变量"""

        def replacer(match):
            var_path = match.group(1)
            return self._get_var_value(var_path, context)

        return self.VAR_PATTERN.sub(replacer, template)

    def _get_var_value(self, var_path: str, context: Dict[str, Any]) -> str:
        """获取变量值

        Args:
            var_path: 变量路径，如 "input.query", "step.search_result", "prev", "prev.id"
            context: 上下文
        """
        parts = self._parse_var_path(var_path)

        if not parts:
            return ""

        # 获取根对象
        root = parts[0]
        if root not in context:
            logger.warning(f"变量 {var_path} 的根 '{root}' 不存在")
            return ""

        value = context[root]

        # 遍历路径
        for part in parts[1:]:
            if isinstance(value, str):
                parsed = self._try_parse_json(value)
                if parsed is not None:
                    value = parsed

            if isinstance(value, dict):
                value = value.get(part, "")
            elif isinstance(value, list):
                if part.isdigit():
                    idx = int(part)
                    value = value[idx] if 0 <= idx < len(value) else ""
                else:
                    value = ""
            else:
                value = ""

        # 确保返回字符串
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if value is None:
            return ""
        if value == "":
            return ""
        return str(value)

    def _try_parse_json(self, value: str) -> Optional[Any]:
        """尝试将字符串解析为 JSON 对象，失败则返回 None。"""
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _parse_var_path(self, var_path: str) -> List[str]:
        """解析变量路径，支持点号与下标写法。

        支持:
        - step.geo.return.0.location
        - step.geo.return[0].location
        - step.geo['return'][0]['location']
        """
        if not var_path:
            return []

        tokens: List[str] = []
        buf: List[str] = []
        in_bracket = False
        in_quote = False
        quote_char = ""

        def flush_buf() -> None:
            if buf:
                token = "".join(buf).strip()
                if token:
                    tokens.append(token)
                buf.clear()

        i = 0
        while i < len(var_path):
            ch = var_path[i]

            if not in_bracket and ch == ".":
                flush_buf()
                i += 1
                continue

            if not in_bracket and ch == "[":
                flush_buf()
                in_bracket = True
                in_quote = False
                quote_char = ""
                i += 1
                continue

            if in_bracket and not in_quote and ch == "]":
                flush_buf()
                in_bracket = False
                i += 1
                continue

            if in_bracket and ch in ("'", '"'):
                if not in_quote:
                    in_quote = True
                    quote_char = ch
                    i += 1
                    continue
                if quote_char == ch:
                    in_quote = False
                    quote_char = ""
                    i += 1
                    continue

            if in_bracket and not in_quote:
                if ch.isspace():
                    i += 1
                    continue
                if ch == ",":
                    i += 1
                    continue

            buf.append(ch)
            i += 1

        flush_buf()

        if in_bracket or in_quote:
            return [p for p in var_path.split(".") if p]

        return tokens


class ToolChainManager:
    """工具链管理器"""

    _instance: Optional["ToolChainManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._chains: Dict[str, ToolChainDefinition] = {}
        self._executor: Optional[ToolChainExecutor] = None

    def set_executor(self, mcp_manager) -> None:
        """设置执行器"""
        self._executor = ToolChainExecutor(mcp_manager)

    def add_chain(self, chain: ToolChainDefinition) -> bool:
        """添加工具链"""
        if not chain.name:
            logger.error("工具链名称不能为空")
            return False

        if chain.name in self._chains:
            logger.warning(f"工具链 {chain.name} 已存在，将被覆盖")

        self._chains[chain.name] = chain
        logger.info(f"已添加工具链: {chain.name} ({len(chain.steps)} 个步骤)")
        return True

    def remove_chain(self, name: str) -> bool:
        """移除工具链"""
        if name in self._chains:
            del self._chains[name]
            logger.info(f"已移除工具链: {name}")
            return True
        return False

    def get_chain(self, name: str) -> Optional[ToolChainDefinition]:
        """获取工具链"""
        return self._chains.get(name)

    def get_all_chains(self) -> Dict[str, ToolChainDefinition]:
        """获取所有工具链"""
        return self._chains.copy()

    def get_enabled_chains(self) -> Dict[str, ToolChainDefinition]:
        """获取所有启用的工具链"""
        return {name: chain for name, chain in self._chains.items() if chain.enabled}

    async def execute_chain(
        self,
        chain_name: str,
        input_args: Dict[str, Any],
    ) -> ChainExecutionResult:
        """执行工具链"""
        chain = self._chains.get(chain_name)
        if not chain:
            return ChainExecutionResult(
                success=False,
                final_output="",
                error=f"工具链 {chain_name} 不存在",
            )

        if not chain.enabled:
            return ChainExecutionResult(
                success=False,
                final_output="",
                error=f"工具链 {chain_name} 已禁用",
            )

        if not self._executor:
            return ChainExecutionResult(
                success=False,
                final_output="",
                error="工具链执行器未初始化",
            )

        return await self._executor.execute(chain, input_args)

    def load_from_json(self, json_str: str) -> Tuple[int, List[str]]:
        """从 JSON 字符串加载工具链配置

        Returns:
            (成功加载数量, 错误列表)
        """
        errors = []
        loaded = 0

        try:
            data = json.loads(json_str) if json_str.strip() else []
        except json.JSONDecodeError as e:
            return 0, [f"JSON 解析失败: {e}"]

        if not isinstance(data, list):
            data = [data]

        for i, item in enumerate(data):
            try:
                chain = ToolChainDefinition.from_dict(item)
                if not chain.name:
                    errors.append(f"第 {i + 1} 个工具链缺少名称")
                    continue
                if not chain.steps:
                    errors.append(f"工具链 {chain.name} 没有步骤")
                    continue

                self.add_chain(chain)
                loaded += 1
            except Exception as e:
                errors.append(f"第 {i + 1} 个工具链解析失败: {e}")

        return loaded, errors

    def export_to_json(self, pretty: bool = True) -> str:
        """导出所有工具链为 JSON"""
        chains_data = [chain.to_dict() for chain in self._chains.values()]
        if pretty:
            return json.dumps(chains_data, ensure_ascii=False, indent=2)
        return json.dumps(chains_data, ensure_ascii=False)

    def clear(self) -> None:
        """清空所有工具链"""
        self._chains.clear()


# 全局工具链管理器实例
tool_chain_manager = ToolChainManager()
