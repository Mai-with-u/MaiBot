/**
 * DeleteTab：用 mock hook 结果锁定来源删除、操作恢复与明细 UI。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

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

/** makeDelete 里 setter 实际是 vi.fn()，但对外类型是 React Dispatch */
function pageUpdater(
  setter: UseMemoryDeleteResult['setOperationPage'],
  callIndex: number,
): (current: number) => number {
  return (setter as unknown as Mock).mock.calls[callIndex][0] as (current: number) => number
}

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
    // 后端来源列表以 count 统计段落数，并回填 paragraph_count 供前端展示
    count: 3,
    paragraph_count: 3,
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
    sourceKindFilter: 'chat_summary',
    setSourceKindFilter: vi.fn(),
    sourceSearch: '',
    setSourceSearch: vi.fn(),
    selectedSources: [],
    setSelectedSources: vi.fn(),
    filteredSources: [],
    openDeletePreview: vi.fn(async () => {}),
    openSourceDeletePreview: vi.fn(async () => {}),
    toggleSourceSelection: vi.fn(),
    refreshSources: vi.fn(async () => {}),
    sourceNameBySource: {},
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
    selectedOperationId: '',
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
  it('空来源展示占位，预览删除保持禁用，操作详情以弹窗呈现', () => {
    renderDelete()

    expect(screen.getByText('当前没有可删除的来源')).toBeInTheDocument()
    // 操作详情改为弹出卡片，页面不再渲染固定的详情占位区
    expect(screen.queryByText('当前没有可查看的删除操作详情')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '删除操作详情' })).not.toBeInTheDocument()
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

  it('来源类别切换、深链接限定、全选、勾选与带原因的预览删除', async () => {
    const user = userEvent.setup()
    const filteredSources = [
      makeSource(),
      makeSource({ source: 'chat:beta', count: 0, paragraph_count: 0 }),
      // 空 source 会进表格，但全选时被 filter(Boolean) 丢掉
      makeSource({ source: '', count: 0, paragraph_count: 0 }),
    ]
    const { memoryDelete, rerender } = renderDelete({
      sourceSearch: 'chat:stream:1',
      filteredSources,
    })

    expect(screen.getByText('当前命中 3 个来源')).toBeInTheDocument()
    expect(screen.getByText('chat:alpha')).toBeInTheDocument()
    expect(screen.getByText('chat:beta')).toBeInTheDocument()
    // 段落数按行校验，避免表格内数字混在一起时断言失效
    expect(screen.getByText('段落数')).toBeInTheDocument()
    expect(screen.getByText('最后更新')).toBeInTheDocument()
    expect(screen.getByText('chat:alpha').closest('tr')).toHaveTextContent('3')
    expect(screen.getByText('chat:beta').closest('tr')).toHaveTextContent('0')

    // 类别用标签页切换，用户不需要猜该输入什么；不提供「全部」入口，默认落在聊天摘要
    expect(screen.queryByRole('tab', { name: '全部' })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '聊天摘要' })).toHaveAttribute('data-state', 'active')
    // 聊天流、聊天记录没有独立标签页，统一收在「其他」里
    expect(screen.queryByRole('tab', { name: '聊天流' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: '聊天记录' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '人物事实' }))
    expect(memoryDelete.setSourceKindFilter).toHaveBeenCalledWith('person_fact')
    await user.click(screen.getByRole('tab', { name: '其他' }))
    expect(memoryDelete.setSourceKindFilter).toHaveBeenCalledWith('other')

    // 深链接带入的来源限定以可清除的芯片呈现
    expect(screen.getByText('chat:stream:1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '清除限定' }))
    expect(memoryDelete.setSourceSearch).toHaveBeenCalledWith('')

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
    // 删除原因已移到预览对话框填写，这里只负责发起预览
    expect(
      screen.queryByPlaceholderText('例如：清理测试导入批次，会记录在删除历史里'),
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '预览删除' }))
    expect(memoryDelete.openSourceDeletePreview).toHaveBeenCalledWith()
  })

  it('来源优先展示聊天流名称与来源类型，裸 source 降级为副标题', () => {
    // 「其他」下类别徽章保留，借此断言行内类型文案
    renderDelete({
      sourceKindFilter: 'other',
      filteredSources: [
        makeSource({
          source: 'chat_summary:s1',
          count: 12,
          paragraph_count: 12,
          source_kind: 'chat_summary',
          chat_id: 's1',
          chat_name: '摸鱼群',
        }),
        makeSource({ source: 'import:batch-1', count: 4, paragraph_count: 4 }),
      ],
    })

    expect(screen.getByText('摸鱼群')).toBeInTheDocument()
    // 类型徽章限定在来源行内断言，避免与筛选标签页的同名文案混淆
    expect(screen.getByText('摸鱼群').closest('tr')).toHaveTextContent('聊天摘要')
    // 原始 source 仍保留，便于核对具体来源标记
    expect(screen.getByText('chat_summary:s1')).toBeInTheDocument()
    // 解析不出聊天流时直接展示原始 source，不额外渲染副标题
    expect(screen.getByText('import:batch-1')).toBeInTheDocument()
  })

  it('人物事实来源展示可读姓名，悬停可见 ID 等详情', async () => {
    const user = userEvent.setup()
    const personSource = 'person_fact:e1549e1d55b88bcd783dff0d3fe9f4fa'
    renderDelete({
      filteredSources: [
        makeSource({
          source: personSource,
          count: 6,
          paragraph_count: 6,
          source_kind: 'person_fact',
          person_id: 'e1549e1d55b88bcd783dff0d3fe9f4fa',
          person_name: '张三',
        }),
      ],
    })

    // 主标题是可读姓名，不可读的 person_id 降级为副标题
    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText(personSource)).toBeInTheDocument()

    await user.hover(screen.getByText('张三'))
    const tooltip = await screen.findByRole('tooltip')
    expect(tooltip).toHaveTextContent('人物 ID：e1549e1d55b88bcd783dff0d3fe9f4fa')
    expect(tooltip).toHaveTextContent(`原始来源：${personSource}`)
    expect(tooltip).toHaveTextContent('段落数：6')
  })

  it('切到具体类别后行尾不再重复展示类别徽章，其他下保留', () => {
    const sources = [
      makeSource({
        source: 'person_fact:abc',
        count: 1,
        paragraph_count: 1,
        source_kind: 'person_fact',
        person_name: '张三',
      }),
    ]

    const { rerender } = renderDelete({ sourceKindFilter: 'person_fact', filteredSources: sources })
    expect(screen.getByText('张三').closest('tr')).not.toHaveTextContent('人物事实')

    // 「其他」下类别不固定，仍保留徽章以便区分来源
    rerender(
      <Tabs defaultValue="delete">
        <DeleteTab
          delete={makeDelete({ sourceKindFilter: 'other', filteredSources: sources })}
        />
      </Tabs>,
    )
    expect(screen.getByText('张三').closest('tr')).toHaveTextContent('人物事实')
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
    const prev = pageUpdater(memoryDelete.setOperationPage, 0)
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    const next = pageUpdater(memoryDelete.setOperationPage, 1)
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
      // 传入 selectedOperationId 使 DeleteTab 初始即打开详情弹窗
      selectedOperationId: operation.operation_id,
      selectedOperationCounts: { entities: 1, relations: 2, paragraphs: 3, sources: 4 },
      selectedOperationSources: ['chat:alpha', 'chat:beta'],
      sourceNameBySource: { 'chat:beta': '摸鱼群' },
      selectedOperationItems: items,
      filteredSelectedOperationItems: items,
      pagedSelectedOperationItems: items,
    })

    expect(screen.queryByText('未填写删除原因')).not.toBeInTheDocument()
    expect(screen.getAllByText('清理测试批次').length).toBeGreaterThan(0)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('关联来源')).toBeInTheDocument()
    // 能解析出聊天流时显示实际名称，原始 source 作为副标题保留
    expect(screen.getByText('摸鱼群')).toBeInTheDocument()
    expect(screen.getByText('chat:beta')).toBeInTheDocument()
    // 删除范围用可读条目替代直接读原始 JSON
    expect(screen.getByText('来源 1 个：chat:alpha')).toBeInTheDocument()
    expect(screen.getByText('查看原始选择器')).toBeInTheDocument()
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
      selectedOperationId: restored.operation_id,
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
            selectedOperationId: 'op-1',
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
      selectedOperationId: 'op-1',
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

  it('筛选后无明细与对象分页、搜索', () => {
    const items = [makeItem()]
    const { memoryDelete } = renderDelete({
      selectedDeleteOperation: makeOperation(),
      selectedOperationId: 'op-1',
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

    // 详情弹窗的 portal 在主树之外，用 within 精确命中弹窗内的对象分页按钮
    const detailDialog = screen.getByRole('dialog', { name: '删除操作详情' })
    fireEvent.click(within(detailDialog).getByRole('button', { name: '上一页' }))
    const prev = pageUpdater(memoryDelete.setSelectedOperationItemPage, 0)
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    fireEvent.click(within(detailDialog).getByRole('button', { name: '下一页' }))
    const next = pageUpdater(memoryDelete.setSelectedOperationItemPage, 1)
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)
  })
})
