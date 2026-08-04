import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AMemorixRetrievalChatsHook } from '../AMemorixRetrievalChatsHook'
import { AMemorixRetrievalFilterGroupHook } from '../AMemorixRetrievalFilterGroupHook'
import {
  buildAMemorixRetrievalChatTokenOptions,
  resolveAMemorixRetrievalChatsCopy,
} from '../AMemorixRetrievalChatsHook.utils'
import type { ChatStream } from '@/lib/chat-management-api'

vi.mock('@/lib/chat-management-api', () => ({
  getChatStreams: vi.fn(async () => [
    {
      session_id: 'session-group',
      display_name: '测试群',
      chat_type: 'group',
      target_id: '10001',
      platform: 'qq',
      group_id: '10001',
      user_id: null,
    },
    {
      session_id: 'session-private',
      display_name: '小明的私聊',
      chat_type: 'private',
      target_id: '20002',
      platform: 'qq',
      group_id: null,
      user_id: '20002',
    },
  ] as ChatStream[]),
}))

describe('AMemorixRetrievalChatsHook', () => {
  it('builds only exact stream token options from known chats', () => {
    const options = buildAMemorixRetrievalChatTokenOptions([
      {
        session_id: 'session-group',
        display_name: '测试群',
        chat_type: 'group',
        target_id: '10001',
        platform: 'qq',
        group_id: '10001',
        user_id: null,
      },
      {
        session_id: 'session-private',
        display_name: '小明的私聊',
        chat_type: 'private',
        target_id: '20002',
        platform: 'qq',
        group_id: null,
        user_id: '20002',
      },
    ] as ChatStream[])

    expect(options.map((item) => item.token)).toEqual([
      'stream:session-group',
      'stream:session-private',
    ])
  })

  it('adds exact streams while preserving existing legacy tokens', async () => {
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
        value={['group:legacy-group']}
      />,
    )

    expect(screen.queryByLabelText('选择 stream:session-group')).not.toBeInTheDocument()
    expect(screen.getByText('group:legacy-group')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '添加聊天流' }))

    expect(await screen.findByLabelText('选择 stream:session-group')).toBeInTheDocument()
    await user.click(screen.getByLabelText('选择 stream:session-group'))
    await user.click(screen.getByRole('button', { name: '添加 1 个聊天流' }))

    expect(onChange).toHaveBeenLastCalledWith(['group:legacy-group', 'stream:session-group'])
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

  it('keeps the entry filter chat list inside the add dialog', async () => {
    const user = userEvent.setup()

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

    expect(screen.getByText('聊天过滤范围')).toBeInTheDocument()
    expect(screen.getByText('入口过滤')).toBeInTheDocument()
    expect(screen.getByText(/影响当前聊天流是否允许使用记忆能力/)).toBeInTheDocument()
    expect(screen.queryByLabelText('选择 stream:session-group')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '添加聊天流' }))

    await waitFor(() => {
      expect(screen.getByLabelText('选择 stream:session-group')).toBeInTheDocument()
    })
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
