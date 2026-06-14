/**
 * useImportForm —— 长期记忆「导入表单」领域 hook（页面逻辑下沉的样板切片）。
 *
 * 收编导入任务创建相关的表单状态与提交逻辑：
 * - 表单参数（通用参数 15 项 + 7 种导入模式各自字段）以本地 state 维护；
 * - 导入设置（settings）/路径别名（path_aliases）/聊天流（chat-targets）走 useQuery，仅在面板激活时拉取；
 * - 服务端默认值在 settings 首次到达时 seed 一次进表单（渲染期版本标记模式，避免 effect 内 setState 级联）；
 * - 别名到达后，各模式 alias 字段为空时自动选第一个可用别名；
 * - submitImportByMode 按当前模式分派到 7 个 submit 函数，创建成功后回调 onCreated 刷新队列；
 * - 写失败弹全局 toast（与原页面一致）；路径解析读失败仅写入输出框。
 *
 * 与 useImportQueue 共享 settings 查询（同 queryKey 由 React Query 去重）。
 */
import { useCallback, useMemo, useState } from 'react'

import { useToast } from '@/hooks/use-toast'
import {
  createMemoryLpmmConvertImport,
  createMemoryLpmmOpenieImport,
  createMemoryMaibotMigrationImport,
  createMemoryPasteImport,
  createMemoryRawScanImport,
  createMemoryTemporalBackfillImport,
  createMemoryUploadImport,
  getMemoryImportChatTargets,
  getMemoryImportPathAliases,
  getMemoryImportSettings,
  resolveMemoryImportPath,
  type MemoryImportChatTargetPayload,
  type MemoryImportInputMode,
  type MemoryImportSettings,
  type MemoryImportTaskKind,
} from '@/lib/memory-api'
import { useQuery } from '@tanstack/react-query'

import {
  parseCommaSeparatedList,
  parseOptionalNonNegativeInt,
  parseOptionalPositiveInt,
} from '../utils'

const DATE_TIME_LOCAL_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/
const POSITIVE_INTEGER_PATTERN = /^[1-9]\d*$/

function parseMaibotPositiveInt(input: string, fieldName: string): number | undefined {
  const value = input.trim()
  if (!value) {
    return undefined
  }
  if (!POSITIVE_INTEGER_PATTERN.test(value)) {
    throw new Error(`${fieldName} 必须填写正整数`)
  }
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${fieldName} 超过可支持的整数范围`)
  }
  return parsed
}

function getMaibotDateTimeLocalTimestamp(input: string, fieldName: string): number | undefined {
  const value = input.trim()
  if (!value) {
    return undefined
  }
  if (!DATE_TIME_LOCAL_PATTERN.test(value)) {
    throw new Error(`${fieldName}格式无效，请使用时间选择器填写`)
  }
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) {
    throw new Error(`${fieldName}不是有效时间`)
  }
  return timestamp
}

function formatMaibotDateTimeLocalForApi(input: string, fieldName: string): string | undefined {
  const value = input.trim()
  if (!value) {
    return undefined
  }
  if (!DATE_TIME_LOCAL_PATTERN.test(value)) {
    throw new Error(`${fieldName}格式无效，请使用时间选择器填写`)
  }
  const date = new Date(value)
  const timestamp = date.getTime()
  if (!Number.isFinite(timestamp)) {
    throw new Error(`${fieldName}不是有效时间`)
  }
  return date.toISOString()
}

export interface UseImportFormOptions {
  /** 导入面板是否激活；非激活时不拉取设置/别名/聊天流 */
  active: boolean
  /** 创建任务成功后回调（由 useImportQueue.afterCreated 提供），刷新队列并选中新任务 */
  onCreated: (taskId: string) => Promise<void>
}

export interface UseImportFormResult {
  importCreateMode: MemoryImportTaskKind
  setImportCreateMode: React.Dispatch<React.SetStateAction<MemoryImportTaskKind>>
  importSettings: MemoryImportSettings
  importChatTargets: MemoryImportChatTargetPayload[]

  importCommonFileConcurrency: string
  setImportCommonFileConcurrency: React.Dispatch<React.SetStateAction<string>>
  importCommonChunkConcurrency: string
  setImportCommonChunkConcurrency: React.Dispatch<React.SetStateAction<string>>
  importCommonNarrativeWindowSize: string
  setImportCommonNarrativeWindowSize: React.Dispatch<React.SetStateAction<string>>
  importCommonNarrativeOverlap: string
  setImportCommonNarrativeOverlap: React.Dispatch<React.SetStateAction<string>>
  importCommonFactualTargetSize: string
  setImportCommonFactualTargetSize: React.Dispatch<React.SetStateAction<string>>
  importCommonLlmEnabled: boolean
  setImportCommonLlmEnabled: React.Dispatch<React.SetStateAction<boolean>>
  importCommonStrategyOverride: string
  setImportCommonStrategyOverride: React.Dispatch<React.SetStateAction<string>>
  importCommonDedupePolicy: string
  setImportCommonDedupePolicy: React.Dispatch<React.SetStateAction<string>>
  importCommonChatLog: boolean
  setImportCommonChatLog: React.Dispatch<React.SetStateAction<boolean>>
  importCommonChatId: string
  setImportCommonChatId: React.Dispatch<React.SetStateAction<string>>
  importCommonChatReferenceTime: string
  setImportCommonChatReferenceTime: React.Dispatch<React.SetStateAction<string>>
  importCommonForce: boolean
  setImportCommonForce: React.Dispatch<React.SetStateAction<boolean>>
  importCommonClearManifest: boolean
  setImportCommonClearManifest: React.Dispatch<React.SetStateAction<boolean>>

  uploadInputMode: MemoryImportInputMode
  setUploadInputMode: React.Dispatch<React.SetStateAction<MemoryImportInputMode>>
  uploadFiles: File[]
  setUploadFiles: React.Dispatch<React.SetStateAction<File[]>>

  pasteName: string
  setPasteName: React.Dispatch<React.SetStateAction<string>>
  pasteMode: MemoryImportInputMode
  setPasteMode: React.Dispatch<React.SetStateAction<MemoryImportInputMode>>
  pasteContent: string
  setPasteContent: React.Dispatch<React.SetStateAction<string>>

  rawAlias: string
  setRawAlias: React.Dispatch<React.SetStateAction<string>>
  rawInputMode: MemoryImportInputMode
  setRawInputMode: React.Dispatch<React.SetStateAction<MemoryImportInputMode>>
  rawRelativePath: string
  setRawRelativePath: React.Dispatch<React.SetStateAction<string>>
  rawGlob: string
  setRawGlob: React.Dispatch<React.SetStateAction<string>>
  rawRecursive: boolean
  setRawRecursive: React.Dispatch<React.SetStateAction<boolean>>

  openieAlias: string
  setOpenieAlias: React.Dispatch<React.SetStateAction<string>>
  openieRelativePath: string
  setOpenieRelativePath: React.Dispatch<React.SetStateAction<string>>
  openieIncludeAllJson: boolean
  setOpenieIncludeAllJson: React.Dispatch<React.SetStateAction<boolean>>

  convertAlias: string
  setConvertAlias: React.Dispatch<React.SetStateAction<string>>
  convertTargetAlias: string
  setConvertTargetAlias: React.Dispatch<React.SetStateAction<string>>
  convertRelativePath: string
  setConvertRelativePath: React.Dispatch<React.SetStateAction<string>>
  convertTargetRelativePath: string
  setConvertTargetRelativePath: React.Dispatch<React.SetStateAction<string>>
  convertDimension: string
  setConvertDimension: React.Dispatch<React.SetStateAction<string>>
  convertBatchSize: string
  setConvertBatchSize: React.Dispatch<React.SetStateAction<string>>

  backfillAlias: string
  setBackfillAlias: React.Dispatch<React.SetStateAction<string>>
  backfillLimit: string
  setBackfillLimit: React.Dispatch<React.SetStateAction<string>>
  backfillRelativePath: string
  setBackfillRelativePath: React.Dispatch<React.SetStateAction<string>>
  backfillDryRun: boolean
  setBackfillDryRun: React.Dispatch<React.SetStateAction<boolean>>
  backfillNoCreatedFallback: boolean
  setBackfillNoCreatedFallback: React.Dispatch<React.SetStateAction<boolean>>

  maibotSourceDb: string
  setMaibotSourceDb: React.Dispatch<React.SetStateAction<string>>
  maibotTimeFrom: string
  setMaibotTimeFrom: React.Dispatch<React.SetStateAction<string>>
  maibotTimeTo: string
  setMaibotTimeTo: React.Dispatch<React.SetStateAction<string>>
  maibotStartId: string
  setMaibotStartId: React.Dispatch<React.SetStateAction<string>>
  maibotEndId: string
  setMaibotEndId: React.Dispatch<React.SetStateAction<string>>
  maibotStreamIds: string
  setMaibotStreamIds: React.Dispatch<React.SetStateAction<string>>
  maibotGroupIds: string
  setMaibotGroupIds: React.Dispatch<React.SetStateAction<string>>
  maibotUserIds: string
  setMaibotUserIds: React.Dispatch<React.SetStateAction<string>>
  maibotReadBatchSize: string
  setMaibotReadBatchSize: React.Dispatch<React.SetStateAction<string>>
  maibotCommitWindowRows: string
  setMaibotCommitWindowRows: React.Dispatch<React.SetStateAction<string>>
  maibotEmbedWorkers: string
  setMaibotEmbedWorkers: React.Dispatch<React.SetStateAction<string>>
  maibotNoResume: boolean
  setMaibotNoResume: React.Dispatch<React.SetStateAction<boolean>>
  maibotResetState: boolean
  setMaibotResetState: React.Dispatch<React.SetStateAction<boolean>>
  maibotDryRun: boolean
  setMaibotDryRun: React.Dispatch<React.SetStateAction<boolean>>
  maibotVerifyOnly: boolean
  setMaibotVerifyOnly: React.Dispatch<React.SetStateAction<boolean>>

  submitImportByMode: () => Promise<void>
  creatingImport: boolean
  /** 构建公共导入参数载荷，供队列重试（retry overrides）复用当前表单参数 */
  buildCommonImportPayload: () => Record<string, unknown>

  pathResolveAlias: string
  setPathResolveAlias: React.Dispatch<React.SetStateAction<string>>
  importAliasKeys: string[]
  pathResolveRelativePath: string
  setPathResolveRelativePath: React.Dispatch<React.SetStateAction<string>>
  pathResolveMustExist: boolean
  setPathResolveMustExist: React.Dispatch<React.SetStateAction<boolean>>
  resolveImportPath: () => Promise<void>
  resolvingPath: boolean
  pathResolveOutput: string
}

export function useImportForm({ active, onCreated }: UseImportFormOptions): UseImportFormResult {
  const { toast } = useToast()

  const [importCreateMode, setImportCreateMode] = useState<MemoryImportTaskKind>('upload')
  const [creatingImport, setCreatingImport] = useState(false)

  // 通用导入参数
  const [importCommonFileConcurrency, setImportCommonFileConcurrency] = useState('2')
  const [importCommonChunkConcurrency, setImportCommonChunkConcurrency] = useState('4')
  const [importCommonNarrativeWindowSize, setImportCommonNarrativeWindowSize] = useState('1600')
  const [importCommonNarrativeOverlap, setImportCommonNarrativeOverlap] = useState('400')
  const [importCommonFactualTargetSize, setImportCommonFactualTargetSize] = useState('1200')
  const [importCommonLlmEnabled, setImportCommonLlmEnabled] = useState(true)
  const [importCommonStrategyOverride, setImportCommonStrategyOverride] = useState('auto')
  const [importCommonDedupePolicy, setImportCommonDedupePolicy] = useState('content_hash')
  const [importCommonChatLog, setImportCommonChatLog] = useState(false)
  const [importCommonChatId, setImportCommonChatId] = useState('')
  const [importCommonChatReferenceTime, setImportCommonChatReferenceTime] = useState('')
  const [importCommonForce, setImportCommonForce] = useState(false)
  const [importCommonClearManifest, setImportCommonClearManifest] = useState(false)

  const [uploadInputMode, setUploadInputMode] = useState<MemoryImportInputMode>('text')
  const [uploadFiles, setUploadFiles] = useState<File[]>([])

  const [pasteName, setPasteName] = useState('')
  const [pasteMode, setPasteMode] = useState<MemoryImportInputMode>('text')
  const [pasteContent, setPasteContent] = useState('')

  const [rawAlias, setRawAlias] = useState('raw')
  const [rawRelativePath, setRawRelativePath] = useState('')
  const [rawGlob, setRawGlob] = useState('*')
  const [rawInputMode, setRawInputMode] = useState<MemoryImportInputMode>('text')
  const [rawRecursive, setRawRecursive] = useState(true)

  const [openieAlias, setOpenieAlias] = useState('lpmm')
  const [openieRelativePath, setOpenieRelativePath] = useState('')
  const [openieIncludeAllJson, setOpenieIncludeAllJson] = useState(false)

  const [convertAlias, setConvertAlias] = useState('lpmm')
  const [convertRelativePath, setConvertRelativePath] = useState('')
  const [convertTargetAlias, setConvertTargetAlias] = useState('plugin_data')
  const [convertTargetRelativePath, setConvertTargetRelativePath] = useState('')
  const [convertDimension, setConvertDimension] = useState('')
  const [convertBatchSize, setConvertBatchSize] = useState('1024')

  const [backfillAlias, setBackfillAlias] = useState('plugin_data')
  const [backfillRelativePath, setBackfillRelativePath] = useState('')
  const [backfillLimit, setBackfillLimit] = useState('100000')
  const [backfillDryRun, setBackfillDryRun] = useState(false)
  const [backfillNoCreatedFallback, setBackfillNoCreatedFallback] = useState(false)

  const [maibotSourceDb, setMaibotSourceDb] = useState('')
  const [maibotTimeFrom, setMaibotTimeFrom] = useState('')
  const [maibotTimeTo, setMaibotTimeTo] = useState('')
  const [maibotStartId, setMaibotStartId] = useState('')
  const [maibotEndId, setMaibotEndId] = useState('')
  const [maibotStreamIds, setMaibotStreamIds] = useState('')
  const [maibotGroupIds, setMaibotGroupIds] = useState('')
  const [maibotUserIds, setMaibotUserIds] = useState('')
  const [maibotReadBatchSize, setMaibotReadBatchSize] = useState('2000')
  const [maibotCommitWindowRows, setMaibotCommitWindowRows] = useState('20000')
  const [maibotEmbedWorkers, setMaibotEmbedWorkers] = useState('')
  const [maibotNoResume, setMaibotNoResume] = useState(false)
  const [maibotResetState, setMaibotResetState] = useState(false)
  const [maibotDryRun, setMaibotDryRun] = useState(false)
  const [maibotVerifyOnly, setMaibotVerifyOnly] = useState(false)

  const [pathResolveAlias, setPathResolveAlias] = useState('raw')
  const [pathResolveRelativePath, setPathResolveRelativePath] = useState('')
  const [pathResolveMustExist, setPathResolveMustExist] = useState(true)
  const [pathResolveOutput, setPathResolveOutput] = useState('')
  const [resolvingPath, setResolvingPath] = useState(false)

  // 导入设置 / 路径别名 / 聊天流：仅在面板激活时拉取；settings 与 useImportQueue 共享查询缓存
  const settingsQuery = useQuery({
    queryKey: ['memory-import', 'settings'],
    queryFn: () => getMemoryImportSettings(),
    enabled: active,
  })
  const pathAliasesQuery = useQuery({
    queryKey: ['memory-import', 'path-aliases'],
    queryFn: () => getMemoryImportPathAliases(),
    enabled: active,
  })
  const chatTargetsQuery = useQuery({
    queryKey: ['memory-import', 'chat-targets'],
    queryFn: () => getMemoryImportChatTargets(),
    enabled: active,
  })

  const importSettings: MemoryImportSettings = settingsQuery.data?.settings ?? {}
  const importPathAliases = useMemo(
    () => pathAliasesQuery.data?.path_aliases ?? {},
    [pathAliasesQuery.data?.path_aliases],
  )
  const importChatTargets = useMemo(
    () => chatTargetsQuery.data?.data ?? [],
    [chatTargetsQuery.data?.data],
  )

  const importAliasKeys = useMemo(
    () => Object.keys(importPathAliases).sort((left, right) => left.localeCompare(right)),
    [importPathAliases],
  )

  // 服务端默认值 seed：settings 首次到达时按默认值回填通用参数与 maibot 源库一次。
  // 用「渲染期版本标记」模式（React 官方推荐）替代 effect 内 setState，避免级联渲染告警。
  const settingsVersion = settingsQuery.data !== undefined ? String(settingsQuery.dataUpdatedAt) : null
  const [seededSettingsVersion, setSeededSettingsVersion] = useState<string | null>(null)
  if (settingsVersion !== null && settingsVersion !== seededSettingsVersion) {
    setSeededSettingsVersion(settingsVersion)

    const defaultFileConcurrency = String(importSettings.default_file_concurrency ?? '').trim()
    const defaultChunkConcurrency = String(importSettings.default_chunk_concurrency ?? '').trim()
    const defaultNarrativeWindowSize = String(importSettings.default_narrative_window_size ?? '').trim()
    const defaultNarrativeOverlap = String(importSettings.default_narrative_overlap ?? '').trim()
    const defaultFactualTargetSize = String(importSettings.default_factual_target_size ?? '').trim()
    const defaultSourceDb = String(importSettings.maibot_source_db_default ?? '').trim()

    if (defaultFileConcurrency) {
      setImportCommonFileConcurrency((current) => (current === '2' ? defaultFileConcurrency : current))
    }
    if (defaultChunkConcurrency) {
      setImportCommonChunkConcurrency((current) => (current === '4' ? defaultChunkConcurrency : current))
    }
    if (defaultNarrativeWindowSize) {
      setImportCommonNarrativeWindowSize((current) => (current === '1600' ? defaultNarrativeWindowSize : current))
    }
    if (defaultNarrativeOverlap) {
      setImportCommonNarrativeOverlap((current) => (current === '400' ? defaultNarrativeOverlap : current))
    }
    if (defaultFactualTargetSize) {
      setImportCommonFactualTargetSize((current) => (current === '1200' ? defaultFactualTargetSize : current))
    }
    if (defaultSourceDb) {
      setMaibotSourceDb((current) => (current.trim() ? current : defaultSourceDb))
    }
  }

  // 别名联动：别名到达后，各模式 alias 字段为空或不在可用列表中时自动选第一个可用别名。
  // 同样用「渲染期版本标记」模式，避免 effect 内 setState 级联。
  const aliasVersion = importAliasKeys.length > 0 ? importAliasKeys.join('|') : null
  const [linkedAliasVersion, setLinkedAliasVersion] = useState<string | null>(null)
  if (aliasVersion !== null && aliasVersion !== linkedAliasVersion) {
    setLinkedAliasVersion(aliasVersion)
    const pickAlias = (current: string, preferred: string): string => {
      if (current && importAliasKeys.includes(current)) {
        return current
      }
      if (importAliasKeys.includes(preferred)) {
        return preferred
      }
      return importAliasKeys[0]
    }
    setRawAlias((current) => pickAlias(current, 'raw'))
    setOpenieAlias((current) => pickAlias(current, 'lpmm'))
    setConvertAlias((current) => pickAlias(current, 'lpmm'))
    setConvertTargetAlias((current) => pickAlias(current, 'plugin_data'))
    setBackfillAlias((current) => pickAlias(current, 'plugin_data'))
    setPathResolveAlias((current) => pickAlias(current, 'raw'))
  }

  const buildCommonImportPayload = useCallback((): Record<string, unknown> => {
    const chatId = importCommonChatId.trim()
    const payload: Record<string, unknown> = {
      llm_enabled: importCommonLlmEnabled,
      strategy_override: importCommonStrategyOverride,
      dedupe_policy: importCommonDedupePolicy,
      chat_log: importCommonChatLog,
      force: importCommonForce,
      clear_manifest: importCommonClearManifest,
    }

    const fileConcurrency = parseOptionalPositiveInt(importCommonFileConcurrency)
    const chunkConcurrency = parseOptionalPositiveInt(importCommonChunkConcurrency)
    const narrativeWindowSize = parseOptionalPositiveInt(importCommonNarrativeWindowSize)
    const narrativeOverlap = parseOptionalNonNegativeInt(importCommonNarrativeOverlap)
    const factualTargetSize = parseOptionalPositiveInt(importCommonFactualTargetSize)
    if (fileConcurrency !== undefined) {
      payload.file_concurrency = fileConcurrency
    }
    if (chunkConcurrency !== undefined) {
      payload.chunk_concurrency = chunkConcurrency
    }
    if (narrativeWindowSize !== undefined) {
      payload.narrative_window_size = narrativeWindowSize
    }
    if (narrativeOverlap !== undefined) {
      payload.narrative_overlap = narrativeOverlap
    }
    if (factualTargetSize !== undefined) {
      payload.factual_target_size = factualTargetSize
    }
    if (importCommonChatReferenceTime.trim()) {
      payload.chat_reference_time = importCommonChatReferenceTime.trim()
    }
    if (chatId) {
      payload.chat_id = chatId
    }
    return payload
  }, [
    importCommonChatId,
    importCommonChatLog,
    importCommonChatReferenceTime,
    importCommonChunkConcurrency,
    importCommonClearManifest,
    importCommonDedupePolicy,
    importCommonFactualTargetSize,
    importCommonFileConcurrency,
    importCommonForce,
    importCommonLlmEnabled,
    importCommonNarrativeOverlap,
    importCommonNarrativeWindowSize,
    importCommonStrategyOverride,
  ])

  const submitUploadImport = useCallback(async () => {
    if (uploadFiles.length <= 0) {
      toast({
        title: '请选择上传文件',
        description: '至少选择一个 txt/md/json 文件后再提交',
        variant: 'destructive',
      })
      return
    }
    try {
      setCreatingImport(true)
      const payload = {
        ...buildCommonImportPayload(),
        input_mode: uploadInputMode,
      }
      const result = await createMemoryUploadImport(uploadFiles, payload)
      if (!result.success) {
        throw new Error(result.error || '创建上传导入任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      setUploadFiles([])
      await onCreated(taskId)
      toast({
        title: '上传导入任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建上传导入任务失败'
      toast({
        title: '创建上传导入任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [buildCommonImportPayload, onCreated, toast, uploadFiles, uploadInputMode])

  const submitPasteImport = useCallback(async () => {
    if (!pasteContent.trim()) {
      toast({
        title: '粘贴内容不能为空',
        description: '请填写导入内容后再提交',
        variant: 'destructive',
      })
      return
    }
    try {
      setCreatingImport(true)
      const result = await createMemoryPasteImport({
        ...buildCommonImportPayload(),
        name: pasteName || undefined,
        content: pasteContent,
        input_mode: pasteMode,
      })
      if (!result.success) {
        throw new Error(result.error || '创建粘贴导入任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      setPasteContent('')
      setPasteName('')
      await onCreated(taskId)
      toast({
        title: '粘贴导入任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建粘贴导入任务失败'
      toast({
        title: '创建粘贴导入任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [buildCommonImportPayload, onCreated, pasteContent, pasteMode, pasteName, toast])

  const submitRawScanImport = useCallback(async () => {
    try {
      setCreatingImport(true)
      const result = await createMemoryRawScanImport({
        ...buildCommonImportPayload(),
        alias: rawAlias,
        relative_path: rawRelativePath,
        glob: rawGlob,
        recursive: rawRecursive,
        input_mode: rawInputMode,
      })
      if (!result.success) {
        throw new Error(result.error || '创建本地扫描任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      await onCreated(taskId)
      toast({
        title: '本地扫描任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建本地扫描任务失败'
      toast({
        title: '创建本地扫描任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [buildCommonImportPayload, onCreated, rawAlias, rawGlob, rawInputMode, rawRecursive, rawRelativePath, toast])

  const submitOpenieImport = useCallback(async () => {
    try {
      setCreatingImport(true)
      const result = await createMemoryLpmmOpenieImport({
        ...buildCommonImportPayload(),
        alias: openieAlias,
        relative_path: openieRelativePath,
        include_all_json: openieIncludeAllJson,
      })
      if (!result.success) {
        throw new Error(result.error || '创建 LPMM OpenIE 任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      await onCreated(taskId)
      toast({
        title: 'LPMM OpenIE 任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建 LPMM OpenIE 任务失败'
      toast({
        title: '创建 LPMM OpenIE 任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [buildCommonImportPayload, onCreated, openieAlias, openieIncludeAllJson, openieRelativePath, toast])

  const submitConvertImport = useCallback(async () => {
    try {
      setCreatingImport(true)
      const result = await createMemoryLpmmConvertImport({
        alias: convertAlias,
        relative_path: convertRelativePath,
        target_alias: convertTargetAlias,
        target_relative_path: convertTargetRelativePath,
        dimension: parseOptionalPositiveInt(convertDimension),
        batch_size: parseOptionalPositiveInt(convertBatchSize),
      })
      if (!result.success) {
        throw new Error(result.error || '创建 LPMM 转换任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      await onCreated(taskId)
      toast({
        title: 'LPMM 转换任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建 LPMM 转换任务失败'
      toast({
        title: '创建 LPMM 转换任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [
    convertAlias,
    convertBatchSize,
    convertDimension,
    convertRelativePath,
    convertTargetAlias,
    convertTargetRelativePath,
    onCreated,
    toast,
  ])

  const submitBackfillImport = useCallback(async () => {
    try {
      setCreatingImport(true)
      const result = await createMemoryTemporalBackfillImport({
        alias: backfillAlias,
        relative_path: backfillRelativePath,
        limit: parseOptionalPositiveInt(backfillLimit),
        dry_run: backfillDryRun,
        no_created_fallback: backfillNoCreatedFallback,
      })
      if (!result.success) {
        throw new Error(result.error || '创建时序回填任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      await onCreated(taskId)
      toast({
        title: '时序回填任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建时序回填任务失败'
      toast({
        title: '创建时序回填任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [
    backfillAlias,
    backfillDryRun,
    backfillLimit,
    backfillNoCreatedFallback,
    backfillRelativePath,
    onCreated,
    toast,
  ])

  const submitMaibotMigrationImport = useCallback(async () => {
    try {
      setCreatingImport(true)
      const sourceDb = maibotSourceDb.trim()
      if (!sourceDb) {
        throw new Error('请填写源数据库路径')
      }
      const timeFromTimestamp = getMaibotDateTimeLocalTimestamp(maibotTimeFrom, '起始时间')
      const timeToTimestamp = getMaibotDateTimeLocalTimestamp(maibotTimeTo, '结束时间')
      if (
        timeFromTimestamp !== undefined &&
        timeToTimestamp !== undefined &&
        timeFromTimestamp > timeToTimestamp
      ) {
        throw new Error('起始时间不能晚于结束时间')
      }
      const startId = parseMaibotPositiveInt(maibotStartId, '起始 ID')
      const endId = parseMaibotPositiveInt(maibotEndId, '结束 ID')
      if (startId !== undefined && endId !== undefined && startId > endId) {
        throw new Error('起始 ID 不能大于结束 ID')
      }
      const result = await createMemoryMaibotMigrationImport({
        source_db: sourceDb,
        time_from: formatMaibotDateTimeLocalForApi(maibotTimeFrom, '起始时间'),
        time_to: formatMaibotDateTimeLocalForApi(maibotTimeTo, '结束时间'),
        start_id: startId,
        end_id: endId,
        stream_ids: parseCommaSeparatedList(maibotStreamIds),
        group_ids: parseCommaSeparatedList(maibotGroupIds),
        user_ids: parseCommaSeparatedList(maibotUserIds),
        read_batch_size: parseMaibotPositiveInt(maibotReadBatchSize, '读取批大小'),
        commit_window_rows: parseMaibotPositiveInt(maibotCommitWindowRows, '提交窗口行数'),
        embed_workers: parseMaibotPositiveInt(maibotEmbedWorkers, '向量线程数'),
        no_resume: maibotNoResume,
        reset_state: maibotResetState,
        dry_run: maibotDryRun,
        verify_only: maibotVerifyOnly,
      })
      if (!result.success) {
        throw new Error(result.error || '创建 MaiBot 迁移任务失败')
      }
      const taskId = String(result.task?.task_id ?? '')
      await onCreated(taskId)
      toast({
        title: 'MaiBot 迁移任务已创建',
        description: taskId ? `任务 ${taskId.slice(0, 12)} 已加入导入队列` : '导入任务已加入队列',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建 MaiBot 迁移任务失败'
      toast({
        title: '创建 MaiBot 迁移任务失败',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setCreatingImport(false)
    }
  }, [
    maibotCommitWindowRows,
    maibotDryRun,
    maibotEmbedWorkers,
    maibotEndId,
    maibotGroupIds,
    maibotNoResume,
    maibotReadBatchSize,
    maibotResetState,
    maibotSourceDb,
    maibotStartId,
    maibotStreamIds,
    maibotTimeFrom,
    maibotTimeTo,
    maibotUserIds,
    maibotVerifyOnly,
    onCreated,
    toast,
  ])

  const submitImportByMode = useCallback(async () => {
    if (creatingImport) {
      return
    }
    switch (importCreateMode) {
      case 'upload':
        await submitUploadImport()
        break
      case 'paste':
        await submitPasteImport()
        break
      case 'raw_scan':
        await submitRawScanImport()
        break
      case 'lpmm_openie':
        await submitOpenieImport()
        break
      case 'lpmm_convert':
        await submitConvertImport()
        break
      case 'temporal_backfill':
        await submitBackfillImport()
        break
      case 'maibot_migration':
        await submitMaibotMigrationImport()
        break
      default:
        break
    }
  }, [
    creatingImport,
    importCreateMode,
    submitBackfillImport,
    submitConvertImport,
    submitMaibotMigrationImport,
    submitOpenieImport,
    submitPasteImport,
    submitRawScanImport,
    submitUploadImport,
  ])

  const resolveImportPath = useCallback(async () => {
    if (!pathResolveAlias.trim()) {
      return
    }
    try {
      setResolvingPath(true)
      const payload = await resolveMemoryImportPath({
        alias: pathResolveAlias,
        relative_path: pathResolveRelativePath,
        must_exist: pathResolveMustExist,
      })
      const lines = [
        `路径别名: ${payload.alias}`,
        `相对路径: ${payload.relative_path || '(空)'}`,
        `解析结果: ${payload.resolved_path}`,
        `是否存在: ${String(payload.exists)}`,
        `是否文件: ${String(payload.is_file)}`,
        `是否目录: ${String(payload.is_dir)}`,
      ]
      setPathResolveOutput(lines.join('\n'))
    } catch (error) {
      const message = error instanceof Error ? error.message : '路径解析失败'
      setPathResolveOutput(`解析失败：${message}`)
    } finally {
      setResolvingPath(false)
    }
  }, [pathResolveAlias, pathResolveMustExist, pathResolveRelativePath])

  // importErrorText 由各 submit 在写失败时写入；保留引用以便后续扩展（当前由 toast 主要呈现）

  return {
    importCreateMode,
    setImportCreateMode,
    importSettings,
    importChatTargets,
    importCommonFileConcurrency,
    setImportCommonFileConcurrency,
    importCommonChunkConcurrency,
    setImportCommonChunkConcurrency,
    importCommonNarrativeWindowSize,
    setImportCommonNarrativeWindowSize,
    importCommonNarrativeOverlap,
    setImportCommonNarrativeOverlap,
    importCommonFactualTargetSize,
    setImportCommonFactualTargetSize,
    importCommonLlmEnabled,
    setImportCommonLlmEnabled,
    importCommonStrategyOverride,
    setImportCommonStrategyOverride,
    importCommonDedupePolicy,
    setImportCommonDedupePolicy,
    importCommonChatLog,
    setImportCommonChatLog,
    importCommonChatId,
    setImportCommonChatId,
    importCommonChatReferenceTime,
    setImportCommonChatReferenceTime,
    importCommonForce,
    setImportCommonForce,
    importCommonClearManifest,
    setImportCommonClearManifest,
    uploadInputMode,
    setUploadInputMode,
    uploadFiles,
    setUploadFiles,
    pasteName,
    setPasteName,
    pasteMode,
    setPasteMode,
    pasteContent,
    setPasteContent,
    rawAlias,
    setRawAlias,
    rawInputMode,
    setRawInputMode,
    rawRelativePath,
    setRawRelativePath,
    rawGlob,
    setRawGlob,
    rawRecursive,
    setRawRecursive,
    openieAlias,
    setOpenieAlias,
    openieRelativePath,
    setOpenieRelativePath,
    openieIncludeAllJson,
    setOpenieIncludeAllJson,
    convertAlias,
    setConvertAlias,
    convertTargetAlias,
    setConvertTargetAlias,
    convertRelativePath,
    setConvertRelativePath,
    convertTargetRelativePath,
    setConvertTargetRelativePath,
    convertDimension,
    setConvertDimension,
    convertBatchSize,
    setConvertBatchSize,
    backfillAlias,
    setBackfillAlias,
    backfillLimit,
    setBackfillLimit,
    backfillRelativePath,
    setBackfillRelativePath,
    backfillDryRun,
    setBackfillDryRun,
    backfillNoCreatedFallback,
    setBackfillNoCreatedFallback,
    maibotSourceDb,
    setMaibotSourceDb,
    maibotTimeFrom,
    setMaibotTimeFrom,
    maibotTimeTo,
    setMaibotTimeTo,
    maibotStartId,
    setMaibotStartId,
    maibotEndId,
    setMaibotEndId,
    maibotStreamIds,
    setMaibotStreamIds,
    maibotGroupIds,
    setMaibotGroupIds,
    maibotUserIds,
    setMaibotUserIds,
    maibotReadBatchSize,
    setMaibotReadBatchSize,
    maibotCommitWindowRows,
    setMaibotCommitWindowRows,
    maibotEmbedWorkers,
    setMaibotEmbedWorkers,
    maibotNoResume,
    setMaibotNoResume,
    maibotResetState,
    setMaibotResetState,
    maibotDryRun,
    setMaibotDryRun,
    maibotVerifyOnly,
    setMaibotVerifyOnly,
    submitImportByMode,
    creatingImport,
    buildCommonImportPayload,
    pathResolveAlias,
    setPathResolveAlias,
    importAliasKeys,
    pathResolveRelativePath,
    setPathResolveRelativePath,
    pathResolveMustExist,
    setPathResolveMustExist,
    resolveImportPath,
    resolvingPath,
    pathResolveOutput,
  }
}
