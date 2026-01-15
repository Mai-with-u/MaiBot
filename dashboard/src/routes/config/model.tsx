import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
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
  AlertDialogTrigger,
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
import { Plus, Pencil, Trash2, Save, Search, Info, Power, Check, ChevronsUpDown, RefreshCw, Loader2, GraduationCap, Share2, AlertTriangle, Settings, Lock, Unlock } from 'lucide-react'
import { getModelConfig, updateModelConfig } from '@/lib/config-api'
import { useToast } from '@/hooks/use-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { ExtraParamsDialog } from '@/components/ui/extra-params-dialog'
import { SharePackDialog } from '@/components/share-pack-dialog'

// 导入模块化的类型定义和组件
import type { ModelInfo, ProviderConfig, ModelTaskConfig, TaskConfig } from './model/types'
import { TaskConfigCard, Pagination, ModelTable, ModelCardList } from './model/components'
import { useModelTour, useModelFetcher, useModelAutoSave } from './model/hooks'

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
  const [models, setModels] = useState<ModelInfo[]>([])
  const [providers, setProviders] = useState<string[]>([])
  const [providerConfigs, setProviderConfigs] = useState<ProviderConfig[]>([])
  const [modelNames, setModelNames] = useState<string[]>([])
  const [taskConfig, setTaskConfig] = useState<ModelTaskConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<ModelInfo | null>(null)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [extraParamsDialogOpen, setExtraParamsDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedModels, setSelectedModels] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')
  
  const [advancedTemperatureMode, setAdvancedTemperatureMode] = useState(false)
  
  // 模型 Combobox 状态
  const [modelComboboxOpen, setModelComboboxOpen] = useState(false)
  
  // 嵌入模型警告相关状态
  const [embeddingWarningOpen, setEmbeddingWarningOpen] = useState(false)
  const previousEmbeddingModelsRef = useRef<string[]>([])
  const pendingEmbeddingUpdateRef = useRef<{ field: keyof TaskConfig; value: string[] | number } | null>(null)
  
  // 任务配置问题检查状态
  const [invalidModelRefs, setInvalidModelRefs] = useState<{ taskName: string; invalidModels: string[] }[]>([])
  const [emptyTasks, setEmptyTasks] = useState<string[]>([])
  
  // 表单验证错误状态
  const [formErrors, setFormErrors] = useState<{
    name?: string
    api_provider?: string
    model_identifier?: string
  }>({})
  
  const { toast } = useToast()
  const { triggerRestart, isRestarting } = useRestart()
  
  // Tour 引导 (使用 hook 封装的逻辑)
  const { startTour: handleStartTour, isRunning: tourIsRunning } = useModelTour({
    onCloseEditDialog: () => setEditDialogOpen(false),
  })

  // 自动保存 (使用 hook 封装的逻辑)
  const { clearTimers: clearAutoSaveTimers, initialLoadRef } = useModelAutoSave({
    models,
    taskConfig,
    onSavingChange: setAutoSaving,
    onUnsavedChange: setHasUnsavedChanges,
  })

  // 检查任务配置问题
  const checkTaskConfigIssues = useCallback((taskConf: ModelTaskConfig | null, modelList: ModelInfo[]) => {
    if (!taskConf) return
    
    const modelNameSet = new Set(modelList.map(m => m.name))
    const invalidRefs: { taskName: string; invalidModels: string[] }[] = []
    const emptyTaskList: string[] = []
    
    const taskNames: Array<{ key: keyof ModelTaskConfig; label: string }> = [
      { key: 'utils', label: '工具模型' },
      { key: 'tool_use', label: '工具调用模型' },
      { key: 'replyer', label: '回复模型' },
      { key: 'planner', label: '规划器模型' },
      { key: 'vlm', label: '视觉模型' },
      { key: 'voice', label: '语音模型' },
      { key: 'embedding', label: '嵌入模型' },
      { key: 'lpmm_entity_extract', label: 'LPMM实体抽取' },
      { key: 'lpmm_rdf_build', label: 'LPMM关系构建' },
    ]
    
    for (const { key, label } of taskNames) {
      const task = taskConf[key]
      if (!task) continue
      
      // 检查是否有模型
      if (!task.model_list || task.model_list.length === 0) {
        emptyTaskList.push(label)
        continue
      }
      
      // 检查是否引用了不存在的模型
      const invalid = task.model_list.filter(modelName => !modelNameSet.has(modelName))
      if (invalid.length > 0) {
        invalidRefs.push({ taskName: label, invalidModels: invalid })
      }
    }
    
    setInvalidModelRefs(invalidRefs)
    setEmptyTasks(emptyTaskList)
  }, [])
  
  // 加载配置
  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      const config = await getModelConfig()
      const modelList = (config.models as ModelInfo[]) || []
      setModels(modelList)
      setModelNames(modelList.map((m) => m.name))
      
      const providerList = (config.api_providers as ProviderConfig[]) || []
      setProviders(providerList.map((p) => p.name))
      setProviderConfigs(providerList)
      
      const taskConf = (config.model_task_config as ModelTaskConfig) || null
      setTaskConfig(taskConf)
      
      // 检查任务配置问题
      checkTaskConfigIssues(taskConf, modelList)
      
      // 初始化上一次的 embedding 模型列表
      const embeddingModels = taskConf?.embedding?.model_list || []
      previousEmbeddingModelsRef.current = [...embeddingModels]
      setHasUnsavedChanges(false)
      initialLoadRef.current = false
    } catch (error) {
      console.error('加载配置失败:', error)
    } finally {
      setLoading(false)
    }
  }, [initialLoadRef, checkTaskConfigIssues])

  // 初始加载
  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  // 获取指定提供商的配置
  const getProviderConfig = useCallback((providerName: string): ProviderConfig | undefined => {
    return providerConfigs.find(p => p.name === providerName)
  }, [providerConfigs])

  // 模型列表获取 (使用 hook 封装的逻辑)
  const {
    availableModels,
    fetchingModels,
    modelFetchError,
    matchedTemplate,
    fetchModelsForProvider,
    clearModels,
  } = useModelFetcher({ getProviderConfig })

  // 当选择的提供商变化时，获取模型列表
  useEffect(() => {
    if (editDialogOpen && editingModel?.api_provider) {
      fetchModelsForProvider(editingModel.api_provider)
    }
  }, [editDialogOpen, editingModel?.api_provider, fetchModelsForProvider])

  // 重启麦麦
  const handleRestart = async () => {
    await triggerRestart()
  }
  
  // 一键删除所有无效模型引用
  const handleRemoveInvalidRefs = useCallback(() => {
    if (!taskConfig) return
    
    const modelNameSet = new Set(models.map(m => m.name))
    const newTaskConfig = { ...taskConfig }
    
    // 遍历所有任务，过滤掉无效的模型引用
    const taskKeys = Object.keys(newTaskConfig) as Array<keyof ModelTaskConfig>
    for (const key of taskKeys) {
      const task = newTaskConfig[key]
      if (task && task.model_list) {
        task.model_list = task.model_list.filter(modelName => modelNameSet.has(modelName))
      }
    }
    
    setTaskConfig(newTaskConfig)
    setInvalidModelRefs([])
    
    toast({
      title: '清理完成',
      description: '已删除所有无效的模型引用',
    })
  }, [taskConfig, models, toast])

  // 清理模型中的 null 值（TOML 不支持 null）
  const cleanModelForSave = (model: ModelInfo): ModelInfo => {
    const cleaned: ModelInfo = {
      model_identifier: model.model_identifier,
      name: model.name,
      api_provider: model.api_provider,
      price_in: model.price_in ?? 0,
      price_out: model.price_out ?? 0,
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
  }

  // 保存并重启
  const handleSaveAndRestart = async () => {
    try {
      setSaving(true)
      clearAutoSaveTimers()
      const config = await getModelConfig()
      // 清理每个模型中的 null 值
      config.models = models.map(cleanModelForSave)
      config.model_task_config = taskConfig
      await updateModelConfig(config)
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '正在重启麦麦...',
      })
      await handleRestart()
    } catch (error) {
      console.error('保存配置失败:', error)
      toast({
        title: '保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
      setSaving(false)
    }
  }

  // 保存配置（手动保存）
  const saveConfig = async () => {
    try {
      setSaving(true)
      
      // 先取消自动保存定时器
      clearAutoSaveTimers()

      const config = await getModelConfig()
      // 清理每个模型中的 null 值
      config.models = models.map(cleanModelForSave)
      config.model_task_config = taskConfig
      await updateModelConfig(config)
      setHasUnsavedChanges(false)
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
  }

  // 打开编辑对话框
  const openEditDialog = (model: ModelInfo | null, index: number | null) => {
    // 清除表单验证错误
    setFormErrors({})
    
    setEditingModel(
      model || {
        model_identifier: '',
        name: '',
        api_provider: providers[0] || '',
        price_in: 0,
        price_out: 0,
        temperature: null,
        max_tokens: null,
        force_stream_mode: false,
        extra_params: {},
      }
    )
    setEditingIndex(index)
    setEditDialogOpen(true)
  }

  // 保存编辑
  const handleSaveEdit = () => {
    if (!editingModel) return

    // 验证必填项
    const errors: { name?: string; api_provider?: string; model_identifier?: string } = {}
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
    // 立即更新模型名称列表
    setModelNames(newModels.map((m) => m.name))

    // 如果模型名称发生变化，更新任务配置中对该模型的引用
    if (oldModelName && oldModelName !== modelToSave.name && taskConfig) {
      const updateModelList = (list: string[]): string[] => {
        return list.map(name => name === oldModelName ? modelToSave.name : name)
      }
      
      setTaskConfig({
        ...taskConfig,
        utils: { ...taskConfig.utils, model_list: updateModelList(taskConfig.utils?.model_list || []) },
        tool_use: { ...taskConfig.tool_use, model_list: updateModelList(taskConfig.tool_use?.model_list || []) },
        replyer: { ...taskConfig.replyer, model_list: updateModelList(taskConfig.replyer?.model_list || []) },
        planner: { ...taskConfig.planner, model_list: updateModelList(taskConfig.planner?.model_list || []) },
        vlm: { ...taskConfig.vlm, model_list: updateModelList(taskConfig.vlm?.model_list || []) },
        voice: { ...taskConfig.voice, model_list: updateModelList(taskConfig.voice?.model_list || []) },
        embedding: { ...taskConfig.embedding, model_list: updateModelList(taskConfig.embedding?.model_list || []) },
        lpmm_entity_extract: { ...taskConfig.lpmm_entity_extract, model_list: updateModelList(taskConfig.lpmm_entity_extract?.model_list || []) },
        lpmm_rdf_build: { ...taskConfig.lpmm_rdf_build, model_list: updateModelList(taskConfig.lpmm_rdf_build?.model_list || []) },
      })
    }

    setEditDialogOpen(false)
    setEditingModel(null)
    setEditingIndex(null)
    
    // 提示用户配置将自动保存
    toast({
      title: editingIndex !== null ? '模型已更新' : '模型已添加',
      description: '配置将在 2 秒后自动保存，或点击右上角"保存配置"按钮立即保存',
    })
  }

  // 处理编辑对话框关闭
  const handleEditDialogClose = (open: boolean) => {
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
  }

  // 打开删除确认对话框
  const openDeleteDialog = (index: number) => {
    setDeletingIndex(index)
    setDeleteDialogOpen(true)
  }

  // 确认删除模型
  const handleConfirmDelete = () => {
    if (deletingIndex !== null) {
      const newModels = models.filter((_, i) => i !== deletingIndex)
      setModels(newModels)
      // 立即更新模型名称列表
      setModelNames(newModels.map((m) => m.name))
      // 重新检查任务配置问题
      checkTaskConfigIssues(taskConfig, newModels)
      toast({
        title: '删除成功',
        description: '配置将在 2 秒后自动保存，或点击右上角"保存配置"按钮立即保存',
      })
    }
    setDeleteDialogOpen(false)
    setDeletingIndex(null)
  }

  // 切换单个模型选择
  const toggleModelSelection = (index: number) => {
    const newSelected = new Set(selectedModels)
    if (newSelected.has(index)) {
      newSelected.delete(index)
    } else {
      newSelected.add(index)
    }
    setSelectedModels(newSelected)
  }

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedModels.size === filteredModels.length) {
      setSelectedModels(new Set())
    } else {
      const allIndices = filteredModels.map((_, idx) => 
        models.findIndex(m => m === filteredModels[idx])
      )
      setSelectedModels(new Set(allIndices))
    }
  }

  // 打开批量删除确认对话框
  const openBatchDeleteDialog = () => {
    if (selectedModels.size === 0) {
      toast({
        title: '提示',
        description: '请先选择要删除的模型',
        variant: 'default',
      })
      return
    }
    setBatchDeleteDialogOpen(true)
  }

  // 确认批量删除
  const handleConfirmBatchDelete = () => {
    const deletedCount = selectedModels.size
    const newModels = models.filter((_, index) => !selectedModels.has(index))
    setModels(newModels)
    // 立即更新模型名称列表
    setModelNames(newModels.map((m) => m.name))
    // 重新检查任务配置问题
    checkTaskConfigIssues(taskConfig, newModels)
    setSelectedModels(new Set())
    setBatchDeleteDialogOpen(false)
    toast({
      title: '批量删除成功',
      description: `已删除 ${deletedCount} 个模型，配置将在 2 秒后自动保存`,
    })
  }

  // 更新任务配置
  const updateTaskConfig = (
    taskName: keyof ModelTaskConfig,
    field: keyof TaskConfig,
    value: string[] | number | string
  ) => {
    if (!taskConfig) return
    
    // 检测 embedding 模型列表变化
    if (taskName === 'embedding' && field === 'model_list' && Array.isArray(value)) {
      const previousModels = previousEmbeddingModelsRef.current
      const newModels = value as string[]
      
      // 判断是否有变化（添加、删除或替换）
      const hasChanges = 
        previousModels.length !== newModels.length ||
        previousModels.some(model => !newModels.includes(model)) ||
        newModels.some(model => !previousModels.includes(model))
      
      if (hasChanges && previousModels.length > 0) {
        // 存储待更新的配置
        pendingEmbeddingUpdateRef.current = { field, value }
        // 显示警告对话框
        setEmbeddingWarningOpen(true)
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
    
    // 如果是 embedding 模型列表，更新 ref
    if (taskName === 'embedding' && field === 'model_list' && Array.isArray(value)) {
      previousEmbeddingModelsRef.current = [...(value as string[])]
    }
  }
  
  // 确认更新嵌入模型
  const handleConfirmEmbeddingChange = () => {
    if (!taskConfig || !pendingEmbeddingUpdateRef.current) return
    
    const { field, value } = pendingEmbeddingUpdateRef.current
    
    const newTaskConfig = {
      ...taskConfig,
      embedding: {
        ...taskConfig.embedding,
        [field]: value,
      },
    }
    setTaskConfig(newTaskConfig)
    
    // 重新检查任务配置问题
    checkTaskConfigIssues(newTaskConfig, models)
    
    // 更新 ref
    if (field === 'model_list' && Array.isArray(value)) {
      previousEmbeddingModelsRef.current = [...(value as string[])]
    }
    
    // 清理
    pendingEmbeddingUpdateRef.current = null
    setEmbeddingWarningOpen(false)
    
    toast({
      title: '嵌入模型已更新',
      description: '建议重新生成知识库向量以确保最佳匹配精度',
    })
  }
  
  // 取消更新嵌入模型
  const handleCancelEmbeddingChange = () => {
    pendingEmbeddingUpdateRef.current = null
    setEmbeddingWarningOpen(false)
  }

  // 过滤模型列表
  const filteredModels = models.filter((model) => {
    if (!searchQuery) return true
    const query = searchQuery.toLowerCase()
    return (
      model.name.toLowerCase().includes(query) ||
      model.model_identifier.toLowerCase().includes(query) ||
      model.api_provider.toLowerCase().includes(query)
    )
  })

  // 分页逻辑
  const totalPages = Math.ceil(filteredModels.length / pageSize)
  const paginatedModels = filteredModels.slice(
    (page - 1) * pageSize,
    page * pageSize
  )

  // 页码跳转
  const handleJumpToPage = () => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    }
  }

  // 检查模型是否被任务使用
  const isModelUsed = (modelName: string): boolean => {
    if (!taskConfig) return false
    
    const allTaskLists = [
      taskConfig.utils?.model_list || [],
      taskConfig.tool_use?.model_list || [],
      taskConfig.replyer?.model_list || [],
      taskConfig.planner?.model_list || [],
      taskConfig.vlm?.model_list || [],
      taskConfig.voice?.model_list || [],
      taskConfig.embedding?.model_list || [],
      taskConfig.lpmm_entity_extract?.model_list || [],
      taskConfig.lpmm_rdf_build?.model_list || [],
    ]
    
    return allTaskLists.some(list => list.includes(modelName))
  }

  if (loading) {
    return (
      <ScrollArea className="h-full">
        <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
          <div className="flex items-center justify-center h-64">
            <p className="text-muted-foreground">加载中...</p>
          </div>
        </div>
      </ScrollArea>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        {/* 页面标题 */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">模型管理与分配</h1>
            <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">添加模型并为模型分配功能</p>
          </div>
          <div className="flex gap-2 w-full sm:w-auto">
            <SharePackDialog 
              trigger={
                <Button variant="outline" size="sm" className="flex-1 sm:flex-none">
                  <Share2 className="mr-2 h-4 w-4" />
                  分享配置
                </Button>
              }
            />
            <Button 
              onClick={saveConfig} 
              disabled={saving || autoSaving || !hasUnsavedChanges || isRestarting} 
              size="sm"
              variant="outline"
              className="flex-1 sm:flex-none sm:min-w-[120px]"
            >
              <Save className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
              {saving ? '保存中...' : autoSaving ? '自动保存中...' : hasUnsavedChanges ? '保存配置' : '已保存'}
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  disabled={saving || autoSaving || isRestarting}
                  size="sm"
                  className="flex-1 sm:flex-none sm:min-w-[120px]"
                >
                  <Power className="mr-2 h-4 w-4" />
                  {isRestarting ? '重启中...' : hasUnsavedChanges ? '保存并重启' : '重启麦麦'}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认重启麦麦？</AlertDialogTitle>
                  <AlertDialogDescription asChild>
                    <div>
                      <p>
                        {hasUnsavedChanges 
                          ? '当前有未保存的配置更改。点击确认将先保存配置,然后重启麦麦使新配置生效。重启过程中麦麦将暂时离线。'
                          : '即将重启麦麦主程序。重启过程中麦麦将暂时离线,配置将在重启后生效。'
                        }
                      </p>
                    </div>
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={hasUnsavedChanges ? handleSaveAndRestart : handleRestart}>
                    {hasUnsavedChanges ? '保存并重启' : '确认重启'}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        {/* 重启提示 */}
        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            配置更新后需要<strong>重启麦麦</strong>才能生效。你可以点击右上角的"保存并重启"按钮一键完成保存和重启。
          </AlertDescription>
        </Alert>
        
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
        <Alert className="hidden lg:flex border-primary/30 bg-primary/5 cursor-pointer hover:bg-primary/10 transition-colors" onClick={handleStartTour}>
          <GraduationCap className="h-4 w-4 text-primary" />
          <AlertDescription className="flex items-center justify-between">
            <span>
              <strong className="text-primary">新手引导：</strong>不知道如何配置模型？点击这里开始学习如何为麦麦的组件分配模型。
            </span>
            <Button variant="outline" size="sm" className="ml-4 shrink-0">
              开始引导
            </Button>
          </AlertDescription>
        </Alert>

        {/* 标签页 */}
        <Tabs defaultValue="models" className="w-full">
          <TabsList className="grid w-full max-w-full sm:max-w-md grid-cols-2">
            <TabsTrigger value="models">添加模型</TabsTrigger>
            <TabsTrigger value="tasks" data-tour="tasks-tab-trigger">为模型分配功能</TabsTrigger>
          </TabsList>
          {/* 模型配置标签页 */}
          <TabsContent value="models" className="space-y-4 mt-0">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
              <p className="text-sm text-muted-foreground">
                配置可用的模型列表
              </p>
              <div className="flex gap-2 w-full sm:w-auto">
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
                <Button onClick={() => openEditDialog(null, null)} size="sm" variant="outline" className="w-full sm:w-auto" data-tour="add-model-button">
                  <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
                  添加模型
                </Button>
              </div>
            </div>

          {/* 搜索框 */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2">
            <div className="relative w-full sm:flex-1 sm:max-w-sm">
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
          <ModelCardList
            paginatedModels={paginatedModels}
            allModels={models}
            onEdit={openEditDialog}
            onDelete={openDeleteDialog}
            isModelUsed={isModelUsed}
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
            onToggleSelection={toggleModelSelection}
            onToggleSelectAll={toggleSelectAll}
            isModelUsed={isModelUsed}
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
        <TabsContent value="tasks" className="space-y-6 mt-0">
          <p className="text-sm text-muted-foreground">
            为不同的任务配置使用的模型和参数
          </p>

          {taskConfig && (
            <div className="grid gap-4 sm:gap-6">
              {/* Utils 任务 */}
              <TaskConfigCard
                title="组件模型 (utils)"
                description="用于表情包、取名、关系、情绪变化等组件"
                taskConfig={taskConfig.utils}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('utils', field, value)}
                dataTour="task-model-select"
              />

              {/* Tool Use 任务 */}
              <TaskConfigCard
                title="工具调用模型 (tool_use)"
                description="需要使用支持工具调用的模型"
                taskConfig={taskConfig.tool_use}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('tool_use', field, value)}
              />

              {/* Replyer 任务 */}
              <TaskConfigCard
                title="首要回复模型 (replyer)"
                description="用于表达器和表达方式学习"
                taskConfig={taskConfig.replyer}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('replyer', field, value)}
              />

              {/* Planner 任务 */}
              <TaskConfigCard
                title="决策模型 (planner)"
                description="负责决定麦麦该什么时候回复"
                taskConfig={taskConfig.planner}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('planner', field, value)}
              />

              {/* VLM 任务 */}
              <TaskConfigCard
                title="图像识别模型 (vlm)"
                description="视觉语言模型"
                taskConfig={taskConfig.vlm}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('vlm', field, value)}
                hideTemperature
              />

              {/* Voice 任务 */}
              <TaskConfigCard
                title="语音识别模型 (voice)"
                description="语音转文字"
                taskConfig={taskConfig.voice}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('voice', field, value)}
                hideTemperature
                hideMaxTokens
              />

              {/* Embedding 任务 */}
              <TaskConfigCard
                title="嵌入模型 (embedding)"
                description="用于向量化"
                taskConfig={taskConfig.embedding}
                modelNames={modelNames}
                onChange={(field, value) => updateTaskConfig('embedding', field, value)}
                hideTemperature
                hideMaxTokens
              />

              {/* LPMM 相关任务 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold">LPMM 知识库模型</h3>
                
                <TaskConfigCard
                  title="实体提取模型 (lpmm_entity_extract)"
                  description="从文本中提取实体"
                  taskConfig={taskConfig.lpmm_entity_extract}
                  modelNames={modelNames}
                  onChange={(field, value) =>
                    updateTaskConfig('lpmm_entity_extract', field, value)
                  }
                />

                <TaskConfigCard
                  title="RDF 构建模型 (lpmm_rdf_build)"
                  description="构建知识图谱"
                  taskConfig={taskConfig.lpmm_rdf_build}
                  modelNames={modelNames}
                  onChange={(field, value) =>
                    updateTaskConfig('lpmm_rdf_build', field, value)
                  }
                />
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* 编辑模型对话框 */}
      <Dialog open={editDialogOpen} onOpenChange={handleEditDialogClose}>
        <DialogContent 
          className="max-w-[95vw] sm:max-w-2xl max-h-[90vh] overflow-y-auto" 
          data-tour="model-dialog"
          preventOutsideClose={tourIsRunning}
        >
          <DialogHeader>
            <DialogTitle>
              {editingIndex !== null ? '编辑模型' : '添加模型'}
            </DialogTitle>
            <DialogDescription>配置模型的基本信息和参数</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4">
            <div className="grid gap-2" data-tour="model-name-input">
              <Label htmlFor="model_name" className={formErrors.name ? 'text-destructive' : ''}>模型名称 *</Label>
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
                className={formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}
              />
              {formErrors.name ? (
                <p className="text-xs text-destructive">{formErrors.name}</p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  用于在任务配置中引用此模型
                </p>
              )}
            </div>

            <div className="grid gap-2" data-tour="model-provider-select">
              <Label htmlFor="api_provider" className={formErrors.api_provider ? 'text-destructive' : ''}>API 提供商 *</Label>
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
              {formErrors.api_provider && (
                <p className="text-xs text-destructive">{formErrors.api_provider}</p>
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
              
              {/* 模型标识符 Combobox */}
              {matchedTemplate?.modelFetcher ? (
                <Popover open={modelComboboxOpen} onOpenChange={setModelComboboxOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-expanded={modelComboboxOpen}
                      className="w-full justify-between font-normal"
                      disabled={fetchingModels || !!modelFetchError}
                    >
                      {fetchingModels ? (
                        <span className="flex items-center gap-2 text-muted-foreground">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          正在获取模型列表...
                        </span>
                      ) : modelFetchError ? (
                        <span className="text-muted-foreground text-sm">点击下方输入框手动填写</span>
                      ) : editingModel?.model_identifier ? (
                        <span className="truncate">{editingModel.model_identifier}</span>
                      ) : (
                        <span className="text-muted-foreground">搜索或选择模型...</span>
                      )}
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
                    <Command>
                      <CommandInput placeholder="搜索模型..." />
                      <ScrollArea className="h-[300px]">
                        <CommandList className="max-h-none overflow-visible">
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
                                onSelect={() => {
                                  setEditingModel((prev) =>
                                    prev ? { ...prev, model_identifier: model.id } : null
                                  )
                                  setModelComboboxOpen(false)
                                }}
                              >
                                <Check
                                  className={`mr-2 h-4 w-4 ${
                                    editingModel?.model_identifier === model.id ? 'opacity-100' : 'opacity-0'
                                  }`}
                                />
                                <div className="flex flex-col">
                                  <span>{model.id}</span>
                                  {model.name !== model.id && (
                                    <span className="text-xs text-muted-foreground">{model.name}</span>
                                  )}
                                </div>
                              </CommandItem>
                            ))}
                          </CommandGroup>
                          <CommandGroup heading="手动输入">
                            <CommandItem
                              value="__manual_input__"
                              onSelect={() => {
                                setModelComboboxOpen(false)
                                // 聚焦到手动输入框（如果需要的话可以实现）
                              }}
                            >
                              <Pencil className="mr-2 h-4 w-4" />
                              手动输入模型标识符...
                            </CommandItem>
                          </CommandGroup>
                        </CommandList>
                      </ScrollArea>
                    </Command>
                  </PopoverContent>
                </Popover>
              ) : (
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
                  placeholder="Qwen/Qwen3-30B-A3B-Instruct-2507"
                  className={formErrors.model_identifier ? 'border-destructive focus-visible:ring-destructive' : ''}
                />
              )}
              
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
              
              {/* 手动输入区域 - 当使用 Combobox 时也显示一个可编辑的输入框 */}
              {matchedTemplate?.modelFetcher && (
                <Input
                  value={editingModel?.model_identifier || ''}
                  onChange={(e) => {
                    setEditingModel((prev) =>
                      prev ? { ...prev, model_identifier: e.target.value } : null
                    )
                    if (formErrors.model_identifier) {
                      setFormErrors((prev) => ({ ...prev, model_identifier: undefined }))
                    }
                  }}
                  placeholder="或手动输入模型标识符"
                  className={`mt-2 ${formErrors.model_identifier ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                />
              )}
              
              {!formErrors.model_identifier && (
                <p className="text-xs text-muted-foreground">
                  {modelFetchError 
                    ? '请手动输入模型标识符，或前往"模型提供商配置"检查 API Key'
                    : matchedTemplate?.modelFetcher 
                      ? `已识别为 ${matchedTemplate.display_name}，支持自动获取模型列表` 
                      : 'API 提供商提供的模型 ID'}
                </p>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
            </div>

            {/* 模型级别温度 */}
            <div className="rounded-lg border p-4 space-y-3">
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
                      setEditingModel((prev) => prev ? { ...prev, temperature: 0.5 } : null)
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
                    <div className="flex items-center gap-2">
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
                        className="w-20 h-8 text-sm text-right tabular-nums"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setAdvancedTemperatureMode(!advancedTemperatureMode)}
                        className="h-8 px-2"
                        title={advancedTemperatureMode ? "切换到基础模式 (0-1)" : "解锁高级范围 (0-2)"}
                      >
                        {advancedTemperatureMode ? (
                          <Unlock className="h-4 w-4" />
                        ) : (
                          <Lock className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground tabular-nums">0</span>
                    <Slider
                      value={[editingModel.temperature]}
                      onValueChange={(values) =>
                        setEditingModel((prev) =>
                          prev ? { ...prev, temperature: values[0] } : null
                        )
                      }
                      min={0}
                      max={advancedTemperatureMode ? 2 : 1}
                      step={advancedTemperatureMode ? 0.05 : 0.1}
                      className="flex-1"
                    />
                    <span className="text-xs text-muted-foreground tabular-nums">{advancedTemperatureMode ? '2' : '1'}</span>
                  </div>
                  {advancedTemperatureMode && (
                    <Alert className="bg-amber-500/10 border-amber-500/20 [&>svg+div]:translate-y-0">
                      <AlertTriangle className="h-4 w-4 text-amber-500" />
                      <AlertDescription className="text-xs text-amber-600 dark:text-amber-400">
                        高级模式：温度 &gt; 1 会产生更随机、更不可预测的输出，请谨慎使用
                      </AlertDescription>
                    </Alert>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {advancedTemperatureMode 
                      ? "较低（0.1-0.5）产生确定输出，中等（0.5-1.0）平衡创造性，较高（1.0-2.0）产生极度随机输出"
                      : "较低的温度（0.1-0.3）产生更确定的输出，较高的温度（0.7-1.0）产生更多样化的输出"
                    }
                  </p>
                </div>
              )}
            </div>

            {/* 模型级别最大 Token */}
            <div className="rounded-lg border p-4 space-y-3">
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

            <div className="flex items-center space-x-2">
              <Switch
                id="force_stream_mode"
                checked={editingModel?.force_stream_mode || false}
                onCheckedChange={(checked) =>
                  setEditingModel((prev) =>
                    prev ? { ...prev, force_stream_mode: checked } : null
                  )
                }
              />
              <Label htmlFor="force_stream_mode" className="cursor-pointer">
                强制流式输出模式
              </Label>
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

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDialogOpen(false)} data-tour="model-cancel-button">
              取消
            </Button>
            <Button onClick={handleSaveEdit} data-tour="model-save-button">保存</Button>
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
      <AlertDialog open={embeddingWarningOpen} onOpenChange={setEmbeddingWarningOpen}>
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
            <AlertDialogCancel onClick={handleCancelEmbeddingChange}>取消</AlertDialogCancel>
            <AlertDialogAction 
              onClick={handleConfirmEmbeddingChange}
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

      {/* 重启遮罩层 */}
      <RestartOverlay />
      </div>
    </ScrollArea>
  )
}
