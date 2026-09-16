import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import userEvent from '@testing-library/user-event'

import { backendApi } from '@/lib/http'

import { ReplyEffectsPage } from './reply-effects'
import { ReplyEffectsBrowser } from './reply-effects-browser'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('recharts', async (importOriginal) => {
  const React = await import('react')
  const actual = await importOriginal<typeof import('recharts')>()
  // 直接复用 recharts 的 formatter 类型：桩组件若自行收窄参数，props 将无法传给真实 Tooltip
  type TooltipFormatter = NonNullable<React.ComponentProps<typeof actual.Tooltip>['formatter']>
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => (
      <div data-testid="chart-responsive" style={{ width: 800, height: 300 }}>
        {React.isValidElement(children)
          ? React.cloneElement(children as React.ReactElement<{ width?: number; height?: number }>, {
              width: 800,
              height: 300,
            })
          : children}
      </div>
    ),
    // 按 recharts 的真实调用约定补全 index / payload，以便覆盖页面 formatter 的各个分支
    Tooltip: (props: { formatter?: TooltipFormatter }) => {
      props.formatter?.(52, '评分', { payload: { label: 'model-a', sample: 1 } }, 0, [])
      props.formatter?.(40, '评分', { payload: {} }, 1, [])
      props.formatter?.(1, 'row', { payload: {} }, 2, [])
      return React.createElement(actual.Tooltip, props)
    },
  }
})

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

const versionAggregates = [
  {
    name: 'model-a · prompt-a',
    count: 12,
    response_score: 52,
    response_score_std: 8,
    reception_counts: { appreciation: 5, neutral: 2 },
    reception_record_count: 6,
    conversation_score: 50,
    conversation_score_std: 6,
    confidence: 0.8,
    confidence_std: 0.05,
    model_name: 'model-a',
    prompt_fingerprint: 'prompt-a',
    evaluation_version: 5,
    model_names: ['model-a'],
    prompt_fingerprints: ['prompt-a'],
    evaluation_versions: [5],
    first_seen: '2026-01-01T00:00:00',
    last_seen: '2026-01-02T00:00:00',
    collapsed_models: false,
    collapsed_versions: false,
    score_distributions: {
      response_score: { sample_count: 3, values: [0, 52, 76] },
      conversation_score: { sample_count: 3, values: [0, 34, 58] },
    },
  },
  {
    name: 'model-b · prompt-b',
    count: 10,
    response_score: 66,
    response_score_std: 7,
    reception_counts: { appreciation: 3, neutral: 4 },
    reception_record_count: 5,
    conversation_score: 58,
    conversation_score_std: 7,
    confidence: 0.82,
    confidence_std: 0.04,
    model_name: 'model-b',
    prompt_fingerprint: 'prompt-b',
    evaluation_version: 5,
    model_names: ['model-b'],
    prompt_fingerprints: ['prompt-b'],
    evaluation_versions: [5],
    first_seen: '2026-01-01T00:00:00',
    last_seen: '2026-01-02T00:00:00',
    collapsed_models: false,
    collapsed_versions: false,
    score_distributions: {
      response_score: { sample_count: 3, values: [0, 66, 82] },
      conversation_score: { sample_count: 3, values: [0, 40, 65] },
    },
  },
]

describe('ReplyEffectsPage', () => {
  beforeEach(() => {
    vi.mocked(backendApi.get).mockImplementation((path: string) => {
      if (path.includes('/overview')) {
        return Promise.resolve({
          summary: {
            count: 1,
            response_score: 80,
            reception_counts: { appreciation: 1 },
            reception_record_count: 1,
            conversation_score: 60,
            confidence: 0.8,
          },
          strategies: [
            {
              name: 'answer',
              count: 1,
              response_score: 80,
              reception_counts: { appreciation: 1 },
              reception_record_count: 1,
              conversation_score: 60,
              confidence: 0.8,
            },
          ],
          versions: versionAggregates,
          trend: [],
          filters: { sessions: [['s1', '测试群']], strategies: ['answer'], models: [] },
        }) as never
      }
      if (path.endsWith('/e1')) {
        return Promise.resolve({
          effect_id: 'e1',
          status: 'finalized',
          created_at: '2026-01-01T00:00:00',
          finalized_at: '2026-01-01T00:10:00',
          finalize_reason: 'session_followups_limit',
          evaluation_error: '',
          evaluation_version: 5,
          session: { session_name: '测试群' },
          reply: {
            target_message_id: '-1085252920',
            reply_text: '你好',
            model_name: 'test',
            request_fingerprint: 'request123',
            prompt_fingerprint: 'prompt123',
          },
          scores: {
            response_score: 0,
            reception_categories: [],
            reception_counts: {},
            conversation_score: 0,
            confidence: null,
          },
          context_snapshot: [
            {
              message_id: '-1085252920',
              source: 'user',
              role: 'user',
              timestamp: '2026-08-06T19:51:07',
              text: '19:51:07[msg_id:-1085252920][花生]怎么操作呀？',
            },
          ],
          followup_messages: [
            {
              message_id: 'followup-1',
              timestamp: '2026-08-06T19:51:15',
              user_id: '10002',
              nickname: '明光',
              cardname: '',
              visible_text: '应该只有群里有吧',
              reply_to: '',
              associations: [],
            },
          ],
          followup_summary: { total_count: 1, associated_count: 0, participant_count: 1 },
        }) as never
      }
      return Promise.resolve({
        total: 2,
        next_cursor: null,
        items: [
          {
            effect_id: 'e1',
            session_name: '测试群',
            status: 'finalized',
            created_at: '2026-01-01T00:00:00',
            finalize_reason: 'session_followups_limit',
            strategy_primary: 'answer',
            model_name: 'test',
            evaluation_version: 5,
            reply_text: '你好',
            response_score: 80,
            reception_categories: ['appreciation'],
            reception_counts: { appreciation: 1 },
            conversation_score: 60,
            confidence: 0.8,
            evaluation_error: '',
          },
          {
            effect_id: 'e2',
            session_name: '测试群',
            status: 'incomplete',
            created_at: '2026-01-01T00:05:00',
            finalize_reason: 'runtime_stop',
            strategy_primary: 'other',
            model_name: 'test',
            evaluation_version: 5,
            reply_text: '观察被中断',
            response_score: null,
            reception_categories: [],
            reception_counts: {},
            conversation_score: null,
            confidence: null,
            evaluation_error: '',
          },
        ],
      }) as never
    })
    vi.mocked(backendApi.post).mockResolvedValue({
      method: 'two_sided_welch_t_test',
      alpha: 0.05,
      left: { name: 'model-a · 版本 1', record_count: 12 },
      right: { name: 'model-b · 版本 1', record_count: 10 },
      significant_count: 1,
      metrics: [
        {
          field: 'response_score',
          label: '回应度',
          left_count: 12,
          right_count: 10,
          left_mean: 52,
          right_mean: 66,
          mean_difference: -14,
          confidence_interval: [-20.2, -7.8],
          p_value: 0.0123,
          significant: true,
          hedges_g: -0.72,
          sufficient: true,
          reason: '',
        },
      ],
    } as never)
  })

  it('展示分析视图和三维分数', async () => {
    render(<ReplyEffectsPage />)
    await waitFor(() => expect(backendApi.get).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: '回复效果评估' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新数据' })).toBeInTheDocument()
    expect(screen.getAllByText('回应度').length).toBeGreaterThan(0)
    expect(screen.getAllByText('反馈倾向').length).toBeGreaterThan(0)
    expect(screen.getByText('聊天推动度')).toBeInTheDocument()
    expect(screen.getAllByText('80.0').length).toBeGreaterThan(0)
    expect(screen.getAllByText('60.0').length).toBeGreaterThan(0)
    expect(screen.getByText('回应度分布')).toBeInTheDocument()
    expect(screen.getAllByText(/每个点代表一条实际评分/).length).toBe(2)

    const requestPaths = vi.mocked(backendApi.get).mock.calls.map(([path]) => path)
    expect(requestPaths.find((path) => path.includes('/overview'))).toContain('min_confidence=0')
  })

  it('以结构化消息样式展示评估上下文且不显示消息 ID', async () => {
    render(<ReplyEffectsBrowser refreshToken={0} />)

    expect(await screen.findByText('花生')).toBeInTheDocument()
    expect(screen.getByText('评估对话时间线')).toBeInTheDocument()
    expect(screen.getByText('怎么操作呀？')).toBeInTheDocument()
    expect(screen.getByText('本次回复')).toBeInTheDocument()
    expect(screen.getByText('明光')).toBeInTheDocument()
    expect(screen.getByText('应该只有群里有吧')).toBeInTheDocument()
    expect(screen.getByText('目标消息')).toBeInTheDocument()
    expect(screen.getByText('评估标准 v5')).toBeInTheDocument()
    expect(screen.getByText('已完成 / 无信息')).toBeInTheDocument()
    expect(screen.getByText('已完成观察，未发现与本次回复相关的后续信息。')).toBeInTheDocument()
    expect(screen.getAllByText('不完整').length).toBeGreaterThan(0)
    expect(screen.queryByText(/msg_id:/)).not.toBeInTheDocument()
  })

  it('对任意两个版本项目执行显著性检验并展示结论', async () => {
    render(<ReplyEffectsPage />)

    const compareButton = await screen.findByRole('button', { name: '计算显著性' })
    fireEvent.click(compareButton)

    expect(await screen.findByText('发现 1 项显著差异')).toBeInTheDocument()
    expect(screen.getByText('0.0123')).toBeInTheDocument()
    expect(screen.getByText('显著')).toBeInTheDocument()
    expect(backendApi.post).toHaveBeenCalledWith('/api/webui/reply-effects/compare', {
      body: expect.objectContaining({
        left: expect.objectContaining({
          model_names: ['model-a'],
          evaluation_versions: [5],
        }),
        right: expect.objectContaining({
          model_names: ['model-b'],
          evaluation_versions: [5],
        }),
        min_confidence: 0,
      }),
    })
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function setScrollMetrics(
  element: HTMLElement,
  metrics: { scrollHeight: number; scrollTop: number; clientHeight: number }
) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: metrics.scrollHeight })
  Object.defineProperty(element, 'scrollTop', { configurable: true, value: metrics.scrollTop })
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: metrics.clientHeight })
}

function getPaths() {
  return vi.mocked(backendApi.get).mock.calls.map(([path]) => String(path))
}

function lastOverviewPath() {
  const path = getPaths().filter((item) => item.includes('/overview')).at(-1) ?? ''
  return decodeURIComponent(path)
}

async function findVisibleText(text: string | RegExp) {
  const nodes = await screen.findAllByText(text)
  expect(nodes.length).toBeGreaterThan(0)
  return nodes
}

function clickTableText(text: string) {
  const match = screen.getAllByText(text).find((el) => el.closest('tr'))
  expect(match).toBeTruthy()
  fireEvent.click(match as HTMLElement)
}

function makeVersion(overrides: Record<string, unknown> = {}) {
  return {
    ...versionAggregates[0],
    ...overrides,
  }
}

const extraVersion = makeVersion({
  name: 'model-c · prompt-c',
  count: 8,
  response_score: 40,
  model_name: 'model-c',
  prompt_fingerprint: 'prompt-c',
  model_names: ['model-c'],
  prompt_fingerprints: ['prompt-c'],
  score_distributions: {
    response_score: { sample_count: 2, values: [20, 40] },
    conversation_score: { sample_count: 2, values: [10, 30] },
  },
})

const defaultFilters = {
  sessions: [['s1', '测试群'] as [string, string], ['s2', '私聊'] as [string, string]],
  strategies: ['answer'],
  models: ['model-a', 'gpt-test'],
}

function makeOverview(overrides: Record<string, unknown> = {}) {
  return {
    summary: {
      count: 3,
      response_score: 80,
      response_score_std: 4,
      reception_counts: { appreciation: 2, mystery_mood: 5 },
      reception_record_count: 2,
      conversation_score: 60,
      conversation_score_std: 3,
      confidence: 0.8,
      confidence_std: 0.05,
    },
    strategies: [
      {
        name: 'answer',
        count: 2,
        response_score: 80,
        response_score_std: null,
        reception_counts: {},
        reception_record_count: 0,
        conversation_score: null,
        confidence: null,
      },
      {
        name: 'weird-strategy',
        count: 1,
        response_score: 10,
        reception_counts: { playful: 1 },
        reception_record_count: 1,
        conversation_score: 20,
        confidence: 0.2,
      },
    ],
    versions: [...versionAggregates, extraVersion],
    trend: [
      {
        name: '01-01',
        count: 1,
        response_score: 50,
        reception_counts: {},
        reception_record_count: 0,
        conversation_score: 40,
        confidence: 0.5,
      },
    ],
    filters: defaultFilters,
    ...overrides,
  }
}

function makeRecord(overrides: Record<string, unknown> = {}) {
  return {
    effect_id: 'e-rich',
    session_name: '测试群',
    status: 'finalized',
    created_at: '2026-01-01T00:00:00',
    finalize_reason: 'session_followups_limit',
    strategy_primary: 'answer',
    model_name: 'test',
    evaluation_version: 5,
    reply_text: '你好世界',
    response_score: 80,
    reception_categories: ['appreciation'],
    reception_counts: { appreciation: 1 },
    conversation_score: 60,
    confidence: 0.8,
    evaluation_error: '',
    ...overrides,
  }
}

function makeRecords(items: ReturnType<typeof makeRecord>[], nextCursor: number | null = null) {
  return { total: items.length, next_cursor: nextCursor, items }
}

function makeContextMessage(
  overrides: Record<string, unknown> = {},
  index = 0
): Record<string, unknown> {
  return {
    message_id: `msg-${index}`,
    source: 'user',
    role: 'user',
    timestamp: '2026-01-01T00:00:00',
    text: `内容${index}`,
    ...overrides,
  }
}

function makeRichDetail(overrides: Record<string, unknown> = {}) {
  const filler = Array.from({ length: 20 }, (_, index) => {
    if (index === 0) {
      return makeContextMessage(
        { source: 'guided_reply', role: 'assistant', text: '引导回复', sender: { display_name: '麦麦引导' } },
        index
      )
    }
    if (index === 1) {
      return makeContextMessage({ source: 'system', role: 'reasoning', text: '推理过程' }, index)
    }
    if (index === 2) {
      return makeContextMessage({ source: 'system', role: 'assistant', text: '助手旁白' }, index)
    }
    if (index === 3) {
      return makeContextMessage({ source: 'note', role: 'system', text: '参考资料' }, index)
    }
    if (index === 4) {
      return makeContextMessage({ text: '', sender: { display_name: '空内容用户' } }, index)
    }
    if (index === 5) {
      return makeContextMessage({ text: '原始文本', display_text: '展示文本' }, index)
    }
    if (index === 6) {
      return makeContextMessage(
        { text: '只有头像', sender: { avatar_url: 'http://example.test/a.png', user_id: '99' } },
        index
      )
    }
    if (index === 7) {
      return makeContextMessage(
        { text: '仅名片', sender: { cardname: '名片用户', user_id: '88' } },
        index
      )
    }
    if (index === 8) {
      return makeContextMessage({ text: '仅昵称', sender: { nickname: '昵称用户' } }, index)
    }
    if (index === 9) {
      return makeContextMessage(
        { text: '无身份头像', sender: { avatar_url: 'http://example.test/z.png' } },
        index
      )
    }
    if (index === 10) {
      return makeContextMessage({ text: '无时间戳', timestamp: '' }, index)
    }
    return makeContextMessage({ text: `填充${index}` }, index)
  })
  const target = makeContextMessage(
    {
      message_id: 'target-1',
      text: '最早的目标',
      sender: { display_name: '目标用户', avatar_url: 'http://example.test/t.png', user_id: '1' },
    },
    -1
  )

  return {
    effect_id: 'e-rich',
    status: 'finalized',
    created_at: '2026-01-01T00:00:00',
    finalized_at: '2026-01-01T00:10:00',
    finalize_reason: 'session_followups_limit',
    evaluation_error: '评分警告：证据偏少',
    confidence_note: '后续样本较少',
    evaluation_version: 5,
    pre_activity_count: 2,
    pre_activity_bucket: 'few',
    session: { session_name: '测试群' },
    target_user: { nickname: '花生', cardname: '花名片', user_id: '10001' },
    reply: {
      target_message_id: 'target-1',
      reply_text: '你好世界',
      model_name: 'test',
      strategy_primary: 'answer',
      strategy_secondary: ['humor'],
      strategy_confidence: 0.9,
    },
    scores: {
      response_score: 80,
      reception_categories: ['appreciation', 'custom_mood'],
      reception_counts: { appreciation: 1 },
      conversation_score: 60,
      confidence: 0.66,
      response_evidence_confidence: 0.7,
      reception_evidence_confidence: 0.5,
      conversation_evidence_confidence: 0.4,
    },
    context_snapshot: [target, ...filler],
    followup_messages: [
      {
        message_id: 'follow-associated',
        timestamp: '2026-01-01T00:11:00',
        user_id: '10002',
        nickname: '明光',
        cardname: '明卡片',
        visible_text: '说得对',
        reply_to: '',
        avatar_url: 'http://example.test/m.png',
        associations: [
          {
            effect_id: 'e-rich',
            attribution_type: 'direct',
            attribution_confidence: 0.7,
            stance_target: 'bot_content',
            stance: 'appreciation',
            contribution: 'advance',
            reason: '夸了回复',
            evidence_spans: ['好', '不错'],
            evaluator_confidence: 0.8,
          },
          {
            effect_id: 'e-rich',
            attribution_type: 'weak',
            attribution_confidence: 0.2,
            stance_target: 'custom_target',
            stance: 'custom_stance',
            contribution: 'custom_contrib',
            reason: '',
            evidence_spans: [],
            evaluator_confidence: 0.1,
          },
        ],
      },
      {
        message_id: 'follow-plain',
        timestamp: '2026-01-01T00:12:00',
        nickname: '路人',
        cardname: '',
        visible_text: '',
        reply_to: '',
        associations: [],
      },
    ],
    followup_summary: { total_count: 2, associated_count: 1, participant_count: 2 },
    ...overrides,
  }
}

const defaultPromptDetail = {
  prompt_fingerprint: 'prompt-a',
  evaluation_version: 5,
  model_name: 'model-a',
  sample_count: 12,
  first_seen: '2026-01-01T00:00:00',
  last_seen: '2026-01-02T00:00:00',
  sessions: [
    { session_id: 's1', session_name: '测试群', sample_count: 8, last_seen: '2026-01-02T00:00:00' },
    { session_id: 's2', session_name: '私聊', sample_count: 4, last_seen: '2026-01-01T12:00:00' },
  ],
  selected_session_id: 's1',
  system_prompt: 'system a',
  current_prompt_fingerprint: 'prompt-current',
  current_system_prompt: 'system current',
  current_created_at: '2026-01-03T00:00:00',
  is_current: true,
  diff_lines: ['--- a', '+++ b', '@@ -1,2 +1,2 @@', '-old', '+new', ' context', ''],
}

const mixedCompareResult = {
  method: 'two_sided_welch_t_test' as const,
  alpha: 0.05,
  left: { name: 'A', record_count: 12 },
  right: { name: 'B', record_count: 10 },
  significant_count: 0,
  metrics: [
    {
      field: 'response_score',
      label: '回应度',
      left_count: 12,
      right_count: 10,
      left_mean: 52,
      right_mean: 66,
      mean_difference: 3.5,
      confidence_interval: [Number.NaN, 1] as [number, number],
      p_value: 0.00001,
      significant: false,
      hedges_g: 0.1,
      sufficient: true,
      reason: '',
    },
    {
      field: 'conversation_score',
      label: '推动度',
      left_count: 2,
      right_count: 2,
      left_mean: Number.NaN,
      right_mean: Number.POSITIVE_INFINITY,
      mean_difference: Number.NaN,
      confidence_interval: null,
      p_value: Number.NaN,
      significant: false,
      hedges_g: 0.3,
      sufficient: false,
      reason: '样本量不足',
    },
    {
      field: 'confidence',
      label: '置信度',
      left_count: 12,
      right_count: 10,
      left_mean: 0.8,
      right_mean: 0.7,
      mean_difference: -0.1,
      confidence_interval: [-0.2, 0.0] as [number, number],
      p_value: 0.4,
      significant: false,
      hedges_g: 0.9,
      sufficient: true,
      reason: '',
    },
    {
      field: 'empty',
      label: '空指标',
      left_count: 0,
      right_count: 0,
      left_mean: null,
      right_mean: null,
      mean_difference: null,
      confidence_interval: null,
      p_value: null,
      significant: false,
      hedges_g: Number.NaN,
      sufficient: false,
      reason: '',
    },
  ],
}

const defaultBrowserRecords = makeRecords(
  [
    makeRecord(),
    makeRecord({
      effect_id: 'e-incomplete',
      status: 'incomplete',
      reply_text: '观察被中断',
      response_score: null,
      reception_categories: [],
      conversation_score: null,
      confidence: null,
      strategy_primary: 'other',
    }),
    makeRecord({
      effect_id: 'e-failed',
      status: 'evaluation_failed',
      reply_text: '',
      evaluation_error: '模型超时',
      confidence: null,
      reception_categories: ['mystery_cat'],
      strategy_primary: 'unknown-strategy',
    }),
    makeRecord({
      effect_id: 'e-pending',
      status: 'pending',
      reply_text: '等待中',
      confidence: null,
    }),
    makeRecord({
      effect_id: 'e-evaluating',
      status: 'evaluating',
      reply_text: '评估中',
      confidence: null,
    }),
    makeRecord({
      effect_id: 'e-unknown',
      status: 'mystery',
      reply_text: '',
      evaluation_error: '',
      confidence: null,
    }),
    makeRecord({
      effect_id: 'e-hot',
      reply_text: '高置信',
      confidence: 1.2,
    }),
    makeRecord({
      effect_id: 'e-cold',
      reply_text: '负置信',
      confidence: -0.2,
    }),
  ],
  null
)

function detailFor(id: string) {
  if (id === 'e-incomplete') {
    return makeRichDetail({
      effect_id: 'e-incomplete',
      status: 'incomplete',
      evaluation_error: '',
      scores: undefined,
      reply: { target_message_id: 'missing', reply_text: '', model_name: '', strategy_primary: undefined },
      target_user: {},
      session: {},
      finalize_reason: '',
      context_snapshot: undefined,
      followup_messages: undefined,
      followup_summary: undefined,
      pre_activity_count: undefined,
    })
  }
  if (id === 'e-failed') {
    return makeRichDetail({
      effect_id: 'e-failed',
      status: 'evaluation_failed',
      scores: undefined,
      evaluation_error: '模型超时',
    })
  }
  if (id === 'e-empty-score') {
    return makeRichDetail({
      effect_id: 'e-empty-score',
      status: 'finalized',
      scores: undefined,
      evaluation_error: '',
    })
  }
  if (id === 'e-no-reception') {
    return makeRichDetail({
      effect_id: 'e-no-reception',
      scores: {
        response_score: 10,
        reception_categories: [],
        reception_counts: {},
        conversation_score: 20,
        confidence: 0.5,
        response_evidence_confidence: 0.4,
        reception_evidence_confidence: 0.1,
        conversation_evidence_confidence: 0.2,
      },
      confidence_note: '',
      evaluation_error: '',
    })
  }
  return makeRichDetail({ effect_id: id })
}

function mockBackend(handlers: {
  overview?: unknown | ((path: string) => unknown)
  records?: unknown | ((path: string) => unknown)
  detail?: unknown | ((id: string) => unknown)
  prompt?: unknown | ((path: string) => unknown)
  exportBlob?: Blob
  compare?: unknown
  importResult?: unknown
  clearResult?: unknown
  overviewError?: unknown
  recordsError?: unknown
  detailError?: unknown
  promptError?: unknown
  compareError?: unknown
  importError?: unknown
  exportError?: unknown
  clearError?: unknown
} = {}) {
  vi.mocked(backendApi.get).mockImplementation((path: string) => {
    if (path.includes('/overview')) {
      if (handlers.overviewError) return Promise.reject(handlers.overviewError)
      const value =
        typeof handlers.overview === 'function'
          ? handlers.overview(path)
          : (handlers.overview ?? makeOverview())
      return Promise.resolve(value) as never
    }
    if (path.includes('/export')) {
      if (handlers.exportError) return Promise.reject(handlers.exportError)
      return Promise.resolve(handlers.exportBlob ?? new Blob(['gzip-data'])) as never
    }
    if (path.includes('/prompt-versions/')) {
      if (handlers.promptError) return Promise.reject(handlers.promptError)
      const value =
        typeof handlers.prompt === 'function'
          ? handlers.prompt(path)
          : (handlers.prompt ?? defaultPromptDetail)
      return Promise.resolve(value) as never
    }
    const detailMatch = path.match(/\/reply-effects\/([^/?]+)$/)
    if (detailMatch) {
      if (handlers.detailError) return Promise.reject(handlers.detailError)
      const value =
        typeof handlers.detail === 'function'
          ? handlers.detail(detailMatch[1])
          : (handlers.detail ?? detailFor(detailMatch[1]))
      return Promise.resolve(value) as never
    }
    if (handlers.recordsError) return Promise.reject(handlers.recordsError)
    const value =
      typeof handlers.records === 'function'
        ? handlers.records(path)
        : (handlers.records ?? defaultBrowserRecords)
    return Promise.resolve(value) as never
  })
  vi.mocked(backendApi.post).mockImplementation((path: string) => {
    if (path.includes('/compare')) {
      if (handlers.compareError) return Promise.reject(handlers.compareError)
      return Promise.resolve(handlers.compare ?? mixedCompareResult) as never
    }
    if (path.includes('/import')) {
      if (handlers.importError) return Promise.reject(handlers.importError)
      return Promise.resolve(
        handlers.importResult ?? { total: 4, imported: 3, skipped: 1, conflicts: 0 }
      ) as never
    }
    return Promise.resolve({}) as never
  })
  vi.mocked(backendApi.delete).mockImplementation(() => {
    if (handlers.clearError) return Promise.reject(handlers.clearError)
    return Promise.resolve(
      handlers.clearResult ?? {
        deleted_records: 9,
        deleted_mirrors: 2,
        cleared_trackers: 1,
        space_reclaimed: true,
      }
    ) as never
  })
}

async function selectOption(user: ReturnType<typeof userEvent.setup>, label: string, optionName: string | RegExp) {
  await user.click(screen.getByRole('combobox', { name: label }))
  await user.click(await screen.findByRole('option', { name: optionName }))
}

describe('ReplyEffectsPage 空数据、错误与筛选折叠', () => {
  afterEach(() => {
    cleanup()
    toastMock.mockReset()
  })

  it('空统计展示占位，并处理空分数与未知分类', async () => {
    mockBackend({
      overview: makeOverview({
        summary: {
          count: 0,
          response_score: null,
          reception_counts: {},
          reception_record_count: 0,
          conversation_score: null,
          confidence: null,
        },
        strategies: [],
        versions: [],
        trend: [],
      }),
    })
    render(<ReplyEffectsPage />)

    expect(await screen.findByText('当前统计门槛下暂无已完成记录')).toBeInTheDocument()
    expect(screen.getByText('暂无可对比的策略数据')).toBeInTheDocument()
    expect(screen.getByText('暂无模型版本统计')).toBeInTheDocument()
    expect(screen.getByText('有已完成记录后，这里会展示分数趋势')).toBeInTheDocument()
    expect(screen.getAllByText('暂无可绘制的分数').length).toBe(2)
    expect(screen.getByText('当前筛选与合并方式下至少需要两个项目')).toBeInTheDocument()
    expect(screen.getByText('无情绪证据')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('加载失败区分 Error 与非 Error', async () => {
    mockBackend({ overviewError: new Error('网络断开') })
    const { unmount } = render(<ReplyEffectsPage />)
    expect(await screen.findByText('网络断开')).toBeInTheDocument()
    unmount()
    cleanup()

    mockBackend({ overviewError: 'offline' })
    render(<ReplyEffectsPage />)
    expect(await screen.findByText('加载回复效果数据失败')).toBeInTheDocument()
  })

  it('筛选聊天流、策略、日期和置信度后可重置，并带入显著性请求', async () => {
    const user = userEvent.setup()
    mockBackend()
    render(<ReplyEffectsPage />)
    await screen.findByRole('button', { name: '计算显著性' })

    expect(screen.getByText('weird-strategy')).toBeInTheDocument()
    expect(screen.getByText(/mystery_mood 5/)).toBeInTheDocument()
    expect(screen.getByText('80.0 ± 4.0')).toBeInTheDocument()

    await selectOption(user, '聊天流', '测试群')
    await selectOption(user, '回复策略', '信息回答')
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-01-31' } })
    fireEvent.change(screen.getByLabelText('统计最低置信度'), { target: { value: '0.4' } })

    await waitFor(() => {
      const path = lastOverviewPath()
      expect(path).toContain('session_id=s1')
      expect(path).toContain('strategy=answer')
      expect(path).toContain('start_at=2026-01-01T00:00:00')
      expect(path).toContain('end_at=2026-01-31T23:59:59')
      expect(path).toContain('min_confidence=0.4')
    })

    fireEvent.click(await screen.findByRole('button', { name: '计算显著性' }))
    await waitFor(() => {
      expect(backendApi.post).toHaveBeenCalledWith(
        '/api/webui/reply-effects/compare',
        expect.objectContaining({
          body: expect.objectContaining({
            session_id: 's1',
            strategy: 'answer',
            start_at: '2026-01-01T00:00:00',
            end_at: '2026-01-31T23:59:59',
            min_confidence: 0.4,
          }),
        })
      )
    })

    await user.click(screen.getByRole('button', { name: '重置' }))
    await waitFor(() => {
      const path = lastOverviewPath()
      expect(path).not.toContain('session_id=')
      expect(path).not.toContain('strategy=answer')
      expect(path).toContain('min_confidence=0')
    })
  })

  it('折叠版本和模型会切换聚合名称', async () => {
    const user = userEvent.setup()
    mockBackend({
      overview: (path: string) => {
        const collapseVersions = path.includes('collapse_versions=true')
        const collapseModels = path.includes('collapse_models=true')
        if (collapseVersions && collapseModels) {
          return makeOverview({
            versions: [
              makeVersion({
                name: 'all-all',
                collapsed_models: true,
                collapsed_versions: true,
                model_name: 'kept',
              }),
            ],
          })
        }
        if (collapseVersions) {
          return makeOverview({
            versions: [
              makeVersion({
                name: 'unknown-all-versions',
                collapsed_versions: true,
                collapsed_models: false,
                model_name: '',
              }),
              makeVersion({
                name: 'named-all-versions',
                collapsed_versions: true,
                collapsed_models: false,
                model_name: 'model-z',
                prompt_fingerprint: 'prompt-z',
              }),
            ],
          })
        }
        if (collapseModels) {
          return makeOverview({
            versions: [
              makeVersion({
                name: 'all-models-v1',
                collapsed_models: true,
                collapsed_versions: false,
                prompt_fingerprint: 'p1',
                prompt_fingerprints: ['p1'],
              }),
              makeVersion({
                name: 'all-models-v2',
                collapsed_models: true,
                collapsed_versions: false,
                prompt_fingerprint: 'p2',
                prompt_fingerprints: ['p2'],
              }),
            ],
          })
        }
        return makeOverview({
          versions: [
            makeVersion({ prompt_fingerprint: '', model_name: '', model_names: [] }),
            extraVersion,
            makeVersion({
              name: 'no-dist',
              model_name: 'model-empty',
              prompt_fingerprint: 'prompt-empty',
              model_names: ['model-empty'],
              prompt_fingerprints: ['prompt-empty'],
              score_distributions: {
                response_score: { sample_count: 0, values: [] },
              },
            }),
          ],
        })
      },
    })
    render(<ReplyEffectsPage />)

    await findVisibleText('未知模型 · 版本 1 · 评估标准 v5')
    clickTableText('未知模型 · 版本 1 · 评估标准 v5')
    expect(getPaths().some((path) => path.includes('/prompt-versions/'))).toBe(false)

    await user.click(screen.getByRole('button', { name: '合并不同版本' }))
    await findVisibleText('未知模型 · 全部版本 · 评估标准 v5')
    await findVisibleText('model-z · 全部版本 · 评估标准 v5')

    await user.click(screen.getByRole('button', { name: '显示不同版本' }))
    await user.click(await screen.findByRole('button', { name: '合并不同模型' }))
    await findVisibleText('全部模型 · 版本 1 · 评估标准 v5')
    await findVisibleText('全部模型 · 版本 2 · 评估标准 v5')

    await user.click(screen.getByRole('button', { name: '合并不同版本' }))
    await findVisibleText('全部模型 · 全部版本 · 评估标准 v5')
    expect(screen.getByText('当前筛选与合并方式下至少需要两个项目')).toBeInTheDocument()
  })
})

describe('ReplyEffectsPage 导入导出清空、浏览切换与 Prompt 详情', () => {
  afterEach(() => {
    cleanup()
    toastMock.mockReset()
  })

  it('切换评估浏览并刷新会重新拉取列表', async () => {
    const user = userEvent.setup()
    mockBackend()
    render(<ReplyEffectsPage />)
    await screen.findByRole('button', { name: '刷新数据' })

    await user.click(screen.getByRole('tab', { name: '评估浏览' }))
    expect(await screen.findByText('筛选与排序')).toBeInTheDocument()
    await findVisibleText('你好世界')

    const callsBefore = vi.mocked(backendApi.get).mock.calls.length
    await user.click(screen.getByRole('button', { name: '刷新数据' }))
    await waitFor(() => {
      expect(vi.mocked(backendApi.get).mock.calls.length).toBeGreaterThan(callsBefore)
      expect(getPaths().filter((path) => path.includes('/overview')).length).toBeGreaterThan(1)
      expect(getPaths().some((path) => path.includes('/reply-effects?'))).toBe(true)
    })
  })

  it('导出成功与失败会提示', async () => {
    const user = userEvent.setup()
    mockBackend()
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:maibot-test/export')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    render(<ReplyEffectsPage />)
    await screen.findByRole('button', { name: '导出评分' })

    await user.click(screen.getByRole('button', { name: '导出评分' }))
    await waitFor(() => {
      expect(backendApi.get).toHaveBeenCalledWith('/api/webui/reply-effects/export', {
        parse: 'blob',
      })
      expect(toastMock).toHaveBeenCalledWith({
        title: '导出成功',
        description: '已导出全部回复效果评分数据',
      })
    })
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalled()

    mockBackend({ exportError: new Error('磁盘满了') })
    await user.click(screen.getByRole('button', { name: '导出评分' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导出失败',
        description: '磁盘满了',
        variant: 'destructive',
      })
    })

    mockBackend({ exportError: 'nope' })
    await user.click(screen.getByRole('button', { name: '导出评分' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导出失败',
        description: '无法导出评分数据',
        variant: 'destructive',
      })
    })
    clickSpy.mockRestore()
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
  })

  it('导入无文件直接返回，成功区分冲突，失败区分 Error', async () => {
    const user = userEvent.setup()
    mockBackend()
    render(<ReplyEffectsPage />)
    await screen.findByRole('button', { name: '导入评分' })

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const clickSpy = vi.spyOn(input, 'click')
    await user.click(screen.getByRole('button', { name: '导入评分' }))
    expect(clickSpy).toHaveBeenCalled()

    fireEvent.change(input, { target: { files: [] } })
    expect(backendApi.post).not.toHaveBeenCalled()

    const file = new File(['{}'], 'scores.json.gz', { type: 'application/gzip' })
    await user.upload(input, file)
    await waitFor(() => {
      expect(backendApi.post).toHaveBeenCalledWith(
        '/api/webui/reply-effects/import',
        expect.objectContaining({ body: expect.any(FormData) })
      )
      expect(toastMock).toHaveBeenCalledWith({
        title: '导入完成',
        description: '新增 3 条，跳过 1 条相同记录',
      })
    })

    mockBackend({
      importResult: { total: 10, imported: 3, skipped: 5, conflicts: 2 },
    })
    await user.upload(input, file)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导入完成',
        description: '新增 3 条，跳过 5 条相同记录，2 条冲突未覆盖',
      })
    })

    mockBackend({ importError: new Error('格式错误') })
    await user.upload(input, file)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导入失败',
        description: '格式错误',
        variant: 'destructive',
      })
    })

    mockBackend({ importError: 'bad' })
    await user.upload(input, file)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '导入失败',
        description: '无法导入评分数据',
        variant: 'destructive',
      })
    })
    clickSpy.mockRestore()
  })

  it('清空评分成功与失败，并处理空间未回收文案', async () => {
    const user = userEvent.setup()
    mockBackend({
      clearResult: {
        deleted_records: 9,
        deleted_mirrors: 2,
        cleared_trackers: 1,
        space_reclaimed: false,
      },
    })
    render(<ReplyEffectsPage />)
    await screen.findByRole('button', { name: '清空评分' })

    await user.click(screen.getByRole('button', { name: '清空评分' }))
    expect(screen.getByText('确定清空全部评分数据？')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('确定清空全部评分数据？')).not.toBeInTheDocument()
    })
    expect(backendApi.delete).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '清空评分' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(backendApi.delete).toHaveBeenCalledWith('/api/webui/reply-effects/clear')
      expect(toastMock).toHaveBeenCalledWith({
        title: '评分已清空',
        description: '已删除 9 条评分和 2 个诊断镜像；数据库空页将在后续写入时复用',
      })
    })

    mockBackend({
      clearResult: {
        deleted_records: 1,
        deleted_mirrors: 0,
        cleared_trackers: 0,
        space_reclaimed: true,
      },
    })
    await user.click(screen.getByRole('button', { name: '清空评分' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '评分已清空',
        description: '已删除 1 条评分和 0 个诊断镜像',
      })
    })

    mockBackend({ clearError: new Error('无权清空') })
    await user.click(screen.getByRole('button', { name: '清空评分' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '清空失败',
        description: '无权清空',
        variant: 'destructive',
      })
    })

    mockBackend({ clearError: 'denied' })
    await user.click(screen.getByRole('button', { name: '清空评分' }))
    await user.click(screen.getByRole('button', { name: '确认清空' }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '清空失败',
        description: '无法清空评分数据',
        variant: 'destructive',
      })
    })
  })

  it('打开 Prompt 详情展示 diff，可切换聊天流并关闭；加载失败会提示', async () => {
    const user = userEvent.setup()
    const pending = deferred<typeof defaultPromptDetail>()
    mockBackend()
    vi.mocked(backendApi.get).mockImplementation((path: string) => {
      if (path.includes('/overview')) return Promise.resolve(makeOverview()) as never
      if (path.includes('/prompt-versions/')) return pending.promise as never
      return Promise.resolve(defaultBrowserRecords) as never
    })
    render(<ReplyEffectsPage />)
    await findVisibleText('model-a · 版本 1 · 评估标准 v5')
    clickTableText('model-a · 版本 1 · 评估标准 v5')
    expect(await screen.findByText('正在加载 Prompt…')).toBeInTheDocument()
    pending.resolve(defaultPromptDetail)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('当前版本')).toBeInTheDocument()
    expect(within(dialog).getByText('system a')).toBeInTheDocument()
    expect(within(dialog).getByText('--- a')).toBeInTheDocument()
    expect(within(dialog).getByText('+new')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('combobox', { name: 'Prompt 对比聊天流' }))
    await user.click(await screen.findByRole('option', { name: '私聊（4）' }))
    await waitFor(() => {
      expect(getPaths().some((path) => path.includes('/prompt-versions/') && path.includes('session_id=s2'))).toBe(
        true
      )
    })

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    mockBackend({
      prompt: {
        ...defaultPromptDetail,
        is_current: false,
        system_prompt: '',
        current_system_prompt: '',
        diff_lines: [],
      },
    })
    await findVisibleText('model-b · 版本 1 · 评估标准 v5')
    clickTableText('model-b · 版本 1 · 评估标准 v5')
    expect(await screen.findByText('该版本就是此聊天流当前使用的 Prompt，没有差异。')).toBeInTheDocument()
    expect(screen.getAllByText('未记录 System Prompt').length).toBe(2)
    expect(screen.queryByText('当前版本')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭' }))

    mockBackend({ promptError: new Error('Prompt 丢失') })
    await findVisibleText('model-a · 版本 1 · 评估标准 v5')
    clickTableText('model-a · 版本 1 · 评估标准 v5')
    expect(await screen.findByText('Prompt 丢失')).toBeInTheDocument()

    mockBackend({ promptError: 'missing' })
    await findVisibleText('model-c · 版本 1 · 评估标准 v5')
    clickTableText('model-c · 版本 1 · 评估标准 v5')
    expect(await screen.findByText('加载 Prompt 版本失败')).toBeInTheDocument()
  })
})

describe('ReplyEffectsPage 显著性对比与分布图交互', () => {
  afterEach(() => {
    cleanup()
    toastMock.mockReset()
  })

  it('展示多种对比结论，切换项目后结果失效，错误区分 Error', async () => {
    const user = userEvent.setup()
    const pending = deferred<typeof mixedCompareResult>()
    mockBackend({ compare: mixedCompareResult })
    vi.mocked(backendApi.post).mockImplementation(
      () => pending.promise as never
    )
    render(<ReplyEffectsPage />)
    const compareButton = await screen.findByRole('button', { name: '计算显著性' })
    fireEvent.click(compareButton)
    expect(await screen.findByText('计算中')).toBeInTheDocument()
    pending.resolve(mixedCompareResult)

    expect(await screen.findByText('未发现显著差异')).toBeInTheDocument()
    expect(screen.getByText('< 0.0001')).toBeInTheDocument()
    expect(screen.getByText('+3.50')).toBeInTheDocument()
    expect(screen.getByText('0.10（极小）')).toBeInTheDocument()
    expect(screen.getByText('0.30（小）')).toBeInTheDocument()
    expect(screen.getByText('0.90（大）')).toBeInTheDocument()
    expect(screen.getAllByText('未计算').length).toBeGreaterThan(0)
    expect(screen.getByText('样本量不足')).toBeInTheDocument()
    expect(screen.getAllByText('样本不足').length).toBeGreaterThan(0)
    expect(screen.getAllByText('不显著').length).toBeGreaterThan(0)

    await selectOption(user, '显著性对比项目 A', /model-c/)
    await waitFor(() => {
      expect(screen.queryByText('未发现显著差异')).not.toBeInTheDocument()
    })

    mockBackend({ compareError: new Error('对比失败') })
    fireEvent.click(screen.getByRole('button', { name: '计算显著性' }))
    expect(await screen.findByText('对比失败')).toBeInTheDocument()

    mockBackend({ compareError: 'compare-offline' })
    fireEvent.click(screen.getByRole('button', { name: '计算显著性' }))
    expect(await screen.findByText('显著性检验失败')).toBeInTheDocument()
  })

  it('分布图渲染图例，空分布显示占位，并可触发 tooltip', async () => {
    mockBackend({
      overview: makeOverview({
        versions: [
          makeVersion(),
          makeVersion({
            name: 'empty-dist',
            model_name: 'model-empty',
            prompt_fingerprint: 'prompt-empty',
            model_names: ['model-empty'],
            prompt_fingerprints: ['prompt-empty'],
            score_distributions: {
              response_score: { sample_count: 0, values: [] },
            },
          }),
        ],
      }),
    })
    const { container } = render(<ReplyEffectsPage />)
    expect(await screen.findByText('回应度分布')).toBeInTheDocument()
    expect(screen.getAllByText(/model-a · 版本 1 · 评估标准 v5/).length).toBeGreaterThan(0)

    const symbols = container.querySelectorAll('circle, .recharts-scatter-symbol, path.recharts-symbols')
    if (symbols.length > 0) {
      fireEvent.mouseOver(symbols[0])
      fireEvent.mouseMove(symbols[0])
      fireEvent.mouseEnter(symbols[0])
    }
    const tooltipLayer = container.querySelector('.recharts-tooltip-wrapper, .recharts-default-tooltip')
    if (tooltipLayer) {
      fireEvent.mouseOver(tooltipLayer)
    }
  })
})

describe('ReplyEffectsBrowser 筛选、分页、错误与详情分支', () => {
  afterEach(() => {
    cleanup()
    toastMock.mockReset()
  })

  it('空列表、列表错误和详情错误会展示对应文案', async () => {
    mockBackend({ records: makeRecords([]) })
    const { unmount } = render(
      <ReplyEffectsBrowser refreshToken={0} filters={defaultFilters} />
    )
    expect(await screen.findByText('没有符合条件的评估记录')).toBeInTheDocument()
    expect(screen.getByText('从左侧选择一条评估记录')).toBeInTheDocument()
    unmount()
    cleanup()

    mockBackend({ recordsError: new Error('列表挂了') })
    const second = render(<ReplyEffectsBrowser refreshToken={0} />)
    expect(await screen.findByText('列表挂了')).toBeInTheDocument()
    second.unmount()
    cleanup()

    mockBackend({ recordsError: 'list-offline' })
    const third = render(<ReplyEffectsBrowser refreshToken={0} />)
    expect(await screen.findByText('加载评估列表失败')).toBeInTheDocument()
    third.unmount()
    cleanup()

    mockBackend({
      records: makeRecords([makeRecord()]),
      detailError: new Error('详情挂了'),
    })
    const fourth = render(<ReplyEffectsBrowser refreshToken={0} />)
    expect(await screen.findByText('详情挂了')).toBeInTheDocument()
    fourth.unmount()
    cleanup()

    mockBackend({
      records: makeRecords([makeRecord()]),
      detailError: 'detail-offline',
    })
    render(<ReplyEffectsBrowser refreshToken={0} />)
    expect(await screen.findByText('加载评估详情失败')).toBeInTheDocument()
  })

  it('筛选、排序、重置会改查询，滚动到底加载更多并去重', async () => {
    const user = userEvent.setup()
    const onLoadingChange = vi.fn()
    mockBackend({
      records: (path: string) => {
        if (path.includes('cursor=30')) {
          return makeRecords(
            [
              makeRecord({ effect_id: 'e-rich', reply_text: '重复项' }),
              makeRecord({ effect_id: 'e-more', reply_text: '第二页新记录' }),
            ],
            null
          )
        }
        return { ...defaultBrowserRecords, next_cursor: 30, total: 10 }
      },
    })
    const { rerender } = render(
      <ReplyEffectsBrowser
        refreshToken={0}
        filters={defaultFilters}
        onLoadingChange={onLoadingChange}
      />
    )
    await findVisibleText('你好世界')
    expect(screen.getByText('模型超时')).toBeInTheDocument()
    expect(screen.getByText('无可见回复文本')).toBeInTheDocument()
    expect(screen.getAllByText('评估失败').length).toBeGreaterThan(0)
    expect(screen.getAllByText('等待结算').length).toBeGreaterThan(0)
    expect(screen.getAllByText('正在评估').length).toBeGreaterThan(0)
    expect(screen.getByText('mystery')).toBeInTheDocument()
    expect(screen.getByLabelText('置信度 100%')).toBeInTheDocument()
    expect(screen.getByLabelText('置信度 0%')).toBeInTheDocument()

    await selectOption(user, '浏览聊天流', '测试群')
    await selectOption(user, '评估状态', '已完成')
    await selectOption(user, '浏览回复策略', '信息回答')
    await selectOption(user, '浏览回复模型', 'gpt-test')
    fireEvent.change(screen.getByLabelText('浏览最低置信度'), { target: { value: '0.3' } })
    await selectOption(user, '评估排序字段', '按回应度')
    await user.click(screen.getByRole('button', { name: '从高到低' }))
    await waitFor(() => {
      const path = getPaths().filter((item) => item.includes('/reply-effects?')).at(-1) ?? ''
      expect(path).toContain('session_id=s1')
      expect(path).toContain('status=finalized')
      expect(path).toContain('strategy=answer')
      expect(path).toContain('model_name=gpt-test')
      expect(path).toContain('min_confidence=0.3')
      expect(path).toContain('sort_by=response_score')
      expect(path).toContain('sort_order=asc')
    })
    expect(screen.getByRole('button', { name: '从低到高' })).toBeInTheDocument()

    await selectOption(user, '评估排序字段', '按评估时间')
    expect(await screen.findByRole('button', { name: '最早在前' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '最早在前' }))
    expect(await screen.findByRole('button', { name: '最新在前' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置' }))
    await waitFor(() => {
      const path = getPaths().filter((item) => item.includes('/reply-effects?')).at(-1) ?? ''
      expect(path).not.toContain('session_id=')
      expect(path).toContain('sort_by=created_at')
      expect(path).toContain('sort_order=desc')
    })
    await selectOption(user, '浏览聊天流', '测试群')
    await selectOption(user, '浏览聊天流', '全部聊天流')
    await selectOption(user, '评估状态', '已完成')
    await selectOption(user, '评估状态', '全部状态')
    await waitFor(() => {
      const path = getPaths().filter((item) => item.includes('/reply-effects?')).at(-1) ?? ''
      expect(path).not.toContain('session_id=')
      expect(path).not.toContain('status=finalized')
    })

    const aside = screen.getByText(/共 \d+ 条评估/).closest('aside') as HTMLElement
    const scroller = aside.querySelector('.overflow-y-auto') as HTMLElement
    setScrollMetrics(scroller, { scrollHeight: 500, scrollTop: 0, clientHeight: 200 })
    fireEvent.scroll(scroller)
    expect(getPaths().some((path) => path.includes('cursor=30'))).toBe(false)

    const morePending = deferred<ReturnType<typeof makeRecords>>()
    const originalGet = vi.mocked(backendApi.get).getMockImplementation()
    vi.mocked(backendApi.get).mockImplementation((path: string) => {
      if (path.includes('cursor=30')) return morePending.promise as never
      return originalGet ? originalGet(path) : Promise.resolve(defaultBrowserRecords)
    })
    setScrollMetrics(scroller, { scrollHeight: 500, scrollTop: 400, clientHeight: 50 })
    fireEvent.scroll(scroller)
    fireEvent.scroll(scroller)
    expect(await screen.findByText('继续加载…')).toBeInTheDocument()
    expect(getPaths().filter((path) => path.includes('cursor=30')).length).toBe(1)
    morePending.resolve(
      makeRecords(
        [
          makeRecord({ effect_id: 'e-rich', reply_text: '重复项' }),
          makeRecord({ effect_id: 'e-more', reply_text: '第二页新记录' }),
        ],
        null
      )
    )
    expect(await screen.findByText('第二页新记录')).toBeInTheDocument()
    expect(screen.getAllByText('你好世界').length).toBeGreaterThan(0)

    const listCalls = getPaths().filter((path) => path.includes('/reply-effects?')).length
    rerender(
      <ReplyEffectsBrowser
        refreshToken={1}
        filters={defaultFilters}
        onLoadingChange={onLoadingChange}
      />
    )
    await waitFor(() => {
      expect(getPaths().filter((path) => path.includes('/reply-effects?')).length).toBeGreaterThan(
        listCalls
      )
    })
    expect(onLoadingChange).toHaveBeenCalledWith(true)
    expect(onLoadingChange).toHaveBeenCalledWith(false)
  })

  it('详情覆盖多种消息类型、关联、不完整与失败评分', async () => {
    const user = userEvent.setup()
    mockBackend({
      records: makeRecords([
        makeRecord(),
        makeRecord({ effect_id: 'e-incomplete', status: 'incomplete', reply_text: '观察被中断', confidence: null }),
        makeRecord({ effect_id: 'e-failed', status: 'evaluation_failed', reply_text: '失败样本', confidence: null }),
        makeRecord({ effect_id: 'e-empty-score', reply_text: '无分样本' }),
        makeRecord({ effect_id: 'e-no-reception', reply_text: '无情绪样本' }),
      ]),
    })
    render(<ReplyEffectsBrowser refreshToken={0} filters={defaultFilters} />)

    expect(await screen.findByText('最早的目标')).toBeInTheDocument()
    expect(screen.getByText('引导回复')).toBeInTheDocument()
    expect(screen.getByText('推理过程')).toBeInTheDocument()
    expect(screen.getByText('助手旁白')).toBeInTheDocument()
    expect(screen.getByText('参考资料')).toBeInTheDocument()
    expect(screen.getByText('空消息')).toBeInTheDocument()
    expect(screen.getByText('展示文本')).toBeInTheDocument()
    expect(screen.getByText('名片用户')).toBeInTheDocument()
    expect(screen.getByText('昵称用户')).toBeInTheDocument()
    expect(screen.getByText('目标用户')).toBeInTheDocument()
    expect(screen.getByText(/花名片/)).toBeInTheDocument()
    expect(screen.getByText('评分警告：证据偏少')).toBeInTheDocument()
    expect(screen.getByText('后续样本较少')).toBeInTheDocument()
    expect(screen.getByText('正向认可、custom_mood')).toBeInTheDocument()
    expect(screen.getByText('Bot 回复内容')).toBeInTheDocument()
    expect(screen.getByText('认可')).toBeInTheDocument()
    expect(screen.getByText('推进')).toBeInTheDocument()
    expect(screen.getByText('夸了回复')).toBeInTheDocument()
    expect(screen.getByText('证据：好；不错')).toBeInTheDocument()
    expect(screen.getByText('custom_target')).toBeInTheDocument()
    expect(screen.getByText('评审未将这条消息关联到本次回复')).toBeInTheDocument()
    expect(screen.getByText('无可见文本')).toBeInTheDocument()
    expect(screen.getByText('66%')).toBeInTheDocument()
    expect(screen.getAllByText('麦麦').length).toBeGreaterThan(0)
    expect(screen.getByText('推理')).toBeInTheDocument()
    expect(screen.getByText('助手')).toBeInTheDocument()
    expect(screen.getByText('参考')).toBeInTheDocument()
    expect(screen.getByText(/回复前 2 分钟有 2 条人类消息/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /观察被中断/ }))
    expect(await screen.findByText('观察窗口不完整，未进行评分。')).toBeInTheDocument()
    expect(screen.getByText('未知聊天流')).toBeInTheDocument()
    expect(screen.getByText('未记录模型')).toBeInTheDocument()
    expect(screen.getByText('策略：未分类')).toBeInTheDocument()
    expect(screen.getByText('目标用户：未记录')).toBeInTheDocument()
    expect(screen.getByText('结算原因：尚未结算')).toBeInTheDocument()
    expect(screen.getByText('无可见回复文本')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /失败样本/ }))
    expect(await screen.findByText('模型超时')).toBeInTheDocument()
    expect(screen.getAllByText('评估失败').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: /无分样本/ }))
    expect(await screen.findByText('无分样本')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /无情绪样本/ }))
    expect(await screen.findByText('反馈倾向：无针对 Bot 的情绪证据')).toBeInTheDocument()
  })

  it('切换筛选时忽略过期列表请求，详情加载中显示占位', async () => {
    const firstList = deferred<ReturnType<typeof makeRecords>>()
    const secondList = makeRecords([makeRecord({ effect_id: 'e-new', reply_text: '新筛选结果' })])
    let listCalls = 0
    vi.mocked(backendApi.get).mockImplementation((path: string) => {
      if (path.includes('/reply-effects?')) {
        listCalls += 1
        if (listCalls === 1) return firstList.promise as never
        return Promise.resolve(secondList) as never
      }
      if (path.endsWith('/e-new')) {
        return Promise.resolve(makeRichDetail({ effect_id: 'e-new', reply: { reply_text: '新筛选结果' } })) as never
      }
      const pendingDetail = deferred<ReturnType<typeof makeRichDetail>>()
      return pendingDetail.promise as never
    })
    const user = userEvent.setup()
    render(<ReplyEffectsBrowser refreshToken={0} filters={defaultFilters} />)
    expect(await screen.findByText('加载评估记录…')).toBeInTheDocument()

    await selectOption(user, '评估状态', '不完整')
    await waitFor(() => expect(listCalls).toBeGreaterThan(1))
    firstList.resolve(makeRecords([makeRecord({ effect_id: 'e-stale', reply_text: '过期结果' })]))
    await findVisibleText('新筛选结果')
    expect(screen.queryByText('过期结果')).not.toBeInTheDocument()
  })

  it('切换详情时忽略过期成功和失败响应', async () => {
    const firstDetail = deferred<ReturnType<typeof makeRichDetail>>()
    vi.mocked(backendApi.get).mockImplementation((path: string) => {
      if (path.includes('/reply-effects?')) {
        return Promise.resolve(
          makeRecords([
            makeRecord({ effect_id: 'e-rich', reply_text: '第一条' }),
            makeRecord({
              effect_id: 'e-incomplete',
              reply_text: '第二条',
              status: 'incomplete',
              confidence: null,
            }),
          ])
        ) as never
      }
      if (path.endsWith('/e-rich')) return firstDetail.promise as never
      return Promise.resolve(detailFor('e-incomplete')) as never
    })
    const user = userEvent.setup()
    render(<ReplyEffectsBrowser refreshToken={0} />)
    expect(await screen.findByText('加载评估详情…')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /第二条/ }))
    expect(await screen.findByText('观察窗口不完整，未进行评分。')).toBeInTheDocument()
    firstDetail.resolve(
      makeRichDetail({ effect_id: 'e-rich', reply: { reply_text: '过期详情' } })
    )
    await waitFor(() => {
      expect(screen.queryByText('过期详情')).not.toBeInTheDocument()
    })
  })
})

