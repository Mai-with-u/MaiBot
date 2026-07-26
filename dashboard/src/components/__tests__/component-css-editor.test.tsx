import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ComponentCSSEditor } from '../component-css-editor'

/** 捕获透传给 CodeEditor 的 props（工厂会被提升，须用 vi.hoisted） */
const editorCapture = vi.hoisted(() => ({
  props: null as {
    value: string
    onChange?: (value: string) => void
    language?: string
    readOnly?: boolean
    height?: string
    placeholder?: string
  } | null,
}))

// CodeEditor 本体懒加载 CodeMirror，这里替换为可交互的轻量桩
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: (props: {
    value: string
    onChange?: (value: string) => void
    language?: string
    readOnly?: boolean
    height?: string
    placeholder?: string
  }) => {
    editorCapture.props = props
    return (
      <textarea
        data-testid="code-editor-mock"
        value={props.value}
        onChange={(event) => props.onChange?.(event.target.value)}
      />
    )
  },
}))

describe('ComponentCSSEditor', () => {
  beforeEach(() => {
    editorCapture.props = null
  })

  it('默认标签为「自定义 CSS」，placeholder 内嵌组件 ID', () => {
    render(<ComponentCSSEditor componentId="chat-panel" value="" onChange={() => {}} />)

    expect(screen.getByText('自定义 CSS')).toBeInTheDocument()
    expect(editorCapture.props?.language).toBe('css')
    expect(editorCapture.props?.height).toBe('200px')
    expect(editorCapture.props?.placeholder).toContain('为 chat-panel 组件编写自定义 CSS')
  })

  it('自定义 label 与 height 透传', () => {
    render(
      <ComponentCSSEditor
        componentId="chat-panel"
        value=""
        onChange={() => {}}
        label="主题样式"
        height="320px"
      />
    )

    expect(screen.getByText('主题样式')).toBeInTheDocument()
    expect(editorCapture.props?.height).toBe('320px')
  })

  it('编辑器输入桥接到 onChange 回调', () => {
    const onChange = vi.fn()
    render(<ComponentCSSEditor componentId="chat-panel" value="" onChange={onChange} />)

    fireEvent.change(screen.getByTestId('code-editor-mock'), {
      target: { value: 'body { color: red; }' },
    })
    expect(onChange).toHaveBeenCalledWith('body { color: red; }')
  })

  it('清除按钮把内容重置为空字符串', () => {
    const onChange = vi.fn()
    render(
      <ComponentCSSEditor componentId="chat-panel" value="a { color: red; }" onChange={onChange} />
    )

    fireEvent.click(screen.getByRole('button', { name: '清除' }))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('内容为空时清除按钮禁用', () => {
    render(<ComponentCSSEditor componentId="chat-panel" value="" onChange={() => {}} />)

    expect(screen.getByRole('button', { name: '清除' })).toBeDisabled()
  })

  it('disabled 时降低透明度、禁用清除并把编辑器设为只读', () => {
    const onChange = vi.fn()
    const { container } = render(
      <ComponentCSSEditor componentId="chat-panel" value="a {}" onChange={onChange} disabled />
    )

    expect(container.firstElementChild).toHaveClass('opacity-50')
    expect(screen.getByRole('button', { name: '清除' })).toBeDisabled()
    expect(editorCapture.props?.readOnly).toBe(true)
    // 禁用状态下不再透传 onChange
    expect(editorCapture.props?.onChange).toBeUndefined()
  })

  it('安全 CSS 不显示警告块', () => {
    render(
      <ComponentCSSEditor componentId="chat-panel" value="a { color: red; }" onChange={() => {}} />
    )

    expect(screen.queryByText('检测到不安全的 CSS 规则：')).not.toBeInTheDocument()
  })

  it('危险 CSS 触发安全警告列表', () => {
    render(
      <ComponentCSSEditor
        componentId="chat-panel"
        value={'@import url("https://evil.com/a.css");'}
        onChange={() => {}}
      />
    )

    expect(screen.getByText('检测到不安全的 CSS 规则：')).toBeInTheDocument()
    expect(
      screen.getByText('Line 1: 移除 @import 语句（禁止加载外部资源）')
    ).toBeInTheDocument()
  })
})
