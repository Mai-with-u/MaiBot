/**
 * 表达方式管理 API
 */
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type {
  ExpressionListResponse,
  ExpressionDetailResponse,
  ExpressionCreateRequest,
  ExpressionCreateResponse,
  ExpressionUpdateRequest,
  ExpressionUpdateResponse,
  ExpressionDeleteResponse,
  ExpressionStatsResponse,
  ChatListResponse,
  ChatInfo,
  ReviewStats,
  ReviewListResponse,
  BatchReviewItem,
  BatchReviewResponse,
} from '@/types/expression'
import type { ApiResponse } from '@/types/api'

const API_BASE = '/api/webui/expression'

/**
 * 获取聊天列表
 */
export async function getChatList(): Promise<ApiResponse<ChatInfo[]>> {
  const response = await fetchWithAuth(`${API_BASE}/chats`, {
    
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取聊天列表失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取聊天列表失败',
      }
    }
  }

  try {
    const data: ChatListResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data.data,
      }
    } else {
      return {
        success: false,
        error: '获取聊天列表失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析聊天列表响应',
    }
  }
}

/**
 * 获取表达方式列表
 */
export async function getExpressionList(params: {
  page?: number
  page_size?: number
  search?: string
  chat_id?: string
}): Promise<ApiResponse<ExpressionListResponse>> {
  const queryParams = new URLSearchParams()

  if (params.page) queryParams.append('page', params.page.toString())
  if (params.page_size) queryParams.append('page_size', params.page_size.toString())
  if (params.search) queryParams.append('search', params.search)
  if (params.chat_id) queryParams.append('chat_id', params.chat_id)

  const response = await fetchWithAuth(`${API_BASE}/list?${queryParams}`, {
    
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取表达方式列表失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取表达方式列表失败',
      }
    }
  }

  try {
    const data: ExpressionListResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data,
      }
    } else {
      return {
        success: false,
        error: '获取表达方式列表失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析表达方式列表响应',
    }
  }
}

/**
 * 获取表达方式详细信息
 */
export async function getExpressionDetail(expressionId: number): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/${expressionId}`, {
    
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取表达方式详情失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取表达方式详情失败',
      }
    }
  }

  try {
    const data: ExpressionDetailResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data.data,
      }
    } else {
      return {
        success: false,
        error: '获取表达方式详情失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析表达方式详情响应',
    }
  }
}

/**
 * 创建表达方式
 */
export async function createExpression(
  data: ExpressionCreateRequest
): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/`, {
    method: 'POST',
    
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '创建表达方式失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '创建表达方式失败',
      }
    }
  }

  try {
    const responseData: ExpressionCreateResponse = await response.json()
    if (responseData.success) {
      return {
        success: true,
        data: responseData.data,
      }
    } else {
      return {
        success: false,
        error: responseData.message || '创建表达方式失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析创建表达方式响应',
    }
  }
}

/**
 * 更新表达方式（增量更新）
 */
export async function updateExpression(
  expressionId: number,
  data: ExpressionUpdateRequest
): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/${expressionId}`, {
    method: 'PATCH',
    
    body: JSON.stringify(data),
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '更新表达方式失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '更新表达方式失败',
      }
    }
  }

  try {
    const responseData: ExpressionUpdateResponse = await response.json()
    if (responseData.success) {
      return {
        success: true,
        data: responseData.data || {},
      }
    } else {
      return {
        success: false,
        error: responseData.message || '更新表达方式失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析更新表达方式响应',
    }
  }
}

/**
 * 删除表达方式
 */
export async function deleteExpression(expressionId: number): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/${expressionId}`, {
    method: 'DELETE',
    
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '删除表达方式失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '删除表达方式失败',
      }
    }
  }

  try {
    const data: ExpressionDeleteResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: {},
      }
    } else {
      return {
        success: false,
        error: data.message || '删除表达方式失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析删除表达方式响应',
    }
  }
}

/**
 * 批量删除表达方式
 */
export async function batchDeleteExpressions(expressionIds: number[]): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/batch/delete`, {
    method: 'POST',
    
    body: JSON.stringify({ ids: expressionIds }),
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '批量删除表达方式失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '批量删除表达方式失败',
      }
    }
  }

  try {
    const data: ExpressionDeleteResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: {},
      }
    } else {
      return {
        success: false,
        error: data.message || '批量删除表达方式失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析批量删除表达方式响应',
    }
  }
}

/**
 * 获取表达方式统计数据
 */
export async function getExpressionStats(): Promise<ApiResponse<any>> {
  const response = await fetchWithAuth(`${API_BASE}/stats/summary`, {
    
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取统计数据失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取统计数据失败',
      }
    }
  }

  try {
    const data: ExpressionStatsResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data.data,
      }
    } else {
      return {
        success: false,
        error: '获取统计数据失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析统计数据响应',
    }
  }
}

// ============ 审核相关 API ============

/**
 * 获取审核统计数据
 */
export async function getReviewStats(): Promise<ApiResponse<ReviewStats>> {
  const response = await fetchWithAuth(`${API_BASE}/review/stats`)

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取审核统计失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取审核统计失败',
      }
    }
  }

  try {
    const data = await response.json() as ReviewStats
    return {
      success: true,
      data: data,
    }
  } catch {
    return {
      success: false,
      error: '无法解析审核统计响应',
    }
  }
}

/**
 * 获取审核列表
 */
export async function getReviewList(params: {
  page?: number
  page_size?: number
  filter_type?: 'unchecked' | 'passed' | 'rejected' | 'all'
  search?: string
  chat_id?: string
}): Promise<ApiResponse<ReviewListResponse>> {
  const queryParams = new URLSearchParams()

  if (params.page) queryParams.append('page', params.page.toString())
  if (params.page_size) queryParams.append('page_size', params.page_size.toString())
  if (params.filter_type) queryParams.append('filter_type', params.filter_type)
  if (params.search) queryParams.append('search', params.search)
  if (params.chat_id) queryParams.append('chat_id', params.chat_id)

  const response = await fetchWithAuth(`${API_BASE}/review/list?${queryParams}`)

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取审核列表失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取审核列表失败',
      }
    }
  }

  try {
    const data: ReviewListResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data,
      }
    } else {
      return {
        success: false,
        error: '获取审核列表失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析审核列表响应',
    }
  }
}

/**
 * 批量审核表达方式
 */
export async function batchReviewExpressions(
  items: BatchReviewItem[]
): Promise<ApiResponse<BatchReviewResponse>> {
  const response = await fetchWithAuth(`${API_BASE}/review/batch`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  })

  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '批量审核失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '批量审核失败',
      }
    }
  }

  try {
    const data: BatchReviewResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data,
      }
    } else {
      return {
        success: false,
        error: '批量审核失败',
      }
    }
  } catch {
    return {
      success: false,
      error: '无法解析批量审核响应',
    }
  }
}
