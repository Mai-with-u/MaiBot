import { type UIEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { DraftNumberInput } from '@/components/ui/draft-number-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { DashboardFolderPanel, DashboardFolderTab, DashboardFolderTabBar } from '@/components/ui/dashboard-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { FormColumn, FormColumns, FormRow, FormSection, FormUnit } from '@/components/ui/form-section'
import { ComboboxInput } from '@/components/ui/combobox-input'
import { SplitDropdownSelect } from '@/components/ui/split-dropdown-select'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { Badge } from '@/components/ui/badge'
import {
  AlertTriangle,
  Copy,
  History,
  Loader2,
  Pencil,
  Plus,
  Search,
  Settings,
  Trash2,
  Zap,
  Boxes,
  ListChecks,
} from 'lucide-react'
import { resolveFieldLabel } from '@/lib/config-label'
import {
  getConfigSearchField,
  getModelConfigTabForField,
  scrollToConfigSearchField,
} from '@/lib/config-search-navigation'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { MobileAddMenu } from '@/components/layout/MobileAddMenu'
import { MobileHeaderActions } from '@/components/layout/MobileHeaderActions'
import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider } from '@/lib/restart-context'
import { ExtraParamsDialog } from '@/components/ui/extra-params-dialog'
import { ModelTable, ModelCardList } from './model/components'
import { TaskAssignmentBoard } from './model/components/TaskAssignmentBoard'
import { taskShortName } from './model/taskMeta'
import { AddModelMenu, type ModelCreationType } from './model/components/AddModelMenu'
import { EmbeddingBuildProgress } from './model/components/EmbeddingBuildProgress'
import { TaskRuntimeSummary } from './model/components/TaskRuntimeSummary'
import { TaskUsageStats } from './model/components/TaskUsageStats'
import { ThinkingProbeControls } from './model/components/ThinkingProbeControls'
import { useModelTour, useModelFetcher, useModelConfig } from './model/hooks'
import {
  getThinkingBudget,
  getThinkingEffort,
  isThinkingEnabled,
  setThinkingBudget,
  setThinkingEffort,
  setThinkingEnabled,
  validateThinkingParams,
  type ThinkingFormatConfig,
} from './model/thinkingFormats'
import { findTemplateByBaseUrl, resolveThinkingFormatForModel } from './providerTemplates'
import {
  getDeepSeekReasoningEffort,
  isDeepSeekThinkingEnabled,
  isDeepSeekWebSearchEnabled,
  setDeepSeekReasoningEffort,
  setDeepSeekThinkingEnabled,
  setDeepSeekWebSearchEnabled,
  validateDeepSeekExtraParams,
  type DeepSeekClientType,
  type DeepSeekReasoningEffort,
} from './model/deepSeekExtraParams'
import { ProviderForm } from './modelProvider/ProviderForm'
import { ProviderSidebar } from './modelProvider/ProviderSidebar'
import { WebSearchDialog } from './webSearch/WebSearchDialog'
import type { APIProvider } from './modelProvider/types'

// 导入模块化的类型定义和组件
import type { ModelInfo, ModelPricePeriod } from './model/types'

const MODEL_CONFIG_TABS = ['configuration', 'tasks'] as const
type ModelConfigTab = (typeof MODEL_CONFIG_TABS)[number]

function getInitialModelConfigTab(): ModelConfigTab {
  if (typeof window === 'undefined') {
    return 'tasks'
  }

  const tab = new URLSearchParams(window.location.search).get('tab')
  if (tab === 'configuration' || tab === 'models' || tab === 'providers') {
    return 'configuration'
  }
  return 'tasks'
}

// 主导出组件：包装 RestartProvider
type ModelKindFilter = 'all' | 'used' | 'visual' | 'embedding'

const MODEL_KIND_FILTERS: Array<{ value: ModelKindFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'used', label: '使用中' },
  { value: 'visual', label: '视觉' },
  { value: 'embedding', label: '嵌入' },
]

export function ModelConfigPage() {
  return (
    <RestartProvider>
      <ModelConfigPageContent />
    </RestartProvider>
  )
}

// 内部实现组件
function ModelConfigPageContent() {
  const { i18n } = useTranslation()
  const routeSearch = useRouterState({ select: (state) => state.location.searchStr })
  const searchFieldPath = getConfigSearchField(routeSearch)
  const scrolledSearchFieldRef = useRef('')
  const modelConfigurationRef = useRef<HTMLDivElement>(null)

  // 核心领域 hook：models / apiProviders / model_task_config 三份草稿及其全部编排
  const mc = useModelConfig()
  const [webSearchEditor, setWebSearchEditor] = useState<{ name: string | null } | null>(null)
  const {
    // 草稿态
    models,
    providers,
    apiProviders,
    taskConfig,
    taskConfigSchema,
    // 加载 / 保存
    loading,
    saving,
    autoSaving,
    activeConfigVersion,
    configVersions,
    versionsLoading,
    switchingConfigVersion,
    creatingConfigVersion,
    handleCreateConfigVersion,
    handleSwitchConfigVersion,
    handleDeleteConfigVersion,
    // 任务配置问题
    configWarnings,
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
    handleSaveEdit,
    handleEditDialogClose,
    deleteDialogOpen,
    setDeleteDialogOpen,
    deletingIndex,
    openDeleteDialog,
    handleConfirmDelete,
    // 提供商编辑
    providerDialogOpen,
    setProviderDialogOpen,
    editingProvider,
    editingProviderIndex,
    openProviderDialog: openProviderDialogBase,
    handleSaveProviderEdit: saveProviderEdit,
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
    testingModels,
    modelTestResults,
    selectedModelTestResult,
    setSelectedModelTestResult,
    handleTestModelCapability,
    // 模型批量
    selectedModels,
    toggleModelSelection,
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
    isModelUsed,
    getProviderConfig,
    // embedding 警告
    embeddingWarning,
  } = mc

  // 纯 UI 态（不属于配置草稿，留在渲染层）
  const [activeTab, setActiveTab] = useState<ModelConfigTab>(getInitialModelConfigTab)
  const [modelConfigurationHeight, setModelConfigurationHeight] = useState<number>()
  const [visibleModelCount, setVisibleModelCount] = useState(20)
  // 模型列表上方的筛选键：全部 / 使用中 / 视觉 / 嵌入，只影响显示
  const [modelKindFilter, setModelKindFilter] = useState<ModelKindFilter>('all')
  const shownModels = useMemo(
    () =>
      filteredModels.filter((model) => {
        if (modelKindFilter === 'used') return isModelUsed(model.name)
        if (modelKindFilter === 'visual') return model.model_type === 'llm' && Boolean(model.visual)
        if (modelKindFilter === 'embedding') return model.model_type !== 'llm'
        return true
      }),
    [filteredModels, isModelUsed, modelKindFilter]
  )
  const visibleModels = useMemo(
    () => shownModels.slice(0, visibleModelCount),
    [shownModels, visibleModelCount]
  )
  // 全选只作用于当前筛选出的模型
  const toggleShownSelectAll = () => {
    const indices = shownModels.map((shown) => models.findIndex((model) => model === shown))
    const allSelected = indices.length > 0 && indices.every((index) => selectedModels.has(index))
    mc.setSelectedModels(allSelected ? new Set() : new Set(indices))
  }

  const modelListFilterKey = `${modelProviderFilter}:${searchQuery}:${modelKindFilter}`
  const [seenModelListFilterKey, setSeenModelListFilterKey] = useState(modelListFilterKey)
  if (seenModelListFilterKey !== modelListFilterKey) {
    setSeenModelListFilterKey(modelListFilterKey)
    setVisibleModelCount(20)
  }

  const handleModelListScroll = (event: UIEvent<HTMLElement>) => {
    const target = event.currentTarget
    if (target.scrollHeight - target.scrollTop - target.clientHeight > 160) return

    setVisibleModelCount((current) => Math.min(current + 20, filteredModels.length))
  }

  const [advancedModelSettingsVisible, setAdvancedModelSettingsVisible] = useState(false)
  const [advancedTaskSettingsVisible, setAdvancedTaskSettingsVisible] = useState(false)
  const [selectedTaskName, setSelectedTaskName] = useState('replyer')
  // 模型库里每个模型负责哪些工作，和「分工」页互相对应
  const getModelTasks = (modelName: string) =>
    Object.entries(taskConfig ?? {})
      .filter(([task, config]) => task !== 'web_search' && config?.model_list?.includes(modelName))
      .map(([task]) => taskShortName(task))
  // 分工页编辑抽屉里正在编辑的工作；搜索定位到某项工作时直接打开它
  const [taskDrawerTask, setTaskDrawerTask] = useState<string | null>(null)
  const [extraParamsDialogOpen, setExtraParamsDialogOpen] = useState(false)
  // 用户是否手动编辑过模型名称；编辑过后，名称不再跟随模型标识符自动填充
  const [modelNameTouched, setModelNameTouched] = useState(false)
  const [createVersionDialogOpen, setCreateVersionDialogOpen] = useState(false)
  const [manageVersionsDialogOpen, setManageVersionsDialogOpen] = useState(false)
  const visibleTaskFields = useMemo(
    () =>
      taskConfigSchema?.fields.filter(
        (field) =>
          field.type === 'object' &&
          (advancedTaskSettingsVisible || !field.advanced || (taskConfig?.[field.name]?.model_list.length ?? 0) > 0)
      ).sort((a, b) => Number(a.name === 'web_search') - Number(b.name === 'web_search')) ?? [],
    [advancedTaskSettingsVisible, taskConfig, taskConfigSchema]
  )
  const selectedTaskField =
    visibleTaskFields.find((field) => field.name === selectedTaskName) ?? visibleTaskFields[0]

  if (selectedTaskField && selectedTaskField.name !== selectedTaskName) {
    setSelectedTaskName(selectedTaskField.name)
  }

  useEffect(() => {
    const searchParams = new URLSearchParams(routeSearch.startsWith('?') ? routeSearch.slice(1) : routeSearch)
    const tab = searchParams.get('tab')
    const fieldTab = searchFieldPath ? getModelConfigTabForField(searchFieldPath) : ''
    const searchedTaskName = searchFieldPath.match(/^model_task_config\.([^.]+)/)?.[1]
    const nextTab: ModelConfigTab = fieldTab
      ? fieldTab === 'tasks'
        ? 'tasks'
        : 'configuration'
      : tab === 'configuration' || tab === 'models' || tab === 'providers'
        ? 'configuration'
        : 'tasks'

    const frameId = window.requestAnimationFrame(() => {
      setActiveTab(nextTab)
      if (searchFieldPath) {
        setAdvancedModelSettingsVisible(true)
        setAdvancedTaskSettingsVisible(true)
      }
      if (searchedTaskName) {
        setSelectedTaskName(searchedTaskName)
        setTaskDrawerTask(searchedTaskName)
      }
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [routeSearch, searchFieldPath])

  const [seenSearchFieldPath, setSeenSearchFieldPath] = useState(searchFieldPath)
  if (seenSearchFieldPath !== searchFieldPath) {
    setSeenSearchFieldPath(searchFieldPath)
    const searchedSchemaTaskName = searchFieldPath.match(/^model_task_config\.([^.]+)/)?.[1]
    if (searchedSchemaTaskName && taskConfigSchema?.fields.some((field) => field.name === searchedSchemaTaskName)) {
      setSelectedTaskName(searchedSchemaTaskName)
    }
  }

  useEffect(() => {
    if (
      loading ||
      !searchFieldPath ||
      scrolledSearchFieldRef.current === searchFieldPath
    ) {
      return
    }

    let nestedFrameId = 0
    const frameId = window.requestAnimationFrame(() => {
      nestedFrameId = window.requestAnimationFrame(() => {
        if (scrollToConfigSearchField(searchFieldPath)) {
          scrolledSearchFieldRef.current = searchFieldPath
        }
      })
    })

    return () => {
      window.cancelAnimationFrame(frameId)
      if (nestedFrameId) {
        window.cancelAnimationFrame(nestedFrameId)
      }
    }
  }, [activeTab, advancedModelSettingsVisible, advancedTaskSettingsVisible, loading, searchFieldPath, selectedTaskName])
  const [newVersionLabel, setNewVersionLabel] = useState('')
  const [deletingVersionId, setDeletingVersionId] = useState<string | null>(null)

  useLayoutEffect(() => {
    const updateConfigurationHeight = () => {
      if (!modelConfigurationRef.current) return
      if (!window.matchMedia('(min-width: 1024px)').matches) {
        setModelConfigurationHeight(undefined)
        return
      }

      const top = modelConfigurationRef.current.getBoundingClientRect().top
      const page = modelConfigurationRef.current.closest<HTMLElement>('[data-model-config-page="true"]')
      if (!page) return

      const pageBottom = page.getBoundingClientRect().bottom
      const pageBottomPadding = parseFloat(window.getComputedStyle(page).paddingBottom) || 0
      setModelConfigurationHeight(Math.max(pageBottom - pageBottomPadding - top, 0))
    }

    updateConfigurationHeight()
    const frameId = window.requestAnimationFrame(updateConfigurationHeight)
    window.addEventListener('resize', updateConfigurationHeight)
    window.visualViewport?.addEventListener('resize', updateConfigurationHeight)

    return () => {
      window.cancelAnimationFrame(frameId)
      window.removeEventListener('resize', updateConfigurationHeight)
      window.visualViewport?.removeEventListener('resize', updateConfigurationHeight)
    }
  }, [activeTab, loading])

  const providerModelCounts = useMemo(() => {
    const counts = new Map<string, number>()
    models.forEach((model) => counts.set(model.api_provider, (counts.get(model.api_provider) ?? 0) + 1))
    return counts
  }, [models])
  const selectedProviderInfo = apiProviders.find((provider) => provider.name === modelProviderFilter)
  const selectedProviderIndex = selectedProviderInfo
    ? apiProviders.findIndex((provider) => provider.name === selectedProviderInfo.name)
    : -1

  useEffect(() => {
    if (modelProviderFilter && !apiProviders.some((provider) => provider.name === modelProviderFilter)) {
      setModelProviderFilter('')
    }
  }, [apiProviders, modelProviderFilter, setModelProviderFilter])

  // 模型列表获取 (使用 hook 封装的逻辑)
  const {
    availableModels,
    fetchingModels,
    modelFetchError,
    matchedTemplate,
    fetchModelsForProvider,
    clearModels,
  } = useModelFetcher({ getProviderConfig })

  const selectedProviderConfig = editingModel?.api_provider
    ? getProviderConfig(editingModel.api_provider)
    : undefined
  const selectedClientType = selectedProviderConfig?.client_type
  const selectedThinkingTemplate = findTemplateByBaseUrl(selectedProviderConfig?.base_url ?? '')
  const [thinkingProbeRunning, setThinkingProbeRunning] = useState(false)
  const modelExtraParams = editingModel?.extra_params || {}
  const editingModelType = editingModel?.model_type
  const editingModelIsEmbedding = editingModelType === 'embedding' || editingModelType === 'image_embedding'
  const deepSeekClientType: DeepSeekClientType | null =
    !editingModelIsEmbedding && selectedThinkingTemplate?.id === 'deepseek' &&
    (selectedClientType === 'openai' || selectedClientType === 'openai_responses')
      ? selectedClientType
      : null
  // 预置思考格式直接匹配当前提供商地址；无预置时显示自动测试入口。
  // DeepSeek 有专用段（含 Responses 客户端的 reasoning.effort 与联网搜索），不走通用开关
  const thinkingFormatActive: ThinkingFormatConfig | null =
    !editingModelIsEmbedding && selectedThinkingTemplate?.id !== 'deepseek'
      ? resolveThinkingFormatForModel(selectedThinkingTemplate, editingModel?.model_identifier ?? '')
      : null
  const thinkingEnabled = thinkingFormatActive
    ? isThinkingEnabled(modelExtraParams, thinkingFormatActive)
    : false
  // 思考力度仅在配置了力度参数时显示；思考关闭时置灰
  const thinkingEffortOptions: string[] =
    thinkingFormatActive?.kind === 'reasoning_effort'
      ? (thinkingFormatActive.efforts ?? [])
      : thinkingFormatActive?.kind === 'thinking_type' && thinkingFormatActive.effortParam
        ? (thinkingFormatActive.efforts ?? [])
        : []
  const thinkingEffort = thinkingFormatActive ? getThinkingEffort(modelExtraParams, thinkingFormatActive) : ''
  const thinkingCanDisable =
    thinkingFormatActive?.kind === 'enable_thinking' ||
    (thinkingFormatActive?.kind === 'thinking_type' && thinkingFormatActive.canDisable === true)
  const thinkingSwitchInteractive = thinkingFormatActive !== null && (thinkingCanDisable || !thinkingEnabled)
  const thinkingBudget = thinkingFormatActive ? getThinkingBudget(modelExtraParams, thinkingFormatActive) : null
  const thinkingExtraParamsError = thinkingFormatActive
    ? validateThinkingParams(modelExtraParams, thinkingFormatActive)
    : null
  const deepSeekWebSearchEnabled = deepSeekClientType === 'openai_responses'
    ? isDeepSeekWebSearchEnabled(modelExtraParams)
    : false
  const deepSeekThinkingEnabled = deepSeekClientType
    ? isDeepSeekThinkingEnabled(modelExtraParams, deepSeekClientType)
    : false
  const deepSeekReasoningEffort = deepSeekClientType
    ? getDeepSeekReasoningEffort(modelExtraParams, deepSeekClientType)
    : 'high'
  const deepSeekExtraParamsError = deepSeekClientType
    ? validateDeepSeekExtraParams(modelExtraParams, deepSeekClientType)
    : null
  const deepSeekToolsInvalid =
    modelExtraParams.tools !== undefined && !Array.isArray(modelExtraParams.tools)

  const updateModelExtraParams = (
    updater: (params: Record<string, unknown>) => Record<string, unknown>
  ) => {
    setEditingModel((previousModel) => previousModel
      ? {
          ...previousModel,
          extra_params: updater(previousModel.extra_params || {}),
        }
      : null
    )
  }

  const updatePricePeriods = (updater: (periods: ModelPricePeriod[]) => ModelPricePeriod[]) => {
    setEditingModel((previousModel) => previousModel
      ? { ...previousModel, price_periods: updater(previousModel.price_periods ?? []) }
      : null
    )
    setFormErrors((previous) => ({ ...previous, price_periods: undefined }))
  }

  // 打开模型编辑对话框：重置高级设置可见性后委托核心 hook
  const openEditDialog = (
    model: ModelInfo | null,
    index: number | null,
    preferredProvider?: string,
    modelType: ModelInfo['model_type'] = 'llm'
  ) => {
    setModelNameTouched(false)
    mc.openEditDialog(model, index, () => setAdvancedModelSettingsVisible(false), preferredProvider, modelType)
  }

  const openAddModelDialog = (type: ModelCreationType) => {
    openEditDialog(null, null, modelProviderFilter || undefined, type)
  }

  const openProviderDialog = (provider: APIProvider | null, index: number | null) => {
    openProviderDialogBase(provider, index)
  }

  const handleModelEditDialogOpenChange = (open: boolean) => {
    // 移动端触摸嵌套弹窗时，Radix 可能把内层操作判定为外层的 outside-interaction。
    // 额外参数弹窗打开期间只允许内层处理关闭，避免保存参数时连带退出模型编辑。
    if (!open && extraParamsDialogOpen) return
    handleEditDialogClose(open)
  }

  // 当选择的提供商变化时，获取模型列表
  useEffect(() => {
    if (editDialogOpen && editingModel?.api_provider) {
      fetchModelsForProvider(editingModel.api_provider)
    }
  }, [editDialogOpen, editingModel?.api_provider, fetchModelsForProvider])

  const handleActiveTabChange = (value: string) => {
    const nextTab = MODEL_CONFIG_TABS.includes(value as ModelConfigTab)
      ? (value as ModelConfigTab)
      : 'tasks'
    setActiveTab(nextTab)
    const nextUrl = nextTab === 'tasks' ? '/config/model' : '/config/model?tab=configuration'
    window.history.replaceState(null, '', nextUrl)
  }

  const handleSaveProviderEdit = async (provider: APIProvider, index: number | null, templateId?: string) => {
    const savedProvider = await saveProviderEdit(provider, index, templateId)
    if (!savedProvider || index !== null) return

    // 新建成功后衔接模型配置，显式指定供应商，避免沿用之前的选择。
    handleActiveTabChange('configuration')
    setModelProviderFilter(savedProvider.name)
    setSearchQuery('')
    clearModels()
    openEditDialog(null, null, savedProvider.name)
  }

  const formatVersionTime = (timestamp?: number) => {
    if (!timestamp) {
      return '-'
    }
    return new Date(timestamp * 1000).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const handleCreateVersion = async () => {
    await handleCreateConfigVersion(newVersionLabel)
    setNewVersionLabel('')
    setCreateVersionDialogOpen(false)
  }

  const deletingVersion = deletingVersionId
    ? configVersions.find((version) => version.id === deletingVersionId)
    : null

  // Tour 引导 (使用 hook 封装的逻辑)
  const { startTour: handleStartTour, stopTour: handleStopTour, isRunning: tourIsRunning } = useModelTour({
    onOpenEditDialog: () => openEditDialog(null, null),
    onCloseEditDialog: () => setEditDialogOpen(false),
    onOpenProviderDialog: () => {
      if (!providerDialogOpen) openProviderDialog(null, null)
    },
    onCloseProviderDialog: () => setProviderDialogOpen(false),
    onOpenProvidersTab: () => handleActiveTabChange('configuration'),
    onOpenModelsTab: () => handleActiveTabChange('configuration'),
    onOpenTasksTab: () => handleActiveTabChange('tasks'),
    onOpenTaskEditor: () => setTaskDrawerTask('replyer'),
    onCloseTaskEditor: () => setTaskDrawerTask(null),
  })

  if (loading) {
    return (
      <ScrollArea className="h-full">
        <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
          <div className="flex items-center justify-center h-64">
            <ThinkingIllustration size="lg" />
          </div>
        </div>
      </ScrollArea>
    )
  }

  return (
    <div
      className="h-full overflow-y-auto lg:overflow-hidden"
      onScroll={(event) => {
        // 小屏由整页滚动触发追加模型，桌面端仍由列表自身触发。
        if (activeTab === 'configuration' && !window.matchMedia('(min-width: 1024px)').matches) {
          handleModelListScroll(event)
        }
      }}
    >
      <div
        data-model-config-page="true"
        className="flex min-h-full flex-col gap-4 p-4 sm:gap-6 sm:p-6 lg:h-full lg:overflow-hidden"
      >
        {/* 已保存配置的诊断 */}
        {configWarnings.length > 0 && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <strong>模型配置需要修正</strong>
                <div className="mt-2 space-y-1">
                  {configWarnings.map((warning) => (
                    <div key={warning} className="text-sm">
                      {warning}
                    </div>
                  ))}
                </div>
              </div>
              {invalidModelRefs.length > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0 bg-background hover:bg-accent"
                  onClick={handleRemoveInvalidRefs}
                >
                  一键清理
                </Button>
              )}
            </AlertDescription>
          </Alert>
        )}
        
        {/* 空任务警告 */}
        {emptyTasks.length > 0 && (
          <Alert variant="default" className="border-yellow-500/50 bg-yellow-500/10">
            <AlertTriangle className="h-4 w-4 text-yellow-600" />
            <AlertDescription>
              <strong className="text-yellow-600">以下任务未配置模型</strong>
              <div className="mt-2 text-sm">
                {emptyTasks.join('、')} 还未分配模型，这些功能将无法正常工作。
              </div>
            </AlertDescription>
          </Alert>
        )}



        {/* 标签页 */}
        <Tabs
          value={activeTab}
          onValueChange={handleActiveTabChange}
          className="flex min-h-0 w-full flex-1 flex-col"
        >
          <div
            data-model-config-tabs-bar="true"
            className="sticky top-0 z-40 -mx-4 flex w-[calc(100%+2rem)] flex-wrap items-end gap-2 bg-background px-4 pt-2 sm:-mx-6 sm:w-[calc(100%+3rem)] sm:px-6"
          >
            {/* 「分工」决定每项工作由谁来做，「模型库」管理可用的厂商和模型；分工在前，是这一页的主入口。 */}
            <DashboardFolderTabBar size="large" data-model-config-tabs-list="true" aria-label="模型页">
              <DashboardFolderTab
                value="tasks"
                data-tour="tasks-tab-trigger"
                icon={<ListChecks />}
              >
                分工
              </DashboardFolderTab>
              <DashboardFolderTab
                value="configuration"
                data-tour="providers-tab-trigger"
                icon={<Boxes />}
              >
                模型库
              </DashboardFolderTab>
            </DashboardFolderTabBar>
            <div className="flex w-full min-w-0 items-center gap-2 pb-2 sm:ml-auto sm:w-auto">
              <SplitDropdownSelect
                value="active"
                label="Bot 任务配置副本"
                controlSize="sm"
                icon={<History />}
                className="flex-1 sm:w-[190px] sm:flex-none"
                disabled={versionsLoading || saving || autoSaving || Boolean(switchingConfigVersion)}
                onValueChange={(value) => {
                  if (value !== 'active') {
                    void handleSwitchConfigVersion(value)
                  }
                }}
                options={[
                  { value: 'active', label: activeConfigVersion?.label || '默认配置' },
                  ...configVersions.map((version) => ({
                    value: version.id,
                    label: version.label,
                    description: formatVersionTime(version.modified_at),
                    disabled: !version.valid,
                  })),
                ]}
                actions={[
                  {
                    id: 'save',
                    label: '保存副本',
                    ariaLabel: '保存当前配置副本',
                    icon: creatingConfigVersion ? <Loader2 className="animate-spin" /> : <Copy />,
                    disabled: saving || autoSaving || creatingConfigVersion,
                    onSelect: () => setCreateVersionDialogOpen(true),
                  },
                  {
                    id: 'manage',
                    label: '管理副本',
                    ariaLabel: '管理配置副本',
                    icon: <History />,
                    onSelect: () => setManageVersionsDialogOpen(true),
                  },
                ]}
              />
            </div>
          </div>
          {/* 厂商与模型合并配置视图 */}
          <TabsContent
            value="configuration"
            className="mt-0 flex min-h-0 flex-1 flex-col overflow-visible lg:overflow-hidden"
          >
            <DashboardFolderPanel className="flex min-h-0 flex-1 flex-col p-3 sm:p-4">
            <div
              ref={modelConfigurationRef}
              data-model-config-layout="true"
              className="grid min-h-0 gap-5 lg:grid-cols-[17rem_minmax(0,1fr)] lg:overflow-hidden"
              style={
                modelConfigurationHeight === undefined
                  ? undefined
                  : {
                      height: modelConfigurationHeight,
                      maxHeight: modelConfigurationHeight,
                    }
              }
            >
              <ProviderSidebar
                providers={apiProviders}
                modelCounts={providerModelCounts}
                selectedProvider={modelProviderFilter}
                testingProviders={testingProviders}
                testResults={testResults}
                onSelectProvider={setModelProviderFilter}
                onAdd={() => openProviderDialog(null, null)}
                onAddWebSearch={() => setWebSearchEditor({ name: null })}
                onEditWebSearch={(name) => setWebSearchEditor({ name })}
                webConfig={mc.webConfig}
                assignedWebService={taskConfig?.web_search?.model_list[0] ?? ''}
              />

              <section
                className="flex min-h-0 min-w-0 flex-col gap-2 lg:h-full lg:overflow-hidden"
                data-tour="models-tab-trigger"
              >
          {/* 列表标签、筛选键、搜索和添加入口排在同一行 */}
          <div data-library-label="true" className="flex-wrap gap-y-2">
            <b>MODELS</b>
            <h2>模型列表</h2>
            <div role="group" aria-label="筛选模型" data-library-filters="true" className="ml-1">
              {MODEL_KIND_FILTERS.map((item) => (
                <Button
                  key={item.value}
                  type="button"
                  size="xs"
                  variant={modelKindFilter === item.value ? 'default' : 'outline'}
                  aria-pressed={modelKindFilter === item.value}
                  onClick={() => setModelKindFilter(item.value)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
            <div className="ml-auto flex min-w-0 flex-wrap items-center gap-2">
              {searchQuery && (
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  找到 {shownModels.length} 个
                </span>
              )}
              <div className="relative w-full sm:w-60">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="搜索名称、标识符或厂商"
                  aria-label="搜索模型"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  controlSize="sm"
                  className="pl-8"
                />
              </div>
              {selectedModels.size > 0 && (
                <Button onClick={openBatchDeleteDialog} size="sm" variant="destructive">
                  <Trash2 className="h-4 w-4" strokeWidth={2} fill="none" />
                  批量删除 ({selectedModels.size})
                </Button>
              )}
              {/* 手机上由页头右上角的「+」添加 */}
              <AddModelMenu
                onAdd={openAddModelDialog}
                disabled={apiProviders.length === 0}
                className="hidden md:inline-flex"
                dataTour="add-model-button"
              />
            </div>
          </div>

          <div
            className="min-h-0 pb-1 lg:h-0 lg:flex-1 lg:overflow-y-auto lg:overscroll-contain"
            data-config-field-path="models"
            onScroll={handleModelListScroll}
          >
            {selectedProviderInfo && (
              <div className="mb-4 rounded-lg border px-4 py-3">
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <h2 className="font-semibold">{selectedProviderInfo.name}</h2>
                      <span className="text-muted-foreground text-xs">
                        {providerModelCounts.get(selectedProviderInfo.name) ?? 0} 个模型
                      </span>
                      <span className="text-muted-foreground text-xs">
                        客户端类型：{selectedProviderInfo.client_type}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-1 truncate text-xs" title={selectedProviderInfo.base_url}>
                      {selectedProviderInfo.base_url}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="icon-sm"
                      onClick={() => handleTestProviderConnection(selectedProviderInfo.name)}
                      disabled={testingProviders.has(selectedProviderInfo.name)}
                      title="测试连接"
                      aria-label={`测试厂商 ${selectedProviderInfo.name} 连接`}
                    >
                      {testingProviders.has(selectedProviderInfo.name) ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Zap className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon-sm"
                      onClick={() => openProviderDialog(selectedProviderInfo, selectedProviderIndex)}
                      title="编辑厂商"
                      aria-label={`编辑厂商 ${selectedProviderInfo.name}`}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      size="icon-sm"
                      onClick={() => openProviderDeleteDialog(selectedProviderIndex)}
                      title="删除厂商"
                      aria-label={`删除厂商 ${selectedProviderInfo.name}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </div>
            )}
            <ModelCardList
              paginatedModels={visibleModels}
              allModels={models}
              onEdit={openEditDialog}
              onDelete={openDeleteDialog}
              onTest={handleTestModelCapability}
              isModelUsed={isModelUsed}
              getModelTasks={getModelTasks}
              testingModels={testingModels}
              modelTestResults={modelTestResults}
              searchQuery={searchQuery}
            />

            {/* 模型列表 - 桌面端表格视图 */}
            <ModelTable
              paginatedModels={visibleModels}
              allModels={models}
              filteredModels={shownModels}
              selectedModels={selectedModels}
              onEdit={openEditDialog}
              onDelete={openDeleteDialog}
              onTest={handleTestModelCapability}
              onToggleSelection={toggleModelSelection}
              onToggleSelectAll={toggleShownSelectAll}
              isModelUsed={isModelUsed}
              getModelTasks={getModelTasks}
              testingModels={testingModels}
              modelTestResults={modelTestResults}
              searchQuery={searchQuery}
              onAdd={openAddModelDialog}
            />
          </div>

              </section>
            </div>
            </DashboardFolderPanel>
          </TabsContent>

        {/* 模型任务配置标签页 */}
        <TabsContent
          value="tasks"
          className="mt-0 flex min-h-0 flex-1 flex-col gap-3 overflow-visible lg:overflow-hidden"
        >
          <DashboardFolderPanel className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
          {taskConfig && taskConfigSchema && (
            <TaskAssignmentBoard
              fields={taskConfigSchema.fields}
              taskConfig={taskConfig}
              models={models}
              webServices={Object.keys(mc.webConfig.services)}
              openTask={taskDrawerTask}
              onOpenTaskChange={(task) => {
                if (task === null && tourIsRunning) handleStopTour()
                setTaskDrawerTask(task)
              }}
              onChange={updateTaskConfig}
              onAddWebService={() => setWebSearchEditor({ name: null })}
              hasProviders={providers.length > 0}
              onStartTour={handleStartTour}
              tourRunning={tourIsRunning}
              fallbackLabel={(field) => resolveFieldLabel(field, i18n.language)}
              renderExtra={(task) => (
                <>
                  {task === 'embedding' && (
                    <EmbeddingBuildProgress selectedEmbeddingModel={taskConfig.embedding?.model_list[0] ?? ''} />
                  )}
                  {task !== 'web_search' && (taskConfig[task]?.model_list.length ?? 0) > 0 && (
                    <TaskUsageStats
                      key={task}
                      taskName={task}
                      summary={
                        (task === 'planner' || task === 'replyer') && (
                          <TaskRuntimeSummary
                            taskName={task}
                            modelList={taskConfig[task]?.model_list ?? []}
                            models={models}
                          />
                        )
                      }
                    />
                  )}
                </>
              )}
            />
          )}
          </DashboardFolderPanel>
        </TabsContent>
      </Tabs>

      <Dialog open={createVersionDialogOpen} onOpenChange={setCreateVersionDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>保存Bot 任务配置副本</DialogTitle>
            <DialogDescription>
              当前启用的 model_config.toml 会复制到 config/versions/model，之后可随时切换回来。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="model_config_version_label">副本名称</Label>
            <Input
              id="model_config_version_label"
              value={newVersionLabel}
              onChange={(event) => setNewVersionLabel(event.target.value)}
              placeholder="例如：生产环境Bot 任务配置副本"
              maxLength={80}
            />
          </div>
          <DialogFooter className="flex-row justify-end gap-2 space-x-0">
            <Button
              variant="outline"
              className="flex-1 sm:flex-none"
              onClick={() => setCreateVersionDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              data-dialog-action="confirm"
              className="flex-1 sm:flex-none"
              disabled={creatingConfigVersion}
              onClick={handleCreateVersion}
            >
              {creatingConfigVersion ? '保存中...' : '保存副本'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={manageVersionsDialogOpen} onOpenChange={setManageVersionsDialogOpen}>
        <DialogContent className="max-w-[95vw] sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Bot 任务配置副本</DialogTitle>
            <DialogDescription>
              副本保存当前 Bot 的任务选择和参数，不包含公共供应商、模型与联网服务定义；切换前会自动归档当前配置。
            </DialogDescription>
          </DialogHeader>
          <DialogBody viewportClassName="max-h-[60vh] pr-3 sm:pr-4">
            <div className="space-y-2 py-2">
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">当前启用</p>
                  <p className="text-xs text-muted-foreground">
                    {activeConfigVersion?.label || '默认配置'}
                  </p>
                </div>
                <Badge variant="secondary">启用中</Badge>
              </div>

              {configVersions.length === 0 ? (
                <div className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
                  暂无未启用副本
                </div>
              ) : (
                configVersions.map((version) => (
                  <div key={version.id} className="flex flex-col gap-3 rounded-md border px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0 space-y-1">
                      <div className="flex min-w-0 items-center gap-2">
                        <p className="truncate text-sm font-medium">{version.label}</p>
                        {!version.valid && <Badge variant="destructive">无效</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {formatVersionTime(version.modified_at)}
                      </p>
                      {version.error && (
                        <p className="line-clamp-2 text-xs text-destructive">{version.error}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!version.valid || switchingConfigVersion === version.id}
                        onClick={() => void handleSwitchConfigVersion(version.id)}
                      >
                        {switchingConfigVersion === version.id ? '切换中...' : '切换'}
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        size="icon"
                        className="h-9 w-9"
                        aria-label={`删除副本 ${version.label}`}
                        onClick={() => setDeletingVersionId(version.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </DialogBody>
          <DialogFooter className="flex-row justify-end gap-2 space-x-0">
            <Button variant="outline" onClick={() => setManageVersionsDialogOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={selectedModelTestResult !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedModelTestResult(null)
        }}
      >
        <DialogContent className="max-w-[95vw] gap-3 p-4 sm:gap-4 sm:p-6 sm:[--dialog-width:38rem]">
          <DialogHeader>
            <DialogTitle>模型测试详情</DialogTitle>
            <DialogDescription>
              {selectedModelTestResult?.model_name || '模型'} 的最近一次能力测试结果
            </DialogDescription>
          </DialogHeader>
          {selectedModelTestResult && (
            <DialogBody viewportClassName="max-h-[70vh] pr-3 sm:pr-4">
              <div className="space-y-4 py-2 text-sm">
                {(() => {
                  const isEmbeddingTest =
                    selectedModelTestResult.test_kind === 'text_embedding' ||
                    selectedModelTestResult.test_kind === 'image_embedding' ||
                    (selectedModelTestResult.embedding_pairs?.length ?? 0) > 0
                  return (
                    <>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div>
                          <span className="text-muted-foreground">测试状态</span>
                          <p className={selectedModelTestResult.success ? 'font-medium text-green-600' : 'font-medium text-destructive'}>
                            {selectedModelTestResult.success ? '通过' : '未通过'}
                          </p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">耗时</span>
                          <p className="font-medium">
                            {selectedModelTestResult.latency_ms != null ? `${(selectedModelTestResult.latency_ms / 1000).toFixed(2)}s` : '-'}
                          </p>
                        </div>
                        {!isEmbeddingTest && (
                          <>
                            <div>
                              <span className="text-muted-foreground">{selectedModelTestResult.test_kind === 'replyer' ? '测试方式' : '工具调用'}</span>
                              <p className={selectedModelTestResult.test_kind === 'replyer' || selectedModelTestResult.tool_call_ok ? 'font-medium' : 'font-medium text-destructive'}>
                                {selectedModelTestResult.test_kind === 'replyer' ? '模拟 replyer 任务（仅文本回复）' : selectedModelTestResult.tool_call_ok ? '已返回测试工具调用' : '未返回测试工具调用'}
                              </p>
                            </div>
                            <div>
                              <span className="text-muted-foreground">视觉测试</span>
                              <p className="font-medium">
                                {selectedModelTestResult.visual_tested ? '已附加测试图片' : '未附加图片'}
                              </p>
                            </div>
                            <div>
                              <span className="text-muted-foreground">Prompt tokens</span>
                              <p className="font-medium tabular-nums">{selectedModelTestResult.prompt_tokens}</p>
                            </div>
                            <div>
                              <span className="text-muted-foreground">Completion tokens</span>
                              <p className="font-medium tabular-nums">{selectedModelTestResult.completion_tokens}</p>
                            </div>
                          </>
                        )}
                      </div>

                      {selectedModelTestResult.error && (
                        <div>
                          <h4 className="mb-2 text-sm font-semibold">完整错误信息</h4>
                          <pre className="bg-muted overflow-auto rounded-md p-3 text-xs break-all whitespace-pre-wrap">
                            {selectedModelTestResult.error}
                          </pre>
                        </div>
                      )}

                      {selectedModelTestResult.reasoning && (
                        <div>
                          <h4 className="mb-2 text-sm font-semibold">推理内容</h4>
                          <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                            {selectedModelTestResult.reasoning}
                          </pre>
                        </div>
                      )}

                      {isEmbeddingTest && (selectedModelTestResult.embedding_pairs?.length ?? 0) > 0 && (
                        <div>
                          <h4 className="mb-2 text-sm font-semibold">
                            嵌入对比结果{selectedModelTestResult.embedding_dimension ? `（向量维度 ${selectedModelTestResult.embedding_dimension}）` : ''}
                          </h4>
                          <div className="space-y-1">
                            {selectedModelTestResult.embedding_pairs!.map((pair) => (
                              <div
                                key={pair.label}
                                className="bg-muted flex items-center justify-between gap-3 rounded-md px-3 py-2 text-xs"
                              >
                                <span className="min-w-0 truncate">{pair.label}</span>
                                <span className="tabular-nums font-medium">{pair.similarity.toFixed(4)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {!isEmbeddingTest && selectedModelTestResult.tool_calls.length > 0 && (
                        <div>
                          <h4 className="mb-2 text-sm font-semibold">工具调用返回</h4>
                          <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                            {JSON.stringify(selectedModelTestResult.tool_calls, null, 2)}
                          </pre>
                        </div>
                      )}

                      <div>
                        <h4 className="mb-2 text-sm font-semibold">{isEmbeddingTest ? '测试结果' : '模型文本返回'}</h4>
                        <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                          {selectedModelTestResult.response || '（无文本返回）'}
                        </pre>
                      </div>
                    </>
                  )
                })()}
              </div>
            </DialogBody>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setSelectedModelTestResult(null)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ProviderForm
        open={providerDialogOpen}
        onOpenChange={setProviderDialogOpen}
        editingProvider={editingProvider}
        editingIndex={editingProviderIndex}
        providers={apiProviders}
        onSave={handleSaveProviderEdit}
        tourState={{ isRunning: tourIsRunning }}
      />

      {/* 删除提供商确认对话框 */}
      <AlertDialog open={providerDeleteDialogOpen} onOpenChange={setProviderDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除提供商</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除提供商"{deletingProviderIndex !== null ? apiProviders[deletingProviderIndex]?.name : ''}"吗？
              如果该提供商下存在模型，确认时会提示一并处理关联模型。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmProviderDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除提供商影响确认对话框 */}
      <AlertDialog open={deleteConfirmState.isOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              删除提供商会同时移除关联模型
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>
                  将删除 {deleteConfirmState.providersToDelete.length} 个提供商，并移除
                  {' '}{deleteConfirmState.affectedModels.length} 个使用这些提供商的模型。
                </p>
                {deleteConfirmState.affectedModels.length > 0 && (
                  <div className="rounded-md bg-muted p-3 text-muted-foreground">
                    {deleteConfirmState.affectedModels.slice(0, 8).map((model) => (
                      <div key={model.name}>
                        {model.name} ({model.api_provider})
                      </div>
                    ))}
                    {deleteConfirmState.affectedModels.length > 8 && (
                      <div>还有 {deleteConfirmState.affectedModels.length - 8} 个模型...</div>
                    )}
                  </div>
                )}
                <p className="font-medium text-foreground">
                  关联模型会从模型列表和任务分配中移除，此操作无法撤销。
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={handleCancelDeleteProviderImpact}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDeleteProviderImpact}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 编辑模型对话框 */}
      <Dialog open={editDialogOpen} onOpenChange={handleModelEditDialogOpenChange}>
        <DialogContent
          className="max-w-[95vw] gap-3 p-4 sm:gap-4 sm:p-6 sm:[--dialog-width:64rem]"
          data-tour="model-dialog"
          // 模型编辑是数据录入弹窗，只通过关闭按钮和底部操作显式退出。
          // 始终拦截 outside-interaction，避免内层弹窗关闭后的延迟触摸事件击穿外层。
          preventOutsideClose
          confirmOnEnter
          // 无描述文案，显式置空避免 Radix 的 aria-describedby 缺失告警
          aria-describedby={undefined}
        >
          <DialogHeader>
            <DialogTitle>
              {editingIndex !== null ? '编辑模型' : editingModelIsEmbedding ? '添加嵌入模型' : '添加 LLM 模型'}
            </DialogTitle>
            {/* 「高级」开关浮在右上角，与 DialogContent 内置的关闭按钮同行 */}
            {!editingModelIsEmbedding && (
            <label
              htmlFor="advanced_model_settings"
              className="absolute top-4 right-12 flex cursor-pointer items-center gap-2 text-sm font-semibold"
            >
              高级设置
              <Switch
                id="advanced_model_settings"
                checked={advancedModelSettingsVisible}
                onCheckedChange={setAdvancedModelSettingsVisible}
              />
            </label>
            )}
          </DialogHeader>

          <DialogBody type="auto" data-form-scroll="true" viewportClassName="min-h-0 flex-1 pr-0 [&>div]:!block">
          <FormColumns className="py-2 sm:py-3">
          <FormColumn>
          <FormSection title="模型">
            {editingModelIsEmbedding && (
            <FormRow label="模型类型" htmlFor="model_type">
              <Select
                value={editingModelType}
                onValueChange={(value) => setEditingModel((model) => model
                  ? { ...model, model_type: value as ModelInfo['model_type'] }
                  : null
                )}
              >
                <SelectTrigger id="model_type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="embedding">文本嵌入模型</SelectItem>
                  <SelectItem value="image_embedding">图片嵌入模型</SelectItem>
                </SelectContent>
              </Select>
            </FormRow>
            )}

            <FormRow label="提供商" htmlFor="api_provider" required error={formErrors.api_provider} data-tour="model-provider-select">
              <Select
                value={editingModel?.api_provider || ''}
                onValueChange={(value) => {
                  setEditingModel((prev) =>
                    prev
                      ? { ...prev, api_provider: value }
                      : null
                  )
                  // 清空模型列表和错误状态，等待 useEffect 重新获取
                  clearModels()
                  if (formErrors.api_provider) {
                    setFormErrors((prev) => ({ ...prev, api_provider: undefined }))
                  }
                }}
              >
                <SelectTrigger id="api_provider" className={formErrors.api_provider ? 'border-destructive focus-visible:ring-destructive' : ''}>
                  <SelectValue placeholder="选择提供商" />
                </SelectTrigger>
                <SelectContent>
                  {providers.map((provider) => (
                    <SelectItem key={provider} value={provider}>
                      {provider}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>

            <FormRow label="模型标识符" htmlFor="model_identifier" required error={formErrors.model_identifier} data-tour="model-identifier-input">
              <ComboboxInput
                id="model_identifier"
                value={editingModel?.model_identifier || ''}
                onValueChange={(value) => {
                  setEditingModel((prev) => {
                    if (!prev) return null
                    // 名称留空且用户未手动编辑过时，名称跟随标识符自动填充
                    const shouldFillName = !prev.name && !modelNameTouched
                    return {
                      ...prev,
                      model_identifier: value,
                      ...(shouldFillName ? { name: value } : {}),
                    }
                  })
                  if (formErrors.model_identifier) {
                    setFormErrors((prev) => ({ ...prev, model_identifier: undefined }))
                  }
                }}
                options={matchedTemplate?.modelFetcher
                  ? availableModels.map((model) => ({ value: model.id, description: model.name }))
                  : undefined}
                loading={Boolean(matchedTemplate?.modelFetcher) && fetchingModels}
                error={matchedTemplate?.modelFetcher && modelFetchError
                  ? `${modelFetchError}。可直接手动输入，或前往「模型厂商设置」检查 API Key`
                  : undefined}
                onRefresh={matchedTemplate?.modelFetcher
                  ? () => editingModel?.api_provider && fetchModelsForProvider(editingModel.api_provider, true)
                  : undefined}
                placeholder={matchedTemplate?.modelFetcher ? '输入或从列表选择' : 'Qwen/Qwen3-30B-A3B-Instruct-2507'}
                invalid={Boolean(formErrors.model_identifier)}
              />
            </FormRow>

            <FormRow label="名称" htmlFor="model_name" required error={formErrors.name} data-tour="model-name-input">
              <Input
                id="model_name"
                value={editingModel?.name || ''}
                onChange={(e) => {
                  setModelNameTouched(true)
                  setEditingModel((prev) =>
                    prev ? { ...prev, name: e.target.value } : null
                  )
                  if (formErrors.name) {
                    setFormErrors((prev) => ({ ...prev, name: undefined }))
                  }
                }}
                placeholder="默认与标识符相同"
                className={formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}
              />
            </FormRow>
          </FormSection>

          {!editingModelIsEmbedding && (
          <FormSection title="能力">
            <FormRow label="视觉" htmlFor="model_visual">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">能看图片</span>
                <Switch
                  id="model_visual"
                  checked={editingModel?.visual || false}
                  onCheckedChange={(checked) =>
                    setEditingModel((prev) =>
                      prev ? { ...prev, visual: checked } : null
                    )
                  }
                />
              </div>
            </FormRow>

            {deepSeekClientType && (
              <>
                <FormRow label="思考" htmlFor="deepseek_thinking">
                  <div className="flex items-center justify-between gap-3">
                    <Select
                      value={deepSeekReasoningEffort}
                      disabled={!deepSeekThinkingEnabled}
                      onValueChange={(value) => updateModelExtraParams((params) =>
                        setDeepSeekReasoningEffort(
                          params,
                          deepSeekClientType,
                          value as DeepSeekReasoningEffort
                        )
                      )}
                    >
                      <SelectTrigger id="deepseek_reasoning_effort" aria-label="思考力度" className="w-36">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="low">低</SelectItem>
                        <SelectItem value="high">高（默认）</SelectItem>
                        <SelectItem value="max">最高</SelectItem>
                      </SelectContent>
                    </Select>
                    <Switch
                      id="deepseek_thinking"
                      aria-label="启用思考"
                      checked={deepSeekThinkingEnabled}
                      onCheckedChange={(checked) => updateModelExtraParams((params) =>
                        setDeepSeekThinkingEnabled(params, deepSeekClientType, checked)
                      )}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {deepSeekClientType === 'openai_responses' ? '写入 reasoning.effort' : '写入 thinking.type'}
                  </p>
                </FormRow>
                <FormRow label="联网搜索" htmlFor="deepseek_web_search">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground">
                      {deepSeekClientType === 'openai_responses'
                        ? '添加 DeepSeek 原生 web_search 工具'
                        : '仅 DeepSeek Responses API 支持'}
                    </span>
                    <Switch
                      id="deepseek_web_search"
                      aria-label="启用联网搜索"
                      checked={deepSeekWebSearchEnabled}
                      disabled={deepSeekClientType !== 'openai_responses' || deepSeekToolsInvalid}
                      onCheckedChange={(checked) => updateModelExtraParams((params) =>
                        setDeepSeekWebSearchEnabled(params, checked)
                      )}
                    />
                  </div>
                </FormRow>
                {deepSeekExtraParamsError && (
                  <p role="alert" className="text-xs text-destructive">
                    额外参数配置冲突：{deepSeekExtraParamsError}
                  </p>
                )}
              </>
            )}

            {!deepSeekClientType && thinkingFormatActive && (
              <>
                {/* 不支持关闭时将已开启的开关置灰；旧配置若为关闭状态，仍允许切回开启。 */}
                <FormRow label="思考" htmlFor="model_thinking">
                  <div className="flex items-center justify-between gap-3">
                    {thinkingEffortOptions.length > 0 ? (
                      <Select
                        value={thinkingEffort}
                        disabled={!thinkingEnabled}
                        onValueChange={(value) => updateModelExtraParams((params) =>
                          setThinkingEffort(params, thinkingFormatActive, value)
                        )}
                      >
                        <SelectTrigger id="model_thinking_effort" aria-label="思考力度" className="w-36">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {thinkingEffortOptions.map((effort) => (
                            <SelectItem key={effort} value={effort}>
                              {effort}
                              {effort === getThinkingEffort({}, thinkingFormatActive) ? '（默认）' : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : <span />}
                    <Switch
                      id="model_thinking"
                      aria-label="启用思考"
                      checked={thinkingEnabled}
                      disabled={!thinkingSwitchInteractive}
                      onCheckedChange={(checked) => updateModelExtraParams((params) =>
                        setThinkingEnabled(params, thinkingFormatActive, checked)
                      )}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {thinkingFormatActive.kind === 'reasoning_effort'
                      ? '推理模型思考常开，仅支持调整力度档位'
                      : thinkingFormatActive.kind === 'enable_thinking'
                        ? '写入 enable_thinking'
                        : `写入 thinking.type（${thinkingFormatActive.onValue ?? 'enabled'}）`}
                    {thinkingFormatActive.kind === 'thinking_type' && thinkingFormatActive.disableNote
                      ? `。${thinkingFormatActive.disableNote}`
                      : ''}
                  </p>
                </FormRow>

                {/* 思考预算：配置了预算参数的格式显示 */}
                {thinkingFormatActive.budgetParam && (
                  <FormRow label="思考预算" htmlFor="model_thinking_budget">
                    <div className="flex items-center gap-2">
                      <Input
                        id="model_thinking_budget"
                        type="number"
                        min="0"
                        step="1"
                        className="w-36"
                        value={thinkingBudget ?? ''}
                        onChange={(e) => {
                          const value = e.target.value === '' ? null : Math.max(0, parseInt(e.target.value, 10))
                          if (e.target.value === '' || Number.isFinite(value)) {
                            updateModelExtraParams((params) => setThinkingBudget(params, thinkingFormatActive, value))
                          }
                        }}
                        placeholder={thinkingFormatActive.budgetNote ?? '服务商默认'}
                      />
                      <FormUnit>token</FormUnit>
                    </div>
                  </FormRow>
                )}

                {thinkingFormatActive.note && (
                  <p className="text-xs text-muted-foreground">{thinkingFormatActive.note}</p>
                )}

                {thinkingExtraParamsError && (
                  <p role="alert" className="text-xs text-destructive">
                    思考参数配置有误：{thinkingExtraParamsError}
                  </p>
                )}
              </>
            )}

            {editDialogOpen && editingModel && !deepSeekClientType && !thinkingFormatActive &&
              (selectedClientType === 'openai' || selectedClientType === 'openai_responses') && (
              <ThinkingProbeControls
                key={JSON.stringify([editingModel.api_provider, editingModel.model_identifier, selectedClientType, selectedProviderConfig?.base_url, editingModel.force_stream_mode])}
                apiProvider={editingModel.api_provider}
                modelIdentifier={editingModel.model_identifier}
                extraParams={modelExtraParams}
                forceStreamMode={editingModel.force_stream_mode ?? false}
                disabled={saving}
                onBusyChange={setThinkingProbeRunning}
                onChange={(params) => setEditingModel((previousModel) => previousModel
                  ? { ...previousModel, extra_params: params }
                  : null
                )}
              />
            )}
          </FormSection>
          )}
          </FormColumn>

          <FormColumn>
          <FormSection
            title="价格"
            action={<span className="text-xs text-muted-foreground">¥ / 百万 token</span>}
          >
            <FormRow label="默认价格">
              <div className={`grid min-w-0 gap-2 ${editingModelIsEmbedding ? 'grid-cols-1 sm:w-1/3' : 'grid-cols-3'}`}>
                <div className="grid min-w-0 gap-1">
                  <Label htmlFor="price_in" className="text-xs text-muted-foreground">输入</Label>
                  <Input
                    id="price_in"
                    type="number"
                    step="0.1"
                    min="0"
                    value={editingModel?.price_in ?? ''}
                    onChange={(e) => {
                      const val = e.target.value === '' ? null : parseFloat(e.target.value)
                      setEditingModel((prev) => {
                        if (!prev) return null
                        // 缓存价格未被手动改过时（当前值仍等于修改前的输入价）跟随输入价格
                        const cacheFollowsInput = (prev.cache_price_in ?? 0) === (prev.price_in ?? 0)
                        return {
                          ...prev,
                          price_in: val,
                          cache_price_in: cacheFollowsInput ? val : prev.cache_price_in,
                        }
                      })
                    }}
                    placeholder="0"
                  />
                </div>
                {!editingModelIsEmbedding && (
                <>
                <div className="grid min-w-0 gap-1">
                  <Label htmlFor="price_out" className="text-xs text-muted-foreground">输出</Label>
                  <Input
                    id="price_out"
                    type="number"
                    step="0.1"
                    min="0"
                    value={editingModel?.price_out ?? ''}
                    onChange={(e) => {
                      const val = e.target.value === '' ? null : parseFloat(e.target.value)
                      setEditingModel((prev) =>
                        prev
                          ? { ...prev, price_out: val }
                          : null
                      )
                    }}
                    placeholder="0"
                  />
                </div>
                <div className="grid min-w-0 gap-1">
                  <Label htmlFor="cache_price_in" className="text-xs text-muted-foreground">缓存命中</Label>
                  <Input
                    id="cache_price_in"
                    type="number"
                    step="0.1"
                    min="0"
                    value={editingModel?.cache_price_in ?? ''}
                    onChange={(e) => {
                      const val = e.target.value === '' ? null : parseFloat(e.target.value)
                      setEditingModel((prev) =>
                        prev
                          ? { ...prev, cache_price_in: val }
                          : null
                      )
                    }}
                    placeholder="同输入价"
                  />
                </div>
                </>
                )}
              </div>
            </FormRow>

            <FormRow
              label="分时价格"
              alignTop
              labelExtra={(
                <HelpTooltip
                  content="每日按服务器本地时间计价，按成功请求尝试的开始时间计；包含开始、不包含结束，支持跨午夜。无时段或时段外使用默认价格。"
                  side="right"
                  maxWidth="320px"
                />
              )}
            >
              {(editingModel?.price_periods?.length ?? 0) === 0 && (
                <span className="flex min-h-9 items-center text-xs text-muted-foreground">全天按默认价格计</span>
              )}
              {editingModel?.price_periods?.map((period, index) => (
                <fieldset key={index} className={`min-w-0 ${index > 0 ? 'border-t border-dashed pt-2' : ''}`}>
                  <legend className="sr-only">时段 {index + 1}</legend>
                  <div className="flex items-end gap-2">
                    <div className="grid min-w-0 flex-1 gap-2">
                      <div className="grid min-w-0 grid-cols-2 gap-2">
                        {([
                          ['start_time', '开始时间'],
                          ['end_time', '结束时间'],
                        ] as const).map(([field, label]) => (
                          <div key={field} className="grid min-w-0 gap-1">
                            <Label htmlFor={`price-period-${index}-${field}`} className="text-xs text-muted-foreground">
                              {label}
                              {field === 'end_time' && period.end_time && period.end_time < period.start_time && (
                                <span className="ml-1">次日</span>
                              )}
                            </Label>
                            <Input
                              id={`price-period-${index}-${field}`}
                              aria-label={`时段 ${index + 1} ${label}`}
                              aria-describedby={formErrors.price_periods ? 'price-periods-error' : undefined}
                              type="time"
                              step="60"
                              required
                              className="h-8 min-w-0 px-2 text-xs"
                              value={period[field]}
                              onChange={(event) => {
                                const value = event.target.value
                                updatePricePeriods((periods) => periods.map((item, itemIndex) =>
                                  itemIndex === index ? { ...item, [field]: value } : item
                                ))
                              }}
                            />
                          </div>
                        ))}
                      </div>
                      <div className={`grid min-w-0 gap-2 ${editingModelIsEmbedding ? 'grid-cols-1' : 'grid-cols-3'}`}>
                        {([
                          ['price_in', '输入价格'],
                          ['price_out', '输出价格'],
                          ['cache_price_in', '缓存价格'],
                        ] as const).filter(([field]) => !editingModelIsEmbedding || field === 'price_in').map(([field, label]) => (
                          <div key={field} className="grid min-w-0 gap-1">
                            <Label htmlFor={`price-period-${index}-${field}`} className="text-xs text-muted-foreground">{label}</Label>
                            <Input
                              id={`price-period-${index}-${field}`}
                              aria-label={`时段 ${index + 1} ${label}`}
                              aria-describedby={formErrors.price_periods ? 'price-periods-error' : undefined}
                              type="number"
                              min="0"
                              step="any"
                              required
                              className="h-8 min-w-0 px-2 text-xs"
                              value={Number.isFinite(period[field]) ? period[field] : ''}
                              onChange={(event) => {
                                const value = event.target.valueAsNumber
                                updatePricePeriods((periods) => periods.map((item, itemIndex) =>
                                  itemIndex === index ? { ...item, [field]: value } : item
                                ))
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0 text-destructive hover:text-destructive"
                      title={`删除时段 ${index + 1}`}
                      aria-label={`删除时段 ${index + 1}`}
                      onClick={() => updatePricePeriods((periods) => periods.filter((_, itemIndex) => itemIndex !== index))}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </fieldset>
              ))}
              {formErrors.price_periods && (
                <p id="price-periods-error" role="alert" className="text-xs text-destructive">
                  {formErrors.price_periods}
                </p>
              )}
              <div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1"
                  onClick={() => updatePricePeriods((periods) => [...periods, {
                    start_time: '',
                    end_time: '',
                    price_in: editingModel?.price_in ?? 0,
                    price_out: editingModel?.price_out ?? 0,
                    cache_price_in: editingModel?.cache_price_in ?? 0,
                  }])}
                >
                  <Plus className="h-3.5 w-3.5" />
                  添加时段
                </Button>
              </div>
            </FormRow>
          </FormSection>

          <FormSection title="生成参数">
            {!editingModelIsEmbedding && (
            <>
            <FormRow
              label="温度"
              htmlFor="enable_model_temperature"
              alignTop
              labelExtra={(
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">什么是温度（Temperature）？</p>
                      <p>温度控制模型输出的随机性和创造性：</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li><strong>低温度（0.1-0.3）</strong>：更确定、更保守的输出，适合事实性任务</li>
                        <li><strong>中温度（0.5-0.7）</strong>：平衡创造性与可控性</li>
                        <li><strong>高温度（0.8-1.0）</strong>：更有创意、更多样化的输出</li>
                        <li><strong>极高温度（1.0-2.0）</strong>：极度随机，可能产生不可预测的结果</li>
                      </ul>
                      <p className="text-xs text-muted-foreground">
                        启用后覆盖「为模型分配功能」中该任务配置的温度。
                      </p>
                    </div>
                  }
                  side="right"
                  maxWidth="400px"
                />
              )}
            >
              <div className="flex min-h-9 items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {editingModel?.send_temperature === false
                    ? '已在高级设置中关闭 temperature 参数发送'
                    : editingModel?.temperature != null ? '自定义' : '使用任务配置'}
                </span>
                <Switch
                  id="enable_model_temperature"
                  aria-label="自定义模型温度"
                  checked={editingModel?.temperature != null}
                  disabled={editingModel?.send_temperature === false}
                  onCheckedChange={(checked) => {
                    setEditingModel((prev) => prev ? { ...prev, temperature: checked ? 0.7 : null } : null)
                  }}
                />
              </div>
              {editingModel?.temperature != null && editingModel.send_temperature !== false && (
                <>
                  <div className="flex items-center gap-3">
                    <Slider
                      value={[editingModel.temperature]}
                      onValueChange={(values) =>
                        setEditingModel((prev) =>
                          prev ? { ...prev, temperature: values[0] } : null
                        )
                      }
                      min={0}
                      max={2}
                      step={0.05}
                      className="flex-1"
                      aria-label="温度值"
                    />
                    <DraftNumberInput
                      aria-label="温度数值"
                      value={editingModel.temperature}
                      onValueChange={(temperature) => {
                        setEditingModel((prev) => prev ? { ...prev, temperature } : null)
                      }}
                      step={0.01}
                      range={[0, 2]}
                      className="h-8 w-20 text-right text-sm tabular-nums"
                    />
                  </div>
                  {editingModel.temperature > 1 && (
                    <p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      温度 &gt; 1 会产生更随机、更不可预测的输出，请谨慎使用
                    </p>
                  )}
                </>
              )}
            </FormRow>

            <FormRow
              label="最大 Token"
              htmlFor="enable_model_max_tokens"
              alignTop
              labelExtra={(
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">什么是最大 Token？</p>
                      <p>控制模型单次回复的最大长度。1 token ≈ 0.75 个英文单词或 0.5 个中文字符。</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li><strong>较小值（512-1024）</strong>：简短回复，节省成本</li>
                        <li><strong>中等值（2048-4096）</strong>：正常对话长度</li>
                        <li><strong>较大值（8192+）</strong>：长文本生成，成本较高</li>
                      </ul>
                      <p className="text-xs text-muted-foreground">
                        启用后覆盖「为模型分配功能」中该任务配置的最大 Token。
                      </p>
                    </div>
                  }
                  side="right"
                  maxWidth="400px"
                />
              )}
            >
              <div className="flex min-h-9 items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {editingModel?.max_tokens != null ? '自定义' : '使用任务配置'}
                </span>
                <Switch
                  id="enable_model_max_tokens"
                  aria-label="自定义最大 Token"
                  checked={editingModel?.max_tokens != null}
                  onCheckedChange={(checked) => {
                    // 启用时设置默认值 2048，禁用时清除
                    setEditingModel((prev) => prev ? { ...prev, max_tokens: checked ? 2048 : null } : null)
                  }}
                />
              </div>
              {editingModel?.max_tokens != null && (
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    aria-label="最大 Token 数"
                    min="1"
                    max="128000"
                    value={editingModel.max_tokens}
                    onChange={(e) => {
                      const val = parseInt(e.target.value)
                      if (!isNaN(val) && val >= 1) {
                        setEditingModel((prev) => prev ? { ...prev, max_tokens: val } : null)
                      }
                    }}
                    className="h-8 w-28 text-sm"
                  />
                  <FormUnit>token</FormUnit>
                </div>
              )}
            </FormRow>

            {advancedModelSettingsVisible && (
              <>
                <FormRow label="强制流式" htmlFor="force_stream_mode">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground">用于必须通过流式响应返回内容的模型</span>
                    <Switch
                      id="force_stream_mode"
                      aria-label="强制流式输出模式"
                      checked={editingModel?.force_stream_mode || false}
                      onCheckedChange={(checked) =>
                        setEditingModel((prev) =>
                          prev ? { ...prev, force_stream_mode: checked } : null
                        )
                      }
                    />
                  </div>
                </FormRow>
                <FormRow label="发送温度" htmlFor="send_temperature">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground">关闭后不发送模型温度和任务温度</span>
                    <Switch
                      id="send_temperature"
                      aria-label="发送 temperature 参数"
                      checked={editingModel?.send_temperature ?? true}
                      onCheckedChange={(checked) =>
                        setEditingModel((prev) =>
                          prev ? { ...prev, send_temperature: checked } : null
                        )
                      }
                    />
                  </div>
                </FormRow>
              </>
            )}
            </>
            )}

            <FormRow label="额外参数">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-9 justify-start"
                onClick={() => setExtraParamsDialogOpen(true)}
              >
                <Settings className="mr-2 h-4 w-4" />
                {Object.keys(editingModel?.extra_params || {}).length > 0 ? (
                  <span>
                    已配置 {Object.keys(editingModel?.extra_params || {}).length} 个参数
                  </span>
                ) : (
                  <span className="text-muted-foreground">未配置额外参数</span>
                )}
              </Button>
              {Object.keys(editingModel?.extra_params || {}).length > 0 && (
                <div className="text-xs text-muted-foreground">
                  {Object.keys(editingModel?.extra_params || {})
                    .slice(0, 3)
                    .map((key) => (
                      <span key={key} className="mr-2 inline-block">
                        <code className="rounded bg-muted px-1.5 py-0.5">{key}</code>
                      </span>
                    ))}
                  {Object.keys(editingModel?.extra_params || {}).length > 3 && (
                    <span>...</span>
                  )}
                </div>
              )}
            </FormRow>
          </FormSection>
          </FormColumn>
          </FormColumns>
          </DialogBody>

          <DialogFooter className="flex-row justify-end gap-2 space-x-0">
            <Button
              variant="outline"
              className="flex-1 sm:flex-none"
              onClick={() => setEditDialogOpen(false)}
              data-tour="model-cancel-button"
            >
              取消
            </Button>
            <Button
              data-dialog-action="confirm"
              className="flex-1 sm:flex-none"
              onClick={handleSaveEdit}
              disabled={saving || thinkingProbeRunning || Boolean(deepSeekExtraParamsError) || Boolean(thinkingExtraParamsError)}
              data-tour="model-save-button"
            >
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认对话框 */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除模型 "{deletingIndex !== null ? models[deletingIndex]?.name : ''}" 吗？
              此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmDelete}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 批量删除确认对话框 */}
      <AlertDialog open={batchDeleteDialogOpen} onOpenChange={setBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除选中的 {selectedModels.size} 个模型吗？
              此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmBatchDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              批量删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 嵌入模型更换警告对话框 */}
      <AlertDialog open={embeddingWarning.isOpen} onOpenChange={embeddingWarning.setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="sr-only">更换嵌入模型警告</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>
                  <strong className="text-foreground">注意：</strong>更换嵌入模型后，表达库会在后台自动补建；记忆向量若提示需要重建，请在长期记忆页处理。
                </p>
                <p className="text-foreground font-medium">
                  确定要更换嵌入模型吗？
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={embeddingWarning.cancel}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void embeddingWarning.confirm()}
              className="bg-amber-600 hover:bg-amber-700"
            >
              确认更换
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 额外参数编辑弹窗 */}
      <ExtraParamsDialog
        open={extraParamsDialogOpen}
        onOpenChange={setExtraParamsDialogOpen}
        value={editingModel?.extra_params || {}}
        validate={
          deepSeekClientType
            ? (params) => validateDeepSeekExtraParams(params, deepSeekClientType)
            : thinkingFormatActive
              ? (params) => validateThinkingParams(params, thinkingFormatActive)
              : undefined
        }
        onChange={(params) =>
          setEditingModel((prev) =>
            prev ? { ...prev, extra_params: params } : null
          )
        }
      />

      <AlertDialog open={deletingVersionId !== null} onOpenChange={(open) => !open && setDeletingVersionId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除Bot 任务配置副本</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除副本「{deletingVersion?.label || deletingVersionId}」吗？此操作不会影响当前启用配置，但无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deletingVersionId) {
                  void handleDeleteConfigVersion(deletingVersionId)
                }
                setDeletingVersionId(null)
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 重启遮罩层 */}
      {webSearchEditor && (
        <WebSearchDialog
          config={mc.webConfig}
          providers={apiProviders}
          models={models}
          editingName={webSearchEditor.name}
          assignedService={taskConfig?.web_search?.model_list[0] ?? ''}
          onClose={() => setWebSearchEditor(null)}
          onSave={mc.saveWebConfig}
        />
      )}
      {/* 手机页头右上角的「+」：添加模型、厂商与联网服务 */}
      <MobileHeaderActions>
        <MobileAddMenu
          label="添加"
          items={[
            { label: '添加 LLM 模型', onSelect: () => openAddModelDialog('llm'), disabled: apiProviders.length === 0 },
            { label: '添加嵌入模型', onSelect: () => openAddModelDialog('embedding'), disabled: apiProviders.length === 0 },
            { label: '添加 LLM 模型提供商', onSelect: () => openProviderDialog(null, null) },
            { label: '添加联网搜索', onSelect: () => setWebSearchEditor({ name: null }) },
          ]}
        />
      </MobileHeaderActions>
      <RestartOverlay />
      </div>
    </div>
  )
}
