from typing import List

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """单条首页资讯。"""

    id: str = Field("", description="资讯 ID")
    title: str = Field(..., description="资讯标题")
    summary: str = Field("", description="资讯摘要")
    content: str = Field("", description="资讯正文（Markdown）")
    url: str = Field("", description="相关链接")
    source: str = Field("", description="资讯来源")
    published_at: str = Field("", description="发布时间（ISO 8601 字符串）")
    pinned: bool = Field(False, description="是否置顶")


class NewsResponse(BaseModel):
    """首页资讯列表。"""

    items: List[NewsItem] = Field(default_factory=list, description="资讯列表")
    total: int = Field(0, description="资讯总数")
    updated_at: str = Field("", description="资讯源本次读取时间（ISO 8601 字符串）")
