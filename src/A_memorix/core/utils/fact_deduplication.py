"""写入前的事实去重：归属由调用方确认，模型只判断等价关系。"""

from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Sequence
from weakref import WeakValueDictionary

import asyncio
import json
import re
import threading
import time
import unicodedata

from src.common.prompt_i18n import load_prompt
from src.services import llm_service

from .hash import compute_hash

_LOCKS: WeakValueDictionary = WeakValueDictionary()
_LOCK_GUARD = threading.Lock()


@asynccontextmanager
async def fact_scope_lock(store: Any, scope_type: str, scope_id: str):
    """同一个数据库的范围锁跨内核、跨事件循环共享，等待时不占住事件循环。"""
    key = (str(store.get_db_path().resolve()), scope_type, scope_id)
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.02)
    try:
        yield
    finally:
        lock.release()


def normalized_fact(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def visible_fact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """写入决定和疑似重复关系仅供内部恢复，不进入检索或画像的模型输入。"""
    return {
        key: value for key, value in metadata.items() if not key.startswith("dedup_") and key != "fact_dedup_decisions"
    }


def observation_id(*, platform: str, chat_id: str, message_ids: Sequence[str], window_id: str = "") -> str:
    """使用真实来源集合，而不是模型措辞，标识一次观察。"""
    if not chat_id or not (message_ids or window_id):
        raise ValueError("去重写入缺少真实会话或来源标识")
    return compute_hash(json.dumps([platform, chat_id, sorted(set(message_ids)), window_id], ensure_ascii=False))


class FactDeduplicator:
    def __init__(self, store: Any, embedding: Any = None, vectors: Any = None, task_name: str = "utils"):
        self.store = store
        self.embedding = embedding
        self.vectors = vectors
        self.task_name = task_name

    def _claim_people(self, evidence: Sequence[Dict[str, Any]], paragraph_hash: str) -> List[str]:
        for row in evidence:
            if row["evidence_type"] == "paragraph" and row["evidence_id"] == paragraph_hash:
                metadata = row["metadata"]
                if "person_ids" in metadata:
                    return list(metadata["person_ids"])
        paragraph = self.store.get_paragraph(paragraph_hash)
        people = (paragraph.get("metadata") or {}).get("person_ids", []) if paragraph else []
        # 旧混合段落涉及多个人时，人员列表不能证明某条事实的主体。
        return list(people) if len(people) == 1 else []

    @staticmethod
    def _compatible_candidates(
        scope_type: str, fact: Dict[str, Any], rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if scope_type != "chat" or not fact.get("person_ids"):
            return rows
        people = set(fact["person_ids"])
        return [row for row in rows if set(row.get("person_ids", [])) == people]

    def _scope_paragraphs(self, scope_type: str, scope_id: str) -> List[str]:
        source = f"person_fact:{scope_id}" if scope_type == "person" else f"chat_summary:{scope_id}"
        return [
            row["hash"]
            for row in self.store.query(
                "SELECT p.hash FROM paragraphs p JOIN (SELECT hash FROM paragraphs WHERE source=? UNION "
                "SELECT e.evidence_id AS hash FROM fact_claims c JOIN fact_evidence e ON e.claim_id=c.claim_id "
                "WHERE c.scope_type=? AND c.scope_id=? AND e.evidence_type='paragraph' UNION "
                "SELECT r.paragraph_hash AS hash FROM external_memory_refs r WHERE ?='chat' "
                "AND r.source_type='chat_summary' AND json_extract(r.metadata_json,'$.chat_id')=? "
                "AND COALESCE(json_extract(r.metadata_json,'$.dedup_phase'),'complete')='complete') owned ON owned.hash=p.hash "
                "WHERE COALESCE(p.is_deleted,0)=0 "
                "AND COALESCE(json_extract(p.metadata, '$.dedup_phase'),'complete')='complete' "
                "AND (json_extract(p.metadata, '$.memory_change.valid_to') IS NULL "
                "OR json_extract(p.metadata, '$.memory_change.valid_to')>?)",
                (source, scope_type, scope_id, scope_type, scope_id, time.time()),
            )
        ]

    def _candidates(
        self, scope_type: str, scope_id: str, text: str, paragraph_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        """先按账本精确命中，再做有上限的关键词召回；不向模型发送整段历史。"""
        now = time.time()
        rows = self.store.query(
            "SELECT * FROM fact_claims WHERE scope_type=? AND scope_id=? AND status='active' AND stability<>'uncertain' "
            "AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?) "
            "AND value_normalized=? LIMIT 8",
            (scope_type, scope_id, now, now, normalized_fact(text)),
        )
        if len(rows) < 8:
            words = re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]{2}", text)
            terms = list(dict.fromkeys(words))[:8]
            if terms:
                conditions = " OR ".join("value_text LIKE ?" for _ in terms)
                rows += self.store.query(
                    "SELECT * FROM fact_claims WHERE scope_type=? AND scope_id=? AND status='active' AND stability<>'uncertain' "
                    f"AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>?) "
                    f"AND ({conditions}) ORDER BY updated_at DESC LIMIT 8",
                    (scope_type, scope_id, now, now, *[f"%{term}%" for term in terms]),
                )
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            evidence = self.store.get_fact_evidence(row["claim_id"])
            paragraph = next(
                (
                    item["evidence_id"]
                    for item in evidence
                    if item["evidence_type"] == "paragraph" and item["evidence_id"] in paragraph_ids
                ),
                "",
            )
            if paragraph:
                result[row["claim_id"]] = {
                    "id": row["claim_id"],
                    "text": row["value_text"],
                    "paragraph_hash": paragraph,
                    "claim_id": row["claim_id"],
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "person_ids": self._claim_people(evidence, paragraph),
                    "observations": [item["metadata"] for item in evidence if item["evidence_type"] == "source"][:2],
                }
        return list(result.values())[:8]

    def _paragraph_candidates(self, ids: Sequence[str], scope_type: str, scope_id: str) -> List[Dict[str, Any]]:
        result = []
        for paragraph_id in ids:
            paragraph = self.store.get_paragraph(paragraph_id)
            if not paragraph or paragraph.get("is_deleted"):
                continue
            claims = self.store.query(
                "SELECT c.* FROM fact_claims c JOIN fact_evidence e ON e.claim_id=c.claim_id "
                "WHERE e.evidence_type='paragraph' AND e.evidence_id=? AND c.scope_type=? AND c.scope_id=?",
                (paragraph_id, scope_type, scope_id),
            )
            # 已登记的段落只能召回仍有效的具体事实，不能把失效内容当作历史正文重新导入。
            if claims:
                for claim in claims:
                    if (
                        claim["status"] == "active"
                        and claim["stability"] != "uncertain"
                        and (claim["valid_to"] is None or claim["valid_to"] > time.time())
                        and (claim["valid_from"] is None or claim["valid_from"] <= time.time())
                    ):
                        result.append(
                            {
                                "id": claim["claim_id"],
                                "text": claim["value_text"],
                                "claim_id": claim["claim_id"],
                                "paragraph_hash": paragraph_id,
                                "valid_from": claim["valid_from"],
                                "valid_to": claim["valid_to"],
                                "person_ids": self._claim_people(
                                    self.store.get_fact_evidence(claim["claim_id"]), paragraph_id
                                ),
                                "observations": [
                                    item["metadata"]
                                    for item in self.store.get_fact_evidence(claim["claim_id"])
                                    if item["evidence_type"] == "source"
                                ][:2],
                            }
                        )
            else:
                result.append(
                    {
                        "id": f"paragraph:{paragraph_id}",
                        "text": paragraph["content"],
                        "claim_id": "",
                        "paragraph_hash": paragraph_id,
                        "person_ids": self._claim_people([], paragraph_id),
                        "event_time_start": paragraph.get("event_time_start"),
                        "event_time_end": paragraph.get("event_time_end"),
                        "observed_at": paragraph.get("created_at"),
                    }
                )
        return result

    async def plan(
        self,
        *,
        scope_type: str,
        scope_id: str,
        facts: List[Dict[str, Any]],
        source: Dict[str, Any],
        relations: Sequence[Dict[str, str]] = (),
    ) -> Dict[str, Any]:
        ids = [item["fact_id"] for item in facts]
        if len(set(ids)) != len(ids):
            raise ValueError("去重事实 ID 重复")
        paragraph_ids = await asyncio.to_thread(self._scope_paragraphs, scope_type, scope_id)
        decisions: List[Dict[str, Any]] = []
        support: Dict[str, List[str]] = {}
        for offset in range(0, len(facts), 8):
            batch = facts[offset : offset + 8]
            candidates: Dict[str, List[Dict[str, Any]]] = {}
            fixed: Dict[str, Dict[str, Any]] = {}
            for fact in batch:
                found = await asyncio.to_thread(self._candidates, scope_type, scope_id, fact["text"], paragraph_ids)
                found = self._compatible_candidates(scope_type, fact, found)
                exact = next(
                    (row for row in found if normalized_fact(row["text"]) == normalized_fact(fact["text"])), None
                )
                # 相同文字也可能是不同日期的事件；来源时间不同交由语义判断。
                temporal = bool(
                    re.search(
                        r"今天|昨天|明天|刚刚|再次|每天|参加|跑了|买了|完成|出差|去了|\d{1,4}[-/年月日:]|today|yesterday|again|attended|ran|bought|今日|昨日|明日|参加|購入",
                        fact["text"],
                        re.I,
                    )
                )
                durable = bool(
                    re.search(
                        r"喜欢|偏好|爱好|职业|常住|长期|惯用|偏爱|居住|prefers?|likes?|lives?|occupation|好き|好む|職業|住んで",
                        fact["text"],
                        re.I,
                    )
                )
                if exact and durable and not temporal:
                    fixed[fact["fact_id"]] = {"fact_id": fact["fact_id"], "action": "reuse", "target": exact}
                elif paragraph_ids and self.embedding is not None and self.vectors is not None:
                    vector = await self.embedding.encode(fact["text"])
                    hits, _ = await asyncio.to_thread(self.vectors.search, vector, k=8, allowed_ids=paragraph_ids)
                    found += await asyncio.to_thread(self._paragraph_candidates, hits, scope_type, scope_id)
                # 同一批和之前批次内的新事实也作为候选，不依赖尚未写入的向量。
                earlier = facts[:offset] + batch[: batch.index(fact)]
                inputs = [
                    {
                        "id": f"input:{row['fact_id']}",
                        "text": row["text"],
                        "input_fact_id": row["fact_id"],
                        "person_ids": row.get("person_ids", []),
                    }
                    for row in earlier[-8:]
                ]
                inputs.sort(
                    key=lambda row: SequenceMatcher(
                        None, normalized_fact(fact["text"]), normalized_fact(row["text"])
                    ).ratio(),
                    reverse=True,
                )
                # 历史候选与当前输入交错，避免历史候选占满上限后漏掉同批重复。
                found = [row for pair in zip_longest(found, inputs) for row in pair if row is not None]
                found = self._compatible_candidates(scope_type, fact, found)
                unique = {row["id"]: row for row in found}
                candidates[fact["fact_id"]] = list(unique.values())[:8]
                if not candidates[fact["fact_id"]]:
                    fixed[fact["fact_id"]] = {"fact_id": fact["fact_id"], "action": "keep"}
            needs_model = any(item["fact_id"] not in fixed for item in batch) or bool(relations)
            if not needs_model:
                decisions.extend(fixed[item["fact_id"]] for item in batch)
                continue
            # 模型只需要文本、时间和本批次短引用；完整来源和存储 ID 留在程序内。
            # 多条事实共享候选时保持短引用一致，避免重复发送证据消息集合和哈希。
            aliases: Dict[str, str] = {}
            for rows in candidates.values():
                for row in rows:
                    aliases.setdefault(
                        row["id"], row["id"] if row["id"].startswith("input:") else f"c{len(aliases) + 1}"
                    )
            temporal_keys = {"observed_at", "event_time_start", "event_time_end", "valid_from", "valid_to"}
            model_candidates = {
                fact_id: [
                    {
                        "id": aliases[row["id"]],
                        "text": row["text"],
                        **{key: row[key] for key in temporal_keys if row.get(key) is not None},
                        "observations": [
                            {
                                key: value
                                for key, value in observation.items()
                                if key in temporal_keys and value is not None
                            }
                            for observation in row.get("observations", [])
                        ],
                    }
                    for row in rows
                ]
                for fact_id, rows in candidates.items()
            }
            response = await llm_service.generate(
                llm_service.LLMServiceRequest(
                    task_name=self.task_name,
                    request_type="A_Memorix.FactDeduplication",
                    prompt=load_prompt(
                        "a_memorix_fact_deduplication",
                        prompts_root=Path(__file__).with_name("prompts"),
                        payload=json.dumps(
                            {
                                "facts": [{"fact_id": row["fact_id"], "text": row["text"]} for row in batch],
                                "candidates": model_candidates,
                                "source": {
                                    key: value
                                    for key, value in source.items()
                                    if key in temporal_keys and value is not None
                                },
                                "fixed": [
                                    {
                                        "fact_id": row["fact_id"],
                                        "action": row["action"],
                                        **({"target_id": aliases[row["target"]["id"]]} if "target" in row else {}),
                                    }
                                    for row in fixed.values()
                                ],
                                "relations": list(relations),
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    temperature=0.0,
                )
            )
            if not response.success:
                raise RuntimeError(f"事实去重模型调用失败: {response.error}")
            raw = str(response.completion.response).strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            data = json.loads(raw)
            output = data.get("decisions")
            if not isinstance(output, list) or len(output) != len(batch):
                raise ValueError("去重响应必须逐条覆盖当前批次")
            by_id = {row["fact_id"]: row for row in output}
            if set(by_id) != {item["fact_id"] for item in batch}:
                raise ValueError("去重响应包含无效或重复的事实 ID")
            for fact in batch:
                fact_id = fact["fact_id"]
                row = by_id[fact_id]
                action = row.get("action")
                if action not in {"keep", "reuse", "possible_duplicate"}:
                    raise ValueError("无效去重决定")
                allowed = {aliases[item["id"]]: item for item in candidates[fact_id]}
                if fact_id in fixed:
                    decisions.append(fixed[fact_id])
                elif action == "reuse":
                    if row.get("target_id") not in allowed:
                        raise ValueError("去重目标不属于当前候选")
                    decisions.append({"fact_id": fact_id, "action": action, "target": allowed[row["target_id"]]})
                else:
                    possible = row.get("candidate_ids", [])
                    if (
                        not isinstance(possible, list)
                        or any(token not in allowed for token in possible)
                        or (action == "possible_duplicate" and not possible)
                    ):
                        raise ValueError("疑似重复候选无效")
                    decisions.append(
                        {
                            "fact_id": fact_id,
                            "action": action,
                            "candidate_ids": [allowed[token]["id"] for token in possible],
                        }
                    )
            relation_support = data.get("relation_support", {})
            if not isinstance(relation_support, dict):
                raise ValueError("关系支持信息必须是对象")
            for key, values in relation_support.items():
                if (
                    key not in {str(index) for index in range(len(relations))}
                    or not isinstance(values, list)
                    or any(value not in by_id for value in values)
                ):
                    raise ValueError("关系支持信息引用了输入以外的内容")
                support.setdefault(key, []).extend(values)
        return {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "facts": facts,
            "source": source,
            "decisions": decisions,
            "relation_support": support,
        }

    def commit(
        self, plan: Dict[str, Any], paragraph_hash: str = "", classification: Dict[str, Any] | None = None
    ) -> List[str]:
        """决定可重复提交；每个来源只增加一次证据，不把疑似重复变成不确定事实。"""
        classification = classification or {}
        facts = {row["fact_id"]: row for row in plan["facts"]}
        resolved: Dict[str, Dict[str, str]] = {}
        for decision in plan["decisions"]:
            fact = facts[decision["fact_id"]]
            target = decision.get("target", {})
            if "input_fact_id" in target:
                target = resolved[target["input_fact_id"]]
            target_paragraph = target.get("paragraph_hash") or paragraph_hash
            claim_id = target.get("claim_id", "")
            if not target_paragraph:
                raise ValueError("去重事实缺少可检索的段落")
            paragraph = self.store.get_paragraph(target_paragraph)
            if paragraph is None or paragraph.get("is_deleted"):
                raise ValueError("去重目标段落在提交前已删除")
            if claim_id:
                claim = self.store.get_fact_claim(claim_id)
                if (
                    claim is None
                    or claim["status"] != "active"
                    or claim["stability"] == "uncertain"
                    or claim["scope_type"] != plan["scope_type"]
                    or claim["scope_id"] != plan["scope_id"]
                    or (claim["valid_from"] is not None and claim["valid_from"] > time.time())
                    or (claim["valid_to"] is not None and claim["valid_to"] <= time.time())
                ):
                    raise ValueError("去重目标在提交前已失效")
            else:
                # 存量混合段落只登记这次已确认匹配的具体事实，不能把整段视为人物事实。
                origin = target_paragraph if decision["action"] == "reuse" else plan["source"]["observation_id"]
                claim = self.store.upsert_fact_claim(
                    scope_type=plan["scope_type"],
                    scope_id=plan["scope_id"],
                    fact_key=f"statement:{origin}:{compute_hash(normalized_fact(fact['text']))}",
                    value_text=fact["text"],
                    evidence_type="paragraph",
                    evidence_id=target_paragraph,
                    authority="summary_derived"
                    if decision["action"] == "reuse" or plan["scope_type"] == "chat"
                    else classification.get("authority", "direct_user"),
                    stability=classification.get("stability", "stable"),
                    profile_section=classification.get("profile_section", "stable_facts"),
                    evidence_metadata={
                        "fact_id": fact["fact_id"],
                        "source_managed": decision["action"] != "reuse",
                        "person_ids": fact.get("person_ids", []),
                    },
                    reason="dedup_fact_registered",
                    observed_at=target.get("observed_at", plan["source"].get("observed_at")),
                )
                claim_id = claim["claim_id"]
            evidence = self.store.add_fact_evidence(
                claim_id,
                evidence_type="source",
                evidence_id=plan["source"]["observation_id"],
                metadata={
                    **plan["source"],
                    "paragraph_hash": target_paragraph,
                    "dedup_candidate_ids": decision.get("candidate_ids", []),
                },
                reason="dedup_source_observed",
                observed_at=plan["source"].get("observed_at"),
            )
            if evidence["created"]:
                with self.store.transaction(immediate=True) as database:
                    database.execute(
                        "UPDATE fact_claims SET last_confirmed_at=MAX(last_confirmed_at,?), "
                        "updated_at=MAX(updated_at,?) WHERE claim_id=?",
                        (evidence["observed_at"], evidence["observed_at"], claim_id),
                    )
            resolved[fact["fact_id"]] = {"claim_id": claim_id, "paragraph_hash": target_paragraph}
        return [row["claim_id"] for row in resolved.values()]
