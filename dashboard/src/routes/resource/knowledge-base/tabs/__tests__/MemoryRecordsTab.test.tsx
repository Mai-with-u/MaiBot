import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import i18n from '@/i18n'
import {
  createMemoryFact,
  getMemoryRecordContext,
  restoreMemoryFact,
  retractMemoryFact,
  searchMemoryRecords,
  updateMemoryFact,
  type MemoryRecordContextPayload,
  type MemoryRecordPayload,
} from '@/lib/memory-api'

import { MemoryRecordsTab } from '../MemoryRecordsTab'

vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    createMemoryFact: vi.fn(),
    getMemoryRecordContext: vi.fn(),
    restoreMemoryFact: vi.fn(),
    retractMemoryFact: vi.fn(),
    searchMemoryRecords: vi.fn(),
    updateMemoryFact: vi.fn(),
  }
})

const searchMock = vi.mocked(searchMemoryRecords)
const contextMock = vi.mocked(getMemoryRecordContext)
const createFactMock = vi.mocked(createMemoryFact)
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

function renderTab(onAction = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <Tabs value="records">
        <MemoryRecordsTab onAction={onAction} />
      </Tabs>
    </QueryClientProvider>
  )
  return onAction
}

beforeEach(async () => {
  await i18n.changeLanguage('zh')
  searchMock.mockReset()
  contextMock.mockReset()
  createFactMock.mockReset()
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
  createFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-new' }, refresh_queued: true })
  updateFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1' }, refresh_queued: true })
  retractFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1', status: 'retracted' } })
  restoreFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-1', status: 'active' } })
})

describe('MemoryRecordsTab', () => {
  it('展示权威记录及数据库派生的关联内容', async () => {
    renderTab()

    expect(await screen.findAllByText('小明喜欢咖啡')).not.toHaveLength(0)
    expect(await screen.findByText('关联实体')).toBeInTheDocument()
    expect(screen.getByText('小明')).toBeInTheDocument()
    expect(contextMock).toHaveBeenCalledWith('paragraph', 'paragraph-01')
  })

  it('把删除动作交给页面现有删除流程', async () => {
    const user = userEvent.setup()
    const onAction = renderTab()

    await screen.findByText('关联实体')
    await user.click(screen.getByRole('button', { name: '删除' }))

    await waitFor(() => {
      expect(onAction).toHaveBeenCalledWith('delete', paragraph, context, undefined)
    })
  })

  it('展示事实状态变更和图投影失败原因', async () => {
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

    expect(await screen.findByText('状态变更')).toBeInTheDocument()
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
    renderTab()

    expect(await screen.findAllByText('Inactive')).not.toHaveLength(0)
    expect(await screen.findByText('事实证据')).toBeInTheDocument()
    expect(screen.getByText('Retracted')).toBeInTheDocument()
    expect(screen.getByText('Paragraph')).toBeInTheDocument()
    expect(screen.getByText('Support')).toBeInTheDocument()

    await i18n.changeLanguage('zh')
    await waitFor(() => {
      expect(screen.getAllByText('已停用')).not.toHaveLength(0)
      expect(screen.getByText('已撤回')).toBeInTheDocument()
      expect(screen.getByText('支持')).toBeInTheDocument()
    })
  })

  it('新增结构化事实并刷新数据库记录', async () => {
    const user = userEvent.setup()
    renderTab()

    await user.click(await screen.findByRole('button', { name: '新增事实' }))
    await user.type(screen.getByLabelText('归属 ID'), 'person-1')
    await user.type(screen.getByLabelText('事实键'), 'favorite_drink')
    await user.type(screen.getByLabelText('事实内容'), '咖啡')
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(createFactMock).toHaveBeenCalledWith(
        expect.objectContaining({
          scope_type: 'person',
          scope_id: 'person-1',
          fact_key: 'favorite_drink',
          value_text: '咖啡',
          profile_section: 'stable_facts',
        })
      )
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
    await waitFor(() => expect(screen.getByRole('button', { name: '恢复' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '恢复' }))
    await waitFor(() => {
      expect(restoreFactMock).toHaveBeenCalledWith('fact-1', 'knowledge_base_fact_restore')
    })
  })
})
