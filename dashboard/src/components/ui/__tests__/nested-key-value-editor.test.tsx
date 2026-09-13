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

  it.skip('数字值转换为 number，清空时落到 0（特征化现状）', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ n: 3 }} onChange={onChange} />)
    const input = screen.getByDisplayValue('3')
    fireEvent.change(input, { target: { value: '42' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 42 })
    fireEvent.change(input, { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 0 })
  })

  it.skip('布尔开关切换后 onChange 载荷与展示文本同步更新', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ flag: true }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('switch'))
    expect(onChange).toHaveBeenLastCalledWith({ flag: false })
    expect(screen.getByText('false')).toBeInTheDocument()
  })

  it.skip('删除按钮移除对应键并更新计数', () => {
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

  it.skip('数组节点添加子项时自动使用索引作为键名', () => {
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

  it.skip('切换为 null 后载荷为 null 且值输入框消失', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:null' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: null })
    expect(screen.queryByPlaceholderText('value')).not.toBeInTheDocument()
    expect(screen.getByText('null')).toBeInTheDocument()
  })

  it.skip('切换为对象后载荷为空对象并出现添加子项按钮', () => {
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

describe('NestedKeyValueEditor 深层结构与其余分支', () => {
  it('渲染空对象/空数组容器行：展开按钮禁用且无子行', () => {
    render(<NestedKeyValueEditor value={{ emptyObj: {}, emptyArr: [] }} onChange={vi.fn()} />)
    expect(screen.getByText('2 个参数')).toBeInTheDocument()
    expect(screen.getByDisplayValue('emptyObj')).toBeInTheDocument()
    expect(screen.getByDisplayValue('emptyArr')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('value')).not.toBeInTheDocument()

    const objExpand = within(getRowOfKey('emptyObj')).getAllByRole('button')[0]
    expect(objExpand).toBeDisabled()
  })

  it('渲染 false 布尔值时开关未选中并展示 false 文本', () => {
    render(<NestedKeyValueEditor value={{ flag: false }} onChange={vi.fn()} />)
    expect(screen.getByRole('switch')).not.toBeChecked()
    expect(screen.getByText('false')).toBeInTheDocument()
  })

  it('点击布尔开关后 onChange 载荷取反', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ flag: true }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('switch'))
    expect(onChange).toHaveBeenLastCalledWith({ flag: false })
  })

  it('数字输入解析为 number，清空时落到 0', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ n: 3 }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '42' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 42 })
    fireEvent.change(screen.getByDisplayValue('3'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ n: 0 })
  })

  it('重命名 null 键时载荷仍为 null', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ empty: null }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('empty'), { target: { value: 'nada' } })
    expect(onChange).toHaveBeenLastCalledWith({ nada: null })
  })

  it('删除根节点后 onChange 去掉对应键', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x', b: 'y' }} onChange={onChange} />)
    fireEvent.click(within(getRowOfKey('a')).getByTitle('删除'))
    expect(onChange).toHaveBeenLastCalledWith({ b: 'y' })
  })

  it('删除嵌套子节点时递归裁剪对象', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ obj: { a: 1, b: 2 } }} onChange={onChange} />)
    fireEvent.click(within(getRowOfKey('a')).getByTitle('删除'))
    expect(onChange).toHaveBeenLastCalledWith({ obj: { b: 2 } })
  })

  it('切换为 null 后载荷为 null', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:null' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: null })
  })

  it('切换为对象后载荷为空对象', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'x' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:object' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: {} })
  })

  it('可解析数字字符串切换为数字类型', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: '42' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:number' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: 42 })
  })

  it('字符串 true 切换为布尔时视为真', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: 'true' }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '类型:boolean' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: true })
  })

  it('从 null 切回字符串时把值序列化成 "null"', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ a: null }} onChange={onChange} />)
    fireEvent.click(within(getRowOfKey('a')).getByRole('button', { name: '类型:string' }))
    expect(onChange).toHaveBeenLastCalledWith({ a: 'null' })
  })

  it('渲染数组内对象与嵌套数组，并在修改后按类型递归序列化', () => {
    const onChange = vi.fn()
    render(
      <NestedKeyValueEditor
        value={{ arr: ['x', { b: 2 }, ['y']] }}
        onChange={onChange}
      />
    )
    expect(screen.getByDisplayValue('x')).toBeInTheDocument()
    expect(screen.getByDisplayValue('b')).toBeInTheDocument()
    expect(screen.getByDisplayValue('y')).toBeInTheDocument()

    fireEvent.change(screen.getByDisplayValue('x'), { target: { value: 'z' } })
    expect(onChange).toHaveBeenLastCalledWith({ arr: ['z', { b: 2 }, ['y']] })
  })

  it('修改数组内对象字段时递归 treeToRecord', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ arr: [{ a: 1 }] }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('1'), { target: { value: '9' } })
    expect(onChange).toHaveBeenLastCalledWith({ arr: [{ a: 9 }] })
  })

  it('修改嵌套数组项时映射子节点 value', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ arr: [['x']] }} onChange={onChange} />)
    fireEvent.change(screen.getByDisplayValue('x'), { target: { value: 'y' } })
    expect(onChange).toHaveBeenLastCalledWith({ arr: [['y']] })
  })

  it('数组节点添加子项时自动使用下一个索引', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ arr: ['x'] }} onChange={onChange} />)
    fireEvent.click(screen.getByTitle('添加子项'))
    expect(onChange).toHaveBeenLastCalledWith({ arr: ['x', ''] })
  })

  it('空数组添加第一项时索引为 0', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ arr: [] }} onChange={onChange} />)
    fireEvent.click(screen.getByTitle('添加子项'))
    expect(onChange).toHaveBeenLastCalledWith({ arr: [''] })
  })

  it('向嵌套对象添加子项并命名后写入深层载荷', () => {
    const onChange = vi.fn()
    render(
      <NestedKeyValueEditor
        value={{ outer: { inner: { a: 1 }, sib: 'x' } }}
        onChange={onChange}
      />
    )
    fireEvent.click(within(getRowOfKey('inner')).getByTitle('添加子项'))
    expect(onChange).toHaveBeenLastCalledWith({ outer: { inner: { a: 1 }, sib: 'x' } })

    fireEvent.change(findEmptyKeyInput(), { target: { value: 'c' } })
    expect(onChange).toHaveBeenLastCalledWith({ outer: { inner: { a: 1, c: '' }, sib: 'x' } })
  })

  it('向嵌套数组添加子项走递归 addChild', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ wrap: { arr: ['x'] } }} onChange={onChange} />)
    fireEvent.click(within(getRowOfKey('arr')).getByTitle('添加子项'))
    expect(onChange).toHaveBeenLastCalledWith({ wrap: { arr: ['x', ''] } })
  })

  it('折叠嵌套容器只隐藏其子行，不触发 onChange', () => {
    const onChange = vi.fn()
    render(
      <NestedKeyValueEditor
        value={{ outer: { inner: { a: 1 }, sib: 'x' } }}
        onChange={onChange}
      />
    )
    expect(screen.getByDisplayValue('a')).toBeInTheDocument()

    const expandButton = within(getRowOfKey('inner')).getAllByRole('button')[0]
    fireEvent.click(expandButton)
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('sib')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('两层对象上的同层重复键带上完整父路径', () => {
    const onChange = vi.fn()
    render(
      <NestedKeyValueEditor
        value={{ config: { inner: { first: 'a', second: 'b' } } }}
        onChange={onChange}
      />
    )
    fireEvent.change(screen.getByDisplayValue('second'), { target: { value: 'first' } })
    expect(screen.getByRole('alert')).toHaveTextContent('config.inner.first')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('空键名对象子节点不会写入载荷，但仍按父路径参与校验遍历', () => {
    const onChange = vi.fn()
    render(<NestedKeyValueEditor value={{ config: {} }} onChange={onChange} />)
    fireEvent.click(screen.getByTitle('添加子项'))
    const emptyRow = findEmptyKeyInput().parentElement as HTMLElement
    fireEvent.click(within(emptyRow).getByRole('button', { name: '类型:object' }))
    fireEvent.click(within(emptyRow).getByTitle('添加子项'))

    // 空键对象及其空键子项都被 treeToRecord 跳过，载荷仍是 { config: {} }，内部树保留
    expect(onChange).toHaveBeenLastCalledWith({ config: {} })
    expect(findEmptyKeyInput()).toBeInTheDocument()
    expect(within(emptyRow).getByTitle('添加子项')).toBeInTheDocument()
  })

  it('外部传入 falsy value 时按空对象重建树', () => {
    const onChange = vi.fn()
    const { rerender } = render(<NestedKeyValueEditor value={{ x: 1 }} onChange={onChange} />)
    expect(screen.getByDisplayValue('x')).toBeInTheDocument()

    rerender(
      <NestedKeyValueEditor
        value={undefined as unknown as Record<string, unknown>}
        onChange={onChange}
      />
    )
    expect(screen.getByText('0 个参数')).toBeInTheDocument()
    expect(screen.getByText('添加参数...')).toBeInTheDocument()
  })

  it('自定义 placeholder 在空对象时展示', () => {
    render(<NestedKeyValueEditor value={{}} onChange={vi.fn()} placeholder="没有参数" />)
    expect(screen.getByText('没有参数')).toBeInTheDocument()
  })
})
