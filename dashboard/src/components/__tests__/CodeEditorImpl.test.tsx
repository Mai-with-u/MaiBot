import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CodeEditorImpl from '../CodeEditorImpl'

/** 带 marker 标识的假扩展对象，便于在 extensions 数组中检索 */
type MarkedExtension = { marker?: string } & Record<string, unknown>

// vi.mock 工厂会被提升，捕获容器必须用 vi.hoisted 定义
const captured = vi.hoisted(() => ({
  /** 最近一次渲染时透传给 CodeMirror 的 props */
  cmProps: null as Record<string, unknown> | null,
  /** EditorView.decorations.compute 注册的回调 */
  computeCallback: null as ((state: unknown) => unknown) | null,
  /** 最近一次 Decoration.set 收到的参数 */
  lastSet: null as { items: unknown[]; sort: boolean } | null,
  /** 主题解析结果，可按用例切换 */
  resolvedTheme: 'light' as 'light' | 'dark',
}))

vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({ resolvedTheme: captured.resolvedTheme }),
}))

vi.mock('@uiw/react-codemirror', () => ({
  default: (
    props: Record<string, unknown> & {
      value?: string
      placeholder?: string
      onChange?: (value: string) => void
    }
  ) => {
    captured.cmProps = props
    return (
      <textarea
        data-testid="codemirror-mock"
        value={typeof props.value === 'string' ? props.value : ''}
        placeholder={props.placeholder}
        onChange={(event) => props.onChange?.(event.target.value)}
      />
    )
  },
}))

vi.mock('@codemirror/view', () => ({
  EditorView: {
    lineWrapping: { marker: 'line-wrapping' },
    theme: (spec: Record<string, unknown>) => ({ marker: 'theme', spec }),
    decorations: {
      compute: (_deps: readonly unknown[], callback: (state: unknown) => unknown) => {
        captured.computeCallback = callback
        return { marker: 'decorations' }
      },
    },
    editable: { of: (value: boolean) => ({ marker: 'editable', value }) },
  },
  Decoration: {
    line: (spec: { class: string }) => ({
      range: (from: number) => ({ kind: 'line', className: spec.class, from }),
    }),
    mark: (spec: { class: string }) => ({
      range: (from: number, to: number) => ({ kind: 'mark', className: spec.class, from, to }),
    }),
    set: (items: unknown[], sort?: boolean) => {
      captured.lastSet = { items, sort: sort === true }
      return { marker: 'decoration-set', items }
    },
  },
  ViewPlugin: {
    fromClass: (pluginClass: new (view: { scrollDOM: HTMLElement }) => unknown) => ({
      marker: 'view-plugin',
      pluginClass,
    }),
  },
}))

vi.mock('@codemirror/lang-css', () => ({ css: () => ({ marker: 'lang-css' }) }))
vi.mock('@codemirror/lang-json', () => ({
  json: () => ({ marker: 'lang-json' }),
  jsonParseLinter: () => ({ marker: 'json-linter-source' }),
}))
vi.mock('@codemirror/lang-python', () => ({ python: () => ({ marker: 'lang-python' }) }))
vi.mock('@codemirror/language', () => ({
  StreamLanguage: { define: (mode: unknown) => ({ marker: 'stream-language', mode }) },
}))
vi.mock('@codemirror/legacy-modes/mode/toml', () => ({ toml: { marker: 'toml-mode' } }))
vi.mock('@codemirror/lint', () => ({
  linter: (source: unknown) => ({ marker: 'linter', source }),
}))
vi.mock('@codemirror/theme-one-dark', () => ({ oneDark: { marker: 'one-dark' } }))

function getExtensions(): MarkedExtension[] {
  return (captured.cmProps?.extensions ?? []) as MarkedExtension[]
}

function findByMarker(marker: string): MarkedExtension | undefined {
  return getExtensions().find((ext) => ext.marker === marker)
}

describe('CodeEditorImpl', () => {
  beforeEach(() => {
    captured.cmProps = null
    captured.computeCallback = null
    captured.lastSet = null
    captured.resolvedTheme = 'light'
  })

  it('容器带编辑器标记并透传核心 props', () => {
    const { container } = render(
      <CodeEditorImpl value="hello" height="120px" placeholder="占位" className="extra" />
    )

    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper).toHaveAttribute('data-dashboard-code-editor', 'true')
    expect(wrapper).toHaveClass('custom-scrollbar', 'extra')

    expect(captured.cmProps).not.toBeNull()
    expect(captured.cmProps?.value).toBe('hello')
    expect(captured.cmProps?.height).toBe('120px')
    expect(captured.cmProps?.placeholder).toBe('占位')
    expect(captured.cmProps?.style).toMatchObject({ height: '120px' })
    // 明亮主题下不传 oneDark
    expect(captured.cmProps?.theme).toBeUndefined()

    // 基础扩展：自动换行、JetBrains Mono 字体主题
    expect(findByMarker('line-wrapping')).toBeDefined()
    const themeExt = findByMarker('theme')
    const themeSpec = themeExt?.spec as Record<string, Record<string, unknown>>
    expect(String(themeSpec['&'].fontFamily)).toContain('JetBrains Mono')
  })

  it('onChange 由 CodeMirror 回调桥接到外部', () => {
    const onChange = vi.fn()
    render(<CodeEditorImpl value="" onChange={onChange} />)

    fireEvent.change(screen.getByTestId('codemirror-mock'), { target: { value: 'next' } })
    expect(onChange).toHaveBeenCalledWith('next')
  })

  it('readOnly 时追加 editable(false) 扩展', () => {
    render(<CodeEditorImpl value="" readOnly />)
    expect(findByMarker('editable')).toMatchObject({ value: false })
  })

  it('非只读时不追加 editable 扩展', () => {
    render(<CodeEditorImpl value="" />)
    expect(findByMarker('editable')).toBeUndefined()
  })

  it('json 语言注入语法与 lint 扩展', () => {
    render(<CodeEditorImpl value="{}" language="json" />)

    expect(findByMarker('lang-json')).toBeDefined()
    expect(findByMarker('linter')).toMatchObject({ source: { marker: 'json-linter-source' } })
  })

  it('toml 语言通过 StreamLanguage 定义', () => {
    render(<CodeEditorImpl value="" language="toml" />)

    expect(findByMarker('stream-language')).toMatchObject({ mode: { marker: 'toml-mode' } })
  })

  it('text 语言不注入任何语法扩展', () => {
    render(<CodeEditorImpl value="" language="text" />)

    for (const marker of ['lang-css', 'lang-json', 'lang-python', 'stream-language']) {
      expect(findByMarker(marker)).toBeUndefined()
    }
  })

  it('未传 theme 时跟随上下文解析出的暗色主题', () => {
    captured.resolvedTheme = 'dark'
    render(<CodeEditorImpl value="" />)

    expect(captured.cmProps?.theme).toMatchObject({ marker: 'one-dark' })
  })

  it('显式 theme 优先于上下文主题', () => {
    captured.resolvedTheme = 'light'
    render(<CodeEditorImpl value="" theme="dark" />)

    expect(captured.cmProps?.theme).toMatchObject({ marker: 'one-dark' })
  })

  it('滚动容器标记插件为 scrollDOM 写入 data 属性', () => {
    render(<CodeEditorImpl value="" />)

    const viewPlugin = findByMarker('view-plugin')
    const PluginClass = viewPlugin?.pluginClass as new (view: {
      scrollDOM: HTMLElement
    }) => unknown
    const scroller = document.createElement('div')
    new PluginClass({ scrollDOM: scroller })

    expect(scroller.dataset.dashboardCodeScroller).toBe('true')
  })

  it('装饰计算过滤越界行并按行长收窄区间', () => {
    render(
      <CodeEditorImpl
        value=""
        lineClassNames={{ 0: 'line-invalid', 1: 'line-a', 5: 'line-out' }}
        rangeClassNames={[
          { fromLine: 1, fromCh: 1, toLine: 2, toCh: 2, className: 'range-ok' },
          { fromLine: 2, fromCh: 0, toLine: 2, toCh: 99, className: 'range-clamp' },
          { fromLine: 3, fromCh: 0, toLine: 1, toCh: 3, className: 'range-inverted' },
          { fromLine: 4, fromCh: 0, toLine: 4, toCh: 2, className: 'range-out' },
          { fromLine: 1, fromCh: 2, toLine: 1, toCh: 2, className: 'range-empty' },
        ]}
      />
    )

    expect(captured.computeCallback).not.toBeNull()

    // 假文档：3 行，每行长度 4，第 n 行起点为 (n-1)*10
    const fakeState = {
      doc: {
        lines: 3,
        line: (lineNumber: number) => ({ from: (lineNumber - 1) * 10, length: 4 }),
      },
    }
    captured.computeCallback?.(fakeState)

    expect(captured.lastSet).not.toBeNull()
    expect(captured.lastSet?.sort).toBe(true)
    expect(captured.lastSet?.items).toEqual([
      // 只有第 1 行合法；0 行与超出行数的 5 行被过滤
      { kind: 'line', className: 'line-a', from: 0 },
      // 正常区间：第 1 行第 1 列到第 2 行第 2 列
      { kind: 'mark', className: 'range-ok', from: 1, to: 12 },
      // 列号超长时收窄到行末
      { kind: 'mark', className: 'range-clamp', from: 10, to: 14 },
      // 反向、越界、零宽区间全部被过滤
    ])
  })
})
