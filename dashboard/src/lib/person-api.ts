/**
 * 人物信息管理 API
 */
import { fetchWithAuth, getAuthHeaders } from '@/lib/fetch-with-auth'
import type { ApiResponse } from '@/types/api'
import type {
  PersonListResponse,
  PersonDetailResponse,
  PersonUpdateRequest,
  PersonUpdateResponse,
  PersonDeleteResponse,
  PersonStatsResponse,
  PersonInfo,
  PersonStats,
} from '@/types/person'

const API_BASE = '/api/webui/person'

/**
 * Person list response with pagination info
 */
export interface PersonListData {
  data: PersonInfo[]
  total: number
  page: number
  page_size: number
}

/**
 * 获取人物信息列表
 */
export async function getPersonList(params: {
  page?: number
  page_size?: number
  search?: string
  is_known?: boolean
  platform?: string
}): Promise<ApiResponse<PersonListData>> {
  const queryParams = new URLSearchParams()
  
  if (params.page) queryParams.append('page', params.page.toString())
  if (params.page_size) queryParams.append('page_size', params.page_size.toString())
  if (params.search) queryParams.append('search', params.search)
  if (params.is_known !== undefined) queryParams.append('is_known', params.is_known.toString())
  if (params.platform) queryParams.append('platform', params.platform)
  
  const response = await fetchWithAuth(`${API_BASE}/list?${queryParams}`, {
    headers: getAuthHeaders(),
  })
  
  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取人物列表失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取人物列表失败',
      }
    }
  }
  
  try {
    const data: PersonListResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: {
          data: data.data,
          total: data.total,
          page: data.page,
          page_size: data.page_size,
        },
      }
    } else {
      return {
        success: false,
        error: '获取人物列表失败',
      }
    }
  } catch {
    return {
      success: false,
      error: 'Failed to parse response',
    }
  }
}

/**
 * 获取人物详细信息
 */
export async function getPersonDetail(personId: string): Promise<ApiResponse<PersonInfo>> {
  const response = await fetchWithAuth(`${API_BASE}/${personId}`, {
    headers: getAuthHeaders(),
  })
  
  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '获取人物详情失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '获取人物详情失败',
      }
    }
  }
  
  try {
    const data: PersonDetailResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: data.data,
      }
    } else {
      return {
        success: false,
        error: '获取人物详情失败',
      }
    }
  } catch {
    return {
      success: false,
      error: 'Failed to parse response',
    }
  }
}

/**
 * 更新人物信息（增量更新）
 */
export async function updatePerson(
  personId: string,
  data: PersonUpdateRequest
): Promise<ApiResponse<PersonInfo>> {
  const response = await fetchWithAuth(`${API_BASE}/${personId}`, {
    method: 'PATCH',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  })
  
  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '更新人物信息失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '更新人物信息失败',
      }
    }
  }
  
  try {
    const data: PersonUpdateResponse = await response.json()
    if (data.success && data.data) {
      return {
        success: true,
        data: data.data,
      }
    } else {
      return {
        success: false,
        error: data.message || '更新人物信息失败',
      }
    }
  } catch {
    return {
      success: false,
      error: 'Failed to parse response',
    }
  }
}

/**
 * 删除人物信息
 */
export async function deletePerson(personId: string): Promise<ApiResponse<void>> {
  const response = await fetchWithAuth(`${API_BASE}/${personId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  })
  
  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '删除人物信息失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '删除人物信息失败',
      }
    }
  }
  
  try {
    const data: PersonDeleteResponse = await response.json()
    if (data.success) {
      return {
        success: true,
        data: undefined as unknown as void,
      }
    } else {
      return {
        success: false,
        error: data.message || '删除人物信息失败',
      }
    }
  } catch {
    return {
      success: false,
      error: 'Failed to parse response',
    }
  }
}

/**
 * 获取人物统计数据
 */
export async function getPersonStats(): Promise<ApiResponse<PersonStats>> {
  const response = await fetchWithAuth(`${API_BASE}/stats/summary`, {
    headers: getAuthHeaders(),
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
    const data: PersonStatsResponse = await response.json()
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
      error: 'Failed to parse response',
    }
  }
}

/**
 * 批量删除人物信息
 */
export async function batchDeletePersons(
  personIds: string[]
): Promise<ApiResponse<{
  message: string
  deleted_count: number
  failed_count: number
  failed_ids: string[]
}>> {
  const response = await fetchWithAuth(`${API_BASE}/batch/delete`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ person_ids: personIds }),
  })
  
  if (!response.ok) {
    try {
      const errorData = await response.json()
      return {
        success: false,
        error: errorData.detail || errorData.message || '批量删除失败',
      }
    } catch {
      return {
        success: false,
        error: response.statusText || '批量删除失败',
      }
    }
  }
  
  try {
    const data = await response.json()
    if (data.success) {
      return {
        success: true,
        data: {
          message: data.message,
          deleted_count: data.deleted_count,
          failed_count: data.failed_count,
          failed_ids: data.failed_ids,
        },
      }
    } else {
      return {
        success: false,
        error: data.message || '批量删除失败',
      }
    }
  } catch {
    return {
      success: false,
      error: 'Failed to parse response',
    }
  }
}
