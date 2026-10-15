/**
 * 首页资讯 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint 与响应类型。失败时抛出 ApiError，由调用方自行 catch。
 */
import { backendApi } from '@/lib/http'

export interface NewsItem {
  id: string
  title: string
  summary: string
  /** 正文 Markdown */
  content: string
  url: string
  source: string
  published_at: string
  pinned: boolean
}

export interface NewsResponse {
  items: NewsItem[]
  total: number
  updated_at: string
}

/**
 * 获取首页资讯列表
 *
 * @param force 跳过服务端缓存强制回源，供用户手动刷新使用
 */
export async function getNews(force = false): Promise<NewsResponse> {
  return backendApi.get<NewsResponse>('/api/webui/news', {
    errorMessage: '获取资讯失败',
    query: { force: force ? true : undefined },
  })
}
