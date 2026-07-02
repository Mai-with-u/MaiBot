import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AMemorixRetrievalChatsHook } from '../AMemorixRetrievalChatsHook'
import { AMemorixRetrievalFilterGroupHook } from '../AMemorixRetrievalFilterGroupHook'
import {
  buildAMemorixRetrievalChatTokenOptions,
  resolveAMemorixRetrievalChatsCopy,
} from '../AMemorixRetrievalChatsHook.utils'
import type { MemoryImportChatTargetPayload } from '@/lib/memory-api'

vi.mock('@/lib/memory-api', () => ({
  getMemoryImportChatTargets: vi.fn(async () => ({
    success: true,
    data: [
      {
        chat_id: 'session-group',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: '',
        is_group: true,
      },
      {
        chat_id: 'session-private',
        chat_name: '小明的私聊',
        platform: 'qq',
        group_id: '',
        user_id: '20002',
        is_group: false,
      },
    ],
  })),
}))

describe('AMemorixRetrievalChatsHook', () => {
  it('builds stream, group, private and user token options from chat targets', () => {
    const options = buildAMemorixRetrievalChatTokenOptions([
      {
        chat_id: 'session-group',
        chat_name: '测试群',
        platform: 'qq',
        group_id: '10001',
        user_id: '',
        is_group: true,
      },
      {
        chat_id: 'session-private',
        chat_name: '小明的私聊',
        platform: 'qq',
        group_id: '',
        user_id: '20002',
        is_group: false,
      },
    ] as MemoryImportChatTargetPayload[])

    expect(options.map((item) => item.token)).toEqual([
      'stream:session-group',
      'stream:session-private',
      'group:10001',
      'user:20002',
      'private:20002',
    ])
  })

  it('lets users add manual tokens when editing retrieval filter chats', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()

    render(
      <AMemorixRetrievalChatsHook
        fieldPath="a_memorix.filter.retrieval.chat_summary.chats"
        onChange={onChange}
        schema={{
          name: 'chats',
          type: 'array',
          label: '聊天流列表',
          description: '聊天流列表',
          required: false,
        }}
        value={['stream:session-group']}
      />,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('选择 group:10001')).toBeInTheDocument()
    })

    await user.type(screen.getByPlaceholderText('手动添加，如 group:123 或 stream:session_id'), 'group:10001')
    await user.click(screen.getByRole('button', { name: '添加' }))

    expect(onChange).toHaveBeenLastCalledWith(['stream:session-group', 'group:10001'])
  })

  it('uses distinct labels for each retrieval result type', () => {
    expect(resolveAMemorixRetrievalChatsCopy('a_memorix.filter.chats').title)
      .toBe('聊天过滤范围')
    expect(resolveAMemorixRetrievalChatsCopy('a_memorix.filter.retrieval.chat_stream.chats').title)
      .toBe('普通聊天流跨聊天流过滤范围')
    expect(resolveAMemorixRetrievalChatsCopy('a_memorix.filter.retrieval.chat_summary.chats').title)
      .toBe('聊天总结跨聊天流过滤范围')
    expect(resolveAMemorixRetrievalChatsCopy('a_memorix.filter.retrieval.episode.chats').title)
      .toBe('Episode 跨聊天流过滤范围')
  })

  it('uses the token selector for entry chat filter chats', async () => {
    render(
      <AMemorixRetrievalChatsHook
        fieldPath="a_memorix.filter.chats"
        schema={{
          name: 'chats',
          type: 'array',
          label: '聊天流列表',
          description: '聊天流列表',
          required: false,
        }}
        value={[]}
      />,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('选择 group:10001')).toBeInTheDocument()
    })
    expect(screen.getByText('聊天过滤范围')).toBeInTheDocument()
    expect(screen.getByText('入口过滤')).toBeInTheDocument()
    expect(screen.getByText(/影响当前聊天流是否允许使用记忆能力/)).toBeInTheDocument()
  })

  it('renders the cross-chat retrieval filter summary above subtype configs', () => {
    render(
      <AMemorixRetrievalFilterGroupHook
        fieldPath="a_memorix.filter.retrieval"
        value={{
          chat_stream: {
            enabled: true,
            mode: 'blacklist',
            chats: ['group:10001'],
          },
          chat_summary: {
            enabled: false,
            mode: 'blacklist',
            chats: [],
          },
          episode: {
            enabled: true,
            mode: 'whitelist',
            chats: ['stream:session-group', 'private:20002'],
          },
        }}
      >
        <div>三个子配置</div>
      </AMemorixRetrievalFilterGroupHook>,
    )

    const summaryTitle = screen.getByText('跨聊天流检索结果过滤')
    const subtypeContent = screen.getByText('三个子配置')
    expect(summaryTitle.compareDocumentPosition(subtypeContent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('已启用，黑名单，1 个来源 token')).toBeInTheDocument()
    expect(screen.getByText('未启用，黑名单，0 个来源 token')).toBeInTheDocument()
    expect(screen.getByText('已启用，白名单，2 个来源 token')).toBeInTheDocument()
  })
})
