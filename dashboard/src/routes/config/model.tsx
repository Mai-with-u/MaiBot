import { type CSSProperties, type MouseEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { Badge } from '@/components/ui/badge'
import {
  AlertTriangle,
  Check,
  ChevronsUpDown,
  Copy,
  GraduationCap,
  History,
  Info,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings,
  Trash2,
  Zap,
} from 'lucide-react'
import { resolveFieldLabel } from '@/lib/config-label'
import { cn } from '@/lib/utils'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { ExtraParamsDialog } from '@/components/ui/extra-params-dialog'
import { TaskConfigCard, Pagination, ModelTable, ModelCardList } from './model/components'
import { useModelTour, useModelFetcher, useModelConfig } from './model/hooks'
import { ProviderForm } from './modelProvider/ProviderForm'
import { ProviderList } from './modelProvider/ProviderList'
import type { APIProvider } from './modelProvider/types'

// 导入模块化的类型定义和组件
import type { ModelInfo } from './model/types'

const MODEL_CONFIG_TABS = ['providers', 'models', 'tasks'] as const
type ModelConfigTab = (typeof MODEL_CONFIG_TABS)[number]

interface ModelIdentifierMarqueeProps {
  text: string
  className?: string
  textClassName?: string
}

function ModelIdentifierMarquee({ text, className, textClassName }: ModelIdentifierMarqueeProps) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [scrollDistance, setScrollDistance] = useState(0)

  useEffect(() => {
    const containerElement = containerRef.current
    const textElement = textRef.current
    if (!containerElement || !textElement) {
      return
    }

    const updateOverflowState = () => {
      setScrollDistance(Math.max(0, textElement.scrollWidth - containerElement.clientWidth))
    }

    updateOverflowState()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateOverflowState)
      return () => window.removeEventListener('resize', updateOverflowState)
    }

    const resizeObserver = new ResizeObserver(updateOverflowState)
    resizeObserver.observe(containerElement)
    resizeObserver.observe(textElement)
    return () => resizeObserver.disconnect()
  }, [text])

  const shouldScroll = scrollDistance > 1
  const durationMs = Math.min(Math.max(scrollDistance * 45, 1800), 6000)

  return (
    <span
      ref={containerRef}
      className={cn('group/model-marquee block min-w-0 overflow-hidden', className)}
      title={text}
    >
      <span
        ref={textRef}
        className={cn(
          'model-identifier-marquee-text block max-w-full overflow-hidden text-ellipsis whitespace-nowrap',
          shouldScroll &&
            'group-hover/model-marquee:w-max group-hover/model-marquee:max-w-none group-hover/model-marquee:animate-[model-identifier-marquee_var(--model-identifier-marquee-duration)_ease-in-out_infinite_alternate] group-hover/model-marquee:overflow-visible group-hover/model-option:w-max group-hover/model-option:max-w-none group-hover/model-option:animate-[model-identifier-marquee_var(--model-identifier-marquee-duration)_ease-in-out_infinite_alternate] group-hover/model-option:overflow-visible',
          textClassName
        )}
        style={
          shouldScroll
            ? ({
                '--model-identifier-marquee-distance': `-${scrollDistance}px`,
                '--model-identifier-marquee-duration': `${durationMs}ms`,
              } as CSSProperties)
            : undefined
        }
      >
        {text}
      </span>
    </span>
  )
}

function getInitialModelConfigTab(): ModelConfigTab {
  if (typeof window === 'undefined') {
    return 'providers'
  }

  const tab = new URLSearchParams(window.location.search).get('tab')
  return MODEL_CONFIG_TABS.includes(tab as ModelConfigTab) ? (tab as ModelConfigTab) : 'providers'
}

// 主导出组件：包装 RestartProvider
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
  const { isRestarting } = useRestart()

  // 核心领域 hook：models / apiProviders / model_task_config 三份草稿及其全部编排
  const mc = useModelConfig()
  const {
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
    getProviderConfig,
    // embedding 警告
    embeddingWarning,
  } = mc

  // 纯 UI 态（不属于配置草稿，留在渲染层）
  const [activeTab, setActiveTab] = useState<ModelConfigTab>(getInitialModelConfigTab)
  const [advancedModelSettingsVisible, setAdvancedModelSettingsVisible] = useState(false)
  const [advancedTaskSettingsVisible, setAdvancedTaskSettingsVisible] = useState(false)
  const [extraParamsDialogOpen, setExtraParamsDialogOpen] = useState(false)
  const [modelComboboxOpen, setModelComboboxOpen] = useState(false)
  const [createVersionDialogOpen, setCreateVersionDialogOpen] = useState(false)
  const [manageVersionsDialogOpen, setManageVersionsDialogOpen] = useState(false)
  const [newVersionLabel, setNewVersionLabel] = useState('')
  const [deletingVersionId, setDeletingVersionId] = useState<string | null>(null)
  const [tourEntryVisible, setTourEntryVisible] = useState(
    () => localStorage.getItem('model-assignment-tour-entry-dismissed') !== 'true'
  )

  // 模型列表获取 (使用 hook 封装的逻辑)
  const {
    availableModels,
    fetchingModels,
    modelFetchError,
    matchedTemplate,
    fetchModelsForProvider,
    clearModels,
  } = useModelFetcher({ getProviderConfig })

  // 打开模型编辑对话框：重置高级设置可见性后委托核心 hook
  const openEditDialog = (model: ModelInfo | null, index: number | null) => {
    mc.openEditDialog(model, index, () => setAdvancedModelSettingsVisible(false))
  }

  const openProviderDialog = (provider: APIProvider | null, index: number | null) => {
    openProviderDialogBase(provider, index)
  }

  // 当选择的提供商变化时，获取模型列表
  useEffect(() => {
    if (editDialogOpen && editingModel?.api_provider) {
      fetchModelsForProvider(editingModel.api_provider)
    }
  }, [editDialogOpen, editingModel?.api_provider, fetchModelsForProvider])

  const dismissTourEntry = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    localStorage.setItem('model-assignment-tour-entry-dismissed', 'true')
    setTourEntryVisible(false)
  }

  const handleActiveTabChange = (value: string) => {
    const nextTab = MODEL_CONFIG_TABS.includes(value as ModelConfigTab)
      ? (value as ModelConfigTab)
      : 'providers'
    setActiveTab(nextTab)
    const nextUrl = nextTab === 'providers' ? '/config/model' : `/config/model?tab=${nextTab}`
    window.history.replaceState(null, '', nextUrl)
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
  const { startTour: handleStartTour, isRunning: tourIsRunning } = useModelTour({
    onOpenEditDialog: () => openEditDialog(null, null),
    onCloseEditDialog: () => setEditDialogOpen(false),
    onOpenProviderDialog: () => openProviderDialog(null, null),
    onCloseProviderDialog: () => setProviderDialogOpen(false),
    onOpenProvidersTab: () => handleActiveTabChange('providers'),
    onOpenModelsTab: () => handleActiveTabChange('models'),
    onOpenTasksTab: () => handleActiveTabChange('tasks'),
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
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        {/* 无效模型引用警告 */}
        {invalidModelRefs.length > 0 && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <strong>检测到无效的模型引用</strong>
                <div className="mt-2 space-y-1">
                  {invalidModelRefs.map(({ taskName, invalidModels }) => (
                    <div key={taskName} className="text-sm">
                      <strong>{taskName}</strong> 引用了不存在的模型: {invalidModels.join(', ')}
                    </div>
                  ))}
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 bg-background hover:bg-accent"
                onClick={handleRemoveInvalidRefs}
              >
                一键清理
              </Button>
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


        {/* 新手引导入口 - 仅在桌面端显示，移动端隐藏 */}
        {tourEntryVisible && (
        <Alert className="hidden lg:flex border-primary/30 bg-primary/5 cursor-pointer hover:bg-primary/10 transition-colors" onClick={handleStartTour}>
          <GraduationCap className="h-4 w-4 text-primary" />
          <AlertDescription className="flex items-center justify-between">
            <span>
              <strong className="text-primary">新手引导：</strong>不知道如何配置模型？点击这里开始学习如何为麦麦的组件分配模型。
            </span>
            <div className="ml-4 flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm">
              开始引导
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={dismissTourEntry}>
              关闭
            </Button>
            </div>
          </AlertDescription>
        </Alert>
        )}

        {/* 标签页 */}
        <Tabs value={activeTab} onValueChange={handleActiveTabChange} className="w-full">
          <div
            data-model-config-tabs-bar="true"
            className="sticky top-0 z-40 -mx-4 flex w-[calc(100%+2rem)] flex-wrap items-stretch gap-2 border-b bg-background px-4 py-2 sm:-mx-6 sm:w-[calc(100%+3rem)] sm:px-6"
          >
            <TabsList className="grid h-9 min-w-[min(100%,22rem)] flex-1 grid-cols-3">
              <TabsTrigger value="providers" className="w-full" data-tour="providers-tab-trigger">模型厂商设置</TabsTrigger>
              <TabsTrigger value="models" className="w-full" data-tour="models-tab-trigger">模型列表</TabsTrigger>
              <TabsTrigger value="tasks" className="w-full" data-tour="tasks-tab-trigger">为模型分配功能</TabsTrigger>
            </TabsList>
            <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
              <Select
                value="active"
                onValueChange={(value) => {
                  if (value !== 'active') {
                    void handleSwitchConfigVersion(value)
                  }
                }}
                disabled={versionsLoading || saving || autoSaving || Boolean(switchingConfigVersion)}
              >
                <SelectTrigger className="h-9 min-w-0 flex-1 sm:w-[190px] sm:flex-none" aria-label="模型配置副本">
                  <History className="mr-2 h-4 w-4 shrink-0 text-muted-foreground" />
                  <SelectValue placeholder={activeConfigVersion?.label || '当前启用'} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">
                    {activeConfigVersion?.label || '默认配置'} · 当前启用
                  </SelectItem>
                  {configVersions.map((version) => (
                    <SelectItem key={version.id} value={version.id} disabled={!version.valid}>
                      {version.label} · {formatVersionTime(version.modified_at)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                title="保存当前配置副本"
                aria-label="保存当前配置副本"
                disabled={saving || autoSaving || creatingConfigVersion}
                onClick={() => setCreateVersionDialogOpen(true)}
              >
                {creatingConfigVersion ? <Loader2 className="h-4 w-4 animate-spin" /> : <Copy className="h-4 w-4" />}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                title="管理配置副本"
                aria-label="管理配置副本"
                onClick={() => setManageVersionsDialogOpen(true)}
              >
                <History className="h-4 w-4" />
              </Button>
            </div>
          </div>
          {/* 模型厂商设置标签页 */}
          <TabsContent value="providers" className="space-y-4 mt-0">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="hidden">
                {selectedProviders.size > 0 && (
                  <Button
                    onClick={openProviderBatchDeleteDialog}
                    size="sm"
                    variant="destructive"
                    className="w-full sm:w-auto"
                  >
                    <Trash2 className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                    批量删除 ({selectedProviders.size})
                  </Button>
                )}
                <Button
                  onClick={handleTestAllProviderConnections}
                  size="sm"
                  variant="outline"
                  className="w-full sm:w-auto"
                  disabled={apiProviders.length === 0 || testingProviders.size > 0}
                >
                  <Zap className="mr-2 h-4 w-4" />
                  {testingProviders.size > 0 ? `测试中 (${testingProviders.size})` : '测试全部'}
                </Button>
                <Button onClick={() => openProviderDialog(null, null)} size="sm" variant="outline" className="w-full sm:w-auto">
                  <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                  添加提供商
                </Button>
              </div>
            </div>

            <ProviderList
              providers={apiProviders}
              testingProviders={testingProviders}
              testResults={testResults}
              selectedProviders={selectedProviders}
              toolbarActions={(
                <>
                  {selectedProviders.size > 0 && (
                    <Button
                      onClick={openProviderBatchDeleteDialog}
                      size="sm"
                      variant="destructive"
                      className="w-full sm:w-auto"
                    >
                      <Trash2 className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                      <span className="text-sm">批量删除 ({selectedProviders.size})</span>
                    </Button>
                  )}
                  <Button
                    onClick={handleTestAllProviderConnections}
                    size="sm"
                    variant="outline"
                    className="w-full sm:w-auto"
                    disabled={apiProviders.length === 0 || testingProviders.size > 0}
                  >
                    <Zap className="mr-2 h-4 w-4" />
                    <span className="text-sm">
                      {testingProviders.size > 0 ? `测试中 (${testingProviders.size})` : '测试全部连接'}
                    </span>
                  </Button>
                  <Button onClick={() => openProviderDialog(null, null)} size="sm" variant="outline" className="w-full sm:w-auto" data-tour="add-provider-button">
                    <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                    <span className="text-sm">添加厂商</span>
                  </Button>
                </>
              )}
              onEdit={openProviderDialog}
              onDelete={openProviderDeleteDialog}
              onTest={handleTestProviderConnection}
              onToggleSelect={toggleProviderSelection}
              onToggleSelectAll={toggleSelectAllProviders}
            />
          </TabsContent>
          {/* 模型配置标签页 */}
          <TabsContent value="models" className="space-y-4 mt-0">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
              <div className="hidden">
                {selectedModels.size > 0 && (
                  <Button 
                    onClick={openBatchDeleteDialog} 
                    size="sm" 
                    variant="destructive" 
                    className="w-full sm:w-auto"
                  >
                    <Trash2 className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                    批量删除 ({selectedModels.size})
                  </Button>
                )}
                <Button onClick={() => openEditDialog(null, null)} size="sm" variant="outline" className="w-full sm:w-auto">
                  <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                  添加模型
                </Button>
              </div>
            </div>

          {/* 搜索框 */}
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex w-full min-w-0 flex-col gap-2 sm:flex-1 sm:flex-row sm:items-center">
              <div className="relative w-full sm:max-w-sm">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索模型名称、标识符或提供商..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>
              {searchQuery && (
                <p className="text-sm text-muted-foreground whitespace-nowrap">
                  找到 {filteredModels.length} 个结果
                </p>
              )}
            </div>

          {/* 模型列表 - 移动端卡片视图 */}
            <div className="flex w-full flex-col gap-2 sm:ml-auto sm:w-auto sm:flex-row sm:items-center sm:justify-end">
              <Button 
                onClick={saveConfig} 
                disabled={saving || autoSaving || !hasUnsavedChanges || isRestarting} 
                size="sm"
                variant="outline"
                className="w-full sm:w-auto sm:min-w-[120px]"
              >
                <Save className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                {saving ? '保存中...' : autoSaving ? '自动保存中...' : hasUnsavedChanges ? '保存配置' : '已保存'}
              </Button>
              {selectedModels.size > 0 && (
                <Button
                  onClick={openBatchDeleteDialog}
                  size="sm"
                  variant="destructive"
                  className="w-full sm:w-auto"
                >
                  <Trash2 className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                  <span className="text-sm">批量删除 ({selectedModels.size})</span>
                </Button>
              )}
              <Button onClick={() => openEditDialog(null, null)} size="sm" variant="outline" className="w-full sm:w-auto" data-tour="add-model-button">
                <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                <span className="text-sm">添加模型</span>
              </Button>
            </div>
          </div>

          <ModelCardList
            paginatedModels={paginatedModels}
            allModels={models}
            onEdit={openEditDialog}
            onDelete={openDeleteDialog}
            onTest={handleTestModelCapability}
            isModelUsed={isModelUsed}
            testingModels={testingModels}
            modelTestResults={modelTestResults}
            searchQuery={searchQuery}
          />

          {/* 模型列表 - 桌面端表格视图 */}
          <ModelTable
            paginatedModels={paginatedModels}
            allModels={models}
            filteredModels={filteredModels}
            selectedModels={selectedModels}
            onEdit={openEditDialog}
            onDelete={openDeleteDialog}
            onTest={handleTestModelCapability}
            onToggleSelection={toggleModelSelection}
            onToggleSelectAll={toggleSelectAll}
            isModelUsed={isModelUsed}
            testingModels={testingModels}
            modelTestResults={modelTestResults}
            searchQuery={searchQuery}
          />

          {/* 分页 - 使用模块化组件 */}
          <Pagination
            page={page}
            pageSize={pageSize}
            totalItems={filteredModels.length}
            jumpToPage={jumpToPage}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
            onJumpToPageChange={setJumpToPage}
            onJumpToPage={handleJumpToPage}
            onSelectionClear={() => setSelectedModels(new Set())}
          />
        </TabsContent>

        {/* 模型任务配置标签页 */}
        <TabsContent value="tasks" className="mt-0 space-y-3">
          <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              为不同的任务配置使用的模型和参数
            </p>
            {taskConfigSchema?.fields.some((field) => field.advanced) && (
              <Button
                type="button"
                variant={advancedTaskSettingsVisible ? 'default' : 'outline'}
                size="sm"
                onClick={() => setAdvancedTaskSettingsVisible((current) => !current)}
              >
                高级设置
              </Button>
            )}
          </div>

          {taskConfig && taskConfigSchema && (
            <div className="divide-y-2">
              {taskConfigSchema.fields
                .filter(f => f.type === 'object' && (advancedTaskSettingsVisible || !f.advanced))
                .map((field, index) => {
                  return (
                    <TaskConfigCard
                      key={field.name}
                      title={resolveFieldLabel(field, i18n.language)}
                      description={field.description}
                      taskConfig={taskConfig[field.name] ?? { model_list: [] }}
                      modelNames={modelNames}
                      onChange={(f, value) => updateTaskConfig(field.name, f, value)}
                      advanced={field.advanced}
                      showAdvancedSettings={advancedTaskSettingsVisible}
                      {...(index === 0 ? { dataTour: 'task-model-select' } : {})}
                    />
                  )
                })}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={createVersionDialogOpen} onOpenChange={setCreateVersionDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>保存模型配置副本</DialogTitle>
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
              placeholder="例如：生产环境模型配置副本"
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
            <DialogTitle>模型配置副本</DialogTitle>
            <DialogDescription>
              未启用副本保存在 config/versions/model，切换副本时当前配置会先自动归档。
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
        <DialogContent className="max-w-[95vw] gap-3 p-4 sm:max-w-3xl sm:gap-4 sm:p-6">
          <DialogHeader>
            <DialogTitle>模型测试详情</DialogTitle>
            <DialogDescription>
              {selectedModelTestResult?.model_name || '模型'} 的最近一次能力测试结果
            </DialogDescription>
          </DialogHeader>
          {selectedModelTestResult && (
            <DialogBody viewportClassName="max-h-[70vh] pr-3 sm:pr-4">
              <div className="space-y-4 py-2 text-sm">
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
                  <div>
                    <span className="text-muted-foreground">工具调用</span>
                    <p className={selectedModelTestResult.tool_call_ok ? 'font-medium text-green-600' : 'font-medium text-destructive'}>
                      {selectedModelTestResult.tool_call_ok ? '已返回测试工具调用' : '未返回测试工具调用'}
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
                </div>

                {selectedModelTestResult.error && (
                  <div>
                    <h4 className="mb-2 text-sm font-semibold">错误信息</h4>
                    <pre className="bg-muted max-h-40 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                      {selectedModelTestResult.error}
                    </pre>
                  </div>
                )}

                <div>
                  <h4 className="mb-2 text-sm font-semibold">工具调用返回</h4>
                  <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                    {JSON.stringify(selectedModelTestResult.tool_calls, null, 2)}
                  </pre>
                </div>

                <div>
                  <h4 className="mb-2 text-sm font-semibold">模型文本返回</h4>
                  <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                    {selectedModelTestResult.response || '（无文本返回）'}
                  </pre>
                </div>

                {selectedModelTestResult.reasoning && (
                  <div>
                    <h4 className="mb-2 text-sm font-semibold">推理内容</h4>
                    <pre className="bg-muted max-h-56 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                      {selectedModelTestResult.reasoning}
                    </pre>
                  </div>
                )}
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

      {/* 批量删除提供商确认对话框 */}
      <AlertDialog open={providerBatchDeleteDialogOpen} onOpenChange={setProviderBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除提供商</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除选中的 {selectedProviders.size} 个提供商吗？
              如果这些提供商下存在模型，确认时会提示一并处理关联模型。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmProviderBatchDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              批量删除
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
                      <div key={(model as ModelInfo).name}>
                        {(model as ModelInfo).name} ({(model as ModelInfo).api_provider})
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
      <Dialog open={editDialogOpen} onOpenChange={handleEditDialogClose}>
        <DialogContent 
          className="max-w-[95vw] gap-3 p-4 sm:max-w-2xl sm:gap-4 sm:p-6"
          data-tour="model-dialog"
          preventOutsideClose={tourIsRunning}
          confirmOnEnter
        >
          <DialogHeader>
            <DialogTitle>
              {editingIndex !== null ? '编辑模型' : '添加模型'}
            </DialogTitle>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <DialogDescription>配置模型的基本信息和参数</DialogDescription>
              <Button
                type="button"
                variant={advancedModelSettingsVisible ? 'default' : 'outline'}
                size="sm"
                onClick={() => setAdvancedModelSettingsVisible((current) => !current)}
                className="self-start sm:self-auto"
              >
                高级设置
              </Button>
            </div>
          </DialogHeader>

          <DialogBody viewportClassName="min-h-0 flex-1 pr-3 sm:pr-4 [&>div]:!block">
          <div className="grid gap-3 py-2 sm:gap-4 sm:py-4">
            <div className="grid gap-2" data-tour="model-name-input">
              <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                <Label
                  htmlFor="model_name"
                  className={`sm:w-28 sm:flex-shrink-0 ${formErrors.name ? 'text-destructive' : ''}`}
                >
                  模型名称 *
                </Label>
                <Input
                  id="model_name"
                  value={editingModel?.name || ''}
                  onChange={(e) => {
                    setEditingModel((prev) =>
                      prev ? { ...prev, name: e.target.value } : null
                    )
                    if (formErrors.name) {
                      setFormErrors((prev) => ({ ...prev, name: undefined }))
                    }
                  }}
                  placeholder="例如: qwen3-30b"
                  className={`sm:flex-1 ${formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                />
              </div>
              {formErrors.name ? (
                <p className="text-xs text-destructive sm:pl-28">{formErrors.name}</p>
              ) : null}
            </div>

            <div className="grid gap-2" data-tour="model-provider-select">
              <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                <Label
                  htmlFor="api_provider"
                  className={`sm:w-28 sm:flex-shrink-0 ${formErrors.api_provider ? 'text-destructive' : ''}`}
                >
                  API 提供商 *
                </Label>
                <Select
                  value={editingModel?.api_provider || ''}
                  onValueChange={(value) => {
                    setEditingModel((prev) =>
                      prev ? { ...prev, api_provider: value } : null
                    )
                    // 清空模型列表和错误状态，等待 useEffect 重新获取
                    clearModels()
                    if (formErrors.api_provider) {
                      setFormErrors((prev) => ({ ...prev, api_provider: undefined }))
                    }
                  }}
                >
                  <SelectTrigger id="api_provider" className={`sm:flex-1 ${formErrors.api_provider ? 'border-destructive focus-visible:ring-destructive' : ''}`}>
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
              </div>
              {formErrors.api_provider && (
                <p className="text-xs text-destructive sm:pl-28">{formErrors.api_provider}</p>
              )}
            </div>

            <div className="grid gap-2" data-tour="model-identifier-input">
              <div className="flex items-center justify-between">
                <Label htmlFor="model_identifier" className={formErrors.model_identifier ? 'text-destructive' : ''}>模型标识符 *</Label>
                {matchedTemplate?.modelFetcher && (
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="text-xs">
                      {matchedTemplate.display_name}
                    </Badge>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2"
                      onClick={() => editingModel?.api_provider && fetchModelsForProvider(editingModel.api_provider, true)}
                      disabled={fetchingModels}
                    >
                      {fetchingModels ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3 w-3" />
                      )}
                    </Button>
                  </div>
                )}
              </div>
              
              <div className="flex flex-col gap-1.5 sm:flex-row sm:gap-2">
                {/* 模型标识符 Combobox */}
                {matchedTemplate?.modelFetcher && (
                  <Popover open={modelComboboxOpen} onOpenChange={setModelComboboxOpen}>
                    <PopoverTrigger asChild>
                      <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={modelComboboxOpen}
                        className="w-full justify-between font-normal sm:w-[46%]"
                        disabled={fetchingModels || !!modelFetchError}
                      >
                        {fetchingModels ? (
                          <span className="flex items-center gap-2 text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            正在获取模型列表...
                          </span>
                        ) : modelFetchError ? (
                          <span className="text-muted-foreground text-sm">手动填写</span>
                        ) : editingModel?.model_identifier ? (
                          <ModelIdentifierMarquee
                            text={editingModel.model_identifier}
                            className="min-w-0 flex-1 text-left"
                          />
                        ) : (
                          <span className="text-muted-foreground">搜索或选择模型...</span>
                        )}
                        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="z-[60] p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
                      <Command>
                        <CommandInput placeholder="搜索模型..." />
                        <CommandList className="max-h-[300px]">
                          <CommandEmpty>
                            {modelFetchError ? (
                              <div className="py-4 px-2 text-center space-y-2">
                                <p className="text-sm text-destructive">{modelFetchError}</p>
                                {!modelFetchError.includes('API Key') && (
                                  <Button
                                    variant="link"
                                    size="sm"
                                    onClick={() => editingModel?.api_provider && fetchModelsForProvider(editingModel.api_provider, true)}
                                  >
                                    重试
                                  </Button>
                                )}
                              </div>
                            ) : (
                              '未找到匹配的模型'
                            )}
                          </CommandEmpty>
                          <CommandGroup heading="可用模型">
                            {availableModels.map((model) => (
                              <CommandItem
                                key={model.id}
                                value={model.id}
                                className="group/model-option pr-8"
                                onSelect={() => {
                                  setEditingModel((prev) =>
                                    prev ? { ...prev, model_identifier: model.id } : null
                                  )
                                  setModelComboboxOpen(false)
                                }}
                              >
                                {editingModel?.model_identifier === model.id && (
                                  <Check className="absolute right-2 h-4 w-4" />
                                )}
                                <div className="flex min-w-0 flex-1 flex-col">
                                  <ModelIdentifierMarquee text={model.id} />
                                  {model.name !== model.id && (
                                    <ModelIdentifierMarquee
                                      text={model.name}
                                      textClassName="text-xs text-muted-foreground"
                                    />
                                  )}
                                </div>
                              </CommandItem>
                            ))}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                )}

                <Input
                  id="model_identifier"
                  value={editingModel?.model_identifier || ''}
                  onChange={(e) => {
                    setEditingModel((prev) =>
                      prev ? { ...prev, model_identifier: e.target.value } : null
                    )
                    if (formErrors.model_identifier) {
                      setFormErrors((prev) => ({ ...prev, model_identifier: undefined }))
                    }
                  }}
                  placeholder={matchedTemplate?.modelFetcher ? '手动输入模型标识符' : 'Qwen/Qwen3-30B-A3B-Instruct-2507'}
                  className={`${matchedTemplate?.modelFetcher ? 'sm:flex-1' : 'w-full'} ${formErrors.model_identifier ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                />
              </div>
              
              {/* 表单验证错误提示 */}
              {formErrors.model_identifier && (
                <p className="text-xs text-destructive">{formErrors.model_identifier}</p>
              )}
              
              {/* 模型获取错误提示 */}
              {modelFetchError && matchedTemplate?.modelFetcher && !formErrors.model_identifier && (
                <Alert variant="destructive" className="mt-2 py-2">
                  <Info className="h-4 w-4" />
                  <AlertDescription className="text-xs">
                    {modelFetchError}
                  </AlertDescription>
                </Alert>
              )}
              
              {!formErrors.model_identifier && (
                <p className="text-xs text-muted-foreground">
                  {modelFetchError 
                    ? '请手动输入模型标识符，或前往"模型厂商设置"检查 API Key'
                    : matchedTemplate?.modelFetcher 
                      ? `已识别为 ${matchedTemplate.display_name}，支持自动获取模型列表` 
                      : 'API 提供商提供的模型 ID'}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:gap-6">
              <div className="flex items-center space-x-2">
                <Switch
                  id="model_visual"
                  checked={editingModel?.visual || false}
                  onCheckedChange={(checked) =>
                    setEditingModel((prev) =>
                      prev ? { ...prev, visual: checked } : null
                    )
                  }
                />
                <Label htmlFor="model_visual" className="cursor-pointer">
                  启用视觉
                </Label>
              </div>

              <div className="flex items-center space-x-2">
                <Switch
                  id="model_cache"
                  checked={editingModel?.cache || false}
                  onCheckedChange={(checked) =>
                    setEditingModel((prev) =>
                      prev ? { ...prev, cache: checked } : null
                    )
                  }
                />
                <Label htmlFor="model_cache" className="cursor-pointer">
                  支持缓存
                </Label>
              </div>
            </div>

            <div className={`grid grid-cols-1 gap-3 sm:gap-4 ${editingModel?.cache ? 'md:grid-cols-3' : 'sm:grid-cols-2'}`}>
              <div className="grid gap-2">
                <Label htmlFor="price_in">输入价格 (¥/M token)</Label>
                <Input
                  id="price_in"
                  type="number"
                  step="0.1"
                  min="0"
                  value={editingModel?.price_in ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseFloat(e.target.value)
                    setEditingModel((prev) =>
                      prev
                        ? { ...prev, price_in: val }
                        : null
                    )
                  }}
                  placeholder="默认: 0"
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="price_out">输出价格 (¥/M token)</Label>
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
                  placeholder="默认: 0"
                />
              </div>

              {editingModel?.cache && (
                <div className="grid gap-2">
                  <Label htmlFor="cache_price_in">缓存价格 (¥/M token)</Label>
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
                    placeholder="默认: 0"
                  />
                </div>
              )}
            </div>

            {advancedModelSettingsVisible && (
              <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-500/40 dark:bg-amber-500/10 sm:space-y-4 sm:p-4">
                <div className="flex items-center justify-between gap-4">
                  <div className="space-y-1">
                    <Label htmlFor="force_stream_mode" className="cursor-pointer">强制流式输出模式</Label>
                    <p className="text-xs text-muted-foreground">
                      用于必须通过流式响应返回内容的模型
                    </p>
                  </div>
                  <Switch
                    id="force_stream_mode"
                    checked={editingModel?.force_stream_mode || false}
                    onCheckedChange={(checked) =>
                      setEditingModel((prev) =>
                        prev ? { ...prev, force_stream_mode: checked } : null
                      )
                    }
                  />
                </div>
              </div>
            )}

            {/* 模型级别温度 */}
            <div className="space-y-2 rounded-lg border p-3 sm:space-y-3 sm:p-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="enable_model_temperature" className="cursor-pointer">自定义模型温度</Label>
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
                        </div>
                      }
                      side="right"
                      maxWidth="400px"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    启用后将覆盖「为模型分配功能」中的任务温度配置
                  </p>
                </div>
                <Switch
                  id="enable_model_temperature"
                  checked={editingModel?.temperature != null}
                  onCheckedChange={(checked) => {
                    if (checked) {
                      setEditingModel((prev) => prev ? { ...prev, temperature: 0.7 } : null)
                    } else {
                      setEditingModel((prev) => prev ? { ...prev, temperature: null } : null)
                    }
                  }}
                />
              </div>
              
              {editingModel?.temperature != null && (
                <div className="space-y-3 pt-2 border-t">
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-sm">温度值</Label>
                    <Input
                      type="number"
                      value={editingModel.temperature}
                      onChange={(e) => {
                        const value = parseFloat(e.target.value)
                        if (!isNaN(value) && value >= 0 && value <= 2) {
                          setEditingModel((prev) => prev ? { ...prev, temperature: value } : null)
                        }
                      }}
                      onBlur={(e) => {
                        const value = parseFloat(e.target.value)
                        if (isNaN(value) || value < 0) {
                          setEditingModel((prev) => prev ? { ...prev, temperature: 0 } : null)
                        } else if (value > 2) {
                          setEditingModel((prev) => prev ? { ...prev, temperature: 2 } : null)
                        }
                      }}
                      step={0.01}
                      min={0}
                      max={2}
                      className="h-8 w-24 text-right text-sm tabular-nums sm:w-20"
                    />
                  </div>
                  <div className="hidden items-center gap-3 sm:flex">
                    <span className="text-xs text-muted-foreground tabular-nums">0</span>
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
                    />
                    <span className="text-xs text-muted-foreground tabular-nums">2</span>
                  </div>
                  {editingModel.temperature > 1 && (
                    <Alert className="bg-amber-500/10 border-amber-500/20 [&>svg+div]:translate-y-0">
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                      <AlertDescription className="text-xs text-amber-600 dark:text-amber-400">
                        温度 &gt; 1 会产生更随机、更不可预测的输出，请谨慎使用
                      </AlertDescription>
                    </Alert>
                  )}
                  <p className="text-xs text-muted-foreground">
                    较低（0.1-0.5）产生确定输出，中等（0.5-1.0）平衡创造性，较高（1.0-2.0）产生极度随机输出
                  </p>
                </div>
              )}
            </div>

            {/* 模型级别最大 Token */}
            <div className="space-y-2 rounded-lg border p-3 sm:space-y-3 sm:p-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="enable_model_max_tokens" className="cursor-pointer">自定义最大 Token</Label>
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
                        </div>
                      }
                      side="right"
                      maxWidth="400px"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    启用后将覆盖「为模型分配功能」中的任务最大 Token 配置
                  </p>
                </div>
                <Switch
                  id="enable_model_max_tokens"
                  checked={editingModel?.max_tokens != null}
                  onCheckedChange={(checked) => {
                    if (checked) {
                      // 启用时设置默认值 2048
                      setEditingModel((prev) => prev ? { ...prev, max_tokens: 2048 } : null)
                    } else {
                      // 禁用时清除
                      setEditingModel((prev) => prev ? { ...prev, max_tokens: null } : null)
                    }
                  }}
                />
              </div>
              
              {editingModel?.max_tokens != null && (
                <div className="space-y-2 pt-2 border-t">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm">最大 Token 数</Label>
                    <Input
                      type="number"
                      min="1"
                      max="128000"
                      value={editingModel.max_tokens}
                      onChange={(e) => {
                        const val = parseInt(e.target.value)
                        if (!isNaN(val) && val >= 1) {
                          setEditingModel((prev) => prev ? { ...prev, max_tokens: val } : null)
                        }
                      }}
                      className="w-28 h-8 text-sm"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    限制模型单次输出的最大 token 数量，不同模型支持的上限不同
                  </p>
                </div>
              )}
            </div>

            {/* 额外参数 */}
            <div className="space-y-2">
              <Label className="text-sm font-medium">额外参数</Label>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="flex-1 justify-start h-9"
                  onClick={() => setExtraParamsDialogOpen(true)}
                >
                  <Settings className="h-4 w-4 mr-2" />
                  {Object.keys(editingModel?.extra_params || {}).length > 0 ? (
                    <span>
                      已配置 {Object.keys(editingModel?.extra_params || {}).length} 个参数
                    </span>
                  ) : (
                    <span className="text-muted-foreground">未配置额外参数</span>
                  )}
                </Button>
              </div>
              {Object.keys(editingModel?.extra_params || {}).length > 0 && (
                <div className="text-xs text-muted-foreground px-1">
                  {Object.keys(editingModel?.extra_params || {})
                    .slice(0, 3)
                    .map((key) => (
                      <span key={key} className="inline-block mr-2">
                        <code className="px-1.5 py-0.5 bg-muted rounded">{key}</code>
                      </span>
                    ))}
                  {Object.keys(editingModel?.extra_params || {}).length > 3 && (
                    <span>...</span>
                  )}
                </div>
              )}
            </div>
          </div>
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
              data-tour="model-save-button"
            >
              保存
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
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              更换嵌入模型警告
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>
                  <strong className="text-foreground">注意：</strong>更换嵌入模型可能会影响知识库的匹配精度！
                </p>
                <ul className="space-y-2 ml-4 list-disc text-muted-foreground">
                  <li>不同的嵌入模型会产生不同的向量表示</li>
                  <li>这可能导致现有知识库的检索结果不准确</li>
                  <li>建议更换嵌入模型后重新生成所有知识库的向量</li>
                </ul>
                <p className="text-foreground font-medium">
                  确定要更换嵌入模型吗？
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={embeddingWarning.cancel}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={embeddingWarning.confirm}
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
        onChange={(params) =>
          setEditingModel((prev) =>
            prev ? { ...prev, extra_params: params } : null
          )
        }
      />

      <AlertDialog open={deletingVersionId !== null} onOpenChange={(open) => !open && setDeletingVersionId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除模型配置副本</AlertDialogTitle>
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
      <RestartOverlay />
      </div>
    </ScrollArea>
  )
}
