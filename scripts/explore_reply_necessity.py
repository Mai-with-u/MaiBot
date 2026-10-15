# ruff: noqa: E402
"""系统性探索预测 Planner no_reply 的函数：更多特征、多种模型、语义嵌入。

数据：无必要性门控期间（--since 之后）的 Planner 首轮请求，原始日志先备份到
logs/decision_eval/reply_necessity_raw/，避免被 Prompt 预览上限滚动删除。

评估：滚动时间验证。按时间排序后，从 40% 处开始切成 5 段，每段只用它之前的全部样本训练。
所有模型在同样的切分上比较，阈值相关指标在合并后的样本外预测上计算。

用法（sklearn 只用于离线分析，不加入项目依赖）：
  uv run --with scikit-learn python scripts/explore_reply_necessity.py --since "2026-10-01 13:41:56"
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

import argparse
import asyncio
import glob
import json
import math
import os
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

import numpy as np

# 先导入编排器，避免循环导入
from src.llm_models.utils_model import LLMOrchestrator
from src.config.config import global_config

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from fit_reply_necessity import _gap, _is_first_round, _parse_message, _pending_messages, _seconds, extract

PROMPT_LOG_ROOT = PROJECT_ROOT / "logs" / "maisaka_prompt" / "planner"
RAW_DIR = PROJECT_ROOT / "logs" / "decision_eval" / "reply_necessity_raw"
EMBED_CACHE = PROJECT_ROOT / "logs" / "decision_eval" / "reply_necessity_embeddings.json"
OUTPUT_ROOT = PROJECT_ROOT / "logs" / "decision_eval"
PLACEHOLDER_PREFIXES = ("[表情包", "[图片", "[消息类型]", "[CQ:image", "[语音", "[文件]")


# ─────────────────────────── 数据 ───────────────────────────


def backup_raw(since: float) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for path in glob.glob(str(PROMPT_LOG_ROOT / "*" / "*.json")):
        try:
            if int(Path(path).stem) / 1000 < since:
                continue
        except ValueError:
            continue
        target = RAW_DIR / f"{Path(path).parent.name}_{Path(path).name}"
        if not target.exists():
            shutil.copyfile(path, target)


def rich_features(raw: Dict[str, Any], session: str, ts: float, bot_names: Sequence[str]) -> Dict[str, float]:
    """在原有特征基础上补充对话关系、群活跃度、Planner 上一轮动作、时段等特征。"""

    items = raw["request_items"]
    messages = [m for item in items if (m := _parse_message(item))]
    pending = _pending_messages(items)
    others = [m for m in pending if not m["self"]]
    last_time = _seconds(messages[-1]["time"]) if messages else None
    by_id = {m["msg_id"]: m for m in messages}
    self_msgs = [m for m in messages if m["self"]]
    last_self = self_msgs[-1] if self_msgs else None

    # 最近 10 分钟内和麦麦有直接互动（引用麦麦 / 被麦麦引用 / 提到麦麦）的人
    talked = set()
    for m in messages:
        if _gap(last_time, _seconds(m["time"])) > 600:
            continue
        if m["self"] and m["quote"] in by_id:
            talked.add(by_id[m["quote"]].get("user", ""))
        elif not m["self"] and (
            (m["quote"] in by_id and by_id[m["quote"]]["self"]) or any(n in m["text"] for n in bot_names)
        ):
            talked.add(m.get("user", ""))
    window = [m for m in messages if _gap(last_time, _seconds(m["time"])) <= 300]
    texts = [m["text"] for m in others]
    joined = "\n".join(texts)

    tool_calls = [item["tool_call"]["func_name"] for item in items if item.get("item_type") == "FunctionCallItem"]
    prev_tool = tool_calls[-1] if tool_calls else ""
    hour = datetime.fromtimestamp(ts).hour + datetime.fromtimestamp(ts).minute / 60

    return {
        "n_speakers_pending": float(len({m.get("user") for m in others})),
        "n_speakers_recent": float(len({m.get("user") for m in window if not m["self"]})),
        "msgs_recent": float(len(window)),
        "at_other": float(any("@" in t and not any(f"@{n}" in t for n in bot_names) for t in texts)),
        "sender_talked_with_bot": float(any(m.get("user") in talked for m in others)),
        "second_person": float(
            "你" in joined
            and last_self is not None
            and _gap(last_time, _seconds(last_self["time"])) <= 180
        ),
        "has_qmark": float(any(("?" in t or "？" in t) for t in texts)),
        "placeholder_only": float(bool(texts) and all(t.startswith(PLACEHOLDER_PREFIXES) for t in texts)),
        "last_msg_len_log": float(math.log1p(len(texts[-1]))) if texts else 0.0,
        "bot_last_msg_len_log": float(math.log1p(len(last_self["text"]))) if last_self else 0.0,
        "prev_planner_reply": float(prev_tool == "reply"),
        "prev_planner_wait": float(prev_tool == "wait"),
        "prev_planner_none": float(not tool_calls),
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
    }


def load(since: float, bot_names: Sequence[str]) -> List[Dict[str, Any]]:
    samples = []
    for path in sorted(RAW_DIR.glob("*.json")):
        ts = int(path.stem.rsplit("_", 1)[-1]) / 1000
        if ts < since:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != 6 or not _is_first_round(raw["request_items"]):
            continue
        session = path.stem.rsplit("_", 1)[0]
        base = extract(raw, session, bot_names)
        tools = [i["tool_call"]["func_name"] for i in raw["output_items"] if i.get("item_type") == "FunctionCallItem"]
        messages = [m for item in raw["request_items"] if (m := _parse_message(item))]
        pending_others = [m["text"] for m in _pending_messages(raw["request_items"]) if not m["self"]]
        last_self = next((m["text"] for m in reversed(messages) if m["self"]), "")
        samples.append(
            {
                "id": path.stem,
                "ts": ts,
                "session": session,
                "features": {**base["features"], **rich_features(raw, session, ts, bot_names)},
                "old_score": base["old_score"],
                "act": int(any(name != "wait" for name in tools)),
                "tokens": int(raw["metadata"].get("total_tokens") or 0),
                "embed_text": "新消息：" + " / ".join(pending_others)[-400:] + "\n麦麦上一句：" + last_self[:150],
            }
        )
    return sorted(samples, key=lambda s: s["ts"])


async def embed_all(samples: List[Dict[str, Any]], concurrency: int = 6) -> None:
    cache: Dict[str, List[float]] = json.loads(EMBED_CACHE.read_text(encoding="utf-8")) if EMBED_CACHE.exists() else {}
    orchestrator = LLMOrchestrator(task_name="embedding", request_type="eval.reply_necessity_embed")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(sample: Dict[str, Any]) -> None:
        if sample["id"] in cache:
            return
        async with semaphore:
            result = await orchestrator.get_embedding(sample["embed_text"])
            cache[sample["id"]] = result.embedding

    await asyncio.gather(*(one(s) for s in samples))
    EMBED_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    for sample in samples:
        sample["embedding"] = cache[sample["id"]]


# ─────────────────────────── 模型 ───────────────────────────

BASE_FEATURES = [
    "at_bot", "mention_bot", "quote_bot", "question", "request", "short_reaction", "log_text_len",
    "n_pending", "last_is_self", "log_secs_since_bot", "recent_self_ratio",
]


def matrix(samples: Sequence[Dict[str, Any]], names: Sequence[str]) -> np.ndarray:
    return np.array([[s["features"][n] for n in names] for s in samples], dtype=float)


def session_rate(train: Sequence[Dict[str, Any]], test: Sequence[Dict[str, Any]], prior: float = 10.0) -> Tuple[np.ndarray, np.ndarray]:
    """各群行动率（只用训练集计算，带先验平滑），作为群级基础倾向特征。"""

    overall = np.mean([s["act"] for s in train])
    stats: Dict[str, List[int]] = {}
    for s in train:
        stats.setdefault(s["session"], []).append(s["act"])

    def rate(session: str) -> float:
        acts = stats.get(session, [])
        return (sum(acts) + prior * overall) / (len(acts) + prior)

    return np.array([[rate(s["session"])] for s in train]), np.array([[rate(s["session"])] for s in test])


ModelFn = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], np.ndarray]


def logistic(names: Sequence[str], c: float = 1.0, use_session: bool = False, embed_dims: int = 0) -> ModelFn:
    def run(train: List[Dict[str, Any]], test: List[Dict[str, Any]]) -> np.ndarray:
        xtr, xte = matrix(train, names), matrix(test, names)
        if use_session:
            a, b = session_rate(train, test)
            xtr, xte = np.hstack([xtr, a]), np.hstack([xte, b])
        if embed_dims:
            etr = np.array([s["embedding"] for s in train])
            ete = np.array([s["embedding"] for s in test])
            pca = PCA(n_components=min(embed_dims, len(train) - 1), random_state=0).fit(etr)
            xtr, xte = np.hstack([xtr, pca.transform(etr)]), np.hstack([xte, pca.transform(ete)])
        scaler = StandardScaler().fit(xtr)
        model = LogisticRegression(C=c, max_iter=2000).fit(scaler.transform(xtr), [s["act"] for s in train])
        return model.predict_proba(scaler.transform(xte))[:, 1]

    return run


def boosting(names: Sequence[str], use_session: bool = False, embed_dims: int = 0, depth: int = 3) -> ModelFn:
    def run(train: List[Dict[str, Any]], test: List[Dict[str, Any]]) -> np.ndarray:
        xtr, xte = matrix(train, names), matrix(test, names)
        if use_session:
            a, b = session_rate(train, test)
            xtr, xte = np.hstack([xtr, a]), np.hstack([xte, b])
        if embed_dims:
            etr = np.array([s["embedding"] for s in train])
            ete = np.array([s["embedding"] for s in test])
            pca = PCA(n_components=min(embed_dims, len(train) - 1), random_state=0).fit(etr)
            xtr, xte = np.hstack([xtr, pca.transform(etr)]), np.hstack([xte, pca.transform(ete)])
        model = HistGradientBoostingClassifier(
            max_depth=depth, learning_rate=0.05, max_iter=200, l2_regularization=1.0, min_samples_leaf=15, random_state=0
        ).fit(xtr, [s["act"] for s in train])
        return model.predict_proba(xte)[:, 1]

    return run


def old_rule(train: List[Dict[str, Any]], test: List[Dict[str, Any]]) -> np.ndarray:
    del train
    return np.array([s["old_score"] for s in test], dtype=float)


def rolling(samples: List[Dict[str, Any]], model: ModelFn, start: float = 0.4, folds: int = 5) -> Tuple[List[float], np.ndarray, np.ndarray]:
    n = len(samples)
    edges = [int(n * (start + (1 - start) * k / folds)) for k in range(folds + 1)]
    aucs, preds, labels = [], [], []
    for k in range(folds):
        train, test = samples[: edges[k]], samples[edges[k] : edges[k + 1]]
        p = model(train, test)
        y = np.array([s["act"] for s in test])
        if 0 < y.sum() < len(y):
            aucs.append(roc_auc_score(y, p))
        preds.append(p)
        labels.append(y)
    return aucs, np.concatenate(preds), np.concatenate(labels)


def recall_at_fp(p_act: np.ndarray, act: np.ndarray, max_fp: float) -> str:
    """误跳过的行动 ≤ max_fp 时，最多能跳过多少比例的 no_reply（以及精准率）。"""

    no_reply = act == 0
    best = (0.0, float("nan"))
    for threshold in np.unique(p_act):
        skip = p_act < threshold
        fp = (skip & ~no_reply).sum() / max(1, (~no_reply).sum())
        if fp <= max_fp:
            recall = (skip & no_reply).sum() / max(1, no_reply.sum())
            precision = (skip & no_reply).sum() / max(1, skip.sum())
            if recall > best[0]:
                best = (recall, precision)
    return f"{best[0] * 100:.0f}%（精准 {best[1] * 100:.0f}%）" if best[0] else "0%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True)
    parser.add_argument("--no-embed", action="store_true")
    args = parser.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d %H:%M:%S").timestamp()
    bot_names = [n for n in [global_config.bot.nickname.strip(), *global_config.bot.alias_names] if n]

    backup_raw(since)
    samples = load(since, bot_names)
    if not args.no_embed:
        asyncio.run(embed_all(samples))
    all_names = list(samples[0]["features"].keys())
    rich_names = [n for n in all_names if np.mean([s["features"][n] != 0 for s in samples]) >= 0.02]

    candidates: List[Tuple[str, ModelFn]] = [
        ("旧必要性评分", old_rule),
        ("逻辑回归 · 原 11 特征", logistic(BASE_FEATURES)),
        ("逻辑回归 · 全部特征 C=1", logistic(rich_names)),
        ("逻辑回归 · 全部特征 C=0.1", logistic(rich_names, c=0.1)),
        ("逻辑回归 · 全部特征 + 群倾向", logistic(rich_names, use_session=True)),
        ("梯度提升树 · 全部特征", boosting(rich_names)),
        ("梯度提升树 · 全部特征 + 群倾向", boosting(rich_names, use_session=True)),
        ("梯度提升树 · 深度 2 + 群倾向", boosting(rich_names, use_session=True, depth=2)),
    ]
    if not args.no_embed:
        candidates += [
            ("逻辑回归 · 全部特征 + 群倾向 + 语义 16 维", logistic(rich_names, c=0.3, use_session=True, embed_dims=16)),
            ("逻辑回归 · 全部特征 + 群倾向 + 语义 48 维", logistic(rich_names, c=0.1, use_session=True, embed_dims=48)),
            ("梯度提升树 · 全部特征 + 群倾向 + 语义 16 维", boosting(rich_names, use_session=True, embed_dims=16)),
            ("逻辑回归 · 仅语义 32 维", logistic([], c=0.3, embed_dims=32)),
        ]

    acts = np.array([s["act"] for s in samples])
    lines = [
        "# 预测 no_reply：多方案对比（滚动时间验证）",
        "",
        f"样本 {len(samples)} 条（行动 {acts.sum()}，no_reply {len(acts) - acts.sum()}）；"
        f"特征 {len(rich_names)} 个；从 40% 处开始滚动 5 段。",
        "",
        "| 方案 | 平均 AUC | 各段 AUC | 误跳过行动 ≤10% 时跳过的 no_reply | ≤25% 时 |",
        "|---|---|---|---|---|",
    ]
    for label, model in candidates:
        aucs, p, y = rolling(samples, model)
        lines.append(
            f"| {label} | **{np.mean(aucs):.3f}** | {' / '.join(f'{a:.2f}' for a in aucs)} | "
            f"{recall_at_fp(p, y, 0.10)} | {recall_at_fp(p, y, 0.25)} |"
        )
        print(lines[-1], flush=True)

    # 全量逻辑回归（全部特征）的标准化权重，便于解释
    x = matrix(samples, rich_names)
    scaler = StandardScaler().fit(x)
    model = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(x), acts)
    lines += ["", "## 全部特征逻辑回归的权重（全量，标准化后）", "", "| 特征 | 权重 |", "|---|---|"]
    for name, weight in sorted(zip(rich_names, model.coef_[0], strict=True), key=lambda t: -abs(t[1])):
        lines.append(f"| {name} | {weight:+.2f} |")

    run_dir = OUTPUT_ROOT / f"explore_reply_necessity_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    with (run_dir / "samples.jsonl").open("w", encoding="utf-8") as file:
        for s in samples:
            file.write(json.dumps({k: v for k, v in s.items() if k != "embedding"}, ensure_ascii=False) + "\n")
    print(f"-> {run_dir}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
