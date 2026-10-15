from collections.abc import Callable, Coroutine
from pathlib import Path
from string import Formatter
from threading import RLock
from typing import Any, Optional

import inspect

from src.common.paths import get_instance_root, resource_path
from src.common.i18n import get_locale
from src.common.i18n.loaders import DEFAULT_LOCALE, normalize_locale
from src.common.logger import get_logger
from src.common.prompt_i18n import get_prompt_cache_revision, list_prompt_templates, load_prompt_template


logger = get_logger("Prompt")

_LEFT_BRACE = chr(0xFDE9)
_RIGHT_BRACE = chr(0xFDEA)

PROJECT_ROOT = get_instance_root()
PROMPTS_DIR = resource_path("prompts")
DATA_DIR = PROJECT_ROOT / "data"
CUSTOM_PROMPTS_DIR = DATA_DIR / "custom_prompts"
CUSTOM_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
SUFFIX_PROMPT = ".prompt"


def _normalize_prompt_locale(locale: str | None = None) -> str:
    return normalize_locale(locale or get_locale())


def _get_prompt_locale_from_path(prompt_path: Path) -> str | None:
    try:
        relative_path = prompt_path.resolve().relative_to(PROMPTS_DIR.resolve())
    except ValueError:
        return None

    return relative_path.parts[0] if len(relative_path.parts) > 1 else None


def _custom_prompt_path(prompt_name: str, locale: str | None = None) -> Path:
    return CUSTOM_PROMPTS_DIR / _normalize_prompt_locale(locale) / f"{prompt_name}{SUFFIX_PROMPT}"


def _legacy_custom_prompt_path(prompt_name: str) -> Path:
    return CUSTOM_PROMPTS_DIR / f"{prompt_name}{SUFFIX_PROMPT}"


def _iter_custom_prompt_candidates(prompt_name: str, locale: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if locale:
        candidates.append(_custom_prompt_path(prompt_name, locale))
    candidates.append(_legacy_custom_prompt_path(prompt_name))
    return candidates


def _iter_active_custom_prompt_dirs() -> list[Path]:
    prompt_dirs = [
        CUSTOM_PROMPTS_DIR / DEFAULT_LOCALE,
        CUSTOM_PROMPTS_DIR / _normalize_prompt_locale(),
        CUSTOM_PROMPTS_DIR,
    ]
    return list(dict.fromkeys(prompt_dirs))


class Prompt:
    def __init__(self, prompt_name: str, template: str) -> None:
        self.prompt_name = prompt_name
        self.template = template
        self.prompt_render_context: dict[str, Callable[[str], str | Coroutine[Any, Any, str]]] = {}
        self._is_cloned = False
        self.__post_init__()

    def add_context(self, name: str, func_or_str: Callable[[str], str | Coroutine[Any, Any, str]] | str) -> None:
        if name in self.prompt_render_context:
            raise KeyError(f"Context function name '{name}' 已存在于 Prompt '{self.prompt_name}' 中")
        if isinstance(func_or_str, str):

            def tmp_func(_: str) -> str:
                return func_or_str

            render_function = tmp_func
        else:
            render_function = func_or_str
        self.prompt_render_context[name] = render_function

    def clone(self) -> "Prompt":
        return Prompt(self.prompt_name, self.template)

    @property
    def is_cloned(self) -> bool:
        return self._is_cloned

    def mark_as_cloned(self) -> None:
        self._is_cloned = True

    def __post_init__(self) -> None:
        if not self.prompt_name:
            raise ValueError("prompt_name 不能为空")
        if not self.template:
            raise ValueError("template 不能为空")
        tmp = self.template.replace("{{", _LEFT_BRACE).replace("}}", _RIGHT_BRACE)
        if "{}" in tmp:
            raise ValueError(r"模板中不允许使用未命名的占位符 '{}'")


class PromptManager:
    def __init__(self) -> None:
        self.prompts: dict[str, Prompt] = {}
        """存储 Prompt 实例，禁止直接从外部访问，否则将引起不可知后果"""
        self._context_construct_functions: dict[str, tuple[Callable[[str], str | Coroutine[Any, Any, str]], str]] = {}
        """存储上下文构造函数及其所属模块"""
        self._formatter = Formatter()  # 仅用来解析模板
        """模板解析器"""
        self._prompt_to_save: set[str] = set()
        """需要保存的 Prompt 名称集合"""
        self._prompt_save_locales: dict[str, str] = {}
        """Prompt 保存时使用的语言目录"""
        self._loaded_from_files = False
        """是否已经从 prompt 文件加载过模板"""
        self._loaded_cache_revision = get_prompt_cache_revision()
        """上次加载时的 prompt 文件缓存版本"""
        self._reload_lock = RLock()

    def add_prompt(self, prompt: Prompt, need_save: bool = False, prompt_locale: str | None = None) -> None:
        """
        添加一个新的 Prompt 实例

        Args:
            prompt (Prompt): 要添加的 Prompt 实例
            need_save (bool): 是否需要保存该 Prompt，默认为 False
        Raises:
            KeyError: 如果 Prompt 名称已存在则引发该异常
        """
        if prompt.prompt_name in self.prompts or prompt.prompt_name in self._context_construct_functions:
            # 确保名称无冲突
            raise KeyError(f"Prompt name '{prompt.prompt_name}' 已存在")
        self.prompts[prompt.prompt_name] = prompt
        if need_save:
            self._prompt_to_save.add(prompt.prompt_name)
            self._prompt_save_locales[prompt.prompt_name] = _normalize_prompt_locale(prompt_locale)

    def remove_prompt(self, prompt_name: str) -> None:
        """
        移除一个已存在的 Prompt 实例
        Args:
            prompt_name (str): 要移除的 Prompt 名称
        Raises:
            KeyError: 如果 Prompt 名称不存在则引发该异常
        """
        if prompt_name not in self.prompts:
            raise KeyError(f"Prompt name '{prompt_name}' 不存在")
        del self.prompts[prompt_name]
        if prompt_name in self._prompt_to_save:
            self._prompt_to_save.remove(prompt_name)
        self._prompt_save_locales.pop(prompt_name, None)

    def replace_prompt(self, prompt: Prompt, need_save: bool = False, prompt_locale: str | None = None) -> None:
        """
        替换一个已存在的 Prompt 实例
        Args:
            prompt (Prompt): 要替换的 Prompt 实例
            need_save (bool): 是否需要保存该 Prompt，默认为 False
        Raises:
            KeyError: 如果 Prompt 名称不存在则引发该异常
        """
        if prompt.prompt_name not in self.prompts:
            raise KeyError(f"Prompt name '{prompt.prompt_name}' 不存在，无法替换")
        self.prompts[prompt.prompt_name] = prompt
        if need_save:
            self._prompt_to_save.add(prompt.prompt_name)
            self._prompt_save_locales[prompt.prompt_name] = _normalize_prompt_locale(prompt_locale)
        elif prompt.prompt_name in self._prompt_to_save:
            self._prompt_to_save.remove(prompt.prompt_name)
            self._prompt_save_locales.pop(prompt.prompt_name, None)

    def add_context_construct_function(self, name: str, func: Callable[[str], str | Coroutine[Any, Any, str]]) -> None:
        """
        添加一个上下文构造函数

        Args:
            name (str): 上下文名称
            func (Callable[[str], str | Coroutine[Any, Any, str]]): 构造函数，接受 Prompt 名称作为参数，返回字符串或返回字符串的协程
        Raises:
            KeyError: 如果上下文名称已存在则引发该异常
        """
        if name in self._context_construct_functions or name in self.prompts:
            raise KeyError(f"Construct function name '{name}' 已存在")
        # 获取调用栈
        frame = inspect.currentframe()
        if not frame:
            # 不应该出现的情况
            raise RuntimeError("无法获取调用栈")
        caller_frame = frame.f_back
        if not caller_frame:
            # 不应该出现的情况
            raise RuntimeError("无法获取调用栈的上一级")
        caller_module = caller_frame.f_globals.get("__name__", "unknown")
        if caller_module == "unknown":
            logger.warning("无法获取调用函数的模块名，使用 'unknown' 作为默认值")

        self._context_construct_functions[name] = func, caller_module

    def get_prompt(self, prompt_name: str) -> Prompt:
        """
        获取指定名称的 Prompt 实例的克隆

        Args:
            prompt_name (str): 要获取的 Prompt 名称
        Returns:
            return (Prompt): 指定名称的 Prompt 实例的克隆
        Raises:
            KeyError: 如果 Prompt 名称不存在则引发该异常
        """
        self._reload_if_prompt_cache_changed(prompt_name)
        if prompt_name not in self.prompts:
            raise KeyError(f"Prompt name '{prompt_name}' 不存在")
        prompt = self.prompts[prompt_name].clone()
        prompt.mark_as_cloned()
        return prompt

    async def render_prompt(self, prompt: Prompt) -> str:
        """
        渲染一个 Prompt 实例

        Args:
            prompt (Prompt): 要渲染的 Prompt 实例
        Returns:
            return (str): 渲染后的字符串
        Raises:
            ValueError: 如果传入的 Prompt 实例不是通过 get_prompt 方法获取的克隆实例则引发该异常
        """
        if not prompt.is_cloned:
            raise ValueError(
                "只能渲染通过 PromptManager.get_prompt 方法获取的 Prompt 实例，你可能对原始实例进行了修改和渲染操作"
            )
        return await self._render(prompt)

    async def _render(
        self,
        prompt: Prompt,
        recursive_level: int = 0,
        additional_construction_function_dict: dict[str, Callable[[str], str | Coroutine[Any, Any, str]]] | None = None,
    ) -> str:
        if additional_construction_function_dict is None:
            additional_construction_function_dict = {}
        prompt.template = prompt.template.replace("{{", _LEFT_BRACE).replace("}}", _RIGHT_BRACE)
        if recursive_level > 10:
            raise RecursionError("递归层级过深，可能存在循环引用")
        field_block = {field_name for _, field_name, _, _ in self._formatter.parse(prompt.template) if field_name}
        rendered_fields: dict[str, str] = {}
        for field_name in field_block:
            if field_name in self.prompts:
                nested_prompt = self.get_prompt(field_name)
                merged_context = additional_construction_function_dict | prompt.prompt_render_context
                rendered_fields[field_name] = await self._render(
                    nested_prompt,
                    recursive_level + 1,
                    merged_context,
                )
            elif field_name in prompt.prompt_render_context:
                # 优先使用内部构造函数
                func = prompt.prompt_render_context[field_name]
                rendered_fields[field_name] = await self._get_function_result(
                    func,
                    prompt.prompt_name,
                    field_name,
                    is_prompt_context=True,
                )
            elif field_name in self._context_construct_functions:
                # 随后查找全局构造函数
                func, module = self._context_construct_functions[field_name]
                rendered_fields[field_name] = await self._get_function_result(
                    func,
                    prompt.prompt_name,
                    field_name,
                    is_prompt_context=False,
                    module=module,
                )
            elif field_name in additional_construction_function_dict:
                # 最后查找额外传入的构造函数
                func = additional_construction_function_dict[field_name]
                rendered_fields[field_name] = await self._get_function_result(
                    func,
                    prompt.prompt_name,
                    field_name,
                    is_prompt_context=True,
                )
            else:
                raise KeyError(f"Prompt '{prompt.prompt_name}' 中缺少必要的内容块或构建函数: '{field_name}'")
        rendered_template = prompt.template.format(**rendered_fields)
        return rendered_template.replace(_LEFT_BRACE, "{").replace(_RIGHT_BRACE, "}")

    def save_prompts(self) -> None:
        """
        保存需要保存的 Prompt 实例到自定义目录，将清空未注册的自定义 Prompt 文件
        Raises:
            Exception: 如果在保存过程中出现任何文件操作错误则引发该异常
        """
        # 只清理当前加载语言层的 Prompt 文件，避免误删其它语言的用户自定义模板。
        for prompt_dir in _iter_active_custom_prompt_dirs():
            if not prompt_dir.exists():
                continue
            for prompt_file in prompt_dir.glob(f"*{SUFFIX_PROMPT}"):
                try:
                    prompt_file.unlink()
                except Exception as exc:
                    logger.error(f"删除自定义 Prompt 文件 '{prompt_file}' 时出错，错误信息: {exc}")
                    raise
        for prompt_name in self._prompt_to_save:
            prompt = self.prompts[prompt_name]
            prompt_locale = self._prompt_save_locales.get(prompt_name, _normalize_prompt_locale())
            file_path = _custom_prompt_path(prompt_name, prompt_locale)
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(prompt.template, encoding="utf-8")
            except Exception as exc:
                logger.error(f"保存 Prompt '{prompt_name}' 时出错，文件路径: '{file_path}'，错误信息: {exc}")
                raise

    def _load_prompt_template(self, prompt_name: str, source_path: Path) -> tuple[str, bool, str | None]:
        prompt_locale = _get_prompt_locale_from_path(source_path)
        resolved_template = load_prompt_template(
            prompt_name,
            locale=prompt_locale,
            prompts_root=PROMPTS_DIR,
            custom_prompts_root=CUSTOM_PROMPTS_DIR,
            include_legacy_custom=True,
        )
        return resolved_template.template, resolved_template.customized, resolved_template.locale or prompt_locale

    def load_prompts(self) -> None:
        """
        加载全部 Prompt 实例，优先加载自定义目录下的文件，支持覆盖加载
        Raises:
            Exception: 如果在加载过程中出现任何文件操作错误则引发该异常
        """
        with self._reload_lock:
            loaded_prompts: dict[str, Prompt] = {}
            prompt_to_save: set[str] = set()
            prompt_save_locales: dict[str, str] = {}

            def add_loaded_prompt(prompt: Prompt, need_save: bool = False, prompt_locale: str | None = None) -> None:
                if prompt.prompt_name in loaded_prompts or prompt.prompt_name in self._context_construct_functions:
                    raise KeyError(f"Prompt name '{prompt.prompt_name}' 已存在")
                loaded_prompts[prompt.prompt_name] = prompt
                if need_save:
                    prompt_to_save.add(prompt.prompt_name)
                    prompt_save_locales[prompt.prompt_name] = _normalize_prompt_locale(prompt_locale)

            prompt_templates = list_prompt_templates(prompts_root=PROMPTS_DIR)
            for prompt_name, prompt_template in prompt_templates.items():
                try:
                    template, need_save, prompt_locale = self._load_prompt_template(prompt_name, prompt_template.path)
                    add_loaded_prompt(
                        Prompt(prompt_name=prompt_name, template=template),
                        need_save=need_save,
                        prompt_locale=prompt_locale,
                    )
                except Exception as exc:
                    logger.error(f"加载 Prompt 文件 '{prompt_template.path}' 时出错，错误信息: {exc}")
                    raise
            loaded_custom_prompts = set(prompt_templates)
            for prompt_dir in _iter_active_custom_prompt_dirs():
                if not prompt_dir.exists():
                    continue
                prompt_locale = prompt_dir.name if prompt_dir.parent == CUSTOM_PROMPTS_DIR else None
                for prompt_file in prompt_dir.glob(f"*{SUFFIX_PROMPT}"):
                    if prompt_file.stem in loaded_custom_prompts:
                        continue  # 已经加载过了，跳过
                    try:
                        resolved_template = load_prompt_template(
                            prompt_file.stem,
                            locale=prompt_locale,
                            prompts_root=PROMPTS_DIR,
                            custom_prompts_root=CUSTOM_PROMPTS_DIR,
                            include_legacy_custom=True,
                        )
                        add_loaded_prompt(
                            Prompt(prompt_name=prompt_file.stem, template=resolved_template.template),
                            need_save=True,
                            prompt_locale=resolved_template.locale or prompt_locale,
                        )
                        loaded_custom_prompts.add(prompt_file.stem)
                    except Exception as exc:
                        logger.error(f"加载自定义 Prompt 文件 '{prompt_file}' 时出错，错误信息: {exc}")
                        raise
            self.prompts = loaded_prompts
            self._prompt_to_save = prompt_to_save
            self._prompt_save_locales = prompt_save_locales
            self._loaded_from_files = True
            self._loaded_cache_revision = get_prompt_cache_revision()

    def _reload_if_prompt_cache_changed(self, requested_prompt_name: str) -> None:
        if not self._loaded_from_files:
            return
        if self._loaded_cache_revision == get_prompt_cache_revision():
            return
        previous_templates = {prompt_name: prompt.template for prompt_name, prompt in self.prompts.items()}
        self.load_prompts()
        current_templates = {prompt_name: prompt.template for prompt_name, prompt in self.prompts.items()}
        changed_prompt_names = sorted(
            prompt_name
            for prompt_name in set(previous_templates) | set(current_templates)
            if previous_templates.get(prompt_name) != current_templates.get(prompt_name)
        )
        if changed_prompt_names:
            logger.info(
                "Prompt 文件缓存已更新，已重新加载 PromptManager 模板，"
                f"触发 Prompt: {requested_prompt_name}，变更 Prompt: {', '.join(changed_prompt_names)}"
            )
        else:
            logger.info(
                "Prompt 文件缓存已更新，已重新加载 PromptManager 模板，"
                f"触发 Prompt: {requested_prompt_name}，未发现模板内容变化"
            )

    async def _get_function_result(
        self,
        func: Callable[[str], str | Coroutine[Any, Any, str]],
        prompt_name: str,
        field_name: str,
        is_prompt_context: bool,
        module: Optional[str] = None,
    ) -> str:
        try:
            res = func(prompt_name)
            if isinstance(res, Coroutine):
                res = await res
            return res
        except Exception as exc:
            if is_prompt_context:
                logger.error(f"调用 Prompt '{prompt_name}' 内部上下文构造函数 '{field_name}' 时出错，错误信息: {exc}")
            else:
                logger.error(f"调用上下文构造函数 '{field_name}' 时出错，所属模块: '{module}'，错误信息: {exc}")
            raise


prompt_manager = PromptManager()
