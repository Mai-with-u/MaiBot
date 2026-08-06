import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { ReplyEffectsPage } from './reply-effects'
import { ReplyEffectsBrowser } from './reply-effects-browser'

vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn(), post: vi.fn() } }))

const versionAggregates = [
  {
    name: 'model-a · prompt-a',
    count: 12,
    response_score: 52,
    response_score_std: 8,
    reception_score: 60,
    reception_score_std: 7,
    conversation_score: 50,
    conversation_score_std: 6,
    raw_score: 54,
    raw_score_std: 7,
    relative_score: 55,
    relative_score_std: 8,
    confidence: 0.8,
    confidence_std: 0.05,
    model_name: 'model-a',
    prompt_fingerprint: 'prompt-a',
    model_names: ['model-a'],
    prompt_fingerprints: ['prompt-a'],
    first_seen: '2026-01-01T00:00:00',
    last_seen: '2026-01-02T00:00:00',
    collapsed_models: false,
    collapsed_versions: false,
    score_distributions: {},
  },
  {
    name: 'model-b · prompt-b',
    count: 10,
    response_score: 66,
    response_score_std: 7,
    reception_score: 62,
    reception_score_std: 6,
    conversation_score: 58,
    conversation_score_std: 7,
    raw_score: 62,
    raw_score_std: 6,
    relative_score: 64,
    relative_score_std: 7,
    confidence: 0.82,
    confidence_std: 0.04,
    model_name: 'model-b',
    prompt_fingerprint: 'prompt-b',
    model_names: ['model-b'],
    prompt_fingerprints: ['prompt-b'],
    first_seen: '2026-01-01T00:00:00',
    last_seen: '2026-01-02T00:00:00',
    collapsed_models: false,
    collapsed_versions: false,
    score_distributions: {},
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
            reception_score: 70,
            conversation_score: 60,
            raw_score: 72,
            relative_score: null,
            confidence: 0.8,
          },
          strategies: [
            {
              name: 'answer',
              count: 1,
              response_score: 80,
              reception_score: 70,
              conversation_score: 60,
              raw_score: 72,
              relative_score: null,
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
          scorer_version: 2,
          session: { session_name: '测试群' },
          reply: {
            target_message_id: '-1085252920',
            reply_text: '你好',
            model_name: 'test',
            request_fingerprint: 'request123',
            prompt_fingerprint: 'prompt123',
          },
          scores: {
            response_score: 80,
            reception_score: 70,
            conversation_score: 60,
            raw_score: 72,
            relative_score: null,
            confidence: 0.8,
            baseline_sample_size: 0,
            baseline_level: 'insufficient',
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
        total: 1,
        next_cursor: null,
        items: [
          {
            effect_id: 'e1',
            session_name: '测试群',
            status: 'finalized',
            created_at: '2026-01-01T00:00:00',
            strategy_primary: 'answer',
            model_name: 'test',
            reply_text: '你好',
            response_score: 80,
            reception_score: 70,
            conversation_score: 60,
            raw_score: 72,
            relative_score: null,
            confidence: 0.8,
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
    expect(screen.getByText('情感接受度')).toBeInTheDocument()
    expect(screen.getByText('聊天推动度')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)

    const requestPaths = vi.mocked(backendApi.get).mock.calls.map(([path]) => path)
    expect(requestPaths.find((path) => path.includes('/overview'))).toContain('min_confidence=0.6')
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
        left: expect.objectContaining({ model_names: ['model-a'] }),
        right: expect.objectContaining({ model_names: ['model-b'] }),
        min_confidence: 0.6,
      }),
    })
  })
})
