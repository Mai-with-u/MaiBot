/**
 * ImportTab：用 mock 的 useImportForm / useImportQueue 结果锁定导入方式切换、提交、校验与队列 UI。
 */
import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import { createMemoryFact } from '@/lib/memory-api'
import type {
  MemoryImportChatTargetPayload,
  MemoryImportChunkPayload,
  MemoryImportFilePayload,
  MemoryImportTaskPayload,
} from '@/lib/memory-api'

import type { UseImportFormResult } from '../../hooks/useImportForm'
import type { UseImportQueueResult } from '../../hooks/useImportQueue'
import { ImportTab } from '../ImportTab'

vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    createMemoryFact: vi.fn(),
  }
})

const createFactMock = vi.mocked(createMemoryFact)

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  createFactMock.mockReset()
  createFactMock.mockResolvedValue({ success: true, claim: { claim_id: 'fact-new' }, refresh_queued: true })
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

function checkboxNear(text: string) {
  const node = screen.getByText(text)
  const root = node.closest('div, label') ?? node.parentElement
  if (!root) {
    throw new Error('找不到附近的复选框')
  }
  return within(root as HTMLElement).getByRole('checkbox')
}

/** 这些 Label 没有 htmlFor，需要从相邻容器里找控件 */
function controlNearLabel(label: string) {
  const labelEl = screen.getByText(label, { selector: 'label' })
  let current: HTMLElement | null = labelEl.parentElement
  while (current) {
    const control = current.querySelector('input, textarea')
    if (control) {
      return control as HTMLInputElement | HTMLTextAreaElement
    }
    current = current.parentElement
  }
  throw new Error(`找不到「${label}」对应的输入框`)
}

function makeChat(overrides: Partial<MemoryImportChatTargetPayload> = {}): MemoryImportChatTargetPayload {
  return {
    chat_id: 'chat-1',
    chat_name: '测试群',
    platform: 'qq',
    group_id: '10001',
    user_id: null,
    is_group: true,
    account_id: 'bot-1',
    scope: 'group',
    ...overrides,
  }
}

function makeImportTask(overrides: Partial<MemoryImportTaskPayload> = {}): MemoryImportTaskPayload {
  return {
    task_id: 'task-run-1',
    source: 'webui',
    status: 'running',
    current_step: 'extracting',
    total_chunks: 120,
    done_chunks: 36,
    failed_chunks: 2,
    cancelled_chunks: 1,
    progress: 30,
    error: '',
    file_count: 2,
    created_at: 1_710_000_000,
    started_at: 1_710_000_001,
    finished_at: null,
    updated_at: 1_710_000_100,
    task_kind: 'paste',
    params: {},
    files: [],
    ...overrides,
  }
}

function makeImportFile(overrides: Partial<MemoryImportFilePayload> = {}): MemoryImportFilePayload {
  return {
    file_id: 'file-alpha',
    name: 'alpha.txt',
    source_kind: 'paste',
    input_mode: 'text',
    status: 'failed',
    current_step: 'extracting',
    detected_strategy_type: 'auto',
    total_chunks: 80,
    done_chunks: 30,
    failed_chunks: 4,
    cancelled_chunks: 2,
    progress: 37.5,
    error: '文件抽取失败',
    created_at: 1_710_000_000,
    updated_at: 1_710_000_100,
    ...overrides,
  }
}

function makeChunk(overrides: Partial<MemoryImportChunkPayload> = {}): MemoryImportChunkPayload {
  return {
    chunk_id: 'chunk-1',
    index: 3,
    chunk_type: 'narrative',
    status: 'failed',
    step: 'writing',
    failed_at: 'writing',
    retryable: true,
    error: '分块写入失败',
    progress: 12,
    content_preview: '分块预览文本',
    updated_at: 1_710_000_100,
    ...overrides,
  }
}

function makeForm(overrides: Partial<UseImportFormResult> = {}): UseImportFormResult {
  return {
    importCreateMode: 'upload',
    setImportCreateMode: vi.fn(),
    unifiedImportMode: 'file',
    setUnifiedImportMode: vi.fn(),
    importSettings: {
      max_file_concurrency: 8,
      max_chunk_concurrency: 16,
      default_narrative_window_size: 1600,
      default_narrative_overlap: 400,
      default_factual_target_size: 1200,
      max_chunk_chars: 3200,
    },
    importChatTargets: [],
    importCommonFileConcurrency: '2',
    setImportCommonFileConcurrency: vi.fn(),
    importCommonChunkConcurrency: '4',
    setImportCommonChunkConcurrency: vi.fn(),
    importCommonNarrativeWindowSize: '1600',
    setImportCommonNarrativeWindowSize: vi.fn(),
    importCommonNarrativeOverlap: '400',
    setImportCommonNarrativeOverlap: vi.fn(),
    importCommonFactualTargetSize: '1200',
    setImportCommonFactualTargetSize: vi.fn(),
    importCommonLlmEnabled: true,
    setImportCommonLlmEnabled: vi.fn(),
    importContentCategory: 'narrative',
    setImportContentCategory: vi.fn(),
    importContentCategoryMissing: false,
    importCommonDedupePolicy: 'content_hash',
    setImportCommonDedupePolicy: vi.fn(),
    importCommonChatId: '',
    setImportCommonChatId: vi.fn(),
    importCommonChatReferenceTime: '',
    setImportCommonChatReferenceTime: vi.fn(),
    importCommonForce: false,
    setImportCommonForce: vi.fn(),
    importCommonClearManifest: false,
    setImportCommonClearManifest: vi.fn(),
    uploadInputMode: 'text',
    setUploadInputMode: vi.fn(),
    uploadFiles: [],
    setUploadFiles: vi.fn(),
    pasteName: '',
    setPasteName: vi.fn(),
    pasteMode: 'text',
    setPasteMode: vi.fn(),
    pasteContent: '',
    setPasteContent: vi.fn(),
    rawInputMode: 'text',
    setRawInputMode: vi.fn(),
    rawRelativePath: '',
    setRawRelativePath: vi.fn(),
    rawGlob: '**/*.{txt,md,json}',
    setRawGlob: vi.fn(),
    rawRecursive: true,
    setRawRecursive: vi.fn(),
    openieRelativePath: '',
    setOpenieRelativePath: vi.fn(),
    openieIncludeAllJson: false,
    setOpenieIncludeAllJson: vi.fn(),
    convertRelativePath: '',
    setConvertRelativePath: vi.fn(),
    convertTargetRelativePath: '',
    setConvertTargetRelativePath: vi.fn(),
    convertDimension: '1024',
    setConvertDimension: vi.fn(),
    convertBatchSize: '32',
    setConvertBatchSize: vi.fn(),
    submitImportByMode: vi.fn(async () => {}),
    creatingImport: false,
    buildCommonImportPayload: vi.fn(() => ({})),
    checkImportPath: vi.fn(async () => '解析到 /data/raw/exports（目录，已存在）'),
    ...overrides,
  }
}

function makeQueue(overrides: Partial<UseImportQueueResult> = {}): UseImportQueueResult {
  return {
    refreshImportQueue: vi.fn(async () => {}),
    runningImportTasks: [],
    queuedImportTasks: [],
    recentImportTasks: [],
    selectedImportTaskId: '',
    selectImportTask: vi.fn(async () => {}),
    importPollInterval: 1000,
    importErrorText: '',
    cancelSelectedImportTask: vi.fn(async () => {}),
    retrySelectedImportTask: vi.fn(async () => {}),
    selectedImportTaskLoading: false,
    selectedImportTaskResolved: null,
    selectedImportRetrySummary: null,
    selectedImportTaskErrorText: '',
    selectedImportFiles: [],
    selectedImportFileId: '',
    selectImportFile: vi.fn(async () => {}),
    importChunkTotal: 0,
    importChunkOffset: 0,
    moveImportChunkPage: vi.fn(async () => {}),
    canImportChunkPrev: false,
    canImportChunkNext: false,
    importChunksLoading: false,
    selectedImportChunks: [],
    afterCreated: vi.fn(async () => {}),
    invalidate: vi.fn(),
    ...overrides,
  }
}

function renderImport(options: {
  form?: Partial<UseImportFormResult>
  queue?: Partial<UseImportQueueResult>
} = {}) {
  const form = makeForm(options.form)
  const queue = makeQueue(options.queue)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Tabs defaultValue="import">
        <ImportTab form={form} queue={queue} />
      </Tabs>
    </QueryClientProvider>,
  )
  // ImportTab 依赖 useQueryClient，rerender 时也要包上 Provider。
  const rerender = (ui: ReactElement) =>
    view.rerender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
  return { ...view, form, queue, rerender }
}

const manyChats: MemoryImportChatTargetPayload[] = [
  makeChat({ chat_id: 'g-1', chat_name: '测试群', platform: 'qq', group_id: '10001', user_id: null }),
  makeChat({
    chat_id: 'u-qq',
    chat_name: '小明私聊',
    platform: 'qq',
    group_id: null,
    user_id: '20001',
    is_group: false,
    scope: 'private',
  }),
  makeChat({
    chat_id: 'u-wx',
    chat_name: '微信好友',
    platform: 'wechat',
    group_id: null,
    user_id: 'wx-88',
    is_group: false,
    account_id: null,
  }),
  makeChat({
    chat_id: 'u-wx2',
    chat_name: '企微好友',
    platform: 'wx',
    group_id: null,
    user_id: 'wx-99',
    is_group: false,
  }),
  makeChat({
    chat_id: 'u-tg',
    chat_name: 'Telegram 私聊',
    platform: 'telegram',
    group_id: null,
    user_id: 'tg-1',
    is_group: false,
  }),
  makeChat({
    chat_id: 'u-empty',
    chat_name: '无名会话',
    platform: '',
    group_id: null,
    user_id: '',
    is_group: false,
    account_id: null,
  }),
  makeChat({ chat_id: 'g-2', chat_name: '备用群 A', group_id: '20002' }),
  makeChat({ chat_id: 'g-3', chat_name: '备用群 B', group_id: '20003' }),
  makeChat({ chat_id: 'g-4', chat_name: '备用群 C', group_id: '20004' }),
  makeChat({ chat_id: 'g-5', chat_name: '备用群 D', group_id: '20005' }),
]

describe('ImportTab', () => {
  it('未选资料类别时禁用提交并展示校验文案', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: {
        importContentCategory: '',
        importContentCategoryMissing: true,
      },
    })

    const submit = screen.getByRole('button', { name: '创建导入任务' })
    expect(submit).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('请选择资料类别')
    expect(screen.getByRole('combobox', { name: '资料类别' })).toHaveAttribute('aria-invalid', 'true')

    await user.click(screen.getByRole('combobox', { name: '资料类别' }))
    await user.click(screen.getByRole('option', { name: '事实资料' }))
    expect(form.setImportContentCategory).toHaveBeenCalledWith('factual')

    await user.click(screen.getByRole('combobox', { name: '资料类别' }))
    await user.click(screen.getByRole('option', { name: '语录与短句' }))
    expect(form.setImportContentCategory).toHaveBeenCalledWith('quote')

    await user.click(screen.getByRole('combobox', { name: '资料类别' }))
    await user.click(screen.getByRole('option', { name: '聊天记录' }))
    expect(form.setImportContentCategory).toHaveBeenCalledWith('chat_log')
  })

  it('资料类别与输入模式的选项带悬停说明，触发框悬停显示当前选择说明', async () => {
    const user = userEvent.setup()
    const { rerender } = renderImport({ form: { importContentCategory: 'factual' } })

    await user.click(screen.getByRole('combobox', { name: '资料类别' }))
    // SelectItem 会拦截指针事件，说明挂在选项自身 title 上，悬停可见
    expect(screen.getByRole('option', { name: '叙事资料' })).toHaveAttribute(
      'title',
      expect.stringContaining('适合小说、剧情、长对话等连续叙事'),
    )
    expect(screen.getByRole('option', { name: '聊天记录' })).toHaveAttribute(
      'title',
      expect.stringContaining('按消息边界切块'),
    )

    await user.keyboard('{Escape}')

    // 触发框悬停展示当前所选类别的说明
    await user.hover(screen.getByRole('combobox', { name: '资料类别' }))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('适合设定集、术语表、说明文档')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({ unifiedImportMode: 'folder', rawInputMode: 'json' })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    await user.click(screen.getByRole('combobox', { name: 'raw-input-mode' }))
    expect(screen.getByRole('option', { name: '结构化 JSON' })).toHaveAttribute(
      'title',
      expect.stringContaining('按 JSON 结构解析，保留字段层级与键名'),
    )
  })

  it('提交创建导入任务，创建中时按钮进入 loading', async () => {
    const user = userEvent.setup()
    const { form, rerender } = renderImport()

    await user.click(screen.getByRole('button', { name: '创建导入任务' }))
    expect(form.submitImportByMode).toHaveBeenCalledOnce()

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm({ creatingImport: true })} queue={makeQueue()} />
      </Tabs>,
    )
    expect(screen.getByRole('button', { name: '创建导入任务' })).toBeDisabled()
  })

  it('切换资料导入、文本、文件、文件夹、LPMM OpenIE、LPMM 转换并编辑各模式字段', async () => {
    const user = userEvent.setup()
    const { form, rerender } = renderImport({
      form: { pasteName: '草稿', pasteContent: '旧内容' },
    })

    await user.click(screen.getByRole('tab', { name: '文本' }))
    expect(form.setUnifiedImportMode).toHaveBeenCalledWith('text')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            unifiedImportMode: 'text',
            pasteName: '草稿',
            pasteContent: '旧内容',
            setPasteName: form.setPasteName,
            setPasteContent: form.setPasteContent,
            setPasteMode: form.setPasteMode,
            setUnifiedImportMode: form.setUnifiedImportMode,
            setImportCreateMode: form.setImportCreateMode,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    fireEvent.change(screen.getByDisplayValue('草稿'), { target: { value: '新名称' } })
    fireEvent.change(screen.getByDisplayValue('旧内容'), { target: { value: '新内容' } })
    await user.click(screen.getByRole('combobox', { name: 'paste-input-mode' }))
    await user.click(screen.getByRole('option', { name: '结构化 JSON' }))
    expect(form.setPasteName).toHaveBeenCalledWith('新名称')
    expect(form.setPasteContent).toHaveBeenCalledWith('新内容')
    expect(form.setPasteMode).toHaveBeenCalledWith('json')

    await user.click(screen.getByRole('tab', { name: '文件' }))
    expect(form.setUnifiedImportMode).toHaveBeenCalledWith('file')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            unifiedImportMode: 'file',
            uploadFiles: [new File(['a'], 'a.txt', { type: 'text/plain' })],
            setUploadInputMode: form.setUploadInputMode,
            setUploadFiles: form.setUploadFiles,
            setUnifiedImportMode: form.setUnifiedImportMode,
            setImportCreateMode: form.setImportCreateMode,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    expect(screen.getByText('已选择 1 个文件')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'upload-input-mode' }))
    await user.click(screen.getByRole('option', { name: '结构化 JSON' }))
    expect(form.setUploadInputMode).toHaveBeenCalledWith('json')
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const nextFile = new File(['x'], 'next.md', { type: 'text/markdown' })
    await user.upload(fileInput, nextFile)
    expect(form.setUploadFiles).toHaveBeenCalled()
    fireEvent.change(fileInput, { target: { files: null } })
    expect(form.setUploadFiles).toHaveBeenLastCalledWith([])

    await user.click(screen.getByRole('tab', { name: '文件夹' }))
    expect(form.setUnifiedImportMode).toHaveBeenCalledWith('folder')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            unifiedImportMode: 'folder',
            rawRelativePath: 'notes',
            rawGlob: '*.txt',
            setRawInputMode: form.setRawInputMode,
            setRawRelativePath: form.setRawRelativePath,
            setRawGlob: form.setRawGlob,
            setRawRecursive: form.setRawRecursive,
            setUnifiedImportMode: form.setUnifiedImportMode,
            setImportCreateMode: form.setImportCreateMode,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    await user.click(screen.getByRole('combobox', { name: 'raw-input-mode' }))
    await user.click(screen.getByRole('option', { name: '结构化 JSON' }))
    expect(form.setRawInputMode).toHaveBeenCalledWith('json')
    fireEvent.change(screen.getByDisplayValue('notes'), { target: { value: 'docs' } })
    fireEvent.change(screen.getByDisplayValue('*.txt'), { target: { value: '**/*.md' } })
    await user.click(checkboxNear('递归扫描'))
    expect(form.setRawRelativePath).toHaveBeenCalledWith('docs')
    expect(form.setRawGlob).toHaveBeenCalledWith('**/*.md')
    expect(form.setRawRecursive).toHaveBeenCalledWith(false)

    await user.click(screen.getByRole('button', { name: '切换导入方式' }))
    await user.click(screen.getByRole('menuitemradio', { name: /LPMM OpenIE/ }))
    expect(form.setImportCreateMode).toHaveBeenCalledWith('lpmm_openie')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'lpmm_openie',
            openieRelativePath: 'lpmm/in',
            setOpenieRelativePath: form.setOpenieRelativePath,
            setOpenieIncludeAllJson: form.setOpenieIncludeAllJson,
            setImportCreateMode: form.setImportCreateMode,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    expect(screen.getByText('读取 LPMM 内容并抽取关系')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('lpmm/in'), { target: { value: 'lpmm/out' } })
    await user.click(checkboxNear('包含全部 JSON 文件'))
    expect(form.setOpenieRelativePath).toHaveBeenCalledWith('lpmm/out')
    expect(form.setOpenieIncludeAllJson).toHaveBeenCalledWith(true)

    await user.click(screen.getByRole('button', { name: '切换导入方式' }))
    await user.click(screen.getByRole('menuitemradio', { name: /LPMM 转换/ }))
    expect(form.setImportCreateMode).toHaveBeenCalledWith('lpmm_convert')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'lpmm_convert',
            convertRelativePath: 'src',
            convertTargetRelativePath: 'dst',
            convertDimension: '512',
            convertBatchSize: '8',
            setConvertRelativePath: form.setConvertRelativePath,
            setConvertTargetRelativePath: form.setConvertTargetRelativePath,
            setConvertDimension: form.setConvertDimension,
            setConvertBatchSize: form.setConvertBatchSize,
            setImportCreateMode: form.setImportCreateMode,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    expect(screen.getByText('将 LPMM 数据转换到目标目录')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('src'), { target: { value: 'from' } })
    fireEvent.change(screen.getByDisplayValue('dst'), { target: { value: 'to' } })
    fireEvent.change(screen.getByDisplayValue('512'), { target: { value: '768' } })
    fireEvent.change(screen.getByDisplayValue('8'), { target: { value: '16' } })
    expect(form.setConvertRelativePath).toHaveBeenCalledWith('from')
    expect(form.setConvertTargetRelativePath).toHaveBeenCalledWith('to')
    expect(form.setConvertDimension).toHaveBeenCalledWith('768')
    expect(form.setConvertBatchSize).toHaveBeenCalledWith('16')

    await user.click(screen.getByRole('button', { name: '切换导入方式' }))
    await user.click(screen.getByRole('menuitemradio', { name: /资料导入/ }))
    expect(form.setImportCreateMode).toHaveBeenLastCalledWith('upload')
  })

  it('卡片右上角省略号菜单展示全部导入方式并高亮当前方式', async () => {
    const user = userEvent.setup()
    const setImportCreateMode = vi.fn()
    const { rerender } = renderImport({
      form: { importCreateMode: 'lpmm_convert', setImportCreateMode },
    })

    await user.click(screen.getByRole('button', { name: '切换导入方式' }))
    expect(screen.getByRole('menu')).toBeInTheDocument()

    // 当前方式在菜单中高亮，选择后回调对应值
    expect(screen.getByRole('menuitemradio', { name: /LPMM 转换/ })).toHaveAttribute(
      'data-state',
      'checked',
    )
    await user.click(screen.getByRole('menuitemradio', { name: /资料导入/ }))
    expect(setImportCreateMode).toHaveBeenCalledWith('upload')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm({ setImportCreateMode })} queue={makeQueue()} />
      </Tabs>,
    )
    await user.click(screen.getByRole('button', { name: '切换导入方式' }))
    expect(screen.getByRole('menuitemradio', { name: /资料导入/ })).toHaveAttribute(
      'data-state',
      'checked',
    )
  })

  it('通过省略号编辑导入参数、聊天流搜索与各平台用户 ID 标签', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: {
        importSettings: {},
        importCommonNarrativeWindowSize: '',
        importChatTargets: manyChats,
        importCommonChatId: 'g-1',
      },
    })

    expect(screen.queryByText('文件并发数')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '导入参数' }))
    const dialog = await screen.findByRole('dialog', { name: '导入参数' })
    expect(dialog).toHaveClass('[--dialog-width:72rem]')

    fireEvent.change(controlNearLabel('文件并发数'), { target: { value: '6' } })
    expect(form.setImportCommonFileConcurrency).toHaveBeenCalledWith('6')
    fireEvent.change(controlNearLabel('分块并发数'), { target: { value: '9' } })
    expect(form.setImportCommonChunkConcurrency).toHaveBeenCalledWith('9')
    await user.click(checkboxNear('启用 LLM 抽取'))
    expect(form.setImportCommonLlmEnabled).toHaveBeenCalledWith(false)

    expect(screen.getByText('备用群 B')).toBeInTheDocument()
    expect(screen.queryByText('备用群 D')).not.toBeInTheDocument()
    expect(screen.getByText(/当前选择：测试群 · 账号 bot-1 · 10001/)).toBeInTheDocument()

    const search = screen.getByRole('textbox', { name: '搜索归属聊天流' })
    await user.type(search, '20001')
    expect(screen.getByText('小明私聊')).toBeInTheDocument()
    expect(screen.getByText(/QQ 20001/)).toBeInTheDocument()
    expect(screen.queryByText('微信好友')).not.toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'wx-88')
    expect(screen.getByText('微信好友')).toBeInTheDocument()
    expect(screen.getByText(/微信 wx-88/)).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'wx-99')
    expect(screen.getByText(/微信 wx-99/)).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'tg-1')
    expect(screen.getByText('Telegram 私聊')).toBeInTheDocument()
    expect(screen.getByText(/用户 ID tg-1/)).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'bot-1')
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(screen.getAllByText(/账号 bot-1/).length).toBeGreaterThan(0)

    await user.clear(search)
    await user.type(search, 'zzz-no-match')
    expect(screen.getByText('没有找到匹配的聊天流')).toBeInTheDocument()

    await user.clear(search)
    await user.click(screen.getByRole('button', { name: /小明私聊/ }))
    expect(form.setImportCommonChatId).toHaveBeenCalledWith('u-qq')
    await user.click(screen.getByRole('button', { name: /所有聊天可用/ }))
    expect(form.setImportCommonChatId).toHaveBeenCalledWith('')

    await user.click(screen.getByText('高级参数（通常不用修改）'))
    fireEvent.change(controlNearLabel('叙事抽取窗口'), { target: { value: '800' } })
    fireEvent.change(controlNearLabel('叙事重叠字符'), { target: { value: '80' } })
    fireEvent.change(controlNearLabel('事实分块目标'), { target: { value: '900' } })
    fireEvent.change(controlNearLabel('去重策略'), { target: { value: 'none' } })
    fireEvent.change(controlNearLabel('聊天参考时间'), { target: { value: '2024-01-01' } })
    fireEvent.change(controlNearLabel('聊天流 ID'), { target: { value: 'chat-manual' } })
    await user.click(checkboxNear('强制导入'))
    await user.click(checkboxNear('清空导入清单'))
    expect(form.setImportCommonNarrativeWindowSize).toHaveBeenCalledWith('800')
    expect(form.setImportCommonForce).toHaveBeenCalledWith(true)
    expect(form.setImportCommonClearManifest).toHaveBeenCalledWith(true)

    await user.click(screen.getByRole('button', { name: '关闭' }))
  })

  it('文件夹模式的路径输入框可内联检查：以固定别名检查当前相对路径并展示结果', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: {
        unifiedImportMode: 'folder',
        rawRelativePath: 'exports/weekly',
      },
    })

    fireEvent.change(screen.getByDisplayValue('exports/weekly'), { target: { value: 'a/b' } })
    expect(form.setRawRelativePath).toHaveBeenCalledWith('a/b')

    await user.click(screen.getByRole('button', { name: '检查' }))
    expect(form.checkImportPath).toHaveBeenCalledWith('raw', 'exports/weekly', true)
    expect(await screen.findByText('解析到 /data/raw/exports（目录，已存在）')).toBeInTheDocument()
  })

  it('展示导入队列进度、错误、空态，并选择任务', async () => {
    const user = userEvent.setup()
    const { queue, rerender } = renderImport({
      queue: {
        importErrorText: '刷新导入任务失败',
        runningImportTasks: [makeImportTask()],
        queuedImportTasks: [makeImportTask({ task_id: 'task-q-1', status: 'queued', task_kind: 'upload' })],
        recentImportTasks: [
          makeImportTask({ task_id: 'task-done-1', status: 'completed', progress: 100, mode: 'raw_scan' }),
        ],
        selectedImportTaskId: 'task-run-1',
      },
    })

    expect(screen.getByText('刷新导入任务失败')).toBeInTheDocument()
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0)
    expect(screen.getAllByText('排队中').length).toBeGreaterThan(0)
    expect(screen.getByText('最近完成')).toBeInTheDocument()
    expect(screen.getByText('30.0%')).toBeInTheDocument()
    expect(screen.getByText('抽取中')).toBeInTheDocument()
    await user.click(screen.getByText('task-run-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-run-1')
    // 详情改为弹窗，打开后先关闭再继续操作队列
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByText('task-q-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-q-1')
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByText('task-done-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-done-1')
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(queue.refreshImportQueue).toHaveBeenCalledOnce()

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm()} queue={makeQueue()} />
      </Tabs>,
    )
    expect(screen.queryByText('运行中')).not.toBeInTheDocument()
    expect(screen.queryByText('排队中')).not.toBeInTheDocument()
    expect(screen.queryByText('最近完成')).not.toBeInTheDocument()
  })

  it('点击队列任务卡片打开详情弹窗，展示进度/文件/分块与取消重试按钮', async () => {
    const user = userEvent.setup()
    const running = makeImportTask({
      status: 'running',
      current_step: 'running',
      done_chunks: 36,
      total_chunks: 120,
      failed_chunks: 2,
      cancelled_chunks: 1,
    })
    const { queue } = renderImport({
      queue: {
        runningImportTasks: [running],
        selectedImportTaskId: running.task_id,
        selectedImportTaskResolved: running,
        selectedImportRetrySummary: {
          chunk_retry_files: 2,
          chunk_retry_chunks: 5,
          file_fallback_files: 1,
          skipped_files: 3,
        },
        selectedImportTaskErrorText: '任务执行出错',
        selectedImportFiles: [
          makeImportFile(),
          makeImportFile({ file_id: 'file-ok', name: '', status: 'completed', error: '', progress: 100 }),
        ],
        selectedImportFileId: 'file-alpha',
        selectedImportChunks: [
          makeChunk(),
          makeChunk({
            chunk_id: 'chunk-2',
            index: 4,
            error: '',
            content_preview: '',
            status: 'completed',
            step: 'completed',
          }),
        ],
        importChunkTotal: 120,
        importChunkOffset: 0,
        canImportChunkPrev: false,
        canImportChunkNext: true,
      },
    })

    // 详情不再常驻：未点开任务前页面上没有详情内容
    expect(screen.queryByText('任务详情')).not.toBeInTheDocument()

    await user.click(screen.getByText('task-run-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-run-1')
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText('成功 36 / 120 分块 · 失败 2 · 取消 1')).toBeInTheDocument()
    expect(within(dialog).getByText('任务执行出错')).toBeInTheDocument()
    // 任务类型走中文标签，状态与步骤同为「运行中」时只显示一次
    expect(within(dialog).getByText('粘贴导入')).toBeInTheDocument()
    expect(within(dialog).getAllByText('运行中')).toHaveLength(1)
    // 重试摘要存在非零计数时按网格展示
    expect(within(dialog).getByText('按分块重试的文件数')).toBeInTheDocument()
    expect(within(dialog).getByText('文件抽取失败')).toBeInTheDocument()
    expect(within(dialog).getAllByText(/成功 30 \/ 80 分块 · 失败 4 · 取消 2/).length).toBeGreaterThan(0)
    expect(within(dialog).getByText('file-ok')).toBeInTheDocument()
    expect(within(dialog).getByText('1-50 / 120')).toBeInTheDocument()
    expect(within(dialog).getByText('分块写入失败')).toBeInTheDocument()
    expect(within(dialog).getByText('查看分块预览')).toBeInTheDocument()
    expect(within(dialog).getByText('查看内容详情')).toBeInTheDocument()

    await user.click(within(dialog).getByText('alpha.txt'))
    expect(queue.selectImportFile).toHaveBeenCalledWith('file-alpha')
    await user.click(within(dialog).getByRole('button', { name: '下一页分块' }))
    expect(queue.moveImportChunkPage).toHaveBeenCalledWith(1)
    expect(within(dialog).getByRole('button', { name: '上一页分块' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: '取消选中导入任务' }))
    expect(queue.cancelSelectedImportTask).toHaveBeenCalledOnce()
    await user.click(within(dialog).getByRole('button', { name: '重试选中导入任务' }))
    expect(queue.retrySelectedImportTask).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.queryByText('任务详情')).not.toBeInTheDocument()
  })

  it('详情弹窗对无信息量的字段做省略：零重试摘要不展示、无文件时不显示进度条', async () => {
    const user = userEvent.setup()
    renderImport({
      queue: {
        recentImportTasks: [makeImportTask({ task_id: 'task-done-1', status: 'completed', progress: 100 })],
        selectedImportTaskResolved: makeImportTask({
          task_id: 'task-done-1',
          status: 'completed',
          current_step: 'completed',
          progress: 100,
          failed_chunks: 0,
          cancelled_chunks: 0,
        }),
        selectedImportRetrySummary: {
          chunk_retry_files: 0,
          chunk_retry_chunks: 0,
          file_fallback_files: 0,
          skipped_files: 0,
        },
        selectedImportFiles: [],
        selectedImportChunks: [],
        importChunkTotal: 0,
      },
    })

    await user.click(screen.getByText('task-done-1'))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText('成功 36 / 120 分块')).toBeInTheDocument()
    // 重试摘要全为 0 时不占据版面
    expect(within(dialog).queryByText('重试摘要')).not.toBeInTheDocument()
    expect(within(dialog).getByText('当前任务没有文件明细')).toBeInTheDocument()
    expect(within(dialog).getByText('0-0 / 0')).toBeInTheDocument()
    expect(within(dialog).getByText('当前页没有分块数据')).toBeInTheDocument()
    // 状态与步骤同为「已完成」时只在标题徽章显示一次
    expect(within(dialog).getAllByText('已完成')).toHaveLength(1)
  })

  it('新增事实是与导入任务、记忆包导入导出平级的写入方式', async () => {
    const user = userEvent.setup()
    renderImport()

    const writeModes = screen.getByRole('tablist', { name: '长期记忆写入方式' })
    expect(within(writeModes).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      '导入任务',
      '记忆包导入导出',
      '新增事实',
    ])

    // 新增事实不再是导入任务面板里的卡片，切到自己的写入方式后才出现
    expect(screen.queryByRole('button', { name: '新增事实' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '新增事实' }))
    expect(screen.getByRole('tab', { name: '新增事实' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByRole('button', { name: '新增事实' })).toBeInTheDocument()
  })

  it('新增事实写入事实账本：取消不调接口，失败保留弹窗，成功关闭弹窗', async () => {
    const user = userEvent.setup()
    renderImport()

    // 「新增事实」是与导入任务、记忆包导入导出平级的写入方式
    await user.click(screen.getByRole('tab', { name: '新增事实' }))
    await user.click(await screen.findByRole('button', { name: '新增事实' }))
    expect(screen.getByText('新增结构化事实')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('新增结构化事实')).not.toBeInTheDocument()
    })
    expect(createFactMock).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '新增事实' }))
    await user.type(screen.getByLabelText('归属 ID'), 'person-1')
    await user.type(screen.getByLabelText('事实键'), 'favorite_drink')
    await user.type(screen.getByLabelText('事实内容'), '咖啡')

    createFactMock.mockResolvedValueOnce({ success: false, error: '范围无效' })
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    await waitFor(() => expect(createFactMock).toHaveBeenCalledTimes(1))
    expect(screen.getByText('新增结构化事实')).toBeInTheDocument()

    createFactMock.mockResolvedValueOnce({
      success: true,
      claim: { claim_id: 'fact-new' },
      replaced: true,
    })
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    await waitFor(() => {
      expect(screen.queryByText('新增结构化事实')).not.toBeInTheDocument()
    })
    expect(createFactMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        scope_type: 'person',
        scope_id: 'person-1',
        fact_key: 'favorite_drink',
        value_text: '咖啡',
        profile_section: 'stable_facts',
      })
    )
  })
})
