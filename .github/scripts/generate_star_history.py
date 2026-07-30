from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import argparse
import json
import math
import os


API_ROOT = "https://api.github.com"
STAR_MEDIA_TYPE = "application/vnd.github.star+json"


def request_json(path: str, token: str) -> Tuple[Any, Dict[str, str]]:
    request = Request(f"{API_ROOT}{path}")
    request.add_header("Accept", STAR_MEDIA_TYPE)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", "MaiBot-star-history")
    request.add_header("X-GitHub-Api-Version", "2026-03-10")

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), dict(response.headers)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API 请求失败: {error.code} {error.reason}: {body}") from error


def collect_star_timestamps(repo: str, token: str) -> List[datetime]:
    encoded_repo = "/".join(quote(part, safe="") for part in repo.split("/", maxsplit=1))
    timestamps: List[datetime] = []
    page = 1

    while True:
        data, headers = request_json(
            f"/repos/{encoded_repo}/stargazers?per_page=100&page={page}",
            token,
        )
        if not isinstance(data, list):
            raise RuntimeError("GitHub Stargazers API 返回了非列表数据。")

        for item in data:
            starred_at = item.get("starred_at")
            if not isinstance(starred_at, str):
                raise RuntimeError("GitHub Stargazers API 未返回 starred_at，请检查令牌权限。")
            timestamps.append(datetime.fromisoformat(starred_at.replace("Z", "+00:00")))

        if 'rel="next"' not in headers.get("Link", ""):
            break
        page += 1

    if not timestamps:
        raise RuntimeError("仓库没有可用于生成历史图的 Star 数据。")

    timestamps.sort()
    return timestamps


def build_daily_points(timestamps: List[datetime]) -> List[Tuple[date, int]]:
    daily_counts: Dict[date, int] = {}
    for timestamp in timestamps:
        star_date = timestamp.date()
        daily_counts[star_date] = daily_counts.get(star_date, 0) + 1

    points: List[Tuple[date, int]] = []
    total = 0
    for star_date in sorted(daily_counts):
        total += daily_counts[star_date]
        points.append((star_date, total))
    return points


def calculate_y_axis(max_value: int) -> Tuple[int, int]:
    rough_step = max_value / 5
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude

    if normalized <= 1:
        multiplier = 1
    elif normalized <= 2:
        multiplier = 2
    elif normalized <= 5:
        multiplier = 5
    else:
        multiplier = 10

    step = max(1, int(multiplier * magnitude))
    axis_max = math.ceil(max_value / step) * step
    return axis_max, step


def format_number(value: int) -> str:
    return f"{value:,}"


def render_svg(repo: str, points: List[Tuple[date, int]], generated_at: datetime) -> str:
    width = 1200
    height = 560
    chart_left = 92
    chart_top = 108
    chart_width = 1036
    chart_height = 352
    chart_bottom = chart_top + chart_height

    start_date = points[0][0]
    end_date = max(points[-1][0], generated_at.date())
    date_span = max(1, (end_date - start_date).days)
    star_count = points[-1][1]
    y_axis_max, y_step = calculate_y_axis(star_count)

    def x_position(value: date) -> float:
        elapsed = (value - start_date).days
        return chart_left + chart_width * elapsed / date_span

    def y_position(value: int) -> float:
        return chart_bottom - chart_height * value / y_axis_max

    line_points = " ".join(f"{x_position(day):.2f},{y_position(count):.2f}" for day, count in points)
    first_x = x_position(points[0][0])
    last_x = x_position(points[-1][0])
    area_path = (
        f"M {first_x:.2f} {chart_bottom} "
        + " ".join(f"L {x_position(day):.2f} {y_position(count):.2f}" for day, count in points)
        + f" L {last_x:.2f} {chart_bottom} Z"
    )

    y_grid: List[str] = []
    for value in range(0, y_axis_max + 1, y_step):
        y = y_position(value)
        y_grid.append(
            f'  <line class="grid" x1="{chart_left}" y1="{y:.2f}" x2="{chart_left + chart_width}" y2="{y:.2f}"/>\n'
            f'  <text class="axis-label" x="{chart_left - 16}" y="{y + 5:.2f}" text-anchor="end">'
            f"{escape(format_number(value))}</text>"
        )

    x_grid: List[str] = []
    for index in range(6):
        tick_date = start_date + timedelta(days=round(date_span * index / 5))
        x = x_position(tick_date)
        x_grid.append(
            f'  <line class="grid" x1="{x:.2f}" y1="{chart_top}" x2="{x:.2f}" y2="{chart_bottom}"/>\n'
            f'  <text class="axis-label" x="{x:.2f}" y="{chart_bottom + 34}" text-anchor="middle">'
            f"{tick_date:%Y-%m}</text>"
        )

    generated_text = generated_at.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M UTC+8")
    first_star_text = start_date.strftime("%Y-%m-%d")

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">{escape(repo)} Star 历史</title>
  <desc id="desc">从 {first_star_text} 至今的累计 Star 趋势，共 {star_count} 个 Stars。</desc>
  <defs>
    <linearGradient id="area" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#c7461f" stop-opacity="0.34"/>
      <stop offset="100%" stop-color="#c7461f" stop-opacity="0.03"/>
    </linearGradient>
    <filter id="shadow" x="-5%" y="-8%" width="110%" height="116%">
      <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#2b2318" flood-opacity="0.12"/>
    </filter>
  </defs>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }}
    .background {{ fill: #f7f1e4; }}
    .panel {{ fill: #fffdf8; stroke: #dfd4c1; }}
    .title {{ fill: #0a4a52; }}
    .muted {{ fill: #6f7778; }}
    .grid {{ stroke: #ddd5c7; stroke-width: 1; }}
    .axis-label {{ fill: #6f7778; font-size: 13px; }}
    @media (prefers-color-scheme: dark) {{
      .background {{ fill: #0d1117; }}
      .panel {{ fill: #161b22; stroke: #30363d; }}
      .title {{ fill: #f0f6fc; }}
      .muted, .axis-label {{ fill: #8b949e; }}
      .grid {{ stroke: #30363d; }}
    }}
  </style>
  <rect class="background" width="{width}" height="{height}"/>
  <rect class="panel" x="20" y="20" width="{width - 40}" height="{height - 40}" rx="14" filter="url(#shadow)"/>
  <rect x="52" y="52" width="84" height="8" fill="#c7461f"/>
  <text class="title" x="52" y="91" font-size="25" font-weight="700">{escape(repo)} Star 历史</text>
  <text class="muted" x="52" y="510" font-size="13">首个 Star：{first_star_text}</text>
  <text class="muted" x="{width - 52}" y="510" font-size="13" text-anchor="end">更新于 {generated_text}</text>
  <text class="title" x="{width - 52}" y="69" font-size="30" font-weight="700" text-anchor="end">{format_number(star_count)}</text>
  <text class="muted" x="{width - 52}" y="91" font-size="13" text-anchor="end">Stars</text>
{"\n".join(y_grid)}
{"\n".join(x_grid)}
  <path d="{area_path}" fill="url(#area)"/>
  <polyline points="{line_points}" fill="none" stroke="#c7461f" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{last_x:.2f}" cy="{y_position(star_count):.2f}" r="6" fill="#fffdf8" stroke="#c7461f" stroke-width="4"/>
</svg>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="生成仓库 Star 历史 SVG。")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/repo")
    parser.add_argument("--output", default="depends-data/star-history.svg", help="SVG 输出路径")
    args = parser.parse_args()

    if not args.repo:
        raise RuntimeError("缺少仓库名称，请传入 --repo 或设置 GITHUB_REPOSITORY。")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("缺少 GITHUB_TOKEN，无法读取受限的 Stargazers 时间戳。")

    timestamps = collect_star_timestamps(args.repo, token)
    points = build_daily_points(timestamps)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_svg(args.repo, points, datetime.now(timezone.utc)),
        encoding="utf-8",
        newline="\n",
    )
    print(f"已生成 {output}，共 {format_number(len(timestamps))} 个 Stars。")


if __name__ == "__main__":
    main()
