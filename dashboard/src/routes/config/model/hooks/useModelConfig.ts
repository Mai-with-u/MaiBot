/**
 * useModelConfig —— Model 配置页面核心领域 hook（页面逻辑下沉）。
 *
 * 把 model.tsx 的状态机整体收编：models / apiProviders / model_task_config 三份「可编辑草稿」，
 * 以及围绕它们的加载（loadConfig）、手动保存（saveConfig）、模型/提供商 CRUD、搜索/分页/批量、
 * 表单校验、任务配置问题检查、提供商连接测试、提供商删除级联移除关联模型等全部编排。
 *
 * 设计判断：
 * - 不引入 useQuery —— 这三份是加载进本地态、autosave 回写的草稿，不是只读服务端态；
 *   保留「加载→本地草稿」与 updateModelConfig/Section 写回。
 * - models / providers / taskConfig / save 高度耦合（provider 删除连带删模型跨三份状态、
 *   手动保存需要三者），故合并为一个核心 hook，避免互相回调的脆弱接口。
 * - embedding 换模型警告单独收进 useEmbeddingWarning（usePendingOperation 包装），
 *   本 hook 通过 applyEmbeddingUpdate / detectChange 与其协调。
 */
import { createElement, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ToastAction, type ToastActionElement } from '@/components/ui/toast'
import {
  createModelConfigVersion,
  deleteModelConfigVersion,
  getModelConfig,
  getModelConfigCached,
  getModelConfigSchema,
  getModelConfigVersions,
  switchModelConfigVersion,
  testModelCapability,
  testProviderConnection,
  updateModelConfig,
  updateModelConfigSection,
} from '@/lib/config-api'
import type {
  ModelConfigVersionInfo,
  ModelTestResult,
  TestConnectionResult,
} from '@/lib/config-api'
import { useToast } from '@/hooks/use-toast'
import type { ConfigSchema } from '@/types/config-schema'

import type { ModelInfo, ModelTaskConfig, ProviderConfig, TaskConfig } from '../types'
import type { APIProvider, DeleteConfirmState } from '../../modelProvider/types'
import { cleanProviderData } from '../../modelProvider/utils'
import { findTemplateByBaseUrl } from '../../providerTemplates'
import { useModelAutoSave } from './useModelAutoSave'
import { useEmbeddingWarning, type PendingEmbeddingUpdate } from './useEmbeddingWarning'

const ADVANCED_MODEL_TASK_NAMES = new Set(['memory', 'learner', 'emoji', 'voice'])

/** Unwrap backend `{ success, config }` envelope to get the actual config */
function unwrapModelConfig(data: unknown): Record<string, unknown> {
  if (data && typeof data === 'object' && 'config' in data) {
    return (data as { config: Record<string, unknown> }).config
  }
  return data as Record<string, unknown>
}

function getRequiredTaskNames(schema: ConfigSchema | null): Set<string> {
  return new Set(
    (schema?.fields ?? [])
      .filter(
        (field) =>
          field.type === 'object' && !field.advanced && !ADVANCED_MODEL_TASK_NAMES.has(field.name)
      )
      .map((field) => field.name)
  )
}

/** 表单验证错误 */
export interface ModelFormErrors {
  name?: string
  api_provider?: string
  model_identifier?: string
}

interface ProviderSaveBarrierCheckpoint {
  generation: number
  sourceSnapshot: string
  targetSnapshot: string
}

interface ConfigDraftPersistResult {
  applyModels: boolean
  applyProviders: boolean
  applyTaskConfig: boolean
}

export function useModelConfig() {
  const { toast } = useToast()

  // ---- 三份可编辑草稿 + 派生态 ----
  const [models, setModels] = useState<ModelInfo[]>([])
  const [providers, setProviders] = useState<string[]>([])
  const [providerConfigs, setProviderConfigs] = useState<ProviderConfig[]>([])
  const [apiProviders, setApiProviders] = useState<APIProvider[]>([])
  const [modelNames, setModelNames] = useState<string[]>([])
  const [taskConfig, setTaskConfig] = useState<ModelTaskConfig | null>(null)

  // ---- 加载 / 保存状态 ----
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [modelAutoSaving, setModelAutoSaving] = useState(false)
  const [providerAutoSaving, setProviderAutoSaving] = useState(false)
  const [modelHasUnsavedChanges, setModelHasUnsavedChanges] = useState(false)
  const [providerHasUnsavedChanges, setProviderHasUnsavedChanges] = useState(false)
  const autoSaving = modelAutoSaving || providerAutoSaving
  const hasUnsavedChanges = modelHasUnsavedChanges || providerHasUnsavedChanges

  // ---- 模型配置文件副本 ----
  const [activeConfigVersion, setActiveConfigVersion] = useState<ModelConfigVersionInfo | null>(
    null
  )
  const [configVersions, setConfigVersions] = useState<ModelConfigVersionInfo[]>([])
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [switchingConfigVersion, setSwitchingConfigVersion] = useState<string | null>(null)
  const [creatingConfigVersion, setCreatingConfigVersion] = useState(false)

  // ---- 模型编辑对话框 ----
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<ModelInfo | null>(null)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [formErrors, setFormErrors] = useState<ModelFormErrors>({})

  // ---- 提供商编辑对话框 ----
  const [providerDialogOpen, setProviderDialogOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<APIProvider | null>(null)
  const [editingProviderIndex, setEditingProviderIndex] = useState<number | null>(null)
  const [providerDeleteDialogOpen, setProviderDeleteDialogOpen] = useState(false)
  const [deletingProviderIndex, setDeletingProviderIndex] = useState<number | null>(null)

  // ---- 搜索 / 分页 / 批量选择 ----
  const [searchQuery, setSearchQuery] = useState('')
  const [modelProviderFilter, setModelProviderFilter] = useState('')
  const [selectedModels, setSelectedModels] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')

  // ---- 提供商连接测试 ----
  const [testingProviders, setTestingProviders] = useState<Set<string>>(new Set())
  const [testResults, setTestResults] = useState<Map<string, TestConnectionResult>>(new Map())

  // ---- 单模型能力测试 ----
  const [testingModels, setTestingModels] = useState<Set<string>>(new Set())
  const [modelTestResults, setModelTestResults] = useState<Map<string, ModelTestResult>>(new Map())
  const [selectedModelTestResult, setSelectedModelTestResult] = useState<ModelTestResult | null>(
    null
  )

  const buildModelTestDetailAction = useCallback(
    (testResult: ModelTestResult): ToastActionElement =>
      createElement(
        ToastAction,
        {
          altText: '查看模型测试详情',
          onClick: () => setSelectedModelTestResult(testResult),
        },
        '详情'
      ) as unknown as ToastActionElement,
    []
  )

  // ---- 提供商删除级联确认 ----
  const [deleteConfirmState, setDeleteConfirmState] = useState<DeleteConfirmState>({
    isOpen: false,
    providersToDelete: [],
    affectedModels: [],
    pendingProviders: [],
    context: 'auto',
    oldProviders: [],
  })

  // ---- schema / 任务配置问题检查 ----
  const [taskConfigSchema, setTaskConfigSchema] = useState<ConfigSchema | null>(null)
  const taskConfigSchemaRef = useRef<ConfigSchema | null>(null)
  const [invalidModelRefs, setInvalidModelRefs] = useState<
    { taskName: string; invalidModels: string[] }[]
  >([])
  const [emptyTasks, setEmptyTasks] = useState<string[]>([])

  // ---- provider 自动保存定时器 / 快照 ----
  const providerAutoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const providersSnapshotRef = useRef<string | null>(null)
  const latestProvidersSnapshotRef = useRef('')
  const providerGenerationRef = useRef(0)
  const providerSaveCountRef = useRef(0)
  const configWriteChainRef = useRef<Promise<void>>(Promise.resolve())
  latestProvidersSnapshotRef.current = JSON.stringify(apiProviders.map(cleanProviderData))

  const enqueueConfigWrite = useCallback((operation: () => Promise<void>): Promise<void> => {
    const operationPromise = configWriteChainRef.current.then(operation)
    // 单次写入失败不能阻断后续保存，但调用方仍会收到本次失败。
    configWriteChainRef.current = operationPromise.catch(() => undefined)
    return operationPromise
  }, [])

  // 自动保存 models / taskConfig（沿用既有 hook）
  const {
    cancelPendingTimers: cancelModelAutoSaveTimers,
    commitSaveBarrier,
    initialLoadRef,
    prepareSaveBarrier,
    resetSnapshots,
  } = useModelAutoSave({
    models,
    taskConfig,
    enqueueWrite: enqueueConfigWrite,
    onSavingChange: setModelAutoSaving,
    onUnsavedChange: setModelHasUnsavedChanges,
  })

  const cancelProviderAutoSaveTimer = useCallback(() => {
    if (providerAutoSaveTimerRef.current) {
      clearTimeout(providerAutoSaveTimerRef.current)
      providerAutoSaveTimerRef.current = null
    }
  }, [])

  const prepareProviderSaveBarrier = useCallback(
    (nextProviders: APIProvider[]): ProviderSaveBarrierCheckpoint => {
      cancelProviderAutoSaveTimer()
      return {
        generation: providerGenerationRef.current,
        sourceSnapshot: latestProvidersSnapshotRef.current,
        targetSnapshot: JSON.stringify(nextProviders.map(cleanProviderData)),
      }
    },
    [cancelProviderAutoSaveTimer]
  )

  const commitProviderSaveBarrier = useCallback(
    (checkpoint: ProviderSaveBarrierCheckpoint): boolean => {
      providersSnapshotRef.current = checkpoint.targetSnapshot
      const applyProviders =
        checkpoint.generation === providerGenerationRef.current &&
        checkpoint.sourceSnapshot === latestProvidersSnapshotRef.current

      if (applyProviders) {
        latestProvidersSnapshotRef.current = checkpoint.targetSnapshot
      }
      setProviderHasUnsavedChanges(latestProvidersSnapshotRef.current !== checkpoint.targetSnapshot)
      return applyProviders
    },
    []
  )

  const updateProviderSavingCount = useCallback((delta: number) => {
    providerSaveCountRef.current += delta
    setProviderAutoSaving(providerSaveCountRef.current > 0)
  }, [])

  // 检查任务配置问题
  const checkTaskConfigIssues = useCallback(
    (taskConf: ModelTaskConfig | null, modelList: ModelInfo[], schema?: ConfigSchema | null) => {
      if (!taskConf) return

      const modelNameSet = new Set(modelList.map((m) => m.name))
      const requiredTaskNames = getRequiredTaskNames(schema ?? taskConfigSchemaRef.current)
      const invalidRefs: { taskName: string; invalidModels: string[] }[] = []
      const emptyTaskList: string[] = []

      for (const [key, task] of Object.entries(taskConf)) {
        if (!task) continue

        // 检查是否有模型
        if (!task.model_list || task.model_list.length === 0) {
          if (requiredTaskNames.has(key)) {
            emptyTaskList.push(key)
          }
          continue
        }

        // 检查是否引用了不存在的模型
        const invalid = task.model_list.filter((modelName) => !modelNameSet.has(modelName))
        if (invalid.length > 0) {
          invalidRefs.push({ taskName: key, invalidModels: invalid })
        }
      }

      setInvalidModelRefs(invalidRefs)
      setEmptyTasks(emptyTaskList)
    },
    []
  )

  // 应用待定的 embedding 更新（供 useEmbeddingWarning 在确认时回调）
  const applyEmbeddingUpdate = useCallback(
    (update: PendingEmbeddingUpdate) => {
      setTaskConfig((current) => {
        if (!current) return current
        const newTaskConfig = {
          ...current,
          embedding: {
            ...current.embedding,
            [update.field]: update.value,
          },
        }
        // 重新检查任务配置问题
        checkTaskConfigIssues(newTaskConfig, models)
        return newTaskConfig
      })
    },
    [checkTaskConfigIssues, models]
  )

  const embeddingWarning = useEmbeddingWarning({ applyUpdate: applyEmbeddingUpdate })
  const { setPrevious: setPreviousEmbedding, detectChange: detectEmbeddingChange } =
    embeddingWarning

  const loadConfigVersions = useCallback(async () => {
    try {
      setVersionsLoading(true)
      const versionResult = await getModelConfigVersions()
      setActiveConfigVersion(versionResult.active_version)
      setConfigVersions(versionResult.versions)
    } catch (error) {
      console.error('加载模型配置副本失败:', error)
      toast({
        title: '副本列表加载失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setVersionsLoading(false)
    }
  }, [toast])

  // 加载配置
  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      // 用 allSettled：模型配置为必需，schema 为可选，二者失败互不影响
      const [result, schemaResult, versionsResult] = await Promise.allSettled([
        getModelConfigCached(),
        getModelConfigSchema(),
        getModelConfigVersions(),
      ])
      if (result.status !== 'fulfilled') {
        toast({
          title: '加载失败',
          description: result.reason instanceof Error ? result.reason.message : '加载模型配置失败',
          variant: 'destructive',
        })
        setLoading(false)
        return
      }
      const config = unwrapModelConfig(result.value)
      const modelList = (config.models as ModelInfo[]) || []
      setModels(modelList)
      setModelNames(modelList.map((m) => m.name))

      const providerList = (config.api_providers as ProviderConfig[]) || []
      setProviders(providerList.map((p) => p.name))
      setProviderConfigs(providerList)
      setApiProviders(providerList.map((provider) => cleanProviderData(provider as APIProvider)))
      const providersSnapshot = JSON.stringify(
        providerList.map((provider) => cleanProviderData(provider as APIProvider))
      )
      providersSnapshotRef.current = providersSnapshot
      latestProvidersSnapshotRef.current = providersSnapshot
      providerGenerationRef.current += 1

      const taskConf = (config.model_task_config as ModelTaskConfig) || null
      setTaskConfig(taskConf)
      resetSnapshots(modelList, taskConf)

      // 解析 model_task_config 的 schema
      let nextTaskConfigSchema: ConfigSchema | null = null
      if (schemaResult.status === 'fulfilled' && schemaResult.value) {
        const schema = (schemaResult.value as unknown as Record<string, unknown>)
          .schema as ConfigSchema
        nextTaskConfigSchema = schema.nested?.model_task_config ?? null
        taskConfigSchemaRef.current = nextTaskConfigSchema
        setTaskConfigSchema(nextTaskConfigSchema)
      }
      if (versionsResult.status === 'fulfilled') {
        setActiveConfigVersion(versionsResult.value.active_version)
        setConfigVersions(versionsResult.value.versions)
      } else {
        console.error('加载模型配置副本失败:', versionsResult.reason)
      }

      // 检查任务配置问题
      checkTaskConfigIssues(taskConf, modelList, nextTaskConfigSchema)

      // 初始化上一次的 embedding 模型列表
      const embeddingModels = taskConf?.embedding?.model_list || []
      setPreviousEmbedding(embeddingModels)
      setModelHasUnsavedChanges(false)
      setProviderHasUnsavedChanges(false)
      initialLoadRef.current = false
    } catch (error) {
      console.error('加载配置失败:', error)
    } finally {
      setLoading(false)
    }
  }, [initialLoadRef, checkTaskConfigIssues, resetSnapshots, setPreviousEmbedding, toast])

  // 初始加载
  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  // 获取指定提供商的配置
  const getProviderConfig = useCallback(
    (providerName: string): ProviderConfig | undefined => {
      return providerConfigs.find((p) => p.name === providerName)
    },
    [providerConfigs]
  )

  const isDeepSeekTemplateProvider = useCallback(
    (providerName: string): boolean => {
      const provider = getProviderConfig(providerName)
      return provider ? findTemplateByBaseUrl(provider.base_url)?.id === 'deepseek' : false
    },
    [getProviderConfig]
  )

  // 清理模型中的 null 值（TOML 不支持 null）
  const cleanModelForSave = useCallback((model: ModelInfo): ModelInfo => {
    const cleaned: ModelInfo = {
      model_identifier: model.model_identifier,
      name: model.name,
      api_provider: model.api_provider,
      price_in: model.price_in ?? 0,
      price_out: model.price_out ?? 0,
      cache: model.cache ?? false,
      cache_price_in: model.cache_price_in ?? 0,
      visual: model.visual ?? false,
      force_stream_mode: model.force_stream_mode ?? false,
      prefill: model.prefill ?? true,
      extra_params: model.extra_params ?? {},
    }
    // 只有在有值时才添加可选字段
    if (model.temperature != null) {
      cleaned.temperature = model.temperature
    }
    if (model.max_tokens != null) {
      cleaned.max_tokens = model.max_tokens
    }
    return cleaned
  }, [])

  // ---- 提供商状态同步 / 级联删除 ----
  const syncProviderState = useCallback((nextProviders: APIProvider[]) => {
    const cleanedProviders = nextProviders.map(cleanProviderData)
    setApiProviders(cleanedProviders)
    setProviders(cleanedProviders.map((provider) => provider.name))
    setProviderConfigs(
      cleanedProviders.map((provider) => ({
        name: provider.name,
        base_url: provider.base_url,
        api_key: provider.api_key,
        client_type: provider.client_type,
        max_retry: provider.max_retry ?? 2,
        timeout: provider.timeout ?? 30,
        retry_interval: provider.retry_interval ?? 10,
      }))
    )
  }, [])

  const removeModelsForProviders = useCallback(
    (
      sourceModels: ModelInfo[],
      sourceTaskConfig: ModelTaskConfig | null,
      removedModels: unknown[]
    ) => {
      const removedModelNames = new Set(
        removedModels
          .map((model) =>
            typeof model === 'object' && model !== null && 'name' in model
              ? String((model as Record<string, unknown>).name)
              : ''
          )
          .filter(Boolean)
      )
      if (removedModelNames.size === 0) {
        return { models: sourceModels, taskConfig: sourceTaskConfig }
      }

      const nextModels = sourceModels.filter((model) => !removedModelNames.has(model.name))
      if (!sourceTaskConfig) {
        return { models: nextModels, taskConfig: sourceTaskConfig }
      }

      const nextTaskConfig: ModelTaskConfig = {}
      for (const [taskName, task] of Object.entries(sourceTaskConfig)) {
        nextTaskConfig[taskName] = {
          ...task,
          model_list: (task?.model_list || []).filter(
            (modelName) => !removedModelNames.has(modelName)
          ),
        }
      }
      return { models: nextModels, taskConfig: nextTaskConfig }
    },
    []
  )

  const checkDeleteProviderImpact = useCallback(
    async (nextProviders: APIProvider[], context: 'auto' | 'manual' = 'auto') => {
      const oldProviderNames = new Set(apiProviders.map((provider) => provider.name))
      const nextProviderNames = new Set(nextProviders.map((provider) => provider.name))
      const deletedProviders = Array.from(oldProviderNames).filter(
        (name) => !nextProviderNames.has(name)
      )

      if (deletedProviders.length === 0) {
        return { shouldProceed: true }
      }

      const affectedModels = models.filter((model) => deletedProviders.includes(model.api_provider))
      if (affectedModels.length === 0) {
        return { shouldProceed: true }
      }

      setDeleteConfirmState({
        isOpen: true,
        providersToDelete: deletedProviders,
        affectedModels,
        pendingProviders: nextProviders,
        context,
        oldProviders: [...apiProviders],
      })
      return { shouldProceed: false }
    },
    [apiProviders, models]
  )

  const persistModelConfigDraft = useCallback(
    async (
      nextModels: ModelInfo[],
      nextTaskConfig: ModelTaskConfig | null,
      nextApiProviders: APIProvider[]
    ): Promise<ConfigDraftPersistResult> => {
      const modelCheckpoint = prepareSaveBarrier(nextModels, nextTaskConfig)
      const providerCheckpoint = prepareProviderSaveBarrier(nextApiProviders)

      // 屏障在首次 await 前入队；此后产生的自动保存只能排在整份写入之后。
      const savePromise = enqueueConfigWrite(async () => {
        const config = unwrapModelConfig(await getModelConfig())
        config.api_providers = nextApiProviders.map(cleanProviderData)
        config.models = nextModels.map(cleanModelForSave)
        config.model_task_config = nextTaskConfig
        await updateModelConfig(config)
      })
      await savePromise

      const modelCommit = commitSaveBarrier(modelCheckpoint)
      const applyProviders = commitProviderSaveBarrier(providerCheckpoint)
      return {
        applyModels: modelCommit.applyModels,
        applyProviders,
        applyTaskConfig: modelCommit.applyTaskConfig,
      }
    },
    [
      cleanModelForSave,
      commitProviderSaveBarrier,
      commitSaveBarrier,
      enqueueConfigWrite,
      prepareProviderSaveBarrier,
      prepareSaveBarrier,
    ]
  )

  const saveProviders = useCallback(
    async (nextProviders: APIProvider[], affectedModels: unknown[] = []) => {
      const cleanedProviders = nextProviders.map(cleanProviderData)
      const { models: nextModels, taskConfig: nextTaskConfig } = removeModelsForProviders(
        models,
        taskConfig,
        affectedModels
      )

      const persistResult = await persistModelConfigDraft(
        nextModels,
        nextTaskConfig,
        cleanedProviders
      )
      if (persistResult.applyProviders) {
        syncProviderState(cleanedProviders)
      }
      if (persistResult.applyModels) {
        setModels(nextModels)
        setModelNames(nextModels.map((model) => model.name))
      }
      if (persistResult.applyTaskConfig) {
        setTaskConfig(nextTaskConfig)
      }
      if (persistResult.applyModels && persistResult.applyTaskConfig) {
        checkTaskConfigIssues(nextTaskConfig, nextModels)
      }
    },
    [
      checkTaskConfigIssues,
      models,
      persistModelConfigDraft,
      removeModelsForProviders,
      syncProviderState,
      taskConfig,
    ]
  )

  const autoSaveProviders = useCallback(
    async (nextProviders: APIProvider[], snapshot: string, generation: number) => {
      if (initialLoadRef.current) return
      if (
        generation !== providerGenerationRef.current ||
        snapshot !== latestProvidersSnapshotRef.current
      ) {
        return
      }

      const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'auto')
      if (!shouldProceed) {
        setProviderHasUnsavedChanges(true)
        return
      }
      if (
        generation !== providerGenerationRef.current ||
        snapshot !== latestProvidersSnapshotRef.current
      ) {
        return
      }

      updateProviderSavingCount(1)
      try {
        await enqueueConfigWrite(async () => {
          await updateModelConfigSection('api_providers', nextProviders.map(cleanProviderData))
        })
        if (
          generation === providerGenerationRef.current &&
          snapshot === latestProvidersSnapshotRef.current
        ) {
          providersSnapshotRef.current = snapshot
          setProviderHasUnsavedChanges(false)
        }
      } catch (error) {
        console.error('自动保存提供商失败:', error)
        if (generation === providerGenerationRef.current) {
          toast({
            title: '自动保存失败',
            description: (error as Error).message,
            variant: 'destructive',
          })
          setProviderHasUnsavedChanges(true)
        }
      } finally {
        updateProviderSavingCount(-1)
      }
    },
    [
      checkDeleteProviderImpact,
      enqueueConfigWrite,
      initialLoadRef,
      toast,
      updateProviderSavingCount,
    ]
  )

  // 监听 apiProviders 变化，防抖自动保存
  useEffect(() => {
    if (initialLoadRef.current) return
    const snapshot = JSON.stringify(apiProviders.map(cleanProviderData))
    if (providersSnapshotRef.current === null) {
      providersSnapshotRef.current = snapshot
      return
    }

    providerGenerationRef.current += 1
    const generation = providerGenerationRef.current
    const dirty = snapshot !== providersSnapshotRef.current || providerSaveCountRef.current > 0
    setProviderHasUnsavedChanges(dirty)
    if (!dirty) return

    providerAutoSaveTimerRef.current = setTimeout(() => {
      providerAutoSaveTimerRef.current = null
      void autoSaveProviders(apiProviders, snapshot, generation)
    }, 2000)

    return () => {
      if (providerAutoSaveTimerRef.current) {
        clearTimeout(providerAutoSaveTimerRef.current)
        providerAutoSaveTimerRef.current = null
      }
    }
  }, [apiProviders, autoSaveProviders, initialLoadRef])

  // 一键删除所有无效模型引用
  const handleRemoveInvalidRefs = useCallback(() => {
    if (!taskConfig) return

    const modelNameSet = new Set(models.map((m) => m.name))
    const newTaskConfig: ModelTaskConfig = {}

    // 遍历所有任务，过滤掉无效的模型引用
    for (const [key, task] of Object.entries(taskConfig)) {
      if (task && task.model_list) {
        newTaskConfig[key] = {
          ...task,
          model_list: task.model_list.filter((modelName) => modelNameSet.has(modelName)),
        }
      } else {
        newTaskConfig[key] = task
      }
    }

    setTaskConfig(newTaskConfig)
    setInvalidModelRefs([])

    toast({
      title: '清理完成',
      description: '已删除所有无效的模型引用',
    })
  }, [taskConfig, models, toast])

  const persistCurrentDraft = useCallback(async () => {
    return persistModelConfigDraft(models, taskConfig, apiProviders)
  }, [apiProviders, models, persistModelConfigDraft, taskConfig])

  // 保存配置（手动保存）
  const saveConfig = useCallback(async () => {
    try {
      setSaving(true)

      const persistResult = await persistCurrentDraft()
      toast({
        title: '保存成功',
        description: '模型配置已保存',
      })
      if (
        persistResult.applyModels &&
        persistResult.applyProviders &&
        persistResult.applyTaskConfig
      ) {
        await loadConfig() // 保存期间没有新编辑时再重新加载，避免覆盖屏障后的草稿。
      }
    } catch (error) {
      console.error('保存配置失败:', error)
      toast({
        title: '保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }, [loadConfig, persistCurrentDraft, toast])

  const handleCreateConfigVersion = useCallback(
    async (label: string) => {
      try {
        setCreatingConfigVersion(true)
        setSaving(true)
        if (hasUnsavedChanges) {
          await persistCurrentDraft()
        }
        const version = await createModelConfigVersion(label)
        await loadConfigVersions()
        toast({
          title: '副本已创建',
          description: `已保存为「${version.label}」`,
        })
      } catch (error) {
        toast({
          title: '创建副本失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      } finally {
        setCreatingConfigVersion(false)
        setSaving(false)
      }
    },
    [hasUnsavedChanges, loadConfigVersions, persistCurrentDraft, toast]
  )

  const handleSwitchConfigVersion = useCallback(
    async (versionId: string) => {
      if (!versionId || versionId === 'active') return

      try {
        setSwitchingConfigVersion(versionId)
        setSaving(true)
        if (hasUnsavedChanges) {
          await persistCurrentDraft()
        } else {
          cancelModelAutoSaveTimers()
          cancelProviderAutoSaveTimer()
        }
        await enqueueConfigWrite(async () => {
          await switchModelConfigVersion(versionId)
        })
        await loadConfig()
        toast({
          title: '副本已切换',
          description: '当前模型配置已更新，切换前配置已归档为未启用副本',
        })
      } catch (error) {
        toast({
          title: '切换副本失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      } finally {
        setSwitchingConfigVersion(null)
        setSaving(false)
      }
    },
    [
      cancelModelAutoSaveTimers,
      cancelProviderAutoSaveTimer,
      enqueueConfigWrite,
      hasUnsavedChanges,
      loadConfig,
      persistCurrentDraft,
      toast,
    ]
  )

  const handleDeleteConfigVersion = useCallback(
    async (versionId: string) => {
      try {
        await deleteModelConfigVersion(versionId)
        await loadConfigVersions()
        toast({
          title: '副本已删除',
          description: '未启用的模型配置副本已删除',
        })
      } catch (error) {
        toast({
          title: '删除副本失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      }
    },
    [loadConfigVersions, toast]
  )

  // ---- 模型编辑对话框 ----
  const openEditDialog = useCallback(
    (model: ModelInfo | null, index: number | null, onOpened?: () => void) => {
      // 清除表单验证错误
      setFormErrors({})

      const defaultProvider = providers[0] || ''

      setEditingModel(
        model || {
          model_identifier: '',
          name: '',
          api_provider: defaultProvider,
          price_in: 0,
          price_out: 0,
          cache: isDeepSeekTemplateProvider(defaultProvider),
          cache_price_in: 0,
          temperature: null,
          max_tokens: null,
          visual: false,
          force_stream_mode: false,
          prefill: true,
          extra_params: {},
        }
      )
      onOpened?.()
      setEditingIndex(index)
      setEditDialogOpen(true)
    },
    [isDeepSeekTemplateProvider, providers]
  )

  const openProviderDialog = useCallback((provider: APIProvider | null, index: number | null) => {
    setEditingProvider(
      provider || {
        name: '',
        base_url: '',
        api_key: '',
        client_type: 'openai',
        max_retry: 2,
        timeout: 30,
        retry_interval: 10,
      }
    )
    setEditingProviderIndex(index)
    setProviderDialogOpen(true)
  }, [])

  const handleSaveProviderEdit = useCallback(
    async (provider: APIProvider, index: number | null) => {
      const providerToSave = cleanProviderData(provider)
      const nextProviders = [...apiProviders]
      if (index !== null) {
        nextProviders[index] = providerToSave
      } else {
        nextProviders.push(providerToSave)
      }

      const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'manual')
      if (!shouldProceed) {
        setProviderDialogOpen(false)
        setEditingProvider(null)
        setEditingProviderIndex(null)
        return
      }

      try {
        setSaving(true)
        await saveProviders(nextProviders)
        setProviderDialogOpen(false)
        setEditingProvider(null)
        setEditingProviderIndex(null)
        toast({
          title: index !== null ? '提供商已更新' : '提供商已添加',
          description: '模型配置已保存',
        })
      } catch (error) {
        toast({
          title: '保存失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      } finally {
        setSaving(false)
      }
    },
    [apiProviders, checkDeleteProviderImpact, saveProviders, toast]
  )

  // 保存模型编辑
  const handleSaveEdit = useCallback(async () => {
    if (!editingModel) return

    // 验证必填项
    const errors: ModelFormErrors = {}
    if (!editingModel.name?.trim()) {
      errors.name = '请输入模型名称'
    } else {
      // 检查名称是否与现有模型重复
      const isDuplicate = models.some((m, index) => {
        // 编辑时排除自身
        if (editingIndex !== null && index === editingIndex) {
          return false
        }
        return m.name.trim().toLowerCase() === editingModel.name.trim().toLowerCase()
      })
      if (isDuplicate) {
        errors.name = '模型名称已存在，请使用其他名称'
      }
    }
    if (!editingModel.api_provider?.trim()) {
      errors.api_provider = '请选择 API 提供商'
    }
    if (!editingModel.model_identifier?.trim()) {
      errors.model_identifier = '请输入模型标识符'
    }

    if (Object.keys(errors).length > 0) {
      setFormErrors(errors)
      return
    }

    // 清除错误状态
    setFormErrors({})

    // 填充空值的默认值，并移除 null 值的可选字段（TOML 不支持 null）
    const modelToSave: ModelInfo = {
      model_identifier: editingModel.model_identifier,
      name: editingModel.name,
      api_provider: editingModel.api_provider,
      price_in: editingModel.price_in ?? 0,
      price_out: editingModel.price_out ?? 0,
      cache: editingModel.cache ?? false,
      cache_price_in: editingModel.cache_price_in ?? 0,
      visual: editingModel.visual ?? false,
      force_stream_mode: editingModel.force_stream_mode ?? false,
      prefill: editingModel.prefill ?? true,
      extra_params: editingModel.extra_params ?? {},
    }

    // 只有在有值时才添加可选字段
    if (editingModel.temperature != null) {
      modelToSave.temperature = editingModel.temperature
    }
    if (editingModel.max_tokens != null) {
      modelToSave.max_tokens = editingModel.max_tokens
    }

    let newModels: ModelInfo[]
    let oldModelName: string | null = null

    if (editingIndex !== null) {
      // 记录旧的模型名称，用于更新任务配置
      oldModelName = models[editingIndex].name
      newModels = [...models]
      newModels[editingIndex] = modelToSave
    } else {
      newModels = [...models, modelToSave]
    }

    // 如果模型名称发生变化，更新任务配置中对该模型的引用
    const modelRenamed = oldModelName !== null && oldModelName !== modelToSave.name
    let newTaskConfig = taskConfig
    if (modelRenamed && taskConfig) {
      const updateModelList = (list: string[]): string[] => {
        return list.map((name) => (name === oldModelName ? modelToSave.name : name))
      }

      newTaskConfig = {}
      for (const [key, task] of Object.entries(taskConfig)) {
        newTaskConfig[key] = { ...task, model_list: updateModelList(task?.model_list || []) }
      }
    }

    try {
      setSaving(true)
      // 模型名称与任务引用必须在同一次写入中保存，避免热重载读到不一致的中间状态。
      const persistResult = await persistModelConfigDraft(newModels, newTaskConfig, apiProviders)
      if (persistResult.applyModels) {
        setModels(newModels)
        setModelNames(newModels.map((model) => model.name))
      }
      if (persistResult.applyTaskConfig) {
        setTaskConfig(newTaskConfig)
      }
      if (persistResult.applyModels && persistResult.applyTaskConfig) {
        checkTaskConfigIssues(newTaskConfig, newModels)
      }
      setEditDialogOpen(false)
      setEditingModel(null)
      setEditingIndex(null)
      toast({
        title: editingIndex !== null ? '模型已更新' : '模型已添加',
        description: modelRenamed ? '模型名称及任务引用已同步保存' : '模型配置已保存',
      })
    } catch (error) {
      console.error('保存模型配置失败:', error)
      toast({
        title: '保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }, [
    apiProviders,
    checkTaskConfigIssues,
    editingIndex,
    editingModel,
    models,
    persistModelConfigDraft,
    taskConfig,
    toast,
  ])

  // 处理编辑对话框关闭
  const handleEditDialogClose = useCallback(
    (open: boolean) => {
      if (!open && editingModel) {
        // 关闭时填充默认值
        const updatedModel = {
          ...editingModel,
          price_in: editingModel.price_in ?? 0,
          price_out: editingModel.price_out ?? 0,
        }
        setEditingModel(updatedModel)
      }
      setEditDialogOpen(open)
    },
    [editingModel]
  )

  // 打开删除确认对话框
  const openDeleteDialog = useCallback((index: number) => {
    setDeletingIndex(index)
    setDeleteDialogOpen(true)
  }, [])

  // 确认删除模型
  const handleConfirmDelete = useCallback(() => {
    if (deletingIndex !== null) {
      const newModels = models.filter((_, i) => i !== deletingIndex)
      setModels(newModels)
      setModelNames(newModels.map((m) => m.name))
      // 重新检查任务配置问题
      checkTaskConfigIssues(taskConfig, newModels)
      toast({
        title: '删除成功',
        description: '配置将在 2 秒后自动保存',
      })
    }
    setDeleteDialogOpen(false)
    setDeletingIndex(null)
  }, [checkTaskConfigIssues, deletingIndex, models, taskConfig, toast])

  const openProviderDeleteDialog = useCallback((index: number) => {
    setDeletingProviderIndex(index)
    setProviderDeleteDialogOpen(true)
  }, [])

  const handleConfirmProviderDelete = useCallback(async () => {
    if (deletingProviderIndex !== null) {
      const nextProviders = apiProviders.filter((_, index) => index !== deletingProviderIndex)
      const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'manual')
      if (shouldProceed) {
        syncProviderState(nextProviders)
        toast({
          title: '删除成功',
          description: '提供商已从列表中移除',
        })
      }
    }
    setProviderDeleteDialogOpen(false)
    setDeletingProviderIndex(null)
  }, [apiProviders, checkDeleteProviderImpact, deletingProviderIndex, syncProviderState, toast])

  const handleConfirmDeleteProviderImpact = useCallback(async () => {
    const isAutoSave = deleteConfirmState.context === 'auto'
    try {
      if (isAutoSave) {
        updateProviderSavingCount(1)
      } else {
        setSaving(true)
      }
      await saveProviders(deleteConfirmState.pendingProviders, deleteConfirmState.affectedModels)
      toast({
        title: '删除成功',
        description: `已删除 ${deleteConfirmState.providersToDelete.length} 个提供商和 ${deleteConfirmState.affectedModels.length} 个关联模型`,
      })
      setDeleteConfirmState({
        isOpen: false,
        providersToDelete: [],
        affectedModels: [],
        pendingProviders: [],
        context: 'auto',
        oldProviders: [],
      })
    } catch (error) {
      toast({
        title: '删除失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      if (isAutoSave) {
        updateProviderSavingCount(-1)
      } else {
        setSaving(false)
      }
    }
  }, [deleteConfirmState, saveProviders, toast, updateProviderSavingCount])

  const handleCancelDeleteProviderImpact = useCallback(() => {
    const currentSnapshot = JSON.stringify(apiProviders.map(cleanProviderData))
    const pendingSnapshot = JSON.stringify(
      deleteConfirmState.pendingProviders.map(cleanProviderData)
    )
    if (deleteConfirmState.oldProviders.length > 0 && currentSnapshot === pendingSnapshot) {
      syncProviderState(deleteConfirmState.oldProviders)
      const restoredSnapshot = JSON.stringify(
        deleteConfirmState.oldProviders.map(cleanProviderData)
      )
      providersSnapshotRef.current = restoredSnapshot
      latestProvidersSnapshotRef.current = restoredSnapshot
      providerGenerationRef.current += 1
      setProviderHasUnsavedChanges(false)
    } else {
      setProviderHasUnsavedChanges(currentSnapshot !== providersSnapshotRef.current)
    }
    setDeleteConfirmState({
      isOpen: false,
      providersToDelete: [],
      affectedModels: [],
      pendingProviders: [],
      context: 'auto',
      oldProviders: [],
    })
  }, [apiProviders, deleteConfirmState, syncProviderState])

  // ---- 提供商连接测试 ----
  const handleTestProviderConnection = useCallback(
    async (providerName: string) => {
      setTestingProviders((prev) => new Set(prev).add(providerName))
      try {
        const testResult = await testProviderConnection(providerName)
        setTestResults((prev) => new Map(prev).set(providerName, testResult))
        if (testResult.network_ok && testResult.api_key_valid !== false) {
          toast({
            title: testResult.api_key_valid === true ? '连接正常' : '网络连接正常',
            description: `${providerName} 可以访问 (${testResult.latency_ms}ms)`,
          })
        } else {
          toast({
            title: testResult.network_ok ? '连接正常但 Key 无效' : '连接失败',
            description: testResult.error || `${providerName} API Key 无效或无法连接`,
            variant: 'destructive',
          })
        }
      } catch (error) {
        toast({
          title: '测试失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      } finally {
        setTestingProviders((prev) => {
          const next = new Set(prev)
          next.delete(providerName)
          return next
        })
      }
    },
    [toast]
  )

  const handleTestAllProviderConnections = useCallback(async () => {
    for (const provider of apiProviders) {
      await handleTestProviderConnection(provider.name)
    }
  }, [apiProviders, handleTestProviderConnection])

  const handleTestModelCapability = useCallback(
    async (modelName: string) => {
      setTestingModels((prev) => new Set(prev).add(modelName))
      try {
        const testResult = await testModelCapability(modelName)
        setModelTestResults((prev) => new Map(prev).set(modelName, testResult))
        if (testResult.success) {
          toast({
            title: '模型测试通过',
            description: `${modelName} 已完成文本${testResult.visual_tested ? '、视觉' : ''}与工具调用测试 (${testResult.latency_ms != null ? `${(testResult.latency_ms / 1000).toFixed(2)}s` : '-'})`,
            duration: 8000,
            action: buildModelTestDetailAction(testResult),
          })
        } else {
          toast({
            title: testResult.tool_call_ok ? '模型响应异常' : '工具调用未通过',
            description: testResult.error || `${modelName} 未通过模型能力测试`,
            variant: 'destructive',
            duration: 10000,
            action: buildModelTestDetailAction(testResult),
          })
        }
      } catch (error) {
        toast({
          title: '模型测试失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
      } finally {
        setTestingModels((prev) => {
          const next = new Set(prev)
          next.delete(modelName)
          return next
        })
      }
    },
    [buildModelTestDetailAction, toast]
  )

  // ---- 模型批量选择 ----
  // 过滤模型列表（搜索）
  const filteredModels = useMemo(
    () =>
      models.filter((model) => {
        if (modelProviderFilter && model.api_provider !== modelProviderFilter) return false
        if (!searchQuery) return true
        const query = searchQuery.toLowerCase()
        return (
          model.name.toLowerCase().includes(query) ||
          model.model_identifier.toLowerCase().includes(query) ||
          model.api_provider.toLowerCase().includes(query)
        )
      }),
    [modelProviderFilter, models, searchQuery]
  )

  useEffect(() => {
    setPage(1)
    setSelectedModels(new Set())
  }, [modelProviderFilter, searchQuery])

  // 切换单个模型选择
  const toggleModelSelection = useCallback((index: number) => {
    setSelectedModels((prev) => {
      const newSelected = new Set(prev)
      if (newSelected.has(index)) {
        newSelected.delete(index)
      } else {
        newSelected.add(index)
      }
      return newSelected
    })
  }, [])

  // 全选/取消全选
  const toggleSelectAll = useCallback(() => {
    setSelectedModels((prev) => {
      if (prev.size === filteredModels.length) {
        return new Set()
      }
      const allIndices = filteredModels.map((fm) => models.findIndex((m) => m === fm))
      return new Set(allIndices)
    })
  }, [filteredModels, models])

  // 打开批量删除确认对话框
  const openBatchDeleteDialog = useCallback(() => {
    if (selectedModels.size === 0) {
      toast({
        title: '提示',
        description: '请先选择要删除的模型',
        variant: 'default',
      })
      return
    }
    setBatchDeleteDialogOpen(true)
  }, [selectedModels, toast])

  // 确认批量删除
  const handleConfirmBatchDelete = useCallback(() => {
    const deletedCount = selectedModels.size
    const newModels = models.filter((_, index) => !selectedModels.has(index))
    setModels(newModels)
    setModelNames(newModels.map((m) => m.name))
    // 重新检查任务配置问题
    checkTaskConfigIssues(taskConfig, newModels)
    setSelectedModels(new Set())
    setBatchDeleteDialogOpen(false)
    toast({
      title: '批量删除成功',
      description: `已删除 ${deletedCount} 个模型，配置将在 2 秒后自动保存`,
    })
  }, [checkTaskConfigIssues, models, selectedModels, taskConfig, toast])

  // ---- 任务配置更新（与 embedding 警告协调）----
  const updateTaskConfig = useCallback(
    (taskName: string, field: keyof TaskConfig, value: string[] | number | string) => {
      if (!taskConfig) return

      // 检测 embedding 模型列表变化：有变化则交由 useEmbeddingWarning 拦截弹警告
      if (taskName === 'embedding' && field === 'model_list' && Array.isArray(value)) {
        const intercepted = detectEmbeddingChange(field, value)
        if (intercepted) {
          return
        }
      }

      // 正常更新配置
      const newTaskConfig = {
        ...taskConfig,
        [taskName]: {
          ...taskConfig[taskName],
          [field]: value,
        },
      }
      setTaskConfig(newTaskConfig)

      // 重新检查任务配置问题
      checkTaskConfigIssues(newTaskConfig, models)

      // 如果是 embedding 模型列表，更新 previous ref
      if (taskName === 'embedding' && field === 'model_list' && Array.isArray(value)) {
        setPreviousEmbedding(value)
      }
    },
    [checkTaskConfigIssues, detectEmbeddingChange, models, setPreviousEmbedding, taskConfig]
  )

  // ---- 分页 ----
  const totalPages = Math.ceil(filteredModels.length / pageSize)
  const paginatedModels = useMemo(
    () => filteredModels.slice((page - 1) * pageSize, page * pageSize),
    [filteredModels, page, pageSize]
  )

  // 页码跳转
  const handleJumpToPage = useCallback(() => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    }
  }, [jumpToPage, totalPages])

  // 检查模型是否被任务使用
  const isModelUsed = useCallback(
    (modelName: string): boolean => {
      if (!taskConfig) return false
      return Object.values(taskConfig).some((task) => task?.model_list?.includes(modelName))
    },
    [taskConfig]
  )

  return {
    // 草稿态
    models,
    providers,
    apiProviders,
    modelNames,
    taskConfig,
    taskConfigSchema,
    // 加载 / 保存
    loading,
    saving,
    autoSaving,
    hasUnsavedChanges,
    activeConfigVersion,
    configVersions,
    versionsLoading,
    switchingConfigVersion,
    creatingConfigVersion,
    loadConfig,
    loadConfigVersions,
    saveConfig,
    handleCreateConfigVersion,
    handleSwitchConfigVersion,
    handleDeleteConfigVersion,
    // 任务配置问题
    invalidModelRefs,
    emptyTasks,
    handleRemoveInvalidRefs,
    // 模型编辑
    editDialogOpen,
    setEditDialogOpen,
    editingModel,
    setEditingModel,
    editingIndex,
    formErrors,
    setFormErrors,
    openEditDialog,
    isDeepSeekTemplateProvider,
    handleSaveEdit,
    handleEditDialogClose,
    deleteDialogOpen,
    setDeleteDialogOpen,
    deletingIndex,
    openDeleteDialog,
    handleConfirmDelete,
    getProviderConfig,
    // 提供商编辑
    providerDialogOpen,
    setProviderDialogOpen,
    editingProvider,
    editingProviderIndex,
    openProviderDialog,
    handleSaveProviderEdit,
    providerDeleteDialogOpen,
    setProviderDeleteDialogOpen,
    deletingProviderIndex,
    openProviderDeleteDialog,
    handleConfirmProviderDelete,
    // 级联删除确认
    deleteConfirmState,
    handleConfirmDeleteProviderImpact,
    handleCancelDeleteProviderImpact,
    // 连接测试
    testingProviders,
    testResults,
    handleTestProviderConnection,
    handleTestAllProviderConnections,
    testingModels,
    modelTestResults,
    selectedModelTestResult,
    setSelectedModelTestResult,
    handleTestModelCapability,
    // 模型批量
    selectedModels,
    setSelectedModels,
    toggleModelSelection,
    toggleSelectAll,
    batchDeleteDialogOpen,
    setBatchDeleteDialogOpen,
    openBatchDeleteDialog,
    handleConfirmBatchDelete,
    // 任务配置
    updateTaskConfig,
    // 搜索 / 分页
    searchQuery,
    setSearchQuery,
    modelProviderFilter,
    setModelProviderFilter,
    filteredModels,
    paginatedModels,
    page,
    setPage,
    pageSize,
    setPageSize,
    jumpToPage,
    setJumpToPage,
    handleJumpToPage,
    isModelUsed,
    // embedding 警告
    embeddingWarning,
  }
}
