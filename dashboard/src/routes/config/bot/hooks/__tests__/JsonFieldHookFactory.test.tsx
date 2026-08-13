import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { createJsonFieldHook } from '../JsonFieldHookFactory'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

const fieldSchema: FieldSchema = {
  name: 'roots',
  type: 'array',
  label: 'Roots',
  description: 'JSON 字段说明',
  required: false,
}

describe('createJsonFieldHook', () => {
  it('value 为 undefined 时使用 emptyValue 初始化编辑器', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: { enabled: true },
      helperText: '编辑 JSON。',
      placeholder: '{}',
    })

    render(<JsonHook fieldPath="mcp.roots" onChange={vi.fn()} schema={fieldSchema} />)

    expect(screen.getByDisplayValue(/"enabled": true/)).toBeInTheDocument()
    expect(screen.getByText('JSON 有效，修改会立即写回配置草稿。')).toBeInTheDocument()
  })

  it('解析成功时清空错误并写回 onChange', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '编辑 JSON。',
      placeholder: '[]',
    })
    const onChange = vi.fn()

    render(<JsonHook fieldPath="mcp.roots" onChange={onChange} schema={fieldSchema} value={[]} />)

    fireEvent.change(screen.getByPlaceholderText('[]'), {
      target: { value: '{"ok":1}' },
    })

    expect(onChange).toHaveBeenCalledWith({ ok: 1 })
    expect(screen.getByText('JSON 有效，修改会立即写回配置草稿。')).toBeInTheDocument()
  })

  it('JSON.parse 抛出 Error 时展示错误横幅', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '编辑 JSON。',
      placeholder: '[]',
    })

    render(<JsonHook fieldPath="mcp.roots" onChange={vi.fn()} schema={fieldSchema} value={[]} />)

    fireEvent.change(screen.getByPlaceholderText('[]'), {
      target: { value: '{bad' },
    })

    expect(screen.getByText(/JSON 解析失败：/)).toBeInTheDocument()
  })

  it('非 Error 抛出时回退到默认错误文案', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '编辑 JSON。',
      placeholder: '[]',
    })
    const parseSpy = vi.spyOn(JSON, 'parse').mockImplementation(() => {
      throw 'broken'
    })

    render(<JsonHook fieldPath="mcp.roots" onChange={vi.fn()} schema={fieldSchema} value={[]} />)
    fireEvent.change(screen.getByPlaceholderText('[]'), {
      target: { value: '{"x":1}' },
    })

    expect(screen.getByText('JSON 解析失败：JSON 格式错误')).toBeInTheDocument()
    parseSpy.mockRestore()
  })

  it('resolveLabel / resolveDescription 按 schema 回退', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '帮助文本',
      placeholder: '[]',
    })

    const { rerender } = render(<JsonHook fieldPath="section.json_field" onChange={vi.fn()} value={[]} />)
    expect(screen.getByText('json_field')).toBeInTheDocument()
    expect(screen.queryByText('JSON 字段说明')).not.toBeInTheDocument()

    rerender(
      <JsonHook
        fieldPath="section.json_field"
        onChange={vi.fn()}
        schema={{ className: 'JsonClass', classDoc: '', fields: [] } satisfies ConfigSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('JsonClass')).toBeInTheDocument()

    rerender(
      <JsonHook
        fieldPath="section.json_field"
        onChange={vi.fn()}
        schema={{ className: 'JsonClass', classDoc: '类文档标题', fields: [] }}
        value={[]}
      />,
    )
    expect(screen.getAllByText('类文档标题').length).toBeGreaterThan(1)

    rerender(
      <JsonHook
        fieldPath="section.json_field"
        onChange={vi.fn()}
        schema={{ className: 'JsonClass', classDoc: '', uiLabel: 'UI 标题', fields: [] }}
        value={[]}
      />,
    )
    expect(screen.getByText('UI 标题')).toBeInTheDocument()

    rerender(
      <JsonHook
        fieldPath="section.json_field"
        onChange={vi.fn()}
        schema={{ ...fieldSchema, description: '' }}
        value={[]}
      />,
    )
    expect(screen.getByText('Roots')).toBeInTheDocument()
  })

  it('没有 fieldPath 且没有 schema 时使用默认标题', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '帮助文本',
      placeholder: '[]',
    })

    render(<JsonHook fieldPath="" onChange={vi.fn()} value={[]} />)
    expect(screen.getByText('JSON 配置')).toBeInTheDocument()
  })

  it('空 label 且无 classDoc/className 时回退到字段名或默认标题', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: [],
      helperText: '帮助文本',
      placeholder: '[]',
    })

    const { rerender } = render(
      <JsonHook
        fieldPath="section.custom_json"
        onChange={vi.fn()}
        schema={{ name: 'custom_json', type: 'object', label: '', required: false } as FieldSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('custom_json')).toBeInTheDocument()
    expect(screen.queryByText('JSON 字段说明')).not.toBeInTheDocument()

    rerender(
      <JsonHook
        fieldPath=""
        onChange={vi.fn()}
        schema={{ className: '', classDoc: '', fields: [] }}
        value={[]}
      />,
    )
    expect(screen.getByText('JSON 配置')).toBeInTheDocument()
  })

  it('外部 value 变化时同步编辑器并清空解析错误', () => {
    const JsonHook = createJsonFieldHook({
      emptyValue: {},
      helperText: '帮助文本',
      placeholder: '{}',
    })
    const { rerender } = render(
      <JsonHook fieldPath="mcp.roots" onChange={vi.fn()} schema={fieldSchema} value={{ a: 1 }} />,
    )

    fireEvent.change(screen.getByPlaceholderText('{}'), {
      target: { value: '{bad' },
    })
    expect(screen.getByText(/JSON 解析失败：/)).toBeInTheDocument()

    rerender(
      <JsonHook fieldPath="mcp.roots" onChange={vi.fn()} schema={fieldSchema} value={{ a: 2 }} />,
    )
    expect(screen.getByDisplayValue(/"a": 2/)).toBeInTheDocument()
    expect(screen.getByText('JSON 有效，修改会立即写回配置草稿。')).toBeInTheDocument()
  })
})
