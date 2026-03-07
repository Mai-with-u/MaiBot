/**
 * 黑话（俚语）管理 API
 */
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type {
  JargonListResponse,
  JargonDetailResponse,
  JargonCreateRequest,
  JargonCreateResponse,
  JargonUpdateRequest,
  JargonUpdateResponse,
  JargonDeleteResponse,
  JargonStatsResponse,
  JargonChatListResponse,
} from '@/types/jargon'

const API_BASE = '/api/webui/jargon'

/**
 * 获取聊天列表（有黑话记录的聊天）
 */
export async function getJargonChatList(): Promise<JargonChatListResponse> {
  const response = await fetchWithAuth(`${API_BASE}/chats`, {})
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取聊天列表失败')
  }
  
  return response.json()
}

/**
 * 获取黑话列表
 */
export async function getJargonList(params: {
  page?: number
  page_size?: number
  search?: string
  chat_id?: string
  is_jargon?: boolean | null
  is_global?: boolean
}): Promise<JargonListResponse> {
  const queryParams = new URLSearchParams()
  
  if (params.page) queryParams.append('page', params.page.toString())
  if (params.page_size) queryParams.append('page_size', params.page_size.toString())
  if (params.search) queryParams.append('search', params.search)
  if (params.chat_id) queryParams.append('chat_id', params.chat_id)
  if (params.is_jargon !== undefined && params.is_jargon !== null) {
    queryParams.append('is_jargon', params.is_jargon.toString())
  }
  if (params.is_global !== undefined) {
    queryParams.append('is_global', params.is_global.toString())
  }
  
  const response = await fetchWithAuth(`${API_BASE}/list?${queryParams}`, {})
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取黑话列表失败')
  }
  
  return response.json()
}

/**
 * 获取黑话详细信息
 */
export async function getJargonDetail(jargonId: number): Promise<JargonDetailResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${jargonId}`, {})
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取黑话详情失败')
  }
  
  return response.json()
}

/**
 * 创建黑话
 */
export async function createJargon(
  data: JargonCreateRequest
): Promise<JargonCreateResponse> {
  const response = await fetchWithAuth(`${API_BASE}/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '创建黑话失败')
  }
  
  return response.json()
}

/**
 * 更新黑话（增量更新）
 */
export async function updateJargon(
  jargonId: number,
  data: JargonUpdateRequest
): Promise<JargonUpdateResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${jargonId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '更新黑话失败')
  }
  
  return response.json()
}

/**
 * 删除黑话
 */
export async function deleteJargon(jargonId: number): Promise<JargonDeleteResponse> {
  const response = await fetchWithAuth(`${API_BASE}/${jargonId}`, {
    method: 'DELETE',
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '删除黑话失败')
  }
  
  return response.json()
}

/**
 * 批量删除黑话
 */
export async function batchDeleteJargons(jargonIds: number[]): Promise<JargonDeleteResponse> {
  const response = await fetchWithAuth(`${API_BASE}/batch/delete`, {
    method: 'POST',
    body: JSON.stringify({ ids: jargonIds }),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '批量删除黑话失败')
  }
  
  return response.json()
}

/**
 * 获取黑话统计数据
 */
export async function getJargonStats(): Promise<JargonStatsResponse> {
  const response = await fetchWithAuth(`${API_BASE}/stats/summary`, {})
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '获取黑话统计失败')
  }
  
  return response.json()
}

/**
 * 批量设置黑话状态
 */
export async function batchSetJargonStatus(
  jargonIds: number[],
  isJargon: boolean
): Promise<JargonUpdateResponse> {
  const queryParams = new URLSearchParams()
  jargonIds.forEach(id => queryParams.append('ids', id.toString()))
  queryParams.append('is_jargon', isJargon.toString())
  
  const response = await fetchWithAuth(`${API_BASE}/batch/set-jargon?${queryParams}`, {
    method: 'POST',
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || '批量设置黑话状态失败')
  }
  
  return response.json()
}
