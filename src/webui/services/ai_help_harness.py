"""答疑 Agent 的内置技能目录和单轮工作计划。"""

from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.common.prompt_i18n import load_prompt
from src.llm_models.payload_content.tool_option import ToolDefinitionInput

from .ai_search_models import AISearchPlanStep, AISearchProgressEvent


SkillId = Literal["setup", "troubleshoot", "plugin", "develop"]
LOAD_SKILL_TOOL = "load_help_skill"
UPDATE_PLAN_TOOL = "update_help_plan"
HARNESS_TOOLS = {LOAD_SKILL_TOOL, UPDATE_PLAN_TOOL}


class SkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: SkillId = Field(
        description=(
            "setup: initial setup, model providers, configuration or migration; "
            "troubleshoot: failures, logs, connectivity, unexpected behavior; "
            "plugin: find, compare, install or configure plugins; "
            "develop: plugin SDK, extension APIs, implementation or debugging plugin code."
        ),
    )


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: List[AISearchPlanStep] = Field(min_length=1, max_length=5)


def build_harness_tools() -> List[ToolDefinitionInput]:
    return [
        {
            "name": LOAD_SKILL_TOOL,
            "description": (
                "Load a built-in workflow only when its scope fits the current question. "
                "The plugin workflow exposes plugin tools on the next model step."
            ),
            "parameters": SkillRequest.model_json_schema(),
        },
        {
            "name": UPDATE_PLAN_TOOL,
            "description": (
                "For multi-step work, publish or replace a concise user-visible plan in the user's language. "
                "Use at most one in-progress step. Mark a step completed only after doing the work. "
                "Do not include secrets, raw logs, or private reasoning. Skip for straightforward lookups."
            ),
            "parameters": PlanRequest.model_json_schema(),
        },
    ]


class HelpHarness:
    """技能和计划仅在一次 Agent 运行内生效。"""

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.active_skills: Dict[SkillId, str] = {}
        self.plan: List[AISearchPlanStep] = []

    def execute(self, name: str, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], AISearchProgressEvent | None]:
        try:
            if name == LOAD_SKILL_TOOL:
                skill = SkillRequest.model_validate(arguments).skill
                if skill in self.active_skills:
                    return {"skill": skill, "message": "该技能已经加载，请继续执行"}, None
                if len(self.active_skills) >= 2:
                    return {"error": "单轮最多加载两个相关技能，请使用已加载的技能完成任务"}, None
                instructions = load_prompt(f"webui_help_skill_{skill}", locale=self.locale)
                self.active_skills[skill] = instructions
                return (
                    {"skill": skill, "instructions": instructions},
                    AISearchProgressEvent(stage="skill", skill=skill, status="completed"),
                )

            steps = PlanRequest.model_validate(arguments).steps
            if sum(step.status == "in_progress" for step in steps) > 1:
                return {"error": "工作计划同时最多有一个进行中的步骤"}, None
            if steps == self.plan:
                return {"message": "工作计划无变化"}, None
            self.plan = steps
            return {"message": "工作计划已更新"}, AISearchProgressEvent(stage="plan", plan=steps)
        except ValidationError:
            return {"error": "技能或计划参数无效，请按照工具定义提交"}, None
