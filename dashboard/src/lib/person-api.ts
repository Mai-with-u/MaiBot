/**
 * 人物信息管理 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 * 已切换为 throw 契约：成功直接返回数据，失败抛出 ApiError（配合 TanStack Query 使用）。
 */
import { ApiError, backendApi, requireSuccess } from '@/lib/http'
import type {
  PersonDeleteResponse,
  PersonDetailResponse,
  PersonInfo,
  PersonListResponse,
  PersonStats,
  PersonStatsResponse,
  PersonUpdateRequest,
  PersonUpdateResponse,
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
}): Promise<PersonListData> {
  const data = await backendApi.get<PersonListResponse>(`${API_BASE}/list`, {
    query: {
      page: params.page || undefined,
      page_size: params.page_size || undefined,
      search: params.search || undefined,
      is_known: params.is_known,
      platform: params.platform || undefined,
    },
    errorMessage: '获取人物列表失败',
  })
  const checked = requireSuccess(data, '获取人物列表失败')
  return {
    data: checked.data,
    total: checked.total,
    page: checked.page,
    page_size: checked.page_size,
  }
}

/**
 * 获取人物详细信息
 */
export async function getPersonDetail(personId: string): Promise<PersonInfo> {
  const data = await backendApi.get<PersonDetailResponse>(`${API_BASE}/${personId}`, {
    errorMessage: '获取人物详情失败',
  })
  return requireSuccess(data, '获取人物详情失败').data
}

/**
 * 更新人物信息（增量更新）
 */
export async function updatePerson(
  personId: string,
  data: PersonUpdateRequest
): Promise<PersonInfo> {
  const responseData = await backendApi.patch<PersonUpdateResponse>(`${API_BASE}/${personId}`, {
    body: data,
    errorMessage: '更新人物信息失败',
  })
  const checked = requireSuccess(responseData, '更新人物信息失败')
  if (!checked.data) {
    throw new ApiError(checked.message || '更新人物信息失败', { detail: checked })
  }
  return checked.data
}

/**
 * 删除人物信息
 */
export async function deletePerson(personId: string): Promise<void> {
  const data = await backendApi.delete<PersonDeleteResponse>(`${API_BASE}/${personId}`, {
    errorMessage: '删除人物信息失败',
  })
  requireSuccess(data, '删除人物信息失败')
}

/**
 * 获取人物统计数据
 */
export async function getPersonStats(): Promise<PersonStats> {
  const data = await backendApi.get<PersonStatsResponse>(`${API_BASE}/stats/summary`, {
    errorMessage: '获取统计数据失败',
  })
  return requireSuccess(data, '获取统计数据失败').data
}

/**
 * 批量删除人物信息
 */
export async function batchDeletePersons(personIds: string[]): Promise<{
  message: string
  deleted_count: number
  failed_count: number
  failed_ids: string[]
}> {
  const data = await backendApi.post<{
    success: boolean
    message: string
    deleted_count: number
    failed_count: number
    failed_ids: string[]
  }>(`${API_BASE}/batch/delete`, {
    body: { person_ids: personIds },
    errorMessage: '批量删除失败',
  })
  const checked = requireSuccess(data, '批量删除失败')
  return {
    message: checked.message,
    deleted_count: checked.deleted_count,
    failed_count: checked.failed_count,
    failed_ids: checked.failed_ids,
  }
}
