import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ListFieldEditor } from '@/components/ListFieldEditor'

const dndState = vi.hoisted(() => ({
  onDragEnd: undefined as ((event: unknown) => void) | undefined,
  sortableItems: [] as string[],
}))

vi.mock('@dnd-kit/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@dnd-kit/core')>()

  return {
    ...actual,
    DndContext: ({
      children,
      onDragEnd,
    }: {
      children: ReactNode
      onDragEnd?: (event: unknown) => void
    }) => {
      dndState.onDragEnd = onDragEnd
      return <div>{children}</div>
    },
  }
})

vi.mock('@dnd-kit/sortable', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@dnd-kit/sortable')>()

  return {
    ...actual,
    SortableContext: ({
      children,
      items,
    }: {
      children: ReactNode
      items: string[]
    }) => {
      dndState.sortableItems = [...items]
      return <div>{children}</div>
    },
    useSortable: () => ({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: undefined,
      isDragging: false,
    }),
  }
})

// jsdom 未实现 Pointer Capture，Radix Select 打开选项时会调用这些方法
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

describe('ListFieldEditor', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
    dndState.onDragEnd = undefined
    dndState.sortableItems = []
  })

  it('将 multiple=true 的 select 子字段渲染为多选下拉', async () => {
    const user = userEvent.setup()
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ api_names: [] }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          api_names: {
            type: 'select',
            label: '需要推送的 API 配置组',
            default: [],
            multiple: true,
            choices: ['daily_news', 'ai_news', 'it_news'],
          },
        }}
      />
    )

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByText('ai_news'))

    expect(handleChange).toHaveBeenCalledWith([{ api_names: ['ai_news'] }])
  })

  it('对象数组中的多选子字段会将已选数字值规范为字符串并正确切换', async () => {
    const user = userEvent.setup()
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ api_ids: [1] }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          api_ids: {
            type: 'select',
            label: 'API 编号',
            default: [],
            multiple: true,
            choices: [1, 2, 3],
          },
        }}
      />
    )

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getAllByText('1').at(-1)!)

    expect(handleChange).toHaveBeenCalledWith([{ api_ids: [] }])
  })

  it('父级 disabled 时禁用对象数组中的多选子字段', () => {
    render(
      <ListFieldEditor
        value={[{ api_names: ['daily_news'] }]}
        onChange={vi.fn()}
        itemType="object"
        itemFields={{
          api_names: {
            type: 'select',
            label: '需要推送的 API 配置组',
            default: [],
            multiple: true,
            choices: ['daily_news', 'ai_news', 'it_news'],
          },
        }}
        disabled
      />
    )

    expect(screen.getByRole('combobox')).toBeDisabled()
  })

  it('保留对象数组子字段中的嵌套字符串数组编辑能力', async () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ push_groups: ['group-a'] }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          push_groups: {
            type: 'array',
            label: '推送群列表',
            default: [],
            item_type: 'string',
          },
        }}
      />
    )

    const input = screen.getByDisplayValue('group-a')
    fireEvent.change(input, { target: { value: 'group-b' } })

    expect(handleChange).toHaveBeenLastCalledWith([{ push_groups: ['group-b'] }])
  })

  it('拖拽排序后正在编辑的数字项状态会跟随项目移动', () => {
    const ControlledListFieldEditor = () => {
      const [items, setItems] = useState<unknown[]>([1, 2])

      return (
        <ListFieldEditor
          value={items}
          onChange={setItems}
          itemType="number"
        />
      )
    }

    render(<ControlledListFieldEditor />)

    const inputs = screen.getAllByRole('spinbutton')
    fireEvent.focus(inputs[0])
    fireEvent.change(inputs[0], { target: { value: '10' } })

    expect(dndState.onDragEnd).toBeDefined()
    act(() => {
      dndState.onDragEnd?.({
        active: { id: dndState.sortableItems[0] },
        over: { id: dndState.sortableItems[1] },
      })
    })

    expect(screen.getAllByRole('spinbutton').map((input) => (input as HTMLInputElement).value)).toEqual(['2', '10'])
  })

  it('空数组显示占位提示，添加字符串项后可编辑并删除', () => {
    const handleChange = vi.fn()

    const { rerender } = render(
      <ListFieldEditor value={[]} onChange={handleChange} placeholder="自定义占位" />
    )

    expect(screen.getByText('暂无数据，点击下方按钮添加')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /添加项目/ }))
    expect(handleChange).toHaveBeenCalledWith([''])

    rerender(<ListFieldEditor value={['alpha']} onChange={handleChange} placeholder="自定义占位" />)

    const input = screen.getByPlaceholderText('自定义占位')
    expect(input).toHaveValue('alpha')
    fireEvent.change(input, { target: { value: 'beta' } })
    expect(handleChange).toHaveBeenLastCalledWith(['beta'])

    fireEvent.click(getRemoveButtons()[0])
    expect(handleChange).toHaveBeenLastCalledWith([])
  })

  it('添加数字项时写入 0，并可修改该项', () => {
    const handleChange = vi.fn()

    const { rerender } = render(
      <ListFieldEditor value={[1]} onChange={handleChange} itemType="number" />
    )

    fireEvent.click(screen.getByRole('button', { name: /添加项目/ }))
    expect(handleChange).toHaveBeenCalledWith([1, 0])

    rerender(<ListFieldEditor value={[1, 0]} onChange={handleChange} itemType="number" />)

    fireEvent.change(screen.getAllByRole('spinbutton')[1], { target: { value: '8' } })
    expect(handleChange).toHaveBeenLastCalledWith([1, 8])
  })

  it('添加对象项时按字段默认值建对象，缺省字段回落为空字符串', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          name: { type: 'string', default: 'untitled' },
          count: { type: 'number', default: 3 },
          note: { type: 'string' },
        }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /添加项目/ }))
    expect(handleChange).toHaveBeenCalledWith([{ name: 'untitled', count: 3, note: '' }])
  })

  it('达到 maxItems 时禁用添加按钮并显示计数，点击不会追加', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor value={['a', 'b']} onChange={handleChange} maxItems={2} />
    )

    const addButton = screen.getByRole('button', { name: /添加项目/ })
    expect(addButton).toBeDisabled()
    expect(addButton).toHaveTextContent('(2/2)')
    expect(screen.getByText('最多 2 项')).toBeInTheDocument()

    fireEvent.click(addButton)
    expect(handleChange).not.toHaveBeenCalled()
  })

  it('达到 minItems 时禁用删除，点击删除按钮不会减少项', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor value={['keep']} onChange={handleChange} minItems={1} />
    )

    expect(screen.getByText('至少 1 项')).toBeInTheDocument()
    const removeButton = getRemoveButtons()[0]
    expect(removeButton).toBeDisabled()

    fireEvent.click(removeButton)
    expect(handleChange).not.toHaveBeenCalled()
  })

  it('同时配置最小和最大项数时展示区间提示', () => {
    render(
      <ListFieldEditor value={['a']} onChange={vi.fn()} minItems={1} maxItems={4} />
    )

    expect(screen.getByText('允许 1 - 4 项')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /添加项目/ })).toHaveTextContent('(1/4)')
  })

  it('删除中间项后后续项前移，仍可编辑剩余项', () => {
    const handleChange = vi.fn()

    const { rerender } = render(
      <ListFieldEditor value={['one', 'two', 'three']} onChange={handleChange} />
    )

    fireEvent.click(getRemoveButtons()[1])
    expect(handleChange).toHaveBeenCalledWith(['one', 'three'])

    rerender(<ListFieldEditor value={['one', 'three']} onChange={handleChange} />)
    fireEvent.change(screen.getByDisplayValue('three'), { target: { value: 'last' } })
    expect(handleChange).toHaveBeenLastCalledWith(['one', 'last'])
  })

  it('逗号分隔字符串会被拆成列表，非数组且非有效字符串则显示空状态', () => {
    const { rerender } = render(
      <ListFieldEditor value="foo, bar, baz" onChange={vi.fn()} />
    )

    expect(screen.getByDisplayValue('foo')).toBeInTheDocument()
    expect(screen.getByDisplayValue('bar')).toBeInTheDocument()
    expect(screen.getByDisplayValue('baz')).toBeInTheDocument()

    rerender(<ListFieldEditor value="   " onChange={vi.fn()} />)
    expect(screen.getByText('暂无数据，点击下方按钮添加')).toBeInTheDocument()

    rerender(<ListFieldEditor value={{ not: 'array' }} onChange={vi.fn()} />)
    expect(screen.getByText('暂无数据，点击下方按钮添加')).toBeInTheDocument()
  })

  it('object 类型但未提供 itemFields 时按字符串项渲染和追加', () => {
    const handleChange = vi.fn()

    const { rerender } = render(
      <ListFieldEditor value={['legacy']} onChange={handleChange} itemType="object" />
    )

    expect(screen.getByDisplayValue('legacy')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /添加项目/ }))
    expect(handleChange).toHaveBeenCalledWith(['legacy', ''])

    rerender(
      <ListFieldEditor value={['legacy', '']} onChange={handleChange} itemType="object" />
    )
    fireEvent.change(screen.getAllByRole('textbox')[1], { target: { value: 'new' } })
    expect(handleChange).toHaveBeenLastCalledWith(['legacy', 'new'])
  })

  it('拖拽缺少有效目标时不改动列表顺序', () => {
    const handleChange = vi.fn()

    render(<ListFieldEditor value={['a', 'b']} onChange={handleChange} />)

    expect(dndState.onDragEnd).toBeDefined()
    act(() => {
      dndState.onDragEnd?.({ active: { id: dndState.sortableItems[0] }, over: null })
    })
    act(() => {
      dndState.onDragEnd?.({
        active: { id: dndState.sortableItems[0] },
        over: { id: dndState.sortableItems[0] },
      })
    })
    act(() => {
      dndState.onDragEnd?.({
        active: { id: 'missing-active' },
        over: { id: dndState.sortableItems[1] },
      })
    })
    act(() => {
      dndState.onDragEnd?.({
        active: { id: dndState.sortableItems[0] },
        over: { id: 'missing-over' },
      })
    })

    expect(handleChange).not.toHaveBeenCalled()
  })

  it('disabled 时禁用添加、删除和输入', () => {
    render(
      <ListFieldEditor value={['locked']} onChange={vi.fn()} disabled minItems={0} />
    )

    expect(screen.getByRole('button', { name: /添加项目/ })).toBeDisabled()
    expect(getRemoveButtons()[0]).toBeDisabled()
    expect(screen.getByDisplayValue('locked')).toBeDisabled()
  })

  it('对象项的 boolean 与 switch 字段可切换，缺省值回落到 default', async () => {
    const user = userEvent.setup()
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ enabled: false }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          enabled: { type: 'boolean', label: '启用' },
          visible: { type: 'switch', default: true },
        }}
      />
    )

    const switches = screen.getAllByRole('switch')
    expect(switches[0]).not.toBeChecked()
    expect(switches[1]).toBeChecked()

    await user.click(switches[0])
    expect(handleChange).toHaveBeenCalledWith([{ enabled: true }])
  })

  it('slider 字段与带 min/max 的 number 字段走滑块，并回写数值', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ volume: 4 }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          volume: { type: 'slider', label: '音量', min: 0, max: 10, step: 1 },
          weight: { type: 'number', label: '权重', min: 1, max: 5, default: 2 },
          fallback: { type: 'slider' },
        }}
      />
    )

    const sliders = screen.getAllByRole('slider')
    expect(sliders[0]).toHaveAttribute('aria-valuenow', '4')
    expect(sliders[1]).toHaveAttribute('aria-valuenow', '2')
    expect(sliders[2]).toHaveAttribute('aria-valuenow', '0')

    fireEvent.keyDown(sliders[0], { key: 'ArrowRight' })
    expect(handleChange).toHaveBeenCalledWith([expect.objectContaining({ volume: 5 })])
  })

  it('无范围的 number 子字段使用数字输入框', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ count: 2 }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          count: { type: 'number', label: '次数', placeholder: '输入次数' },
        }}
      />
    )

    fireEvent.change(screen.getByPlaceholderText('输入次数'), { target: { value: '9' } })
    expect(handleChange).toHaveBeenCalledWith([{ count: 9 }])
  })

  it('单选 select 子字段变更时回写选项值，无 label 时使用字段名', async () => {
    const user = userEvent.setup()
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ mode: 'a' }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          mode: { type: 'select', choices: ['a', 'b'], placeholder: '请选择模式' },
        }}
      />
    )

    expect(screen.getByText('mode')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'b' }))
    expect(handleChange).toHaveBeenCalledWith([{ mode: 'b' }])
  })

  it('多选 select 在值非数组时回落到 default，再切换选项', async () => {
    const user = userEvent.setup()
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ tags: 'legacy' }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          tags: {
            type: 'select',
            label: '标签',
            multiple: true,
            default: ['daily_news'],
            choices: ['daily_news', 'ai_news'],
          },
        }}
      />
    )

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByText('ai_news'))
    expect(handleChange).toHaveBeenCalledWith([{ tags: ['daily_news', 'ai_news'] }])
  })

  it('嵌套数组字段在值为非数组且无 default 时从空列表添加', () => {
    const handleChange = vi.fn()

    const { rerender } = render(
      <ListFieldEditor
        value={[{ aliases: null }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          aliases: { type: 'array', label: '别名', item_type: 'string' },
        }}
      />
    )

    const addButtons = screen.getAllByRole('button', { name: /添加项目/ })
    fireEvent.click(addButtons[0])
    expect(handleChange).toHaveBeenCalledWith([{ aliases: [''] }])

    rerender(
      <ListFieldEditor
        value={[{ aliases: [''] }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          aliases: { type: 'array', label: '别名', item_type: 'string' },
        }}
      />
    )

    fireEvent.click(getRemoveButtons()[0])
    expect(handleChange).toHaveBeenLastCalledWith([{ aliases: [] }])
  })

  it('对象字符串子字段变更时合并写回整项', () => {
    const handleChange = vi.fn()

    render(
      <ListFieldEditor
        value={[{ title: 'old' }]}
        onChange={handleChange}
        itemType="object"
        itemFields={{
          title: { type: 'string', label: '标题', placeholder: '输入标题', default: 'untitled' },
        }}
      />
    )

    fireEvent.change(screen.getByPlaceholderText('输入标题'), { target: { value: 'new-title' } })
    expect(handleChange).toHaveBeenCalledWith([{ title: 'new-title' }])
  })

  it('字符串子字段在值为空时展示字段 default', () => {
    render(
      <ListFieldEditor
        value={[{}]}
        onChange={vi.fn()}
        itemType="object"
        itemFields={{
          title: { type: 'string', default: 'fallback-title' },
        }}
      />
    )

    expect(screen.getByDisplayValue('fallback-title')).toBeInTheDocument()
  })
})

function getRemoveButtons() {
  return screen
    .getAllByRole('button')
    .filter((button) => button.querySelector('svg.lucide-trash-2'))
}