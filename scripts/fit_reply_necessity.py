# ruff: noqa: E402
"""用无门控期间的 Planner 首轮记录，拟合一个预测 no_reply 的函数，并与现有必要性评分对比。

数据：logs/maisaka_prompt/planner 中 --since 之后的首轮请求（关闭必要性门控后，每批消息都会进 Planner）。
标签：no_reply = Planner 首轮没有调用工具，或只调用了 wait。
特征：现有必要性评分用到的信号（@、提及、问句/请求/征询、短反应、文本长度、存在感）+ 结构特征。
对照：用 reply_necessity.score_reply_necessity 原函数在同样输入上算出旧规则分数。

用法：
  uv run python scripts/fit_reply_necessity.py --since "2026-10-01 13:41:56"
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import argparse
import glob
import json
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import numpy as np

from src.config.config import global_config
from legacy_reply_necessity import (
    ReplyNecessityInput,
    get_reply_necessity_opinion_reason,
    get_reply_necessity_request_reason,
    has_reply_necessity_question,
    is_short_reaction_batch,
    score_reply_necessity,
    strip_reply_necessity_noise,
)

PROMPT_LOG_ROOT = PROJECT_ROOT / "logs" / "maisaka_prompt" / "planner"
OUTPUT_ROOT = PROJECT_ROOT / "logs" / "decision_eval"
MESSAGE_PATTERN = re.compile(r"<message ([^>]*)>\n?(.*)", re.S)
ATTR_PATTERN = re.compile(r'(\w+)="([^"]*)"')
PLANNER_ARTIFACT_TYPES = {"ReasoningItem", "AssistantMessageItem", "FunctionCallItem", "FunctionCallOutputItem"}
RECENT_WINDOW_SECONDS = 300
MIN_FEATURE_RATE = 0.02
"""特征非零比例低于该值时不进入模型。"""


# ─────────────────────────── 解析 ───────────────────────────


def _parts_text(item: Dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in item.get("parts", []) or [] if part.get("type") == "text")


def _parse_message(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if item.get("item_type") != "UserMessageItem":
        return None
    match = MESSAGE_PATTERN.match(_parts_text(item).strip())
    if not match:
        return None
    attrs = dict(ATTR_PATTERN.findall(match[1]))
    return {
        "msg_id": attrs.get("msg_id", ""),
        "user": attrs.get("user", ""),
        "quote": attrs.get("quote", ""),
        "time": attrs.get("time", ""),
        "self": attrs.get("is_self_message") == "true",
        "text": match[2].strip(),
    }


def _seconds(clock: str) -> Optional[int]:
    try:
        hours, minutes, seconds = (int(part) for part in clock.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None


def _gap(later: Optional[int], earlier: Optional[int]) -> float:
    if later is None or earlier is None:
        return 3600.0
    return float((later - earlier) % 86400)


def _is_first_round(items: Sequence[Dict[str, Any]]) -> bool:
    last_message = max((i for i, item in enumerate(items) if _parse_message(item)), default=-1)
    return last_message >= 0 and not any(
        item.get("item_type") == "FunctionCallOutputItem" for item in items[last_message + 1 :]
    )


def _pending_messages(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """本轮新到的消息：上一轮 Planner 记录之后的聊天消息；没有 Planner 记录时取麦麦最后发言之后的消息。"""

    last_artifact = max((i for i, item in enumerate(items) if item.get("item_type") in PLANNER_ARTIFACT_TYPES), default=-1)
    if last_artifact >= 0:
        return [m for item in items[last_artifact + 1 :] if (m := _parse_message(item))]
    messages = [m for item in items if (m := _parse_message(item))]
    last_self = max((i for i, m in enumerate(messages) if m["self"]), default=-1)
    return messages[last_self + 1 :] or messages[-1:]


# ─────────────────────────── 特征 ───────────────────────────


def extract(raw: Dict[str, Any], session: str, bot_names: Sequence[str]) -> Dict[str, Any]:
    items = raw["request_items"]
    messages = [m for item in items if (m := _parse_message(item))]
    pending = _pending_messages(items)
    pending_others = [m for m in pending if not m["self"]]
    texts = [m["text"] for m in pending_others]
    cleaned = [strip_reply_necessity_noise(text) for text in texts]
    combined = "\n".join(text for text in cleaned if text)
    self_ids = {m["msg_id"] for m in messages if m["self"]}

    has_at = any(any(f"@{name}" in text for name in bot_names) for text in texts)
    has_mention = any(any(name in text for name in bot_names) for text in texts)
    quotes_bot = any(m["quote"] in self_ids for m in pending_others if m["quote"])
    is_group = session.startswith("qq_group")
    is_direct = has_at or has_mention or quotes_bot or not is_group

    last_time = _seconds(messages[-1]["time"]) if messages else None
    last_self = max((i for i, m in enumerate(messages) if m["self"]), default=-1)
    self_time = _seconds(messages[last_self]["time"]) if last_self >= 0 else None
    window = [m for m in messages if _gap(last_time, _seconds(m["time"])) <= RECENT_WINDOW_SECONDS]
    recent_self = sum(m["self"] for m in window)

    old = score_reply_necessity(
        ReplyNecessityInput(
            texts=texts,
            pending_count=len(pending_others),
            trigger_threshold=1,
            has_at=has_at,
            has_mention=has_mention or quotes_bot,
            is_group_chat=is_group,
            focus_active=False,
            recent_self_replies=recent_self,
            recent_window_messages=len(window),
            effective_frequency=1.0,
            idle_seconds=0.0,
            idle_reached_average=False,
        )
    )
    features = {
        "at_bot": float(has_at),
        "mention_bot": float(has_mention),
        "quote_bot": float(quotes_bot),
        "private": float(not is_group),
        "question": float(any(has_reply_necessity_question(text) for text in cleaned)),
        "request": float(bool(get_reply_necessity_request_reason(combined, is_direct_context=is_direct))),
        "opinion": float(bool(get_reply_necessity_opinion_reason(combined, is_direct_context=is_direct))),
        "short_reaction": float(is_short_reaction_batch(cleaned)),
        "log_text_len": float(np.log1p(len(combined))),
        "n_pending": float(min(len(pending_others), 20)),
        "last_is_self": float(bool(messages) and messages[-1]["self"]),
        "log_secs_since_bot": float(np.log1p(_gap(last_time, self_time))),
        "recent_self_ratio": recent_self / max(1, len(window)),
    }
    return {"features": features, "old_score": old.score, "old_detail": old.detail}


def load_samples(since: float, bot_names: Sequence[str]) -> List[Dict[str, Any]]:
    samples = []
    for path in glob.glob(str(PROMPT_LOG_ROOT / "*" / "*.json")):
        try:
            if int(Path(path).stem) / 1000 < since:
                continue
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if raw.get("schema_version") != 6 or not _is_first_round(raw["request_items"]):
            continue
        tools = [i["tool_call"]["func_name"] for i in raw["output_items"] if i.get("item_type") == "FunctionCallItem"]
        session = Path(path).parent.name
        sample = extract(raw, session, bot_names)
        sample.update(
            sample_id=f"{session}_{Path(path).stem}",
            tools=tools,
            no_reply=int(not any(name != "wait" for name in tools)),
            total_tokens=int(raw["metadata"].get("total_tokens") or 0),
        )
        samples.append(sample)
    return sorted(samples, key=lambda s: s["sample_id"].rsplit("_", 1)[-1])


# ─────────────────────────── 拟合与评估 ───────────────────────────


def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    pos, neg = scores[labels == 1], scores[labels == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def _fit(x: np.ndarray, y: np.ndarray, l2: float = 2.0, steps: int = 4000, lr: float = 0.1) -> np.ndarray:
    xb = np.hstack([x, np.ones((len(x), 1))])
    w = np.zeros(xb.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-xb @ w))
        w -= lr * (xb.T @ (p - y) / len(y) + l2 * np.r_[w[:-1], 0] / len(y))
    return w


def _cv(x: np.ndarray, y: np.ndarray, folds: int = 5, repeats: int = 10) -> np.ndarray:
    total = np.zeros(len(y))
    for repeat in range(repeats):
        rng = np.random.default_rng(repeat)
        fold_of = np.empty(len(y), dtype=int)
        for label in (0, 1):
            idx = np.where(y == label)[0]
            rng.shuffle(idx)
            fold_of[idx] = np.arange(len(idx)) % folds
        for fold in range(folds):
            train, test = fold_of != fold, fold_of == fold
            mean, std = x[train].mean(0), x[train].std(0) + 1e-6
            w = _fit((x[train] - mean) / std, y[train])
            total[test] += 1 / (1 + np.exp(-np.hstack([(x[test] - mean) / std, np.ones((test.sum(), 1))]) @ w))
    return total / repeats


def _gate_row(label: str, p_reply: np.ndarray, y_no: np.ndarray, tokens: np.ndarray, max_miss: float) -> str:
    """p_reply 越低越倾向跳过；找到漏掉“该回复”不超过 max_miss 的最大跳过量。"""

    acted = y_no == 0
    best = None
    for threshold in np.unique(p_reply):
        skip = p_reply < threshold
        if (skip & acted).sum() / max(1, acted.sum()) <= max_miss:
            best = threshold
    if best is None:
        return f"| {label} | - | - | - |"
    skip = p_reply < best
    return (
        f"| {label} | {skip.mean() * 100:.1f}% | {(skip & ~acted).sum() / max(1, skip.sum()) * 100:.0f}% | "
        f"{tokens[skip].sum() / tokens.sum() * 100:.1f}% |"
    )


def analyze(samples: List[Dict[str, Any]]) -> str:
    all_names = list(samples[0]["features"].keys())
    all_x = np.array([[s["features"][k] for k in all_names] for s in samples])
    # 出现率低于 MIN_FEATURE_RATE 的特征样本太少，拟合出的权重是噪音，不进入模型
    keep = [i for i, name in enumerate(all_names) if (all_x[:, i] != 0).mean() >= MIN_FEATURE_RATE]
    dropped = [name for i, name in enumerate(all_names) if i not in keep]
    names = [all_names[i] for i in keep]
    x = all_x[:, keep]
    y = np.array([s["no_reply"] for s in samples])
    old = np.array([s["old_score"] for s in samples], dtype=float)
    tokens = np.array([s["total_tokens"] for s in samples], dtype=float)
    acted = 1 - y

    lines = ["# 拟合 no_reply（无门控期间的 Planner 首轮）", ""]
    lines.append(f"样本 {len(samples)} 条：no_reply {y.sum()}（{y.mean() * 100:.1f}%），行动 {acted.sum()}。")
    if len(samples) < 300:
        lines.append(f"**样本偏少（{len(samples)} 条），以下结果只用于检验流程，不能作为结论。**")
    lines.append("")

    fitted_reply = _cv(x, acted)
    lines.append("## 预测“该行动”的能力（AUC，0.5 = 随机）")
    lines.append("")
    lines.append("| 方法 | AUC |")
    lines.append("|---|---|")
    lines.append(f"| 旧必要性评分（原函数） | {_auc(old, acted):.3f} |")
    lines.append(f"| 拟合函数（随机交叉验证） | {_auc(fitted_reply, acted):.3f} |")
    split = int(len(samples) * 0.7)
    mean, std = x[:split].mean(0), x[:split].std(0) + 1e-6
    w_split = _fit((x[:split] - mean) / std, acted[:split])
    p_test = 1 / (1 + np.exp(-np.hstack([(x[split:] - mean) / std, np.ones((len(samples) - split, 1))]) @ w_split))
    lines.append(
        f"| 时间切分：旧规则（后 30% 样本） | {_auc(old[split:], acted[split:]):.3f} |"
    )
    lines.append(f"| 时间切分：拟合函数（用前 70% 训练，测后 30%） | {_auc(p_test, acted[split:]):.3f} |")
    lines.append("")
    if dropped:
        lines.append(f"出现率低于 {MIN_FEATURE_RATE * 100:.0f}% 未进入模型的特征：{', '.join(dropped)}")
        lines.append("")
    lines.append("旧规则按原阈值 80 分：")
    passed = old >= 80
    lines.append(
        f"- 放行 {passed.mean() * 100:.1f}%；放行里真行动 {acted[passed].mean() * 100 if passed.any() else 0:.0f}%；"
        f"拦下 {(~passed).sum()} 轮，其中 {acted[~passed].sum()} 轮 Planner 实际行动了（被误拦）"
    )
    lines.append("")
    lines.append("## 当门控用：漏掉的行动不超过 N% 时")
    lines.append("")
    for max_miss in (0.05, 0.10):
        lines.append(f"漏行动 ≤{int(max_miss * 100)}%：")
        lines.append("")
        lines.append("| 方法 | 跳过的轮次 | 跳过里真 no_reply | 省 Planner token |")
        lines.append("|---|---|---|---|")
        lines.append(_gate_row("旧必要性评分", old, y, tokens, max_miss))
        lines.append(_gate_row("拟合函数", fitted_reply, y, tokens, max_miss))
        lines.append("")

    mean, std = x.mean(0), x.std(0) + 1e-6
    w = _fit((x - mean) / std, acted)
    lines.append(f"原始单位的函数：logit = {w[-1] - (w[:-1] * mean / std).sum():+.3f} " + " ".join(
        f"{weight / scale:+.3f}×{name}" for name, weight, scale in zip(names, w[:-1], std, strict=True)
    ))
    lines.append("")
    lines.append("## 拟合权重（全量数据，标准化后；正数 = 越倾向行动）")
    lines.append("")
    lines.append("| 特征 | 权重 | 行动轮均值 | no_reply 轮均值 |")
    lines.append("|---|---|---|---|")
    for name, weight, mean_act, mean_no in sorted(
        zip(names, w[:-1], x[acted == 1].mean(0), x[acted == 0].mean(0), strict=True), key=lambda t: -abs(t[1])
    ):
        lines.append(f"| {name} | {weight:+.2f} | {mean_act:.2f} | {mean_no:.2f} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="关闭必要性门控的时间，如 2026-10-01 13:41:56")
    args = parser.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d %H:%M:%S").timestamp()
    bot_names = [name for name in [global_config.bot.nickname.strip(), *global_config.bot.alias_names] if name]

    samples = load_samples(since, bot_names)
    # 合并之前运行保存的样本：Prompt 日志会按上限滚动删除，已提取过的样本不能因此丢失
    known = {sample["sample_id"] for sample in samples}
    restored = 0
    for previous in sorted(OUTPUT_ROOT.glob("fit_reply_necessity_*/samples.jsonl")):
        for line in previous.read_text(encoding="utf-8").splitlines():
            sample = json.loads(line)
            if sample["sample_id"] not in known and int(sample["sample_id"].rsplit("_", 1)[-1]) / 1000 >= since:
                known.add(sample["sample_id"])
                samples.append(sample)
                restored += 1
    samples.sort(key=lambda s: int(s["sample_id"].rsplit("_", 1)[-1]))
    print(f"从日志读取 {len(samples) - restored} 条，从之前的运行恢复已被滚动删除的 {restored} 条")
    run_dir = OUTPUT_ROOT / f"fit_reply_necessity_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True)
    with (run_dir / "samples.jsonl").open("w", encoding="utf-8") as file:
        for sample in samples:
            file.write(json.dumps(sample, ensure_ascii=False) + "\n")
    summary = analyze(samples)
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(f"-> {run_dir}")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
