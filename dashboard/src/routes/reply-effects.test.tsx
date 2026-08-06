import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { ReplyEffectsPage } from './reply-effects'

vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() } }))

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
          versions: [],
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
          followup_messages: [],
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
})
