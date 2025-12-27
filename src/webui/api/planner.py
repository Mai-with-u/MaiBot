"""
规划器监控API
提供规划器日志数据的查询接口

性能优化：
1. 聊天摘要只统计文件数量和最新时间戳，不读取文件内容
2. 日志列表使用文件名解析时间戳，只在需要时读取完整内容
3. 详情按需加载
"""
import json
from pathlib import Path
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/planner", tags=["planner"])

# 规划器日志目录
PLAN_LOG_DIR = Path("logs/plan")


class ChatSummary(BaseModel):
    """聊天摘要 - 轻量级，不读取文件内容"""
    chat_id: str
    plan_count: int
    latest_timestamp: float
    latest_filename: str


class PlanLogSummary(BaseModel):
    """规划日志摘要"""
    chat_id: str
    timestamp: float
    filename: str
    action_count: int
    action_types: List[str]  # 动作类型列表
    total_plan_ms: float
    llm_duration_ms: float
    reasoning_preview: str


class PlanLogDetail(BaseModel):
    """规划日志详情"""
    type: str
    chat_id: str
    timestamp: float
    prompt: str
    reasoning: str
    raw_output: str
    actions: List[Dict]
    timing: Dict
    extra: Optional[Dict] = None


class PlannerOverview(BaseModel):
    """规划器总览 - 轻量级统计"""
    total_chats: int
    total_plans: int
    chats: List[ChatSummary]


class PaginatedChatLogs(BaseModel):
    """分页的聊天日志列表"""
    data: List[PlanLogSummary]
    total: int
    page: int
    page_size: int
    chat_id: str


def parse_timestamp_from_filename(filename: str) -> float:
    """从文件名解析时间戳: 1766497488220_af92bdb1.json -> 1766497488.220"""
    try:
        timestamp_str = filename.split('_')[0]
        # 时间戳是毫秒级，需要转换为秒
        return float(timestamp_str) / 1000
    except (ValueError, IndexError):
        return 0


@router.get("/overview", response_model=PlannerOverview)
async def get_planner_overview():
    """
    获取规划器总览 - 轻量级接口
    只统计文件数量，不读取文件内容
    """
    if not PLAN_LOG_DIR.exists():
        return PlannerOverview(total_chats=0, total_plans=0, chats=[])
    
    chats = []
    total_plans = 0
    
    for chat_dir in PLAN_LOG_DIR.iterdir():
        if not chat_dir.is_dir():
            continue
        
        # 只统计json文件数量
        json_files = list(chat_dir.glob("*.json"))
        plan_count = len(json_files)
        total_plans += plan_count
        
        if plan_count == 0:
            continue
        
        # 从文件名获取最新时间戳
        latest_file = max(json_files, key=lambda f: parse_timestamp_from_filename(f.name))
        latest_timestamp = parse_timestamp_from_filename(latest_file.name)
        
        chats.append(ChatSummary(
            chat_id=chat_dir.name,
            plan_count=plan_count,
            latest_timestamp=latest_timestamp,
            latest_filename=latest_file.name
        ))
    
    # 按最新时间戳排序
    chats.sort(key=lambda x: x.latest_timestamp, reverse=True)
    
    return PlannerOverview(
        total_chats=len(chats),
        total_plans=total_plans,
        chats=chats
    )


@router.get("/chat/{chat_id}/logs", response_model=PaginatedChatLogs)
async def get_chat_plan_logs(
    chat_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索关键词，匹配提示词内容")
):
    """
    获取指定聊天的规划日志列表（分页）
    需要读取文件内容获取摘要信息
    支持搜索提示词内容
    """
    chat_dir = PLAN_LOG_DIR / chat_id
    if not chat_dir.exists():
        return PaginatedChatLogs(
            data=[], total=0, page=page, page_size=page_size, chat_id=chat_id
        )
    
    # 先获取所有文件并按时间戳排序
    json_files = list(chat_dir.glob("*.json"))
    json_files.sort(key=lambda f: parse_timestamp_from_filename(f.name), reverse=True)
    
    # 如果有搜索关键词，需要过滤文件
    if search:
        search_lower = search.lower()
        filtered_files = []
        for log_file in json_files:
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    prompt = data.get('prompt', '')
                    if search_lower in prompt.lower():
                        filtered_files.append(log_file)
            except Exception:
                continue
        json_files = filtered_files
    
    total = len(json_files)
    
    # 分页 - 只读取当前页的文件
    offset = (page - 1) * page_size
    page_files = json_files[offset:offset + page_size]
    
    logs = []
    for log_file in page_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                reasoning = data.get('reasoning', '')
                actions = data.get('actions', [])
                action_types = [a.get('action_type', '') for a in actions if a.get('action_type')]
                logs.append(PlanLogSummary(
                    chat_id=data.get('chat_id', chat_id),
                    timestamp=data.get('timestamp', parse_timestamp_from_filename(log_file.name)),
                    filename=log_file.name,
                    action_count=len(actions),
                    action_types=action_types,
                    total_plan_ms=data.get('timing', {}).get('total_plan_ms', 0),
                    llm_duration_ms=data.get('timing', {}).get('llm_duration_ms', 0),
                    reasoning_preview=reasoning[:100] if reasoning else ''
                ))
        except Exception:
            # 文件读取失败时使用文件名信息
            logs.append(PlanLogSummary(
                chat_id=chat_id,
                timestamp=parse_timestamp_from_filename(log_file.name),
                filename=log_file.name,
                action_count=0,
                action_types=[],
                total_plan_ms=0,
                llm_duration_ms=0,
                reasoning_preview='[读取失败]'
            ))
    
    return PaginatedChatLogs(
        data=logs,
        total=total,
        page=page,
        page_size=page_size,
        chat_id=chat_id
    )


@router.get("/log/{chat_id}/{filename}", response_model=PlanLogDetail)
async def get_log_detail(chat_id: str, filename: str):
    """获取规划日志详情 - 按需加载完整内容"""
    log_file = PLAN_LOG_DIR / chat_id / filename
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return PlanLogDetail(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志失败: {str(e)}")


# ========== 兼容旧接口 ==========

@router.get("/stats")
async def get_planner_stats():
    """获取规划器统计信息 - 兼容旧接口"""
    overview = await get_planner_overview()
    
    # 获取最近10条计划的摘要
    recent_plans = []
    for chat in overview.chats[:5]:  # 从最近5个聊天中获取
        try:
            chat_logs = await get_chat_plan_logs(chat.chat_id, page=1, page_size=2)
            recent_plans.extend(chat_logs.data)
        except Exception:
            continue
    
    # 按时间排序取前10
    recent_plans.sort(key=lambda x: x.timestamp, reverse=True)
    recent_plans = recent_plans[:10]
    
    return {
        "total_chats": overview.total_chats,
        "total_plans": overview.total_plans,
        "avg_plan_time_ms": 0,
        "avg_llm_time_ms": 0,
        "recent_plans": recent_plans
    }


@router.get("/chats")
async def get_chat_list():
    """获取所有聊天ID列表 - 兼容旧接口"""
    overview = await get_planner_overview()
    return [chat.chat_id for chat in overview.chats]


@router.get("/all-logs")
async def get_all_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """获取所有规划日志 - 兼容旧接口"""
    if not PLAN_LOG_DIR.exists():
        return {"data": [], "total": 0, "page": page, "page_size": page_size}
    
    # 收集所有文件
    all_files = []
    for chat_dir in PLAN_LOG_DIR.iterdir():
        if chat_dir.is_dir():
            for log_file in chat_dir.glob("*.json"):
                all_files.append((chat_dir.name, log_file))
    
    # 按时间戳排序
    all_files.sort(key=lambda x: parse_timestamp_from_filename(x[1].name), reverse=True)
    
    total = len(all_files)
    offset = (page - 1) * page_size
    page_files = all_files[offset:offset + page_size]
    
    logs = []
    for chat_id, log_file in page_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                reasoning = data.get('reasoning', '')
                logs.append({
                    "chat_id": data.get('chat_id', chat_id),
                    "timestamp": data.get('timestamp', parse_timestamp_from_filename(log_file.name)),
                    "filename": log_file.name,
                    "action_count": len(data.get('actions', [])),
                    "total_plan_ms": data.get('timing', {}).get('total_plan_ms', 0),
                    "llm_duration_ms": data.get('timing', {}).get('llm_duration_ms', 0),
                    "reasoning_preview": reasoning[:100] if reasoning else ''
                })
        except Exception:
            continue
    
    return {"data": logs, "total": total, "page": page, "page_size": page_size}
