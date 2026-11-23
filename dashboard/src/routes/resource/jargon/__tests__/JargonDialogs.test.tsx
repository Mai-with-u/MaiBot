import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  BatchDeleteConfirmDialog,
  DeleteConfirmDialog,
  JargonCreateDialog,
  JargonDetailDialog,
  JargonExportDialog,
} from '../JargonDialogs'
import { JargonList } from '../JargonList'
import * as jargonApi from '@/lib/jargon-api'

import type { Jargon, JargonChatInfo } from '@/types/jargon'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  // Radix Select 打开下拉时会调用 pointer-capture，jsdom 未实现
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

// toast 断言用稳定引用的 spy（vi.mock 工厂被提升，必须用 vi.hoisted）
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/jargon-api', () => ({
  createJargon: vi.fn(),
  importJargons: vi.fn(),
  updateJargon: vi.fn(),
  getJargonVersions: vi.fn(),
  getJargonEvidence: vi.fn(),
}))

interface MultiSelectStubProps {
  options: { label: string; value: string }[]
  selected: string[]
  onChange: (values: string[]) => void
}

// MultiSelect 是 Radix 浮层组件，jsdom 下交互受限，桩为简单按钮驱动 onChange
vi.mock('@/components/ui/multi-select', () => ({
  MultiSelect: ({ options, selected, onChange }: MultiSelectStubProps) => (
    <div data-testid="multi-select">
      {options.map((option) => (
        <button
          type="button"
          key={option.value}
          onClick={() => onChange([...selected, option.value])}
        >
          {`ms-add-${option.value}`}
        </button>
      ))}
      <button type="button" onClick={() => onChange([])}>
        ms-clear
      </button>
    </div>
  ),
}))

const chatList: JargonChatInfo[] = [
  { session_id: 's1', chat_name: '测试群', platform: 'qq', account_id: null, is_group: true },
]

/** 构造一条完整的黑话数据 */
function makeJargon(id: number, overrides: Partial<Jargon> = {}): Jargon {
  return {
    id,
    content: `黑话${id}`,
    meaning: `含义${id}`,
    session_id: 's1',
    session_ids: ['s1'],
    chat_name: '测试群',
    chat_names: ['测试群'],
    is_global: false,
    count: 3,
    is_jargon: true,
    is_legacy_empty_meaning: false,
    is_complete: false,
    allow_update: true,
    created_by: 'AI',
    created_timestamp: '2026-01-01T00:00:00Z',
    updated_timestamp: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

describe('JargonDetailDialog', () => {
  function renderDetail(jargon: Jargon | null) {
    const onChanged = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <JargonDetailDialog
        jargon={jargon}
        open
        onOpenChange={onOpenChange}
        chatList={chatList}
        onChanged={onChanged}
      />
    )
    return { onChanged, onOpenChange }
  }

  it('jargon 为 null 时不渲染对话框', () => {
    renderDetail(null)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('渲染详情：表单初值与状态徽章', () => {
    renderDetail(
      makeJargon(7, { is_global: true, is_complete: true, is_legacy_empty_meaning: true })
    )
    expect(screen.getByRole('dialog', { name: '黑话7' })).toBeInTheDocument()
    expect(screen.getByText('旧数据')).toBeInTheDocument()
    expect(screen.getByText('推断完成')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '内容' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('含义')).toHaveValue('含义7')
    expect(screen.getByRole('combobox', { name: '黑话状态' })).toHaveTextContent('黑话')
    expect(screen.getByRole('switch', { name: '通用词条' })).toBeChecked()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('清空聊天后保存提示至少选择一个聊天', async () => {
    const user = userEvent.setup()
    renderDetail(makeJargon(1))
    await user.click(screen.getByText('ms-clear'))
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '验证失败', description: '请至少选择一个聊天' })
    )
    expect(jargonApi.updateJargon).not.toHaveBeenCalled()
  })

  it('保存成功：以编辑后的表单调用 updateJargon，onChanged 收到返回数据', async () => {
    const user = userEvent.setup()
    const updated = makeJargon(1, { meaning: '新含义' })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: updated,
    })
    const { onChanged } = renderDetail(makeJargon(1))
    const input = screen.getByLabelText('含义')
    await user.clear(input)
    await user.type(input, '新含义')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ meaning: '新含义', session_ids: ['s1'], is_jargon: true, allow_update: false })
      )
    )
    expect(vi.mocked(jargonApi.updateJargon).mock.calls[0][1]).not.toHaveProperty('content')
    expect(onChanged).toHaveBeenCalledWith(updated)
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存成功' }))
  })

  it('保存失败：展示接口错误信息', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.updateJargon).mockRejectedValue(new Error('后端错误'))
    const { onChanged } = renderDetail(makeJargon(1))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '后端错误' })
      )
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('可以暂停自动更新', async () => {
    const user = userEvent.setup()
    const pinned = makeJargon(1, { created_by: 'MANUAL', allow_update: false })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: pinned,
    })
    const { onChanged } = renderDetail(makeJargon(1, { meaning: '老含义' }))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    expect(jargonApi.updateJargon).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ meaning: '老含义', allow_update: false, is_jargon: true })
      )
    )
    expect(onChanged).toHaveBeenCalledWith(pinned)
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存成功' }))
  })

  it('积累中的词条默认允许更新', () => {
    renderDetail(makeJargon(1, { meaning: null, is_jargon: null }))
    expect(screen.getByRole('combobox', { name: '黑话状态' })).toHaveTextContent('积累中')
    expect(screen.getByRole('checkbox', { name: '允许更新' })).toBeChecked()
  })

  it('可恢复手动词条的自动更新', async () => {
    const user = userEvent.setup()
    const unpinned = makeJargon(1, { created_by: 'AI' })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: unpinned,
    })
    renderDetail(makeJargon(1, { created_by: 'MANUAL', allow_update: false }))
    const lock = screen.getByRole('checkbox', { name: '允许更新' })
    expect(lock).not.toBeChecked()
    await user.click(lock)
    expect(jargonApi.updateJargon).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ allow_update: true })
      )
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存成功' }))
  })

  it('session_ids 为空时回退为 session_id，保存仍带上该聊天', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: makeJargon(1),
    })
    renderDetail(makeJargon(1, { session_ids: [] }))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ session_ids: ['s1'], session_id: 's1' })
      )
    )
  })

  it('编辑含义、切换黑话状态与全局开关后保存', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const updated = makeJargon(1, { meaning: '新含义', is_jargon: false, is_global: true })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: updated,
    })
    const { onChanged } = renderDetail(makeJargon(1))

    const meaningInput = screen.getByLabelText('含义')
    await user.clear(meaningInput)
    await user.type(meaningInput, '新含义')
    const lock = screen.getByRole('checkbox', { name: '允许更新' })
    expect(lock).not.toBeChecked()
    await user.click(lock)

    await user.click(screen.getByRole('combobox', { name: '黑话状态' }))
    await user.click(await screen.findByRole('option', { name: '不是黑话' }))
    expect(meaningInput).toBeDisabled()
    expect(lock).toBeEnabled()
    expect(screen.getAllByText('不是黑话').length).toBeGreaterThanOrEqual(1)

    await user.click(screen.getByRole('switch', { name: '通用词条' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          meaning: undefined,
          is_jargon: false,
          is_global: true,
        })
      )
    )
    expect(onChanged).toHaveBeenCalledWith(updated)
  })

  it('保存成功但无 data 时只提示成功，不回调 onChanged', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
    } as never)
    const { onChanged } = renderDetail(makeJargon(1))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存成功' }))
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('保存失败且非 Error 时展示兜底文案', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.updateJargon).mockRejectedValue('boom')
    renderDetail(makeJargon(1))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '无法更新黑话' })
      )
    )
  })

  it('右上角关闭时可以继续编辑或放弃修改', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDetail(makeJargon(1))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(onOpenChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(screen.getByRole('checkbox', { name: '允许更新' })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByRole('button', { name: '放弃修改' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(jargonApi.updateJargon).not.toHaveBeenCalled()
  })

  it('暂停更新后也可以保存空释义', async () => {
    const user = userEvent.setup()
    renderDetail(makeJargon(1))
    await user.click(screen.getByRole('checkbox', { name: '允许更新' }))
    await user.clear(screen.getByLabelText('含义'))
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(jargonApi.updateJargon).toHaveBeenCalledWith(1, expect.objectContaining({ allow_update: false, meaning: '' }))
  })
})

describe('JargonCreateDialog', () => {
  function renderCreate() {
    const onSuccess = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <JargonCreateDialog
        open
        onOpenChange={onOpenChange}
        chatList={chatList}
        onSuccess={onSuccess}
      />
    )
    return { onSuccess, onOpenChange }
  }

  it('必填字段缺失时提示验证失败，不调用创建接口', async () => {
    const user = userEvent.setup()
    renderCreate()
    await user.click(screen.getByRole('button', { name: '创建' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '验证失败', description: '请填写必填字段：内容和聊天' })
    )
    expect(jargonApi.createJargon).not.toHaveBeenCalled()
  })

  it('填写内容并选择聊天后创建成功：表单重置且回调 onSuccess', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.createJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: makeJargon(9, { content: '新词' }),
    })
    const { onSuccess } = renderCreate()
    const contentInput = screen.getByLabelText(/内容/)
    await user.type(contentInput, '新词')
    await user.click(screen.getByText('ms-add-s1'))
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() =>
      expect(jargonApi.createJargon).toHaveBeenCalledWith({
        content: '新词',
        meaning: '',
        session_ids: ['s1'],
        session_id: 's1',
        is_global: false,
      })
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '创建成功' }))
    expect(onSuccess).toHaveBeenCalledTimes(1)
    // 创建成功后表单重置
    expect(contentInput).toHaveValue('')
  })

  it('创建失败：展示错误信息且不回调 onSuccess', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.createJargon).mockRejectedValue(new Error('词条冲突'))
    const { onSuccess } = renderCreate()
    await user.type(screen.getByLabelText(/内容/), '新词')
    await user.click(screen.getByText('ms-add-s1'))
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '创建失败', description: '词条冲突' })
      )
    )
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('填写含义并开启全局后创建成功', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.createJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: makeJargon(10, { content: '新词', meaning: '解释', is_global: true }),
    })
    const { onSuccess } = renderCreate()
    await user.type(screen.getByLabelText(/内容/), '新词')
    await user.type(screen.getByLabelText('含义'), '解释')
    await user.click(screen.getByRole('switch', { name: '通用词条' }))
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() =>
      expect(jargonApi.createJargon).toHaveBeenCalledWith({
        content: '新词',
        meaning: '解释',
        session_ids: [],
        is_global: true,
      })
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('创建失败且非 Error 时展示兜底文案', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.createJargon).mockRejectedValue('boom')
    const { onSuccess } = renderCreate()
    await user.type(screen.getByLabelText(/内容/), '新词')
    await user.click(screen.getByText('ms-add-s1'))
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '创建失败', description: '无法创建黑话' })
      )
    )
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('取消按钮回调 onOpenChange(false)', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderCreate()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('JargonExportDialog', () => {
  function renderExport(
    overrides: Partial<{
      selectedCount: number
      scope: 'all' | 'selected'
      includeChatInfo: boolean
      exporting: boolean
    }> = {}
  ) {
    const onExport = vi.fn(async () => {})
    const onScopeChange = vi.fn()
    const onIncludeChatInfoChange = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <JargonExportDialog
        open
        onOpenChange={onOpenChange}
        selectedCount={overrides.selectedCount ?? 0}
        scope={overrides.scope ?? 'all'}
        includeChatInfo={overrides.includeChatInfo ?? false}
        exporting={overrides.exporting ?? false}
        onScopeChange={onScopeChange}
        onIncludeChatInfoChange={onIncludeChatInfoChange}
        onExport={onExport}
      />
    )
    return { onExport, onScopeChange, onIncludeChatInfoChange, onOpenChange }
  }

  it('无选中项时 scope=selected 回退为 all 导出', async () => {
    const user = userEvent.setup()
    const { onExport } = renderExport({ selectedCount: 0, scope: 'selected' })
    await user.click(screen.getByRole('button', { name: '导出' }))
    expect(onExport).toHaveBeenCalledWith('all', false)
  })

  it('有选中项时按 selected 与包含聊天信息导出', async () => {
    const user = userEvent.setup()
    const { onExport } = renderExport({
      selectedCount: 2,
      scope: 'selected',
      includeChatInfo: true,
    })
    await user.click(screen.getByRole('button', { name: '导出' }))
    expect(onExport).toHaveBeenCalledWith('selected', true)
  })

  it('切换开关回调 onIncludeChatInfoChange', async () => {
    const user = userEvent.setup()
    const { onIncludeChatInfoChange } = renderExport()
    await user.click(screen.getByRole('switch'))
    expect(onIncludeChatInfoChange).toHaveBeenCalledWith(true)
  })

  it('导出中：按钮文案变化且取消/导出均禁用', () => {
    renderExport({ exporting: true })
    expect(screen.getByRole('button', { name: '导出中...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
  })

  it('有选中项时切换范围为 selected', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const { onScopeChange } = renderExport({ selectedCount: 2, scope: 'all' })
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '已选择 2 个' }))
    expect(onScopeChange).toHaveBeenCalledWith('selected')
  })

  it('取消按钮回调 onOpenChange(false)', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderExport()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('删除确认对话框', () => {
  it('DeleteConfirmDialog 显示黑话内容，确认与取消各自回调', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <DeleteConfirmDialog
        jargon={makeJargon(1, { content: '词A' })}
        open
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />
    )
    expect(screen.getByRole('alertdialog')).toHaveTextContent('删除「词A」')
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('BatchDeleteConfirmDialog 显示数量，确认回调 onConfirm', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(<BatchDeleteConfirmDialog open onOpenChange={vi.fn()} onConfirm={onConfirm} count={3} />)
    expect(screen.getByRole('alertdialog')).toHaveTextContent('3 条释义')
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})


it('在归属详情中按来源查看素材，编辑时保留正在修改的版本', async () => {
  const user = userEvent.setup()
  const row = makeJargon(1, { scope_kind: 'chat', scope_id: 's1', scope_name: '测试群' })
  const other = makeJargon(2, { content: row.content, scope_kind: 'global', scope_name: '通用词库', meaning: '另一种解释' })
  const switchScope = vi.fn()
  vi.mocked(jargonApi.getJargonVersions).mockResolvedValue([
    row, other, makeJargon(3, { content: row.content, scope_name: '归档群', archived: true }),
  ])
  vi.mocked(jargonApi.getJargonEvidence).mockResolvedValue([
    { id: 11, source_name: '测试群', excerpt: '当时的上下文', recorded_at: null },
    { id: 12, source_name: '旧记录', excerpt: '', evidence: '[[{"platform":"qq","message_id":"1"}]]', recorded_at: null },
  ])
  function Detail() {
    const [selected, setSelected] = useState(row)
    return <JargonDetailDialog jargon={selected} open onOpenChange={vi.fn()} chatList={chatList}
      onScopeChange={(next) => { if (next) { switchScope(next); setSelected(next) } }} onChanged={vi.fn()} />
  }
  render(<QueryClientProvider client={new QueryClient()}><Detail /></QueryClientProvider>)
  const index = within(await screen.findByRole('navigation', { name: '作用域索引' }))
  const globalScope = await index.findByRole('button', { name: '通用词库' })
  expect(index.getByRole('button', { name: '测试群' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('textbox', { name: '含义' })).toHaveValue('含义1')
  await user.click(globalScope)
  const global = screen.getByRole('article', { name: '通用词库' })
  expect(within(global).getByRole('textbox', { name: '含义' })).toHaveValue('另一种解释')
  expect(screen.queryByRole('article', { name: '测试群' })).not.toBeInTheDocument()
  expect(switchScope).toHaveBeenCalledWith(other)
  const disclosure = screen.getByText(/其他作用域（1）/)
  expect(disclosure.closest('details')).not.toHaveAttribute('open')
  expect(disclosure).toHaveTextContent('1 个释义不同')
  await user.click(disclosure)
  expect(screen.queryByText('归档群')).not.toBeInTheDocument()
  expect(within(screen.getByRole('article', { name: '通用词库' })).getByRole('button', { name: '保存' })).toBeVisible()
  expect(screen.getByRole('textbox', { name: '含义' })).toHaveValue('另一种解释')
  await user.click(screen.getByRole('button', { name: '测试群' }))
  await user.click(within(screen.getByRole('article', { name: '测试群' })).getByRole('button', { name: '线索来源' }))
  await user.click(await screen.findByText(/测试群 · 历史线索/))
  expect(screen.getByText('当时的上下文')).toBeVisible()
  await user.click(screen.getByText('旧记录 · 历史线索'))
  expect(screen.getByText('这条线索只返回了消息引用，尚未取得聊天原文')).toBeVisible()
  await user.click(screen.getByRole('tab', { name: '释义' }))
  await user.type(screen.getByRole('textbox', { name: '含义' }), '更新')
  await user.click(screen.getByRole('button', { name: '取消' }))
  await user.click(screen.getByRole('button', { name: '继续编辑' }))
  expect(screen.getByRole('textbox', { name: '含义' })).toHaveValue('含义1更新')
  await user.click(screen.getByRole('button', { name: '取消' }))
  await user.click(screen.getByRole('button', { name: '放弃修改' }))
  expect(screen.getByRole('textbox', { name: '含义' })).toHaveValue('含义1')

})

it('保存到全部只提交本次改动和所有生效作用域的版本号', async () => {
  const user = userEvent.setup()
  const row = makeJargon(1, {scope_kind: 'chat', scope_id: 'A', revision: 2})
  const other = makeJargon(2, {scope_kind: 'group', scope_id: 'AB', revision: 5, content: row.content})
  vi.mocked(jargonApi.getJargonVersions).mockResolvedValue([row, other])
  vi.mocked(jargonApi.updateJargon).mockResolvedValue({success: true, message: 'ok', data: row})
  render(<QueryClientProvider client={new QueryClient()}>
    <JargonDetailDialog jargon={row} open onOpenChange={vi.fn()} chatList={chatList} onScopeChange={vi.fn()} onChanged={vi.fn()} />
  </QueryClientProvider>)
  await screen.findByRole('button', {name: '保存到全部'})
  await user.click(screen.getByRole('checkbox', {name: '允许更新'}))
  await user.click(screen.getByRole('button', {name: '保存到全部'}))
  expect(jargonApi.updateJargon).toHaveBeenCalledWith(1, {
    allow_update: false, revision: 2, all_scope_revisions: {1: 2, 2: 5},
  })
})

it('按词选择，每个作用域各占一行，共享组的多个聊天共用一行', async () => {
  const user = userEvent.setup()
  const first = makeJargon(1, {
    scope_kind: 'group', scope_id: 'ab', scope_name: '共享组', meaning: '圈内用法',
    session_ids: ['a', 'b'], chat_names: ['A 群', 'B 群'],
  })
  const second = makeJargon(2, { scope_name: '通用词库', meaning: '通用解释' })
  const third = makeJargon(3, { scope_name: '测试群', meaning: '圈内用法' })
  vi.mocked(jargonApi.getJargonVersions).mockResolvedValue([third, first, second])
  const word = { ...first, scope_ids: [1, 2, 3] }
  const edit = vi.fn()
  const remove = vi.fn()
  render(<QueryClientProvider client={new QueryClient()}><JargonList jargons={[word]} loading={false} total={1} page={1} pageSize={20}
    selectedIds={new Set()} onEdit={edit} onDelete={remove} onToggleSelect={vi.fn()}
    onToggleSelectAll={vi.fn()} onPageChange={vi.fn()} /></QueryClientProvider>)
  const table = within(screen.getByRole('table', { name: '黑话列表' }))
  const toggle = table.getByRole('button', { name: /展开.*3 个作用域/ })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(table.getAllByRole('row')).toHaveLength(2)
  expect(table.queryByText('通用解释')).toBeNull()
  await user.click(toggle)
  await table.findByText('通用解释')
  expect(edit).not.toHaveBeenCalled()
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(table.getAllByRole('row')).toHaveLength(4) // 表头和三个作用域
  expect(table.getAllByText('圈内用法')).toHaveLength(2)
  expect(table.getByRole('button', { name: '查看共享组释义' }).closest('tr')).toHaveTextContent('圈内用法')
  expect(table.getByRole('button', { name: '查看测试群释义' }).closest('tr')).toHaveTextContent('圈内用法')
  expect(table.getAllByRole('checkbox', { name: /选择黑话/ })).toHaveLength(1)
  await user.click(table.getByText('通用解释'))
  expect(edit).toHaveBeenLastCalledWith(second)
  await user.click(table.getByRole('button', { name: '查看测试群释义' }))
  expect(edit).toHaveBeenLastCalledWith(third)
  await user.click(table.getByRole('button', { name: '删除黑话' }))
  expect(remove).toHaveBeenCalledWith(word)
  await user.click(toggle)
  expect(table.getAllByRole('row')).toHaveLength(2)
})
