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
import type { ModelConfigVersionInfo, ModelTestResult, TestConnectionResult } from '@/lib/config-api'
import { useToast } from '@/hooks/use-toast'
import type { ConfigSchema } from '@/types/config-schema'

import type { ModelInfo, ModelTaskConfig, ProviderConfig, TaskConfig } from '../types'
import type { APIProvider, DeleteConfirmState } from '../../modelProvider/types'
import { cleanProviderData } from '../../modelProvider/utils'
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
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)

  // ---- 模型配置文件副本 ----
  const [activeConfigVersion, setActiveConfigVersion] = useState<ModelConfigVersionInfo | null>(null)
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
  const [selectedModels, setSelectedModels] = useState<Set<number>>(new Set())
  const [selectedProviders, setSelectedProviders] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [providerBatchDeleteDialogOpen, setProviderBatchDeleteDialogOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')

  // ---- 提供商连接测试 ----
  const [testingProviders, setTestingProviders] = useState<Set<string>>(new Set())
  const [testResults, setTestResults] = useState<Map<string, TestConnectionResult>>(new Map())

  // ---- 单模型能力测试 ----
  const [testingModels, setTestingModels] = useState<Set<string>>(new Set())
  const [modelTestResults, setModelTestResults] = useState<Map<string, ModelTestResult>>(new Map())
  const [selectedModelTestResult, setSelectedModelTestResult] = useState<ModelTestResult | null>(null)

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

  // 自动保存 models / taskConfig（沿用既有 hook）
  const {
    clearTimers: clearAutoSaveTimers,
    initialLoadRef,
    resetSnapshots,
  } = useModelAutoSave({
    models,
    taskConfig,
    onSavingChange: setAutoSaving,
    onUnsavedChange: setHasUnsavedChanges,
  })

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
      providersSnapshotRef.current = JSON.stringify(
        providerList.map((provider) => cleanProviderData(provider as APIProvider))
      )

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
      setHasUnsavedChanges(false)
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

  const saveProviders = useCallback(
    async (
      nextProviders: APIProvider[],
      context: 'auto' | 'manual' = 'auto',
      affectedModels: unknown[] = []
    ) => {
      const cleanedProviders = nextProviders.map(cleanProviderData)
      const { models: nextModels, taskConfig: nextTaskConfig } = removeModelsForProviders(
        models,
        taskConfig,
        affectedModels
      )

      if (context === 'auto' && affectedModels.length === 0) {
        await updateModelConfigSection('api_providers', cleanedProviders)
      } else {
        const config = unwrapModelConfig(await getModelConfig())
        config.api_providers = cleanedProviders
        config.models = nextModels.map(cleanModelForSave)
        config.model_task_config = nextTaskConfig
        await updateModelConfig(config)
      }

      syncProviderState(cleanedProviders)
      setModels(nextModels)
      setModelNames(nextModels.map((model) => model.name))
      setTaskConfig(nextTaskConfig)
      checkTaskConfigIssues(nextTaskConfig, nextModels)
      providersSnapshotRef.current = JSON.stringify(cleanedProviders)
      setHasUnsavedChanges(false)
    },
    [
      checkTaskConfigIssues,
      cleanModelForSave,
      models,
      removeModelsForProviders,
      syncProviderState,
      taskConfig,
    ]
  )

  const autoSaveProviders = useCallback(
    async (nextProviders: APIProvider[]) => {
      if (initialLoadRef.current) return
      const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'auto')
      if (!shouldProceed) {
        setHasUnsavedChanges(true)
        return
      }

      try {
        setAutoSaving(true)
        await saveProviders(nextProviders, 'auto')
      } catch (error) {
        console.error('自动保存提供商失败:', error)
        toast({
          title: '自动保存失败',
          description: (error as Error).message,
          variant: 'destructive',
        })
        setHasUnsavedChanges(true)
      } finally {
        setAutoSaving(false)
      }
    },
    [checkDeleteProviderImpact, initialLoadRef, saveProviders, toast]
  )

  // 监听 apiProviders 变化，防抖自动保存
  useEffect(() => {
    if (initialLoadRef.current) return
    const snapshot = JSON.stringify(apiProviders.map(cleanProviderData))
    if (providersSnapshotRef.current === null) {
      providersSnapshotRef.current = snapshot
      return
    }
    if (snapshot === providersSnapshotRef.current) return

    setHasUnsavedChanges(true)
    if (providerAutoSaveTimerRef.current) {
      clearTimeout(providerAutoSaveTimerRef.current)
    }
    providerAutoSaveTimerRef.current = setTimeout(() => {
      autoSaveProviders(apiProviders)
    }, 2000)

    return () => {
      if (providerAutoSaveTimerRef.current) {
        clearTimeout(providerAutoSaveTimerRef.current)
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
    clearAutoSaveTimers()
    if (providerAutoSaveTimerRef.current) {
      clearTimeout(providerAutoSaveTimerRef.current)
      providerAutoSaveTimerRef.current = null
    }

    const config = unwrapModelConfig(await getModelConfig())
    config.api_providers = apiProviders.map(cleanProviderData)
    config.models = models.map(cleanModelForSave)
    config.model_task_config = taskConfig
    await updateModelConfig(config)
    resetSnapshots(config.models as ModelInfo[], taskConfig)
    providersSnapshotRef.current = JSON.stringify(config.api_providers)
    setHasUnsavedChanges(false)
  }, [
    apiProviders,
    clearAutoSaveTimers,
    cleanModelForSave,
    models,
    resetSnapshots,
    taskConfig,
  ])

  // 保存配置（手动保存）
  const saveConfig = useCallback(async () => {
    try {
      setSaving(true)

      await persistCurrentDraft()
      toast({
        title: '保存成功',
        description: '模型配置已保存',
      })
      await loadConfig() // 重新加载以更新模型名称列表
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
          clearAutoSaveTimers()
          if (providerAutoSaveTimerRef.current) {
            clearTimeout(providerAutoSaveTimerRef.current)
            providerAutoSaveTimerRef.current = null
          }
        }
        await switchModelConfigVersion(versionId)
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
    [clearAutoSaveTimers, hasUnsavedChanges, loadConfig, persistCurrentDraft, toast]
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

      setEditingModel(
        model || {
          model_identifier: '',
          name: '',
          api_provider: providers[0] || '',
          price_in: 0,
          price_out: 0,
          cache: false,
          cache_price_in: 0,
          temperature: null,
          max_tokens: null,
          visual: false,
          force_stream_mode: false,
          extra_params: {},
        }
      )
      onOpened?.()
      setEditingIndex(index)
      setEditDialogOpen(true)
    },
    [providers]
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

      if (providerAutoSaveTimerRef.current) {
        clearTimeout(providerAutoSaveTimerRef.current)
      }
      clearAutoSaveTimers()

      const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'manual')
      if (!shouldProceed) {
        setProviderDialogOpen(false)
        setEditingProvider(null)
        setEditingProviderIndex(null)
        return
      }

      try {
        setSaving(true)
        await saveProviders(nextProviders, 'manual')
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
    [
      apiProviders,
      checkDeleteProviderImpact,
      clearAutoSaveTimers,
      saveProviders,
      toast,
    ]
  )

  // 保存模型编辑
  const handleSaveEdit = useCallback(() => {
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

    setModels(newModels)
    setModelNames(newModels.map((m) => m.name))

    // 如果模型名称发生变化，更新任务配置中对该模型的引用
    if (oldModelName && oldModelName !== modelToSave.name && taskConfig) {
      const updateModelList = (list: string[]): string[] => {
        return list.map((name) => (name === oldModelName ? modelToSave.name : name))
      }

      const newTaskConfig: ModelTaskConfig = {}
      for (const [key, task] of Object.entries(taskConfig)) {
        newTaskConfig[key] = { ...task, model_list: updateModelList(task?.model_list || []) }
      }
      setTaskConfig(newTaskConfig)
    }

    setEditDialogOpen(false)
    setEditingModel(null)
    setEditingIndex(null)

    // 提示用户配置将自动保存
    toast({
      title: editingIndex !== null ? '模型已更新' : '模型已添加',
      description: '配置将在 2 秒后自动保存',
    })
  }, [editingIndex, editingModel, models, taskConfig, toast])

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

  // ---- 批量选择 / 删除 ----
  const toggleProviderSelection = useCallback((index: number) => {
    setSelectedProviders((prev) => {
      const nextSelected = new Set(prev)
      if (nextSelected.has(index)) {
        nextSelected.delete(index)
      } else {
        nextSelected.add(index)
      }
      return nextSelected
    })
  }, [])

  const toggleSelectAllProviders = useCallback(() => {
    setSelectedProviders((prev) => {
      if (prev.size === apiProviders.length) {
        return new Set()
      }
      return new Set(apiProviders.map((_, index) => index))
    })
  }, [apiProviders])

  const openProviderBatchDeleteDialog = useCallback(() => {
    if (selectedProviders.size === 0) {
      toast({
        title: '提示',
        description: '请先选择要删除的提供商',
        variant: 'default',
      })
      return
    }
    setProviderBatchDeleteDialogOpen(true)
  }, [selectedProviders, toast])

  const handleConfirmProviderBatchDelete = useCallback(async () => {
    const nextProviders = apiProviders.filter((_, index) => !selectedProviders.has(index))
    const { shouldProceed } = await checkDeleteProviderImpact(nextProviders, 'manual')
    if (shouldProceed) {
      const deletedCount = selectedProviders.size
      syncProviderState(nextProviders)
      setSelectedProviders(new Set())
      toast({
        title: '批量删除成功',
        description: `已删除 ${deletedCount} 个提供商`,
      })
    }
    setProviderBatchDeleteDialogOpen(false)
  }, [apiProviders, checkDeleteProviderImpact, selectedProviders, syncProviderState, toast])

  const handleConfirmDeleteProviderImpact = useCallback(async () => {
    try {
      const savingFlag = deleteConfirmState.context === 'auto' ? setAutoSaving : setSaving
      const saveContext = deleteConfirmState.context === 'auto' ? 'auto' : 'manual'
      savingFlag(true)
      await saveProviders(
        deleteConfirmState.pendingProviders,
        saveContext,
        deleteConfirmState.affectedModels
      )
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
      setSelectedProviders(new Set())
    } catch (error) {
      toast({
        title: '删除失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
      setAutoSaving(false)
    }
  }, [deleteConfirmState, saveProviders, toast])

  const handleCancelDeleteProviderImpact = useCallback(() => {
    if (deleteConfirmState.oldProviders.length > 0) {
      syncProviderState(deleteConfirmState.oldProviders)
    }
    setDeleteConfirmState({
      isOpen: false,
      providersToDelete: [],
      affectedModels: [],
      pendingProviders: [],
      context: 'auto',
      oldProviders: [],
    })
    setHasUnsavedChanges(false)
  }, [deleteConfirmState, syncProviderState])

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
        if (!searchQuery) return true
        const query = searchQuery.toLowerCase()
        return (
          model.name.toLowerCase().includes(query) ||
          model.model_identifier.toLowerCase().includes(query) ||
          model.api_provider.toLowerCase().includes(query)
        )
      }),
    [models, searchQuery]
  )

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
    // 提供商批量
    selectedProviders,
    toggleProviderSelection,
    toggleSelectAllProviders,
    providerBatchDeleteDialogOpen,
    setProviderBatchDeleteDialogOpen,
    openProviderBatchDeleteDialog,
    handleConfirmProviderBatchDelete,
    // 任务配置
    updateTaskConfig,
    // 搜索 / 分页
    searchQuery,
    setSearchQuery,
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
