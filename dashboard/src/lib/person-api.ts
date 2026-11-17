/**
 * 人物信息管理 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 * 已切换为 throw 契约：成功直接返回数据，失败抛出 ApiError（配合 TanStack Query 使用）。
 */
import { ApiError, backendApi, requireSuccess } from '@/lib/http'
import type {
  Individual,
  IndividualMergePreview,
  IndividualProfile,
  ProfileField,
  IndividualDirectoryResponse,
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
  user_id?: string
  exclude_numeric_user_ids?: boolean
}): Promise<PersonListData> {
  const data = await backendApi.get<PersonListResponse>(`${API_BASE}/list`, {
    query: {
      page: params.page || undefined,
      page_size: params.page_size || undefined,
      search: params.search || undefined,
      is_known: params.is_known,
      platform: params.platform || undefined,
      user_id: params.user_id || undefined,
      exclude_numeric_user_ids: params.exclude_numeric_user_ids,
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
  const stats = requireSuccess(data, '获取统计数据失败').data
  const counts = ['individuals', 'linked_individuals', 'total', 'known', 'unknown'] as const
  for (const field of counts) {
    if (!Number.isSafeInteger(stats?.[field]) || stats[field] < 0) {
      throw new ApiError(
        `人物统计接口缺少有效字段 ${field}，请重启当前 Bot 以加载新版后端，然后重试。`,
        {
          detail: data,
        }
      )
    }
  }
  return stats
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

function validateIndividual(individual: Individual): Individual {
  if (
    typeof individual.display_name !== 'string' ||
    typeof individual.is_known !== 'boolean' ||
    !Array.isArray(individual.profile_sources)
  ) {
    throw new ApiError('人物资料接口版本不匹配，请重启当前 Bot 加载新版后端后重试。', {
      detail: individual,
    })
  }
  return individual
}

export async function getIndividual(individualId: string): Promise<Individual> {
  const individual = await backendApi.get<Individual>(
    `${API_BASE}/individual/${encodeURIComponent(individualId)}`,
    {
      errorMessage: '获取个体成员失败',
    }
  )
  return validateIndividual(individual)
}

export async function previewIndividualMerge(
  targetIndividualId: string,
  individualIds: string[]
): Promise<IndividualMergePreview> {
  return backendApi.post<IndividualMergePreview>(`${API_BASE}/individual/preview`, {
    body: { target_individual_id: targetIndividualId, individual_ids: individualIds },
    errorMessage: '加载合并预览失败',
  })
}

export async function mergeIndividuals(
  preview: IndividualMergePreview,
  profile: IndividualProfile,
  resolvedConflicts: ProfileField[]
): Promise<Individual> {
  return backendApi.post<Individual>(`${API_BASE}/individual/merge`, {
    body: {
      target_individual_id: preview.target_individual_id,
      expected_members: preview.members,
      expected_revision: preview.revision,
      profile,
      resolved_conflicts: resolvedConflicts,
    },
    errorMessage: '合并人物失败',
  })
}

export async function getIndividualDirectory(params: {
  page: number
  page_size: number
  view: 'individuals' | 'persons'
  search?: string
  platform?: string
  is_known?: boolean
  linked_only?: boolean
}): Promise<IndividualDirectoryResponse> {
  const directory = await backendApi.get<IndividualDirectoryResponse>(
    `${API_BASE}/directory/list`,
    {
      query: params,
      errorMessage: '获取个体与账号关系失败',
    }
  )
  directory.data.forEach(validateIndividual)
  return directory
}
