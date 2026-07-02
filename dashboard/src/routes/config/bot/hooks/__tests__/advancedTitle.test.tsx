import { beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ExpressionGroupsHook, MultipleReplyStyleHook } from '../complexFieldHooks'
import { createJsonFieldHook } from '../JsonFieldHookFactory'
import { createListItemEditorHook } from '../ListItemEditorHookFactory'
import { getChatStreams, type ChatStream } from '@/lib/chat-management-api'
import type { FieldSchema } from '@/types/config-schema'

vi.mock('@/lib/chat-management-api', () => ({
  getChatStreams: vi.fn(async () => [
    {
      id: 1,
      session_id: 'session-group',
      display_name: '测试群',
      chat_type: 'group',
      target_id: '10001',
      platform: 'qq',
      account_id: null,
      scope: null,
      user_id: '',
      user_nickname: null,
      user_cardname: null,
      group_id: '10001',
      group_name: '测试群',
      message_count: 10,
      expression_count: 0,
      jargon_count: 0,
      created_at: null,
      last_active_at: null,
      latest_message: '',
      latest_message_at: null,
    },
    {
      id: 2,
      session_id: 'session-private',
      display_name: '小明的私聊',
      chat_type: 'private',
      target_id: '20002',
      platform: 'qq',
      account_id: null,
      scope: null,
      user_id: '20002',
      user_nickname: '小明',
      user_cardname: null,
      group_id: null,
      group_name: null,
      message_count: 8,
      expression_count: 0,
      jargon_count: 0,
      created_at: null,
      last_active_at: null,
      latest_message: '',
      latest_message_at: null,
    },
  ]),
  resolveChatTargets: vi.fn(async () => []),
}))

const advancedSchema: FieldSchema = {
  name: 'advanced_field',
  type: 'array',
  label: 'Advanced field',
  description: 'Advanced field description',
  required: false,
  advanced: true,
}

const sharedMemorySchema: FieldSchema = {
  name: 'shared_memory_groups',
  type: 'array',
  label: '共享记忆组',
  description: '共享记忆组',
  required: false,
  'x-display-as-section': true,
}

const createChatStream = (index: number, chatType: 'group' | 'private'): ChatStream => {
  const targetId = chatType === 'group' ? `10${index.toString().padStart(3, '0')}` : `20${index.toString().padStart(3, '0')}`
  const displayName = chatType === 'group' ? `测试群 ${index}` : `目标私聊 ${index}`
  return {
    id: index,
    session_id: `session-${chatType}-${index}`,
    display_name: displayName,
    chat_type: chatType,
    target_id: targetId,
    platform: 'qq',
    account_id: null,
    scope: null,
    user_id: chatType === 'private' ? targetId : '',
    user_nickname: chatType === 'private' ? displayName : null,
    user_cardname: null,
    group_id: chatType === 'group' ? targetId : null,
    group_name: chatType === 'group' ? displayName : null,
    message_count: index,
    expression_count: 0,
    jargon_count: 0,
    created_at: null,
    last_active_at: null,
    latest_message: '',
    latest_message_at: null,
  }
}

describe('custom bot config hooks', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value: vi.fn(() => false),
    })
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
      configurable: true,
      value: vi.fn(),
    })
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
      configurable: true,
      value: vi.fn(),
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  it('colors string-list hook titles for advanced fields', () => {
    const { container } = render(
      <MultipleReplyStyleHook
        fieldPath="personality.multiple_reply_style"
        onChange={vi.fn()}
        schema={advancedSchema}
        value={[]}
      />,
    )

    expect(container.querySelector('label')).toHaveClass('text-sky-700')
  })

  it('colors list-editor hook titles for advanced fields', () => {
    const ListHook = createListItemEditorHook({ addLabel: 'Add item' })

    const { getByText } = render(
      <ListHook
        fieldPath="advanced.items"
        onChange={vi.fn()}
        schema={advancedSchema}
        nestedSchema={{
          className: 'AdvancedItem',
          classDoc: 'Advanced item',
          fields: [],
        }}
        value={[]}
      />,
    )

    expect(getByText('Advanced field')).toHaveClass('text-sky-700')
  })

  it('colors JSON hook titles for advanced fields', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: 'Edit JSON.',
      placeholder: '[]',
    })

    const { getByText } = render(
      <JsonHook
        fieldPath="advanced.json"
        onChange={vi.fn()}
        schema={advancedSchema}
        value={[]}
      />,
    )

    expect(getByText('Advanced field')).toHaveClass('text-sky-700')
  })

  it('folds shared memory groups while global memory sharing is enabled', async () => {
    const user = userEvent.setup()
    const onParentChange = vi.fn()
    const sharedMemoryGroups = [
      {
        targets: [
          {
            platform: 'qq',
            item_id: '123456',
            rule_type: 'group',
          },
        ],
      },
    ]

    const { rerender } = render(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        onParentChange={onParentChange}
        parentValues={{ global_memory_sharing_enabled: true }}
        schema={sharedMemorySchema}
        value={sharedMemoryGroups}
      />,
    )

    expect(screen.queryByDisplayValue('123456')).not.toBeInTheDocument()
    expect(screen.getByLabelText('添加共享记忆组')).toBeDisabled()
    expect(screen.getByRole('switch', { name: '全局共享记忆' })).toBeChecked()

    rerender(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        onParentChange={onParentChange}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={sharedMemoryGroups}
      />,
    )

    expect(screen.queryByDisplayValue('123456')).not.toBeInTheDocument()
    expect(screen.getByLabelText('添加共享记忆组')).not.toBeDisabled()
    expect(screen.getByRole('switch', { name: '全局共享记忆' })).not.toBeChecked()

    await user.click(screen.getByLabelText('展开共享记忆组 1'))

    expect(screen.getByDisplayValue('123456')).toBeInTheDocument()
    expect(screen.getByText('qq:123456')).toBeInTheDocument()

    await user.click(screen.getByRole('switch', { name: '全局共享记忆' }))

    expect(onParentChange).toHaveBeenLastCalledWith('global_memory_sharing_enabled', true)
  })

  it('selects shared memory group members from known group or private chats inline', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()

    render(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={onChange}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={[
          {
            targets: [
              {
                platform: 'qq',
                item_id: '',
                rule_type: 'group',
              },
            ],
          },
        ]}
      />,
    )

    await user.click(screen.getByLabelText('展开共享记忆组 1'))

    await waitFor(() => {
      expect(screen.getByText('选择群聊/私聊')).toBeInTheDocument()
    })

    expect(screen.getByText('未选择聊天流')).toBeInTheDocument()
    expect(screen.queryByText('平台')).not.toBeInTheDocument()
    expect(screen.queryByText('聊天流 ID')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '手动填写' }))
    expect(screen.getByText('群号或用户 ID')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('群号或用户 ID'), {
      target: { value: '30003' },
    })

    expect(onChange).toHaveBeenLastCalledWith([
      {
        targets: [
          {
            platform: 'qq',
            item_id: '30003',
            rule_type: 'group',
          },
        ],
      },
    ])

    await user.click(screen.getByRole('combobox', { name: '选择群聊或私聊' }))
    await user.click(await screen.findByText(/测试群 · 群聊/))

    expect(onChange).toHaveBeenLastCalledWith([
      {
        targets: [
          {
            platform: 'qq',
            item_id: '10001',
            rule_type: 'group',
          },
        ],
      },
    ])
  })

  it('keeps private chat matches visible when group matches exceed the display limit', async () => {
    const user = userEvent.setup()
    vi.mocked(getChatStreams).mockResolvedValueOnce([
      ...Array.from({ length: 55 }, (_, index) => createChatStream(index + 1, 'group')),
      createChatStream(99, 'private'),
    ])

    render(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={[
          {
            targets: [
              {
                platform: 'qq',
                item_id: '',
                rule_type: 'group',
              },
            ],
          },
        ]}
      />,
    )

    await user.click(screen.getByLabelText('展开共享记忆组 1'))
    await user.click(await screen.findByRole('combobox', { name: '选择群聊或私聊' }))

    expect(await screen.findByText(/目标私聊 99 · 私聊/)).toBeInTheDocument()
    expect(screen.getByText('群聊和私聊各最多显示 50 个匹配项，请输入关键词缩小范围。')).toBeInTheDocument()
  })

  it('keeps expanded shared memory groups open when another group is added', async () => {
    const user = userEvent.setup()
    const firstGroup = {
      targets: [
        {
          platform: 'qq',
          item_id: '123456',
          rule_type: 'group' as const,
        },
      ],
    }
    const { rerender } = render(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={[firstGroup]}
      />,
    )

    await user.click(screen.getByLabelText('展开共享记忆组 1'))
    expect(screen.getByDisplayValue('123456')).toBeInTheDocument()

    rerender(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={[
          firstGroup,
          {
            targets: [
              {
                platform: 'qq',
                item_id: '654321',
                rule_type: 'group',
              },
            ],
          },
        ]}
      />,
    )

    expect(screen.getByDisplayValue('123456')).toBeInTheDocument()
  })

  it('allows collapsing manual fields for unmatched shared memory members', async () => {
    const user = userEvent.setup()

    render(
      <ExpressionGroupsHook
        fieldPath="a_memorix.shared_memory_groups"
        onChange={vi.fn()}
        parentValues={{ global_memory_sharing_enabled: false }}
        schema={sharedMemorySchema}
        value={[
          {
            targets: [
              {
                platform: 'qq',
                item_id: 'not-found',
                rule_type: 'group',
              },
            ],
          },
        ]}
      />,
    )

    await user.click(screen.getByLabelText('展开共享记忆组 1'))

    await waitFor(() => {
      expect(screen.getByText('未匹配')).toBeInTheDocument()
    })

    expect(screen.getByText('群号或用户 ID')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '收起手动填写' }))

    expect(screen.queryByText('群号或用户 ID')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '手动填写' })).toBeInTheDocument()
  })
})
