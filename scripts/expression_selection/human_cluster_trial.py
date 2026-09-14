"""生成真实聊天的聚类数量人工盲评包，不连接数据库或调用外部模型。"""

from pathlib import Path
from typing import Any, Dict, List

import argparse
import ast
import hashlib
import json
import random
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[2]


def load_algorithm() -> Any:
    """直接提取生产纯计算方法，避免导入应用触发数据库初始化。"""
    path = ROOT / "src/chat/replyer/expression_vector_index.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_run_kmeans", "_repair_empty_cluster_labels", "_build_cluster_centers_from_labels", "_select_by_mmr"}
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ExpressionVectorIndex")
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    norm = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "l2_normalize")
    namespace = {"np": np, "Any": Any, "List": List, "Dict": Dict, "VECTOR_DIVERSITY_LAMBDA": 0.85}
    exec(compile(ast.Module(body=[norm, cls], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["ExpressionVectorIndex"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    index_path = args.data_root / "expression_selection/expression_vector_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if len(index["embedding_profiles"]) != 1:
        raise ValueError("本实验要求单一 embedding profile，不混合不同向量空间")
    profile = index["embedding_profiles"][0]
    with np.load(index_path.with_name(index["vectors_file"]), allow_pickle=False) as archive:
        vectors = archive[profile["vectors_key"]].astype(np.float32)
    expressions = sorted(index["expressions"], key=lambda e: e["vector_index"])
    assert [e["vector_index"] for e in expressions] == list(range(len(vectors)))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    eval_root = args.data_root / "analysis/expression_selection_eval"
    batch_path = eval_root / "runs/selector_batches/expression_selection_batch_compare_size4000_clean_intentprompt_fixedbaseline_20260621.json"
    samples = json.loads(batch_path.read_text(encoding="utf-8"))["samples"]
    with np.load(eval_root / "indices/cache/expression_selection_query_embeddings.npz", allow_pickle=False) as archive:
        if archive["model_name"].item() != profile["embedding_model"]:
            raise ValueError("历史查询模型与表达模型不一致")
        query_vectors = dict(zip(archive["hashes"].tolist(), archive["embeddings"], strict=True))
    # 优先覆盖不同聊天流，再用固定随机顺序补足六个上下文。
    random.Random(20260906).shuffle(samples)
    selected = []
    sessions = set()
    for sample in samples:
        if sample["target_session_id"] not in sessions and sample["history_lines"]:
            selected.append(sample)
            sessions.add(sample["target_session_id"])
        if len(selected) == 6:
            break
    for sample in samples:
        if len(selected) >= 6:
            break
        if sample not in selected and sample["history_lines"]:
            selected.append(sample)
    assert len(selected) == 6
    queries = []
    for sample in selected:
        key = hashlib.sha256(sample["query_text"].encode()).hexdigest()
        q = query_vectors[key].astype(np.float32)
        queries.append(q / np.linalg.norm(q))
    algorithm = load_algorithm()
    trials = []
    answer_key = {}
    runs = []
    subsets = []
    for seed in [20260906, 20260907]:
        permutation = np.random.default_rng(seed).permutation(len(vectors))
        for size in [500, 2000, 8000, len(vectors)]:
            # 全库只做一轮，原始顺序固定；小库使用嵌套无放回抽样。
            if size == len(vectors) and seed == 20260907:
                continue
            ids = np.arange(size) if size == len(vectors) else permutation[:size]
            x = np.ascontiguousarray(vectors[ids])
            subset_id = f"n{size}-seed{seed}"
            subsets.append({"id": subset_id, "expression_ids": [expressions[i]["id"] for i in ids]})
            results = [[] for _ in selected]
            for k in [20, 80, 320]:
                started = time.perf_counter()
                labels = algorithm._run_kmeans(x, cluster_count=k, seed=20260621)
                centers = algorithm._build_cluster_centers_from_labels(x, labels, k)
                sizes = np.bincount(labels, minlength=k)
                runs.append({"subset": subset_id, "clusters": k, "seconds": time.perf_counter() - started})
                print(f"{subset_id} K={k} 聚类完成 {runs[-1]['seconds']:.2f}s", flush=True)
                for qi, q in enumerate(queries):
                    cs = centers @ q
                    order = np.argsort(cs)[::-1]
                    chosen = []
                    count = 0
                    for cid in order:
                        chosen.append(int(cid))
                        count += int(sizes[cid])
                        if len(chosen) >= 16 and count >= 50:
                            break
                    pool = np.flatnonzero(np.isin(labels, chosen))
                    scored = [{"vector_index": int(i), "score": .875 * float(x[i] @ q) + .125 * float(cs[labels[i]])} for i in pool]
                    matches = algorithm._select_by_mmr(scored, x, limit=50)
                    matches.sort(key=lambda item: item["score"], reverse=True)
                    output = [{"id": expressions[ids[m["vector_index"]]]["id"], "situation": expressions[ids[m["vector_index"]]]["situation"], "style": expressions[ids[m["vector_index"]]]["style"]} for m in matches]
                    representatives = []
                    for cid in chosen[:3]:
                        members = np.flatnonzero(labels == cid)
                        sims = x[members] @ centers[cid]
                        picks = members[np.argsort(sims)[::-1][:5]]
                        representatives.append([{"situation": expressions[ids[i]]["situation"], "style": expressions[ids[i]]["style"]} for i in picks])
                    results[qi].append({"k": k, "pool_size": len(pool), "items": output, "representatives": representatives})
            for qi, sample in enumerate(selected):
                trial_id = f"T{len(trials)+1:02d}"
                methods = results[qi]
                random.Random(f"{trial_id}-20260906").shuffle(methods)
                methods.sort(key=lambda method: method["k"] != 80)
                answer_key[trial_id] = {"subset": subset_id, "sample_id": sample["sample_id"], "variants": {chr(65+i): {"clusters": m["k"], "pool_size": m["pool_size"]} for i, m in enumerate(methods)}}
                trials.append({"id": trial_id, "context_id": qi+1, "size": size, "history": sample["history_lines"], "guide": sample.get("reply_guide", ""), "query": sample["query_text"], "reply": sample.get("actual_reply", ""), "variants": [{"label": chr(65+i), "items": m["items"], "representatives": m["representatives"]} for i, m in enumerate(methods)]})
    # 首轮先覆盖各库规模的同一聊天；其余按聊天编号展开，方便分批评。
    trials.sort(key=lambda t: (t["context_id"], t["size"], t["id"]))
    metadata = {"expression_count": len(vectors), "query_count": len(selected), "trial_count": len(trials), "source_batch": str(batch_path), "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(), "profile": profile, "source_code_sha256": hashlib.sha256((ROOT / "src/chat/replyer/expression_vector_index.py").read_bytes()).hexdigest(), "scope": "模拟全局共享表达库；不重放历史聊天权限。复用历史 intent+planner 查询向量，模型名和维度匹配但历史缓存没有 profile 探针证明。"}
    metadata["review_version"] = 2
    (args.output / "answer_key.json").write_text(json.dumps({"metadata": metadata, "trials": answer_key, "runs": runs, "subsets": subsets}, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {"metadata": metadata, "trials": trials}
    (args.output / "blind_data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    template = (Path(__file__).with_name("human_cluster_trial.html")).read_text(encoding="utf-8")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    (args.output / "review.html").write_text(template.replace("__TRIAL_DATA__", encoded), encoding="utf-8")
    print(f"完成：{len(trials)}题，{len(runs)}组聚类，{args.output / 'review.html'}", flush=True)


if __name__ == "__main__":
    main()
