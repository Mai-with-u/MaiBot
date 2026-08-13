import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ChartContainer,
  ChartLegendContent,
  ChartStyle,
  ChartTooltipContent,
} from '../chart'
import { DraftNumberInput } from '../draft-number-input'
import { ExtraParamsDialog } from '../extra-params-dialog'
import { FloatingPanel } from '../floating-panel'
import { KeyValueEditor } from '../key-value-editor'
import { MultiSelect } from '../multi-select'

// jsdom 未实现 Pointer Capture，浮动面板拖拽与 Radix 浮层会调用这些方法
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

const dndState = vi.hoisted(() => ({
  onDragEnd: undefined as
    | ((event: { active: { id: unknown }; over: { id: unknown } | null }) => void)
    | undefined,
  isDragging: false,
}))

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    // 不走真实 canvas 测量，只保证 ChartContainer 能把子节点挂进上下文
    ResponsiveContainer: ({ children }: { children?: ReactNode }) => (
      <div data-testid="chart-responsive">{children}</div>
    ),
  }
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
      return <div data-testid="multi-select-dnd">{children}</div>
    },
  }
})

vi.mock('@dnd-kit/sortable', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@dnd-kit/sortable')>()
  return {
    ...actual,
    useSortable: () => ({
      attributes: {},
      listeners: {},
      setNodeRef: () => {},
      transform: null,
      transition: undefined,
      isDragging: dndState.isDragging,
    }),
  }
})

function DesktopIcon() {
  return <svg data-testid="desktop-icon" />
}

const MULTI_OPTIONS = [
  { label: '苹果', value: 'apple' },
  { label: '香蕉', value: 'banana' },
  { label: '樱桃', value: 'cherry' },
]

function setViewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
  Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: height })
}

function mockRect(
  element: Element,
  rect: Partial<DOMRect> & Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>
) {
  return vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
    x: rect.left,
    y: rect.top,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height,
    toJSON: () => ({}),
    ...rect,
  } as DOMRect)
}

function getBadgeRemoveControl(label: string): HTMLElement {
  const text = screen.getByText(label)
  const remove = text.nextElementSibling
  if (!(remove instanceof HTMLElement)) {
    throw new Error(`未找到标签 ${label} 的删除控件`)
  }
  return remove
}

function getMultiSelectTrigger(): HTMLElement {
  const trigger = screen.getAllByRole('combobox').find((node) => node.tagName === 'BUTTON')
  if (!trigger) {
    throw new Error('未找到 MultiSelect 触发按钮')
  }
  return trigger
}

afterEach(() => {
  cleanup()
  dndState.onDragEnd = undefined
  dndState.isDragging = false
})

describe('Chart 容器与样式回退', () => {
  it('ChartContainer 写入 data-chart，并在无色配置时不注入 style', () => {
    const { container } = render(
      <ChartContainer id="stats" className="custom-chart" config={{ visits: { label: '访问' } }}>
        <div>占位图</div>
      </ChartContainer>
    )

    const root = container.querySelector('[data-chart="chart-stats"]')
    expect(root).not.toBeNull()
    expect(root).toHaveClass('custom-chart', 'aspect-video')
    expect(screen.getByTestId('chart-responsive')).toHaveTextContent('占位图')
    expect(root?.querySelector('style')).toBeNull()
  })

  it('ChartStyle 输出颜色变量，并跳过主题里的空颜色', () => {
    const { container } = render(
      <ChartStyle
        id="theme-chart"
        config={{
          visits: { color: '#4ade80' },
          mixed: { theme: { light: '', dark: '#111111' } },
        }}
      />
    )

    const css = container.querySelector('style')?.innerHTML ?? ''
    expect(css).toContain('[data-chart=theme-chart]')
    expect(css).toContain('--color-visits: #4ade80')
    expect(css).toContain('.dark [data-chart=theme-chart]')
    expect(css).toContain('--color-mixed: #111111')
    expect(css).not.toContain('--color-mixed: ;')
  })

  it('ChartTooltipContent 在 ChartContainer 外使用时抛出 useChart 错误', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<ChartTooltipContent active />)).toThrow(
      'useChart must be used within a <ChartContainer />'
    )
    spy.mockRestore()
  })
})

describe('ChartTooltipContent', () => {
  const config = {
    desktop: { label: '桌面', color: '#111', icon: DesktopIcon },
    mobile: { label: '移动', color: '#222' },
    mapped: { label: '映射项', color: '#333' },
  }

  function renderTooltip(props: Record<string, unknown>) {
    return render(
      <ChartContainer config={config}>
        <ChartTooltipContent {...props} />
      </ChartContainer>
    )
  }

  it('未激活或 payload 为空时不渲染内容', () => {
    const { rerender } = renderTooltip({
      active: false,
      payload: [{ dataKey: 'desktop', name: 'desktop', value: 1, payload: {} }],
    })
    expect(screen.queryByText('桌面')).not.toBeInTheDocument()

    rerender(
      <ChartContainer config={config}>
        <ChartTooltipContent active payload={[]} />
      </ChartContainer>
    )
    expect(screen.queryByText('桌面')).not.toBeInTheDocument()
  })

  it('hideLabel 时不展示标签，即便提供了 labelFormatter', () => {
    renderTooltip({
      active: true,
      hideLabel: true,
      label: 'desktop',
      labelFormatter: () => '不该出现',
      payload: [{ dataKey: 'desktop', name: 'desktop', value: 8, payload: {} }],
    })
    expect(screen.queryByText('不该出现')).not.toBeInTheDocument()
    expect(screen.getByText('桌面')).toBeInTheDocument()
  })

  it('字符串 label 优先取 config[label].label，否则回退到原始 label', () => {
    const { rerender } = renderTooltip({
      active: true,
      label: 'desktop',
      payload: [{ dataKey: 'desktop', name: 'desktop', value: 3, payload: {} }],
    })
    expect(screen.getAllByText('桌面').length).toBeGreaterThan(0)

    rerender(
      <ChartContainer config={config}>
        <ChartTooltipContent
          active
          label="未知轴"
          payload={[{ dataKey: 'desktop', name: 'desktop', value: 3, payload: {} }]}
        />
      </ChartContainer>
    )
    expect(screen.getByText('未知轴')).toBeInTheDocument()
  })

  it('label 为空字符串时不渲染标签节点', () => {
    renderTooltip({
      active: true,
      label: '',
      payload: [{ dataKey: 'missing', name: 'missing', value: 1, payload: {} }],
    })
    expect(screen.queryByText('missing')).toBeInTheDocument()
    expect(screen.queryByText('桌面')).not.toBeInTheDocument()
  })

  it('labelKey 从 payload 嵌套字段解析配置，并用 labelFormatter 包装', () => {
    renderTooltip({
      active: true,
      labelKey: 'kind',
      labelFormatter: (value: unknown) => `格式化:${String(value)}`,
      payload: [
        {
          dataKey: 'visits',
          name: 'visits',
          value: 12,
          kind: 'mapped',
          payload: {},
        },
      ],
    })
    expect(screen.getByText('格式化:映射项')).toBeInTheDocument()
  })

  it('嵌套 payload 字符串键、type=none 过滤、formatter 与 0 值分支', () => {
    const formatter = vi.fn((value: number | string, name: string) => (
      <span>{`自定义:${name}=${value}`}</span>
    ))
    renderTooltip({
      active: true,
      nameKey: 'series',
      payload: [
        { type: 'none', dataKey: 'ghost', name: 'ghost', value: 99, payload: {} },
        {
          dataKey: 'visits',
          name: 'visits',
          series: 'ignored-by-nested',
          value: 0,
          payload: { series: 'desktop', fill: '#abc' },
        },
        {
          dataKey: 'mobile',
          name: 'mobile',
          value: 1500,
          color: '#999',
          payload: {},
        },
      ],
      formatter,
    })

    // value===0 仍会走 formatter（!== undefined），type=none 被丢掉
    expect(screen.getByText('自定义:visits=0')).toBeInTheDocument()
    expect(screen.getByText('自定义:mobile=1500')).toBeInTheDocument()
    expect(screen.queryByText('99')).not.toBeInTheDocument()
    expect(formatter).toHaveBeenCalledTimes(2)
  })

  it('无 formatter 时展示图标、toLocaleString 数值，以及 line/dashed 指示器', () => {
    const { rerender } = renderTooltip({
      active: true,
      indicator: 'dot',
      payload: [
        {
          dataKey: 'desktop',
          name: 'desktop',
          value: 1234,
          payload: { fill: '#4ade80' },
        },
      ],
    })
    expect(screen.getByTestId('desktop-icon')).toBeInTheDocument()
    expect(screen.getByText((1234).toLocaleString())).toBeInTheDocument()

    rerender(
      <ChartContainer config={{ mobile: { label: '移动', color: '#222' } }}>
        <ChartTooltipContent
          active
          indicator="line"
          color="#ff0"
          payload={[
            {
              dataKey: 'mobile',
              name: 'mobile',
              value: 8,
              payload: {},
            },
          ]}
        />
      </ChartContainer>
    )
    // 单条 + 非 dot：标签嵌进行内（nestLabel），标题与系列名都会出现
    expect(screen.getAllByText('移动').length).toBeGreaterThan(1)
    expect(screen.getByText('8')).toBeInTheDocument()

    rerender(
      <ChartContainer config={{ mobile: { label: '移动', color: '#222' } }}>
        <ChartTooltipContent
          active
          indicator="dashed"
          hideIndicator={false}
          payload={[
            {
              dataKey: 'mobile',
              name: 'mobile',
              value: 2,
              color: '#0f0',
              payload: {},
            },
          ]}
        />
      </ChartContainer>
    )
    expect(screen.getAllByText('移动').length).toBeGreaterThan(0)
  })

  it('hideIndicator 且无 icon 时不渲染色块指示器', () => {
    render(
      <ChartContainer config={{ mobile: { label: '移动' } }}>
        <ChartTooltipContent
          active
          hideIndicator
          payload={[{ dataKey: 'mobile', name: 'mobile', value: 4, payload: {} }]}
        />
      </ChartContainer>
    )
    expect(screen.getAllByText('移动').length).toBeGreaterThan(0)
    expect(screen.queryByTestId('desktop-icon')).not.toBeInTheDocument()
  })

  it('configLabelKey 不在 config 中时回退到原始 key 配置', () => {
    render(
      <ChartContainer config={{ kind: { label: '种类回退' }, desktop: { label: '桌面' } }}>
        <ChartTooltipContent
          active
          nameKey="kind"
          payload={[
            {
              dataKey: 'desktop',
              name: 'desktop',
              value: 1,
              payload: { kind: 'missing-key' },
            },
          ]}
        />
      </ChartContainer>
    )
    expect(screen.getByText('种类回退')).toBeInTheDocument()
  })
})

describe('ChartLegendContent', () => {
  it('空 payload 不渲染；top/bottom 间距与 icon / hideIcon 分支', () => {
    const { rerender } = render(
      <ChartContainer config={{ desktop: { label: '桌面', icon: DesktopIcon } }}>
        <ChartLegendContent payload={[]} />
      </ChartContainer>
    )
    expect(screen.queryByText('桌面')).not.toBeInTheDocument()

    rerender(
      <ChartContainer config={{ desktop: { label: '桌面', icon: DesktopIcon } }}>
        <ChartLegendContent
          verticalAlign="top"
          payload={[
            { type: 'none', value: 'ghost', dataKey: 'ghost', payload: {} },
            { value: 'desktop', dataKey: 'desktop', color: '#111', payload: {} },
          ]}
        />
      </ChartContainer>
    )
    const topLabel = screen.getByText('桌面')
    expect(screen.getByTestId('desktop-icon')).toBeInTheDocument()
    expect(topLabel).toHaveClass('gap-1.5')
    expect(topLabel.parentElement).toHaveClass('pb-3')

    rerender(
      <ChartContainer config={{ desktop: { label: '桌面', icon: DesktopIcon } }}>
        <ChartLegendContent
          hideIcon
          verticalAlign="bottom"
          payload={[{ value: 'desktop', dataKey: 'desktop', color: '#abc', payload: {} }]}
        />
      </ChartContainer>
    )
    expect(screen.queryByTestId('desktop-icon')).not.toBeInTheDocument()
    expect(screen.getByText('桌面').parentElement).toHaveClass('pt-3')
  })
})

describe('ExtraParamsDialog', () => {
  it('打开时用最新 value 重置草稿，保存提交编辑结果', () => {
    const onChange = vi.fn()
    const onOpenChange = vi.fn()
    const { rerender } = render(
      <ExtraParamsDialog
        open={false}
        value={{ temperature: 0.2 }}
        onChange={onChange}
        onOpenChange={onOpenChange}
      />
    )
    expect(screen.queryByRole('dialog', { name: '编辑额外参数' })).not.toBeInTheDocument()

    rerender(
      <ExtraParamsDialog
        open
        value={{ temperature: 0.8 }}
        onChange={onChange}
        onOpenChange={onOpenChange}
      />
    )

    const dialog = screen.getByRole('dialog', { name: '编辑额外参数' })
    expect(within(dialog).getByText('配置模型调用时的额外参数，支持嵌套对象和数组')).toBeInTheDocument()
    const valueInput = within(dialog).getByDisplayValue('0.8')
    fireEvent.change(valueInput, { target: { value: '1.2' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(onChange).toHaveBeenCalledWith({ temperature: 1.2 })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('取消会丢弃草稿并关闭，不回写 onChange', () => {
    const onChange = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <ExtraParamsDialog
        open
        value={{ foo: 'bar' }}
        onChange={onChange}
        onOpenChange={onOpenChange}
      />
    )
    fireEvent.change(screen.getByDisplayValue('bar'), { target: { value: 'baz' } })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('validate 失败时展示错误、禁用保存，强制点击也不会提交', () => {
    const onChange = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <ExtraParamsDialog
        open
        value={{ thinking: true }}
        onChange={onChange}
        onOpenChange={onOpenChange}
        validate={() => '额外参数不合法'}
      />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('额外参数不合法')
    const save = screen.getByRole('button', { name: '保存' })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(onChange).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('JSON 编辑错误会写入页脚警告，右上角关闭走 handleOpenChange', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <ExtraParamsDialog
        open
        value={{ a: 1 }}
        onChange={onChange}
        onOpenChange={onOpenChange}
      />
    )

    await user.click(screen.getByRole('tab', { name: 'JSON 编辑' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '{' } })
    expect(screen.getByRole('alert')).toHaveTextContent('JSON 格式错误')
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('FloatingPanel', () => {
  beforeEach(() => {
    setViewport(1200, 800)
  })

  it('open=false 不渲染；打开时展示标题、副标题并响应关闭', () => {
    const onClose = vi.fn()
    const { rerender } = render(
      <FloatingPanel open={false} title="文档" subtitle="README" onClose={onClose}>
        面板内容
      </FloatingPanel>
    )
    expect(screen.queryByRole('dialog', { name: '文档' })).not.toBeInTheDocument()

    rerender(
      <FloatingPanel
        open
        title="文档"
        subtitle="README"
        onClose={onClose}
        closeLabel="关掉面板"
        actions={<button type="button">刷新</button>}
      >
        面板内容
      </FloatingPanel>
    )

    const panel = screen.getByRole('dialog', { name: '文档' })
    expect(panel).toHaveAttribute('data-dashboard-floating-content', 'true')
    expect(screen.getByText('README')).toBeInTheDocument()
    expect(screen.getByText('面板内容')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关掉面板' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('主键拖拽更新位置，松手后恢复 grab 光标并释放指针捕获', () => {
    render(
      <FloatingPanel title="可拖拽" onClose={() => {}}>
        内容
      </FloatingPanel>
    )
    const panel = screen.getByRole('dialog', { name: '可拖拽' })
    mockRect(panel, { left: 100, top: 80, width: 240, height: 200 })
    const header = panel.firstElementChild as HTMLElement
    expect(header).toHaveClass('cursor-grab')

    const setCapture = vi.spyOn(header, 'setPointerCapture')
    vi.spyOn(header, 'hasPointerCapture').mockReturnValue(true)
    const releaseCapture = vi.spyOn(header, 'releasePointerCapture')

    fireEvent.pointerDown(header, { button: 0, pointerId: 1, clientX: 120, clientY: 90 })
    expect(setCapture).toHaveBeenCalledWith(1)
    expect(header).toHaveClass('cursor-grabbing')

    fireEvent.pointerMove(header, { pointerId: 1, clientX: 220, clientY: 160 })
    expect(panel).toHaveStyle({ left: '200px', top: '150px' })

    fireEvent.pointerUp(header, { pointerId: 1 })
    expect(releaseCapture).toHaveBeenCalledWith(1)
    expect(header).toHaveClass('cursor-grab')
  })

  it('忽略非主键、已有拖拽、指针 id 不匹配，以及缺失矩形的按下', () => {
    render(
      <FloatingPanel title="边界拖拽" onClose={() => {}}>
        内容
      </FloatingPanel>
    )
    const panel = screen.getByRole('dialog', { name: '边界拖拽' })
    const header = panel.firstElementChild as HTMLElement
    const initialLeft = panel.style.left

    fireEvent.pointerDown(header, { button: 2, pointerId: 9, clientX: 10, clientY: 10 })
    expect(header).toHaveClass('cursor-grab')

    mockRect(panel, { left: 40, top: 40, width: 200, height: 180 })
    fireEvent.pointerDown(header, { button: 0, pointerId: 1, clientX: 50, clientY: 50 })
    expect(header).toHaveClass('cursor-grabbing')

    // 已有 dragRef 时第二次按下直接返回
    fireEvent.pointerDown(header, { button: 0, pointerId: 2, clientX: 80, clientY: 80 })
    fireEvent.pointerMove(header, { pointerId: 2, clientX: 300, clientY: 300 })
    fireEvent.pointerUp(header, { pointerId: 2 })
    expect(header).toHaveClass('cursor-grabbing')
    expect(panel.style.left).not.toBe('300px')

    fireEvent.pointerCancel(header, { pointerId: 1 })
    expect(header).toHaveClass('cursor-grab')

    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue(undefined as unknown as DOMRect)
    fireEvent.pointerDown(header, { button: 0, pointerId: 3, clientX: 10, clientY: 10 })
    expect(header).toHaveClass('cursor-grab')
    expect(panel.style.left).toBe(initialLeft)
  })

  it('操作区 pointerdown 不启动拖拽；resize 在缺少 panelRect 时回退初始尺寸并夹紧', () => {
    render(
      <FloatingPanel
        title="夹紧"
        onClose={() => {}}
        initialWidth={400}
        initialHeight={300}
        actions={<button type="button">更多</button>}
      >
        内容
      </FloatingPanel>
    )
    const panel = screen.getByRole('dialog', { name: '夹紧' })
    const header = panel.firstElementChild as HTMLElement

    fireEvent.pointerDown(screen.getByRole('button', { name: '更多' }), {
      button: 0,
      pointerId: 4,
      clientX: 10,
      clientY: 10,
    })
    expect(header).toHaveClass('cursor-grab')

    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue(undefined as unknown as DOMRect)
    setViewport(80, 80)
    fireEvent(window, new Event('resize'))
    expect(panel).toHaveStyle({ left: '16px', top: '16px' })
  })
})

describe('MultiSelect', () => {
  it('空选展示占位；未知值回退显示 value 本身', () => {
    const { rerender } = render(
      <MultiSelect options={MULTI_OPTIONS} selected={[]} onChange={vi.fn()} placeholder="请选择水果" />
    )
    expect(screen.getByText('请选择水果')).toBeInTheDocument()

    rerender(
      <MultiSelect options={MULTI_OPTIONS} selected={['ghost']} onChange={vi.fn()} />
    )
    expect(screen.getByText('ghost')).toBeInTheDocument()
  })

  it('删除按钮点击、Enter、Space 会从 selected 中移除，disabled 时直接返回', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <MultiSelect options={MULTI_OPTIONS} selected={['apple', 'banana']} onChange={onChange} />
    )

    const removeApple = getBadgeRemoveControl('苹果')
    fireEvent.pointerDown(removeApple)
    fireEvent.mouseDown(removeApple)
    fireEvent.click(removeApple)
    expect(onChange).toHaveBeenCalledWith(['banana'])

    onChange.mockClear()
    fireEvent.keyDown(getBadgeRemoveControl('香蕉'), { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(['apple'])

    onChange.mockClear()
    fireEvent.keyDown(getBadgeRemoveControl('香蕉'), { key: ' ' })
    expect(onChange).toHaveBeenCalledWith(['apple'])

    onChange.mockClear()
    rerender(
      <MultiSelect
        options={MULTI_OPTIONS}
        selected={['apple']}
        onChange={onChange}
        disabled
        compact
      />
    )
    const disabledRemove = getBadgeRemoveControl('苹果')
    expect(disabledRemove).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(disabledRemove)
    fireEvent.keyDown(disabledRemove, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('打开列表后可添加、取消选择', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<MultiSelect options={MULTI_OPTIONS} selected={['apple']} onChange={onChange} />)

    await user.click(getMultiSelectTrigger())
    await user.click(await screen.findByRole('option', { name: '香蕉' }))
    expect(onChange).toHaveBeenCalledWith(['apple', 'banana'])

    onChange.mockClear()
    await user.click(await screen.findByRole('option', { name: '苹果' }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('单选模式会替换已选值并关闭浮层', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <MultiSelect
        options={MULTI_OPTIONS}
        selected={['apple']}
        onChange={onChange}
        singleSelect
      />
    )
    await user.click(getMultiSelectTrigger())
    await user.click(await screen.findByRole('option', { name: '樱桃' }))
    expect(onChange).toHaveBeenCalledWith(['cherry'])
    expect(screen.queryByPlaceholderText('搜索...')).not.toBeInTheDocument()
  })

  it('禁用时不能打开；从打开态切到禁用会强制关闭', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <MultiSelect options={MULTI_OPTIONS} selected={['apple']} onChange={vi.fn()} />
    )
    await user.click(screen.getByRole('combobox'))
    expect(await screen.findByPlaceholderText('搜索...')).toBeInTheDocument()

    rerender(
      <MultiSelect options={MULTI_OPTIONS} selected={['apple']} onChange={vi.fn()} disabled />
    )
    expect(screen.queryByPlaceholderText('搜索...')).not.toBeInTheDocument()
    expect(screen.getByRole('combobox')).toBeDisabled()
  })

  it('搜索无结果时展示 emptyText；拖拽结束会 arrayMove', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <MultiSelect
        options={MULTI_OPTIONS}
        selected={['apple', 'banana']}
        onChange={onChange}
        emptyText="没有水果"
      />
    )

    await user.click(screen.getByRole('combobox'))
    await user.type(screen.getByPlaceholderText('搜索...'), 'zzzz')
    expect(await screen.findByText('没有水果')).toBeInTheDocument()

    expect(dndState.onDragEnd).toBeDefined()
    act(() => {
      dndState.onDragEnd?.({ active: { id: 'apple' }, over: { id: 'banana' } })
    })
    expect(onChange).toHaveBeenCalledWith(['banana', 'apple'])

    onChange.mockClear()
    act(() => {
      dndState.onDragEnd?.({ active: { id: 'apple' }, over: null })
      dndState.onDragEnd?.({ active: { id: 'apple' }, over: { id: 'apple' } })
    })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('disabled 时 handleDragEnd 直接返回；拖拽中标签带半透明阴影', () => {
    const onChange = vi.fn()
    dndState.isDragging = true
    const { rerender } = render(
      <MultiSelect options={MULTI_OPTIONS} selected={['apple']} onChange={onChange} disabled />
    )
    const draggingBadge = screen.getByText('苹果').parentElement?.parentElement
    expect(draggingBadge).toHaveStyle({ opacity: '0.5' })
    expect(draggingBadge).toHaveClass('shadow-lg')

    act(() => {
      dndState.onDragEnd?.({ active: { id: 'apple' }, over: { id: 'banana' } })
    })
    expect(onChange).not.toHaveBeenCalled()

    dndState.isDragging = false
    rerender(<MultiSelect options={MULTI_OPTIONS} selected={['apple']} onChange={onChange} />)
    expect(screen.getByText('苹果').parentElement?.parentElement).not.toHaveClass('shadow-lg')
  })
})

describe('KeyValueEditor', () => {
  it('列表切到 JSON 时，空对象写入空文本，非空对象 stringify', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<KeyValueEditor value={{}} onChange={vi.fn()} />)
    await user.click(screen.getByRole('tab', { name: 'JSON 编辑' }))
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(screen.getByText('暂无参数')).toBeInTheDocument()

    rerender(<KeyValueEditor value={{ alpha: 1 }} onChange={vi.fn()} />)
    await user.click(screen.getByRole('tab', { name: '可视化编辑' }))
    await user.click(screen.getByRole('tab', { name: 'JSON 编辑' }))
    expect(screen.getByRole('textbox')).toHaveValue(JSON.stringify({ alpha: 1 }, null, 2))
    expect(screen.getByText('有效')).toBeInTheDocument()
  })

  it('合法 JSON 触发 onChange；数组/null/非法文本分别给出校验错误', async () => {
    const onChange = vi.fn()
    const onValidationChange = vi.fn()
    render(
      <KeyValueEditor
        value={{}}
        onChange={onChange}
        onValidationChange={onValidationChange}
      />
    )
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'JSON 编辑' }))
    const textarea = screen.getByRole('textbox')

    fireEvent.change(textarea, { target: { value: '{"top_p":0.9}' } })
    expect(onChange).toHaveBeenCalledWith({ top_p: 0.9 })
    expect(screen.getByText('有效')).toBeInTheDocument()
    expect(screen.getByText(/"top_p": 0.9/)).toBeInTheDocument()

    fireEvent.change(textarea, { target: { value: '[1,2]' } })
    expect(screen.getAllByText('必须是一个 JSON 对象 {}').length).toBeGreaterThan(0)
    expect(screen.getByText('JSON 格式错误')).toBeInTheDocument()

    fireEvent.change(textarea, { target: { value: 'null' } })
    expect(screen.getAllByText('必须是一个 JSON 对象 {}').length).toBeGreaterThan(0)

    fireEvent.change(textarea, { target: { value: '{' } })
    expect(screen.getAllByText('JSON 格式错误').length).toBeGreaterThan(0)

    fireEvent.change(textarea, { target: { value: '   ' } })
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(screen.getByText('暂无参数')).toBeInTheDocument()
  })

  it('省略 onValidationChange 且 value 为假值时仍能渲染列表占位', () => {
    render(
      <KeyValueEditor
        value={undefined as unknown as Record<string, unknown>}
        onChange={vi.fn()}
        placeholder="自定义占位"
      />
    )
    expect(screen.getByText('自定义占位')).toBeInTheDocument()
    expect(screen.getByText('0 个参数')).toBeInTheDocument()
  })
})

describe('DraftNumberInput', () => {
  it('输入有限数字会提交；空串与非数字草稿不会 onValueChange', () => {
    const onValueChange = vi.fn()
    render(<DraftNumberInput value={1} onValueChange={onValueChange} />)
    const input = screen.getByRole('spinbutton')
    expect(input).toHaveValue(1)

    fireEvent.change(input, { target: { value: '4.5' } })
    expect(onValueChange).toHaveBeenCalledWith(4.5)

    onValueChange.mockClear()
    fireEvent.change(input, { target: { value: '' } })
    expect(onValueChange).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'abc' } })
    expect(onValueChange).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'Infinity' } })
    expect(onValueChange).not.toHaveBeenCalled()
  })

  it('失焦时空草稿回退到当前数值；不同值会先提交再规范化', () => {
    const onValueChange = vi.fn()
    const onBlur = vi.fn()
    const onFocus = vi.fn()
    render(
      <DraftNumberInput
        value={3}
        onValueChange={onValueChange}
        onBlur={onBlur}
        onFocus={onFocus}
      />
    )
    const input = screen.getByRole('spinbutton')

    fireEvent.focus(input)
    expect(onFocus).toHaveBeenCalledTimes(1)
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    expect(input).toHaveValue(3)
    expect(onValueChange).not.toHaveBeenCalled()
    expect(onBlur).toHaveBeenCalledTimes(1)

    fireEvent.change(input, { target: { value: '9' } })
    expect(onValueChange).toHaveBeenCalledWith(9)
    onValueChange.mockClear()
    fireEvent.blur(input)
    // 受控 value 仍是 3，失焦会再次提交 9
    expect(onValueChange).toHaveBeenCalledWith(9)
    expect(onBlur).toHaveBeenCalledTimes(2)
  })

  it('integer 截断小数；非法 value 回退 defaultValue，再不行落到 0', () => {
    const { rerender } = render(
      <DraftNumberInput value="3.8" integer onValueChange={vi.fn()} />
    )
    expect(screen.getByRole('spinbutton')).toHaveValue(3)

    rerender(<DraftNumberInput value="nope" defaultValue={8} integer onValueChange={vi.fn()} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(8)

    rerender(
      <DraftNumberInput value="nope" defaultValue="still-nope" onValueChange={vi.fn()} />
    )
    expect(screen.getByRole('spinbutton')).toHaveValue(0)

    rerender(<DraftNumberInput value={Number.NaN} onValueChange={vi.fn()} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(0)

    rerender(<DraftNumberInput value={Number.POSITIVE_INFINITY} onValueChange={vi.fn()} />)
    expect(screen.getByRole('spinbutton')).toHaveValue(0)
  })

  it('聚焦时忽略外部 value 同步；未聚焦时外部 value 会覆盖草稿', () => {
    const { rerender } = render(<DraftNumberInput value={1} onValueChange={vi.fn()} />)
    const input = screen.getByRole('spinbutton')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '6' } })

    rerender(<DraftNumberInput value={9} onValueChange={vi.fn()} />)
    expect(input).toHaveValue(6)

    fireEvent.blur(input)
    // 失焦后仍保留刚提交的草稿；外部 value 从 9 改到 4 才会同步
    rerender(<DraftNumberInput value={4} onValueChange={vi.fn()} />)
    expect(input).toHaveValue(4)
  })
})
