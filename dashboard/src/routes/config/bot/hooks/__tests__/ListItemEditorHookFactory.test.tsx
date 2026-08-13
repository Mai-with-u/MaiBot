import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { createListItemEditorHook } from '../ListItemEditorHookFactory'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t, i18n: { language: 'zh-CN' } }) }
})

const itemSchema: ConfigSchema = {
  className: 'DemoItem',
  classDoc: '演示条目',
  fields: [
    { name: 'enabled', type: 'boolean', label: '启用', description: '', required: false },
    { name: 'count', type: 'integer', label: '数量', description: '', required: false },
    { name: 'score', type: 'number', label: '分数', description: '', required: false },
    { name: 'tags', type: 'array', label: '标签', description: '', required: false },
    { name: 'meta', type: 'object', label: '元数据', description: '', required: false },
    {
      name: 'kind',
      type: 'select',
      label: '类型',
      description: '',
      required: false,
      options: ['alpha', 'beta'],
    },
    { name: 'title', type: 'string', label: '标题', description: '', required: false },
    {
      name: 'copiedList',
      type: 'array',
      label: '拷贝列表',
      description: '',
      required: false,
      default: ['a'],
    },
    {
      name: 'copiedObject',
      type: 'object',
      label: '拷贝对象',
      description: '',
      required: false,
      default: { k: 1 },
    },
  ],
}

const fieldSchema: FieldSchema = {
  name: 'items',
  type: 'array',
  label: '条目列表',
  description: '列表说明',
  required: false,
}

describe('createListItemEditorHook', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value: () => false,
    })
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
      configurable: true,
      value: () => {},
    })
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
      configurable: true,
      value: () => {},
    })
  })

  it('缺少 nestedSchema 时展示无法渲染提示', () => {
    const ListHook = createListItemEditorHook()
    render(<ListHook fieldPath="demo.items" onChange={vi.fn()} schema={fieldSchema} value={[]} />)
    expect(screen.getByText('未获取到子配置 schema，无法渲染富编辑器。')).toBeInTheDocument()
  })

  it('按字段默认值和类型构造新 item，并浅拷贝数组/对象默认值', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({ addLabel: '添加条目' })

    render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )

    expect(screen.getByText('尚未添加任何条目，点击下方按钮新增。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加条目' }))

    const added = onChange.mock.calls.at(-1)?.[0][0] as Record<string, unknown>
    expect(added).toMatchObject({
      enabled: false,
      count: 0,
      score: 0,
      tags: [],
      meta: {},
      kind: 'alpha',
      title: '',
      copiedList: ['a'],
      copiedObject: { k: 1 },
    })
    expect(added.copiedList).not.toBe(itemSchema.fields.find((field) => field.name === 'copiedList')?.default)
    expect(added.copiedObject).not.toBe(itemSchema.fields.find((field) => field.name === 'copiedObject')?.default)
  })

  it('无 fields 的 schema 添加空对象，并支持删除与单字段修改', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addLabel: '添加空项',
      itemTitle: (item, index) => `${String(item.title ?? '空')} #${index + 1}`,
    })
    const emptySchema: ConfigSchema = { className: 'Empty', classDoc: '', fields: [] }

    const { rerender } = render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={emptySchema}
        value={[]}
      />,
    )

    await user.click(screen.getByRole('button', { name: '添加空项' }))
    expect(onChange).toHaveBeenLastCalledWith([{}])

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={{
          className: 'Named',
          classDoc: '',
          fields: [{ name: 'title', type: 'string', label: '标题', description: '', required: false }],
        }}
        value={[{ title: '旧标题' }]}
      />,
    )

    fireEvent.change(screen.getByDisplayValue('旧标题'), { target: { value: '新标题' } })
    expect(onChange).toHaveBeenLastCalledWith([{ title: '新标题' }])

    await user.click(screen.getByRole('button', { name: '删除旧标题 #1' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('fieldRows 布局会渲染行内字段和剩余字段，并能写入点路径', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addLabel: '添加',
      fieldRows: [['title', 'missing'], ['kind']],
      fieldSchemaOverrides: {
        title: { 'x-placeholder': '标题占位' },
      },
      visibleFields: ['title', 'kind', 'enabled'],
      renderOverview: ({ onAddItem, onItemFieldChange }) => (
        <div>
          <button type="button" onClick={() => onAddItem({ title: 'overview' })}>
            概览添加
          </button>
          <button type="button" onClick={() => onItemFieldChange(0, 'nested.path', 'deep')}>
            写入嵌套
          </button>
          <button type="button" onClick={() => onItemFieldChange(0, 'title', '单键')}>
            写入单键
          </button>
        </div>
      ),
    })

    const { rerender } = render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[{ title: '行内', kind: 'alpha', enabled: true }]}
      />,
    )

    expect(screen.getByDisplayValue('行内')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '概览添加' }))
    expect(onChange.mock.calls.at(-1)?.[0][1]).toMatchObject({ title: 'overview' })

    await user.click(screen.getByRole('button', { name: '写入嵌套' }))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
      title: '行内',
      nested: { path: 'deep' },
    })

    await user.click(screen.getByRole('button', { name: '写入单键' }))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({ title: '单键' })

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[{ title: '行内', kind: 'alpha', enabled: true, nested: { keep: true } }]}
      />,
    )
    await user.click(screen.getByRole('button', { name: '写入嵌套' }))
    expect(onChange.mock.calls.at(-1)?.[0][0].nested).toMatchObject({ keep: true, path: 'deep' })
  })

  it('collapseWhen 为真时折叠，点击后展开并可再次折叠', async () => {
    const user = userEvent.setup()
    const ListHook = createListItemEditorHook({
      addLabel: '添加',
      collapseWhen: ({ parentValues }) => parentValues?.folded === true,
      collapsedText: '已折叠',
      expandLabel: '展开列表',
      collapseLabel: '收起列表',
    })

    const { rerender } = render(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        parentValues={{ folded: true }}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )

    expect(screen.getByText('已折叠')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开列表' }))
    expect(screen.getByText('尚未添加任何条目，点击下方按钮新增。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '收起列表' }))
    expect(screen.getByText('已折叠')).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        parentValues={{ folded: false }}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.queryByRole('button', { name: '展开列表' })).not.toBeInTheDocument()
  })

  it('图标折叠按钮、自定义 renderItems 和非对象列表项都能工作', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addButtonPlacement: 'top',
      collapseWhen: () => true,
      collapseButtonDisplay: 'icon',
      expandLabel: '展开图标',
      infoText: '说明文本',
      helperText: '辅助说明',
      emptyText: '自定义空态',
      normalizeItems: (items) => items.map((item) => ({ ...item, normalized: true })),
      renderItems: ({ emptyText, items, onAddItem, onRemoveItem, renderItemEditor }) => (
        <div>
          <div>{emptyText}</div>
          <div>条目数 {items.length}</div>
          {items.map((item, index) => (
            <div key={index}>
              {renderItemEditor(item, index)}
              <button type="button" onClick={() => onRemoveItem(index)}>
                自定义删除 {index}
              </button>
            </div>
          ))}
          <button type="button" onClick={() => onAddItem()}>
            自定义添加
          </button>
        </div>
      ),
    })

    render(
      <ListHook
        fieldPath="section.list"
        onChange={onChange}
        schema={undefined}
        nestedSchema={itemSchema}
        value={['skip', { title: '对象' }]}
      />,
    )

    expect(screen.getByText('list')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '展开图标' }))
    expect(screen.getByText('自定义空态')).toBeInTheDocument()
    expect(screen.getByText('条目数 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'list 说明' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '自定义添加' }))
    expect(onChange.mock.calls.at(-1)?.[0].at(-1)).toMatchObject({ normalized: true })

    await user.click(screen.getByRole('button', { name: '自定义删除 0' }))
    expect(onChange).toHaveBeenCalled()
  })

  it('resolveLabel / resolveDescription 回退到 classDoc、className 和 fieldPath', () => {
    const ListHook = createListItemEditorHook({ addButtonPlacement: 'none' })

    const { rerender } = render(
      <ListHook fieldPath="cfg.items" onChange={vi.fn()} nestedSchema={itemSchema} value={[]} />,
    )
    expect(screen.getByText('items')).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath="cfg.items"
        onChange={vi.fn()}
        schema={{ className: 'OnlyClass', classDoc: '', fields: [] }}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('OnlyClass')).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath="cfg.items"
        onChange={vi.fn()}
        schema={{ className: 'OnlyClass', classDoc: '文档标题', fields: [] }}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.getAllByText('文档标题').length).toBeGreaterThan(1)

    rerender(
      <ListHook
        fieldPath=""
        onChange={vi.fn()}
        schema={{ className: '', classDoc: '', uiLabel: '界面标题', fields: [] }}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('界面标题')).toBeInTheDocument()
  })

  it('fallbackNestedSchema 在外部 schema 缺失时维持编辑器', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addLabel: '添加兜底',
      fallbackNestedSchema: {
        className: 'Fallback',
        classDoc: '',
        fields: [{ name: 'name', type: 'string', label: '名称', description: '', required: false, default: 'x' }],
      },
    })

    render(<ListHook fieldPath="demo.items" onChange={onChange} schema={fieldSchema} value={[]} />)
    await user.click(screen.getByRole('button', { name: '添加兜底' }))
    expect(onChange).toHaveBeenLastCalledWith([{ name: 'x' }])
  })

  it('value 非数组时视为空列表，非对象元素会被归一成空对象', () => {
    const ListHook = createListItemEditorHook()

    const { rerender } = render(
      <ListHook fieldPath="demo.items" onChange={vi.fn()} schema={fieldSchema} nestedSchema={itemSchema} value={undefined} />,
    )
    expect(screen.getByText('尚未添加任何条目，点击下方按钮新增。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加一项' })).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={{ not: 'array' }}
      />,
    )
    expect(screen.getByText('尚未添加任何条目，点击下方按钮新增。')).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[[1, 2], 'skip', null]}
      />,
    )
    expect(screen.getByText('条目 1')).toBeInTheDocument()
    expect(screen.getByText('条目 2')).toBeInTheDocument()
    expect(screen.getByText('条目 3')).toBeInTheDocument()
  })

  it('缺少 fields 的 schema 添加空对象，并按原始 default / 类型缺口补齐', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({ addLabel: '添加默认项' })
    const defaultSchema: ConfigSchema = {
      className: 'Defaults',
      classDoc: '',
      fields: [
        { name: 'flag', type: 'boolean', label: '开关', description: '', required: false, default: true },
        { name: 'zero', type: 'integer', label: '零', description: '', required: false, default: 0 },
        { name: 'blank', type: 'string', label: '空串', description: '', required: false, default: '' },
        { name: 'maybe', type: 'string', label: '可空', description: '', required: false, default: null },
        { name: 'choice', type: 'select', label: '选项', description: '', required: false },
        { name: 'note', type: 'textarea', label: '备注', description: '', required: false },
      ],
    }

    const { rerender } = render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={{ className: 'Bare', classDoc: '' } as ConfigSchema}
        value={[]}
      />,
    )
    await user.click(screen.getByRole('button', { name: '添加默认项' }))
    expect(onChange).toHaveBeenLastCalledWith([{}])

    rerender(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={defaultSchema}
        value={[]}
      />,
    )
    await user.click(screen.getByRole('button', { name: '添加默认项' }))
    expect(onChange).toHaveBeenLastCalledWith([
      {
        flag: true,
        zero: 0,
        blank: '',
        maybe: null,
        choice: '',
        note: '',
      },
    ])
  })

  it('setNested 会覆盖非对象中间节点，并支持多层路径与整表替换', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addButtonPlacement: 'none',
      renderOverview: ({ onItemFieldChange, onItemsChange }) => (
        <div>
          <button type="button" onClick={() => onItemFieldChange(0, 'tags.child', 'from-array')}>
            覆盖数组中间层
          </button>
          <button type="button" onClick={() => onItemFieldChange(0, 'title.child', 'from-string')}>
            覆盖字符串中间层
          </button>
          <button type="button" onClick={() => onItemFieldChange(0, 'a.b.c', 1)}>
            写入三层路径
          </button>
          <button
            type="button"
            onClick={() => onItemsChange([{ replaced: true }], { changedIndex: 0 })}
          >
            整表替换
          </button>
        </div>
      ),
    })

    render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[{ tags: ['keep'], title: 't' }]}
      />,
    )

    await user.click(screen.getByRole('button', { name: '覆盖数组中间层' }))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
      title: 't',
      tags: { child: 'from-array' },
    })

    await user.click(screen.getByRole('button', { name: '覆盖字符串中间层' }))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
      tags: ['keep'],
      title: { child: 'from-string' },
    })

    await user.click(screen.getByRole('button', { name: '写入三层路径' }))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
      a: { b: { c: 1 } },
    })

    await user.click(screen.getByRole('button', { name: '整表替换' }))
    expect(onChange).toHaveBeenLastCalledWith([{ replaced: true }])
  })

  it('fieldRows 全缺失行会被跳过，且无剩余字段时不再渲染额外表单', () => {
    const ListHook = createListItemEditorHook({
      addButtonPlacement: 'none',
      fieldRows: [['ghost'], ['title']],
      visibleFields: ['title'],
    })

    render(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[{ title: '仅标题', enabled: true, kind: 'alpha' }]}
      />,
    )

    expect(screen.getByDisplayValue('仅标题')).toBeInTheDocument()
    expect(screen.queryByText('启用')).not.toBeInTheDocument()
    expect(screen.queryByText('类型')).not.toBeInTheDocument()
  })

  it('fieldRows 剩余字段可通过表单写回，visibleFields 为空数组时不过滤', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    const ListHook = createListItemEditorHook({
      addButtonPlacement: 'none',
      fieldRows: [['title']],
      visibleFields: [],
    })

    render(
      <ListHook
        fieldPath="demo.items"
        onChange={onChange}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[{ title: '行内', enabled: false }]}
      />,
    )

    expect(screen.getByText('启用')).toBeInTheDocument()
    await user.click(screen.getByRole('switch'))
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
      title: '行内',
      enabled: true,
    })
  })

  it('折叠按钮在未自定义文案时使用默认可见文本，无障碍名称保留源码回退值', async () => {
    const user = userEvent.setup()
    const ListHook = createListItemEditorHook({
      collapseWhen: () => true,
    })

    render(
      <ListHook
        fieldPath="demo.items"
        onChange={vi.fn()}
        schema={fieldSchema}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )

    expect(screen.getByText('当前配置已折叠，可手动展开查看或编辑。')).toBeInTheDocument()
    // 可见文案是「展开」，但 aria-label 使用源码里的默认乱码字节
    const expandButton = screen.getByRole('button', { name: '灞曞紑' })
    expect(expandButton).toHaveTextContent('展开')

    await user.click(expandButton)
    expect(screen.getByText('尚未添加任何条目，点击下方按钮新增。')).toBeInTheDocument()
    const collapseButton = screen.getByRole('button', { name: '鎶樺彔' })
    expect(collapseButton).toHaveTextContent('折叠')
  })

  it('fieldPath 为空串时标题是空字符串；未传 fieldPath 才回退到列表配置', () => {
    const ListHook = createListItemEditorHook({ addButtonPlacement: 'none' })

    const { rerender, container } = render(
      <ListHook fieldPath="" onChange={vi.fn()} nestedSchema={itemSchema} value={[]} />,
    )
    // "".split('.').at(-1) 是 ""，不会走到 ?? '列表配置'
    expect(container.querySelector('[data-dashboard-card-title="true"]')).toHaveTextContent('')

    rerender(
      <ListHook
        fieldPath=""
        onChange={vi.fn()}
        schema={{ className: '', classDoc: '', uiLabel: '', fields: [] }}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(container.querySelector('[data-dashboard-card-title="true"]')).toHaveTextContent('')

    rerender(
      <ListHook
        fieldPath={undefined as unknown as string}
        onChange={vi.fn()}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('列表配置')).toBeInTheDocument()

    rerender(
      <ListHook
        fieldPath={undefined as unknown as string}
        onChange={vi.fn()}
        schema={{ className: '', classDoc: '', uiLabel: '', fields: [] }}
        nestedSchema={itemSchema}
        value={[]}
      />,
    )
    expect(screen.getByText('列表配置')).toBeInTheDocument()

    rerender(
      <ListHook fieldPath="" onChange={vi.fn()} schema={fieldSchema} value={[]} />,
    )
    expect(screen.getByText('条目列表')).toBeInTheDocument()
    expect(screen.getByText('未获取到子配置 schema，无法渲染富编辑器。')).toBeInTheDocument()
  })

  it('本地化 label 会解析为中文标题，缺少 onChange 时增删不会抛错', async () => {
    const user = userEvent.setup()
    const ListHook = createListItemEditorHook()

    render(
      <ListHook
        fieldPath="demo.items"
        schema={{
          name: 'items',
          type: 'array',
          label: { zh_CN: '本地化列表' },
          description: '',
          required: false,
        }}
        nestedSchema={{
          className: 'Named',
          classDoc: '',
          fields: [{ name: 'title', type: 'string', label: '标题', description: '', required: false }],
        }}
        value={[{ title: '可删' }]}
      />,
    )

    expect(screen.getByText('本地化列表')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加一项' }))
    await user.click(screen.getByRole('button', { name: '删除条目 1' }))
  })
})
