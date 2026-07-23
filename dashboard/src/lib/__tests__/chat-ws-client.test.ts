import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chatWsClient } from '../chat-ws-client'
import { unifiedWsClient } from '../unified-ws'

vi.mock('../unified-ws', () => ({
  unifiedWsClient: {
    addEventListener: vi.fn(),
    call: vi.fn(),
    closeSession: vi.fn(),
    getStatus: vi.fn(() => 'connected'),
    onConnectionChange: vi.fn(),
    onReconnect: vi.fn(),
    onStatusChange: vi.fn(),
    restart: vi.fn(),
  },
}))

describe('chatWsClient', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('sends image payloads through message.send', async () => {
    const callMock = vi.mocked(unifiedWsClient.call)
    callMock.mockResolvedValue({})

    await chatWsClient.sendMessage('tab-1', '看看这张图', 'Alice', {
      images: [
        {
          name: 'cat.png',
          mime_type: 'image/png',
          base64: 'iVBORw0KGgo=',
        },
      ],
    })

    expect(callMock).toHaveBeenCalledWith({
      domain: 'chat',
      method: 'message.send',
      session: 'tab-1',
      data: {
        content: '看看这张图',
        images: [
          {
            name: 'cat.png',
            mime_type: 'image/png',
            base64: 'iVBORw0KGgo=',
          },
        ],
        user_name: 'Alice',
      },
    })
  })

  it('retains a released session for five minutes and restores it when reopened', async () => {
    vi.useFakeTimers()
    const callMock = vi.mocked(unifiedWsClient.call)
    callMock.mockResolvedValue({})
    const payload = {
      client: { type: 'webui' as const, name: 'MaiBot WebUI' },
      user_id: 'retained-user',
      user_name: 'Alice',
    }

    await chatWsClient.openSession('retained-tab', payload)
    chatWsClient.releaseSession('retained-tab')
    await vi.advanceTimersByTimeAsync(4 * 60 * 1000)

    expect(callMock).toHaveBeenCalledTimes(1)

    await chatWsClient.openSession('retained-tab', payload)
    await vi.advanceTimersByTimeAsync(60 * 1000)

    expect(callMock).toHaveBeenCalledTimes(2)
    expect(callMock).toHaveBeenLastCalledWith({
      domain: 'chat',
      method: 'session.open',
      session: 'retained-tab',
      data: {
        ...payload,
        restore: true,
      },
    })
  })

  it('closes a released session after five minutes', async () => {
    vi.useFakeTimers()
    const callMock = vi.mocked(unifiedWsClient.call)
    callMock.mockResolvedValue({})
    const payload = {
      client: { type: 'webui' as const, name: 'MaiBot WebUI' },
      user_id: 'expired-user',
      user_name: 'Bob',
    }

    await chatWsClient.openSession('expired-tab', payload)
    chatWsClient.releaseSession('expired-tab')
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)

    expect(callMock).toHaveBeenLastCalledWith({
      domain: 'chat',
      method: 'session.close',
      session: 'expired-tab',
      data: {},
    })
  })
})
