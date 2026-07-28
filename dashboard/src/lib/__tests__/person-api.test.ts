import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'
import type { PersonInfo } from '@/types/person'

import {
  batchDeletePersons,
  deletePerson,
  getPersonDetail,
  getPersonList,
  getPersonStats,
  updatePerson,
} from '../person-api'

// 只替换 backendApi 的请求方法，保留真实的 ApiError / requireSuccess，
// 以便验证业务级 success 标记解包与 throw 契约的真实行为
vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const patchMock = vi.mocked(backendApi.patch)
const deleteMock = vi.mocked(backendApi.delete)

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  patchMock.mockReset()
  deleteMock.mockReset()
})

/** 构造一条完整的人物信息，便于各用例复用 */
function buildPerson(overrides: Partial<PersonInfo> = {}): PersonInfo {
  return {
    id: 1,
    is_known: true,
    person_id: 'p-1',
    person_name: '麦麦',
    name_reason: '自称',
    platform: 'qq',
    user_id: '10001',
    nickname: '麦麦酱',
    group_nick_name: null,
    memory_points: null,
    know_times: 3,
    know_since: 1700000000,
    last_know: 1700000600,
    ...overrides,
  }
}

describe('getPersonList', () => {
  it('归一化查询参数并把响应转换为分页数据对象', async () => {
    const person = buildPerson()
    getMock.mockResolvedValue({
      success: true,
      total: 42,
      page: 2,
      page_size: 20,
      data: [person],
    })

    await expect(
      getPersonList({ page: 2, page_size: 20, search: '', is_known: false, platform: '' })
    ).resolves.toEqual({
      data: [person],
      total: 42,
      page: 2,
      page_size: 20,
    })

    expect(getMock).toHaveBeenCalledWith('/api/webui/person/list', {
      query: {
        page: 2,
        page_size: 20,
        search: undefined,
        is_known: false,
        platform: undefined,
      },
      errorMessage: '获取人物列表失败',
    })
  })

  it('业务级 success 为 false 时抛出携带后端 message 的 ApiError', async () => {
    getMock.mockResolvedValue({ success: false, message: '人物索引尚未构建' })

    await expect(getPersonList({})).rejects.toBeInstanceOf(ApiError)
    await expect(getPersonList({})).rejects.toMatchObject({ message: '人物索引尚未构建' })
  })

  it('HTTP 层失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取人物列表失败', { status: 500 }))

    await expect(getPersonList({ page: 1 })).rejects.toMatchObject({ status: 500 })
  })
})

describe('getPersonDetail', () => {
  it('按 person_id 拼接详情 URL 并解包 data 字段', async () => {
    const person = buildPerson({ person_id: 'p-9' })
    getMock.mockResolvedValue({ success: true, data: person })

    await expect(getPersonDetail('p-9')).resolves.toBe(person)
    expect(getMock).toHaveBeenCalledWith('/api/webui/person/p-9', {
      errorMessage: '获取人物详情失败',
    })
  })

  it('业务级失败时抛出 ApiError（未给 message 时使用兜底文案）', async () => {
    getMock.mockResolvedValue({ success: false })

    await expect(getPersonDetail('p-404')).rejects.toMatchObject({
      message: '获取人物详情失败',
    })
  })
})

describe('updatePerson', () => {
  it('按 person_id 发起 PATCH 并返回更新后的人物', async () => {
    const person = buildPerson({ person_name: '新名字' })
    patchMock.mockResolvedValue({ success: true, message: '更新成功', data: person })

    await expect(updatePerson('p-1', { person_name: '新名字' })).resolves.toBe(person)
    expect(patchMock).toHaveBeenCalledWith('/api/webui/person/p-1', {
      body: { person_name: '新名字' },
      errorMessage: '更新人物信息失败',
    })
  })

  it('成功响应缺少 data 时仍抛出 ApiError', async () => {
    patchMock.mockResolvedValue({ success: true })

    await expect(updatePerson('p-1', { nickname: '小麦' })).rejects.toBeInstanceOf(ApiError)
    await expect(updatePerson('p-1', { nickname: '小麦' })).rejects.toMatchObject({
      message: '更新人物信息失败',
    })
  })

  it('业务级失败时抛出携带后端 message 的 ApiError', async () => {
    patchMock.mockResolvedValue({ success: false, message: '人物不存在' })

    await expect(updatePerson('p-x', { is_known: true })).rejects.toMatchObject({
      message: '人物不存在',
    })
  })
})

describe('deletePerson', () => {
  it('按 person_id 发起 DELETE，成功时无返回值', async () => {
    deleteMock.mockResolvedValue({ success: true, message: '删除成功' })

    await expect(deletePerson('p-1')).resolves.toBeUndefined()
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/person/p-1', {
      errorMessage: '删除人物信息失败',
    })
  })

  it('业务级失败时抛出 ApiError', async () => {
    deleteMock.mockResolvedValue({ success: false, message: '删除被拒绝' })

    await expect(deletePerson('p-1')).rejects.toMatchObject({ message: '删除被拒绝' })
  })
})

describe('getPersonStats', () => {
  it('从 stats/summary 端点读取并解包 data 字段', async () => {
    const stats = { total: 10, known: 6, unknown: 4, platforms: { qq: 10 } }
    getMock.mockResolvedValue({ success: true, data: stats })

    await expect(getPersonStats()).resolves.toBe(stats)
    expect(getMock).toHaveBeenCalledWith('/api/webui/person/stats/summary', {
      errorMessage: '获取统计数据失败',
    })
  })
})

describe('batchDeletePersons', () => {
  it('把 person_ids 提交到批量删除接口并剔除 success 标记后返回结果', async () => {
    postMock.mockResolvedValue({
      success: true,
      message: '批量删除完成',
      deleted_count: 2,
      failed_count: 1,
      failed_ids: ['p-3'],
    })

    await expect(batchDeletePersons(['p-1', 'p-2', 'p-3'])).resolves.toEqual({
      message: '批量删除完成',
      deleted_count: 2,
      failed_count: 1,
      failed_ids: ['p-3'],
    })
    expect(postMock).toHaveBeenCalledWith('/api/webui/person/batch/delete', {
      body: { person_ids: ['p-1', 'p-2', 'p-3'] },
      errorMessage: '批量删除失败',
    })
  })

  it('业务级失败时抛出携带后端 message 的 ApiError', async () => {
    postMock.mockResolvedValue({ success: false, message: '批量删除被拒绝' })

    await expect(batchDeletePersons(['p-1'])).rejects.toMatchObject({
      message: '批量删除被拒绝',
    })
  })
})
