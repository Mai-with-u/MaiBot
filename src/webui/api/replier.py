"""
回复器监控API
提供回复器日志数据的查询接口

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

router = APIRouter(prefix="/api/replier", tags=["replier"])

# 回复器日志目录
REPLY_LOG_DIR = Path("logs/reply")


class ReplierChatSummary(BaseModel):
    """聊天摘要 - 轻量级，不读取文件内容"""
    chat_id: str
    reply_count: int
    latest_timestamp: float
    latest_filename: str


class ReplyLogSummary(BaseModel):
    """回复日志摘要"""
    chat_id: str
    timestamp: float
    filename: str
    model: str
    success: bool
    llm_ms: float
    overall_ms: float
    output_preview: str


class ReplyLogDetail(BaseModel):
    """回复日志详情"""
    type: str
    chat_id: str
    timestamp: float
    prompt: str
    output: str
    processed_output: List[str]
    model: str
    reasoning: str
    think_level: int
    timing: Dict
    error: Optional[str] = None
    success: bool


class ReplierOverview(BaseModel):
    """回复器总览 - 轻量级统计"""
    total_chats: int
    total_replies: int
    chats: List[ReplierChatSummary]


class PaginatedReplyLogs(BaseModel):
    """分页的回复日志列表"""
    data: List[ReplyLogSummary]
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


@router.get("/overview", response_model=ReplierOverview)
async def get_replier_overview():
    """
    获取回复器总览 - 轻量级接口
    只统计文件数量，不读取文件内容
    """
    if not REPLY_LOG_DIR.exists():
        return ReplierOverview(total_chats=0, total_replies=0, chats=[])
    
    chats = []
    total_replies = 0
    
    for chat_dir in REPLY_LOG_DIR.iterdir():
        if not chat_dir.is_dir():
            continue
        
        # 只统计json文件数量
        json_files = list(chat_dir.glob("*.json"))
        reply_count = len(json_files)
        total_replies += reply_count
        
        if reply_count == 0:
            continue
        
        # 从文件名获取最新时间戳
        latest_file = max(json_files, key=lambda f: parse_timestamp_from_filename(f.name))
        latest_timestamp = parse_timestamp_from_filename(latest_file.name)
        
        chats.append(ReplierChatSummary(
            chat_id=chat_dir.name,
            reply_count=reply_count,
            latest_timestamp=latest_timestamp,
            latest_filename=latest_file.name
        ))
    
    # 按最新时间戳排序
    chats.sort(key=lambda x: x.latest_timestamp, reverse=True)
    
    return ReplierOverview(
        total_chats=len(chats),
        total_replies=total_replies,
        chats=chats
    )


@router.get("/chat/{chat_id}/logs", response_model=PaginatedReplyLogs)
async def get_chat_reply_logs(
    chat_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索关键词，匹配提示词内容")
):
    """
    获取指定聊天的回复日志列表（分页）
    需要读取文件内容获取摘要信息
    支持搜索提示词内容
    """
    chat_dir = REPLY_LOG_DIR / chat_id
    if not chat_dir.exists():
        return PaginatedReplyLogs(
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
                output = data.get('output', '')
                logs.append(ReplyLogSummary(
                    chat_id=data.get('chat_id', chat_id),
                    timestamp=data.get('timestamp', parse_timestamp_from_filename(log_file.name)),
                    filename=log_file.name,
                    model=data.get('model', ''),
                    success=data.get('success', True),
                    llm_ms=data.get('timing', {}).get('llm_ms', 0),
                    overall_ms=data.get('timing', {}).get('overall_ms', 0),
                    output_preview=output[:100] if output else ''
                ))
        except Exception:
            # 文件读取失败时使用文件名信息
            logs.append(ReplyLogSummary(
                chat_id=chat_id,
                timestamp=parse_timestamp_from_filename(log_file.name),
                filename=log_file.name,
                model='',
                success=False,
                llm_ms=0,
                overall_ms=0,
                output_preview='[读取失败]'
            ))
    
    return PaginatedReplyLogs(
        data=logs,
        total=total,
        page=page,
        page_size=page_size,
        chat_id=chat_id
    )


@router.get("/log/{chat_id}/{filename}", response_model=ReplyLogDetail)
async def get_reply_log_detail(chat_id: str, filename: str):
    """获取回复日志详情 - 按需加载完整内容"""
    log_file = REPLY_LOG_DIR / chat_id / filename
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return ReplyLogDetail(
                type=data.get('type', 'reply'),
                chat_id=data.get('chat_id', chat_id),
                timestamp=data.get('timestamp', 0),
                prompt=data.get('prompt', ''),
                output=data.get('output', ''),
                processed_output=data.get('processed_output', []),
                model=data.get('model', ''),
                reasoning=data.get('reasoning', ''),
                think_level=data.get('think_level', 0),
                timing=data.get('timing', {}),
                error=data.get('error'),
                success=data.get('success', True)
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志失败: {str(e)}")


# ========== 兼容接口 ==========

@router.get("/stats")
async def get_replier_stats():
    """获取回复器统计信息"""
    overview = await get_replier_overview()
    
    # 获取最近10条回复的摘要
    recent_replies = []
    for chat in overview.chats[:5]:  # 从最近5个聊天中获取
        try:
            chat_logs = await get_chat_reply_logs(chat.chat_id, page=1, page_size=2)
            recent_replies.extend(chat_logs.data)
        except Exception:
            continue
    
    # 按时间排序取前10
    recent_replies.sort(key=lambda x: x.timestamp, reverse=True)
    recent_replies = recent_replies[:10]
    
    return {
        "total_chats": overview.total_chats,
        "total_replies": overview.total_replies,
        "recent_replies": recent_replies
    }


@router.get("/chats")
async def get_replier_chat_list():
    """获取所有聊天ID列表"""
    overview = await get_replier_overview()
    return [chat.chat_id for chat in overview.chats]
