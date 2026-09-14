import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MEMORY_CORRECTION_FETCH_LIMIT } from '../../constants'
import { useMemoryCorrection } from '../useMemoryCorrection'
import type { UseMemoryCorrectionOptions } from '../useMemoryCorrection'

import type {
  MemoryCorrectionPlanPayload,
  MemoryRuntimeConfigPayload,
} from '@/lib/memory-api'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock }),
}))

vi.mock('@/lib/memory-api', () => ({
  getMemoryImportChatTargets: vi.fn(),
  getMemoryCorrectionPlans: vi.fn(),
  getMemoryCorrectionPlan: vi.fn(),
  previewMemoryCorrection: vi.fn(),
  executeMemoryCorrection: vi.fn(),
  rollbackMemoryCorrectionPlan: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function makePlan(
  overrides: Partial<MemoryCorrectionPlanPayload> = {},
): MemoryCorrectionPlanPayload {
  return {
    plan_id: 'plan-1',
    request_text: '把常住城市改为杭州',
    scope: 'person_profile',
    target_person_id: 'person-1',
    target_chat_id: 'chat-1',
    status: 'awaiting_confirmation',
    confidence: 0.91,
    plan: {
      scope: 'person_profile',
      request_text: '把常住城市改为杭州',
      person_id: 'person-1',
      chat_id: 'chat-1',
      confidence: 0.91,
      risk_level: 'medium',
      reason: 'plan-reason',
      operations: [],
    },
    preview: {
      request_text: '把常住城市改为杭州',
      scope: 'person_profile',
      person_id: 'person-1',
      person_keyword: '测试用户',
      chat_id: 'chat-1',
      candidates: [],
      operations: [],
      requires_confirmation: true,
      confirm_threshold: 0.75,
      reason: 'preview-reason',
    },
    execution: {},
    created_at: 1,
    updated_at: 2,
    requested_by: 'knowledge_base',
    reason: 'top-reason',
    ...overrides,
  }
}

function makeRuntime(
  overrides: Partial<MemoryRuntimeConfigPayload> = {},
): MemoryRuntimeConfigPayload {
  return {
    success: true,
    config: {},
    data_dir: 'data',
    embedding_dimension: 8,
    auto_save: true,
    relation_vectors_enabled: false,
    runtime_ready: true,
    embedding_degraded: false,
    embedding_degraded_reason: '',
    paragraph_vector_backfill_pending: 0,
    paragraph_vector_backfill_running: 0,
    paragraph_vector_backfill_failed: 0,
    paragraph_vector_backfill_done: 0,
    ...overrides,
  }
}

function renderCorrection(options: Partial<UseMemoryCorrectionOptions> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const { active = true, ...rest } = options
  return {
    queryClient,
    ...renderHook((props: UseMemoryCorrectionOptions) => useMemoryCorrection(props), {
      wrapper,
      initialProps: { ...rest, active },
    }),
  }
}

async function waitForSelectedPlan(
  result: {
    current: {
      selectedPlanId: string
      selectedPlanLoading: boolean
    }
  },
  planId: string,
) {
  await waitFor(() => {
    expect(result.current.selectedPlanId).toBe(planId)
    expect(result.current.selectedPlanLoading).toBe(false)
  })
}

beforeEach(() => {
  toastMock.mockReset()

  vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({
    success: true,
    data: [{ chat_id: 'chat-1', chat_name: '测试群', is_group: true }],
  })
  vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
    success: true,
    items: [makePlan()],
    count: 1,
  })
  vi.mocked(memoryApi.getMemoryCorrectionPlan).mockImplementation(async (planId) => ({
    success: true,
    plan: makePlan({ plan_id: planId }),
  }))
  vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
    success: true,
    plan_id: 'plan-preview',
    preview: makePlan({ plan_id: 'plan-preview' }).preview,
  })
  vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
    success: true,
    plan: makePlan({ status: 'executed' }),
  })
  vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
    success: true,
    plan: makePlan({ status: 'rolled_back' }),
  })
})

afterEach(() => {
  cleanup()
})

describe('useMemoryCorrection 主路径', () => {
  it('激活后拉计划列表、自动选中第一项并加载详情', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')

    expect(memoryApi.getMemoryCorrectionPlans).toHaveBeenCalledWith({
      limit: MEMORY_CORRECTION_FETCH_LIMIT,
    })
    expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('plan-1')
    expect(result.current.selectedPlan?.plan_id).toBe('plan-1')
    expect(result.current.selectedPreview?.reason).toBe('preview-reason')
    expect(result.current.chatTargets).toEqual([
      { chat_id: 'chat-1', chat_name: '测试群', is_group: true },
    ])
  })

  it('非激活时不拉计划列表和聊天流', async () => {
    renderCorrection({ active: false })
    await act(async () => {
      await Promise.resolve()
    })
    expect(memoryApi.getMemoryCorrectionPlans).not.toHaveBeenCalled()
    expect(memoryApi.getMemoryImportChatTargets).not.toHaveBeenCalled()
  })

  it('缺少修正内容或人物定位时不发预览请求', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')

    await act(async () => {
      await result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '缺少修正内容' }),
    )

    toastMock.mockClear()
    act(() => result.current.setRequestText('  改城市  '))
    await act(async () => {
      await result.current.submitPreview()
    })
    expect(memoryApi.previewMemoryCorrection).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '缺少人物定位信息' }),
    )
  })

  it('预览成功后选中新计划并刷新详情', async () => {
    const itemsRef = { current: [makePlan({ plan_id: 'plan-1' })] }
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockImplementation(async () => ({
      success: true,
      items: itemsRef.current,
      count: itemsRef.current.length,
    }))
    vi.mocked(memoryApi.previewMemoryCorrection).mockImplementation(async () => {
      itemsRef.current = [makePlan({ plan_id: 'plan-new' })]
      return {
        success: true,
        plan_id: 'plan-new',
        preview: {
          ...makePlan().preview,
          reason: '新预览',
        },
      }
    })

    const { result } = renderCorrection({
      runtimeConfig: makeRuntime({ fuzzy_modify_candidate_limit: 12 }),
    })
    await waitForSelectedPlan(result, 'plan-1')
    await waitFor(() => expect(result.current.candidateLimitMax).toBe(12))

    act(() => {
      result.current.setRequestText('  改成杭州  ')
      result.current.setPersonKeyword(' 测试用户 ')
      result.current.setChatId(' chat-9 ')
      result.current.setCorrectionReason(' 人工确认 ')
      result.current.setCandidateLimit('99')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(memoryApi.previewMemoryCorrection).toHaveBeenCalledWith({
      request_text: '改成杭州',
      scope: 'person_profile',
      person_id: undefined,
      person_keyword: '测试用户',
      chat_id: 'chat-9',
      limit: 12,
      requested_by: 'knowledge_base',
      reason: '人工确认',
    })
    await waitFor(() => expect(result.current.selectedPlanId).toBe('plan-new'))
    expect(result.current.planSearch).toBe('plan-new')
    expect(result.current.selectedPreview?.reason).toBe('新预览')
    expect(result.current.previewing).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '已生成记忆修正预览',
        description: '计划 plan-new 等待确认',
      }),
    )
    expect(memoryApi.getMemoryCorrectionPlan).toHaveBeenCalledWith('plan-new')
  })

  it('executePlan 成功时带上确认载荷、更新详情并失效记录查询', async () => {
    const onSourcesChanged = vi.fn()
    const onRuntimeChanged = vi.fn()
    const { queryClient, result } = renderCorrection({ onSourcesChanged, onRuntimeChanged })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    await waitForSelectedPlan(result, 'plan-1')
    act(() => result.current.setCorrectionReason(' 执行原因 '))

    await act(async () => {
      await result.current.executePlan()
    })

    expect(memoryApi.executeMemoryCorrection).toHaveBeenCalledWith({
      plan_id: 'plan-1',
      confirmed: true,
      requested_by: 'knowledge_base',
      reason: '执行原因',
    })
    expect(result.current.selectedPlan?.status).toBe('executed')
    expect(onSourcesChanged).toHaveBeenCalled()
    expect(onRuntimeChanged).toHaveBeenCalled()
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['memory-records'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['memory-record-context'] })
    expect(result.current.executingPlanId).toBe('')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '记忆修正已执行',
        description: '计划 plan-1 已写入执行结果',
      }),
    )
  })

  it('rollbackPlan 成功时写入回滚载荷并刷新来源/运行时', async () => {
    const onSourcesChanged = vi.fn()
    const onRuntimeChanged = vi.fn()
    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: true,
      plan: makePlan({ plan_id: 'plan-1', status: 'rolled_back' }),
    })

    const { result } = renderCorrection({ onSourcesChanged, onRuntimeChanged })
    await waitForSelectedPlan(result, 'plan-1')
    act(() => result.current.setCorrectionReason(' 回滚原因 '))

    await act(async () => {
      await result.current.rollbackPlan()
    })

    expect(memoryApi.rollbackMemoryCorrectionPlan).toHaveBeenCalledWith('plan-1', {
      requested_by: 'knowledge_base',
      reason: '回滚原因',
    })
    expect(result.current.selectedPlan?.status).toBe('rolled_back')
    expect(onSourcesChanged).toHaveBeenCalled()
    expect(onRuntimeChanged).toHaveBeenCalled()
    expect(result.current.rollingBackPlanId).toBe('')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '记忆修正已回滚',
        description: '计划 plan-1 的回滚结果已写入日志',
      }),
    )
  })

  it('空白 plan id 时执行和回滚都是空操作', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockResolvedValue({
      success: true,
      items: [],
    })
    const { result } = renderCorrection({ initialPlanId: '' })
    await waitFor(() => expect(result.current.selectedPlan).toBeNull())

    await act(async () => {
      await result.current.executePlan('   ')
      await result.current.rollbackPlan('   ')
    })
    expect(memoryApi.executeMemoryCorrection).not.toHaveBeenCalled()
    expect(memoryApi.rollbackMemoryCorrectionPlan).not.toHaveBeenCalled()
  })
})

describe('useMemoryCorrection 失败路径', () => {
  it('计划列表/聊天流查询失败分别写入局部错误文案', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlans).mockRejectedValue(new Error('计划接口挂了'))
    vi.mocked(memoryApi.getMemoryImportChatTargets).mockRejectedValue('bad')
    const { result } = renderCorrection()

    await waitFor(() => expect(result.current.correctionErrorText).toBe('计划接口挂了'))
    await waitFor(() => expect(result.current.chatTargetsErrorText).toBe('加载聊天流列表失败'))
  })

  it('详情 success=false 与抛错分别写入错误文案', async () => {
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockResolvedValue({
      success: false,
      error: '',
    })
    const missing = renderCorrection()
    await waitFor(() =>
      expect(missing.result.current.selectedPlanError).toBe('未能加载记忆修正计划详情'),
    )
    missing.unmount()

    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('详情超时'))
    const failed = renderCorrection()
    await waitFor(() => expect(failed.result.current.selectedPlanError).toBe('详情超时'))
  })

  it('预览 success=false 仍写入 previewPayload，并弹出失败 toast', async () => {
    vi.mocked(memoryApi.previewMemoryCorrection).mockResolvedValue({
      success: false,
      error: '',
    })
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    act(() => {
      result.current.setRequestText('改')
      result.current.setPersonId('p')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(result.current.previewPayload).toEqual({ success: false, error: '' })
    expect(result.current.previewing).toBe(false)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '生成记忆修正预览失败',
        description: '生成记忆修正预览失败',
        variant: 'destructive',
      }),
    )
  })

  it('预览已成功但详情同步失败时弹出同步告警', async () => {
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('同步超时'))
    act(() => {
      result.current.setRequestText('改')
      result.current.setPersonId('p')
    })
    await act(async () => {
      await result.current.submitPreview()
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '预览已生成，但界面同步失败',
        description: '同步超时',
        variant: 'destructive',
      }),
    )
  })

  it('执行接口失败、以及执行后界面同步失败分别弹对应 toast', async () => {
    const onSourcesChanged = vi.fn().mockRejectedValue(new Error('来源刷新失败'))
    const syncFail = renderCorrection({ onSourcesChanged })
    await waitForSelectedPlan(syncFail.result, 'plan-1')
    await act(async () => {
      await syncFail.result.current.executePlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行已完成，但界面同步未完全成功',
        description: '来源刷新失败',
      }),
    )
    syncFail.unmount()

    vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
      success: false,
      error: '',
    })
    const failed = renderCorrection()
    await waitForSelectedPlan(failed.result, 'plan-1')
    toastMock.mockClear()
    await act(async () => {
      await failed.result.current.executePlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行记忆修正失败',
        description: '执行记忆修正失败',
      }),
    )
    expect(failed.result.current.executingPlanId).toBe('')
  })

  it('执行未带回 plan 时补拉详情失败会告警', async () => {
    vi.mocked(memoryApi.executeMemoryCorrection).mockResolvedValue({
      success: true,
      plan: null,
    })
    const { result } = renderCorrection()
    await waitForSelectedPlan(result, 'plan-1')
    vi.mocked(memoryApi.getMemoryCorrectionPlan).mockRejectedValue(new Error('详情丢了'))
    await act(async () => {
      await result.current.executePlan('plan-1')
    })

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '执行已完成，但详情同步失败',
        description: '详情丢了',
      }),
    )
  })

  it('回滚接口失败、回滚后同步失败分别弹对应 toast', async () => {
    const onRuntimeChanged = vi.fn().mockRejectedValue(new Error('运行时刷新失败'))
    const syncFail = renderCorrection({ onRuntimeChanged })
    await waitForSelectedPlan(syncFail.result, 'plan-1')
    await act(async () => {
      await syncFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚已完成，但界面同步未完全成功',
        description: '运行时刷新失败',
      }),
    )
    syncFail.unmount()

    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockResolvedValue({
      success: false,
      error: '',
    })
    const apiFail = renderCorrection()
    await waitForSelectedPlan(apiFail.result, 'plan-1')
    toastMock.mockClear()
    await act(async () => {
      await apiFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚记忆修正失败',
        description: '回滚记忆修正失败',
      }),
    )

    vi.mocked(memoryApi.rollbackMemoryCorrectionPlan).mockRejectedValue('x')
    toastMock.mockClear()
    await act(async () => {
      await apiFail.result.current.rollbackPlan('plan-1')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '回滚记忆修正失败',
        description: '未知错误',
      }),
    )
  })
})
