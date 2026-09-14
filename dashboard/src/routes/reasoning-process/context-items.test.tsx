import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'

import { getReasoningPromptImageUrl } from '@/lib/reasoning-process-api'
import type { ContextItemSnapshot, ReasoningPromptMessageAvatar } from '@/lib/reasoning-process-api'

import {
  ContextItemCard,
  ContextItemTimeline,
  NaturalLanguageText,
  ToolCallsCollapsible,
  ToolDefinitionsCollapsible,
} from './context-items'

vi.mock('@/lib/reasoning-process-api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/reasoning-process-api')>()
  return {
    ...original,
    getReasoningPromptImageUrl: vi.fn(),
  }
})

const itemMeta = {
  item_id: 'item-1',
  logical_turn_id: null,
  timestamp: '2026-08-10T00:00:00.000Z',
}

function createItem(overrides: Partial<ContextItemSnapshot> = {}): ContextItemSnapshot {
  return {
    item_type: 'UserMessageItem',
    meta: itemMeta,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  vi.mocked(getReasoningPromptImageUrl).mockResolvedValue('/resolved-image.png')
})

describe('NaturalLanguageText', () => {
  it('纯文本走单块 pre 渲染', () => {
    render(<NaturalLanguageText text="今晚吃什么" />)
    expect(screen.getByText('今晚吃什么')).toBeInTheDocument()
    expect(screen.queryByText('空消息')).not.toBeInTheDocument()
  })

  it('空文本仍渲染空的纯文本块', () => {
    const { container } = render(<NaturalLanguageText text="" />)
    expect(container.querySelector('pre')).toBeInTheDocument()
    expect(container.querySelector('pre')?.textContent).toBe('')
  })

  it('混合前言和 message 标签，空消息体显示占位', () => {
    render(
      <NaturalLanguageText text={'前言文字 <message user="张三" time="12:00"></message>'} />
    )

    expect(screen.getByText('前言文字')).toBeInTheDocument()
    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('12:00')).toBeInTheDocument()
    expect(screen.getByText('空消息')).toBeInTheDocument()
  })

  it('按 msg_id 渲染头像、用户、时间和会话元信息', () => {
    const avatarMap: Record<string, ReasoningPromptMessageAvatar> = {
      m1: {
        message_id: 'm1',
        platform: 'qq',
        user_id: 'u-100',
        display_name: '群名片甲',
        avatar_url: 'https://example.com/a.png',
      },
    }

    render(
      <NaturalLanguageText
        avatarMap={avatarMap}
        text={'<message user="张三" time="08:01" msg_id="m1" chat_id="c-9">你好</message>'}
      />
    )

    expect(screen.getByText('群')).toBeInTheDocument()
    const avatarImage = screen.queryByAltText('群名片甲 的头像')
    if (avatarImage) {
      expect(avatarImage).toHaveAttribute('src', 'https://example.com/a.png')
    }
    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('08:01')).toBeInTheDocument()
    expect(screen.getByText('msg m1')).toBeInTheDocument()
    expect(screen.getByText('chat c-9')).toBeInTheDocument()
    expect(screen.getByText('你好')).toBeInTheDocument()
  })

  it('头像缺少 url / display_name 时回退到用户 ID 末两位', () => {
    render(
      <NaturalLanguageText
        avatarMap={{
          m2: {
            message_id: 'm2',
            platform: 'qq',
            user_id: 'uid-99',
            display_name: '  ',
            avatar_url: null,
          },
        }}
        text={'<message msg_id="m2">无用户名</message>'}
      />
    )

    expect(screen.getByText('99')).toBeInTheDocument()
    expect(screen.getByText('无用户名')).toBeInTheDocument()
    expect(screen.queryByAltText(/的头像/)).not.toBeInTheDocument()
  })
})

describe('ToolCallsCollapsible', () => {
  it('空列表仍展示数量为 0 的折叠头', () => {
    render(<ToolCallsCollapsible toolCalls={[]} />)
    expect(screen.getByRole('button', { name: '工具调用 · 0 个' })).toBeInTheDocument()
    expect(screen.queryByText('完整工具调用 JSON')).not.toBeInTheDocument()
  })

  it('展开后展示名称、来源、ID、参数和原始 JSON', async () => {
    const user = userEvent.setup()
    render(
      <ToolCallsCollapsible
        toolCalls={[
          {
            id: 'call-1',
            function: { name: 'lookup', arguments: { q: '晚饭' } },
            source: 'reasoning',
          },
          {
            name: 'silent',
            arguments: { ok: true },
          },
        ]}
      />
    )

    await user.click(screen.getByRole('button', { name: '工具调用 · 2 个' }))
    expect(await screen.findByText('lookup')).toBeInTheDocument()
    expect(screen.getByText('推理中调用')).toBeInTheDocument()
    expect(screen.getByText('call-1')).toBeInTheDocument()
    expect(screen.getByText(/"q": "晚饭"/)).toBeInTheDocument()
    expect(screen.getByText('silent')).toBeInTheDocument()
    expect(screen.queryByText('正文调用')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '完整工具调用 JSON' }))
    expect(await screen.findByText(/"source": "reasoning"/)).toBeInTheDocument()
  })
})

describe('ToolDefinitionsCollapsible', () => {
  it('空列表展示 0 个工具定义', () => {
    render(<ToolDefinitionsCollapsible toolDefinitions={[]} />)
    expect(screen.getByRole('button', { name: '工具定义 · 0 个' })).toBeInTheDocument()
    expect(screen.queryByText('无参数')).not.toBeInTheDocument()
  })

  it('展开后展示描述、必填参数、可选值和原始定义', async () => {
    const user = userEvent.setup()
    render(
      <ToolDefinitionsCollapsible
        toolDefinitions={[
          {
            type: 'function',
            function: {
              name: 'search',
              description: '搜索工具',
              parameters: {
                required: ['query'],
                properties: {
                  query: { type: 'string', description: '查询词' },
                  mode: { enum: ['fast', 'slow'], default: 'fast' },
                },
              },
            },
          },
          { name: 'empty_tool' },
        ]}
      />
    )

    await user.click(screen.getByRole('button', { name: '工具定义 · 2 个' }))
    expect(await screen.findByText('search')).toBeInTheDocument()
    expect(screen.getByText('搜索工具')).toBeInTheDocument()
    expect(screen.getByText('query')).toBeInTheDocument()
    expect(screen.getByText('查询词')).toBeInTheDocument()
    expect(screen.getByText('必填')).toBeInTheDocument()
    expect(screen.getByText('可选值：fast、slow')).toBeInTheDocument()
    expect(screen.getByText('默认：fast')).toBeInTheDocument()
    expect(screen.getByText('empty_tool')).toBeInTheDocument()
    expect(screen.getByText('无参数')).toBeInTheDocument()

    const rawButtons = screen.getAllByRole('button', { name: '原始定义' })
    await user.click(rawButtons[0])
    expect(await screen.findByText(/"name": "search"/)).toBeInTheDocument()
  })
})

describe('ContextItemCard', () => {
  const botSelfNames = new Set(['麦麦'])

  it('空 FunctionCallItem 显示无可见文本提示，并可展开 Item JSON', async () => {
    const user = userEvent.setup()
    const item = createItem({ item_type: 'FunctionCallItem' })
    render(
      <ContextItemCard item={item} index={0} avatarMap={{}} botSelfNames={botSelfNames} />
    )

    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('function call')).toBeInTheDocument()
    expect(screen.getByText('FunctionCallItem')).toBeInTheDocument()
    expect(screen.getByText('此 Item 没有可见文本；完整字段见 Item JSON。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '完整 Item JSON' }))
    expect(await screen.findByText(/"item_type": "FunctionCallItem"/)).toBeInTheDocument()
  })

  it('渲染用户消息、自身高亮、turn/phase 徽标和 call_id', () => {
    render(
      <ContextItemCard
        index={2}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({
          item_type: 'UserMessageItem',
          phase: 'reply',
          representation: 'text',
          status: 'ok',
          call_id: 'call-user',
          meta: { ...itemMeta, logical_turn_id: 'turn-9' },
          parts: [{ type: 'text', text: '<message user="麦麦">我自己说的</message>' }],
        })}
      />
    )

    const article = screen.getByRole('article')
    expect(article.className).toContain('orange')
    expect(screen.getByText('#3')).toBeInTheDocument()
    expect(screen.getByText('user')).toBeInTheDocument()
    expect(screen.getByText('turn turn-9')).toBeInTheDocument()
    expect(screen.getByText('phase: reply')).toBeInTheDocument()
    expect(screen.getByText('text')).toBeInTheDocument()
    expect(screen.getByText('ok')).toBeInTheDocument()
    expect(screen.getByText('call_id: call-user')).toBeInTheDocument()
    expect(screen.getByText('我自己说的')).toBeInTheDocument()
  })

  it.each([
    ['SystemMessageItem', 'system', 'cyan'],
    ['AssistantMessageItem', 'assistant', 'amber'],
    ['ReasoningItem', 'reasoning', 'indigo'],
    ['FunctionCallOutputItem', 'tool', 'violet'],
  ] as const)('%s 使用 %s 角色样式', (itemType, label, color) => {
    render(
      <ContextItemCard
        index={0}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({
          item_type: itemType,
          parts: [{ type: 'text', text: '正文' }],
          output: '工具输出',
          summary_parts: ['推理摘要'],
        })}
      />
    )

    expect(screen.getByRole('article').className).toContain(color)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('Provider 类型使用 provider_ 样式，未知类型显示未知角色', () => {
    const { rerender } = render(
      <ContextItemCard
        index={0}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({
          item_type: 'ProviderActivityItem',
          display_summary: '活动摘要',
          details: ['细节甲', '细节乙'],
        })}
      />
    )

    expect(screen.getByRole('article').className).toContain('sky')
    expect(screen.getByText('provider activity')).toBeInTheDocument()
    expect(screen.getByText('活动摘要')).toBeInTheDocument()
    expect(screen.getByText(/细节甲/)).toBeInTheDocument()
    expect(screen.getByText(/细节乙/)).toBeInTheDocument()

    rerender(
      <ContextItemCard
        index={0}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({
          item_type: 'ProviderOpaqueItem',
          display_summary: '不透明摘要',
        })}
      />
    )
    expect(screen.getByText('provider opaque')).toBeInTheDocument()
    expect(screen.getByText('不透明摘要')).toBeInTheDocument()

    rerender(
      <ContextItemCard
        index={0}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({ item_type: '', display_summary: 'x' })}
      />
    )
    expect(screen.getByText('未知角色')).toBeInTheDocument()
    expect(screen.getByRole('article').className).toContain('bg-muted/30')
  })

  it('FunctionCallItem 展示 tool_call.call_id 和工具调用折叠面板', async () => {
    const user = userEvent.setup()
    render(
      <ContextItemCard
        index={0}
        avatarMap={{}}
        botSelfNames={botSelfNames}
        item={createItem({
          item_type: 'FunctionCallItem',
          tool_call: {
            call_id: 'tool-call-9',
            func_name: 'lookup',
            args: { q: 1 },
            extra_content: { tool_call_source: 'response' },
          },
        })}
      />
    )

    expect(screen.getByText('call_id: tool-call-9')).toBeInTheDocument()
    expect(screen.queryByText('此 Item 没有可见文本；完整字段见 Item JSON。')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '工具调用 · 1 个' }))
    expect(await screen.findByText('lookup')).toBeInTheDocument()
    expect(screen.getByText('正文调用')).toBeInTheDocument()
  })

  it('加载图片预览，卸载后忽略迟到的 URL', async () => {
    let resolveUrl!: (url: string) => void
    vi.mocked(getReasoningPromptImageUrl).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUrl = resolve
        })
    )

    const item = createItem({
      parts: [
        {
          type: 'image',
          image_format: 'png',
          size_bytes: 2048,
          image_path: 'data/prompt_imgs/a.png',
        },
        {
          type: 'image',
          format: 'jpeg',
          image_path: 'data/prompt_imgs/b.jpg',
        },
      ],
    })

    const { unmount } = render(
      <ContextItemCard item={item} index={0} avatarMap={{}} botSelfNames={botSelfNames} />
    )
    expect(document.querySelectorAll('.animate-pulse')).toHaveLength(2)

    unmount()
    resolveUrl('/late.png')
    await Promise.resolve()

    vi.mocked(getReasoningPromptImageUrl).mockResolvedValue('/resolved-image.png')
    render(<ContextItemCard item={item} index={0} avatarMap={{}} botSelfNames={botSelfNames} />)

    await waitFor(() => {
      expect(screen.getByAltText('请求图片 1')).toHaveAttribute('src', '/resolved-image.png')
    })
    expect(screen.getByAltText('请求图片 2')).toBeInTheDocument()
    expect(screen.getByText('image/png · 2048 B')).toBeInTheDocument()
    expect(screen.getByText('image/jpeg')).toBeInTheDocument()
  })
})

describe('ContextItemTimeline', () => {
  it('空列表显示没有 Items', () => {
    render(
      <ContextItemTimeline title="请求 Items" items={[]} avatarMap={{}} botSelfNames={new Set()} />
    )
    expect(screen.getByText('请求 Items')).toBeInTheDocument()
    expect(screen.getByText('0 Items')).toBeInTheDocument()
    expect(screen.getByText('没有 Items。')).toBeInTheDocument()
  })

  it('有数据时按顺序渲染卡片', () => {
    render(
      <ContextItemTimeline
        title="输出 Items"
        avatarMap={{}}
        botSelfNames={new Set()}
        items={[
          createItem({
            item_type: 'AssistantMessageItem',
            meta: { ...itemMeta, item_id: 'a1' },
            parts: [{ type: 'text', text: '第一句' }],
          }),
          createItem({
            item_type: 'SystemMessageItem',
            meta: { ...itemMeta, item_id: 's1' },
            parts: [{ type: 'text', text: '系统' }],
          }),
        ]}
      />
    )

    expect(screen.getByText('输出 Items')).toBeInTheDocument()
    expect(screen.getByText('2 Items')).toBeInTheDocument()
    expect(screen.queryByText('没有 Items。')).not.toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('第一句')).toBeInTheDocument()
    expect(screen.getByText('系统')).toBeInTheDocument()
  })
})
