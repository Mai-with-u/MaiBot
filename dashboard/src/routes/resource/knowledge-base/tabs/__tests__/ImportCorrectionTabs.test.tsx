/**
 * ImportTab / CorrectionTab：用 mock hook 结果锁定提交、校验、进度与错误 UI。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import type {
  MemoryCorrectionCandidatePayload,
  MemoryCorrectionOperationPayload,
  MemoryCorrectionPlanPayload,
  MemoryCorrectionRelationCascadePayload,
  MemoryCorrectionStaleMarkRollbackPayload,
  MemoryImportChatTargetPayload,
  MemoryImportChunkPayload,
  MemoryImportFilePayload,
  MemoryImportTaskPayload,
} from '@/lib/memory-api'

import type { UseImportFormResult } from '../../hooks/useImportForm'
import type { UseImportQueueResult } from '../../hooks/useImportQueue'
import type { UseMemoryCorrectionResult } from '../../hooks/useMemoryCorrection'
import { CorrectionTab } from '../CorrectionTab'
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

function checkboxNear(text: string | HTMLElement) {
  const node = typeof text === 'string' ? screen.getByText(text) : text
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
    backfillLimit: '100',
    setBackfillLimit: vi.fn(),
    backfillDryRun: false,
    setBackfillDryRun: vi.fn(),
    backfillNoCreatedFallback: false,
    setBackfillNoCreatedFallback: vi.fn(),
    maibotSourceDb: 'data/MaiBot.db',
    setMaibotSourceDb: vi.fn(),
    maibotTimeFrom: '',
    setMaibotTimeFrom: vi.fn(),
    maibotTimeTo: '',
    setMaibotTimeTo: vi.fn(),
    maibotStartId: '',
    setMaibotStartId: vi.fn(),
    maibotEndId: '',
    setMaibotEndId: vi.fn(),
    maibotStreamIds: '',
    setMaibotStreamIds: vi.fn(),
    maibotGroupIds: '',
    setMaibotGroupIds: vi.fn(),
    maibotUserIds: '',
    setMaibotUserIds: vi.fn(),
    maibotReadBatchSize: '',
    setMaibotReadBatchSize: vi.fn(),
    maibotCommitWindowRows: '',
    setMaibotCommitWindowRows: vi.fn(),
    maibotEmbedWorkers: '',
    setMaibotEmbedWorkers: vi.fn(),
    maibotNoResume: false,
    setMaibotNoResume: vi.fn(),
    maibotResetState: false,
    setMaibotResetState: vi.fn(),
    maibotDryRun: false,
    setMaibotDryRun: vi.fn(),
    maibotVerifyOnly: false,
    setMaibotVerifyOnly: vi.fn(),
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

function makeRelation(
  overrides: Partial<MemoryCorrectionRelationCascadePayload> = {},
): MemoryCorrectionRelationCascadePayload {
  return {
    paragraph_hash: 'para-1',
    relation_hash: 'rel-1',
    action: 'mark_inactive',
    reason: 'superseded',
    subject: '张三',
    predicate: '住在',
    object: '杭州',
    ...overrides,
  }
}

function makeStaleMark(
  overrides: Partial<MemoryCorrectionStaleMarkRollbackPayload> = {},
): MemoryCorrectionStaleMarkRollbackPayload {
  return {
    success: true,
    action: 'deleted',
    paragraph_hash: 'para-stale',
    relation_hash: 'rel-stale',
    ...overrides,
  }
}

function makeCandidate(
  overrides: Partial<MemoryCorrectionCandidatePayload> = {},
): MemoryCorrectionCandidatePayload {
  return {
    candidate_id: 'cand-1',
    target_type: 'paragraph',
    evidence_type: 'profile',
    hash: 'hash-cand-1',
    content: '候选摘要',
    source: 'chat:1',
    metadata: {},
    score: 0.9123,
    ...overrides,
  }
}

function makePlan(overrides: Partial<MemoryCorrectionPlanPayload> = {}): MemoryCorrectionPlanPayload {
  return {
    plan_id: 'plan-1',
    request_text: '把张三的常住城市改为杭州',
    scope: 'person_profile',
    target_person_id: 'person-1',
    target_chat_id: 'chat-1',
    status: 'awaiting_confirmation',
    confidence: 0.86,
    plan: {
      scope: 'person_profile',
      request_text: '把张三的常住城市改为杭州',
      person_id: 'person-1',
      chat_id: 'chat-1',
      confidence: 0.86,
      risk_level: 'low',
      reason: '用户明确修正',
      operations: [],
    },
    preview: {
      request_text: '把张三的常住城市改为杭州',
      scope: 'person_profile',
      person_id: 'person-1',
      person_keyword: '张三',
      chat_id: 'chat-1',
      candidates: [],
      operations: [],
      requires_confirmation: true,
      confirm_threshold: 0.5,
      reason: '用户明确修正',
    },
    execution: {},
    created_at: 1_710_000_000,
    updated_at: 1_710_000_100,
    requested_by: 'knowledge_base',
    reason: '用户明确修正',
    ...overrides,
  }
}

function makeCorrection(overrides: Partial<UseMemoryCorrectionResult> = {}): UseMemoryCorrectionResult {
  return {
    requestText: '',
    setRequestText: vi.fn(),
    scope: 'person_profile',
    setScope: vi.fn(),
    personId: '',
    setPersonId: vi.fn(),
    personKeyword: '',
    setPersonKeyword: vi.fn(),
    chatId: '',
    setChatId: vi.fn(),
    candidateLimit: '12',
    setCandidateLimit: vi.fn(),
    candidateLimitMax: 12,
    correctionReason: '',
    setCorrectionReason: vi.fn(),
    planSearch: '',
    setPlanSearch: vi.fn(),
    planStatusFilter: 'all',
    setPlanStatusFilter: vi.fn(),
    planScopeFilter: 'all',
    setPlanScopeFilter: vi.fn(),
    plans: [],
    filteredPlans: [],
    pagedPlans: [],
    planPage: 1,
    setPlanPage: vi.fn(),
    planPageCount: 1,
    selectedPlanId: '',
    setSelectedPlanId: vi.fn(),
    selectedPlan: null,
    selectedPreview: null,
    selectedPlanLoading: false,
    selectedPlanError: '',
    chatTargets: [],
    chatTargetsLoading: false,
    chatTargetsErrorText: '',
    correctionErrorText: '',
    previewPayload: null,
    previewing: false,
    executingPlanId: '',
    rollingBackPlanId: '',
    submitPreview: vi.fn(async () => {}),
    executePlan: vi.fn(async () => {}),
    rollbackPlan: vi.fn(async () => {}),
    refreshPlans: vi.fn(async () => {}),
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

function renderCorrection(overrides: Partial<UseMemoryCorrectionResult> = {}) {
  const correction = makeCorrection(overrides)
  const view = render(
    <Tabs defaultValue="correction">
      <CorrectionTab correction={correction} />
    </Tabs>,
  )
  return { ...view, correction }
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

    await user.click(screen.getByRole('combobox', { name: '资料类别' }))
    await user.click(screen.getByRole('option', { name: '事实资料' }))
    expect(form.setImportContentCategory).toHaveBeenCalledWith('factual')
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

  it('按聊天流搜索走 getChatTargetSearchText，并渲染各平台用户 ID 标签', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: { importChatTargets: manyChats, importCommonChatId: 'g-1' },
    })

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
  })

  it('编辑公共参数、高级参数与上传输入', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: {
        importSettings: {},
        importCommonNarrativeWindowSize: '',
        uploadFiles: [new File(['a'], 'a.txt', { type: 'text/plain' })],
      },
    })

    fireEvent.change(controlNearLabel('文件并发数'), { target: { value: '6' } })
    expect(form.setImportCommonFileConcurrency).toHaveBeenCalledWith('6')
    fireEvent.change(controlNearLabel('分块并发数'), { target: { value: '9' } })
    expect(form.setImportCommonChunkConcurrency).toHaveBeenCalledWith('9')
    await user.click(checkboxNear('启用 LLM 抽取'))
    expect(form.setImportCommonLlmEnabled).toHaveBeenCalledWith(false)

    await user.click(screen.getByText('高级参数（通常不用修改）'))
    fireEvent.change(controlNearLabel('叙事抽取窗口'), { target: { value: '800' } })
    fireEvent.change(controlNearLabel('叙事重叠字符'), { target: { value: '80' } })
    fireEvent.change(controlNearLabel('事实分块目标'), { target: { value: '900' } })
    fireEvent.change(controlNearLabel('去重策略'), { target: { value: 'none' } })
    fireEvent.change(controlNearLabel('聊天参考时间'), { target: { value: '2024-01-01' } })
    fireEvent.change(controlNearLabel('聊天流 ID'), { target: { value: 'chat-manual' } })
    await user.click(checkboxNear('强制导入'))
    await user.click(checkboxNear('清空导入清单'))
    expect(form.setImportCommonForce).toHaveBeenCalledWith(true)
    expect(form.setImportCommonClearManifest).toHaveBeenCalledWith(true)

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
  })

  it('切换导入方式并编辑各模式字段', async () => {
    const user = userEvent.setup()
    const setImportCreateMode = vi.fn()
    const { rerender, form } = renderImport({
      form: { setImportCreateMode, pasteName: '草稿', pasteContent: '旧内容' },
    })

    await user.click(screen.getByRole('tab', { name: '粘贴导入' }))
    expect(setImportCreateMode).toHaveBeenCalledWith('paste')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'paste',
            pasteName: '草稿',
            pasteContent: '旧内容',
            setPasteName: form.setPasteName,
            setPasteContent: form.setPasteContent,
            setPasteMode: form.setPasteMode,
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

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'raw_scan',
            rawRelativePath: 'notes',
            rawGlob: '*.txt',
            setRawInputMode: form.setRawInputMode,
            setRawRelativePath: form.setRawRelativePath,
            setRawGlob: form.setRawGlob,
            setRawRecursive: form.setRawRecursive,
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
    expect(form.setRawRecursive).toHaveBeenCalledWith(false)

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'lpmm_openie',
            openieRelativePath: 'lpmm/in',
            setOpenieRelativePath: form.setOpenieRelativePath,
            setOpenieIncludeAllJson: form.setOpenieIncludeAllJson,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    fireEvent.change(screen.getByDisplayValue('lpmm/in'), { target: { value: 'lpmm/out' } })
    await user.click(checkboxNear('包含全部 JSON 文件'))
    expect(form.setOpenieRelativePath).toHaveBeenCalledWith('lpmm/out')
    expect(form.setOpenieIncludeAllJson).toHaveBeenCalledWith(true)

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
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    fireEvent.change(screen.getByDisplayValue('src'), { target: { value: 'from' } })
    fireEvent.change(screen.getByDisplayValue('dst'), { target: { value: 'to' } })
    fireEvent.change(screen.getByDisplayValue('512'), { target: { value: '768' } })
    fireEvent.change(screen.getByDisplayValue('8'), { target: { value: '16' } })
    expect(form.setConvertBatchSize).toHaveBeenCalledWith('16')

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm({
            importCreateMode: 'temporal_backfill',
            backfillLimit: '10',
            setBackfillLimit: form.setBackfillLimit,
            setBackfillDryRun: form.setBackfillDryRun,
            setBackfillNoCreatedFallback: form.setBackfillNoCreatedFallback,
          })}
          queue={makeQueue()}
        />
      </Tabs>,
    )
    fireEvent.change(screen.getByDisplayValue('10'), { target: { value: '20' } })
    await user.click(checkboxNear('只预演，不写入数据'))
    await user.click(checkboxNear('禁用创建时间回退'))
    expect(form.setBackfillLimit).toHaveBeenCalledWith('20')
    expect(form.setBackfillDryRun).toHaveBeenCalledWith(true)
  })

  it('编辑 MaiBot 迁移字段与高级选项', async () => {
    const user = userEvent.setup()
    const { form } = renderImport({
      form: {
        importCreateMode: 'maibot_migration',
        maibotTimeFrom: '2024-01-02T03:04',
        maibotTimeTo: '2024-01-03T05:06',
        maibotStartId: '1',
        maibotEndId: '9',
        maibotStreamIds: 's1',
        maibotGroupIds: 'g1',
        maibotUserIds: 'u1',
        maibotReadBatchSize: '10',
        maibotCommitWindowRows: '20',
        maibotEmbedWorkers: '2',
      },
    })

    fireEvent.change(screen.getByLabelText('源数据库路径'), { target: { value: 'data/old.db' } })
    fireEvent.change(screen.getByLabelText('起始时间'), { target: { value: '2024-02-01T00:00' } })
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2024-02-02T00:00' } })
    fireEvent.change(screen.getByLabelText('起始 ID'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('结束 ID'), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText('会话 ID 列表'), { target: { value: 's2' } })
    fireEvent.change(screen.getByLabelText('群组 ID 列表'), { target: { value: 'g2' } })
    fireEvent.change(screen.getByLabelText('用户 ID 列表'), { target: { value: 'u2' } })
    await user.click(screen.getByText('高级选项'))
    fireEvent.change(screen.getByLabelText('读取批大小'), { target: { value: '11' } })
    fireEvent.change(screen.getByLabelText('提交窗口行数'), { target: { value: '22' } })
    fireEvent.change(screen.getByLabelText('向量线程数'), { target: { value: '3' } })
    await user.click(checkboxNear('从头开始，不继续上次进度'))
    await user.click(checkboxNear('重置迁移状态'))
    const dryRuns = screen.getAllByText('只预演，不写入数据')
    await user.click(checkboxNear(dryRuns[dryRuns.length - 1].textContent ?? '只预演，不写入数据'))
    await user.click(checkboxNear('仅校验'))
    expect(form.setMaibotSourceDb).toHaveBeenCalledWith('data/old.db')
    expect(form.setMaibotNoResume).toHaveBeenCalledWith(true)
    expect(form.setMaibotVerifyOnly).toHaveBeenCalledWith(true)
  })

  it('路径预检在别名为空时禁用，解析中展示 loading', async () => {
    const user = userEvent.setup()
    const { form, rerender } = renderImport({
      form: {
        importAliasKeys: [],
        pathResolveRelativePath: 'exports/weekly',
      },
    })

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
  })

  it('展示导入队列进度、错误、空态，并选择任务', async () => {
    const user = userEvent.setup()
    const { queue, rerender } = renderImport({
      queue: {
        importErrorText: '刷新导入任务失败',
        runningImportTasks: [makeImportTask()],
        queuedImportTasks: [makeImportTask({ task_id: 'task-q-1', status: 'queued', task_kind: 'upload' })],
        recentImportTasks: [makeImportTask({ task_id: 'task-done-1', status: 'completed', progress: 100, mode: 'raw_scan' })],
        selectedImportTaskId: 'task-run-1',
      },
    })

    expect(screen.getByText('刷新导入任务失败')).toBeInTheDocument()
    expect(screen.getByText('30.0%')).toBeInTheDocument()
    expect(screen.getByText('抽取中')).toBeInTheDocument()
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

  it('展示任务详情进度、重试摘要、文件与分块错误', async () => {
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
          makeChunk({ chunk_id: 'chunk-2', index: 4, error: '', content_preview: '', status: 'completed', step: 'completed' }),
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
    expect(screen.getByText('当前任务没有文件明细')).toBeInTheDocument()
    expect(screen.getAllByLabelText('加载中').length).toBeGreaterThan(0)

    rerender(
      <Tabs defaultValue="import">
        <ImportTab
          form={makeForm()}
          queue={makeQueue({
            selectedImportTaskId: 'task-run-1',
            selectedImportTaskResolved: makeImportTask({ task_kind: undefined, mode: undefined, status: '' }),
            importChunkTotal: 0,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('0-0 / 0')).toBeInTheDocument()
    expect(screen.getByText('当前页没有分块数据')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="import">
        <ImportTab form={makeForm()} queue={makeQueue({ selectedImportTaskId: '' })} />
      </Tabs>,
    )
    expect(screen.getByText('还没选中任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消选中导入任务' })).toBeDisabled()
  })
})

describe('CorrectionTab', () => {
  it('提交预览并写入表单字段', async () => {
    const user = userEvent.setup()
    const { correction } = renderCorrection({
      requestText: '旧请求',
      personId: 'p0',
      personKeyword: '张',
      candidateLimit: '8',
      correctionReason: '原因',
    })

    fireEvent.change(screen.getByLabelText('修正内容'), { target: { value: '改为杭州' } })
    fireEvent.change(screen.getByLabelText('人物 ID'), { target: { value: 'person-1' } })
    fireEvent.change(screen.getByLabelText('人物关键词'), { target: { value: '张三' } })
    fireEvent.change(screen.getByLabelText('候选上限'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '人工确认' } })
    await user.click(screen.getAllByRole('combobox')[0])
    await user.click(screen.getByRole('option', { name: '记忆段落' }))
    expect(correction.setScope).toHaveBeenCalledWith('memory')
    await user.click(screen.getByRole('button', { name: '生成预览' }))
    expect(correction.submitPreview).toHaveBeenCalledOnce()
  })

  it('预览中与错误文案、聊天流加载/失败/筛选', async () => {
    const user = userEvent.setup()
    const { correction, rerender } = renderCorrection({
      previewing: true,
      correctionErrorText: '预览失败：请求为空',
      chatTargets: manyChats,
      chatId: 'u-qq',
    })

    expect(screen.getByRole('button', { name: '生成预览' })).toBeDisabled()
    expect(screen.getByText('预览失败：请求为空')).toBeInTheDocument()
    expect(screen.getByText('小明私聊 · 账号 bot-1 · 20001')).toBeInTheDocument()
    expect(screen.getByText('u-qq')).toBeInTheDocument()
    expect(screen.getByText(/QQ 20001/)).toBeInTheDocument()
    expect(screen.queryByText('备用群 B')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '清空聊天流' }))
    expect(correction.setChatId).toHaveBeenCalledWith('')
    await user.click(screen.getByRole('button', { name: /小明私聊/ }))
    expect(correction.setChatId).toHaveBeenCalledWith('u-qq')

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab
          correction={makeCorrection({
            chatTargetsLoading: true,
            chatId: '',
            chatTargets: manyChats,
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('加载聊天流')).toBeInTheDocument()
    expect(screen.getByText('可直接填写，名称会在后端尝试转换为真实 session_id')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatId: '', chatTargets: manyChats })} />
      </Tabs>,
    )
    expect(screen.getByText('无名会话')).toBeInTheDocument()
    expect(screen.queryByText('备用群 A')).not.toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatId: '微信', chatTargets: manyChats })} />
      </Tabs>,
    )
    expect(screen.getByText('微信好友')).toBeInTheDocument()
    expect(screen.getByText(/微信 wx-88/)).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatId: 'wx-99', chatTargets: manyChats })} />
      </Tabs>,
    )
    expect(screen.getByText(/微信 wx-99/)).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatId: 'tg-1', chatTargets: manyChats })} />
      </Tabs>,
    )
    expect(screen.getByText(/用户 ID tg-1/)).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab correction={makeCorrection({ chatTargetsErrorText: '聊天流加载失败', chatId: 'nope' })} />
      </Tabs>,
    )
    expect(screen.getByText('聊天流加载失败')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab
          correction={makeCorrection({
            chatId: 'zzz-no-match',
            chatTargets: [makeChat({ chat_name: '' })],
            candidateLimitMax: null,
            candidateLimit: '',
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('未匹配到聊天流，可继续手动填写')).toBeInTheDocument()
    expect(screen.getByLabelText('候选上限')).toHaveAttribute('placeholder', '按配置默认')
  })

  it('空计划时展示引导，执行/回滚保持禁用', () => {
    renderCorrection({ selectedPlan: { ...makePlan(), plan_id: '' } })
    expect(screen.getByText('尚未选择记忆修正计划')).toBeInTheDocument()
    expect(screen.getByText('生成预览或从历史计划中选择一项')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认执行' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '回滚计划' })).toBeDisabled()
  })

  it('确认执行待确认计划，并渲染级联动作文案', async () => {
    const user = userEvent.setup()
    const operations: MemoryCorrectionOperationPayload[] = [
      { action: 'mark_superseded', candidate_id: 'c1', target_type: 'paragraph', hash: 'h-old', reason: '过期' },
      { action: 'ingest_text', text: '新记忆正文', source_type: 'manual', chat_id: 'chat-1', person_ids: [], participants: [], tags: [], relations: [], reason: '写入' },
      { action: 'refresh_person_profile', person_id: 'person-1' },
      { action: 'custom_op', reason: '自定义原因' },
      { action: '', reason: '空动作' },
      { action: 'mark_superseded', reason: '仅原因' },
      { action: 'ingest_text', reason: '无正文' },
      { action: 'refresh_person_profile' },
    ]
    const plan = makePlan({
      status: 'awaiting_confirmation',
      executed_at: 1_710_000_000_000,
      confidence: Number.NaN,
    })
    const { correction } = renderCorrection({
      selectedPlan: plan,
      selectedPreview: {
        ...plan.preview,
        operations,
        candidates: [
          makeCandidate(),
          makeCandidate({ candidate_id: 'cand-2', content: '', score: null, source: '', hash: 'h2', evidence_type: '' }),
        ],
        cascade_preview: {
          counts: {
            relations: 4,
            relations_mark_inactive: 1,
            relations_mark_stale_evidence: 1,
            relations_skipped_protected: 1,
            entities: 2,
          },
          relations: [
            makeRelation({ action: 'mark_inactive', relation_hash: 'r-inactive' }),
            makeRelation({ action: 'mark_stale_evidence', relation_hash: 'r-stale', subject: '李四', object: '上海' }),
            makeRelation({ action: 'skipped_protected', relation_hash: 'r-skip' }),
            makeRelation({
              action: 'unknown_action' as MemoryCorrectionRelationCascadePayload['action'],
              relation_hash: 'r-unknown',
            }),
            makeRelation({ relation_hash: 'r-extra-1' }),
            makeRelation({ relation_hash: 'r-extra-2' }),
            makeRelation({ relation_hash: 'r-extra-3' }),
          ],
          entities: [
            {
              paragraph_hash: 'para-1',
              entity_hash: 'ent-1',
              action: 'record_impact_only',
              reason: 'impact',
              name: '杭州',
              type: 'city',
            },
            {
              paragraph_hash: 'para-2',
              entity_hash: 'ent-hash-only',
              action: 'record_impact_only',
              reason: 'impact',
              name: '',
              type: 'unknown',
            },
          ],
        },
      },
    })

    expect(screen.getByText('待确认')).toBeInTheDocument()
    expect(screen.getByText('置信度 -')).toBeInTheDocument()
    expect(screen.getAllByText('关系失效').length).toBeGreaterThan(0)
    expect(screen.getAllByText('标记旧证据').length).toBeGreaterThan(0)
    expect(screen.getAllByText('受保护跳过').length).toBeGreaterThan(0)
    expect(screen.getByText('unknown_action')).toBeInTheDocument()
    expect(screen.getByText(/受影响实体：杭州、ent-hash-only/)).toBeInTheDocument()
    expect(screen.getAllByText('标记失效').length).toBeGreaterThan(0)
    expect(screen.getAllByText('写入新记忆').length).toBeGreaterThan(0)
    expect(screen.getAllByText('刷新画像').length).toBeGreaterThan(0)
    expect(screen.getByText('custom_op')).toBeInTheDocument()
    expect(screen.getByText('未知操作')).toBeInTheDocument()
    expect(screen.getByText('paragraph:h-old')).toBeInTheDocument()
    expect(screen.getByText('新记忆正文')).toBeInTheDocument()
    expect(screen.getByText('0.912')).toBeInTheDocument()
    expect(screen.getByText('无内容摘要')).toBeInTheDocument()
    expect(screen.getByText('score -')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: '回滚计划' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '确认执行' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('确认执行记忆修正')).toBeInTheDocument()
    expect(within(dialog).getByText('操作 8 项')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '确认执行' }))
    expect(correction.executePlan).toHaveBeenCalledWith('plan-1')
  })

  it('失败计划仍可执行；执行中禁用确认', async () => {
    const user = userEvent.setup()
    const plan = makePlan({ status: 'failed', execution: { error: '执行失败：写入冲突' } })
    const { rerender } = renderCorrection({
      selectedPlan: plan,
      selectedPlanError: '计划详情加载失败',
      selectedPlanLoading: true,
    })

    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getByText('执行失败：写入冲突')).toBeInTheDocument()
    expect(screen.getByText('计划详情加载失败')).toBeInTheDocument()
    expect(screen.getByText('当前计划没有操作')).toBeInTheDocument()
    expect(screen.getByText('当前计划没有候选证据')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="correction">
        <CorrectionTab
          correction={makeCorrection({
            selectedPlan: plan,
            executingPlanId: 'plan-1',
          })}
        />
      </Tabs>,
    )
    expect(screen.getByRole('button', { name: '确认执行' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '确认执行' }))
  })

  it('已执行计划可回滚，并渲染级联与旧证据标记', async () => {
    const user = userEvent.setup()
    const cascade = {
      relations_marked_inactive: [makeRelation()],
      relations_marked_stale: [makeRelation({ action: 'mark_stale_evidence', relation_hash: 'stale' })],
      relations_skipped: [makeRelation({ action: 'skipped_protected', relation_hash: 'skip' })],
      impacted_entities: [
        {
          paragraph_hash: 'para-1',
          entity_hash: 'ent-1',
          action: 'record_impact_only' as const,
          reason: 'impact',
          name: '杭州',
          type: 'city',
        },
      ],
      stale_mark_snapshots: [],
    }
    const plan = makePlan({
      status: 'executed',
      scope: 'memory',
      created_at: 0,
      execution: {
        superseded_targets: [
          { target_type: 'paragraph', hash: 'h1', previous_metadata: {}, cascade },
          { target_type: 'relation', hash: 'h2', previous_metadata: {} },
        ],
        rollback: {
          success: true,
          new_relations_deactivated: [],
          restored_targets: [{ target_type: 'paragraph', hash: 'h1' }],
          stale_marks_deleted: [
            makeStaleMark({ paragraph_hash: 'd1', relation_hash: 'rd1' }),
            makeStaleMark({ paragraph_hash: 'd2', relation_hash: 'rd2' }),
            makeStaleMark({ paragraph_hash: 'd3', relation_hash: 'rd3' }),
            makeStaleMark({ paragraph_hash: 'd4', relation_hash: 'rd4' }),
            makeStaleMark({ paragraph_hash: 'd5', relation_hash: 'rd5' }),
            makeStaleMark({ paragraph_hash: 'd6', relation_hash: 'rd6' }),
          ],
          stale_marks_restored: [makeStaleMark({ action: 'restored', paragraph_hash: 'rst1', relation_hash: 'rr1' })],
          stale_marks_skipped: [],
          items: [],
          requested_by: 'knowledge_base',
          reason: '回滚',
        },
      },
    })
    const { correction } = renderCorrection({ selectedPlan: plan, selectedPreview: null })

    expect(screen.getByText('已执行')).toBeInTheDocument()
    expect(screen.getByText('记忆段落')).toBeInTheDocument()
    expect(screen.getByText('级联失效 1')).toBeInTheDocument()
    expect(screen.getByText('旧证据 1')).toBeInTheDocument()
    expect(screen.getByText('保护跳过 1')).toBeInTheDocument()
    expect(screen.getByText('受影响实体 1')).toBeInTheDocument()
    expect(screen.getByText('删除的旧证据标记')).toBeInTheDocument()
    expect(screen.getByText('恢复的旧证据标记')).toBeInTheDocument()
    expect(screen.queryByText('跳过的旧证据标记')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认执行' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '回滚计划' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('确认回滚记忆修正')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '确认回滚' }))
    expect(correction.rollbackPlan).toHaveBeenCalledWith('plan-1')
  })

  it('回滚中禁用回滚按钮，空级联预览不渲染联动区', () => {
    const plan = makePlan({
      status: 'executed',
      preview: {
        ...makePlan().preview,
        cascade_preview: { relations: [], entities: [], counts: undefined as never },
      },
    })
    renderCorrection({
      selectedPlan: plan,
      selectedPreview: plan.preview,
      rollingBackPlanId: 'plan-1',
    })
    expect(screen.getByRole('button', { name: '回滚计划' })).toBeDisabled()
    expect(screen.queryByText('联动影响')).not.toBeInTheDocument()
  })

  it('渲染多种计划状态并支持筛选、分页与刷新', async () => {
    const user = userEvent.setup()
    const plans = [
      makePlan({ plan_id: 'p-exec', status: 'executing', request_text: '' }),
      makePlan({ plan_id: 'p-rb', status: 'rolled_back', scope: 'weird_scope' as never }),
      makePlan({ plan_id: 'p-rbf', status: 'rollback_failed' }),
      makePlan({ plan_id: 'p-unknown', status: 'mystery' as never }),
      makePlan({ plan_id: 'p-empty', status: '' as never, scope: '' as never }),
    ]
    const { correction } = renderCorrection({
      plans,
      filteredPlans: plans,
      pagedPlans: plans,
      planPage: 2,
      planPageCount: 3,
      selectedPlan: plans[0],
    })

    expect(screen.getAllByText('执行中').length).toBeGreaterThan(0)
    expect(screen.getAllByText('已回滚').length).toBeGreaterThan(0)
    expect(screen.getAllByText('回滚失败').length).toBeGreaterThan(0)
    expect(screen.getByText('mystery')).toBeInTheDocument()
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0)
    expect(screen.getByText('weird_scope')).toBeInTheDocument()
    expect(screen.getByText('无修正内容')).toBeInTheDocument()
    expect(screen.getByText('当前命中 5 条记录')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索计划 / 人物 / 聊天流 / 原因'), { target: { value: '杭州' } })
    expect(correction.setPlanSearch).toHaveBeenCalledWith('杭州')

    const comboboxes = screen.getAllByRole('combobox')
    await user.click(comboboxes[1])
    await user.click(screen.getByRole('option', { name: '已执行' }))
    expect(correction.setPlanStatusFilter).toHaveBeenCalledWith('executed')
    await user.click(comboboxes[2])
    await user.click(screen.getByRole('option', { name: '人物画像' }))
    expect(correction.setPlanScopeFilter).toHaveBeenCalledWith('person_profile')

    await user.click(screen.getByRole('button', { name: '上一页' }))
    const prev = correction.setPlanPage.mock.calls[0][0] as (current: number) => number
    expect(prev(2)).toBe(1)
    expect(prev(1)).toBe(1)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    const next = correction.setPlanPage.mock.calls[1][0] as (current: number) => number
    expect(next(2)).toBe(3)
    expect(next(3)).toBe(3)

    await user.click(screen.getByText('p-rb'))
    expect(correction.setSelectedPlanId).toHaveBeenCalledWith('p-rb')
    await user.click(screen.getByRole('button', { name: '刷新' }))
    expect(correction.refreshPlans).toHaveBeenCalledOnce()
  })

  it('空筛选结果与首页/末页分页按钮禁用', () => {
    renderCorrection({
      plans: [makePlan()],
      filteredPlans: [],
      pagedPlans: [],
      planPage: 1,
      planPageCount: 1,
    })
    expect(screen.getByText('当前筛选条件下没有记忆修正计划')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })
})
