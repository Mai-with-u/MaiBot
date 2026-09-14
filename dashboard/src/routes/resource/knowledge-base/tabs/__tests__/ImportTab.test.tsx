/**
 * ImportTab：用 mock 的 useImportForm / useImportQueue 结果锁定导入方式切换、提交、校验与队列 UI。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import type {
  MemoryImportChatTargetPayload,
  MemoryImportChunkPayload,
  MemoryImportFilePayload,
  MemoryImportTaskPayload,
} from '@/lib/memory-api'

import type { UseImportFormResult } from '../../hooks/useImportForm'
import type { UseImportQueueResult } from '../../hooks/useImportQueue'
import { ImportTab } from '../ImportTab'

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
    pathResolveAlias: 'raw',
    setPathResolveAlias: vi.fn(),
    importAliasKeys: ['raw', 'lpmm'],
    pathResolveRelativePath: '',
    setPathResolveRelativePath: vi.fn(),
    pathResolveMustExist: false,
    setPathResolveMustExist: vi.fn(),
    resolveImportPath: vi.fn(async () => {}),
    resolvingPath: false,
    pathResolveOutput: '',
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
    importAutoPolling: true,
    setImportAutoPolling: vi.fn(),
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
  const view = render(
    <Tabs defaultValue="import">
      <ImportTab form={form} queue={queue} />
    </Tabs>,
  )
  return { ...view, form, queue }
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

    await user.click(screen.getByRole('tab', { name: 'LPMM OpenIE' }))
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

    await user.click(screen.getByRole('tab', { name: 'LPMM 转换' }))
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

    await user.click(screen.getByRole('tab', { name: '资料导入' }))
    expect(form.setImportCreateMode).toHaveBeenLastCalledWith('upload')
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

  it('路径预检可改别名；别名为空时禁用，解析中展示 loading', async () => {
    const user = userEvent.setup()
    const { form, rerender } = renderImport({
      form: {
        pathResolveRelativePath: 'exports/weekly',
      },
    })

    await user.click(screen.getByRole('combobox', { name: 'import-path-alias' }))
    await user.click(screen.getByRole('option', { name: 'lpmm' }))
    expect(form.setPathResolveAlias).toHaveBeenCalledWith('lpmm')

    fireEvent.change(screen.getByPlaceholderText('例如 exports/weekly'), { target: { value: 'a/b' } })
    expect(form.setPathResolveRelativePath).toHaveBeenCalledWith('a/b')
    await user.click(checkboxNear('要求路径已存在'))
    expect(form.setPathResolveMustExist).toHaveBeenCalledWith(true)
    await user.click(screen.getByRole('button', { name: '解析路径' }))
    expect(form.resolveImportPath).toHaveBeenCalledOnce()

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importAliasKeys: [],
            pathResolveAlias: '   ',
            resolvingPath: true,
            pathResolveOutput: '解析失败：路径不存在',
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    expect(screen.getByRole('button', { name: '解析路径' })).toBeDisabled()
    expect(screen.getByDisplayValue('解析失败：路径不存在')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'import-path-alias' }))
    expect(screen.getByRole('option', { name: 'raw' })).toBeInTheDocument()
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
    expect(screen.getAllByText('运行中 1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('排队中 1').length).toBeGreaterThan(0)
    expect(screen.getByText('最近完成 1')).toBeInTheDocument()
    expect(screen.getByText('30.0%')).toBeInTheDocument()
    expect(screen.getByText('抽取中')).toBeInTheDocument()
    await user.click(screen.getByText('task-run-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-run-1')
    await user.click(screen.getByText('task-q-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-q-1')
    await user.click(screen.getByText('task-done-1'))
    expect(queue.selectImportTask).toHaveBeenCalledWith('task-done-1')
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(queue.refreshImportQueue).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('checkbox', { name: /自动轮询 1000ms/ }))
    expect(queue.setImportAutoPolling).toHaveBeenCalledWith(false)

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm()} queue={makeQueue()} />
      </Tabs>,
    )
    expect(screen.getByText('当前没有运行中任务')).toBeInTheDocument()
    expect(screen.getByText('当前没有排队任务')).toBeInTheDocument()
    expect(screen.getByText('暂时没有历史任务')).toBeInTheDocument()
  })

  it('展示任务详情并渲染取消/重试按钮；未选中时禁用', async () => {
    const user = userEvent.setup()
    const running = makeImportTask({
      status: 'running',
      done_chunks: 36,
      total_chunks: 120,
      failed_chunks: 2,
      cancelled_chunks: 1,
    })
    const { queue, rerender } = renderImport({
      queue: {
        selectedImportTaskId: running.task_id,
        selectedImportTaskResolved: running,
        selectedImportTaskLoading: true,
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

    expect(screen.getAllByLabelText('加载中').length).toBeGreaterThan(0)
    expect(screen.getByText('成功 36 / 120 分块 · 失败 2 · 取消 1')).toBeInTheDocument()
    expect(screen.getByText('任务执行出错')).toBeInTheDocument()
    expect(screen.getByText('按分块重试的文件数')).toBeInTheDocument()
    expect(screen.getByText('文件抽取失败')).toBeInTheDocument()
    expect(screen.getAllByText(/成功 30 \/ 80 分块 · 失败 4 · 取消 2/).length).toBeGreaterThan(0)
    expect(screen.getByText('file-ok')).toBeInTheDocument()
    expect(screen.getByText('1-50 / 120')).toBeInTheDocument()
    expect(screen.getByText('分块写入失败')).toBeInTheDocument()
    expect(screen.getByText('查看分块预览')).toBeInTheDocument()
    expect(screen.getByText('查看内容详情')).toBeInTheDocument()

    await user.click(screen.getByText('alpha.txt'))
    expect(queue.selectImportFile).toHaveBeenCalledWith('file-alpha')
    await user.click(screen.getByRole('button', { name: '下一页分块' }))
    expect(queue.moveImportChunkPage).toHaveBeenCalledWith(1)
    expect(screen.getByRole('button', { name: '上一页分块' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '取消选中导入任务' }))
    expect(queue.cancelSelectedImportTask).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: '重试选中导入任务' }))
    expect(queue.retrySelectedImportTask).toHaveBeenCalledOnce()

    const tones: Array<MemoryImportTaskPayload['status']> = [
      'completed',
      'failed',
      'completed_with_errors',
      'cancelled',
    ]
    for (const status of tones) {
      rerender(
        <Tabs defaultValue="import">
          <ImportTab
            form={makeForm()}
            queue={makeQueue({
              selectedImportTaskId: 'task-run-1',
              selectedImportTaskResolved: makeImportTask({
                status,
                current_step: status,
                failed_chunks: 0,
                cancelled_chunks: 0,
              }),
              selectedImportFiles: [],
              selectedImportChunks: [],
              importChunksLoading: status === 'cancelled',
            })}
          />
        </Tabs>,
      )
    }
    expect(screen.getByText('成功 36 / 120 分块')).toBeInTheDocument()
    expect(screen.getByText('当前任务没有文件明细')).toBeInTheDocument()
    expect(screen.getAllByLabelText('加载中').length).toBeGreaterThan(0)

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm()}
          queue={makeQueue({
            selectedImportTaskId: 'task-run-1',
            selectedImportTaskResolved: makeImportTask({
              task_kind: undefined,
              mode: undefined,
              status: '',
            }),
            importChunkTotal: 0,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('0-0 / 0')).toBeInTheDocument()
    expect(screen.getByText('当前页没有分块数据')).toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm()}
          queue={makeQueue({
            selectedImportTaskId: 'task-run-1',
            selectedImportTaskResolved: makeImportTask(),
            importChunkOffset: 50,
            importChunkTotal: 80,
            canImportChunkPrev: true,
            canImportChunkNext: false,
            moveImportChunkPage: queue.moveImportChunkPage,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('51-80 / 80')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '上一页分块' }))
    expect(queue.moveImportChunkPage).toHaveBeenLastCalledWith(-1)
    expect(screen.getByRole('button', { name: '下一页分块' })).toBeDisabled()

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm()} queue={makeQueue({ selectedImportTaskId: '' })} />
      </Tabs>,
    )
    expect(screen.getByText('还没选中任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消选中导入任务' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重试选中导入任务' })).toBeDisabled()
  })
})
