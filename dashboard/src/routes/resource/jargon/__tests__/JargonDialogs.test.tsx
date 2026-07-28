import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  BatchDeleteConfirmDialog,
  DeleteConfirmDialog,
  JargonCreateDialog,
  JargonDetailDialog,
  JargonExportDialog,
  JargonImportDialog,
} from '../JargonDialogs'
import * as jargonApi from '@/lib/jargon-api'

import type { Jargon, JargonChatInfo, JargonExportItem } from '@/types/jargon'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// toast 断言用稳定引用的 spy（vi.mock 工厂被提升，必须用 vi.hoisted）
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/jargon-api', () => ({
  createJargon: vi.fn(),
  importJargons: vi.fn(),
  updateJargon: vi.fn(),
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
    created_by: 'AI',
    created_timestamp: '2026-01-01T00:00:00Z',
    updated_timestamp: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

function makeExportItem(content: string): JargonExportItem {
  return {
    content,
    meaning: `${content}的意思`,
    count: 1,
    is_jargon: true,
    is_complete: false,
    is_global: false,
    created_by: 'AI',
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
    expect(screen.queryByText('黑话详情')).not.toBeInTheDocument()
  })

  it('渲染详情：表单初值与状态徽章', () => {
    renderDetail(
      makeJargon(7, { is_global: true, is_complete: true, is_legacy_empty_meaning: true })
    )
    expect(screen.getByText('黑话详情')).toBeInTheDocument()
    expect(screen.getByLabelText('内容')).toHaveValue('黑话7')
    expect(screen.getByLabelText('含义')).toHaveValue('含义7')
    // 状态徽章区：是黑话 / 旧数据 / AI / 全局 / 推断完成
    // （「是黑话」同时出现在徽章与黑话状态 Select 的隐藏原生 option 中）
    expect(screen.getAllByText('是黑话').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('旧数据')).toBeInTheDocument()
    expect(screen.getByText('AI')).toBeInTheDocument()
    expect(screen.getByText('全局')).toBeInTheDocument()
    expect(screen.getByText('推断完成')).toBeInTheDocument()
  })

  it('内容清空后保存触发验证失败，不调用更新接口', async () => {
    const user = userEvent.setup()
    renderDetail(makeJargon(1))
    await user.clear(screen.getByLabelText('内容'))
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '验证失败', description: '黑话内容不能为空' })
    )
    expect(jargonApi.updateJargon).not.toHaveBeenCalled()
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
    const updated = makeJargon(1, { content: '新内容' })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: updated,
    })
    const { onChanged } = renderDetail(makeJargon(1))
    const input = screen.getByLabelText('内容')
    await user.clear(input)
    await user.type(input, '新内容')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ content: '新内容', session_ids: ['s1'], is_jargon: true })
      )
    )
    expect(onChanged).toHaveBeenCalledWith(updated)
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '保存成功' }))
  })

  it('保存失败：展示接口错误信息', async () => {
    const user = userEvent.setup()
    vi.mocked(jargonApi.updateJargon).mockRejectedValue(new Error('后端错误'))
    const { onChanged } = renderDetail(makeJargon(1))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '保存失败', description: '后端错误' })
      )
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('AI 记录有含义时可固定含义：以 MANUAL + is_jargon 调用更新', async () => {
    const user = userEvent.setup()
    const pinned = makeJargon(1, { created_by: 'MANUAL' })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: pinned,
    })
    const { onChanged } = renderDetail(makeJargon(1, { meaning: '老含义' }))
    await user.click(screen.getByRole('button', { name: '固定含义' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ meaning: '老含义', created_by: 'MANUAL', is_jargon: true })
      )
    )
    expect(onChanged).toHaveBeenCalledWith(pinned)
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已固定含义' }))
  })

  it('AI 记录无含义时固定含义按钮禁用', () => {
    renderDetail(makeJargon(1, { meaning: null }))
    expect(screen.getByRole('button', { name: '固定含义' })).toBeDisabled()
  })

  it('MANUAL 记录显示取消固定：以 created_by=AI 调用更新', async () => {
    const user = userEvent.setup()
    const unpinned = makeJargon(1, { created_by: 'AI' })
    vi.mocked(jargonApi.updateJargon).mockResolvedValue({
      success: true,
      message: 'ok',
      data: unpinned,
    })
    renderDetail(makeJargon(1, { created_by: 'MANUAL' }))
    expect(screen.queryByRole('button', { name: '固定含义' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消固定' }))
    await waitFor(() =>
      expect(jargonApi.updateJargon).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ created_by: 'AI' })
      )
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已取消固定' }))
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
})

describe('JargonImportDialog', () => {
  function renderImport() {
    const onSuccess = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <JargonImportDialog
        open
        onOpenChange={onOpenChange}
        chatList={chatList}
        onSuccess={onSuccess}
      />
    )
    return { onSuccess, onOpenChange }
  }

  function makeJsonFile(name: string, payload: unknown) {
    return new File([typeof payload === 'string' ? payload : JSON.stringify(payload)], name, {
      type: 'application/json',
    })
  }

  it('未选文件直接导入时提示请选择文件', async () => {
    const user = userEvent.setup()
    renderImport()
    await user.click(screen.getByRole('button', { name: '导入' }))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '请选择文件' }))
    expect(jargonApi.importJargons).not.toHaveBeenCalled()
  })

  it('上传数组 JSON 后显示条数；未选聊天先提示，选中后按 skip 策略导入', async () => {
    const user = userEvent.setup()
    const items = [makeExportItem('词1'), makeExportItem('词2')]
    vi.mocked(jargonApi.importJargons).mockResolvedValue({
      success: true,
      message: 'ok',
      imported_count: 2,
      skipped_count: 1,
      failed_count: 0,
    })
    const { onSuccess, onOpenChange } = renderImport()
    await user.upload(screen.getByLabelText('JSON 文件'), makeJsonFile('jargons.json', items))
    expect(await screen.findByText('jargons.json，共 2 条黑话')).toBeInTheDocument()

    // 未选目标聊天时被拦截
    await user.click(screen.getByRole('button', { name: '导入' }))
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '请选择聊天' }))
    expect(jargonApi.importJargons).not.toHaveBeenCalled()

    await user.click(screen.getByText('ms-add-s1'))
    await user.click(screen.getByRole('button', { name: '导入' }))
    await waitFor(() =>
      expect(jargonApi.importJargons).toHaveBeenCalledWith({
        target_session_ids: ['s1'],
        jargons: items,
        conflict_strategy: 'skip',
      })
    )
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '导入完成',
        description: '成功 2 个，跳过 1 个，失败 0 个',
      })
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('支持 maibot.jargon.export 包裹格式（jargons 字段）', async () => {
    const user = userEvent.setup()
    renderImport()
    const payload = { type: 'maibot.jargon.export', jargons: [makeExportItem('词1')] }
    await user.upload(screen.getByLabelText('JSON 文件'), makeJsonFile('export.json', payload))
    expect(await screen.findByText('export.json，共 1 条黑话')).toBeInTheDocument()
  })

  it('JSON 中没有黑话时提示读取失败', async () => {
    const user = userEvent.setup()
    renderImport()
    await user.upload(screen.getByLabelText('JSON 文件'), makeJsonFile('empty.json', {}))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '读取失败', description: 'JSON 中没有可导入的黑话' })
      )
    )
  })

  it('非法 JSON 文件提示读取失败', async () => {
    const user = userEvent.setup()
    renderImport()
    await user.upload(screen.getByLabelText('JSON 文件'), makeJsonFile('bad.json', 'not-json'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '读取失败' }))
    )
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
    expect(screen.getByRole('alertdialog')).toHaveTextContent('确定要删除黑话 "词A" 吗')
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('BatchDeleteConfirmDialog 显示数量，确认回调 onConfirm', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <BatchDeleteConfirmDialog open onOpenChange={vi.fn()} onConfirm={onConfirm} count={3} />
    )
    expect(screen.getByRole('alertdialog')).toHaveTextContent('您即将删除 3 个黑话')
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})
