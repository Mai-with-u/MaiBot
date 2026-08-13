import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryConfigEditor } from '../MemoryConfigEditor'
import { MemoryEpisodeManager } from '../MemoryEpisodeManager'
import { MemoryTimelineManager } from '../MemoryTimelineManager'
import * as memoryApi from '@/lib/memory-api'
import type {
  MemoryEpisodeActionPayload,
  MemoryEpisodeDetailPayload,
  MemoryEpisodeItemPayload,
  MemoryEpisodeListPayload,
  MemoryEpisodeStatusPayload,
  MemoryImportChatTargetPayload,
  MemoryTimelineEventPayload,
  MemoryTimelinePayload,
} from '@/lib/memory-api'
import type { ConfigFieldSchema, PluginConfigSchema } from '@/lib/plugin-api'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryEpisode: vi.fn(),
  getMemoryEpisodes: vi.fn(),
  getMemoryEpisodeStatus: vi.fn(),
  getMemoryTimeline: vi.fn(),
  processMemoryEpisodePending: vi.fn(),
  rebuildMemoryEpisodes: vi.fn(),
}))

// 用按钮驱动滑块，覆盖 length<2 早退以及变更/提交时间窗口
vi.mock('@/components/ui/slider', () => ({
  Slider: ({
    value,
    onValueChange,
    onValueCommit,
  }: {
    value?: number[]
    onValueChange?: (next: number[]) => void
    onValueCommit?: (next: number[]) => void
  }) => {
    const current = value ?? [0, 0]
    return (
      <div>
        <button type="button" onClick={() => onValueChange?.(current.slice(0, 1))}>
          时间轴滑块短变更
        </button>
        <button type="button" onClick={() => onValueChange?.([current[0] + 3600, current[1]])}>
          时间轴滑块变更
        </button>
        <button type="button" onClick={() => onValueCommit?.(current.slice(0, 1))}>
          时间轴滑块短提交
        </button>
        <button type="button" onClick={() => onValueCommit?.([current[0] + 7200, current[1]])}>
          时间轴滑块提交
        </button>
      </div>
    )
  },
}))

function patchPointerCapture() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

function makeField(
  overrides: Partial<ConfigFieldSchema> & { name: string; ui_type: string },
): ConfigFieldSchema {
  return {
    type: 'string',
    default: '',
    description: '',
    required: false,
    label: overrides.name,
    hidden: false,
    disabled: false,
    order: 0,
    ...overrides,
  }
}

function makeSchema(
  sections: PluginConfigSchema['sections'],
  tabs: PluginConfigSchema['layout']['tabs'] | undefined = undefined,
): PluginConfigSchema {
  return {
    plugin_id: 'a_memorix',
    plugin_info: {
      name: 'A_Memorix',
      version: '2.0.0',
      description: '',
      author: 'A_Dawn',
    },
    layout: {
      type: tabs && tabs.length > 0 ? 'tabs' : 'auto',
      tabs: tabs ?? [],
    },
    sections,
  }
}

function formatEpisodeTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatTimelineTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const value = new Date(timestamp * 1000)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function toDatetimeLocal(timestamp?: number | null): string {
  if (!timestamp) {
    return ''
  }
  const value = new Date(timestamp * 1000)
  if (Number.isNaN(value.getTime())) {
    return ''
  }
  const offset = value.getTimezoneOffset()
  const local = new Date(value.getTime() - offset * 60_000)
  return local.toISOString().slice(0, 16)
}

function makeEpisode(overrides: Partial<MemoryEpisodeItemPayload> = {}): MemoryEpisodeItemPayload {
  return {
    episode_id: 'ep-1',
    title: '咖啡店相遇',
    summary: '在咖啡店聊天',
    source: 'chat_summary:qq:1',
    person_id: 'p1',
    person_name: '张三',
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    ...overrides,
  }
}

function makeEpisodeDetail(
  episode: MemoryEpisodeItemPayload,
  overrides: Partial<MemoryEpisodeDetailPayload> = {},
): MemoryEpisodeDetailPayload {
  return {
    success: true,
    episode,
    ...overrides,
  }
}

function makeEpisodeStatus(overrides: Partial<MemoryEpisodeStatusPayload> = {}): MemoryEpisodeStatusPayload {
  return {
    success: true,
    counts: { pending: 1, running: 2, done: 3, failed: 0 },
    failed: [],
    ...overrides,
  }
}

function makeAction(overrides: Partial<MemoryEpisodeActionPayload> = {}): MemoryEpisodeActionPayload {
  return {
    success: true,
    rebuilt: 2,
    ...overrides,
  }
}

function makeChatTarget(
  overrides: Partial<MemoryImportChatTargetPayload> & Pick<MemoryImportChatTargetPayload, 'chat_id' | 'chat_name' | 'is_group'>,
): MemoryImportChatTargetPayload {
  return {
    platform: 'qq',
    account_id: null,
    ...overrides,
  }
}

function makeTimelineEvent(
  index: number,
  overrides: Partial<MemoryTimelineEventPayload> = {},
): MemoryTimelineEventPayload {
  return {
    event_id: `evt-${index}`,
    event_type: 'paragraph_created',
    category: 'paragraph',
    occurred_at: 1_700_000_000 + index * 60,
    chat_id: 'chat-1',
    chat_name: '测试群',
    title: `事件${index}`,
    summary: `摘要${index}`,
    object_count: index,
    key_id: `key-${index}`,
    source: `src-${index}`,
    jump_target: { tab: 'graph', params: { id: `key-${index}` } },
    ...overrides,
  }
}

function makeTimelinePayload(
  items: MemoryTimelineEventPayload[],
  overrides: Partial<MemoryTimelinePayload> = {},
): MemoryTimelinePayload {
  return {
    success: true,
    chat: {
      chat_id: 'chat-1',
      chat_name: '测试群',
      platform: 'qq',
      is_group: true,
      group_id: 'g1',
      user_id: null,
      account_id: 'acc-1',
    },
    range: { min_time: 1_700_000_000, max_time: 1_700_100_000 },
    items,
    summary: {
      total: items.length,
      by_type: {
        paragraph: items.filter((item) => item.category === 'paragraph').length,
        episode: items.filter((item) => item.category === 'episode').length,
        profile: 0,
        feedback: 0,
        delete: 0,
        maintenance: 0,
      },
    },
    ...overrides,
  }
}

const chatTargets: MemoryImportChatTargetPayload[] = [
  makeChatTarget({ chat_id: 'webui-1', chat_name: 'WebUI 会话', platform: 'WebUI', is_group: false }),
  makeChatTarget({ chat_id: 'chat-1', chat_name: '测试群', platform: 'qq', is_group: true, account_id: 'acc-1' }),
  makeChatTarget({ chat_id: 'chat-2', chat_name: '私聊对象', platform: 'telegram', is_group: false, account_id: 'acc-2' }),
]

beforeEach(() => {
  patchPointerCapture()
  vi.mocked(memoryApi.getMemoryEpisodes).mockResolvedValue({ success: true, items: [] })
  vi.mocked(memoryApi.getMemoryEpisodeStatus).mockResolvedValue(makeEpisodeStatus())
  vi.mocked(memoryApi.getMemoryEpisode).mockResolvedValue(makeEpisodeDetail(makeEpisode()))
  vi.mocked(memoryApi.rebuildMemoryEpisodes).mockResolvedValue(makeAction())
  vi.mocked(memoryApi.processMemoryEpisodePending).mockResolvedValue(makeAction({ rebuilt: undefined, processed: 4 }))
  vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue(makeTimelinePayload([]))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('MemoryConfigEditor 无页签回退与字段类型', () => {
  it('没有 tabs 时按 section.order 平铺卡片，并跳过不存在的节', () => {
    const schema = makeSchema({
      later: {
        name: 'later',
        title: '靠后节',
        collapsed: false,
        order: 20,
        fields: {
          note: makeField({ name: 'note', ui_type: 'text', label: '备注文本', order: 1 }),
        },
      },
      earlier: {
        name: 'earlier',
        title: '靠前节',
        description: '靠前说明',
        collapsed: false,
        order: 1,
        fields: {
          title: makeField({ name: 'title', ui_type: 'text', label: '标题文本', order: 1 }),
        },
      },
    })

    const { container } = render(
      <MemoryConfigEditor schema={schema} config={{ earlier: { title: 'A' }, later: { note: 'B' } }} onChange={vi.fn()} />,
    )

    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    const titles = within(container).getAllByText(/靠前节|靠后节/)
    expect(titles[0]).toHaveTextContent('靠前节')
    expect(titles[1]).toHaveTextContent('靠后节')
    expect(screen.getByText('靠前说明')).toBeInTheDocument()
  })

  it('tabs 指向缺失节时不渲染该节，其余节正常展示', () => {
    const schema = makeSchema(
      {
        plugin: {
          name: 'plugin',
          title: '插件节',
          collapsed: false,
          order: 1,
          fields: {
            enabled: makeField({ name: 'enabled', ui_type: 'switch', label: '启用', type: 'boolean', default: true, order: 1 }),
          },
        },
      },
      [
        { id: 'basic', title: '基础', sections: ['missing', 'plugin'], order: 1 },
      ],
    )

    render(<MemoryConfigEditor schema={schema} config={{ plugin: { enabled: true } }} onChange={vi.fn()} />)
    expect(screen.getByRole('tab', { name: '基础' })).toBeInTheDocument()
    expect(screen.getByText('插件节')).toBeInTheDocument()
    expect(screen.queryByText('missing')).not.toBeInTheDocument()
  })

  it('number/select/textarea/list/json 变更写回嵌套配置，非法 JSON 只保留草稿', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const schema = makeSchema({
      'plugin.runtime': {
        name: 'plugin.runtime',
        title: '运行时',
        collapsed: false,
        order: 1,
        fields: {
          count: makeField({
            name: 'count',
            ui_type: 'number',
            type: 'number',
            label: '数量',
            default: 3,
            hint: '数字提示',
            order: 1,
          }),
          mode: makeField({
            name: 'mode',
            ui_type: 'select',
            label: '模式',
            choices: ['alpha', 'beta'],
            default: 'alpha',
            hint: '选择提示',
            order: 2,
          }),
          note: makeField({
            name: 'note',
            ui_type: 'textarea',
            label: '备注',
            default: 'hello',
            hint: '多行提示',
            order: 3,
          }),
          tags: makeField({
            name: 'tags',
            ui_type: 'list',
            label: '标签',
            item_type: 'string',
            default: ['keep'],
            hint: '列表提示',
            order: 4,
          }),
          extra: makeField({
            name: 'extra',
            ui_type: 'json',
            label: '附加 JSON',
            default: {},
            hint: 'JSON 提示',
            order: 5,
          }),
        },
      },
    })

    render(
      <MemoryConfigEditor
        schema={schema}
        config={{
          plugin: {
            keep: true,
            runtime: {
              count: 3,
              mode: 'alpha',
              note: 'hello',
              tags: ['keep'],
              extra: { a: 1 },
            },
          },
        }}
        onChange={onChange}
      />,
    )

    expect(screen.getByText('数字提示')).toBeInTheDocument()
    expect(screen.getByText('选择提示')).toBeInTheDocument()
    expect(screen.getByText('多行提示')).toBeInTheDocument()
    expect(screen.getByText('列表提示')).toBeInTheDocument()
    expect(screen.getByText('JSON 提示')).toBeInTheDocument()

    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '8' } })
    expect(onChange).toHaveBeenLastCalledWith({
      plugin: {
        keep: true,
        runtime: {
          count: 8,
          mode: 'alpha',
          note: 'hello',
          tags: ['keep'],
          extra: { a: 1 },
        },
      },
    })

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'beta' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({
      plugin: { runtime: { mode: 'beta' }, keep: true },
    })

    fireEvent.change(screen.getByDisplayValue('hello'), { target: { value: 'world' } })
    expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({
      plugin: { runtime: { note: 'world' } },
    })

    fireEvent.click(screen.getByRole('button', { name: '添加项目' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({
      plugin: { runtime: { tags: ['keep', ''] } },
    })

    const jsonBox = screen.getByDisplayValue(/"a": 1/)
    fireEvent.change(jsonBox, { target: { value: '{' } })
    const callsAfterInvalid = onChange.mock.calls.length
    expect(jsonBox).toHaveValue('{')
    fireEvent.change(jsonBox, { target: { value: '{"b":2}' } })
    expect(onChange.mock.calls.length).toBe(callsAfterInvalid + 1)
    expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({
      plugin: { runtime: { extra: { b: 2 } } },
    })
  })

  it('节路径终点不是对象时按空值渲染；list 无 default 时从空列表起步', () => {
    const onChange = vi.fn()
    const schema = makeSchema({
      plugin: {
        name: 'plugin',
        title: '插件节',
        collapsed: false,
        order: 1,
        fields: {
          tags: makeField({
            name: 'tags',
            ui_type: 'list',
            label: '标签',
            item_type: 'string',
            order: 1,
          }),
        },
      },
    })

    render(
      <MemoryConfigEditor
        schema={schema}
        config={{ plugin: ['not-an-object'] as unknown as Record<string, unknown> }}
        onChange={onChange}
      />,
    )

    expect(screen.getByText('暂无数据，点击下方按钮添加')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '添加项目' }))
    expect(onChange).toHaveBeenCalledWith({
      plugin: { tags: [''] },
    })
  })

  it('节路径中间值是数组时按空对象写入', () => {
    const onChange = vi.fn()
    const schema = makeSchema({
      'plugin.runtime': {
        name: 'plugin.runtime',
        title: '运行时',
        collapsed: false,
        order: 1,
        fields: {
          tags: makeField({
            name: 'tags',
            ui_type: 'list',
            label: '标签',
            item_type: 'string',
            default: ['from-default'],
            order: 1,
          }),
        },
      },
    })

    render(
      <MemoryConfigEditor
        schema={schema}
        config={{ plugin: [] as unknown as Record<string, unknown> }}
        onChange={onChange}
      />,
    )

    expect(screen.getByDisplayValue('from-default')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '添加项目' }))
    expect(onChange).toHaveBeenCalledWith({
      plugin: {
        runtime: {
          tags: ['from-default', ''],
        },
      },
    })
  })
})

describe('MemoryEpisodeManager 列表、筛选与空态', () => {
  async function renderEpisodes() {
    render(<MemoryEpisodeManager />)
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisodes).toHaveBeenCalled()
    })
  }

  it('首次加载中显示加载态，空列表与空详情展示占位', async () => {
    let resolveList: (value: MemoryEpisodeListPayload) => void = () => {}
    vi.mocked(memoryApi.getMemoryEpisodes).mockImplementation(
      () => new Promise((resolve) => {
        resolveList = resolve
      }),
    )

    render(<MemoryEpisodeManager />)
    expect(screen.getByLabelText('加载中')).toBeInTheDocument()
    expect(screen.queryByText('没有匹配的 Episode')).not.toBeInTheDocument()

    resolveList({ success: true, items: [] })
    expect(await screen.findByText('没有匹配的 Episode')).toBeInTheDocument()
    expect(screen.getByText('选择一个 Episode 查看详情。')).toBeInTheDocument()
  })

  it('渲染列表并自动选中第一项，点击另一行切换详情与标题回退', async () => {
    vi.mocked(memoryApi.getMemoryEpisodes).mockResolvedValue({
      success: true,
      items: [
        makeEpisode({
          paragraphs: [
            { hash: 'h1', preview: '段落预览' },
            { content: '仅有正文' },
          ],
        }),
        makeEpisode({
          episode_id: undefined,
          id: 'ep-2',
          title: undefined,
          summary: undefined,
          content: '只用正文当标题',
          source: undefined,
          person_name: undefined,
          person_id: 'p2',
          updated_at: Number.MAX_VALUE,
          created_at: 0,
        }),
        makeEpisode({
          episode_id: 'ep-3',
          title: undefined,
          summary: '摘要标题',
          person_name: '李四',
          person_id: undefined,
          updated_at: 1_700_000_000_000,
          created_at: 0,
        }),
        makeEpisode({
          episode_id: '',
          id: '',
          title: '无 ID 条目',
          source: 'orphan',
          updated_at: 1_700_111_111,
        }),
      ],
    })
    vi.mocked(memoryApi.getMemoryEpisode).mockImplementation(async (episodeId) => {
      if (episodeId === 'ep-1') {
        return makeEpisodeDetail(makeEpisode({
          paragraphs: [
            { hash: 'h1', preview: '段落预览' },
            { content: '仅有正文' },
          ],
        }))
      }
      return makeEpisodeDetail(makeEpisode({
        episode_id: episodeId,
        title: undefined,
        summary: undefined,
        content: '只用正文当标题',
        source: undefined,
        person_name: undefined,
        person_id: 'p2',
        paragraphs: undefined,
      }))
    })

    await renderEpisodes()

    expect(await screen.findByText('咖啡店相遇')).toBeInTheDocument()
    expect(screen.getByText('只用正文当标题')).toBeInTheDocument()
    expect(screen.getByText('摘要标题')).toBeInTheDocument()
    expect(screen.getAllByText(formatEpisodeTime(1_700_000_100)).length).toBeGreaterThan(0)
    expect(screen.getByText(formatEpisodeTime(1_700_000_000_000))).toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
    expect(screen.getByText('p2')).toBeInTheDocument()
    expect(screen.getByText('李四')).toBeInTheDocument()

    await waitFor(() => {
      expect(memoryApi.getMemoryEpisode).toHaveBeenCalledWith('ep-1')
    })
    expect(await screen.findByText('段落预览')).toBeInTheDocument()
    expect(screen.getByText('仅有正文')).toBeInTheDocument()

    fireEvent.click(screen.getByText('只用正文当标题'))
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisode).toHaveBeenLastCalledWith('ep-2')
    })
    expect(await screen.findByText('当前详情没有段落明细。')).toBeInTheDocument()

    fireEvent.click(screen.getByText('原始响应 JSON'))
    expect(screen.getByText(/"person_id": "p2"/)).toBeInTheDocument()

    // 无 ID 行会把 selectedId 置空，但已加载的详情不会被清掉
    fireEvent.click(screen.getByText('无 ID 条目'))
    expect(screen.getByDisplayValue('只用正文当标题')).toBeInTheDocument()
  })

  it('刷新时提交筛选条件；非法数量/时间戳回退，关闭高级入口不传 person_id', async () => {
    await renderEpisodes()

    fireEvent.change(screen.getByLabelText('平台'), { target: { value: ' qq ' } })
    fireEvent.change(screen.getByLabelText('用户账号'), { target: { value: ' 10086 ' } })
    fireEvent.change(screen.getByLabelText('关键词'), { target: { value: ' 咖啡 ' } })
    fireEvent.change(screen.getByLabelText('来源'), { target: { value: ' chat_summary:1 ' } })
    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('开始时间戳'), { target: { value: '1700000000' } })
    fireEvent.change(screen.getByLabelText('结束时间戳'), { target: { value: 'abc' } })
    fireEvent.click(screen.getByRole('button', { name: '高级查询' }))
    fireEvent.change(screen.getByLabelText('person_id'), { target: { value: '  p-debug  ' } })

    fireEvent.click(screen.getByRole('button', { name: /刷新 Episode/ }))
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisodes).toHaveBeenLastCalledWith({
        query: '咖啡',
        source: 'chat_summary:1',
        platform: 'qq',
        userId: '10086',
        personId: 'p-debug',
        limit: 5,
        timeStart: 1_700_000_000,
        timeEnd: undefined,
      })
    })

    fireEvent.click(screen.getByRole('button', { name: '高级查询' }))
    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '0' } })
    fireEvent.change(screen.getByLabelText('开始时间戳'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: /刷新 Episode/ }))
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisodes).toHaveBeenLastCalledWith({
        query: '咖啡',
        source: 'chat_summary:1',
        platform: 'qq',
        userId: '10086',
        personId: '',
        limit: 20,
        timeStart: undefined,
        timeEnd: undefined,
      })
    })
  })

  it('列表加载失败、详情加载失败分别弹出 toast，详情失败仍回退展示列表项', async () => {
    vi.mocked(memoryApi.getMemoryEpisodes)
      .mockRejectedValueOnce(new Error('列表挂了'))
      .mockResolvedValueOnce({ success: true, items: [makeEpisode()] })
    vi.mocked(memoryApi.getMemoryEpisode).mockRejectedValue('详情挂了')

    await renderEpisodes()
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载情节记忆失败',
          description: '列表挂了',
          variant: 'destructive',
        }),
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /刷新 Episode/ }))
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisode).toHaveBeenCalledWith('ep-1')
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载 Episode 详情失败',
          description: '详情挂了',
          variant: 'destructive',
        }),
      )
    })
    expect(await screen.findByText('咖啡店相遇')).toBeInTheDocument()
    expect(screen.getByDisplayValue('在咖啡店聊天')).toBeInTheDocument()
  })

  it('状态卡读取 counts 与顶层字段，失败来源告警只展示前三项', async () => {
    vi.mocked(memoryApi.getMemoryEpisodeStatus).mockResolvedValue({
      success: true,
      counts: { running: 2, failed: 0 },
      pending: 4,
      done: 9,
      failed: [
        { source: 'src-a' },
        { id: 'id-b' },
        { error: 'err-c' },
        { },
      ],
    })

    await renderEpisodes()
    expect(screen.getByText('待重建').parentElement).toHaveTextContent('4')
    expect(screen.getByText('运行中').parentElement).toHaveTextContent('2')
    expect(screen.getByText('已完成').parentElement).toHaveTextContent('9')
    expect(screen.getByText('失败来源').parentElement).toHaveTextContent('4')
    expect(screen.getByText(/最近失败来源：src-a、id-b、err-c/)).toBeInTheDocument()
  })

  it('initialEpisodeId/source/时间范围会回填并再次拉取', async () => {
    vi.mocked(memoryApi.getMemoryEpisodes).mockResolvedValue({
      success: true,
      items: [makeEpisode({ episode_id: 'ep-init' })],
    })

    render(
      <MemoryEpisodeManager
        initialEpisodeId="ep-init"
        initialSource="chat_summary:init"
        initialTimeStart={1_700_000_000}
        initialTimeEnd={1_700_000_500}
      />,
    )

    await waitFor(() => {
      expect(screen.getByLabelText('来源')).toHaveValue('chat_summary:init')
    })
    expect(screen.getByLabelText('开始时间戳')).toHaveValue('1700000000')
    expect(screen.getByLabelText('结束时间戳')).toHaveValue('1700000500')
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisodes).toHaveBeenCalledWith(
        expect.objectContaining({
          source: 'chat_summary:init',
          timeStart: 1_700_000_000,
          timeEnd: 1_700_000_500,
        }),
      )
    })
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisode).toHaveBeenCalledWith('ep-init')
    })
  })
})

describe('MemoryEpisodeManager 重建与待处理任务', () => {
  async function renderReady() {
    render(<MemoryEpisodeManager />)
    await waitFor(() => {
      expect(memoryApi.getMemoryEpisodes).toHaveBeenCalled()
    })
  }

  it('重建全部取消 confirm 不请求；确认后拆分来源并按返回描述提示', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderReady()

    fireEvent.change(screen.getByLabelText('来源 ID'), { target: { value: '  src-main  ' } })
    fireEvent.change(screen.getByLabelText('多个来源 ID'), { target: { value: ' src-a , src-b,, src-c ' } })
    fireEvent.click(screen.getByRole('button', { name: /重新生成 Episode/ }))
    await waitFor(() => {
      expect(memoryApi.rebuildMemoryEpisodes).toHaveBeenCalledWith({
        source: 'src-main',
        sources: ['src-a', 'src-b', 'src-c'],
        all: false,
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Episode 重建已提交',
          description: '已重建 2 个来源',
        }),
      )
    })

    fireEvent.click(screen.getByLabelText('重新生成全部可用来源'))
    fireEvent.click(screen.getByRole('button', { name: /重新生成 Episode/ }))
    expect(confirmSpy).toHaveBeenCalledWith('确认重建全部可用来源的 Episode？这个操作可能耗时较长。')
    expect(memoryApi.rebuildMemoryEpisodes).toHaveBeenCalledTimes(1)

    confirmSpy.mockReturnValue(true)
    vi.mocked(memoryApi.rebuildMemoryEpisodes).mockResolvedValueOnce({
      success: false,
      failures: [{ source: 'src-a', error: 'superseded' }],
    })
    fireEvent.click(screen.getByRole('button', { name: /重新生成 Episode/ }))
    await waitFor(() => {
      expect(memoryApi.rebuildMemoryEpisodes).toHaveBeenLastCalledWith({
        source: 'src-main',
        sources: ['src-a', 'src-b', 'src-c'],
        all: true,
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Episode 重建失败',
          description: 'src-a: 来源版本已更新',
          variant: 'destructive',
        }),
      )
    })
  })

  it('重建返回的 detail/error/未完成原因/通用失败文案都会展示', async () => {
    await renderReady()

    vi.mocked(memoryApi.rebuildMemoryEpisodes)
      .mockResolvedValueOnce({ success: true, detail: '自定义详情' })
      .mockResolvedValueOnce({ success: false, error: '直接错误' })
      .mockResolvedValueOnce({
        success: false,
        unfinished_items: [{ reason: 'lease_lost_or_claim_mismatch' }],
      })
      .mockResolvedValueOnce({ success: false, failed: 2, unfinished: 3 })
      .mockRejectedValueOnce(new Error('网络中断'))

    const rebuildButton = screen.getByRole('button', { name: /重新生成 Episode/ })

    fireEvent.click(rebuildButton)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ description: '自定义详情' }))
    })

    fireEvent.click(rebuildButton)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ description: '直接错误' }))
    })

    fireEvent.click(rebuildButton)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ description: '任务租约已失效或被其他进程接管' }),
      )
    })

    fireEvent.click(rebuildButton)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ description: '失败 2 项，未完成 3 项' }),
      )
    })

    fireEvent.click(rebuildButton)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Episode 重建失败',
          description: '网络中断',
          variant: 'destructive',
        }),
      )
    })
  })

  it('处理待重建：参数非法只提示；成功与失败走不同 toast', async () => {
    await renderReady()

    fireEvent.change(screen.getByLabelText('本次处理上限'), { target: { value: '201' } })
    fireEvent.change(screen.getByLabelText('最大尝试次数（含首次）'), { target: { value: '21' } })
    fireEvent.click(screen.getByRole('button', { name: /处理来源重建任务/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '处理参数无效',
          description: '本次处理上限必须是1至200的整数，最大尝试次数必须是1至20的整数。',
          variant: 'destructive',
        }),
      )
    })
    expect(memoryApi.processMemoryEpisodePending).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('本次处理上限'), { target: { value: '15' } })
    fireEvent.change(screen.getByLabelText('最大尝试次数（含首次）'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /处理来源重建任务/ }))
    await waitFor(() => {
      expect(memoryApi.processMemoryEpisodePending).toHaveBeenCalledWith({ limit: 15, max_retry: 2 })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '已处理来源重建任务',
          description: '已处理 4 项',
        }),
      )
    })

    vi.mocked(memoryApi.processMemoryEpisodePending).mockRejectedValueOnce(42)
    fireEvent.click(screen.getByRole('button', { name: /处理来源重建任务/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '处理来源重建任务失败',
          description: '42',
          variant: 'destructive',
        }),
      )
    })
  })

  it('处理待重建返回 not_claimed 原因时展示翻译文案', async () => {
    vi.mocked(memoryApi.processMemoryEpisodePending).mockResolvedValue({
      success: false,
      unfinished_items: [{ source: 'src-z', reason: 'not_claimed' }],
    })
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: /处理来源重建任务/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '处理来源重建任务失败',
          description: 'src-z: 本轮未领取到该来源任务',
          variant: 'destructive',
        }),
      )
    })
  })
})

describe('MemoryTimelineManager 范围、筛选与分页', () => {
  async function renderTimeline(
    props: Partial<Parameters<typeof MemoryTimelineManager>[0]> = {},
    options: { waitForLoad?: boolean } = {},
  ) {
    const onJump = props.onJump ?? vi.fn()
    const targets = props.chatTargets ?? chatTargets
    render(
      <MemoryTimelineManager
        chatTargets={targets}
        initialChatId={props.initialChatId}
        initialTimeStart={props.initialTimeStart}
        initialTimeEnd={props.initialTimeEnd}
        onJump={onJump}
      />,
    )
    if (options.waitForLoad !== false && (targets.length > 0 || props.initialChatId)) {
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /刷新时间线/ })).toBeEnabled()
      })
    }
    return { onJump }
  }

  it('没有聊天流时不请求时间线，并显示空列表', async () => {
    await renderTimeline({ chatTargets: [] })
    expect(screen.getByText('当前范围内没有可审计事件。')).toBeInTheDocument()
    expect(screen.getByText(/未选择聊天流/)).toBeInTheDocument()
    expect(memoryApi.getMemoryTimeline).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /刷新时间线/ })).toBeDisabled()
  })

  it('默认跳过 webui 聊天流，并格式化群聊/私聊选项', async () => {
    const user = userEvent.setup()
    await renderTimeline()

    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenCalledWith(
        expect.objectContaining({ chatId: 'chat-1', types: [], limit: 500 }),
      )
    })

    await user.click(screen.getAllByRole('combobox')[0])
    expect(await screen.findByRole('option', { name: /测试群 · 账号 acc-1 \(群聊 · qq\)/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /私聊对象 · 账号 acc-2 \(私聊 · telegram\)/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /WebUI 会话 \(私聊 · WebUI\)/ })).toBeInTheDocument()
  })

  it('渲染事件卡片：类型标签回退、空摘要/ID、归因和跳转', async () => {
    const events = [
      makeTimelineEvent(1, {
        event_type: 'episode_rebuilt',
        category: 'episode',
        attribution: '导入任务',
      }),
      makeTimelineEvent(2, {
        event_type: 'custom_event',
        category: 'custom_category' as MemoryTimelineEventPayload['category'],
        summary: '',
        key_id: '',
        source: '',
        occurred_at: 0,
      }),
    ]
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue(makeTimelinePayload(events, {
      summary: { total: 2, by_type: { paragraph: 0, episode: 1, profile: 0, feedback: 0, delete: 0, maintenance: 0 } },
    }))

    const { onJump } = await renderTimeline({ initialChatId: 'chat-1' })
    expect(await screen.findByText('事件1')).toBeInTheDocument()
    expect(screen.getByText('Episode 重建')).toBeInTheDocument()
    expect(screen.getByText('custom_event')).toBeInTheDocument()
    expect(screen.getByText('custom_category')).toBeInTheDocument()
    expect(screen.getByText('归因：导入任务')).toBeInTheDocument()
    expect(screen.getByText(formatTimelineTime(1_700_000_060))).toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByRole('button', { name: /跳转/ })[0])
    expect(onJump).toHaveBeenCalledWith({ tab: 'graph', params: { id: 'key-1' } })
  })

  it('加载失败弹出 toast，非 Error 转成字符串', async () => {
    vi.mocked(memoryApi.getMemoryTimeline).mockRejectedValueOnce(new Error('时间线挂了'))
    await renderTimeline({ initialChatId: 'chat-1' })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载审计时间线失败',
          description: '时间线挂了',
          variant: 'destructive',
        }),
      )
    })

    vi.mocked(memoryApi.getMemoryTimeline).mockRejectedValueOnce('后端 500')
    fireEvent.click(screen.getByRole('button', { name: /刷新时间线/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载审计时间线失败',
          description: '后端 500',
          variant: 'destructive',
        }),
      )
    })
  })

  it('快捷范围 24h/7d/30d 按当前时间写入起止秒', async () => {
    const nowMs = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockReturnValue(nowMs)
    const end = Math.floor(nowMs / 1000)
    await renderTimeline({ initialChatId: 'chat-1' })
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: '最近 24 小时' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: end - 24 * 3600, timeEnd: end }),
      )
    })
    expect(screen.getByLabelText('开始时间')).toHaveValue(toDatetimeLocal(end - 24 * 3600))

    fireEvent.click(screen.getByRole('button', { name: '最近 7 天' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: end - 7 * 24 * 3600, timeEnd: end }),
      )
    })

    fireEvent.click(screen.getByRole('button', { name: '最近 30 天' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: end - 30 * 24 * 3600, timeEnd: end }),
      )
    })
  })

  it('datetime-local 合法值转秒，空值与非法值清掉时间', async () => {
    await renderTimeline({ initialChatId: 'chat-1', initialTimeStart: 1_700_000_000, initialTimeEnd: 1_700_010_000 })
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalled())
    expect(screen.getByLabelText('开始时间')).toHaveValue(toDatetimeLocal(1_700_000_000))

    fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: '2026-01-15T12:00' } })
    const expectedStart = Math.floor(new Date('2026-01-15T12:00').getTime() / 1000)
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: expectedStart, timeEnd: 1_700_010_000 }),
      )
    })

    fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: '' } })
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: undefined, timeEnd: 1_700_010_000 }),
      )
    })

    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2026-13-40T99:99' } })
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ timeStart: undefined, timeEnd: undefined }),
      )
    })
  })

  it('非法时间戳在输入框和时间文案中回退为空/横杠；后续 initialChatId 不再覆盖', async () => {
    const events = [makeTimelineEvent(1, { occurred_at: Number.MAX_VALUE })]
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue(
      makeTimelinePayload(events, {
        range: { min_time: Number.MAX_VALUE, max_time: Number.MAX_VALUE },
      }),
    )

    const view = render(
      <MemoryTimelineManager
        chatTargets={chatTargets}
        initialChatId="chat-1"
        initialTimeStart={Number.MAX_VALUE}
        initialTimeEnd={Number.MAX_VALUE}
        onJump={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /刷新时间线/ })).toBeEnabled()
    })
    expect(screen.getByLabelText('开始时间')).toHaveValue('')
    expect(screen.getByLabelText('结束时间')).toHaveValue('')
    expect(screen.getByText('事件1')).toBeInTheDocument()

    view.rerender(
      <MemoryTimelineManager
        chatTargets={chatTargets}
        initialChatId="chat-2"
        onJump={vi.fn()}
      />,
    )
    expect(memoryApi.getMemoryTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ chatId: 'chat-1' }),
    )
    expect(memoryApi.getMemoryTimeline).not.toHaveBeenCalledWith(
      expect.objectContaining({ chatId: 'chat-2' }),
    )
  })

  it('只有 webui 聊天流时回退到第一项', async () => {
    await renderTimeline({
      chatTargets: [
        makeChatTarget({ chat_id: 'webui-only', chat_name: '', platform: 'webui', is_group: false }),
      ],
    })
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenCalledWith(
        expect.objectContaining({ chatId: 'webui-only' }),
      )
    })
    expect(screen.getByText(/webui-only \(私聊 · webui\)/)).toBeInTheDocument()
  })

  it('事件类型筛选会把 types 传给接口', async () => {
    const user = userEvent.setup()
    await renderTimeline({ initialChatId: 'chat-1' })
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalled())

    await user.click(screen.getAllByRole('combobox')[1])
    await user.click(await screen.findByRole('option', { name: 'Episode' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ['episode'] }),
      )
    })

    await user.click(screen.getAllByRole('combobox')[1])
    await user.click(await screen.findByRole('option', { name: '全部' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryTimeline).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: [] }),
      )
    })
  })

  it('分页、页码窗口与每页条数切换', async () => {
    const user = userEvent.setup()
    const items = Array.from({ length: 30 }, (_, index) => makeTimelineEvent(index + 1))
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue(makeTimelinePayload(items))
    await renderTimeline({ initialChatId: 'chat-1' })

    expect(await screen.findByText('事件1')).toBeInTheDocument()
    expect(screen.queryByText('事件6')).not.toBeInTheDocument()
    expect(screen.getByText(/第 1 \/ 6 页，每页 5 条/)).toBeInTheDocument()
    expect(screen.getByText(/当前显示 1-5 \/ 30 条/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '跳到第一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('事件6')).toBeInTheDocument()
    expect(screen.queryByText('事件1')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '跳到最后一页' }))
    expect(await screen.findByText('事件30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '6' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('button', { name: '1' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '跳到第一页' }))
    expect(await screen.findByText('事件1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '5' }))
    expect(await screen.findByText('事件21')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(await screen.findByText('事件16')).toBeInTheDocument()

    await user.click(screen.getByLabelText('每页显示条数'))
    await user.click(await screen.findByRole('option', { name: '20 条' }))
    expect(await screen.findByText('事件1')).toBeInTheDocument()
    expect(screen.getByText(/第 1 \/ 2 页，每页 20 条/)).toBeInTheDocument()
    expect(screen.getByText('事件20')).toBeInTheDocument()
  })

  it('滑块短值忽略，变更只改草稿，提交后才重新请求', async () => {
    vi.mocked(memoryApi.getMemoryTimeline).mockResolvedValue(
      makeTimelinePayload([], {
        range: { min_time: 1_700_000_000, max_time: 1_700_000_000 },
      }),
    )
    await renderTimeline({ initialChatId: 'chat-1' })
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalled())

    const startLabel = screen.getByText(/窗口开始：/)
    const before = startLabel.textContent
    const callsBefore = vi.mocked(memoryApi.getMemoryTimeline).mock.calls.length

    fireEvent.click(screen.getByRole('button', { name: '时间轴滑块短变更' }))
    fireEvent.click(screen.getByRole('button', { name: '时间轴滑块短提交' }))
    expect(startLabel.textContent).toBe(before)
    expect(memoryApi.getMemoryTimeline).toHaveBeenCalledTimes(callsBefore)

    fireEvent.click(screen.getByRole('button', { name: '时间轴滑块变更' }))
    expect(startLabel.textContent).not.toBe(before)

    fireEvent.click(screen.getByRole('button', { name: '时间轴滑块提交' }))
    await waitFor(() => {
      expect(vi.mocked(memoryApi.getMemoryTimeline).mock.calls.length).toBeGreaterThan(callsBefore)
      const last = vi.mocked(memoryApi.getMemoryTimeline).mock.calls.at(-1)?.[0]
      expect(last?.timeStart).toBeGreaterThan(1_700_000_000)
    })
  })

  it('过期请求的成功和失败都不会覆盖更新的结果', async () => {
    let resolveFirst: (value: MemoryTimelinePayload) => void = () => {}
    vi.mocked(memoryApi.getMemoryTimeline)
      .mockImplementationOnce(
        () => new Promise((resolve) => {
          resolveFirst = resolve
        }),
      )
      .mockResolvedValueOnce(makeTimelinePayload([makeTimelineEvent(9, { title: '新事件' })]))

    const user = userEvent.setup()
    await renderTimeline({ initialChatId: 'chat-1' }, { waitForLoad: false })
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalledTimes(1))

    await user.click(screen.getAllByRole('combobox')[1])
    await user.click(await screen.findByRole('option', { name: '人物画像' }))
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalledTimes(2))

    resolveFirst(makeTimelinePayload([makeTimelineEvent(1, { title: '旧事件' })]))
    expect(await screen.findByText('新事件')).toBeInTheDocument()
    expect(screen.queryByText('旧事件')).not.toBeInTheDocument()

    // 再发一笔会失败的过期请求，确认 catch 同样按 requestId 丢弃
    let rejectStale: (reason: unknown) => void = () => {}
    vi.mocked(memoryApi.getMemoryTimeline)
      .mockImplementationOnce(
        () => new Promise((_, reject) => {
          rejectStale = reject
        }),
      )
      .mockResolvedValueOnce(makeTimelinePayload([makeTimelineEvent(8, { title: '更新事件' })]))

    fireEvent.click(screen.getByRole('button', { name: /刷新时间线/ }))
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalledTimes(3))
    await user.click(screen.getAllByRole('combobox')[1])
    await user.click(await screen.findByRole('option', { name: '删除恢复' }))
    await waitFor(() => expect(memoryApi.getMemoryTimeline).toHaveBeenCalledTimes(4))
    rejectStale(new Error('过期失败'))
    expect(await screen.findByText('更新事件')).toBeInTheDocument()
    expect(toastMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ description: '过期失败' }),
    )
  })
})
