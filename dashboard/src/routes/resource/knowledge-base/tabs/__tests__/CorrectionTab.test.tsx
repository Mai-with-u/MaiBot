/**
 * CorrectionTab 补测：ImportCorrectionTabs 未打到的空值回退、级联分区与对话框取消。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import type {
  MemoryCorrectionOperationPayload,
  MemoryCorrectionPlanPayload,
  MemoryCorrectionRelationCascadePayload,
  MemoryImportChatTargetPayload,
} from '@/lib/memory-api'

import type { UseMemoryCorrectionResult } from '../../hooks/useMemoryCorrection'
import { CorrectionTab } from '../CorrectionTab'

afterEach(() => {
  cleanup()
})

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

function makeChat(overrides: Partial<MemoryImportChatTargetPayload> = {}): MemoryImportChatTargetPayload {
  return {
    chat_id: 'chat-1',
    chat_name: '测试群',
    platform: 'qq',
    group_id: '10001',
    user_id: null,
    is_group: true,
    account_id: 'bot-1',
    scope: 'group',
    ...overrides,
  }
}

function makeRelation(
  overrides: Partial<MemoryCorrectionRelationCascadePayload> = {},
): MemoryCorrectionRelationCascadePayload {
  return {
    paragraph_hash: 'para-1',
    relation_hash: 'rel-1',
    action: 'mark_inactive',
    reason: 'superseded',
    subject: '张三',
    predicate: '住在',
    object: '杭州',
    ...overrides,
  }
}

function makePlan(overrides: Partial<MemoryCorrectionPlanPayload> = {}): MemoryCorrectionPlanPayload {
  return {
    plan_id: 'plan-1',
    request_text: '把张三的常住城市改为杭州',
    scope: 'person_profile',
    target_person_id: 'person-1',
    target_chat_id: 'chat-1',
    status: 'awaiting_confirmation',
    confidence: 0.86,
    plan: {
      scope: 'person_profile',
      request_text: '把张三的常住城市改为杭州',
      person_id: 'person-1',
      chat_id: 'chat-1',
      confidence: 0.86,
      risk_level: 'low',
      reason: '用户明确修正',
      operations: [],
    },
    preview: {
      request_text: '把张三的常住城市改为杭州',
      scope: 'person_profile',
      person_id: 'person-1',
      person_keyword: '张三',
      chat_id: 'chat-1',
      candidates: [],
      operations: [],
      requires_confirmation: true,
      confirm_threshold: 0.5,
      reason: '用户明确修正',
    },
    execution: {},
    created_at: 1_710_000_000,
    updated_at: 1_710_000_100,
    requested_by: 'knowledge_base',
    reason: '用户明确修正',
    ...overrides,
  }
}

function makeCorrection(overrides: Partial<UseMemoryCorrectionResult> = {}): UseMemoryCorrectionResult {
  return {
    requestText: '',
    setRequestText: vi.fn(),
    scope: 'person_profile',
    setScope: vi.fn(),
    personId: '',
    setPersonId: vi.fn(),
    personKeyword: '',
    setPersonKeyword: vi.fn(),
    chatId: '',
    setChatId: vi.fn(),
    candidateLimit: '12',
    setCandidateLimit: vi.fn(),
    candidateLimitMax: 12,
    correctionReason: '',
    setCorrectionReason: vi.fn(),
    planSearch: '',
    setPlanSearch: vi.fn(),
    planStatusFilter: 'all',
    setPlanStatusFilter: vi.fn(),
    planScopeFilter: 'all',
    setPlanScopeFilter: vi.fn(),
    plans: [],
    filteredPlans: [],
    pagedPlans: [],
    planPage: 1,
    setPlanPage: vi.fn(),
    planPageCount: 1,
    selectedPlanId: '',
    setSelectedPlanId: vi.fn(),
    selectedPlan: null,
    selectedPreview: null,
    selectedPlanLoading: false,
    selectedPlanError: '',
    chatTargets: [],
    chatTargetsLoading: false,
    chatTargetsErrorText: '',
    correctionErrorText: '',
    previewPayload: null,
    previewing: false,
    executingPlanId: '',
    rollingBackPlanId: '',
    submitPreview: vi.fn(async () => {}),
    executePlan: vi.fn(async () => {}),
    rollbackPlan: vi.fn(async () => {}),
    refreshPlans: vi.fn(async () => {}),
    ...overrides,
  }
}

function renderCorrection(overrides: Partial<UseMemoryCorrectionResult> = {}) {
  const correction = makeCorrection(overrides)
  const view = render(
    <Tabs defaultValue="correction">
      <CorrectionTab correction={correction} />
    </Tabs>,
  )
  return { ...view, correction }
}

describe('CorrectionTab', () => {
  it('聊天流输入写入，并处理空名称、无平台与无 ID 标签', () => {
    const chats: MemoryImportChatTargetPayload[] = [
      makeChat({
        chat_id: 'id-only',
        chat_name: '',
        platform: '',
        group_id: null,
        user_id: '',
        account_id: null,
        is_group: false,
      }),
      makeChat({
        chat_id: 'no-plat',
        chat_name: '无平台私聊',
        platform: null,
        group_id: null,
        user_id: 'u-null-plat',
        is_group: false,
        account_id: null,
      }),
      makeChat({
        chat_id: 'u-empty',
        chat_name: '无名会话',
        platform: '',
        group_id: null,
        user_id: '',
        is_group: false,
        account_id: null,
      }),
    ]
    const { correction, rerender } = renderCorrection({ chatTargets: chats, chatId: '' })

    expect(screen.getByText('id-only')).toBeInTheDocument()
    expect(screen.getByLabelText('候选上限')).toHaveAttribute('max', '12')
    expect(screen.getByLabelText('候选上限')).toHaveAttribute('placeholder', '12')

    fireEvent.change(screen.getByLabelText('聊天流 ID / 名称'), { target: { value: 'no-plat' } })
    expect(correction.setChatId).toHaveBeenCalledWith('no-plat')

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatTargets: chats, chatId: 'no-plat' })} />
      </Tabs>,
    )
    expect(screen.getByText(/未知平台/)).toBeInTheDocument()
    expect(screen.getByText(/用户 ID u-null-plat/)).toBeInTheDocument()
    expect(screen.getByText('无平台私聊')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatTargets: chats, chatId: 'u-empty' })} />
      </Tabs>,
    )
    expect(screen.getByText('u-empty')).toBeInTheDocument()
    expect(screen.queryByText(/无名会话 ·/)).not.toBeInTheDocument()
  })

  it('status/scope 缺省时显示未知，操作与级联空字段走回退文案', () => {
    const operations: MemoryCorrectionOperationPayload[] = [
      { action: 'mark_superseded' } as MemoryCorrectionOperationPayload,
      { action: 'mark_superseded', reason: undefined } as MemoryCorrectionOperationPayload,
      { action: 'ingest_text' } as MemoryCorrectionOperationPayload,
      { action: 'ingest_text', reason: undefined } as MemoryCorrectionOperationPayload,
      { action: 'custom_op' } as MemoryCorrectionOperationPayload,
      { action: 'custom_op', reason: undefined } as MemoryCorrectionOperationPayload,
    ]
    const plan = makePlan({
      status: undefined as never,
      scope: undefined as never,
    })
    renderCorrection({
      selectedPlan: plan,
      selectedPreview: {
        ...plan.preview,
        operations,
        cascade_preview: {
          counts: {
            relations: 1,
            relations_mark_inactive: 0,
            relations_mark_stale_evidence: 0,
            relations_skipped_protected: 0,
            entities: 0,
          },
          relations: [
            makeRelation({
              action: '' as MemoryCorrectionRelationCascadePayload['action'],
              relation_hash: 'r-empty-action',
            }),
          ],
          entities: [],
        },
      },
    })

    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
    expect(screen.getAllByText('无操作摘要').length).toBeGreaterThan(0)
    expect(screen.getByText('未知处理')).toBeInTheDocument()
    expect(screen.getByText('联动影响')).toBeInTheDocument()
    expect(screen.queryByText(/受影响实体：/)).not.toBeInTheDocument()
  })

  it('仅实体的联动区不渲染关系列表', () => {
    const plan = makePlan()
    renderCorrection({
      selectedPlan: plan,
      selectedPreview: {
        ...plan.preview,
        cascade_preview: {
          counts: {
            relations: 0,
            relations_mark_inactive: 0,
            relations_mark_stale_evidence: 0,
            relations_skipped_protected: 0,
            entities: 1,
          },
          relations: [],
          entities: [
            {
              paragraph_hash: 'para-1',
              entity_hash: 'ent-1',
              action: 'record_impact_only',
              reason: 'impact',
              name: '杭州',
              type: 'city',
            },
          ],
        },
      },
    })

    expect(screen.getByText('联动影响')).toBeInTheDocument()
    expect(screen.getByText('受影响实体：杭州')).toBeInTheDocument()
    expect(screen.queryByText('关系失效')).not.toBeInTheDocument()
  })

  it('回滚缺省 stale_marks 字段计为 0', () => {
    const plan = makePlan({
      status: 'executed',
      execution: {
        rollback: {
          success: true,
          new_relations_deactivated: [],
          restored_targets: [],
          items: [],
          requested_by: 'knowledge_base',
          reason: '回滚',
        },
      },
    })
    renderCorrection({ selectedPlan: plan })

    expect(screen.getByText('恢复目标 0')).toBeInTheDocument()
    expect(screen.getByText('删除旧证据标记 0')).toBeInTheDocument()
    expect(screen.getByText('恢复旧证据标记 0')).toBeInTheDocument()
    expect(screen.getByText('跳过旧证据标记 0')).toBeInTheDocument()
    expect(screen.queryByText('删除的旧证据标记')).not.toBeInTheDocument()
  })

  it('人物、聊天流和原因按空值链路回退，计划列表同步展示', () => {
    const emptyFallbacks = makePlan({
      plan_id: 'p-empty',
      target_person_id: '',
      target_chat_id: '',
      reason: '',
      plan: {
        ...makePlan().plan,
        reason: '',
      },
      preview: {
        ...makePlan().preview,
        person_keyword: '',
        reason: '',
      },
    })
    const { rerender } = renderCorrection({
      selectedPlan: emptyFallbacks,
      plans: [emptyFallbacks],
      filteredPlans: [emptyFallbacks],
      pagedPlans: [emptyFallbacks],
    })

    expect(screen.getAllByText('人物：-').length).toBeGreaterThan(0)
    expect(screen.getAllByText('聊天流：-').length).toBeGreaterThan(0)
    expect(screen.getByText('原因：-')).toBeInTheDocument()

    const keywordOnly = makePlan({
      plan_id: 'p-keyword',
      target_person_id: '',
      target_chat_id: '',
      reason: '',
      plan: {
        ...makePlan().plan,
        reason: '',
      },
      preview: {
        ...makePlan().preview,
        person_keyword: '关键词甲',
        reason: '预览原因',
      },
    })
    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab
          correction={makeCorrection({
            selectedPlan: keywordOnly,
            plans: [keywordOnly],
            filteredPlans: [keywordOnly],
            pagedPlans: [keywordOnly],
          })}
        />
      </Tabs>,
    )
    expect(screen.getAllByText('人物：关键词甲').length).toBeGreaterThan(0)
    expect(screen.getByText('原因：预览原因')).toBeInTheDocument()

    const planReason = makePlan({
      plan_id: 'p-plan-reason',
      target_person_id: '',
      reason: '',
      plan: {
        ...makePlan().plan,
        reason: '计划原因',
      },
      preview: {
        ...makePlan().preview,
        person_keyword: '关键词甲',
        reason: '预览原因',
      },
    })
    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab
          correction={makeCorrection({
            selectedPlan: planReason,
            plans: [planReason],
            filteredPlans: [planReason],
            pagedPlans: [planReason],
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('原因：计划原因')).toBeInTheDocument()
  })

  it('取消执行与回滚确认框不提交', async () => {
    const user = userEvent.setup()
    const awaiting = makePlan()
    const { correction, rerender } = renderCorrection({ selectedPlan: awaiting })

    await user.click(screen.getByRole('button', { name: '确认执行' }))
    const executeDialog = await screen.findByRole('alertdialog')
    expect(within(executeDialog).getByText('确认执行记忆修正')).toBeInTheDocument()
    await user.click(within(executeDialog).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(correction.executePlan).not.toHaveBeenCalled()

    const executed = makePlan({ status: 'executed' })
    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ selectedPlan: executed, rollbackPlan: correction.rollbackPlan })} />
      </Tabs>,
    )
    await user.click(screen.getByRole('button', { name: '回滚计划' }))
    const rollbackDialog = await screen.findByRole('alertdialog')
    expect(within(rollbackDialog).getByText('确认回滚记忆修正')).toBeInTheDocument()
    await user.click(within(rollbackDialog).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(correction.rollbackPlan).not.toHaveBeenCalled()
  })
})
