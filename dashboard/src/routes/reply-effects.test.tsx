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
          summary: { count: 1, response_score: 80, reception_score: 70, conversation_score: 60, raw_score: 72, relative_score: null, confidence: 0.8 },
          strategies: [{ name: 'answer', count: 1, response_score: 80, reception_score: 70, conversation_score: 60, raw_score: 72, relative_score: null, confidence: 0.8 }],
          versions: [], trend: [], filters: { sessions: [['s1', '测试群']], strategies: ['answer'], models: [] },
        }) as never
      }
      return Promise.resolve({
        total: 1, next_cursor: null, items: [{ effect_id: 'e1', session_name: '测试群', status: 'finalized', created_at: '2026-01-01T00:00:00', strategy_primary: 'answer', model_name: 'test', reply_text: '你好', response_score: 80, reception_score: 70, conversation_score: 60, raw_score: 72, relative_score: null, confidence: 0.8, evaluation_error: '' }],
      }) as never
    })
  })

  it('展示真实聊天流名称、三维分数和冷启动状态', async () => {
    render(<ReplyEffectsPage />)
    await waitFor(() => expect(screen.getAllByText('测试群').length).toBeGreaterThan(0))
    expect(screen.getAllByText('回应度').length).toBeGreaterThan(0)
    expect(screen.getByText('情感接受度')).toBeInTheDocument()
    expect(screen.getByText('聊天推动度')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})
