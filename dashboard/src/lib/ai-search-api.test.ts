import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import { searchWithAIStream } from './ai-search-api'

vi.mock('@/lib/http', () => ({
  backendApi: {
    post: vi.fn(),
  },
}))

const backendPostMock = vi.mocked(backendApi.post)

describe('searchWithAIStream', () => {
  beforeEach(() => {
    backendPostMock.mockReset()
  })

  it('按数据到达顺序回调过程事件并返回最终结果', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            '{"type":"progress","stage":"start"}\n' +
              '{"type":"progress","stage":"tool","status":"started","tool":"search_official_docs",'
          )
        )
        controller.enqueue(
          encoder.encode(
            '"query":"表情包"}\n' +
              '{"type":"result","response":{"success":true,"cached":false,"model_name":"test-model",' +
              '"answer":"完成","suggestions":[],"sources":[],"expanded_terms":[],"results":[],' +
              '"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n'
          )
        )
        controller.close()
      },
    })
    backendPostMock.mockResolvedValue(new Response(stream))
    const progressEvents: string[] = []

    const response = await searchWithAIStream(
      {
        query: '为什么无法发送表情包',
        language: 'zh',
        candidates: [],
      },
      (event) => progressEvents.push(`${event.stage}:${event.tool ?? ''}`)
    )

    expect(progressEvents).toEqual(['start:', 'tool:search_official_docs'])
    expect(response.answer).toBe('完成')
    expect(backendPostMock).toHaveBeenCalledWith(
      '/api/webui/search/ai/stream',
      expect.objectContaining({ parse: 'response' })
    )
  })
})
