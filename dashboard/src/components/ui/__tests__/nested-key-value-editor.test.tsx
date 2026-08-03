import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { NestedKeyValueEditor } from '../nested-key-value-editor'

// Radix Select 在 jsdom 下无法可靠打开浮层，这里替换为可直接点击的类型按钮组
vi.mock('@/components/ui/select', () => {
  const TYPE_OPTIONS = ['string', 'number', 'boolean', 'null', 'object', 'array']
  return {
    Select: ({ value, onValueChange }: { value: string; onValueChange: (v: string) => void }) => (
      <div data-testid="type-select" data-current-type={value}>
        {TYPE_OPTIONS.map((option) => (
          <button type="button" key={option} onClick={() => onValueChange(option)}>
            {`类型:${option}`}
          </button>
        ))}
      </div>
    ),
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: () => null,
    SelectItem: () => null,
  }
})

// 找到当前唯一一个键名为空的输入框（新添加的行）
function findEmptyKeyInput(): HTMLInputElement {
  const input = screen
    .getAllByPlaceholderText('key')
    .find((el): el is HTMLInputElement => el instanceof HTMLInputElement && el.value === '')
  if (!input) {
    throw new Error('未找到空键名输入框')
  }
  return input
}

// 获取某个键名输入框所在的行容器
function getRowOfKey(key: string): HTMLElement {
  const input = screen.getByDisplayValue(key)
  const row = input.parentElement
  if (!(row instanceof HTMLElement)) {
    throw new Error(`未找到键 ${key} 所在的行`)
  }
  return row
}

afterEach(() => {
  cleanup()
})

describe('NestedKeyValueEditor 基础渲染', () => {
  it('空对象时显示占位提示与 0 个参数', () => {
    render(<NestedKeyValueEditor value={{}} onChange={vi.fn()} />)
    expect(screen.getByText('添加参数...')).toBeInTheDocument()
    expect(screen.getByText('0 个参数')).toBeInTheDocument()
    expect(screen.queryByText('键名')).not.toBeInTheDocument()
  })

  it('渲染各类型初始值：字符串/数字输入框、布尔开关、null 底纹、容器行无值输入框', () => {
    render(
      <NestedKeyValueEditor
        value={{ name: 'mai', count: 3, flag: true, empty: null, obj: { a: 1 }, arr: ['x'] }}
        onChange={vi.fn()}
      />
    )
    expect(screen.getByText('6 个参数')).toBeInTheDocument()
    // 表头
    expect(screen.getByText('键名')).toBeInTheDocument()
    expect(screen.getByText('值')).toBeInTheDocument()
    expect(screen.getByText('类型')).toBeInTheDocument()

    // 字符串与数字值
    expect(screen.getByDisplayValue('mai')).toBeInTheDocument()
    expect(screen.getByDisplayValue('3')).toBeInTheDocument()

    // 布尔行渲染开关且显示 true 文本
    expect(screen.getByRole('switch')).toBeChecked()
    expect(screen.getByText('true')).toBeInTheDocument()

    // null 行显示 null 底纹
    expect(screen.getByText('null')).toBeInTheDocument()

    // 对象与数组的子节点默认展开
    expect(screen.getByDisplayValue('a')).toBeInTheDocument()
    expect(screen.getByDisplayValue('x')).toBeInTheDocument()

    // 容器行本身没有值输入框：值输入框 = name/count 两个根节点 + 子节点 a/0 两个
    expect(screen.getAllByPlaceholderText('value')).toHaveLength(4)
  })
})

describe('NestedKeyValueEditor 增删改', () => {
  it('添加参数后行数增加，且空键名的行不会进入 onChange 载荷（特征化现状）', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{}} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /添加参数/ }))

    expect(screen.getByText('1 个参数')).toBeInTheDocument()
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith({})
  })

  it('给新行输入键名后 onChange 携带该键与空字符串值', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{}} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /添加参数/ }))
    fireEvent.change(findEmptyKeyInput(), { target: { value: 'foo' } })
    expect(onChange).toHaveBeenLastCalledWith({ foo: '' })
  })

  it('修改字符串值触发 onChange 更新载荷', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ name: 'mai' }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('mai'), { target: { value: 'bot' } })
    expect(onChange).toHaveBeenLastCalledWith({ name: 'bot' })
  })

  it('重命名键名后 onChange 使用新键', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ name: 'mai' }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('name'), { target: { value: 'nick' } })
    expect(onChange).toHaveBeenLastCalledWith({ nick: 'mai' })
  })

  it('同层键名重复时显示错误且不覆盖父级值', () => {
    const onChange = vi.fn()
    const onValidationChange = vi.fn()
    render(
      <NestedKeyValueEditor
        value={{ first: 'a', second: 'b' }}
        onChange={onChange}
        onValidationChange={onValidationChange}
      />
    )

    fireEvent.change(screen.getByDisplayValue('second'), { target: { value: 'first' } })

    expect(screen.getByRole('alert')).toHaveTextContent('检测到重复键：first')
    expect(onChange).not.toHaveBeenCalled()
    expect(onValidationChange).toHaveBeenLastCalledWith('检测到重复键：first')
  })

  it('嵌套对象中的同层重复键会标出完整路径', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ config: { first: 'a', second: 'b' } }} onChange={onChange} />)

    fireEvent.change(screen.getByDisplayValue('second'), { target: { value: 'first' } })

    expect(screen.getByRole('alert')).toHaveTextContent('config.first')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('数字值转换为 number，清空时落到 0（特征化现状）', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ n: 3 }} onChange={onChange} />)
    const input = screen.getByDisplayValue('3')
    fireEvent.change(input, { target: { value: '42' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 42 })
    fireEvent.change(input, { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 0 })
  })

  it('布尔开关切换后 onChange 载荷与展示文本同步更新', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ flag: true }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('switch'))
    expect(onChange).toHaveBeenLastCalledWith({ flag: false })
    expect(screen.getByText('false')).toBeInTheDocument()
  })

  it('删除按钮移除对应键并更新计数', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x', b: 'y' }} onChange={onChange} />)
    fireEvent.click(screen.getAllByTitle('删除')[0])
    expect(onChange).toHaveBeenLastCalledWith({ b: 'y' })
    expect(screen.getByText('1 个参数')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument()
  })

  it('对象节点添加子项并命名后生成嵌套载荷', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ obj: { a: 1 } }} onChange={onChange} />)
    fireEvent.click(screen.getByTitle('添加子项'))
    // 特征化：新子项键名为空时不进入载荷，本次 onChange 载荷不变
    expect(onChange).toHaveBeenLastCalledWith({ obj: { a: 1 } })

    fireEvent.change(findEmptyKeyInput(), { target: { value: 'b' } })
    expect(onChange).toHaveBeenLastCalledWith({ obj: { a: 1, b: '' } })
  })

  it('数组节点添加子项时自动使用索引作为键名', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ arr: ['x'] }} onChange={onChange} />)
    fireEvent.click(screen.getByTitle('添加子项'))
    expect(onChange).toHaveBeenLastCalledWith({ arr: ['x', ''] })
    // 新行键名自动为下一个索引 1
    expect(screen.getByDisplayValue('1')).toBeInTheDocument()
  })

  it('折叠/展开容器行只影响子行显示，不触发 onChange', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ obj: { a: 1 } }} onChange={onChange} />)
    expect(screen.getByDisplayValue('a')).toBeInTheDocument()

    const objRow = getRowOfKey('obj')
    const expandButton = within(objRow).getAllByRole('button')[0]
    fireEvent.click(expandButton)
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument()

    fireEvent.click(expandButton)
    expect(screen.getByDisplayValue('a')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('NestedKeyValueEditor 类型切换', () => {
  it('切换为数字时无法解析的字符串落到 0（特征化现状）', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:number' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: 0 })
  })

  it('切换为布尔时仅字符串 "true" 视为真', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:boolean' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: false })
  })

  it('切换为 null 后载荷为 null 且值输入框消失', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:null' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: null })
    expect(screen.queryByPlaceholderText('value')).not.toBeInTheDocument()
    expect(screen.getByText('null')).toBeInTheDocument()
  })

  it('切换为对象后载荷为空对象并出现添加子项按钮', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:object' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: {} })
    expect(screen.getByTitle('添加子项')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('value')).not.toBeInTheDocument()
  })

  it('切换为数组后载荷为空数组', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:array' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: [] })
  })
})

describe('NestedKeyValueEditor 外部 value 同步', () => {
  it('回传刚 emit 过的等值对象时保留内部编辑状态', () => {
    const onChange = vi.fn()
    const { rerender } = render(<NestedKeyValueEditor value={{}} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /添加参数/ }))
    expect(screen.getByText('1 个参数')).toBeInTheDocument()

    // 父组件把刚 emit 的 {} 以新对象引用回传，JSON 相同则不重建树，空键行保留
    rerender(<NestedKeyValueEditor value={{}} onChange={onChange} />)
    expect(screen.getByText('1 个参数')).toBeInTheDocument()
    expect(findEmptyKeyInput()).toBeInTheDocument()
  })

  it('外部传入不同的 value 时重建整棵树', () => {
    const onChange = vi.fn()
    const { rerender } = render(<NestedKeyValueEditor value={{}} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /添加参数/ }))
    expect(screen.getByText('1 个参数')).toBeInTheDocument()

    rerender(<NestedKeyValueEditor value={{ x: 1 }} onChange={onChange} />)
    expect(screen.getByText('1 个参数')).toBeInTheDocument()
    expect(screen.getByDisplayValue('x')).toBeInTheDocument()
    // 之前那行空键名的草稿行已被外部值覆盖
    expect(
      screen.getAllByPlaceholderText('key').filter((el) => el instanceof HTMLInputElement && el.value === '')
    ).toHaveLength(0)
  })
})
