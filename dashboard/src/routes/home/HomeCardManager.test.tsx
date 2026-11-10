import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PluginHomeCard } from '@/lib/plugin-api'

import { HomeCardManager, type HomeCardDefinition } from './HomeCardManager'

const LAYOUT_KEY = 'maibot-home-card-layout-v1'
const LEGACY_HITOKOTO_STYLE_KEY = 'maibot-home-hitokoto-style'

const dndState = vi.hoisted(() => ({
  onDragEnd: undefined as
    | ((event: { active: { id: unknown }; over: { id: unknown } | null }) => void)
    | undefined,
}))

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

vi.mock('@dnd-kit/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@dnd-kit/core')>()
  return {
    ...actual,
    DndContext: ({
      children,
      onDragEnd,
    }: {
      children: ReactNode
      onDragEnd?: (event: { active: { id: unknown }; over: { id: unknown } | null }) => void
    }) => {
      dndState.onDragEnd = onDragEnd
      return <div>{children}</div>
    },
  }
})

function builtinCard(
  id: string,
  title: string,
  overrides: Partial<HomeCardDefinition> = {}
): HomeCardDefinition {
  return {
    id,
    title,
    source: 'builtin',
    render: () => <div>{`${title}内容`}</div>,
    ...overrides,
  }
}

function pluginCard(
  overrides: Partial<PluginHomeCard> & Pick<PluginHomeCard, 'id' | 'title'>
): PluginHomeCard {
  return {
    name: 'demo',
    plugin_id: 'demo',
    description: '',
    content: '插件正文',
    link_url: '',
    link_label: '',
    icon: '',
    width: 'medium',
    order: 0,
    enabled: true,
    ...overrides,
  }
}

function seedLayout(layout: Record<string, unknown>): void {
  window.localStorage.setItem(
    LAYOUT_KEY,
    JSON.stringify({
      hidden: [],
      order: [],
      rowModes: {},
      styles: {},
      widths: {},
      ...layout,
    })
  )
}

function storedLayout(): {
  hidden: string[]
  order: string[]
  rowModes: Record<string, string>
  styles: Record<string, string>
  widths: Record<string, string>
} {
  return JSON.parse(window.localStorage.getItem(LAYOUT_KEY) ?? '{}')
}

function visibleCardIds(): string[] {
  return [...document.querySelectorAll('[data-home-card-id]')].map(
    (node) => node.getAttribute('data-home-card-id') ?? ''
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
  dndState.onDragEnd = undefined
  document.getElementById('home-card-controls')?.remove()
})

describe('HomeCardManager 布局持久化', () => {
  it('卡片定义引用变化但布局内容不变时不重复写入 localStorage', () => {
    window.localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        hidden: [],
        order: ['builtin:test'],
        rowModes: {},
      })
    )
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const createCards = (): HomeCardDefinition[] => [
      {
        id: 'builtin:test',
        render: () => <div>测试卡片</div>,
        source: 'builtin',
        title: '测试',
      },
    ]

    const view = render(<HomeCardManager cards={createCards()} pluginCards={[]} />)
    expect(screen.getByText('测试卡片')).toBeInTheDocument()
    expect(setItemSpy).not.toHaveBeenCalled()

    view.rerender(<HomeCardManager cards={createCards()} pluginCards={[]} />)
    expect(setItemSpy).not.toHaveBeenCalled()
  })

  it('自动清理已移除卡片残留的布局配置', async () => {
    window.localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        hidden: ['builtin:removed'],
        order: ['builtin:removed', 'builtin:test'],
        rowModes: {
          0: 'low',
        },
        styles: { 'builtin:removed': 'orange' },
        widths: { 'builtin:removed': 'full' },
      })
    )

    render(
      <HomeCardManager
        cards={[
          {
            id: 'builtin:test',
            render: () => <div>测试卡片</div>,
            source: 'builtin',
            title: '测试',
          },
        ]}
        pluginCards={[]}
      />
    )

    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(LAYOUT_KEY) ?? '{}')).toEqual({
        hidden: [],
        order: ['builtin:test'],
        rowModes: { 0: 'low' },
        styles: {},
        widths: {},
      })
    })
  })

  it('编辑模式通过卡片通用接口编辑内容', async () => {
    const user = userEvent.setup()
    const onEdit = vi.fn()
    render(
      <HomeCardManager
        cards={[
          {
            id: 'builtin:editable',
            editLabel: '编辑测试内容',
            onEdit,
            render: () => <div>可编辑卡片</div>,
            source: 'builtin',
            title: '测试',
          },
        ]}
        pluginCards={[]}
      />
    )

    expect(screen.queryByRole('button', { name: '编辑测试内容' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    await user.click(screen.getByRole('button', { name: 'home.cards.editCard' }))
    await user.click(screen.getByRole('button', { name: '编辑测试内容' }))
    expect(onEdit).toHaveBeenCalledOnce()
  })

  it('所有卡片都能通过通用编辑入口切换并保存样式', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[
          {
            id: 'builtin:test',
            render: () => <div>测试卡片</div>,
            source: 'builtin',
            title: '测试',
          },
        ]}
        pluginCards={[]}
      />
    )

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    await user.click(screen.getByRole('button', { name: 'home.cards.editCard' }))
    await user.click(screen.getByRole('button', { name: /home.cards.styles.orange.title/ }))

    expect(screen.getByText('测试卡片').closest('[data-home-card-id]')).toHaveAttribute(
      'data-home-card-style',
      'orange'
    )
    expect(JSON.parse(window.localStorage.getItem(LAYOUT_KEY) ?? '{}')).toMatchObject({
      styles: { 'builtin:test': 'orange' },
    })
  })

  it('分隔元素使用紧凑行，并可在编辑模式拖拽和隐藏', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[
          {
            id: 'builtin:test',
            render: () => <div>普通卡片</div>,
            source: 'builtin',
            title: '测试',
          },
          {
            id: 'builtin:hitokoto',
            render: () => <div>一言内容</div>,
            source: 'builtin',
            title: '一言',
            variant: 'separator',
            width: 'full',
          },
        ]}
        pluginCards={[]}
      />
    )

    const separator = screen.getByText('一言内容').closest('[data-home-card-id]')
    expect(separator).toHaveAttribute('data-home-card-variant', 'separator')
    expect(separator?.parentElement?.getAttribute('style')).toContain('34px')

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    expect(screen.getByRole('button', { name: '拖拽排序：一言' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '从首页隐藏：一言' }))
    expect(screen.queryByText('一言内容')).not.toBeInTheDocument()
  })

  it('损坏的本地布局会回退并按当前卡片集合重建', async () => {
    window.localStorage.setItem(LAYOUT_KEY, '{not-json')

    render(
      <HomeCardManager
        cards={[builtinCard('builtin:a', '甲'), builtinCard('builtin:b', '乙')]}
        pluginCards={[]}
      />
    )

    await waitFor(() => {
      expect(storedLayout()).toMatchObject({
        hidden: [],
        order: ['builtin:a', 'builtin:b'],
        rowModes: {},
        styles: {},
        widths: {},
      })
    })
    expect(visibleCardIds()).toEqual(['builtin:a', 'builtin:b'])
  })

  it('旧版内置顺序会迁移，并兼容一言橙色样式', async () => {
    seedLayout({
      order: [
        'builtin:bot-status',
        'builtin:quick-actions',
        'builtin:stats-overview',
        'builtin:storage',
        'plugin:keep',
      ],
    })
    window.localStorage.setItem(LEGACY_HITOKOTO_STYLE_KEY, 'orange')

    render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:bot-status', '状态'),
          builtinCard('builtin:quick-actions', '快捷'),
          builtinCard('builtin:storage', '存储'),
          builtinCard('builtin:stats-overview', '统计'),
          builtinCard('builtin:hitokoto', '一言', { variant: 'separator', width: 'full' }),
        ]}
        pluginCards={[pluginCard({ id: 'plugin:keep', title: '保留插件' })]}
      />
    )

    await waitFor(() => {
      expect(storedLayout().order).toEqual([
        'builtin:bot-status',
        'builtin:quick-actions',
        'builtin:storage',
        'builtin:stats-overview',
        'builtin:hitokoto',
        'plugin:keep',
      ])
    })
    expect(screen.getByText('一言内容').closest('[data-home-card-id]')).toHaveAttribute(
      'data-home-card-style',
      'orange'
    )
  })
})

describe('HomeCardManager 添加/隐藏/重置', () => {
  it('可通过添加面板恢复已隐藏的卡片', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:a', '卡片甲', { description: '甲的说明' }),
          builtinCard('builtin:b', '卡片乙'),
        ]}
        pluginCards={[]}
      />
    )

    await user.click(screen.getByRole('button', { name: 'home.cards.add' }))
    expect(screen.getByText('home.cards.dialog.noHiddenCards')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'home.cards.dialog.cancel' }))

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    await user.click(screen.getByRole('button', { name: '从首页隐藏：卡片甲' }))
    expect(screen.queryByText('卡片甲内容')).not.toBeInTheDocument()
    expect(visibleCardIds()).toEqual(['builtin:b'])

    await waitFor(() => {
      expect(storedLayout().hidden).toEqual(['builtin:a'])
    })

    await user.click(screen.getByRole('button', { name: 'home.cards.add' }))
    expect(screen.getByText('home.cards.categories.status')).toBeInTheDocument()
    expect(screen.getByText('甲的说明')).toBeInTheDocument()
    const hiddenRow = screen.getByText('卡片甲').closest('div.flex')
    expect(hiddenRow).not.toBeNull()
    await user.click(
      within(hiddenRow as HTMLElement).getByRole('button', { name: 'home.cards.dialog.restore' })
    )

    expect(screen.getByText('卡片甲内容')).toBeInTheDocument()
    expect(screen.getByText('home.cards.dialog.noHiddenCards')).toBeInTheDocument()
    await waitFor(() => {
      expect(storedLayout().hidden).toEqual([])
    })
  })

  it('默认隐藏的新卡片进入添加列表，重置后重新隐藏', async () => {
    const user = userEvent.setup()
    const visible = builtinCard('builtin:visible', '可见卡')
    const hiddenByDefault = builtinCard('builtin:secret', '默认隐藏', {
      category: 'statistics',
      defaultHidden: true,
      description: '统计卡',
    })

    const view = render(<HomeCardManager cards={[visible]} pluginCards={[]} />)
    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:visible'])
    })

    view.rerender(<HomeCardManager cards={[visible, hiddenByDefault]} pluginCards={[]} />)

    await waitFor(() => {
      expect(storedLayout()).toMatchObject({
        hidden: ['builtin:secret'],
        order: ['builtin:visible', 'builtin:secret'],
      })
    })
    expect(screen.queryByText('默认隐藏内容')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.add' }))
    expect(screen.getByText('home.cards.categories.statistics')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'home.cards.dialog.restore' }))
    expect(screen.getByText('默认隐藏内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.dialog.reset' }))
    expect(screen.queryByText('默认隐藏内容')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(storedLayout()).toMatchObject({
        hidden: ['builtin:secret'],
        order: ['builtin:visible', 'builtin:secret'],
        rowModes: {},
        styles: {},
        widths: {},
      })
    })
  })

  it('重置布局会恢复默认顺序并清掉隐藏、样式与宽度', async () => {
    const user = userEvent.setup()
    seedLayout({
      hidden: ['builtin:c'],
      order: ['builtin:b', 'builtin:a', 'builtin:c'],
      rowModes: { 0: 'high' },
      styles: { 'builtin:a': 'orange' },
      widths: { 'builtin:a': 'full' },
    })

    render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:a', '甲', { allowedWidths: ['small', 'medium', 'full'], width: 'medium' }),
          builtinCard('builtin:b', '乙'),
          builtinCard('builtin:c', '丙'),
        ]}
        pluginCards={[pluginCard({ id: 'plugin:x', title: '插件X' })]}
      />
    )

    expect(screen.queryByText('丙内容')).not.toBeInTheDocument()
    expect(screen.getByText('甲内容').closest('[data-home-card-id]')).toHaveAttribute(
      'data-home-card-style',
      'orange'
    )
    expect(visibleCardIds()).toEqual(['builtin:b', 'builtin:a', 'plugin:x'])

    await user.click(screen.getByRole('button', { name: 'home.cards.reset' }))

    expect(screen.getByText('丙内容')).toBeInTheDocument()
    expect(screen.getByText('甲内容').closest('[data-home-card-id]')).toHaveAttribute(
      'data-home-card-style',
      'default'
    )
    expect(visibleCardIds()).toEqual(['builtin:a', 'builtin:b', 'builtin:c', 'plugin:x'])
    await waitFor(() => {
      expect(storedLayout()).toEqual({
        hidden: [],
        order: ['builtin:a', 'builtin:b', 'builtin:c', 'plugin:x'],
        rowModes: {},
        styles: {},
        widths: {},
      })
    })
  })
})

describe('HomeCardManager 拖拽排序', () => {
  it('拖拽排序会重排可见卡片并把隐藏卡片留在末尾', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:a', '甲'),
          builtinCard('builtin:b', '乙'),
          builtinCard('builtin:c', '丙'),
        ]}
        pluginCards={[]}
      />
    )

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    await user.click(screen.getByRole('button', { name: '从首页隐藏：丙' }))
    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:a', 'builtin:b', 'builtin:c'])
      expect(storedLayout().hidden).toEqual(['builtin:c'])
    })

    expect(dndState.onDragEnd).toBeDefined()
    act(() => {
      dndState.onDragEnd?.({
        active: { id: 'builtin:a' },
        over: { id: 'builtin:b' },
      })
    })

    expect(visibleCardIds()).toEqual(['builtin:b', 'builtin:a'])
    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:b', 'builtin:a', 'builtin:c'])
    })
  })

  it('无效拖拽不会改动顺序', async () => {
    render(
      <HomeCardManager
        cards={[builtinCard('builtin:a', '甲'), builtinCard('builtin:b', '乙')]}
        pluginCards={[]}
      />
    )

    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:a', 'builtin:b'])
    })
    const snapshot = window.localStorage.getItem(LAYOUT_KEY)

    act(() => {
      dndState.onDragEnd?.({ active: { id: 'builtin:a' }, over: null })
      dndState.onDragEnd?.({ active: { id: 'builtin:a' }, over: { id: 'builtin:a' } })
      dndState.onDragEnd?.({ active: { id: 'missing' }, over: { id: 'builtin:b' } })
    })

    expect(visibleCardIds()).toEqual(['builtin:a', 'builtin:b'])
    expect(window.localStorage.getItem(LAYOUT_KEY)).toBe(snapshot)
  })
})

describe('HomeCardManager 插件卡片合并与内容', () => {
  it('新插件卡片会按默认邻接关系合并进现有顺序', async () => {
    seedLayout({
      order: ['builtin:b', 'builtin:a'],
    })
    const cards = [builtinCard('builtin:a', '甲'), builtinCard('builtin:b', '乙')]
    const view = render(<HomeCardManager cards={cards} pluginCards={[]} />)

    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:b', 'builtin:a'])
    })

    view.rerender(
      <HomeCardManager
        cards={cards}
        pluginCards={[pluginCard({ id: 'plugin:x', title: '插件X', description: '来自插件' })]}
      />
    )

    await waitFor(() => {
      // 缺省顺序是 [a, b, plugin]，缺失的 plugin 插到已存在的前驱 b 后面
      expect(storedLayout().order).toEqual(['builtin:b', 'plugin:x', 'builtin:a'])
    })
    expect(visibleCardIds()).toEqual(['builtin:b', 'plugin:x', 'builtin:a'])
    expect(screen.getByText('插件X')).toBeInTheDocument()
    expect(screen.getByText('来自插件')).toBeInTheDocument()
    expect(screen.getByText('插件')).toBeInTheDocument()
  })

  it('仅含插件的自定义顺序会把后到的内置卡片插到插件前面', async () => {
    seedLayout({ order: ['plugin:x'] })

    render(
      <HomeCardManager
        cards={[builtinCard('builtin:a', '甲')]}
        pluginCards={[pluginCard({ id: 'plugin:x', title: '插件X' })]}
      />
    )

    await waitFor(() => {
      expect(storedLayout().order).toEqual(['builtin:a', 'plugin:x'])
    })
    expect(visibleCardIds()).toEqual(['builtin:a', 'plugin:x'])
  })

  it('移除插件后会清掉对应布局项', async () => {
    seedLayout({
      hidden: [],
      order: ['builtin:a', 'plugin:gone', 'builtin:b'],
      styles: { 'plugin:gone': 'orange' },
      widths: { 'plugin:gone': 'wide' },
    })
    const cards = [builtinCard('builtin:a', '甲'), builtinCard('builtin:b', '乙')]
    const view = render(
      <HomeCardManager
        cards={cards}
        pluginCards={[pluginCard({ id: 'plugin:gone', title: '即将卸载' })]}
      />
    )

    expect(screen.getByText('即将卸载')).toBeInTheDocument()
    view.rerender(<HomeCardManager cards={cards} pluginCards={[]} />)

    await waitFor(() => {
      expect(storedLayout()).toMatchObject({
        hidden: [],
        order: ['builtin:a', 'builtin:b'],
        styles: {},
        widths: {},
      })
    })
    expect(screen.queryByText('即将卸载')).not.toBeInTheDocument()
  })

  it('插件卡片渲染 markdown/统计/键值/列表/操作与空内容', () => {
    render(
      <HomeCardManager
        cards={[]}
        pluginCards={[
          pluginCard({
            id: 'plugin:blocks',
            title: '块卡片',
            link_label: '',
            link_url: 'https://example.com/open',
            content: [
              { type: 'markdown', content: '  ', text: 'Markdown正文' },
              { type: 'stat', title: '在线', content: '12', description: '刚才' },
              { type: 'key_value', entries: { 平台: 'QQ', 空值: null } },
              { type: 'list', items: ['一项', '二项'] },
              {
                type: 'actions',
                actions: [
                  { url: 'https://example.com/a', label: '外链' },
                  { href: '/local', title: '内链' },
                  null,
                  'skip',
                  { url: 'javascript:void(0)', label: '危险' },
                  { url: '//evil.com', label: '协议相对' },
                  { url: 'https://example.com/b' },
                ],
              },
              { type: 'unknown', value: '回落文本' },
            ],
          }),
          pluginCard({
            id: 'plugin:object',
            title: '对象卡片',
            content: { type: 'stat', label: '延迟', value: '8ms', detail: '良好' },
          }),
          pluginCard({
            id: 'plugin:empty-string',
            title: '空字符串',
            content: '   ',
            link_label: '进入',
            link_url: '/inside',
            show_title: false,
          }),
          pluginCard({
            id: 'plugin:empty-null',
            title: '空对象',
            content: null as unknown as PluginHomeCard['content'],
          }),
        ]}
      />
    )

    expect(screen.getByText('Markdown正文')).toBeInTheDocument()
    expect(screen.getByText('在线')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('刚才')).toBeInTheDocument()
    expect(screen.getByText('平台')).toBeInTheDocument()
    expect(screen.getByText('QQ')).toBeInTheDocument()
    expect(screen.getByText('一项')).toBeInTheDocument()
    expect(screen.getByText('二项')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '外链' })).toHaveAttribute('href', 'https://example.com/a')
    expect(screen.getByRole('link', { name: '外链' })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: '内链' })).toHaveAttribute('href', '/local')
    expect(screen.getByRole('link', { name: '内链' })).not.toHaveAttribute('target')
    expect(screen.queryByRole('link', { name: '危险' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '协议相对' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://example.com/b' })).toBeInTheDocument()
    expect(screen.getByText('回落文本')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /打开/ })).toHaveAttribute(
      'href',
      'https://example.com/open'
    )

    expect(screen.getByText('延迟')).toBeInTheDocument()
    expect(screen.getByText('8ms')).toBeInTheDocument()
    expect(screen.getByText('良好')).toBeInTheDocument()

    expect(screen.getAllByText('暂无内容')).toHaveLength(2)
    expect(screen.queryByText('空字符串')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '进入' })).toHaveAttribute('href', '/inside')
    expect(screen.getByText('空对象')).toBeInTheDocument()
  })

  it('插件正文会过滤危险链接，隐藏后按插件分类出现在添加面板', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[]}
        pluginCards={[
          pluginCard({
            id: 'plugin:md',
            title: '链接卡片',
            content: '[好链](https://example.com) [坏链](javascript:alert(1)) [相对](/docs)',
          }),
        ]}
      />
    )

    expect(screen.getByRole('link', { name: '好链' })).toHaveAttribute('href', 'https://example.com')
    expect(screen.getByRole('link', { name: '相对' })).toHaveAttribute('href', '/docs')
    expect(screen.queryByRole('link', { name: '坏链' })).not.toBeInTheDocument()
    expect(screen.getByText('坏链')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    await user.click(screen.getByRole('button', { name: '从首页隐藏：链接卡片' }))
    await user.click(screen.getByRole('button', { name: 'home.cards.add' }))
    expect(screen.getByText('home.cards.categories.plugin')).toBeInTheDocument()
    expect(screen.getByText('链接卡片')).toBeInTheDocument()
  })
})

describe('HomeCardManager 自适应宽度与行高', () => {
  it('溢出时先尝试缩一档宽度，放不下则换行', () => {
    const view = render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:large', '大卡', { width: 'large' }),
          builtinCard('builtin:wide', '宽卡', { width: 'wide' }),
        ]}
        pluginCards={[]}
      />
    )

    const grid = document.querySelector('[data-home-summary-cards]')
    expect(screen.getByText('大卡内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-5')
    // 同行剩余 5 列，wide(7) 缩一档后变成 large(5)
    expect(screen.getByText('宽卡内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-5')
    expect(grid?.getAttribute('style')).toContain('192px')
    expect(grid?.getAttribute('style')).not.toContain('360px')

    view.rerender(
      <HomeCardManager
        cards={[
          builtinCard('builtin:large', '大卡', { width: 'large' }),
          builtinCard('builtin:full', '整行', { width: 'full' }),
        ]}
        pluginCards={[]}
      />
    )
    expect(screen.getByText('整行内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-10')
    expect(document.querySelector('[data-home-summary-cards]')?.getAttribute('style')).toContain(
      '192px 360px'
    )

    view.rerender(
      <HomeCardManager
        cards={[
          builtinCard('builtin:wide', '宽卡', { width: 'wide' }),
          builtinCard('builtin:s1', '小一', { width: 'small' }),
          builtinCard('builtin:s2', '小二', { width: 'small' }),
        ]}
        pluginCards={[]}
      />
    )
    expect(screen.getByText('宽卡内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-7')
    expect(screen.getByText('小一内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-2')
    expect(screen.getByText('小二内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-2')
    expect(document.querySelector('[data-home-summary-cards]')?.getAttribute('style')).toContain(
      '192px 360px'
    )

    view.rerender(
      <HomeCardManager
        cards={[
          builtinCard('builtin:medium', '中卡', { width: 'medium' }),
          builtinCard('builtin:separator', '分隔', { variant: 'separator', width: 'full' }),
          builtinCard('builtin:wide', '宽卡', { width: 'wide' }),
        ]}
        pluginCards={[]}
      />
    )
    expect(screen.getByText('分隔内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-10')
    expect(screen.getByText('宽卡内容').closest('[data-home-card-id]')).toHaveClass('lg:col-span-7')
  })

  it('编辑模式可切换行高并循环调整卡片宽度', async () => {
    const user = userEvent.setup()
    render(
      <HomeCardManager
        cards={[
          builtinCard('builtin:flexible', '弹性', {
            allowedWidths: ['small', 'medium', 'large'],
            preferredHeight: 'high',
            width: 'medium',
          }),
          builtinCard('builtin:plain', '普通'),
        ]}
        pluginCards={[]}
      />
    )

    const flexible = () => screen.getByText('弹性内容').closest('[data-home-card-id]')
    expect(flexible()).toHaveClass('lg:col-span-3')
    expect(document.querySelector('[data-home-summary-cards]')?.getAttribute('style')).toContain(
      '360px'
    )

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    expect(screen.queryByRole('button', { name: '调整尺寸：普通' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'home.cards.row.high' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.row.high' }))
    expect(screen.getByRole('button', { name: 'home.cards.row.low' })).toBeInTheDocument()
    await waitFor(() => {
      expect(storedLayout().rowModes).toEqual({ 0: 'low' })
    })
    expect(document.querySelector('[data-home-summary-cards]')?.getAttribute('style')).toContain(
      '192px'
    )

    await user.click(screen.getByRole('button', { name: '调整尺寸：弹性' }))
    expect(flexible()).toHaveClass('lg:col-span-5')
    await user.click(screen.getByRole('button', { name: '调整尺寸：弹性' }))
    expect(flexible()).toHaveClass('lg:col-span-2')
    await user.click(screen.getByRole('button', { name: '调整尺寸：弹性' }))
    expect(flexible()).toHaveClass('lg:col-span-3')
    await waitFor(() => {
      expect(storedLayout().widths).toEqual({ 'builtin:flexible': 'medium' })
    })
  })

  it('控件可渲染到指定 portal', () => {
    const portal = document.createElement('div')
    portal.id = 'home-card-controls'
    document.body.appendChild(portal)

    render(
      <HomeCardManager
        cards={[builtinCard('builtin:a', '甲')]}
        controlsPortalId="home-card-controls"
        pluginCards={[]}
      />
    )

    expect(within(portal).getByRole('button', { name: 'home.cards.add' })).toBeInTheDocument()
    expect(within(portal).getByRole('button', { name: 'home.cards.reset' })).toBeInTheDocument()
    expect(within(portal).getByRole('button', { name: 'home.cards.edit' })).toBeInTheDocument()
  })
})
