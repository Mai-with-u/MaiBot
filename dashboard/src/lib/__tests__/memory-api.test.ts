import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, backendApi } from '@/lib/http'

import {
  applyBestMemoryTuningProfile,
  cancelMemoryImportTask,
  correctMemoryProfileEvidence,
  createMemoryLpmmConvertImport,
  createMemoryLpmmOpenieImport,
  createMemoryMaibotMigrationImport,
  createMemoryPasteImport,
  createMemoryRawScanImport,
  createMemoryTemporalBackfillImport,
  createMemoryTuningTask,
  createMemoryUploadImport,
  deleteMemoryProfileOverride,
  executeMemoryCorrection,
  executeMemoryDelete,
  freezeMemory,
  getMemoryConfig,
  getMemoryConfigRaw,
  getMemoryConfigSchema,
  getMemoryCorrectionPlan,
  getMemoryCorrectionPlans,
  getMemoryDeleteOperation,
  getMemoryDeleteOperations,
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryEpisodeStatus,
  getMemoryFeedbackCorrection,
  getMemoryFeedbackCorrections,
  getMemoryGraph,
  getMemoryGraphEdgeDetail,
  getMemoryGraphNodeDetail,
  getMemoryGraphParagraphDetail,
  getMemoryGraphSearch,
  getMemoryImportChatTargets,
  getMemoryImportGuide,
  getMemoryImportPathAliases,
  getMemoryImportSettings,
  getMemoryImportTask,
  getMemoryImportTaskChunks,
  getMemoryImportTasks,
  getMemoryProfileEvidence,
  getMemoryProfiles,
  getMemoryRecycleBin,
  getMemoryRuntimeConfig,
  getMemorySources,
  getMemoryTimeline,
  getMemoryTuningProfile,
  getMemoryTuningReport,
  getMemoryTuningTasks,
  previewMemoryCorrection,
  previewMemoryDelete,
  processMemoryEpisodePending,
  protectMemory,
  queryMemoryProfile,
  rebuildMemoryEpisodes,
  rebuildMemoryRuntimeVectors,
  refreshMemoryRuntimeSelfCheck,
  reinforceMemory,
  resolveMemoryImportPath,
  restoreMaintainedMemory,
  restoreMemoryDelete,
  retryMemoryImportTask,
  rollbackMemoryCorrectionPlan,
  rollbackMemoryFeedbackCorrection,
  searchMemoryProfiles,
  setMemoryProfileOverride,
  updateMemoryConfig,
  updateMemoryConfigRaw,
} from '../memory-api'

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

const requestMock = vi.mocked(backendApi.request)

/** 记忆 API 的统一路径前缀 */
const BASE = '/api/webui/memory'

/**
 * 拆解 backendApi.request 的第 index 次调用参数，
 * 把路径拆成 pathname 与结构化的 query，便于对编码后的参数做语义断言。
 */
function parseRequestCall(index: number = 0): {
  method: string
  pathname: string
  search: URLSearchParams
  body: unknown
} {
  expect(requestMock.mock.calls.length).toBeGreaterThan(index)
  const [method, path, options] = requestMock.mock.calls[index]
  const [pathname = '', search = ''] = path.split('?')
  return { method, pathname, search: new URLSearchParams(search), body: options?.body }
}

beforeEach(() => {
  requestMock.mockReset()
})

describe('图谱查询', () => {
  it('getMemoryGraph 默认以 limit=120 GET 图谱并原样返回载荷', async () => {
    const response = { success: true, nodes: [], edges: [], total_nodes: 0, total_edges: 0 }
    requestMock.mockResolvedValue(response)

    await expect(getMemoryGraph()).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/graph?limit=120`, { body: undefined })
  })

  it('getMemoryGraph 透传自定义 limit', async () => {
    requestMock.mockResolvedValue({ success: true, nodes: [], edges: [], total_nodes: 0, total_edges: 0 })

    await getMemoryGraph(30)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/graph?limit=30`, { body: undefined })
  })

  it('getMemoryGraphSearch 对查询词做 URL 编码且默认 limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, query: '', limit: 50, count: 0, items: [] })

    await getMemoryGraphSearch('麦麦 测试')

    const call = parseRequestCall()
    expect(call.method).toBe('GET')
    expect(call.pathname).toBe(`${BASE}/graph/search`)
    expect(call.search.get('query')).toBe('麦麦 测试')
    expect(call.search.get('limit')).toBe('50')
  })

  it('getMemoryGraphNodeDetail 默认关系/段落/证据图上限为 20/20/80', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryGraphNodeDetail('实体A')

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/graph/node-detail`)
    expect(Object.fromEntries(call.search.entries())).toEqual({
      node_id: '实体A',
      relation_limit: '20',
      paragraph_limit: '20',
      evidence_node_limit: '80',
    })
  })

  it('getMemoryGraphNodeDetail 透传自定义上限', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryGraphNodeDetail('n1', { relationLimit: 5, paragraphLimit: 6, evidenceNodeLimit: 7 })

    const call = parseRequestCall()
    expect(call.search.get('relation_limit')).toBe('5')
    expect(call.search.get('paragraph_limit')).toBe('6')
    expect(call.search.get('evidence_node_limit')).toBe('7')
  })

  it('getMemoryGraphEdgeDetail 携带 source/target 与默认上限', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryGraphEdgeDetail('张三', '李四')

    const call = parseRequestCall()
    expect(call.method).toBe('GET')
    expect(call.pathname).toBe(`${BASE}/graph/edge-detail`)
    expect(Object.fromEntries(call.search.entries())).toEqual({
      source: '张三',
      target: '李四',
      paragraph_limit: '20',
      evidence_node_limit: '80',
    })
  })

  it('getMemoryGraphParagraphDetail 携带段落哈希与默认证据图上限', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryGraphParagraphDetail('hash-01')

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/graph/paragraph-detail`)
    expect(call.search.get('paragraph_hash')).toBe('hash-01')
    expect(call.search.get('evidence_node_limit')).toBe('80')
  })

  it('后端异常时向上抛出 ApiError', async () => {
    requestMock.mockRejectedValue(new ApiError('获取记忆图谱失败', { status: 502 }))

    await expect(getMemoryGraph()).rejects.toBeInstanceOf(ApiError)
    await expect(getMemoryGraph()).rejects.toMatchObject({ status: 502 })
  })
})

describe('记忆删除', () => {
  it('previewMemoryDelete 以 POST 提交删除请求体到预览接口', async () => {
    const response = { success: true, mode: 'source', selector: 's', counts: {}, sources: [], items: [], item_count: 0 }
    requestMock.mockResolvedValue(response)
    const payload = { mode: 'source', selector: { source: 'doc.txt' }, reason: '误导入' }

    await expect(previewMemoryDelete(payload)).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/delete/preview`, { body: payload })
  })

  it('executeMemoryDelete 以 POST 提交删除请求体到执行接口', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { mode: 'entity', selector: '张三' }

    await executeMemoryDelete(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/delete/execute`, { body: payload })
  })

  it('executeMemoryDelete 后端 500 时向上抛出 ApiError', async () => {
    requestMock.mockRejectedValue(new ApiError('删除失败', { status: 500 }))

    await expect(executeMemoryDelete({ mode: 'entity', selector: 'x' })).rejects.toMatchObject({ status: 500 })
  })

  it('restoreMemoryDelete 以 POST 提交恢复请求体', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { operation_id: 'op-1', reason: '恢复误删' }

    await restoreMemoryDelete(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/delete/restore`, { body: payload })
  })

  it('getMemoryDeleteOperations 默认 limit=20 且不携带 mode', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryDeleteOperations()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/delete/operations?limit=20`, { body: undefined })
  })

  it('getMemoryDeleteOperations 空白 mode 不追加参数，非空 mode 追加', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryDeleteOperations(10, '   ')
    expect(parseRequestCall(0).search.has('mode')).toBe(false)

    await getMemoryDeleteOperations(10, 'source')
    const call = parseRequestCall(1)
    expect(call.search.get('mode')).toBe('source')
    expect(call.search.get('limit')).toBe('10')
  })

  it('getMemoryDeleteOperation 对操作 ID 做 URL 编码', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryDeleteOperation('op/01')
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/delete/operations/op%2F01`, { body: undefined })
  })
})

describe('记忆修正', () => {
  it('previewMemoryCorrection 显式传入 undefined limit 时会从请求体中剔除该键', async () => {
    requestMock.mockResolvedValue({ success: true })

    await previewMemoryCorrection({ request_text: '张三不再喜欢喝咖啡', limit: undefined })

    const body = parseRequestCall().body as Record<string, unknown>
    expect(body.request_text).toBe('张三不再喜欢喝咖啡')
    expect(body).not.toHaveProperty('limit')
  })

  it('previewMemoryCorrection 带 limit 时原样保留', async () => {
    requestMock.mockResolvedValue({ success: true })

    await previewMemoryCorrection({ request_text: '修正', limit: 8, scope: 'memory' })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/corrections/preview`, {
      body: { request_text: '修正', limit: 8, scope: 'memory' },
    })
  })

  it('executeMemoryCorrection 以 POST 提交执行请求体', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { plan_id: 'plan-1', confirmed: true }

    await executeMemoryCorrection(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/corrections/execute`, { body: payload })
  })

  it('getMemoryCorrectionPlans 默认仅携带 limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryCorrectionPlans()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/corrections/plans?limit=50`, { body: undefined })
  })

  it('getMemoryCorrectionPlans 对 status/scope 去除首尾空白后追加，全空白则跳过', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryCorrectionPlans({ limit: 5, status: ' executed ', scope: '   ' })

    const call = parseRequestCall()
    expect(call.search.get('limit')).toBe('5')
    expect(call.search.get('status')).toBe('executed')
    expect(call.search.has('scope')).toBe(false)
  })

  it('getMemoryCorrectionPlan 对计划 ID 做 URL 编码', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryCorrectionPlan('plan#1')
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/corrections/plans/plan%231`, { body: undefined })
  })

  it('rollbackMemoryCorrectionPlan 以 POST 提交回滚请求到编码后的路径', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { requested_by: 'webui', reason: '误操作' }

    await rollbackMemoryCorrectionPlan('plan/1', payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/corrections/plans/plan%2F1/rollback`, {
      body: payload,
    })
  })
})

describe('反馈修正', () => {
  it('getMemoryFeedbackCorrections 默认仅携带 limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryFeedbackCorrections()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/feedback-corrections?limit=50`, { body: undefined })
  })

  it('getMemoryFeedbackCorrections 把 rollbackStatus 映射为 rollback_status 并去除空白', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryFeedbackCorrections({
      limit: 10,
      status: 'completed',
      rollbackStatus: ' rolled_back ',
      query: ' 咖啡 ',
    })

    const call = parseRequestCall()
    expect(Object.fromEntries(call.search.entries())).toEqual({
      limit: '10',
      status: 'completed',
      rollback_status: 'rolled_back',
      query: '咖啡',
    })
  })

  it('getMemoryFeedbackCorrection 按任务 ID 拼接路径', async () => {
    const response = { success: true, task: null }
    requestMock.mockResolvedValue(response)

    await expect(getMemoryFeedbackCorrection(42)).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/feedback-corrections/42`, { body: undefined })
  })

  it('rollbackMemoryFeedbackCorrection 以 POST 提交回滚请求体', async () => {
    requestMock.mockResolvedValue({ success: true })

    await rollbackMemoryFeedbackCorrection(42, { requested_by: 'webui', reason: '误判' })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/feedback-corrections/42/rollback`, {
      body: { requested_by: 'webui', reason: '误判' },
    })
  })
})

describe('来源与时间线', () => {
  it('getMemorySources 以 GET 读取来源列表', async () => {
    const response = { success: true, items: [], count: 0 }
    requestMock.mockResolvedValue(response)

    await expect(getMemorySources()).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/sources`, { body: undefined })
  })

  it('getMemoryTimeline 默认仅携带 chat_id 与 limit=100', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryTimeline({ chatId: 'chat-1' })

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/timeline`)
    expect(Object.fromEntries(call.search.entries())).toEqual({ chat_id: 'chat-1', limit: '100' })
  })

  it('getMemoryTimeline 追加时间范围并清洗 types（去空白、丢弃空项）', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryTimeline({
      chatId: 'chat-1',
      timeStart: 1700000000,
      timeEnd: 1700003600,
      types: [' paragraph ', '  ', 'episode'],
      limit: 30,
    })

    const call = parseRequestCall()
    expect(call.search.get('time_start')).toBe('1700000000')
    expect(call.search.get('time_end')).toBe('1700003600')
    expect(call.search.get('types')).toBe('paragraph,episode')
    expect(call.search.get('limit')).toBe('30')
  })

  it('getMemoryTimeline types 全为空白时不追加 types 参数', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryTimeline({ chatId: 'chat-1', types: ['  ', ''] })
    expect(parseRequestCall().search.has('types')).toBe(false)
  })
})

describe('情景记忆', () => {
  it('getMemoryEpisodes 默认参数全部以空字符串占位且 limit=20', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryEpisodes()

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/episodes`)
    expect(Object.fromEntries(call.search.entries())).toEqual({
      query: '',
      limit: '20',
      source: '',
      person_id: '',
      platform: '',
      user_id: '',
    })
  })

  it('getMemoryEpisodes 有时间范围时追加 time_start/time_end', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryEpisodes({ query: '旅行', personId: 'p1', timeStart: 100, timeEnd: 200 })

    const call = parseRequestCall()
    expect(call.search.get('query')).toBe('旅行')
    expect(call.search.get('person_id')).toBe('p1')
    expect(call.search.get('time_start')).toBe('100')
    expect(call.search.get('time_end')).toBe('200')
  })

  it('getMemoryEpisode 对情景 ID 做 URL 编码', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryEpisode('ep 01')
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/episodes/ep%2001`, { body: undefined })
  })

  it('rebuildMemoryEpisodes 以 POST 提交重建范围', async () => {
    requestMock.mockResolvedValue({ success: true })

    await rebuildMemoryEpisodes({ all: true })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/episodes/rebuild`, { body: { all: true } })
  })

  it('getMemoryEpisodeStatus 默认 limit=20', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryEpisodeStatus()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/episodes/status?limit=20`, { body: undefined })
  })

  it('processMemoryEpisodePending 以 POST 提交处理参数', async () => {
    requestMock.mockResolvedValue({ success: true })

    await processMemoryEpisodePending({ limit: 5, max_retry: 2 })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/episodes/process-pending`, {
      body: { limit: 5, max_retry: 2 },
    })
  })
})

describe('人物画像', () => {
  it('getMemoryProfiles 默认 limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryProfiles()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/profiles?limit=50`, { body: undefined })
  })

  it('searchMemoryProfiles 把驼峰选项映射为下划线参数并以空串补位', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await searchMemoryProfiles({ personKeyword: '麦麦', platform: 'qq' })

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/profiles/search`)
    expect(Object.fromEntries(call.search.entries())).toEqual({
      person_id: '',
      person_keyword: '麦麦',
      platform: 'qq',
      user_id: '',
      limit: '50',
    })
  })

  it('queryMemoryProfile 默认 limit=12 且 force_refresh=false', async () => {
    requestMock.mockResolvedValue({ success: true })

    await queryMemoryProfile({ personId: 'p1' })

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/profiles/query`)
    expect(call.search.get('person_id')).toBe('p1')
    expect(call.search.get('limit')).toBe('12')
    expect(call.search.get('force_refresh')).toBe('false')
  })

  it('queryMemoryProfile forceRefresh=true 时序列化为字符串 true', async () => {
    requestMock.mockResolvedValue({ success: true })

    await queryMemoryProfile({ personKeyword: '张三', forceRefresh: true })
    expect(parseRequestCall().search.get('force_refresh')).toBe('true')
  })

  it('setMemoryProfileOverride 以 POST 提交人工覆写内容', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { person_id: 'p1', override_text: '他是素食主义者', updated_by: 'webui' }

    await setMemoryProfileOverride(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/profiles/override`, { body: payload })
  })

  it('deleteMemoryProfileOverride 以 DELETE 删除编码后的人物覆写', async () => {
    requestMock.mockResolvedValue({ success: true, deleted: true })

    await deleteMemoryProfileOverride('p/1')
    expect(requestMock).toHaveBeenCalledWith('DELETE', `${BASE}/profiles/override/p%2F1`, { body: undefined })
  })

  it('getMemoryProfileEvidence 路径编码 personId 且默认 limit=12、force_refresh=false', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryProfileEvidence({ personId: 'p 1' })

    const call = parseRequestCall()
    expect(call.pathname).toBe(`${BASE}/profiles/p%201/evidence`)
    expect(Object.fromEntries(call.search.entries())).toEqual({ limit: '12', force_refresh: 'false' })
  })

  it('correctMemoryProfileEvidence 缺省字段回填默认值且请求体不含 person_id', async () => {
    requestMock.mockResolvedValue({ success: true })

    await correctMemoryProfileEvidence({ person_id: 'p1', evidence_type: 'paragraph', hash: 'h1' })

    const call = parseRequestCall()
    expect(call.method).toBe('POST')
    expect(call.pathname).toBe(`${BASE}/profiles/p1/evidence/correct`)
    expect(call.body).toEqual({
      evidence_type: 'paragraph',
      hash: 'h1',
      requested_by: 'knowledge_base',
      reason: 'profile_evidence_correction',
      refresh: true,
      limit: 12,
    })
    expect(call.body).not.toHaveProperty('person_id')
  })

  it('correctMemoryProfileEvidence 显式字段覆盖默认值', async () => {
    requestMock.mockResolvedValue({ success: true })

    await correctMemoryProfileEvidence({
      person_id: 'p1',
      evidence_type: 'relation',
      hash: 'h2',
      requested_by: 'webui',
      reason: '证据有误',
      refresh: false,
      limit: 3,
    })

    expect(parseRequestCall().body).toEqual({
      evidence_type: 'relation',
      hash: 'h2',
      requested_by: 'webui',
      reason: '证据有误',
      refresh: false,
      limit: 3,
    })
  })
})

describe('记忆维护', () => {
  it('getMemoryRecycleBin 默认 limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryRecycleBin()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/maintenance/recycle-bin?limit=50`, {
      body: undefined,
    })
  })

  const maintenanceCases: Array<{
    name: string
    fn: (target: string) => Promise<unknown>
    path: string
  }> = [
    { name: 'restoreMaintainedMemory', fn: restoreMaintainedMemory, path: `${BASE}/maintenance/restore` },
    { name: 'reinforceMemory', fn: reinforceMemory, path: `${BASE}/maintenance/reinforce` },
    { name: 'freezeMemory', fn: freezeMemory, path: `${BASE}/maintenance/freeze` },
  ]

  it.each(maintenanceCases)('$name 以 POST 提交 target 到 $path', async ({ fn, path }) => {
    requestMock.mockResolvedValue({ success: true })

    await fn('relation:hash-1')
    expect(requestMock).toHaveBeenCalledWith('POST', path, { body: { target: 'relation:hash-1' } })
  })

  it('protectMemory 未传 hours 时请求体不含 hours 键', async () => {
    requestMock.mockResolvedValue({ success: true })

    await protectMemory('t1')

    const call = parseRequestCall()
    expect(call.method).toBe('POST')
    expect(call.pathname).toBe(`${BASE}/maintenance/protect`)
    expect(call.body).toEqual({ target: 't1' })
    expect(call.body).not.toHaveProperty('hours')
  })

  it('protectMemory 传入 hours=0 时也会携带（不能被当作缺省丢弃）', async () => {
    requestMock.mockResolvedValue({ success: true })

    await protectMemory('t1', 0)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/maintenance/protect`, {
      body: { target: 't1', hours: 0 },
    })
  })
})

describe('运行时状态', () => {
  it('getMemoryRuntimeConfig 以 GET 读取运行时配置', async () => {
    const response = { success: true, runtime_ready: true }
    requestMock.mockResolvedValue(response)

    await expect(getMemoryRuntimeConfig()).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/runtime/config`, { body: undefined })
  })

  it('refreshMemoryRuntimeSelfCheck 以 POST 触发自检且不带请求体', async () => {
    requestMock.mockResolvedValue({ success: true })

    await refreshMemoryRuntimeSelfCheck()
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/runtime/self-check/refresh`, { body: undefined })
  })

  it('rebuildMemoryRuntimeVectors 缺省时提交空对象请求体', async () => {
    requestMock.mockResolvedValue({ success: true })

    await rebuildMemoryRuntimeVectors()
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/runtime/vectors/rebuild`, { body: {} })
  })

  it('rebuildMemoryRuntimeVectors 透传重建参数', async () => {
    requestMock.mockResolvedValue({ success: true })

    await rebuildMemoryRuntimeVectors({ dry_run: true, batch_size: 64, include_relations: null })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/runtime/vectors/rebuild`, {
      body: { dry_run: true, batch_size: 64, include_relations: null },
    })
  })
})

describe('记忆配置', () => {
  it('getMemoryConfigSchema / getMemoryConfig / getMemoryConfigRaw 分别 GET 对应路径', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryConfigSchema()
    await getMemoryConfig()
    await getMemoryConfigRaw()

    expect(requestMock).toHaveBeenNthCalledWith(1, 'GET', `${BASE}/config/schema`, { body: undefined })
    expect(requestMock).toHaveBeenNthCalledWith(2, 'GET', `${BASE}/config`, { body: undefined })
    expect(requestMock).toHaveBeenNthCalledWith(3, 'GET', `${BASE}/config/raw`, { body: undefined })
  })

  it('updateMemoryConfig 以 PUT 提交包装后的 config 对象', async () => {
    requestMock.mockResolvedValue({ success: true })

    await updateMemoryConfig({ auto_save: true })
    expect(requestMock).toHaveBeenCalledWith('PUT', `${BASE}/config`, { body: { config: { auto_save: true } } })
  })

  it('updateMemoryConfigRaw 以 PUT 提交原文字符串', async () => {
    requestMock.mockResolvedValue({ success: true })

    await updateMemoryConfigRaw('auto_save = true')
    expect(requestMock).toHaveBeenCalledWith('PUT', `${BASE}/config/raw`, {
      body: { config: 'auto_save = true' },
    })
  })

  it('updateMemoryConfigRaw 配置非法时向上抛出 ApiError', async () => {
    requestMock.mockRejectedValue(new ApiError('配置格式错误', { status: 400 }))

    await expect(updateMemoryConfigRaw('===')).rejects.toBeInstanceOf(ApiError)
    await expect(updateMemoryConfigRaw('===')).rejects.toMatchObject({ status: 400 })
  })
})

describe('记忆导入', () => {
  const importMetaCases: Array<{ name: string; fn: () => Promise<unknown>; path: string }> = [
    { name: 'getMemoryImportGuide', fn: getMemoryImportGuide, path: `${BASE}/import/guide` },
    { name: 'getMemoryImportSettings', fn: getMemoryImportSettings, path: `${BASE}/import/settings` },
    { name: 'getMemoryImportPathAliases', fn: getMemoryImportPathAliases, path: `${BASE}/import/path-aliases` },
    { name: 'getMemoryImportChatTargets', fn: getMemoryImportChatTargets, path: `${BASE}/import/chat-targets` },
  ]

  it.each(importMetaCases)('$name 以 GET 读取 $path', async ({ fn, path }) => {
    const response = { success: true }
    requestMock.mockResolvedValue(response)

    await expect(fn()).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', path, { body: undefined })
  })

  it('resolveMemoryImportPath 以 POST 提交路径解析请求', async () => {
    requestMock.mockResolvedValue({ alias: 'data', relative_path: 'a.txt', resolved_path: '/data/a.txt', exists: true, is_file: true, is_dir: false })
    const payload = { alias: 'data', relative_path: 'a.txt', must_exist: true }

    await resolveMemoryImportPath(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/import/resolve-path`, { body: payload })
  })

  it('getMemoryImportTasks 默认 limit=20', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryImportTasks()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/import/tasks?limit=20`, { body: undefined })
  })

  it('getMemoryImportTask 编码任务 ID 且默认不含分块', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryImportTask('task 1')
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/import/tasks/task%201?include_chunks=false`, {
      body: undefined,
    })
  })

  it('getMemoryImportTask includeChunks=true 时序列化为字符串 true', async () => {
    requestMock.mockResolvedValue({ success: true })

    await getMemoryImportTask('t1', true)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/import/tasks/t1?include_chunks=true`, {
      body: undefined,
    })
  })

  it('getMemoryImportTaskChunks 编码任务/文件 ID 且默认 offset=0、limit=50', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryImportTaskChunks('t1', 'f/1')
    expect(requestMock).toHaveBeenCalledWith(
      'GET',
      `${BASE}/import/tasks/t1/chunks/f%2F1?offset=0&limit=50`,
      { body: undefined },
    )
  })

  it('createMemoryUploadImport 以 FormData 携带全部文件与 payload_json', async () => {
    requestMock.mockResolvedValue({ success: true })
    const files = [
      new File(['第一段'], 'a.txt', { type: 'text/plain' }),
      new File(['第二段'], 'b.txt', { type: 'text/plain' }),
    ]
    const payload = { input_mode: 'text', file_concurrency: 2 }

    await createMemoryUploadImport(files, payload)

    const call = parseRequestCall()
    expect(call.method).toBe('POST')
    expect(call.pathname).toBe(`${BASE}/import/upload`)
    expect(call.body).toBeInstanceOf(FormData)
    const form = call.body as FormData
    const uploaded = form.getAll('files')
    expect(uploaded).toHaveLength(2)
    expect((uploaded[0] as File).name).toBe('a.txt')
    expect((uploaded[1] as File).name).toBe('b.txt')
    expect(JSON.parse(String(form.get('payload_json')))).toEqual(payload)
  })

  it('createMemoryUploadImport 文件超限时向上抛出 ApiError', async () => {
    requestMock.mockRejectedValue(new ApiError('文件过大', { status: 413 }))

    await expect(createMemoryUploadImport([], {})).rejects.toMatchObject({ status: 413 })
  })

  const importCreateCases: Array<{
    name: string
    fn: (payload: Record<string, unknown>) => Promise<unknown>
    path: string
  }> = [
    { name: 'createMemoryPasteImport', fn: createMemoryPasteImport, path: `${BASE}/import/paste` },
    { name: 'createMemoryRawScanImport', fn: createMemoryRawScanImport, path: `${BASE}/import/raw-scan` },
    { name: 'createMemoryLpmmOpenieImport', fn: createMemoryLpmmOpenieImport, path: `${BASE}/import/lpmm-openie` },
    { name: 'createMemoryLpmmConvertImport', fn: createMemoryLpmmConvertImport, path: `${BASE}/import/lpmm-convert` },
    { name: 'createMemoryTemporalBackfillImport', fn: createMemoryTemporalBackfillImport, path: `${BASE}/import/temporal-backfill` },
    { name: 'createMemoryMaibotMigrationImport', fn: createMemoryMaibotMigrationImport, path: `${BASE}/import/maibot-migration` },
  ]

  it.each(importCreateCases)('$name 以 POST 提交 payload 到 $path', async ({ fn, path }) => {
    const response = { success: true, task: { task_id: 't1' } }
    requestMock.mockResolvedValue(response)
    const payload = { chat_id: 'chat-1', text: '导入内容' }

    await expect(fn(payload)).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('POST', path, { body: payload })
  })

  it('cancelMemoryImportTask 以 POST 请求编码后的取消路径且不带请求体', async () => {
    requestMock.mockResolvedValue({ success: true })

    await cancelMemoryImportTask('t#1')
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/import/tasks/t%231/cancel`, { body: undefined })
  })

  it('retryMemoryImportTask 缺省时提交空对象，带 overrides 时原样透传', async () => {
    requestMock.mockResolvedValue({ success: true })

    await retryMemoryImportTask('t1')
    expect(requestMock).toHaveBeenNthCalledWith(1, 'POST', `${BASE}/import/tasks/t1/retry`, { body: {} })

    await retryMemoryImportTask('t1', { overrides: { chunk_concurrency: 4 } })
    expect(requestMock).toHaveBeenNthCalledWith(2, 'POST', `${BASE}/import/tasks/t1/retry`, {
      body: { overrides: { chunk_concurrency: 4 } },
    })
  })
})

describe('检索调参', () => {
  it('getMemoryTuningProfile 以 GET 读取调参画像', async () => {
    const response = { success: true, profile: {} }
    requestMock.mockResolvedValue(response)

    await expect(getMemoryTuningProfile()).resolves.toBe(response)
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/retrieval_tuning/profile`, { body: undefined })
  })

  it('getMemoryTuningTasks 默认 limit=20', async () => {
    requestMock.mockResolvedValue({ success: true, items: [] })

    await getMemoryTuningTasks()
    expect(requestMock).toHaveBeenCalledWith('GET', `${BASE}/retrieval_tuning/tasks?limit=20`, {
      body: undefined,
    })
  })

  it('createMemoryTuningTask 以 POST 提交任务参数', async () => {
    requestMock.mockResolvedValue({ success: true })
    const payload = { mode: 'quick', sample_size: 20 }

    await createMemoryTuningTask(payload)
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/retrieval_tuning/tasks`, { body: payload })
  })

  it('applyBestMemoryTuningProfile 缺省时提交空对象到编码后的路径', async () => {
    requestMock.mockResolvedValue({ success: true })

    await applyBestMemoryTuningProfile('task/9')
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/retrieval_tuning/tasks/task%2F9/apply-best`, {
      body: {},
    })
  })

  it('applyBestMemoryTuningProfile 透传 persist/validate 参数', async () => {
    requestMock.mockResolvedValue({ success: true })

    await applyBestMemoryTuningProfile('t1', { persist: true, validate: false })
    expect(requestMock).toHaveBeenCalledWith('POST', `${BASE}/retrieval_tuning/tasks/t1/apply-best`, {
      body: { persist: true, validate: false },
    })
  })

  it('getMemoryTuningReport 默认 format=md，可切换为 json', async () => {
    requestMock.mockResolvedValue({ success: true, content: '# 报告', path: '/tmp/r.md' })

    await getMemoryTuningReport('t1')
    expect(requestMock).toHaveBeenNthCalledWith(1, 'GET', `${BASE}/retrieval_tuning/tasks/t1/report?format=md`, {
      body: undefined,
    })

    await getMemoryTuningReport('t 1', 'json')
    expect(requestMock).toHaveBeenNthCalledWith(
      2,
      'GET',
      `${BASE}/retrieval_tuning/tasks/t%201/report?format=json`,
      { body: undefined },
    )
  })
})
