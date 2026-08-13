/**
 * DeleteTab：用 mock hook 结果锁定来源删除、操作恢复与明细 UI。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import type {
  MemoryDeleteOperationItemPayload,
  MemoryDeleteOperationPayload,
  MemorySourceItemPayload,
} from '@/lib/memory-api'

import { DELETE_OPERATION_ITEM_PAGE_SIZE, DELETE_OPERATION_PAGE_SIZE } from '../../constants'
import type { UseMemoryDeleteResult } from '../../hooks/useMemoryDelete'
import { DeleteTab } from '../DeleteTab'

afterEach(() => {
  cleanup()
})

beforeEach(() => {
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

function makeSource(overrides: Partial<MemorySourceItemPayload> = {}): MemorySourceItemPayload {
  return {
    source: 'chat:alpha',
    paragraph_count: 3,
    relation_count: 2,
    ...overrides,
  }
}

function makeItem(
  overrides: Partial<MemoryDeleteOperationItemPayload> = {},
): MemoryDeleteOperationItemPayload {
  return {
    item_type: 'entity',
    item_hash: 'ent-hash',
    item_key: 'ent-key',
    payload: {
      source: 'src-entity',
      entity: { name: '张三' },
      paragraph_links: ['p1', 'p2'],
    },
    ...overrides,
  }
}

function makeOperation(
  overrides: Partial<MemoryDeleteOperationPayload> = {},
): MemoryDeleteOperationPayload {
  return {
    operation_id: 'op-1',
    mode: 'source',
    status: 'executed',
    reason: '清理测试批次',
    requested_by: 'alice',
    created_at: 1_710_000_000,
    restored_at: null,
    selector: { sources: ['chat:alpha'] },
    summary: {
      counts: { entities: 1, relations: 2, paragraphs: 3, sources: 4 },
    },
    ...overrides,
  }
}

function makeDelete(overrides: Partial<UseMemoryDeleteResult> = {}): UseMemoryDeleteResult {
  return {
    sourceSearch: '',
    setSourceSearch: vi.fn(),
    selectedSources: [],
    setSelectedSources: vi.fn(),
    filteredSources: [],
    openSourceDeletePreview: vi.fn(async () => {}),
    toggleSourceSelection: vi.fn(),
    refreshSources: vi.fn(async () => {}),
    operationSearch: '',
    setOperationSearch: vi.fn(),
    operationModeFilter: 'all',
    setOperationModeFilter: vi.fn(),
    operationStatusFilter: 'all',
    setOperationStatusFilter: vi.fn(),
    filteredDeleteOperations: [],
    deleteOperations: [],
    operationPage: 1,
    setOperationPage: vi.fn(),
    deleteOperationPageCount: 1,
    pagedDeleteOperations: [],
    selectedDeleteOperation: null,
    setSelectedOperationId: vi.fn(),
    restoreDeleteOperation: vi.fn(async () => {}),
    deleteRestoring: false,
    selectedOperationCounts: {},
    selectedOperationDetailLoading: false,
    selectedOperationDetailError: '',
    selectedOperationSources: [],
    selectedOperationItems: [],
    filteredSelectedOperationItems: [],
    selectedOperationItemSearch: '',
    setSelectedOperationItemSearch: vi.fn(),
    selectedOperationItemPage: 1,
    setSelectedOperationItemPage: vi.fn(),
    selectedOperationItemPageCount: 1,
    pagedSelectedOperationItems: [],
    deleteDialogOpen: false,
    closeDeleteDialog: vi.fn(),
    deleteDialogTitle: '',
    deleteDialogDescription: '',
    deletePreview: null,
    deletePreviewError: null,
    deletePreviewLoading: false,
    deleteExecuting: false,
    deleteResult: null,
    executePendingDelete: vi.fn(async () => {}),
    deleteErrorText: '',
    ...overrides,
  }
}

function renderDelete(overrides: Partial<UseMemoryDeleteResult> = {}) {
  const memoryDelete = makeDelete(overrides)
  const view = render(
    <Tabs defaultValue="delete">
      <DeleteTab delete={memoryDelete} />
    </Tabs>,
  )
  return { ...view, memoryDelete }
}

describe('DeleteTab', () => {
  it('空来源与未选操作展示占位，预览删除保持禁用', () => {
    renderDelete()

    expect(screen.getByText('当前没有可删除的来源')).toBeInTheDocument()
    expect(screen.getByText('当前没有可查看的删除操作详情')).toBeInTheDocument()
    expect(screen.getByText('当前筛选条件下没有删除操作')).toBeInTheDocument()
    expect(screen.getByText('当前命中 0 个来源')).toBeInTheDocument()
    expect(screen.getByText('已选择 0 个来源')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '预览删除' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    expect(
      screen.getByText(`第 1 / 1 页，每页显示 ${DELETE_OPERATION_PAGE_SIZE} 条`),
    ).toBeInTheDocument()
  })

  it('来源检索、全选、勾选与预览删除', async () => {
    const user = userEvent.setup()
    const filteredSources = [
      makeSource(),
      makeSource({ source: 'chat:beta', paragraph_count: 0, relation_count: 1 }),
      // 空 source 会进表格，但全选时被 filter(Boolean) 丢掉
      makeSource({ source: '', paragraph_count: undefined, relation_count: undefined }),
    ]
    const { memoryDelete, rerender } = renderDelete({
      sourceSearch: 'chat',
      filteredSources,
    })

    expect(screen.getByText('当前命中 3 个来源')).toBeInTheDocument()
    expect(screen.getByText('chat:alpha')).toBeInTheDocument()
    expect(screen.getByText('chat:beta')).toBeInTheDocument()
    const sourceTable = screen.getByText('chat:alpha').closest('table')
    expect(sourceTable).toHaveTextContent('3')
    expect(sourceTable).toHaveTextContent('2')
    expect(sourceTable).toHaveTextContent('0')

    fireEvent.change(screen.getByPlaceholderText('搜索 source 名称'), {
      target: { value: 'beta' },
    })
    expect(memoryDelete.setSourceSearch).toHaveBeenCalledWith('beta')

    await user.click(screen.getByRole('button', { name: '全选当前结果' }))
    expect(memoryDelete.setSelectedSources).toHaveBeenCalledWith(['chat:alpha', 'chat:beta'])

    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[0])
    expect(memoryDelete.toggleSourceSelection).toHaveBeenCalledWith('chat:alpha', true)

    rerender(
      <Tabs defaultValue="delete">
        <DeleteTab
          delete={makeDelete({
            filteredSources,
            selectedSources: ['chat:alpha'],
            openSourceDeletePreview: memoryDelete.openSourceDeletePreview,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('已选择 1 个来源')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '预览删除' }))
    expect(memoryDelete.openSourceDeletePreview).toHaveBeenCalledOnce()
  })

  it('渲染删除操作列表并选中记录，缺省字段走回退文案', async () => {
    const user = userEvent.setup()
    const operations = [
      makeOperation(),
      makeOperation({
        operation_id: 'op-restored',
        mode: 'mixed',
        status: 'restored',
        reason: '',
        created_at: undefined,
        summary: undefined,
      }),
      makeOperation({
        operation_id: 'op-unknown',
        mode: '',
        status: '',
        reason: null,
      }),
    ]
    const { memoryDelete } = renderDelete({
      deleteOperations: operations,
      filteredDeleteOperations: operations,
      pagedDeleteOperations: operations,
      selectedDeleteOperation: operations[0],
    })

    expect(screen.getByText('当前命中 3 条记录，已加载最近 3 条')).toBeInTheDocument()
    expect(screen.getAllByText('已执行').length).toBeGreaterThan(0)
    expect(screen.getAllByText('来源').length).toBeGreaterThan(0)
    expect(screen.getAllByText('已恢复').length).toBeGreaterThan(0)
    expect(screen.getAllByText('混合').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未填写原因').length).toBeGreaterThan(1)
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未知时间').length).toBeGreaterThan(0)
    expect(screen.getAllByText('清理测试批次').length).toBeGreaterThan(0)
    expect(screen.getAllByText('实体 1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('关系 2').length).toBeGreaterThan(0)
    expect(screen.getAllByText('段落 3').length).toBeGreaterThan(0)
    expect(screen.getAllByText('来源 4').length).toBeGreaterThan(0)

    await user.click(screen.getByText('op-restored'))
    expect(memoryDelete.setSelectedOperationId).toHaveBeenCalledWith('op-restored')
  })

  it('操作搜索、模式/状态筛选与分页', async () => {
    const user = userEvent.setup()
    const operations = [makeOperation(), makeOperation({ operation_id: 'op-2', mode: 'entity' })]
    const { memoryDelete } = renderDelete({
      operationSearch: 'alice',
      operationModeFilter: 'all',
      operationStatusFilter: 'all',
      deleteOperations: operations,
      filteredDeleteOperations: operations,
      pagedDeleteOperations: operations,
      operationPage: 2,
      deleteOperationPageCount: 3,
    })

    fireEvent.change(screen.getByPlaceholderText('搜索 operation / reason / requested_by / source'), {
      target: { value: 'op-2' },
    })
    expect(memoryDelete.setOperationSearch).toHaveBeenCalledWith('op-2')

    const comboboxes = screen.getAllByRole('combobox')
    await user.click(comboboxes[0])
    await user.click(screen.getByRole('option', { name: '来源删除' }))
    expect(memoryDelete.setOperationModeFilter).toHaveBeenCalledWith('source')
    await user.click(comboboxes[1])
    await user.click(screen.getByRole('option', { name: '已执行' }))
    expect(memoryDelete.setOperationStatusFilter).toHaveBeenCalledWith('executed')

    await user.click(screen.getByRole('button', { name: '上一页' }))
    const prev = memoryDelete.setOperationPage.mock.calls[0][0] as (current: number) => number
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    const next = memoryDelete.setOperationPage.mock.calls[1][0] as (current: number) => number
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)
  })

  it('已执行操作详情可恢复，并渲染来源、选择器与影响对象', async () => {
    const user = userEvent.setup()
    const operation = makeOperation()
    const items = [
      makeItem(),
      makeItem({
        item_type: 'relation',
        item_hash: 'rel-hash',
        item_key: 'rel-hash',
        payload: {
          relation: { subject: '张三', predicate: '住在', object: '杭州', confidence: 0.9 },
          paragraph_hashes: ['p1'],
        },
      }),
      makeItem({
        item_type: 'paragraph',
        item_hash: 'para-hash',
        item_key: 'para-key',
        payload: { paragraph: { source: 'src-para', content: '段落正文预览' } },
      }),
      makeItem({
        item_type: 'unknown',
        item_hash: 'unk-hash',
        item_key: undefined,
        payload: {},
      }),
    ]
    const { memoryDelete } = renderDelete({
      selectedDeleteOperation: operation,
      selectedOperationCounts: { entities: 1, relations: 2, paragraphs: 3, sources: 4 },
      selectedOperationSources: ['chat:alpha', 'chat:beta'],
      selectedOperationItems: items,
      filteredSelectedOperationItems: items,
      pagedSelectedOperationItems: items,
    })

    expect(screen.queryByText('未填写删除原因')).not.toBeInTheDocument()
    expect(screen.getAllByText('清理测试批次').length).toBeGreaterThan(0)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('关联来源')).toBeInTheDocument()
    expect(screen.getByText('chat:beta')).toBeInTheDocument()
    expect(document.querySelector('pre')).toHaveTextContent('"sources"')
    expect(screen.getByText(`命中 ${items.length} / ${items.length} 项`)).toBeInTheDocument()

    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('关联段落 2 个')).toBeInTheDocument()
    expect(screen.getByText('ent-key')).toBeInTheDocument()
    expect(screen.getByText('src-entity')).toBeInTheDocument()
    expect(screen.getByText('张三 -> 住在 -> 杭州')).toBeInTheDocument()
    expect(screen.getByText('证据段落 1 个，置信度 0.90')).toBeInTheDocument()
    expect(screen.queryByText('rel-hash', { selector: 'span' })).not.toBeInTheDocument()
    expect(screen.getAllByText('src-para').length).toBeGreaterThan(0)
    expect(screen.getByText('段落正文预览')).toBeInTheDocument()
    expect(screen.getByText('para-key')).toBeInTheDocument()
    expect(screen.getAllByText('unk-hash').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: '恢复这次删除' }))
    expect(memoryDelete.restoreDeleteOperation).toHaveBeenCalledWith('op-1')
  })

  it('已恢复或恢复中时禁用恢复按钮', async () => {
    const user = userEvent.setup()
    const restored = makeOperation({ status: 'restored', restored_at: 1_710_000_100, requested_by: '' })
    const { memoryDelete, rerender } = renderDelete({
      selectedDeleteOperation: restored,
    })

    expect(screen.getByRole('button', { name: '已恢复' })).toBeDisabled()
    expect(screen.getByText('-')).toBeInTheDocument()
    expect(screen.queryByText('未填写删除原因')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '已恢复' }))
    expect(memoryDelete.restoreDeleteOperation).not.toHaveBeenCalled()

    rerender(
      <Tabs defaultValue="delete">
        <DeleteTab
          delete={makeDelete({
            selectedDeleteOperation: makeOperation({ reason: null }),
            deleteRestoring: true,
            restoreDeleteOperation: memoryDelete.restoreDeleteOperation,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('未填写删除原因')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '恢复这次删除' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '恢复这次删除' }))
    expect(memoryDelete.restoreDeleteOperation).not.toHaveBeenCalled()
  })

  it('详情加载中、错误与无明细占位', () => {
    renderDelete({
      selectedDeleteOperation: makeOperation({ selector: undefined }),
      selectedOperationDetailLoading: true,
      selectedOperationDetailError: '加载删除明细失败',
      selectedOperationItems: [],
      filteredSelectedOperationItems: [],
      pagedSelectedOperationItems: [],
    })

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByText('加载删除明细失败')).toBeInTheDocument()
    expect(screen.getByText('当前操作没有记录明细项')).toBeInTheDocument()
    expect(screen.queryByText('关联来源')).not.toBeInTheDocument()
    expect(document.querySelector('pre')).toHaveTextContent('{}')
  })

  it('筛选后无明细与对象分页、搜索', async () => {
    const user = userEvent.setup()
    const items = [makeItem()]
    const { memoryDelete } = renderDelete({
      selectedDeleteOperation: makeOperation(),
      selectedOperationItems: items,
      filteredSelectedOperationItems: [],
      pagedSelectedOperationItems: [],
      selectedOperationItemSearch: 'hash',
      selectedOperationItemPage: 2,
      selectedOperationItemPageCount: 3,
    })

    expect(screen.getByText('当前筛选条件下没有明细项')).toBeInTheDocument()
    expect(screen.getByText('命中 0 / 1 项')).toBeInTheDocument()
    expect(screen.getByText(`每页 ${DELETE_OPERATION_ITEM_PAGE_SIZE} 项`)).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索对象类型 / 哈希 / 对象键 / 来源'), {
      target: { value: '张三' },
    })
    expect(memoryDelete.setSelectedOperationItemSearch).toHaveBeenCalledWith('张三')

    const prevButtons = screen.getAllByRole('button', { name: '上一页' })
    const nextButtons = screen.getAllByRole('button', { name: '下一页' })
    await user.click(prevButtons[1])
    const prev = memoryDelete.setSelectedOperationItemPage.mock.calls[0][0] as (
      current: number,
    ) => number
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(nextButtons[1])
    const next = memoryDelete.setSelectedOperationItemPage.mock.calls[1][0] as (
      current: number,
    ) => number
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)
  })
})
