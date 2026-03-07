import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { getModelConfig, testProviderConnection, updateModelConfig, updateModelConfigSection } from '@/lib/config-api'
import type { TestConnectionResult } from '@/lib/config-api'
import { Info, Plus, Power, Save, Trash2, Zap } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { MODEL_ASSIGNMENT_TOUR_ID, modelAssignmentTourSteps, STEP_ROUTE_MAP } from '@/components/tour/tours/model-assignment-tour'
import { useTour } from '@/components/tour'
import { useToast } from '@/hooks/use-toast'
import { RestartOverlay } from '@/components/restart-overlay'
import { RestartProvider, useRestart } from '@/lib/restart-context'

import { ProviderForm } from './ProviderForm'
import { ProviderList } from './ProviderList'
import type { APIProvider, DeleteConfirmState } from './types'
import { cleanProviderData } from './utils'

/**
 * ModelConfig 接口定义
 */
interface ModelConfig extends Record<string, unknown> {
  api_providers?: unknown[]
  models?: unknown[]
  model_task_config?: Record<string, unknown>
}

export function ModelProviderConfigPage() {
  return (
    <RestartProvider>
      <ModelProviderConfigPageContent />
    </RestartProvider>
  )
}

function ModelProviderConfigPageContent() {
  const [providers, setProviders] = useState<APIProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<APIProvider | null>(null)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [selectedProviders, setSelectedProviders] = useState<Set<number>>(new Set())
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [deleteConfirmState, setDeleteConfirmState] = useState<DeleteConfirmState>({
    isOpen: false,
    providersToDelete: [],
    affectedModels: [],
    pendingProviders: [],
    context: 'auto',
    oldProviders: [],
  })
  const [testingProviders, setTestingProviders] = useState<Set<string>>(new Set())
  const [testResults, setTestResults] = useState<Map<string, TestConnectionResult>>(new Map())

  const { toast } = useToast()
  const navigate = useNavigate()
  const { state: tourState, goToStep, registerTour } = useTour()
  const { triggerRestart, isRestarting } = useRestart()

  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialLoadRef = useRef(true)
  const prevTourStepRef = useRef(tourState.stepIndex)

  // 注册 Tour
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
  useEffect(() => {
    if (tourState.activeTourId === MODEL_ASSIGNMENT_TOUR_ID && tourState.isRunning) {
      const prevStep = prevTourStepRef.current
      const currentStep = tourState.stepIndex

      if (prevStep >= 3 && prevStep <= 9 && currentStep < 3) {
        setEditDialogOpen(false)
      }

      if (prevStep >= 10 && currentStep >= 3 && currentStep <= 9) {
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

      if (currentStep === 2 && target.closest('[data-tour="add-provider-button"]')) {
        setTimeout(() => goToStep(3), 300)
      } else if (currentStep === 9 && target.closest('[data-tour="provider-cancel-button"]')) {
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
      const result = await getModelConfig()
      if (!result.success) {
        toast({
          title: '加载失败',
          description: result.error,
          variant: 'destructive',
        })
        setLoading(false)
        return
      }
      const config = result.data as ModelConfig
      setProviders(Array.isArray(config.api_providers) ? config.api_providers as APIProvider[] : [])
      setHasUnsavedChanges(false)
      initialLoadRef.current = false
    } catch (error) {
      console.error('加载配置失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleRestart = async () => {
    await triggerRestart()
  }

  const handleSaveAndRestart = async () => {
    try {
      setSaving(true)
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }

      const cleanedProviders = providers.map(provider => ({
        ...provider,
        max_retry: provider.max_retry ?? 2,
        timeout: provider.timeout ?? 30,
        retry_interval: provider.retry_interval ?? 10,
      }))

      const { shouldProceed } = await checkDeleteProviderImpact(cleanedProviders, 'restart')
      if (!shouldProceed) {
        setSaving(false)
        return
      }

      const resultGet = await getModelConfig()
      if (!resultGet.success) {
        toast({
          title: '保存失败',
          description: resultGet.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
      const config = resultGet.data as ModelConfig

      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      const originalModels = Array.isArray(config.models) ? config.models : []
      const filteredModels = originalModels.filter((model: unknown) => {
        return typeof model === 'object' && model !== null && 'api_provider' in model && validProviderNames.has((model as Record<string, unknown>).api_provider as string)
      })

      config.api_providers = cleanedProviders
      config.models = filteredModels

      const resultUpdate = await updateModelConfig(config)
      if (!resultUpdate.success) {
        toast({
          title: '保存失败',
          description: resultUpdate.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
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

  const checkDeleteProviderImpact = useCallback(async (
    newProviders: APIProvider[],
    context: 'auto' | 'manual' | 'restart' = 'auto'
  ) => {
    try {
      const result = await getModelConfig()
      if (!result.success) {
        console.error('加载配置失败:', result.error)
        return { shouldProceed: true, providers: newProviders }
      }
      const config = result.data
      const oldProviderNames = new Set(providers.map(p => p.name))
      const newProviderNames = new Set(newProviders.map(p => p.name))

      const deletedProviders = Array.from(oldProviderNames).filter(
        name => !newProviderNames.has(name)
      )

      if (deletedProviders.length === 0) {
        return { shouldProceed: true, providers: newProviders }
      }

      const models = Array.isArray(config.models) ? config.models : []
      const affected = models.filter((m: unknown) =>
        typeof m === 'object' && m !== null && 'api_provider' in m && deletedProviders.includes((m as Record<string, unknown>).api_provider as string)
      )

      if (affected.length === 0) {
        return { shouldProceed: true, providers: newProviders }
      }

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

  const handleConfirmDeleteProvider = async () => {
    try {
      const savingFlag = deleteConfirmState.context === 'auto' ? setAutoSaving : setSaving
      savingFlag(true)

      setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))

      const resultGet = await getModelConfig()
      if (!resultGet.success) {
        toast({
          title: '加载失败',
          description: resultGet.error,
          variant: 'destructive',
        })
        savingFlag(false)
        return
      }
      const config = resultGet.data as ModelConfig

      const cleanedProviders = deleteConfirmState.pendingProviders.map(cleanProviderData)
      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      const originalModels = Array.isArray(config.models) ? config.models : []
      const filteredModels = originalModels.filter((model: unknown) => {
        return typeof model === 'object' && model !== null && 'api_provider' in model && validProviderNames.has((model as Record<string, unknown>).api_provider as string)
      })

      const deletedModelNames = new Set(
        deleteConfirmState.affectedModels.map((m: unknown) => typeof m === 'object' && m !== null && 'name' in m ? (m as Record<string, unknown>).name as string : '')
      )

      const modelTaskConfig = config.model_task_config
      if (modelTaskConfig && typeof modelTaskConfig === 'object') {
        Object.keys(modelTaskConfig).forEach(taskName => {
          const task = (modelTaskConfig as Record<string, unknown>)[taskName]
          if (task && typeof task === 'object' && 'model_list' in task) {
            const taskObj = task as Record<string, unknown>
            if (Array.isArray(taskObj.model_list)) {
              taskObj.model_list = taskObj.model_list.filter(
                (modelName: unknown) => typeof modelName === 'string' && !deletedModelNames.has(modelName)
              )
            }
          }
        })
      }

      config.api_providers = cleanedProviders
      config.models = filteredModels
      config.model_task_config = modelTaskConfig

      const resultUpdate = await updateModelConfig(config)
      if (!resultUpdate.success) {
        toast({
          title: '保存失败',
          description: resultUpdate.error,
          variant: 'destructive',
        })
        savingFlag(false)
        return
      }

      setProviders(deleteConfirmState.pendingProviders)
      setHasUnsavedChanges(false)

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

      if (deleteConfirmState.context === 'restart') {
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

  const handleCancelDeleteProvider = () => {
    if (deleteConfirmState.oldProviders.length > 0) {
      setProviders(deleteConfirmState.oldProviders)
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
  }

  const autoSaveProviders = useCallback(async (newProviders: APIProvider[]) => {
    if (initialLoadRef.current) return

    const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'auto')

    if (!shouldProceed) {
      setHasUnsavedChanges(true)
      return
    }

    try {
      setAutoSaving(true)
      const cleanedProviders = newProviders.map(cleanProviderData)
      const result = await updateModelConfigSection('api_providers', cleanedProviders)
      if (!result.success) {
        console.error('自动保存失败:', result.error)
        toast({
          title: '自动保存失败',
          description: result.error,
          variant: 'destructive',
        })
        setHasUnsavedChanges(true)
        return
      }
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

  useEffect(() => {
    if (initialLoadRef.current) return

    setHasUnsavedChanges(true)

    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
    }

    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveProviders(providers)
    }, 2000)

    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
    }
  }, [providers, autoSaveProviders])

  const saveConfig = async () => {
    try {
      setSaving(true)

      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }

      const cleanedProviders = providers.map(cleanProviderData)

      const { shouldProceed } = await checkDeleteProviderImpact(cleanedProviders, 'manual')
      if (!shouldProceed) {
        setSaving(false)
        return
      }

      const resultGet = await getModelConfig()
      if (!resultGet.success) {
        toast({
          title: '保存失败',
          description: resultGet.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
      const config = resultGet.data as ModelConfig

      const validProviderNames = new Set(cleanedProviders.map(p => p.name))
      const originalModels = Array.isArray(config.models) ? config.models : []
      const filteredModels = originalModels.filter((model: unknown) => {
        if (typeof model !== 'object' || model === null || !('api_provider' in model)) return false
        const modelObj = model as Record<string, unknown>
        const isValid = validProviderNames.has(modelObj.api_provider as string)
        if (!isValid) {
          console.warn(`模型 "${modelObj.name}" 引用了已删除的提供商 "${modelObj.api_provider}"、将被移除`)
        }
        return isValid
      })

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

      const resultUpdate = await updateModelConfig(config)
      if (!resultUpdate.success) {
        toast({
          title: '保存失败',
          description: resultUpdate.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
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

  const openEditDialog = (provider: APIProvider | null, index: number | null) => {
    if (provider) {
      setEditingProvider(provider)
    } else {
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
    setEditDialogOpen(true)
  }

  const handleSaveEdit = (provider: APIProvider, index: number | null) => {
    const providerToSave = cleanProviderData(provider)

    if (index !== null) {
      const newProviders = [...providers]
      newProviders[index] = providerToSave
      setProviders(newProviders)
    } else {
      setProviders([...providers, providerToSave])
    }

    setEditDialogOpen(false)
    setEditingProvider(null)
    setEditingIndex(null)
  }

  const openDeleteDialog = (index: number) => {
    setDeletingIndex(index)
    setDeleteDialogOpen(true)
  }

  const handleConfirmDelete = async () => {
    if (deletingIndex !== null) {
      const newProviders = providers.filter((_, i) => i !== deletingIndex)

      const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'manual')

      if (shouldProceed) {
        setProviders(newProviders)
        toast({
          title: '删除成功',
          description: '提供商已从列表中移除',
        })
      }
    }
    setDeleteDialogOpen(false)
    setDeletingIndex(null)
  }

  const toggleProviderSelection = (index: number) => {
    const newSelected = new Set(selectedProviders)
    if (newSelected.has(index)) {
      newSelected.delete(index)
    } else {
      newSelected.add(index)
    }
    setSelectedProviders(newSelected)
  }

  const toggleSelectAll = () => {
    if (selectedProviders.size === providers.length) {
      setSelectedProviders(new Set())
    } else {
      const allIndices = providers.map((_, idx) => idx)
      setSelectedProviders(new Set(allIndices))
    }
  }

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

  const handleConfirmBatchDelete = async () => {
    const newProviders = providers.filter((_, index) => !selectedProviders.has(index))

    const { shouldProceed } = await checkDeleteProviderImpact(newProviders, 'manual')

    if (shouldProceed) {
      setProviders(newProviders)
      setSelectedProviders(new Set())
      toast({
        title: '批量删除成功',
        description: `已删除 ${selectedProviders.size} 个提供商`,
      })
    }

    setBatchDeleteDialogOpen(false)
  }

  const handleTestConnection = async (providerName: string) => {
    setTestingProviders(prev => new Set(prev).add(providerName))

    try {
      const result = await testProviderConnection(providerName)
      if (!result.success) {
        toast({
          title: '测试失败',
          description: result.error,
          variant: 'destructive',
        })
        return
      }
      const testResult = result.data
      setTestResults(prev => new Map(prev).set(providerName, testResult))

      if (testResult.network_ok) {
        if (testResult.api_key_valid === true) {
          toast({
            title: '连接正常',
            description: `${providerName} 网络连接正常、API Key 有效 (${testResult.latency_ms}ms)`,
          })
        } else if (testResult.api_key_valid === false) {
          toast({
            title: '连接正常但 Key 无效',
            description: `${providerName} 网络连接正常、但 API Key 无效或已过期`,
            variant: 'destructive',
          })
        } else {
          toast({
            title: '网络连接正常',
            description: `${providerName} 可以访问 (${testResult.latency_ms}ms)`,
          })
        }
      } else {
        toast({
          title: '连接失败',
          description: testResult.error || '无法连接到提供商',
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

  const handleTestAllConnections = async () => {
    for (const provider of providers) {
      await handleTestConnection(provider.name)
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
        <ProviderList
          providers={providers}
          testingProviders={testingProviders}
          testResults={testResults}
          selectedProviders={selectedProviders}
          onEdit={openEditDialog}
          onDelete={openDeleteDialog}
          onTest={handleTestConnection}
          onToggleSelect={toggleProviderSelection}
          onToggleSelectAll={toggleSelectAll}
        />
      </ScrollArea>

      <ProviderForm
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        editingProvider={editingProvider}
        editingIndex={editingIndex}
        providers={providers}
        onSave={handleSaveEdit}
        tourState={tourState}
      />

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
