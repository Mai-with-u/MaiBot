import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import i18n from '@/i18n'
import {
  getMemoryRecordContext,
  previewMemoryCorrection,
  restoreMemoryFact,
  retractMemoryFact,
  searchMemoryRecords,
  updateMemoryFact,
  type MemoryRecordContextPayload,
  type MemoryRecordPayload,
  type MemoryCorrectionPlanPayload,
} from '@/lib/memory-api'

import { MemoryRecordsTab } from '../MemoryRecordsTab'

vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    getMemoryRecordContext: vi.fn(),
    previewMemoryCorrection: vi.fn(),
    restoreMemoryFact: vi.fn(),
    retractMemoryFact: vi.fn(),
    searchMemoryRecords: vi.fn(),
    updateMemoryFact: vi.fn(),
  }
})

const searchMock = vi.mocked(searchMemoryRecords)
const contextMock = vi.mocked(getMemoryRecordContext)
const previewMock = vi.mocked(previewMemoryCorrection)
const updateFactMock = vi.mocked(updateMemoryFact)
const retractFactMock = vi.mocked(retractMemoryFact)
const restoreFactMock = vi.mocked(restoreMemoryFact)

const paragraph: MemoryRecordPayload = {
  type: 'paragraph',
  id: 'paragraph-01',
  title: '小明喜欢咖啡',
  summary: '小明喜欢咖啡，并且常在周末尝试新的豆子。',
  source: 'chat_summary:session-01',
  status: 'active',
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
  metadata: { knowledge_type: 'factual' },
}

const context: MemoryRecordContextPayload = {
  success: true,
  record: paragraph,
  related: {
    paragraphs: [paragraph],
    entities: [
      {
        type: 'entity',
        id: 'entity-01',
        title: '小明',
        summary: '在 3 处记忆中出现',
        source: '',
        status: 'active',
        metadata: { name: '小明' },
      },
    ],
    relations: [],
    facts: [],
    episodes: [],
    profiles: [],
  },
  counts: {
    paragraphs: 1,
    entities: 1,
    relations: 0,
    facts: 0,
    episodes: 0,
    profiles: 0,
  },
  fact_evidence: [],
  fact_transitions: [],
  projection: { graph_jobs: [], graph_pending_count: 0 },
  available_actions: ['graph', 'correct', 'delete'],
}

function renderTab(onAction = vi.fn(), onCorrectionPlan = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <Tabs value="records">
        <MemoryRecordsTab onAction={onAction} onCorrectionPlan={onCorrectionPlan} />
      </Tabs>
    </QueryClientProvider>
  )
  return onAction
}

beforeEach(async () => {
  await i18n.changeLanguage('zh')
  searchMock.mockReset()
  contextMock.mockReset()
  previewMock.mockReset()
  updateFactMock.mockReset()
  retractFactMock.mockReset()
  restoreFactMock.mockReset()
  searchMock.mockResolvedValue({
    success: true,
    query: '',
    types: ['paragraph', 'entity', 'relation', 'fact'],
    include_inactive: false,
    limit: 80,
    count: 1,
    counts: { paragraph: 1 },
    items: [paragraph],
  })
  contextMock.mockResolvedValue(context)
  updateFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1' }, refresh_queued: true })
  retractFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1', status: 'retracted' } })
  restoreFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1', status: 'active' } })
})

describe('MemoryRecordsTab', () => {
  it('多选独立于详情切换，支持全选、取消和重新搜索后清空', async () => {
    const user = userEvent.setup()
    const second = { ...paragraph, id: 'paragraph-02', title: '小明喜欢红茶' }
    searchMock.mockResolvedValue({ success: true, query: '', types: ['paragraph'], include_inactive: false,
      limit: 80, count: 2, counts: { paragraph: 2 }, items: [paragraph, second] })
    renderTab()
    await user.click(await screen.findByRole('checkbox', { name: '选择段落：小明喜欢咖啡' }))
    expect(screen.getByText('已选 1 条')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '全选当前结果' })).toHaveAttribute('data-state', 'indeterminate')
    await user.click(screen.getByRole('button', { name: /小明喜欢红茶/ }))
    expect(screen.getByText('已选 1 条')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('checkbox', { name: '全选当前结果' }))
    expect(screen.getByText('已选 2 条')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '清空选择' }))
    expect(screen.getByText('已选 0 条')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '全选当前结果' }))
    await user.type(screen.getByLabelText('搜索记忆'), '咖啡')
    await user.click(screen.getByRole('button', { name: '查询' }))
    expect(screen.getByText('已选 0 条')).toBeInTheDocument()
  })

  it('批量预览发送精确目标，失败时保留选择，成功后打开待确认计划', async () => {
    const user = userEvent.setup()
    const onCorrectionPlan = vi.fn()
    const second = { ...paragraph, id: 'relation-01', type: 'relation' as const, title: '小明 喜欢 咖啡' }
    searchMock.mockResolvedValue({ success: true, query: '', types: ['paragraph', 'relation'], include_inactive: false,
      limit: 80, count: 2, counts: { paragraph: 1, relation: 1 }, items: [paragraph, second] })
    const plan: MemoryCorrectionPlanPayload = {
      plan_id: 'batch-plan', request_text: '改为喜欢绿茶', scope: 'memory', target_person_id: '', target_chat_id: '',
      status: 'awaiting_confirmation', confidence: 0.9, created_at: 1, updated_at: 1, requested_by: 'knowledge_base', reason: '',
      plan: { scope: 'memory', request_text: '改为喜欢绿茶', person_id: '', chat_id: '', confidence: 0.9, risk_level: 'medium', reason: '', operations: [] },
      preview: { request_text: '改为喜欢绿茶', scope: 'memory', person_id: '', person_keyword: '', chat_id: '', candidates: [], operations: [], requires_confirmation: true, confirm_threshold: 0.8, reason: '' },
      execution: {},
    }
    previewMock.mockResolvedValueOnce({ success: false, error: '所选关系受保护' })
      .mockResolvedValueOnce({ success: true, plan_id: plan.plan_id, plan })
    renderTab(vi.fn(), onCorrectionPlan)
    await screen.findByRole('checkbox', { name: '选择段落：小明喜欢咖啡' })
    await user.click(screen.getByRole('checkbox', { name: '全选当前结果' }))
    await user.click(screen.getByRole('button', { name: '修正所选' }))
    expect(screen.getByRole('button', { name: '生成预览' })).toBeDisabled()
    await user.type(screen.getByLabelText('修正内容'), '改为喜欢绿茶')
    await user.click(screen.getByRole('button', { name: '生成预览' }))
    expect(await screen.findByText('所选关系受保护')).toBeInTheDocument()
    expect(onCorrectionPlan).not.toHaveBeenCalled()
    expect(previewMock).toHaveBeenCalledWith(expect.objectContaining({
      request_text: '改为喜欢绿茶', scope: 'memory', targets: [
        { type: 'paragraph', id: paragraph.id }, { type: 'relation', id: second.id },
      ],
    }))
    await user.click(screen.getByRole('button', { name: '生成预览' }))
    await waitFor(() => expect(onCorrectionPlan).toHaveBeenCalledWith('batch-plan', '改为喜欢绿茶'))
    expect(screen.getByText('已选 0 条')).toBeInTheDocument()
  })

  it('事实和实体可以勾选，但明确提示使用各自编辑入口', async () => {
    const user = userEvent.setup()
    const fact = { ...paragraph, type: 'fact' as const }
    searchMock.mockResolvedValue({ success: true, query: '', types: ['fact'], include_inactive: false,
      limit: 80, count: 1, counts: { fact: 1 }, items: [fact] })
    renderTab()
    await user.click(await screen.findByRole('checkbox', { name: '选择事实：小明喜欢咖啡' }))
    expect(screen.getByRole('button', { name: '修正所选' })).toBeDisabled()
    expect(screen.getByText(/事实请逐条编辑/)).toBeInTheDocument()
  })

  it('展示权威记录及数据库派生的关联内容', async () => {
    const user = userEvent.setup()
    renderTab()

    expect(await screen.findAllByText('小明喜欢咖啡')).not.toHaveLength(0)
    expect(screen.getAllByText('事实资料')).not.toHaveLength(0)
    await user.click(screen.getByRole('button', { name: /小明喜欢咖啡/ }))
    // 段落标题去重后展示类型标识，正文区固定可见
    expect(await screen.findByText('段落 · 事实资料')).toBeInTheDocument()
    expect(screen.getAllByText('小明喜欢咖啡，并且常在周末尝试新的豆子。').length).toBeGreaterThan(0)
    expect(screen.getByText('来源：chat_summary:session-01')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /实体/ }))
    expect(screen.getByText('小明')).toBeInTheDocument()
    expect(contextMock).toHaveBeenCalledWith('paragraph', 'paragraph-01')
  })

  it('列表与详情只展示可读来源，不把内部来源标记当来源', async () => {
    const user = userEvent.setup()
    const namedParagraph: MemoryRecordPayload = {
      ...paragraph,
      source_label: '摸鱼群',
    }
    searchMock.mockResolvedValue({
      success: true,
      query: '',
      types: ['paragraph'],
      include_inactive: false,
      limit: 80,
      count: 1,
      counts: { paragraph: 1 },
      items: [namedParagraph],
    })
    contextMock.mockResolvedValue({ ...context, record: namedParagraph })
    renderTab()

    // 列表副信息显示「来自 摸鱼群」，不再裸露 chat_summary:<session_id>
    expect(await screen.findByText('来自 摸鱼群')).toBeInTheDocument()
    expect(screen.queryByText('chat_summary:session-01')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /小明喜欢咖啡/ }))
    // 详情里保留可读名称，原始标记降级为等宽次要信息，便于核对
    expect(await screen.findByText(/^来源：摸鱼群/)).toBeInTheDocument()
    expect(screen.getByText('chat_summary:session-01')).toBeInTheDocument()
  })

  it('没有可读来源时不展示来源行', async () => {
    searchMock.mockResolvedValue({
      success: true,
      query: '',
      types: ['paragraph'],
      include_inactive: false,
      limit: 80,
      count: 1,
      counts: { paragraph: 1 },
      items: [{ ...paragraph, source: 'chat_summary:session-01', source_label: '' }],
    })
    renderTab()

    expect(await screen.findByText('小明喜欢咖啡')).toBeInTheDocument()
    expect(screen.queryByText('来自 chat_summary:session-01')).not.toBeInTheDocument()
  })

  it('把删除动作交给页面现有删除流程', async () => {
    const user = userEvent.setup()
    const onAction = renderTab()

    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    await screen.findByText('关联内容')
    await user.click(screen.getByRole('button', { name: '删除' }))

    await waitFor(() => {
      expect(onAction).toHaveBeenCalledWith('delete', paragraph, context, undefined)
    })
  })

  it('展示事实状态变更和图投影失败原因', async () => {
    const user = userEvent.setup()
    contextMock.mockResolvedValue({
      ...context,
      fact_transitions: [
        {
          transition_id: 'transition-01',
          transition_type: 'retract',
          reason: '本人确认该信息已经失效',
          evidence_type: 'paragraph',
          evidence_id: 'paragraph-01',
          created_at: 1_700_000_200,
        },
      ],
      projection: {
        graph_pending_count: 1,
        graph_jobs: [
          {
            relation_hash: 'relation-01',
            desired_active: true,
            status: 'failed',
            attempt_count: 2,
            last_error: '图存储暂时不可用',
          },
        ],
      },
    })

    renderTab()

    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    await screen.findByText('关联内容')
    await user.click(screen.getByRole('tab', { name: /变更/ }))
    expect(screen.getByText('撤回')).toBeInTheDocument()
    expect(screen.getByText('本人确认该信息已经失效')).toBeInTheDocument()
    expect(screen.getByText('图投影状态')).toBeInTheDocument()
    expect(screen.getByText('图存储暂时不可用')).toBeInTheDocument()
    expect(screen.getByText('有 1 条关系图投影任务未完成')).toBeInTheDocument()
  })

  it('状态和证据标签跟随当前语言切换', async () => {
    const inactiveRelation: MemoryRecordPayload = {
      type: 'relation',
      id: 'relation-inactive',
      title: '小明 偏好 浅色主题',
      summary: '置信度 0.80',
      source: 'paragraph-01',
      status: 'inactive',
      metadata: {},
    }
    const retractedFact: MemoryRecordPayload = {
      type: 'fact',
      id: 'fact-retracted',
      title: '界面主题偏好: 浅色主题',
      summary: 'person · 小明',
      source: '小明',
      status: 'retracted',
      metadata: {},
    }
    searchMock.mockResolvedValue({
      success: true,
      query: '',
      types: ['relation'],
      include_inactive: true,
      limit: 80,
      count: 1,
      counts: { relation: 1 },
      items: [inactiveRelation],
    })
    contextMock.mockResolvedValue({
      ...context,
      record: inactiveRelation,
      related: {
        ...context.related,
        facts: [retractedFact],
      },
      fact_evidence: [
        {
          evidence_type: 'paragraph',
          evidence_id: 'paragraph-01',
          stance: 'support',
        },
      ],
    })

    await i18n.changeLanguage('en')
    const user = userEvent.setup()
    renderTab()

    await user.click(await screen.findByRole('button', { name: /小明 偏好 浅色主题/ }))
    expect(await screen.findAllByText('Inactive')).not.toHaveLength(0)
    expect(await screen.findByText('事实账本')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /事实/ }))
    expect(screen.getByText('Retracted')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /证据/ }))
    expect(screen.getByText('Paragraph')).toBeInTheDocument()
    expect(screen.getByText('Support')).toBeInTheDocument()

    await i18n.changeLanguage('zh')
    await waitFor(() => {
      expect(screen.getAllByText('已停用')).not.toHaveLength(0)
      expect(screen.getByText('已撤回')).toBeInTheDocument()
      expect(screen.getByText('支持')).toBeInTheDocument()
    })
  })

  it('编辑、撤回和恢复事实直接调用事实账本接口', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fact: MemoryRecordPayload = {
      type: 'fact',
      id: 'fact-1',
      title: '饮品偏好: 咖啡',
      summary: 'person · person-1',
      source: 'person-1',
      status: 'active',
      metadata: {
        scope_type: 'person',
        scope_id: 'person-1',
        fact_key: 'favorite_drink',
        value_text: '咖啡',
        polarity: 'positive',
        cardinality: 'single',
        stability: 'stable',
        profile_section: 'interaction_preferences',
        authority: 'manual',
        confidence: 0.9,
      },
    }
    searchMock.mockResolvedValue({
      success: true,
      query: '',
      types: ['fact'],
      include_inactive: true,
      limit: 80,
      count: 1,
      counts: { fact: 1 },
      items: [fact],
    })
    contextMock.mockResolvedValue({
      ...context,
      record: fact,
      related: { ...context.related, facts: [fact] },
      available_actions: ['edit_fact', 'retract_fact', 'profile'],
    })

    renderTab()
    // 详情动作位于弹窗内：先点开记录，编辑保存后弹窗随刷新关闭，再重开继续撤回/恢复
    const openDetail = async () => {
      await user.click(await screen.findByRole('button', { name: /饮品偏好: 咖啡/ }))
    }
    await openDetail()
    await user.click(await screen.findByRole('button', { name: '编辑' }))
    const valueInput = screen.getByLabelText('事实内容')
    await user.clear(valueInput)
    await user.type(valueInput, '绿茶')
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    await waitFor(() => {
      expect(updateFactMock).toHaveBeenCalledWith(
        'fact-1',
        expect.objectContaining({ value_text: '绿茶', profile_section: 'interaction_preferences' })
      )
    })

    await openDetail()
    await user.click(await screen.findByRole('button', { name: '撤回' }))
    expect(confirmSpy).toHaveBeenCalledWith('确认撤回事实“饮品偏好: 咖啡”？')
    await waitFor(() => {
      expect(retractFactMock).toHaveBeenCalledWith('fact-1', 'knowledge_base_fact_retract')
    })

    contextMock.mockResolvedValue({
      ...context,
      record: { ...fact, status: 'retracted' },
      available_actions: ['restore_fact', 'profile'],
    })
    searchMock.mockResolvedValue({
      success: true,
      query: '',
      types: ['fact'],
      include_inactive: true,
      limit: 80,
      count: 1,
      counts: { fact: 1 },
      items: [{ ...fact, status: 'retracted' }],
    })
    await user.click(screen.getByRole('button', { name: '刷新查询' }))
    await openDetail()
    await waitFor(() => expect(screen.getByRole('button', { name: '恢复' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '恢复' }))
    await waitFor(() => {
      expect(restoreFactMock).toHaveBeenCalledWith('fact-1', 'knowledge_base_fact_restore')
    })
  })
})

function stubPointerCapture() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

function searchPayload(
  items: MemoryRecordPayload[],
  overrides: Partial<{
    query: string
    include_inactive: boolean
    counts: Partial<Record<MemoryRecordPayload['type'], number>>
  }> = {}
) {
  const counts: Partial<Record<MemoryRecordPayload['type'], number>> = {}
  for (const item of items) {
    counts[item.type] = (counts[item.type] ?? 0) + 1
  }
  return {
    success: true,
    query: overrides.query ?? '',
    types: ['paragraph', 'entity', 'relation', 'fact'] as MemoryRecordPayload['type'][],
    include_inactive: overrides.include_inactive ?? false,
    limit: 80,
    count: items.length,
    counts: overrides.counts ?? counts,
    items,
  }
}

function makeRecord(overrides: Partial<MemoryRecordPayload> = {}): MemoryRecordPayload {
  return {
    type: 'paragraph',
    id: 'paragraph-01',
    title: '小明喜欢咖啡',
    summary: '小明喜欢咖啡，并且常在周末尝试新的豆子。',
    source: 'chat_summary:session-01',
    status: 'active',
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    metadata: {},
    ...overrides,
  }
}

describe('MemoryRecordsTab 未覆盖交互', () => {
  beforeEach(() => {
    cleanup()
    stubPointerCapture()
  })

  afterEach(() => {
    cleanup()
  })

  it('相同查询会 refetch，新查询、类型和停用会重搜并展示空结果', async () => {
    const user = userEvent.setup()
    renderTab()
    await screen.findByText('小明喜欢咖啡')

    const initialCalls = searchMock.mock.calls.length
    await user.click(screen.getByRole('button', { name: /^查询$/ }))
    await waitFor(() => expect(searchMock.mock.calls.length).toBeGreaterThan(initialCalls))
    expect(searchMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: '', types: undefined, includeInactive: false })
    )

    searchMock.mockResolvedValue(
      searchPayload([], { query: '咖啡', counts: { paragraph: 0, entity: 0 } })
    )
    await user.type(screen.getByLabelText('搜索记忆'), '  咖啡  ')
    await user.click(screen.getByRole('button', { name: /^查询$/ }))

    expect(await screen.findByText('没有匹配记录')).toBeInTheDocument()
    await waitFor(() => {
      expect(searchMock).toHaveBeenCalledWith(
        expect.objectContaining({ query: '咖啡', includeInactive: false })
      )
    })

    await user.click(screen.getByRole('switch', { name: '显示停用' }))
    await waitFor(() => {
      expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ includeInactive: true }))
    })

    await user.click(screen.getByLabelText('记录类型'))
    await user.click(await screen.findByRole('option', { name: '事实' }))
    await waitFor(() => {
      expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ types: ['fact'] }))
    })
  })

  it('点击列表和关联记录切换选中，无标题记录回退到 ID', async () => {
    const user = userEvent.setup()
    const untitled = makeRecord({
      type: 'entity',
      id: 'entity-untitled',
      title: '',
      summary: '',
      source: '',
      status: 'legacy-status',
    })
    const relation = makeRecord({
      type: 'relation',
      id: 'relation-01',
      title: '小明 偏好 咖啡',
      summary: '置信度 0.9',
      source: 'paragraph-01',
      status: 'active',
    })
    const fact = makeRecord({
      type: 'fact',
      id: 'fact-list',
      title: '饮品偏好: 咖啡',
      summary: 'person · person-1',
      source: 'person-1',
      status: 'active',
    })
    const relatedParagraph = makeRecord({
      id: 'paragraph-02',
      title: '周末手冲',
      summary: 'compact 摘要不展示',
      source: '',
    })
    searchMock.mockResolvedValue(searchPayload([paragraph, untitled, relation, fact]))
    contextMock.mockImplementation(async (type, id) => ({
      ...context,
      record: makeRecord({
        type,
        id,
        title: id === 'entity-untitled' ? '' : id === 'paragraph-02' ? '周末手冲' : paragraph.title,
        summary: id === 'entity-untitled' ? '' : paragraph.summary,
        source: id === 'entity-untitled' ? '' : paragraph.source,
        status: id === 'entity-untitled' ? 'legacy-status' : 'active',
      }),
      related: {
        ...context.related,
        paragraphs: [relatedParagraph],
        relations: [relation],
        facts: [fact],
      },
    }))

    renderTab()
    expect(await screen.findByText('entity-untitled')).toBeInTheDocument()
    expect(screen.getByText('legacy-status')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /entity-untitled/ }))
    await waitFor(() => {
      expect(contextMock).toHaveBeenCalledWith('entity', 'entity-untitled')
    })

    await user.click(await screen.findByRole('button', { name: /周末手冲/ }))
    await waitFor(() => {
      expect(contextMock).toHaveBeenCalledWith('paragraph', 'paragraph-02')
    })
  })

  it('搜索失败时展示 Error 文案，非 Error 回退到默认文案', async () => {
    searchMock.mockRejectedValueOnce(new Error('后端超时'))
    renderTab()
    expect(await screen.findByText('后端超时')).toBeInTheDocument()

    cleanup()
    searchMock.mockRejectedValueOnce('gateway-down')
    renderTab()
    expect(await screen.findByText('查询记忆失败')).toBeInTheDocument()
  })

  it('详情失败时保留选中记录并展示错误，加载中展示派生状态', async () => {
    const user = userEvent.setup()
    const pending = deferred<MemoryRecordContextPayload>()
    contextMock.mockImplementation(() => pending.promise)
    renderTab()
    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    expect(await screen.findByText('正在派生关联内容')).toBeInTheDocument()
    pending.resolve(context)
    expect(await screen.findByText('关联内容')).toBeInTheDocument()

    cleanup()
    contextMock.mockRejectedValue(new Error('详情不可用'))
    renderTab()
    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    expect(await screen.findByText('详情不可用')).toBeInTheDocument()
    expect(screen.getByText('详情加载失败')).toBeInTheDocument()
    expect(screen.getAllByText('小明喜欢咖啡').length).toBeGreaterThan(0)
  })

  it('取消撤回不调接口，进行中禁用按钮，失败与非 Error 都能走完', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm')
    const fact = makeRecord({
      type: 'fact',
      id: 'fact-1',
      title: '饮品偏好: 咖啡',
      summary: 'person · person-1',
      source: 'person-1',
      status: 'active',
      metadata: { scope_id: 'person-1', fact_key: 'favorite_drink', value_text: '咖啡' },
    })
    searchMock.mockResolvedValue(searchPayload([fact], { include_inactive: true }))
    contextMock.mockResolvedValue({
      ...context,
      record: fact,
      available_actions: ['retract_fact', 'restore_fact', 'graph'],
    })

    const pendingRetract = deferred<Awaited<ReturnType<typeof retractMemoryFact>>>()
    retractFactMock.mockImplementation(() => pendingRetract.promise)
    confirmSpy.mockReturnValue(true)
    renderTab()

    await user.click(await screen.findByRole('button', { name: /饮品偏好: 咖啡/ }))
    await user.click(await screen.findByRole('button', { name: '撤回' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '撤回' })).toBeDisabled()
      expect(screen.getByRole('button', { name: '恢复' })).toBeDisabled()
    })
    pendingRetract.resolve({ success: false, error: '不能撤回' })
    await waitFor(() => expect(screen.getByRole('button', { name: '撤回' })).toBeEnabled())

    retractFactMock.mockResolvedValueOnce({ success: false })
    await user.click(screen.getByRole('button', { name: '撤回' }))
    await waitFor(() => expect(retractFactMock).toHaveBeenCalledTimes(2))

    retractFactMock.mockRejectedValueOnce('boom')
    await user.click(screen.getByRole('button', { name: '撤回' }))
    await waitFor(() => expect(retractFactMock).toHaveBeenCalledTimes(3))

    confirmSpy.mockReturnValue(false)
    await user.click(screen.getByRole('button', { name: '撤回' }))
    expect(retractFactMock).toHaveBeenCalledTimes(3)
  })

  it('恢复事实时带上刷新队列标记', async () => {
    const user = userEvent.setup()
    const fact = makeRecord({
      type: 'fact',
      id: 'fact-1',
      title: '饮品偏好: 咖啡',
      summary: 'person · person-1',
      source: 'person-1',
      status: 'retracted',
    })
    searchMock.mockResolvedValue(searchPayload([fact], { include_inactive: true }))
    contextMock.mockResolvedValue({
      ...context,
      record: fact,
      available_actions: ['restore_fact', 'profile'],
    })
    restoreFactMock
      .mockResolvedValueOnce({ success: false, error: '不能恢复' })
      .mockResolvedValueOnce({ success: false })
      .mockResolvedValueOnce({
        success: true,
        claim: { claim_id: 'fact-1', status: 'active' },
        refresh_queued: true,
      })

    renderTab()
    await user.click(await screen.findByRole('button', { name: /饮品偏好: 咖啡/ }))
    await user.click(await screen.findByRole('button', { name: '恢复' }))
    await waitFor(() => expect(restoreFactMock).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: '恢复' }))
    await waitFor(() => expect(restoreFactMock).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: '恢复' }))
    await waitFor(() => {
      expect(restoreFactMock).toHaveBeenCalledWith('fact-1', 'knowledge_base_fact_restore')
      expect(restoreFactMock).toHaveBeenCalledTimes(3)
    })
  })

  it('图谱与修正类动作按规则回调，未知动作回退标签', async () => {
    const user = userEvent.setup()
    contextMock.mockResolvedValue({
      ...context,
      available_actions: ['graph', 'correct', 'reinforce', 'freeze', 'protect', 'mystery_action'],
    })
    const onAction = renderTab()
    // 除 mystery_action 外的动都会关闭详情弹窗，每步重新点开记录
    const openDetail = async () => {
      await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
      await screen.findByText('关联内容')
    }
    await openDetail()

    await user.click(screen.getByRole('button', { name: 'mystery_action' }))
    await user.click(screen.getByRole('button', { name: '图谱' }))
    await openDetail()
    await user.click(screen.getByRole('button', { name: '修正' }))
    await openDetail()
    await user.click(screen.getByRole('button', { name: '强化' }))
    await openDetail()
    await user.click(screen.getByRole('button', { name: '冻结' }))
    await openDetail()
    await user.click(screen.getByRole('button', { name: '保护' }))

    expect(onAction).toHaveBeenCalledWith('mystery_action', paragraph, expect.anything(), undefined)
    expect(onAction).toHaveBeenCalledWith('graph', paragraph, expect.anything(), undefined)
    expect(onAction).toHaveBeenCalledWith('correct', paragraph, expect.anything(), undefined)
    expect(onAction).toHaveBeenCalledWith('reinforce', paragraph, expect.anything(), undefined)
    expect(onAction).toHaveBeenCalledWith('freeze', paragraph, expect.anything(), undefined)
    expect(onAction).toHaveBeenCalledWith('protect', paragraph, expect.anything(), undefined)
  })

  it('展示情景、画像、非失败图投影以及证据和状态变更回退', async () => {
    const user = userEvent.setup()
    const detailRecord = makeRecord({
      title: '仅有创建时间',
      summary: '',
      source: '',
      updated_at: null,
      created_at: 1_700_000_000,
      status: 'conflicted',
    })
    const untitledEpisode = {
      id: 'episode-untitled',
      title: '',
      summary: '无标题情景摘要',
      source: 'chat',
      paragraph_count: 0,
    }
    const namedEpisode = {
      id: 'episode-named',
      title: '周末咖啡局',
      summary: '大家一起喝',
      source: '',
      paragraph_count: 2,
    }
    contextMock.mockResolvedValue({
      ...context,
      record: detailRecord,
      related: {
        paragraphs: [makeRecord({ id: 'paragraph-02', title: '关联段落甲' })],
        entities: context.related.entities,
        relations: [makeRecord({ type: 'relation', id: 'relation-01', title: '关联关系甲' })],
        facts: [makeRecord({ type: 'fact', id: 'fact-related', title: '关联事实甲' })],
        episodes: [namedEpisode, untitledEpisode],
        profiles: [
          {
            person_id: 'person-88',
            profile_version: 3,
            profile_text: '喜欢尝试新豆子',
            source_note: '',
          },
        ],
      },
      available_actions: ['graph'],
      projection: {
        graph_pending_count: 2,
        graph_jobs: [
          { desired_active: false, status: 'running', attempt_count: 0 },
          { desired_active: false },
        ],
      },
      fact_evidence: [{ stance: 'refute' }, { evidence_type: 'manual' }],
      fact_transitions: [
        {
          transition_type: 'mystery',
          created_at: Number.NaN,
          reason: '无法解析的时间戳',
          evidence_type: 'manual',
        },
        { evidence_id: 'only-id' },
        { reason: '无证据' },
      ],
    })
    const onAction = renderTab()
    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    expect(await screen.findAllByText('仅有创建时间')).not.toHaveLength(0)
    expect(screen.getByText('有冲突')).toBeInTheDocument()
    // 图投影是告警区块，始终直接展示
    expect(screen.getAllByText('目标：停用').length).toBeGreaterThan(0)
    expect(screen.getByText('处理中')).toBeInTheDocument()
    expect(screen.getByText('待处理')).toBeInTheDocument()
    expect(screen.getByText('有 2 条关系图投影任务未完成')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /段落/ }))
    expect(screen.getByText('关联段落甲')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /关系/ }))
    expect(screen.getByText('关联关系甲')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /事实/ }))
    expect(screen.getByText('关联事实甲')).toBeInTheDocument()

    // 事实账本：证据与变更分页签展示
    await user.click(screen.getByRole('tab', { name: /证据/ }))
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
    expect(screen.getByText('反证')).toBeInTheDocument()
    expect(screen.getAllByText('人工').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('tab', { name: /变更/ }))
    expect(screen.getByText('mystery')).toBeInTheDocument()
    expect(screen.getByText('无法解析的时间戳')).toBeInTheDocument()
    expect(screen.getByText('only-id')).toBeInTheDocument()
    expect(screen.getByText('无证据')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /情景/ }))
    expect(screen.getByText('周末咖啡局')).toBeInTheDocument()
    expect(screen.getByText('episode-untitled')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /周末咖啡局/ }))
    expect(onAction).toHaveBeenCalledWith(
      'episode',
      expect.objectContaining({ title: '仅有创建时间' }),
      expect.anything(),
      'episode-named'
    )
    // 跳转类动作会关闭详情弹窗，重新打开后继续
    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    await user.click(screen.getByRole('tab', { name: /情景/ }))
    await user.click(screen.getByRole('button', { name: /episode-untitled/ }))
    expect(onAction).toHaveBeenCalledWith(
      'episode',
      expect.anything(),
      expect.anything(),
      'episode-untitled'
    )
    await user.click(await screen.findByRole('button', { name: /小明喜欢咖啡/ }))
    await user.click(screen.getByRole('tab', { name: /画像/ }))
    await user.click(screen.getByRole('button', { name: /person-88/ }))
    expect(onAction).toHaveBeenCalledWith(
      'profile',
      expect.anything(),
      expect.anything(),
      'person-88'
    )
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}
