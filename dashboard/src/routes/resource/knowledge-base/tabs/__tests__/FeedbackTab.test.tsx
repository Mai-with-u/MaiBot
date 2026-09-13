/**
 * FeedbackTab：用 mock hook 结果锁定搜索/筛选/空态/错误/回滚/分页 UI。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import type {
  MemoryFeedbackActionLogPayload,
  MemoryFeedbackCorrectionDetailTaskPayload,
  MemoryFeedbackCorrectionSummaryPayload,
} from '@/lib/memory-api'

import { FEEDBACK_ACTION_LOG_PAGE_SIZE, FEEDBACK_CORRECTION_PAGE_SIZE } from '../../constants'
import type { UseMemoryFeedbackResult } from '../../hooks/useMemoryFeedback'
import { buildFeedbackImpactSummary, getFeedbackCorrectionPreview } from '../../utils'
import { FeedbackTab } from '../FeedbackTab'

afterEach(() => {
  cleanup()
})

/** makeFeedback 里 setter 实际是 vi.fn()，但对外类型是 React Dispatch */
function pageUpdater(
  setter: UseMemoryFeedbackResult['setFeedbackPage'],
  callIndex: number,
): (current: number) => number {
  return (setter as unknown as Mock).mock.calls[callIndex][0] as (current: number) => number
}

beforeEach(() => {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

const OLD_TRIPLET = { subject: 'Alice', predicate: '认识', object: 'Bob' }
const NEW_TRIPLET = { subject: 'Alice', predicate: '是', object: '同事' }

function makeCorrection(
  overrides: Partial<MemoryFeedbackCorrectionDetailTaskPayload> = {},
): MemoryFeedbackCorrectionDetailTaskPayload {
  return {
    task_id: 11,
    query_tool_id: 'qt-color',
    session_id: 'session-alpha',
    query_text: '最喜欢的颜色是什么',
    query_timestamp: 1_710_000_010,
    task_status: 'applied',
    decision: 'correct',
    decision_confidence: 0.97,
    feedback_message_count: 2,
    rollback_status: 'none',
    affected_counts: {
      relations: 1,
      stale_paragraphs: 1,
      episode_sources: 2,
      profile_person_ids: 1,
      correction_paragraphs: 1,
      corrected_relations: 1,
    },
    created_at: 1_710_000_011,
    rollback_plan_summary: {
      forgotten_relations: [OLD_TRIPLET],
      corrected_write: { corrected_relations: [NEW_TRIPLET] },
    },
    query_snapshot: { query: '最喜欢的颜色是什么' },
    decision_payload: { decision: 'correct' },
    rollback_result: { restored: true },
    ...overrides,
  }
}

function makeLog(
  overrides: Partial<MemoryFeedbackActionLogPayload> = {},
): MemoryFeedbackActionLogPayload {
  return {
    id: 1,
    task_id: 11,
    query_tool_id: 'qt-color',
    action_type: 'forget_relation',
    target_hash: 'rel-old',
    reason: '用户明确纠正',
    before_payload: OLD_TRIPLET,
    after_payload: { hash: 'rel-old', active: false },
    created_at: 1_710_000_020,
    ...overrides,
  }
}

function selectedFields(item: MemoryFeedbackCorrectionDetailTaskPayload | null) {
  return {
    selectedFeedbackCorrection: item,
    selectedFeedbackResolved: item,
    selectedFeedbackPreview: getFeedbackCorrectionPreview(item),
    selectedFeedbackImpactSummary: buildFeedbackImpactSummary(item),
  }
}

function makeFeedback(overrides: Partial<UseMemoryFeedbackResult> = {}): UseMemoryFeedbackResult {
  return {
    feedbackSearch: '',
    setFeedbackSearch: vi.fn(),
    feedbackStatusFilter: 'all',
    setFeedbackStatusFilter: vi.fn(),
    feedbackRollbackFilter: 'all',
    setFeedbackRollbackFilter: vi.fn(),
    filteredFeedbackCorrections: [],
    feedbackCorrections: [],
    pagedFeedbackCorrections: [],
    feedbackPage: 1,
    setFeedbackPage: vi.fn(),
    feedbackPageCount: 1,
    selectedFeedbackCorrection: null,
    setSelectedFeedbackTaskId: vi.fn(),
    selectedFeedbackResolved: null,
    selectedFeedbackPreview: getFeedbackCorrectionPreview(null),
    selectedFeedbackImpactSummary: [],
    openFeedbackRollbackDialog: vi.fn(),
    feedbackRollingBack: false,
    selectedFeedbackTaskLoading: false,
    selectedFeedbackTaskError: null,
    feedbackActionLogPage: 1,
    setFeedbackActionLogPage: vi.fn(),
    feedbackActionLogPageCount: 1,
    feedbackActionLogSearch: '',
    setFeedbackActionLogSearch: vi.fn(),
    pagedFeedbackActionLogs: [],
    selectedFeedbackActionLogs: [],
    feedbackRollbackDialogOpen: false,
    setFeedbackRollbackDialogOpen: vi.fn(),
    feedbackRollbackReason: '',
    setFeedbackRollbackReason: vi.fn(),
    executeFeedbackRollback: vi.fn(async () => {}),
    feedbackErrorText: '',
    ...overrides,
  }
}

function renderFeedback(overrides: Partial<UseMemoryFeedbackResult> = {}) {
  const feedback = makeFeedback(overrides)
  const view = render(
    <Tabs defaultValue="feedback">
      <FeedbackTab feedback={feedback} />
    </Tabs>,
  )
  return { ...view, feedback }
}

describe('FeedbackTab', () => {
  it('空列表与未选详情展示占位，分页按钮保持禁用', () => {
    renderFeedback()

    expect(screen.getByText('当前筛选条件下没有纠错历史')).toBeInTheDocument()
    expect(screen.getByText('当前没有可查看的纠错详情')).toBeInTheDocument()
    expect(screen.getByText('当前命中 0 条记录，已加载最近 0 条')).toBeInTheDocument()
    expect(
      screen.getByText(`第 1 / 1 页，每页显示 ${FEEDBACK_CORRECTION_PAGE_SIZE} 条`),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })

  it('搜索与任务/回退状态筛选会回写 hook', async () => {
    const user = userEvent.setup()
    const { feedback } = renderFeedback({
      feedbackSearch: '颜色',
      feedbackStatusFilter: 'all',
      feedbackRollbackFilter: 'all',
    })

    fireEvent.change(screen.getByPlaceholderText('搜索查询编号 / 会话 / 查询内容 / 原因'), {
      target: { value: 'session-alpha' },
    })
    expect(feedback.setFeedbackSearch).toHaveBeenCalledWith('session-alpha')

    const comboboxes = screen.getAllByRole('combobox')
    await user.click(comboboxes[0])
    await user.click(screen.getByRole('option', { name: '已应用' }))
    expect(feedback.setFeedbackStatusFilter).toHaveBeenCalledWith('applied')

    await user.click(comboboxes[1])
    await user.click(screen.getByRole('option', { name: '已回退' }))
    expect(feedback.setFeedbackRollbackFilter).toHaveBeenCalledWith('rolled_back')
  })

  it('渲染纠错列表并选中记录，缺省字段走回退文案', async () => {
    const user = userEvent.setup()
    const applied = makeCorrection()
    const skipped = makeCorrection({
      task_id: 12,
      query_tool_id: 'qt-city',
      session_id: '',
      query_text: '',
      query_timestamp: undefined,
      created_at: 1_710_000_100,
      task_status: 'skipped',
      decision: '',
      rollback_status: '',
      affected_counts: {},
      rollback_plan_summary: {
        forgotten_relations: [],
        corrected_write: { corrected_relations: [NEW_TRIPLET] },
      },
    })
    const forgotten = makeCorrection({
      task_id: 13,
      query_tool_id: 'qt-forget',
      query_text: '忘掉旧关系',
      task_status: 'applied',
      rollback_plan_summary: {
        forgotten_relations: [OLD_TRIPLET],
        corrected_write: { corrected_relations: [] },
      },
      affected_counts: {},
    })
    const { feedback } = renderFeedback({
      feedbackCorrections: [applied, skipped, forgotten],
      filteredFeedbackCorrections: [applied, skipped, forgotten],
      pagedFeedbackCorrections: [applied, skipped, forgotten] as MemoryFeedbackCorrectionSummaryPayload[],
      ...selectedFields(applied),
    })

    expect(screen.getByText('当前命中 3 条记录，已加载最近 3 条')).toBeInTheDocument()
    expect(screen.getAllByText('已应用').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未回退').length).toBeGreaterThan(0)
    expect(screen.getAllByText('纠正').length).toBeGreaterThan(0)
    expect(
      screen.getAllByText('将“Alice -> 认识 -> Bob”纠正为“Alice -> 是 -> 同事”').length,
    ).toBeGreaterThan(0)
    expect(screen.getAllByText('Alice -> 认识 -> Bob').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Alice -> 是 -> 同事').length).toBeGreaterThan(0)
    expect(screen.getAllByText('影响关系 1 条').length).toBeGreaterThan(0)
    expect(screen.getAllByText('qt-color').length).toBeGreaterThan(0)

    expect(screen.getByText('已跳过')).toBeInTheDocument()
    expect(screen.getByText(/无查询文本/)).toBeInTheDocument()
    expect(screen.getByText('补充了新的纠错结论：“Alice -> 是 -> 同事”')).toBeInTheDocument()
    expect(screen.getByText('撤销了旧记忆关系：“Alice -> 认识 -> Bob”')).toBeInTheDocument()
    expect(screen.getAllByText('纠错后').some((node) => node.parentElement?.textContent?.includes('无'))).toBe(true)
    expect(screen.getAllByText('纠错前').length).toBeGreaterThan(1)
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
    expect(screen.getAllByText('暂无影响摘要').length).toBeGreaterThan(0)

    await user.click(screen.getByText('qt-city'))
    expect(feedback.setSelectedFeedbackTaskId).toHaveBeenCalledWith(12)
  })

  it('列表影响摘要最多展示 3 条，详情展示全部', () => {
    const item = makeCorrection()
    renderFeedback({
      feedbackCorrections: [item],
      filteredFeedbackCorrections: [item],
      pagedFeedbackCorrections: [item],
      ...selectedFields(item),
    })

    expect(screen.getAllByText('影响关系 1 条')).toHaveLength(2)
    expect(screen.getAllByText('新增纠正关系 1 条')).toHaveLength(2)
    expect(screen.getAllByText('写入纠错段落 1 条')).toHaveLength(2)
    expect(screen.getAllByText('标记旧段落 1 条')).toHaveLength(1)
    expect(screen.getByText('触发 Episode 修复 2 个来源')).toBeInTheDocument()
    expect(screen.getByText('触发 Profile 刷新 1 个对象')).toBeInTheDocument()
  })

  it('已应用且未回退时可打开回退对话框', async () => {
    const user = userEvent.setup()
    const item = makeCorrection()
    const { feedback } = renderFeedback({
      pagedFeedbackCorrections: [item],
      ...selectedFields(item),
    })

    await user.click(screen.getByRole('button', { name: '回退本次纠错' }))
    expect(feedback.openFeedbackRollbackDialog).toHaveBeenCalledOnce()
  })

  it('已回退、回退中或非已应用时禁用回退按钮', async () => {
    const user = userEvent.setup()
    const rolled = makeCorrection({ rollback_status: 'rolled_back', rolled_back_at: 1_710_000_200 })
    const { feedback, rerender } = renderFeedback({
      pagedFeedbackCorrections: [rolled],
      ...selectedFields(rolled),
    })

    expect(screen.getByRole('button', { name: '已回退' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '已回退' }))
    expect(feedback.openFeedbackRollbackDialog).not.toHaveBeenCalled()

    const skipped = makeCorrection({ task_status: 'skipped' })
    rerender(
      <Tabs defaultValue="feedback">
        <FeedbackTab
          feedback={makeFeedback({
            pagedFeedbackCorrections: [skipped],
            ...selectedFields(skipped),
            openFeedbackRollbackDialog: feedback.openFeedbackRollbackDialog,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByRole('button', { name: '回退本次纠错' })).toBeDisabled()

    const running = makeCorrection()
    rerender(
      <Tabs defaultValue="feedback">
        <FeedbackTab
          feedback={makeFeedback({
            pagedFeedbackCorrections: [running],
            ...selectedFields(running),
            feedbackRollingBack: true,
            openFeedbackRollbackDialog: feedback.openFeedbackRollbackDialog,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByRole('button', { name: '回退本次纠错' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '回退本次纠错' }))
    expect(feedback.openFeedbackRollbackDialog).not.toHaveBeenCalled()
  })

  it('详情加载中、任务错误与回退错误都会展示', () => {
    const item = makeCorrection({
      rollback_error: '回退写入失败',
      query_text: '',
      session_id: '',
      task_status: undefined as unknown as string,
      rollback_status: undefined as unknown as string,
      decision: undefined as unknown as string,
      feedback_message_count: undefined as unknown as number,
      decision_confidence: undefined as unknown as number,
      rollback_plan_summary: undefined,
      query_snapshot: undefined,
      decision_payload: undefined,
      rollback_result: undefined,
      affected_counts: {},
    })
    renderFeedback({
      pagedFeedbackCorrections: [item],
      ...selectedFields(item),
      selectedFeedbackTaskLoading: true,
      selectedFeedbackTaskError: '未能加载纠错任务详情',
    })

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByText('未能加载纠错任务详情')).toBeInTheDocument()
    expect(screen.getByText('回退写入失败')).toBeInTheDocument()
    expect(screen.getAllByText(/无查询文本/).length).toBeGreaterThan(0)
    expect(screen.getByText('-')).toBeInTheDocument()
    expect(screen.getByText('0.00')).toBeInTheDocument()
    expect(screen.getByText('当前详情没有记录旧结论')).toBeInTheDocument()
    expect(screen.getByText('当前详情没有记录新结论')).toBeInTheDocument()
    expect(screen.getByText('当前没有可展示的影响范围摘要')).toBeInTheDocument()
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
  })

  it('动作时间线支持搜索、分页、缺字段与两种空态', async () => {
    const user = userEvent.setup()
    const item = makeCorrection()
    const fullLog = makeLog()
    const sparseLog = makeLog({
      id: 2,
      action_type: 'skip',
      target_hash: '',
      reason: '',
      before_payload: {},
      after_payload: {},
      created_at: undefined,
    })

    const { feedback, rerender } = renderFeedback({
      pagedFeedbackCorrections: [item],
      ...selectedFields(item),
      selectedFeedbackActionLogs: [fullLog, sparseLog],
      pagedFeedbackActionLogs: [fullLog, sparseLog],
      feedbackActionLogPage: 2,
      feedbackActionLogPageCount: 3,
    })

    await user.click(screen.getByText('动作时间线'))
    expect(
      screen.getByText(`第 2 / 3 页，每页 ${FEEDBACK_ACTION_LOG_PAGE_SIZE} 项`),
    ).toBeInTheDocument()
    expect(screen.getByText('撤销旧关系')).toBeInTheDocument()
    expect(screen.getAllByText('rel-old').length).toBeGreaterThan(0)
    expect(screen.getByText('旧关系已失效：Alice -> 认识 -> Bob')).toBeInTheDocument()
    expect(screen.getByText('原因：用户明确纠正')).toBeInTheDocument()
    expect(screen.getByText('处理前：')).toBeInTheDocument()
    expect(screen.getByText('处理后：')).toBeInTheDocument()
    expect(screen.getByText('跳过处理')).toBeInTheDocument()
    expect(screen.getByText('这次纠错被跳过')).toBeInTheDocument()
    expect(screen.getAllByText('未知时间').length).toBeGreaterThan(0)

    fireEvent.change(screen.getByPlaceholderText('搜索动作 / 目标哈希 / 预览内容'), {
      target: { value: 'rel-old' },
    })
    expect(feedback.setFeedbackActionLogSearch).toHaveBeenCalledWith('rel-old')

    const prevButtons = screen.getAllByRole('button', { name: '上一页' })
    const nextButtons = screen.getAllByRole('button', { name: '下一页' })
    await user.click(prevButtons[0])
    const prev = pageUpdater(feedback.setFeedbackActionLogPage, 0)
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(nextButtons[0])
    const next = pageUpdater(feedback.setFeedbackActionLogPage, 1)
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)

    rerender(
      <Tabs defaultValue="feedback">
        <FeedbackTab
          feedback={makeFeedback({
            pagedFeedbackCorrections: [item],
            ...selectedFields(item),
            selectedFeedbackActionLogs: [fullLog],
            pagedFeedbackActionLogs: [],
            feedbackActionLogSearch: 'no-match',
          })}
        />
      </Tabs>,
    )
    await user.click(screen.getByText('动作时间线'))
    expect(screen.getByText('当前筛选条件下没有动作日志')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="feedback">
        <FeedbackTab
          feedback={makeFeedback({
            pagedFeedbackCorrections: [item],
            ...selectedFields(item),
            selectedFeedbackActionLogs: [],
            pagedFeedbackActionLogs: [],
          })}
        />
      </Tabs>,
    )
    await user.click(screen.getByText('动作时间线'))
    expect(screen.getByText('当前任务没有动作日志')).toBeInTheDocument()
  })

  it('纠错列表分页会夹到合法页码', async () => {
    const user = userEvent.setup()
    const item = makeCorrection()
    const { feedback } = renderFeedback({
      feedbackCorrections: [item],
      filteredFeedbackCorrections: [item],
      pagedFeedbackCorrections: [item],
      feedbackPage: 2,
      feedbackPageCount: 3,
      ...selectedFields(item),
    })

    expect(
      screen.getByText(`第 2 / 3 页，每页显示 ${FEEDBACK_CORRECTION_PAGE_SIZE} 条`),
    ).toBeInTheDocument()

    const prevButtons = screen.getAllByRole('button', { name: '上一页' })
    const nextButtons = screen.getAllByRole('button', { name: '下一页' })
    await user.click(prevButtons[prevButtons.length - 1])
    const prev = pageUpdater(feedback.setFeedbackPage, 0)
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(nextButtons[nextButtons.length - 1])
    const next = pageUpdater(feedback.setFeedbackPage, 1)
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)
  })
})
