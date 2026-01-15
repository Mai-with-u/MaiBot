import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { Plus, Pencil, Trash2, Save, Eye, EyeOff, Copy, Search, Info, Power, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Check, ChevronsUpDown, Zap, Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'
import { getModelConfig, updateModelConfig, updateModelConfigSection, testProviderConnection, type TestConnectionResult } from '@/lib/config-api'
import { useToast } from '@/hooks/use-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { useTour } from '@/components/tour'
import { MODEL_ASSIGNMENT_TOUR_ID, modelAssignmentTourSteps, STEP_ROUTE_MAP } from '@/components/tour/tours/model-assignment-tour'
import { useNavigate } from '@tanstack/react-router'
import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { PROVIDER_TEMPLATES } from './providerTemplates'
import type { APIProvider, DeleteConfirmState, FormErrors } from './modelProvider/types'
import { cleanProviderData, validateProvider } from './modelProvider/utils'

// 主导出组件：包装 RestartProvider
export function ModelProviderConfigPage() {
  return (
    <RestartProvider>
      <ModelProviderConfigPageContent />
    </RestartProvider>
  )
}

// 内部实现组件
function ModelProviderConfigPageContent() {
  const [providers, setProviders] = useState<APIProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<APIProvider | null>(null)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [selectedTemplate, setSelectedTemplate] = useState<string>('custom')
  const [templateComboboxOpen, setTemplateComboboxOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [showApiKey, setShowApiKey] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedProviders, setSelectedProviders] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')
  
  // 删除提供商确认对话框状态（合并为单个对象以减少状态变量）
  const [deleteConfirmState, setDeleteConfirmState] = useState<DeleteConfirmState>({
    isOpen: false,
    providersToDelete: [],
    affectedModels: [],
    pendingProviders: [],
    context: 'auto',
    oldProviders: [],
  })
  
  // 表单验证错误状态
  const [formErrors, setFormErrors] = useState<FormErrors>({})
  
  // 测试连接状态
  const [testingProviders, setTestingProviders] = useState<Set<string>>(new Set())
  const [testResults, setTestResults] = useState<Map<string, TestConnectionResult>>(new Map())
  
  const { toast } = useToast()
  const navigate = useNavigate()
  const { state: tourState, goToStep, registerTour } = useTour()
  const { triggerRestart, isRestarting } = useRestart()
  
  // 用于防抖的定时器
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialLoadRef = useRef(true)

  // 注册 Tour（确保跨页导航时 Tour 仍然可用）
  useEffect(() => {
    registerTour(MODEL_ASSIGNMENT_TOUR_ID, modelAssignmentTourSteps)
  }, [registerTour])

  // 监听 Tour 步骤变化，处理页面导航
  useEffect(() => {
    if (tourState.activeTourId === MODEL_ASSIGNMENT_TOUR_ID && tourState.isRunning) {
      const targetRoute = STEP_ROUTE_MAP[tourState.stepIndex]
      if (targetRoute && !window.location.pathname.endsWith(targetRoute.replace('/config/', ''))) {
        navigate({ to: targetRoute })
      }
    }
  }, [tourState.stepIndex, tourState.activeTourId, tourState.isRunning, navigate])

  // 监听 Tour 步骤变化，处理弹窗的打开和关闭
  // 提供商弹窗步骤: 3-9 (index 3-9)，弹窗外步骤: 0-2 (index 0-2) 和 10+ (index 10+)
  const prevTourStepRef = useRef(tourState.stepIndex)
  useEffect(() => {
    if (tourState.activeTourId === MODEL_ASSIGNMENT_TOUR_ID && tourState.isRunning) {
      const prevStep = prevTourStepRef.current
      const currentStep = tourState.stepIndex
      
      // 如果从弹窗内步骤 (3-9) 回退到弹窗外步骤 (0-2)，关闭弹窗
      if (prevStep >= 3 && prevStep <= 9 && currentStep < 3) {
        setEditDialogOpen(false)
      }
      
      // 如果从弹窗外步骤 (10+) 回退到弹窗内步骤 (3-9)，重新打开弹窗
      // 这处理了从模型管理页面第 11 步点击"上一步"回到提供商弹窗的情况
      if (prevStep >= 10 && currentStep >= 3 && currentStep <= 9) {
        // 需要打开空白弹窗以便 Tour 可以定位到弹窗内的元素
        setFormErrors({})
        setSelectedTemplate('custom')
        setEditingProvider({
          name: '',
          base_url: '',
          api_key: '',
          client_type: 'openai',
          max_retry: 2,
          timeout: 30,
          retry_interval: 10,
        })
        setEditingIndex(null)
        setShowApiKey(false)
        setEditDialogOpen(true)
      }
      
      prevTourStepRef.current = currentStep
    }
  }, [tourState.stepIndex, tourState.activeTourId, tourState.isRunning])

  // 处理 Tour 中需要用户点击才能继续的步骤
  useEffect(() => {
    if (tourState.activeTourId !== MODEL_ASSIGNMENT_TOUR_ID || !tourState.isRunning) return

    const handleTourClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const currentStep = tourState.stepIndex

      // Step 3 (index 2): 点击添加提供商按钮
      if (currentStep === 2 && target.closest('[data-tour="add-provider-button"]')) {
        setTimeout(() => goToStep(3), 300)
      }
      // Step 10 (index 9): 点击取消按钮（关闭提供商弹窗）
      else if (currentStep === 9 && target.closest('[data-tour="provider-cancel-button"]')) {
        setTimeout(() => goToStep(10), 300)
      }
    }

    document.addEventListener('click', handleTourClick, true)
    return () => document.removeEventListener('click', handleTourClick, true)
  }, [tourState, goToStep])

  // 加载配置
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    try {
      setLoading(true)
      const config = await getModelConfig()
      setProviders((config.api_providers as APIProvider[]) || [])
      setHasUnsavedChanges(false)
      initialLoadRef.current = false
    } catch (error) {
      console.error('加载配置失败:', error)
    } finally {
      setLoading(false)
    }
  }

  // 重启麦麦
  const handleRestart = async () => {
    await triggerRestart()
  }

  // 保存并重启
  const handleSaveAndRestart = async () => {
    try {
      setSaving(true)
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
      
      // 清理 providers 数据：将 null 值转换为默认值
      const cleanedProviders = providers.map(provider => ({
        ...provider,
        max_retry: provider.max_retry ?? 2,
        timeout: provider.timeout ?? 30,
        retry_interval: provider.retry_interval ?? 10,
      }))
      
      // 检查删除提供商的影响
      const { shouldProceed } = await checkDeleteProviderImpact(cleanedProviders, 'restart')
      if (!shouldProceed) {
        // 需要用户确认，等待确认对话框
        setSaving(false)
        return
      }
      
      const config = await getModelConfig()
      
      // 获取所有有效的 provider 名称
      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      
      // 过滤掉引用已删除 provider 的模型
      const originalModels = (config.models as any[]) || []
      const filteredModels = originalModels.filter((model: any) => {
        return validProviderNames.has(model.api_provider)
      })
      
      config.api_providers = cleanedProviders
      config.models = filteredModels
      
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

  // 检查删除提供商的影响
  const checkDeleteProviderImpact = useCallback(async (
    newProviders: APIProvider[], 
    context: 'auto' | 'manual' | 'restart' = 'auto'
  ) => {
    try {
      const config = await getModelConfig()
      const oldProviderNames = new Set(providers.map(p => p.name))
      const newProviderNames = new Set(newProviders.map(p => p.name))
      
      // 找出被删除的提供商
      const deletedProviders = Array.from(oldProviderNames).filter(
        name => !newProviderNames.has(name)
      )
      
      if (deletedProviders.length === 0) {
        // 没有删除提供商，直接保存
        return { shouldProceed: true, providers: newProviders }
      }
      
      // 检查受影响的模型
      const models = (config.models as any[]) || []
      const affected = models.filter((m: any) => 
        deletedProviders.includes(m.api_provider)
      )
      
      if (affected.length === 0) {
        // 没有受影响的模型，直接删除
        return { shouldProceed: true, providers: newProviders }
      }
      
      // 有受影响的模型，需要用户确认
      setDeleteConfirmState({
        isOpen: true,
        providersToDelete: deletedProviders,
        affectedModels: affected,
        pendingProviders: newProviders,
        context,
        oldProviders: [...providers],
      })
      
      return { shouldProceed: false, providers: newProviders }
    } catch (error) {
      console.error('检查删除影响失败:', error)
      return { shouldProceed: true, providers: newProviders }
    }
  }, [providers])
  
  // 确认删除提供商及其关联的模型
  const handleConfirmDeleteProvider = async () => {
    try {
      const savingFlag = deleteConfirmState.context === 'auto' ? setAutoSaving : setSaving
      savingFlag(true)
      
      setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))
      
      const config = await getModelConfig()
      
      // 清理 providers 数据
      const cleanedProviders = deleteConfirmState.pendingProviders.map(cleanProviderData)
      
      // 获取有效的 provider 名称
      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      
      // 过滤掉引用已删除 provider 的模型
      const originalModels = (config.models as any[]) || []
      const filteredModels = originalModels.filter((model: any) => {
        return validProviderNames.has(model.api_provider)
      })
      
      // 获取被删除的模型名称
      const deletedModelNames = new Set(
        deleteConfirmState.affectedModels.map((m: any) => m.name)
      )
      
      // 从任务配置中移除这些模型
      const modelTaskConfig = config.model_task_config as any
      if (modelTaskConfig) {
        Object.keys(modelTaskConfig).forEach(taskName => {
          const task = modelTaskConfig[taskName]
          if (task && Array.isArray(task.model_list)) {
            task.model_list = task.model_list.filter(
              (modelName: string) => !deletedModelNames.has(modelName)
            )
          }
        })
      }
      
      // 更新配置
      config.api_providers = cleanedProviders
      config.models = filteredModels
      config.model_task_config = modelTaskConfig
      
      await updateModelConfig(config)
      
      // 更新本地状态
      setProviders(deleteConfirmState.pendingProviders)
      setHasUnsavedChanges(false)
      
      toast({
        title: '删除成功',
        description: `已删除 ${deleteConfirmState.providersToDelete.length} 个提供商和 ${deleteConfirmState.affectedModels.length} 个关联模型`,
      })
      
      // 清理状态
      setDeleteConfirmState({
        isOpen: false,
        providersToDelete: [],
        affectedModels: [],
        pendingProviders: [],
        context: 'auto',
        oldProviders: [],
      })
      setSelectedProviders(new Set()) // 清除选中状态（批量删除时）
      
      // 根据上下文执行后续操作
      if (deleteConfirmState.context === 'restart') {
        // 如果是保存并重启，继续执行重启流程
        await handleRestart()
      }
    } catch (error) {
      console.error('删除失败:', error)
      toast({
        title: '删除失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      if (deleteConfirmState.context === 'auto') {
        setAutoSaving(false)
      } else {
        setSaving(false)
      }
    }
  }
  
  // 取消删除提供商
  const handleCancelDeleteProvider = () => {
    // 恢复到删除前的 providers 状态
    if (deleteConfirmState.oldProviders.length > 0) {
      setProviders(deleteConfirmState.oldProviders)
    }
    // 清理状态
    setDeleteConfirmState({
      isOpen: false,
      providersToDelete: [],
      affectedModels: [],
      pendingProviders: [],
      context: 'auto',
      oldProviders: [],
    })
    setHasUnsavedChanges(false)
  }
  
  // 自动保存函数（使用增量 API）
  const autoSaveProviders = useCallback(async (newProviders: APIProvider[]) => {
    if (initialLoadRef.current) return // 初始加载时不自动保存
    
    // 检查删除影响
    const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'auto')
    
    if (!shouldProceed) {
      // 需要用户确认，对话框已打开
      setHasUnsavedChanges(true)
      return
    }
    
    try {
      setAutoSaving(true)
      // 清理 providers 数据：将 null 值转换为默认值
      const cleanedProviders = newProviders.map(cleanProviderData)
      await updateModelConfigSection('api_providers', cleanedProviders)
      setHasUnsavedChanges(false)
    } catch (error) {
      console.error('自动保存失败:', error)
      toast({
        title: '自动保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
      setHasUnsavedChanges(true)
    } finally {
      setAutoSaving(false)
    }
  }, [providers, checkDeleteProviderImpact])

  // 监听 providers 变化，触发自动保存（带防抖）
  useEffect(() => {
    if (initialLoadRef.current) return

    setHasUnsavedChanges(true)

    // 清除之前的定时器
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
    }

    // 设置新的定时器（2秒后自动保存）
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveProviders(providers)
    }, 2000)

    // 清理函数
    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
    }
  }, [providers, autoSaveProviders])

  // 保存配置（手动保存，保存完整配置）
  const saveConfig = async () => {
    try {
      setSaving(true)
      
      // 先取消自动保存定时器
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }

      // 清理 providers 数据：将 null 值转换为默认值
      const cleanedProviders = providers.map(cleanProviderData)
      
      // 检查删除提供商的影响
      const { shouldProceed } = await checkDeleteProviderImpact(cleanedProviders, 'manual')
      if (!shouldProceed) {
        // 需要用户确认，等待确认对话框
        setSaving(false)
        return
      }

      const config = await getModelConfig()
      
      // 获取所有有效的 provider 名称
      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      
      // 过滤掉引用已删除 provider 的模型
      const originalModels = (config.models as any[]) || []
      const filteredModels = originalModels.filter((model: any) => {
        const isValid = validProviderNames.has(model.api_provider)
        if (!isValid) {
          console.warn(`模型 "${model.name}" 引用了已删除的提供商 "${model.api_provider}"，将被移除`)
        }
        return isValid
      })
      
      // 如果有模型被移除，显示警告
      if (originalModels.length !== filteredModels.length) {
        const removedCount = originalModels.length - filteredModels.length
        toast({
          title: '注意',
          description: `已自动移除 ${removedCount} 个引用已删除提供商的模型`,
          variant: 'default',
        })
      }
      
      console.log('发送的 providers 数据:', cleanedProviders)
      config.api_providers = cleanedProviders
      config.models = filteredModels
      console.log('完整配置数据:', config)
      
      await updateModelConfig(config)
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '模型提供商配置已保存',
      })
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
  const openEditDialog = (provider: APIProvider | null, index: number | null) => {
    // 清除表单验证错误
    setFormErrors({})
    
    if (provider) {
      // 编辑现有提供商 - 检测匹配的模板
      const matchedTemplate = PROVIDER_TEMPLATES.find(
        t => t.base_url === provider.base_url && t.client_type === provider.client_type
      )
      setSelectedTemplate(matchedTemplate?.id || 'custom')
      setEditingProvider(provider)
    } else {
      // 新建提供商 - 默认使用自定义模板
      setSelectedTemplate('custom')
      setEditingProvider({
        name: '',
        base_url: '',
        api_key: '',
        client_type: 'openai',
        max_retry: 2,
        timeout: 30,
        retry_interval: 10,
      })
    }
    setEditingIndex(index)
    setShowApiKey(false)
    setEditDialogOpen(true)
  }
  
  // 处理模板选择变化
  const handleTemplateChange = useCallback((templateId: string) => {
    setSelectedTemplate(templateId)
    setTemplateComboboxOpen(false)
    const template = PROVIDER_TEMPLATES.find(t => t.id === templateId)
    if (template && template.id !== 'custom') {
      // 应用模板配置
      setEditingProvider(prev => ({
        ...prev!,
        name: template.name,
        base_url: template.base_url,
        client_type: template.client_type,
      }))
    } else if (template?.id === 'custom') {
      // 切换到自定义模板 - 清空URL和客户端类型(保留其他字段)
      setEditingProvider(prev => ({
        ...prev!,
        name: '',
        base_url: '',
        client_type: 'openai',
      }))
    }
  }, [])
  
  // 判断当前是否使用模板(非自定义)
  const isUsingTemplate = useMemo(() => {
    return selectedTemplate !== 'custom'
  }, [selectedTemplate])

  // 复制 API Key
  const copyApiKey = useCallback(async () => {
    if (!editingProvider?.api_key) return
    try {
      await navigator.clipboard.writeText(editingProvider.api_key)
      toast({
        title: '复制成功',
        description: 'API Key 已复制到剪贴板',
      })
    } catch {
      toast({
        title: '复制失败',
        description: '无法访问剪贴板',
        variant: 'destructive',
      })
    }
  }, [editingProvider?.api_key, toast])

  // 保存编辑
  const handleSaveEdit = () => {
    if (!editingProvider) return

    // 验证必填项（传入现有提供商列表和当前编辑索引用于重复检查）
    const { isValid, errors } = validateProvider(editingProvider, providers, editingIndex)

    if (!isValid) {
      setFormErrors(errors)
      return
    }

    // 清除错误状态
    setFormErrors({})

    // 填充空值的默认值
    const providerToSave = cleanProviderData(editingProvider)

    if (editingIndex !== null) {
      // 更新现有提供商
      const newProviders = [...providers]
      newProviders[editingIndex] = providerToSave
      setProviders(newProviders)
    } else {
      // 添加新提供商
      setProviders([...providers, providerToSave])
    }

    setEditDialogOpen(false)
    setEditingProvider(null)
    setEditingIndex(null)
  }

  // 处理编辑对话框关闭
  const handleEditDialogClose = (open: boolean) => {
    if (!open && editingProvider) {
      // 关闭时填充默认值
      const updatedProvider = {
        ...editingProvider,
        max_retry: editingProvider.max_retry ?? 2,
        timeout: editingProvider.timeout ?? 30,
        retry_interval: editingProvider.retry_interval ?? 10,
      }
      setEditingProvider(updatedProvider)
    }
    setEditDialogOpen(open)
  }

  // 打开删除确认对话框
  const openDeleteDialog = (index: number) => {
    setDeletingIndex(index)
    setDeleteDialogOpen(true)
  }

  // 确认删除提供商
  const handleConfirmDelete = async () => {
    if (deletingIndex !== null) {
      const newProviders = providers.filter((_, i) => i !== deletingIndex)
      
      // 检查删除影响
      const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'manual')
      
      if (shouldProceed) {
        // 没有影响，直接删除
        setProviders(newProviders)
        toast({
          title: '删除成功',
          description: '提供商已从列表中移除',
        })
      }
      // 如果 shouldProceed = false，对话框会自动打开，等待用户确认
    }
    setDeleteDialogOpen(false)
    setDeletingIndex(null)
  }

  // 切换单个提供商选择
  const toggleProviderSelection = (index: number) => {
    const newSelected = new Set(selectedProviders)
    if (newSelected.has(index)) {
      newSelected.delete(index)
    } else {
      newSelected.add(index)
    }
    setSelectedProviders(newSelected)
  }

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedProviders.size === filteredProviders.length) {
      setSelectedProviders(new Set())
    } else {
      const allIndices = filteredProviders.map((_, idx) => 
        providers.findIndex(p => p === filteredProviders[idx])
      )
      setSelectedProviders(new Set(allIndices))
    }
  }

  // 打开批量删除确认对话框
  const openBatchDeleteDialog = () => {
    if (selectedProviders.size === 0) {
      toast({
        title: '提示',
        description: '请先选择要删除的提供商',
        variant: 'default',
      })
      return
    }
    setBatchDeleteDialogOpen(true)
  }

  // 确认批量删除
  const handleConfirmBatchDelete = async () => {
    const newProviders = providers.filter((_, index) => !selectedProviders.has(index))
    
    // 检查删除影响
    const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'manual')
    
    if (shouldProceed) {
      // 没有影响，直接删除
      setProviders(newProviders)
      setSelectedProviders(new Set())
      toast({
        title: '批量删除成功',
        description: `已删除 ${selectedProviders.size} 个提供商`,
      })
    }
    // 如果 shouldProceed = false，对话框会自动打开，等待用户确认
    
    setBatchDeleteDialogOpen(false)
  }

  // 过滤提供商列表（使用 useMemo 优化性能）
  const filteredProviders = useMemo(() => {
    if (!searchQuery) return providers
    const query = searchQuery.toLowerCase()
    return providers.filter((provider) => (
      provider.name.toLowerCase().includes(query) ||
      provider.base_url.toLowerCase().includes(query) ||
      provider.client_type.toLowerCase().includes(query)
    ))
  }, [providers, searchQuery])

  // 分页逻辑（使用 useMemo 优化性能）
  const { totalPages, paginatedProviders } = useMemo(() => {
    const total = Math.ceil(filteredProviders.length / pageSize)
    const paginated = filteredProviders.slice(
      (page - 1) * pageSize,
      page * pageSize
    )
    return { totalPages: total, paginatedProviders: paginated }
  }, [filteredProviders, page, pageSize])

  // 页码跳转
  const handleJumpToPage = useCallback(() => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    }
  }, [jumpToPage, totalPages])

  // 测试单个提供商连接
  const handleTestConnection = async (providerName: string) => {
    // 标记正在测试
    setTestingProviders(prev => new Set(prev).add(providerName))
    
    try {
      const result = await testProviderConnection(providerName)
      setTestResults(prev => new Map(prev).set(providerName, result))
      
      // 显示结果 toast
      if (result.network_ok) {
        if (result.api_key_valid === true) {
          toast({
            title: '连接正常',
            description: `${providerName} 网络连接正常，API Key 有效 (${result.latency_ms}ms)`,
          })
        } else if (result.api_key_valid === false) {
          toast({
            title: '连接正常但 Key 无效',
            description: `${providerName} 网络连接正常，但 API Key 无效或已过期`,
            variant: 'destructive',
          })
        } else {
          toast({
            title: '网络连接正常',
            description: `${providerName} 可以访问 (${result.latency_ms}ms)`,
          })
        }
      } else {
        toast({
          title: '连接失败',
          description: result.error || '无法连接到提供商',
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
      setTestingProviders(prev => {
        const newSet = new Set(prev)
        newSet.delete(providerName)
        return newSet
      })
    }
  }

  // 批量测试所有提供商
  const handleTestAllConnections = async () => {
    for (const provider of providers) {
      await handleTestConnection(provider.name)
    }
  }

  // 渲染测试状态指示器
  const renderTestStatus = (providerName: string) => {
    const isTesting = testingProviders.has(providerName)
    const result = testResults.get(providerName)
    
    if (isTesting) {
      return (
        <Badge variant="secondary" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          测试中
        </Badge>
      )
    }
    
    if (!result) return null
    
    if (result.network_ok) {
      if (result.api_key_valid === true) {
        return (
          <Badge className="gap-1 bg-green-600 hover:bg-green-700">
            <CheckCircle2 className="h-3 w-3" />
            正常
          </Badge>
        )
      } else if (result.api_key_valid === false) {
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="h-3 w-3" />
            Key无效
          </Badge>
        )
      } else {
        return (
          <Badge className="gap-1 bg-blue-600 hover:bg-blue-700">
            <CheckCircle2 className="h-3 w-3" />
            可访问
          </Badge>
        )
      }
    } else {
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          离线
        </Badge>
      )
    }
  }

  if (loading) {
    return (
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex items-center justify-center h-64">
          <p className="text-muted-foreground">加载中...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">AI模型厂商配置</h1>
          <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">管理 AI 模型厂商的 API 配置</p>
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          {selectedProviders.size > 0 && (
            <Button 
              onClick={openBatchDeleteDialog} 
              size="sm" 
              variant="destructive" 
              className="w-full sm:w-auto"
            >
              <Trash2 className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
              批量删除 ({selectedProviders.size})
            </Button>
          )}
          <Button 
            onClick={handleTestAllConnections} 
            size="sm" 
            variant="outline"
            className="w-full sm:w-auto"
            disabled={providers.length === 0 || testingProviders.size > 0}
          >
            <Zap className="mr-2 h-4 w-4" />
            {testingProviders.size > 0 ? `测试中 (${testingProviders.size})` : '测试全部'}
          </Button>
          <Button onClick={() => openEditDialog(null, null)} size="sm" className="w-full sm:w-auto" data-tour="add-provider-button">
            <Plus className="mr-2 h-4 w-4" strokeWidth={2} fill="none" />
            添加提供商
          </Button>
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
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                disabled={saving || autoSaving || isRestarting}
                size="sm"
                className="w-full sm:w-auto sm:min-w-[120px]"
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

      <ScrollArea className="h-[calc(100vh-260px)]">
        {/* 搜索框 */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2 mb-4">
          <div className="relative w-full sm:flex-1 sm:max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="搜索提供商名称、URL 或类型..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          {searchQuery && (
            <p className="text-sm text-muted-foreground whitespace-nowrap">
              找到 {filteredProviders.length} 个结果
            </p>
          )}
        </div>

        {/* 提供商列表 - 移动端卡片视图 */}
        <div className="md:hidden space-y-3">
          {filteredProviders.length === 0 ? (
            <div className="text-center text-muted-foreground py-8 rounded-lg border bg-card">
              {searchQuery ? '未找到匹配的提供商' : '暂无提供商配置，点击"添加提供商"开始配置'}
            </div>
          ) : (
            paginatedProviders.map((provider, displayIndex) => {
              const actualIndex = providers.findIndex(p => p === provider)
              return (
              <div key={displayIndex} className="rounded-lg border bg-card p-4 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-base truncate">{provider.name}</h3>
                      {renderTestStatus(provider.name)}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 break-all">{provider.base_url}</p>
                  </div>
                  <div className="flex gap-1 flex-shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleTestConnection(provider.name)}
                      disabled={testingProviders.has(provider.name)}
                      title="测试连接"
                    >
                      {testingProviders.has(provider.name) ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Zap className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={() => openEditDialog(provider, actualIndex)}
                    >
                      <Pencil className="h-4 w-4" strokeWidth={2} fill="none" />
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => openDeleteDialog(actualIndex)}
                      className="bg-red-600 hover:bg-red-700 text-white"
                    >
                      <Trash2 className="h-4 w-4" strokeWidth={2} fill="none" />
                    </Button>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-muted-foreground text-xs">客户端类型</span>
                    <p className="font-medium">{provider.client_type}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">最大重试</span>
                    <p className="font-medium">{provider.max_retry}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">超时(秒)</span>
                    <p className="font-medium">{provider.timeout}</p>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">重试间隔(秒)</span>
                    <p className="font-medium">{provider.retry_interval}</p>
                  </div>
                </div>
              </div>
              )
            })
          )}
        </div>

        {/* 提供商列表 - 桌面端表格视图 */}
        <div className="hidden md:block rounded-lg border bg-card overflow-hidden">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12">
                    <Checkbox
                      checked={selectedProviders.size === filteredProviders.length && filteredProviders.length > 0}
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>基础URL</TableHead>
                  <TableHead>客户端类型</TableHead>
                  <TableHead className="text-right">最大重试</TableHead>
                  <TableHead className="text-right">超时(秒)</TableHead>
                  <TableHead className="text-right">重试间隔(秒)</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
            <TableBody>
              {paginatedProviders.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-muted-foreground py-8">
                    {searchQuery ? '未找到匹配的提供商' : '暂无提供商配置，点击"添加提供商"开始配置'}
                  </TableCell>
                </TableRow>
              ) : (
                paginatedProviders.map((provider, displayIndex) => {
                  const actualIndex = providers.findIndex(p => p === provider)
                  return (
                    <TableRow key={displayIndex}>
                      <TableCell>
                        <Checkbox
                          checked={selectedProviders.has(actualIndex)}
                          onCheckedChange={() => toggleProviderSelection(actualIndex)}
                        />
                      </TableCell>
                      <TableCell>
                        {renderTestStatus(provider.name) || (
                          <Badge variant="outline" className="text-muted-foreground">
                            未测试
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="font-medium">{provider.name}</TableCell>
                      <TableCell className="max-w-xs truncate" title={provider.base_url}>
                        {provider.base_url}
                      </TableCell>
                      <TableCell>{provider.client_type}</TableCell>
                      <TableCell className="text-right">{provider.max_retry}</TableCell>
                      <TableCell className="text-right">{provider.timeout}</TableCell>
                      <TableCell className="text-right">{provider.retry_interval}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleTestConnection(provider.name)}
                            disabled={testingProviders.has(provider.name)}
                            title="测试连接"
                          >
                            {testingProviders.has(provider.name) ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Zap className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="default"
                            size="sm"
                            onClick={() => openEditDialog(provider, actualIndex)}
                          >
                            <Pencil className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                            编辑
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => openDeleteDialog(actualIndex)}
                            className="bg-red-600 hover:bg-red-700 text-white"
                          >
                            <Trash2 className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
          </div>
        </div>

        {/* 分页 - 增强版 */}
        {filteredProviders.length > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4">
            <div className="flex items-center gap-2">
              <Label htmlFor="page-size-provider" className="text-sm whitespace-nowrap">每页显示</Label>
              <Select
                value={pageSize.toString()}
                onValueChange={(value) => {
                  setPageSize(parseInt(value))
                  setPage(1)
                  setSelectedProviders(new Set())
                }}
              >
                <SelectTrigger id="page-size-provider" className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10</SelectItem>
                  <SelectItem value="20">20</SelectItem>
                  <SelectItem value="50">50</SelectItem>
                  <SelectItem value="100">100</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-sm text-muted-foreground">
                显示 {(page - 1) * pageSize + 1} 到{' '}
                {Math.min(page * pageSize, filteredProviders.length)} 条，共 {filteredProviders.length} 条
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(1)}
                disabled={page === 1}
                className="hidden sm:flex"
              >
                <ChevronsLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                <ChevronLeft className="h-4 w-4 sm:mr-1" />
                <span className="hidden sm:inline">上一页</span>
              </Button>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={jumpToPage}
                  onChange={(e) => setJumpToPage(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                  placeholder={page.toString()}
                  className="w-16 h-8 text-center"
                  min={1}
                  max={totalPages}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleJumpToPage}
                  disabled={!jumpToPage}
                  className="h-8"
                >
                  跳转
                </Button>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= totalPages}
              >
                <span className="hidden sm:inline">下一页</span>
                <ChevronRight className="h-4 w-4 sm:ml-1" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(totalPages)}
                disabled={page >= totalPages}
                className="hidden sm:flex"
              >
                <ChevronsRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </ScrollArea>

      {/* 编辑对话框 */}
      <Dialog open={editDialogOpen} onOpenChange={handleEditDialogClose}>
        <DialogContent 
          className="max-w-[95vw] sm:max-w-2xl max-h-[90vh] overflow-y-auto" 
          data-tour="provider-dialog"
          preventOutsideClose={tourState.isRunning}
        >
          <DialogHeader>
            <DialogTitle>
              {editingIndex !== null ? '编辑提供商' : '添加提供商'}
            </DialogTitle>
            <DialogDescription>
              配置 API 提供商的连接信息和参数
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={(e) => { e.preventDefault(); handleSaveEdit(); }} autoComplete="off">
          <div className="grid gap-4 py-4">
            <div className="grid gap-2" data-tour="provider-template-select">
              <Label htmlFor="template">提供商模板</Label>
              <Popover open={templateComboboxOpen} onOpenChange={setTemplateComboboxOpen}>
                <PopoverTrigger asChild>
                  <Button
                    variant="outline"
                    role="combobox"
                    aria-expanded={templateComboboxOpen}
                    className="w-full justify-between"
                  >
                    {selectedTemplate
                      ? PROVIDER_TEMPLATES.find((template) => template.id === selectedTemplate)?.display_name
                      : "选择提供商模板..."}
                    <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
                  <Command>
                    <CommandInput placeholder="搜索提供商模板..." />
                    <ScrollArea className="h-[300px]">
                      <CommandList className="max-h-none overflow-visible">
                        <CommandEmpty>未找到匹配的模板</CommandEmpty>
                        <CommandGroup>
                          {PROVIDER_TEMPLATES.map((template) => (
                            <CommandItem
                              key={template.id}
                              value={template.display_name}
                              onSelect={() => handleTemplateChange(template.id)}
                            >
                              <Check
                                className={`mr-2 h-4 w-4 ${
                                  selectedTemplate === template.id ? "opacity-100" : "opacity-0"
                                }`}
                              />
                              {template.display_name}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </ScrollArea>
                  </Command>
                </PopoverContent>
              </Popover>
              <p className="text-xs text-muted-foreground">
                选择预设模板可自动填充 URL 和客户端类型,支持搜索
              </p>
            </div>

            <div className="grid gap-2" data-tour="provider-name-input">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="name" className={formErrors.name ? 'text-destructive' : ''}>名称 *</Label>
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">提供商名称</p>
                      <p>为这个 API 提供商设置一个便于识别的名称，用于在模型配置中引用。</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li>推荐使用厂商官方名称，如 DeepSeek、OpenAI</li>
                        <li>名称需要唯一，不能与现有提供商重复</li>
                      </ul>
                    </div>
                  }
                  side="right"
                  maxWidth="350px"
                />
              </div>
              <Input
                id="name"
                value={editingProvider?.name || ''}
                onChange={(e) => {
                  setEditingProvider((prev) =>
                    prev ? { ...prev, name: e.target.value } : null
                  )
                  if (formErrors.name) {
                    setFormErrors((prev) => ({ ...prev, name: undefined }))
                  }
                }}
                placeholder="例如: DeepSeek, SiliconFlow"
                className={formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}
              />
              {formErrors.name && (
                <p className="text-xs text-destructive">{formErrors.name}</p>
              )}
            </div>

            <div className="grid gap-2" data-tour="provider-url-input">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="base_url" className={formErrors.base_url ? 'text-destructive' : ''}>基础 URL *</Label>
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">API 基础地址</p>
                      <p>提供商的 API 端点基础 URL，通常以 /v1 结尾。</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li><strong>OpenAI 格式：</strong>https://api.openai.com/v1</li>
                        <li><strong>DeepSeek：</strong>https://api.deepseek.com</li>
                        <li><strong>硅基流动：</strong>https://api.siliconflow.cn/v1</li>
                        <li>选择模板会自动填充正确的 URL</li>
                      </ul>
                    </div>
                  }
                  side="right"
                  maxWidth="400px"
                />
              </div>
              <Input
                id="base_url"
                value={editingProvider?.base_url || ''}
                onChange={(e) => {
                  setEditingProvider((prev) =>
                    prev ? { ...prev, base_url: e.target.value } : null
                  )
                  if (formErrors.base_url) {
                    setFormErrors((prev) => ({ ...prev, base_url: undefined }))
                  }
                }}
                placeholder="https://api.example.com/v1"
                disabled={isUsingTemplate}
                className={`${isUsingTemplate ? 'bg-muted cursor-not-allowed' : ''} ${formErrors.base_url ? 'border-destructive focus-visible:ring-destructive' : ''}`}
              />
              {formErrors.base_url && (
                <p className="text-xs text-destructive">{formErrors.base_url}</p>
              )}
              {isUsingTemplate && !formErrors.base_url && (
                <p className="text-xs text-muted-foreground">
                  使用模板时 URL 不可编辑,切换到"自定义"以手动配置
                </p>
              )}
            </div>

            <div className="grid gap-2" data-tour="provider-apikey-input">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="api_key" className={formErrors.api_key ? 'text-destructive' : ''}>API Key *</Label>
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">API 密钥</p>
                      <p>从提供商平台获取的身份验证密钥。</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li>通常以 <code>sk-</code> 开头</li>
                        <li>请妥善保管，不要泄露给他人</li>
                        <li>可以点击眼睛图标切换显示/隐藏</li>
                        <li>点击复制图标可快速复制密钥</li>
                      </ul>
                    </div>
                  }
                  side="right"
                  maxWidth="350px"
                />
              </div>
              <div className="flex gap-2">
                <Input
                  id="api_key"
                  type={showApiKey ? 'text' : 'password'}
                  value={editingProvider?.api_key || ''}
                  onChange={(e) => {
                    setEditingProvider((prev) =>
                      prev ? { ...prev, api_key: e.target.value } : null
                    )
                    if (formErrors.api_key) {
                      setFormErrors((prev) => ({ ...prev, api_key: undefined }))
                    }
                  }}
                  placeholder="sk-..."
                  className={`flex-1 ${formErrors.api_key ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setShowApiKey(!showApiKey)}
                  title={showApiKey ? '隐藏密钥' : '显示密钥'}
                >
                  {showApiKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={copyApiKey}
                  title="复制密钥"
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              {formErrors.api_key && (
                <p className="text-xs text-destructive">{formErrors.api_key}</p>
              )}
            </div>

            <div className="grid gap-2">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="client_type">客户端类型</Label>
                <HelpTooltip
                  content={
                    <div className="space-y-2">
                      <p className="font-medium">API 客户端类型</p>
                      <p>指定与提供商通信时使用的 API 协议格式。</p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li><strong>OpenAI：</strong>兼容 OpenAI API 格式的提供商</li>
                        <li><strong>Gemini：</strong>Google Gemini 专用格式</li>
                        <li>大部分第三方提供商都兼容 OpenAI 格式</li>
                      </ul>
                    </div>
                  }
                  side="right"
                  maxWidth="350px"
                />
              </div>
              <Select
                value={editingProvider?.client_type || 'openai'}
                onValueChange={(value) =>
                  setEditingProvider((prev) =>
                    prev ? { ...prev, client_type: value } : null
                  )
                }
                disabled={isUsingTemplate}
              >
                <SelectTrigger id="client_type" className={isUsingTemplate ? 'bg-muted cursor-not-allowed' : ''}>
                  <SelectValue placeholder="选择客户端类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="openai">OpenAI</SelectItem>
                  <SelectItem value="gemini">Gemini</SelectItem>
                </SelectContent>
              </Select>
              {isUsingTemplate && (
                <p className="text-xs text-muted-foreground">
                  使用模板时客户端类型不可编辑,切换到"自定义"以手动配置
                </p>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="grid gap-2">
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="max_retry">最大重试</Label>
                  <HelpTooltip
                    content="API 请求失败时的最大重试次数。设置为 0 表示不重试。默认值：2"
                    side="top"
                    maxWidth="250px"
                  />
                </div>
                <Input
                  id="max_retry"
                  type="number"
                  min="0"
                  value={editingProvider?.max_retry ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setEditingProvider((prev) =>
                      prev ? { ...prev, max_retry: val } : null
                    )
                  }}
                  placeholder="默认: 2"
                />
              </div>

              <div className="grid gap-2">
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="timeout">超时(秒)</Label>
                  <HelpTooltip
                    content="单次 API 请求的超时时间（秒）。超时后会触发重试或报错。默认值：30 秒"
                    side="top"
                    maxWidth="250px"
                  />
                </div>
                <Input
                  id="timeout"
                  type="number"
                  min="1"
                  value={editingProvider?.timeout ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setEditingProvider((prev) =>
                      prev ? { ...prev, timeout: val } : null
                    )
                  }}
                  placeholder="默认: 30"
                />
              </div>

              <div className="grid gap-2">
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="retry_interval">重试间隔(秒)</Label>
                  <HelpTooltip
                    content="两次重试之间的等待时间（秒）。适当的间隔可以避免触发 API 限流。默认值：10 秒"
                    side="top"
                    maxWidth="250px"
                  />
                </div>
                <Input
                  id="retry_interval"
                  type="number"
                  min="1"
                  value={editingProvider?.retry_interval ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setEditingProvider((prev) =>
                      prev
                        ? { ...prev, retry_interval: val }
                        : null
                    )
                  }}
                  placeholder="默认: 10"
                />
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)} data-tour="provider-cancel-button">
              取消
            </Button>
            <Button type="submit" data-tour="provider-save-button">保存</Button>
          </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 删除确认对话框 */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除提供商 "{deletingIndex !== null ? providers[deletingIndex]?.name : ''}" 吗？
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
              确定要删除选中的 {selectedProviders.size} 个提供商吗？
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

      {/* 删除提供商影响确认对话框 */}
      <AlertDialog open={deleteConfirmState.isOpen} onOpenChange={(open) => setDeleteConfirmState(prev => ({ ...prev, isOpen: open }))}>
        <AlertDialogContent className="max-w-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除提供商</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3">
                <p>
                  您即将删除以下提供商：
                  <strong className="text-foreground ml-1">
                    {deleteConfirmState.providersToDelete.join(', ')}
                  </strong>
                </p>
                <p className="text-yellow-600 dark:text-yellow-500 font-medium">
                  ⚠️ 此操作将同时删除 {deleteConfirmState.affectedModels.length} 个关联的模型：
                </p>
                <ScrollArea className="h-32 w-full rounded border p-3">
                  <div className="space-y-1">
                    {deleteConfirmState.affectedModels.map((model: any, idx: number) => (
                      <div key={idx} className="text-sm">
                        <span className="font-mono text-muted-foreground">•</span>
                        <span className="ml-2 font-medium">{model.name}</span>
                        <span className="ml-2 text-xs text-muted-foreground">
                          ({model.model_identifier})
                        </span>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
                <p className="text-sm text-muted-foreground">
                  这些模型将从模型列表和所有任务分配中移除。此操作无法撤销。
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={handleCancelDeleteProvider}>取消</AlertDialogCancel>
            <AlertDialogAction 
              onClick={handleConfirmDeleteProvider}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 重启遮罩层 */}
      <RestartOverlay />
    </div>
  )
}
