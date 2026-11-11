"""插件指令目录的分组、文本与 WebUI 风格图片模板。"""

from html import escape
from math import ceil
from typing import Any, Dict, List, Tuple


CommandGroups = List[Tuple[str, List[Dict[str, Any]]]]


def collect_command_groups(plugins: Dict[str, Any], plugin_id: str = "") -> CommandGroups:
    """保留注册信息中的插件归属，包含已禁用指令并按名称排序。"""
    groups: CommandGroups = []
    for name, info in sorted(plugins.items()):
        if plugin_id and name != plugin_id:
            continue
        commands = [item for item in info["components"] if item["type"].upper() == "COMMAND"]
        if commands:
            groups.append((info.get("display_name") or name, sorted(commands, key=lambda item: item["name"])))
    return groups


def command_details(command: Dict[str, Any]) -> List[Tuple[str, str]]:
    """展示用户可读的说明、别名与权限。"""
    metadata = command["metadata"]
    details = [("说明", str(metadata.get("description", "") or "未提供说明"))]
    if metadata.get("aliases"):
        details.append(("别名", "、".join(metadata["aliases"])))
    if metadata.get("permission"):
        details.append(("权限", str(metadata["permission"])))
    return details


def format_command_text(groups: CommandGroups) -> str:
    """生成与图片内容一致的文本目录。"""
    lines = ["插件指令列表（含已禁用指令）"]
    for plugin_id, commands in groups:
        lines.append(f"\n【{plugin_id}】（{len(commands)} 条）")
        for command in commands:
            status = "" if command["enabled"] else " [已禁用]"
            lines.append(f"  {command['name']}{status}")
            lines.extend(f"    {label}：{value}" for label, value in command_details(command))
    return "\n".join(lines)


# 对齐 dashboard/src/index.css 的默认浅色主题：橙色主色、冷灰边框与卡片。
_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; background: white; color: hsl(222.2 84% 4.9%);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif; }
main { padding: 36px; }
.catalog.two-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.column { min-width: 0; }
header { border-bottom: 1px solid hsl(188.5 20% 91.4%); padding-bottom: 20px; margin-bottom: 24px; }
.brand { color: hsl(28.9 94.8% 45.1%); font-size: 13px; font-weight: 600; letter-spacing: 2px; }
h1 { font-size: 28px; margin: 6px 0; letter-spacing: -.02em; }
.muted, dt, footer { color: hsl(188.5 20% 46.9%); }
section { border: 1px solid hsl(188.5 20% 91.4%); border-radius: 12px;
  background: hsl(188.5 14% 98.6%); margin-top: 18px; overflow: hidden; }
h2 { margin: 0; padding: 16px 20px; font-size: 18px; border-bottom: 1px solid hsl(188.5 20% 91.4%); }
article { padding: 16px 20px; background: white; }
article + article { border-top: 1px solid hsl(188.5 20% 91.4%); }
h3 { margin: 0; font-size: 17px; }
.badge { display: inline-block; margin-left: 10px; padding: 1px 8px; border-radius: 6px;
  background: hsl(112.7 40.2% 94%); color: hsl(112.7 40.2% 30%); font-size: 12px; font-weight: 500; }
.disabled { background: hsl(188.5 12% 96%); color: hsl(188.5 20% 46.9%); }
dl { margin: 8px 0 0; display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 5px 12px; }
dd { margin: 0; white-space: pre-wrap; }
h2, h3, dd { overflow-wrap: anywhere; }
code { font: 14px/1.6 'JetBrains Mono', Consolas, 'Microsoft YaHei', monospace; }
footer { margin-top: 20px; font-size: 13px; }
"""


_STYLE_NAMES = {0: "modern", 1: "future-retro", 2: "millennium"}

# 配色与材质对应 dashboard/src/index.css、styles/millennium.css，
# 跟随服务端 webui.webui_style；浏览器私有的深浅色与字体选择不在该配置中。
_STYLE_VARIANTS = {
    0: "",
    1: """
body { background: #f3e3cc; color: #0a4550; font-weight: 700;
  background-image: repeating-linear-gradient(0deg, #0a455008 0 1px, transparent 1px 4px); }
header { border-bottom: 3px solid #0a4550; }
.brand { color: #c24d24; }
.muted, dt, footer { color: #0d4650ad; }
section { border: 2px solid #0d4650d1; border-radius: 4px; background: #f3e3cc;
  box-shadow: 4px 4px 0 #49301624; }
h2 { border-bottom: 2px solid #0d4650d1; background: #dec5a5; }
article { background: transparent; }
article + article { border-top: 1px solid #0d465066; }
.badge { border-radius: 2px; border: 1px solid #0a4550; }
.disabled { background: #dec5a5; color: #0a4550; }
""",
    2: """
body { background: #ddd4bf; color: #4a443a;
  background-image: radial-gradient(#5a544a17 1px, transparent 1px); background-size: 4px 4px; }
header { padding: 20px 24px; background: #ebe4d2; border: 2px solid #5a544a;
  box-shadow: inset 2px 2px 0 #f6f1e3, inset -2px -2px 0 #bdb39c, 0 5px 0 #9c927b; }
.brand { color: #2a7d50; }
.muted, dt, footer { color: #5f5849; }
section { background: #ebe4d2; border: 2px solid #5a544a; border-radius: 0; padding: 12px;
  box-shadow: inset 2px 2px 0 #f6f1e3, inset -2px -2px 0 #bdb39c, 0 5px 0 #9c927b; }
h2 { border: 0; padding: 6px 8px 16px; letter-spacing: .04em; }
article { background: #fbf9f0; color: #3a3833; border: 1px solid #bdb39c;
  box-shadow: inset 3px 3px 0 #00000029; }
article + article { border-top: 1px solid #d3cec0; }
.badge { border-radius: 0; color: #2f3a2b; background: #8fd6a0; border: 1px solid #5a544a;
  box-shadow: inset 0 -2px 0 #5fa873, inset 1px 1px 0 #fffaf0; }
.disabled { background: #f3eee1; color: #5f5849; box-shadow: inset 0 -2px 0 #c2b8a1; }
""",
}


def _estimate_command_height(command: Dict[str, Any], text_capacity: int = 82) -> int:
    """按中文全宽字符保守估算换行高度，让常规目录优先放进一张长图。"""
    height = 72
    for _, value in command_details(command):
        lines = sum(
            max(1, ceil(sum(2 if ord(char) > 127 else 1 for char in line) / text_capacity))
            for line in value.split("\n")
        )
        height += lines * 26 + 5
    return height


def _balance_columns(groups: CommandGroups, text_capacity: int) -> List[CommandGroups]:
    """按预计高度顺序分成两列，续列时重复插件标题且不拆开单条指令。"""
    target = (
        sum(
            90 + sum(_estimate_command_height(command, text_capacity) for command in commands) for _, commands in groups
        )
        / 2
    )
    result: List[CommandGroups] = [[], []]
    column = 0
    height = 0
    for name, commands in groups:
        new_group = True
        for command in commands:
            command_height = _estimate_command_height(command, text_capacity)
            if column == 0 and result[0] and height + command_height / 2 >= target:
                column = 1
                new_group = True
            if new_group:
                result[column].append((name, []))
                height += 90
                new_group = False
            result[column][-1][1].append(command)
            height += command_height
    return [column_groups for column_groups in result if column_groups]


def _render_group(name: str, commands: List[Dict[str, Any]]) -> str:
    """渲染一个分组，仅为禁用指令显示状态标签。"""
    rows = []
    for command in commands:
        badge = "" if command["enabled"] else "<span class='badge disabled'>已禁用</span>"
        details = "".join(
            f"<dt>{escape(label)}</dt><dd><code>{escape(value)}</code></dd>"
            for label, value in command_details(command)
        )
        rows.append(f"<article><h3>{escape(command['name'])}{badge}</h3><dl>{details}</dl></article>")
    return f"<section><h2>{escape(name)}</h2>{''.join(rows)}</section>"


def build_command_pages(groups: CommandGroups, webui_style: int) -> List[str]:
    """短目录单列，长目录双列；双列仍过长时再分页。"""
    style_name = _STYLE_NAMES[webui_style]
    estimated_height = 300 + sum(
        90 + sum(_estimate_command_height(command) for command in commands) for _, commands in groups
    )
    columns = 2 if estimated_height > 1800 else 1
    text_capacity = 32 if columns == 2 else 82
    content_height_limit = (4800 - 300) * columns
    pages: List[CommandGroups] = []
    current: CommandGroups = []
    height = 0
    for plugin_id, commands in groups:
        for index, command in enumerate(commands):
            new_group = index == 0 or not current
            command_height = _estimate_command_height(command, text_capacity) + (90 if new_group else 0)
            if current and height + command_height > content_height_limit:
                pages.append(current)
                current, height = [], 0
                command_height = _estimate_command_height(command, text_capacity) + 90
            if new_group or not current:
                current.append((plugin_id, []))
            current[-1][1].append(command)
            height += command_height
    if current:
        pages.append(current)

    documents: List[str] = []
    total_commands = sum(len(commands) for _, commands in groups)
    for number, page in enumerate(pages, 1):
        page_columns = _balance_columns(page, text_capacity) if columns == 2 else [page]
        content = "".join(
            "<div class='column'>" + "".join(_render_group(name, commands) for name, commands in column) + "</div>"
            for column in page_columns
        )
        page_label = f" · 第 {number} / {len(pages)} 页" if len(pages) > 1 else ""
        layout = "catalog two-columns" if columns == 2 else "catalog"
        documents.append(
            f'<!doctype html><html lang="zh-CN" data-dashboard-style="{style_name}"><meta charset="utf-8">'
            f"<style>{_STYLE}{_STYLE_VARIANTS[webui_style]}</style>"
            "<body><main><header><div class='brand'>MAIBOT · PLUGINS</div>"
            f"<h1>插件指令列表</h1><div class='muted'>{len(groups)} 个插件 · {total_commands} 条指令"
            f"{page_label}</div></header><div class='{layout}'>{content}</div>"
            "<footer>包含已禁用指令</footer></main></body></html>"
        )
    return documents
