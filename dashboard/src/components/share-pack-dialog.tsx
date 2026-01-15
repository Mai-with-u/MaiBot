/**
 * 分享 Pack 对话框
 * 
 * 允许用户将当前配置导出并分享到 Pack 市场
 */

import { useState, useEffect } from 'react'
import {
  Package,
  Share2,
  Server,
  Layers,
  ListChecks,
  Tag,
  Loader2,
  Check,
  Info,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { toast } from '@/hooks/use-toast'
import {
  createPack,
  exportCurrentConfigAsPack,
  type PackProvider,
  type PackModel,
  type PackTaskConfigs,
} from '@/lib/pack-api'

// 任务类型名称映射
const TASK_TYPE_NAMES: Record<string, string> = {
  utils: '通用工具',
  utils_small: '轻量工具',
  tool_use: '工具调用',
  replyer: '回复生成',
  planner: '规划推理',
  vlm: '视觉模型',
  voice: '语音处理',
  embedding: '向量嵌入',
  lpmm_entity_extract: '实体提取',
  lpmm_rdf_build: 'RDF构建',
  lpmm_qa: '问答模型',
}

// 预设标签
const PRESET_TAGS = [
  '官方推荐',
  '性价比',
  '高性能',
  '免费模型',
  '国内可用',
  '海外模型',
  'OpenAI',
  'Claude',
  'Gemini',
  '国产模型',
  '多模态',
  '轻量级',
]

interface SharePackDialogProps {
  trigger?: React.ReactNode
}

export function SharePackDialog({ trigger }: SharePackDialogProps) {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(1)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  
  // 配置数据
  const [providers, setProviders] = useState<PackProvider[]>([])
  const [models, setModels] = useState<PackModel[]>([])
  const [taskConfig, setTaskConfig] = useState<PackTaskConfigs>({})
  
  // 选择状态
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set())
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set())
  const [selectedTasks, setSelectedTasks] = useState<Set<string>>(new Set())
  
  // Pack 信息
  const [packName, setPackName] = useState('')
  const [packDescription, setPackDescription] = useState('')
  const [packAuthor, setPackAuthor] = useState('')
  const [packTags, setPackTags] = useState<string[]>([])
  
  // 加载当前配置
  useEffect(() => {
    if (open && step === 1) {
      loadCurrentConfig()
    }
  }, [open, step])
  
  const loadCurrentConfig = async () => {
    setLoading(true)
    try {
      const config = await exportCurrentConfigAsPack({
        name: '',
        description: '',
        author: '',
      })
      
      setProviders(config.providers)
      setModels(config.models)
      setTaskConfig(config.task_config)
      
      // 默认全选
      setSelectedProviders(new Set(config.providers.map(p => p.name)))
      setSelectedModels(new Set(config.models.map(m => m.name)))
      setSelectedTasks(new Set(Object.keys(config.task_config)))
    } catch (error) {
      console.error('加载配置失败:', error)
      toast({ title: '加载当前配置失败', variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }
  
  // 切换选择
  const toggleProvider = (name: string) => {
    const newSet = new Set(selectedProviders)
    const newModels = new Set(selectedModels)
    const newTasks = new Set(selectedTasks)
    
    if (newSet.has(name)) {
      // 取消选择提供商
      newSet.delete(name)
      
      // 取消选择该提供商下的所有模型
      const providerModels = models.filter(m => m.api_provider === name)
      providerModels.forEach(m => newModels.delete(m.name))
      
      // 检查任务配置，如果任务使用的所有模型都被取消选择了，也取消选择该任务
      Object.entries(taskConfig).forEach(([key, config]) => {
        if (config.model_list) {
          const hasSelectedModel = config.model_list.some((modelName: string) => newModels.has(modelName))
          if (!hasSelectedModel) {
            newTasks.delete(key)
          }
        }
      })
    } else {
      // 选择提供商
      newSet.add(name)
      
      // 自动选择该提供商下的所有模型
      const providerModels = models.filter(m => m.api_provider === name)
      providerModels.forEach(m => newModels.add(m.name))
      
      // 自动选择使用这些模型的任务
      Object.entries(taskConfig).forEach(([key, config]) => {
        if (config.model_list) {
          const hasProviderModel = config.model_list.some((modelName: string) => {
            const model = models.find(m => m.name === modelName)
            return model && model.api_provider === name
          })
          if (hasProviderModel) {
            newTasks.add(key)
          }
        }
      })
    }
    
    setSelectedProviders(newSet)
    setSelectedModels(newModels)
    setSelectedTasks(newTasks)
  }
  
  const toggleModel = (name: string) => {
    const newModels = new Set(selectedModels)
    const newTasks = new Set(selectedTasks)
    
    if (newModels.has(name)) {
      // 取消选择模型
      newModels.delete(name)
      
      // 检查任务配置，如果任务使用的所有模型都被取消选择了，也取消选择该任务
      Object.entries(taskConfig).forEach(([key, config]) => {
        if (config.model_list) {
          const hasSelectedModel = config.model_list.some((modelName: string) => newModels.has(modelName))
          if (!hasSelectedModel) {
            newTasks.delete(key)
          }
        }
      })
    } else {
      // 选择模型
      newModels.add(name)
      
      // 自动选择使用这个模型的任务
      Object.entries(taskConfig).forEach(([key, config]) => {
        if (config.model_list && config.model_list.includes(name)) {
          newTasks.add(key)
        }
      })
    }
    
    setSelectedModels(newModels)
    setSelectedTasks(newTasks)
  }
  
  const toggleTask = (key: string) => {
    const newSet = new Set(selectedTasks)
    if (newSet.has(key)) {
      newSet.delete(key)
    } else {
      newSet.add(key)
    }
    setSelectedTasks(newSet)
  }
  
  const toggleTag = (tag: string) => {
    if (packTags.includes(tag)) {
      setPackTags(packTags.filter(t => t !== tag))
    } else if (packTags.length < 5) {
      setPackTags([...packTags, tag])
    } else {
      toast({ title: '最多选择 5 个标签', variant: 'destructive' })
    }
  }
  
  // 全选/取消全选
  const selectAllProviders = () => {
    if (selectedProviders.size === providers.length) {
      setSelectedProviders(new Set())
    } else {
      setSelectedProviders(new Set(providers.map(p => p.name)))
    }
  }
  
  const selectAllModels = () => {
    if (selectedModels.size === models.length) {
      setSelectedModels(new Set())
    } else {
      setSelectedModels(new Set(models.map(m => m.name)))
    }
  }
  
  const selectAllTasks = () => {
    const taskKeys = Object.keys(taskConfig)
    if (selectedTasks.size === taskKeys.length) {
      setSelectedTasks(new Set())
    } else {
      setSelectedTasks(new Set(taskKeys))
    }
  }
  
  // 提交
  const handleSubmit = async () => {
    // 验证
    if (!packName.trim()) {
      toast({ title: '请输入模板名称', variant: 'destructive' })
      return
    }
    if (!packDescription.trim()) {
      toast({ title: '请输入模板描述', variant: 'destructive' })
      return
    }
    if (!packAuthor.trim()) {
      toast({ title: '请输入作者名称', variant: 'destructive' })
      return
    }
    if (selectedProviders.size === 0 && selectedModels.size === 0 && selectedTasks.size === 0) {
      toast({ title: '请至少选择一项配置', variant: 'destructive' })
      return
    }
    
    setSubmitting(true)
    try {
      // 过滤选中的配置
      const selectedProviderConfigs = providers.filter(p => selectedProviders.has(p.name))
      const selectedModelConfigs = models.filter(m => selectedModels.has(m.name))
      const selectedTaskConfigs: PackTaskConfigs = {}
      for (const [key, config] of Object.entries(taskConfig)) {
        if (selectedTasks.has(key)) {
          selectedTaskConfigs[key as keyof PackTaskConfigs] = config
        }
      }
      
      await createPack({
        name: packName.trim(),
        description: packDescription.trim(),
        author: packAuthor.trim(),
        tags: packTags,
        providers: selectedProviderConfigs,
        models: selectedModelConfigs,
        task_config: selectedTaskConfigs,
      })
      
      toast({ title: '模板已提交审核，审核通过后将显示在市场中' })
      setOpen(false)
      resetForm()
    } catch (error) {
      console.error('提交失败:', error)
      toast({ title: error instanceof Error ? error.message : '提交失败', variant: 'destructive' })
    } finally {
      setSubmitting(false)
    }
  }
  
  // 重置表单
  const resetForm = () => {
    setStep(1)
    setPackName('')
    setPackDescription('')
    setPackAuthor('')
    setPackTags([])
    setSelectedProviders(new Set())
    setSelectedModels(new Set())
    setSelectedTasks(new Set())
  }
  
  const totalSteps = 2
  
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger || (
          <Button variant="outline">
            <Share2 className="w-4 h-4 mr-2" />
            分享配置
          </Button>
        )}
      </DialogTrigger>
      
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Package className="w-5 h-5" />
            分享配置模板
          </DialogTitle>
          <DialogDescription>
            步骤 {step} / {totalSteps}：
            {step === 1 && '选择要分享的配置'}
            {step === 2 && '填写模板信息'}
          </DialogDescription>
        </DialogHeader>
        
        <ScrollArea className="h-[calc(85vh-220px)] pr-4">
          {loading ? (
            <div className="py-8 text-center">
              <Loader2 className="w-8 h-8 mx-auto animate-spin text-primary" />
              <p className="mt-4 text-muted-foreground">正在加载当前配置...</p>
            </div>
          ) : (
            <>
              {/* 步骤 1: 选择配置 */}
              {step === 1 && (
                <div className="space-y-4">
                  <Alert>
                    <Info className="h-4 w-4" />
                    <AlertTitle>安全提示</AlertTitle>
                    <AlertDescription>
                      分享的配置将<strong>不包含</strong> API Key，其他用户需要自行配置。
                    </AlertDescription>
                  </Alert>
                  
                  <Tabs defaultValue="providers" className="w-full">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="providers">
                        <Server className="w-4 h-4 mr-2" />
                        API 提供商
                        <Badge variant="secondary" className="ml-2">
                          {selectedProviders.size}/{providers.length}
                        </Badge>
                      </TabsTrigger>
                      <TabsTrigger value="models">
                        <Layers className="w-4 h-4 mr-2" />
                        模型配置
                        <Badge variant="secondary" className="ml-2">
                          {selectedModels.size}/{models.length}
                        </Badge>
                      </TabsTrigger>
                      <TabsTrigger value="tasks">
                        <ListChecks className="w-4 h-4 mr-2" />
                        任务配置
                        <Badge variant="secondary" className="ml-2">
                          {selectedTasks.size}/{Object.keys(taskConfig).length}
                        </Badge>
                      </TabsTrigger>
                    </TabsList>
                    
                    {/* 提供商选择 */}
                    <TabsContent value="providers" className="space-y-2 mt-4">
                        <div className="space-y-2">
                          <div className="flex justify-end">
                            <Button variant="ghost" size="sm" onClick={selectAllProviders}>
                              {selectedProviders.size === providers.length ? '取消全选' : '全选'}
                            </Button>
                          </div>
                          {providers.length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-2">
                              暂无提供商配置
                            </p>
                          ) : (
                            providers.map(provider => (
                              <div
                                key={provider.name}
                                className="flex items-center space-x-2 p-2 rounded hover:bg-muted"
                              >
                                <Checkbox
                                  id={`provider-${provider.name}`}
                                  checked={selectedProviders.has(provider.name)}
                                  onCheckedChange={() => toggleProvider(provider.name)}
                                />
                                <Label
                                  htmlFor={`provider-${provider.name}`}
                                  className="flex-1 cursor-pointer"
                                >
                                  <span className="font-medium">{provider.name}</span>
                                  <span className="text-xs text-muted-foreground ml-2">
                                    {provider.base_url}
                                  </span>
                                </Label>
                                <Badge variant="outline" className="text-xs">
                                  {provider.client_type}
                                </Badge>
                              </div>
                            ))
                          )}
                        </div>
                    </TabsContent>
                    
                    {/* 模型选择 */}
                    <TabsContent value="models" className="space-y-2 mt-4">
                        <div className="space-y-2">
                          <div className="flex justify-end">
                            <Button variant="ghost" size="sm" onClick={selectAllModels}>
                              {selectedModels.size === models.length ? '取消全选' : '全选'}
                            </Button>
                          </div>
                          {models.length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-2">
                              暂无模型配置
                            </p>
                          ) : (
                            models.map(model => (
                              <div
                                key={model.name}
                                className="flex items-center space-x-2 p-2 rounded hover:bg-muted"
                              >
                                <Checkbox
                                  id={`model-${model.name}`}
                                  checked={selectedModels.has(model.name)}
                                  onCheckedChange={() => toggleModel(model.name)}
                                />
                                <Label
                                  htmlFor={`model-${model.name}`}
                                  className="flex-1 cursor-pointer"
                                >
                                  <span className="font-medium">{model.name}</span>
                                  <span className="text-xs text-muted-foreground ml-2">
                                    {model.model_identifier}
                                  </span>
                                </Label>
                                <span className="text-xs text-muted-foreground">
                                  {model.api_provider}
                                </span>
                              </div>
                            ))
                          )}
                        </div>
                    </TabsContent>
                    
                    {/* 任务配置选择 */}
                    <TabsContent value="tasks" className="space-y-2 mt-4">
                        <div className="space-y-2">
                          <div className="flex justify-end">
                            <Button variant="ghost" size="sm" onClick={selectAllTasks}>
                              {selectedTasks.size === Object.keys(taskConfig).length ? '取消全选' : '全选'}
                            </Button>
                          </div>
                          {Object.keys(taskConfig).length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-2">
                              暂无任务配置
                            </p>
                          ) : (
                            Object.entries(taskConfig).map(([key, config]) => (
                              <div
                                key={key}
                                className="space-y-2 p-2 rounded hover:bg-muted"
                              >
                                <div className="flex items-center space-x-2">
                                  <Checkbox
                                    id={`task-${key}`}
                                    checked={selectedTasks.has(key)}
                                    onCheckedChange={() => toggleTask(key)}
                                  />
                                  <Label
                                    htmlFor={`task-${key}`}
                                    className="flex-1 cursor-pointer"
                                  >
                                    <span className="font-medium">
                                      {TASK_TYPE_NAMES[key] || key}
                                    </span>
                                  </Label>
                                  <Badge variant="outline" className="text-xs">
                                    {config.model_list.length} 个模型
                                  </Badge>
                                </div>
                                {config.model_list && config.model_list.length > 0 && (
                                  <div className="ml-6 flex flex-wrap gap-1">
                                    {config.model_list.map((modelName: string) => {
                                      const model = models.find(m => m.name === modelName)
                                      const isSelected = selectedModels.has(modelName)
                                      return (
                                        <Badge 
                                          key={modelName} 
                                          variant={isSelected ? "default" : "outline"}
                                          className="text-xs cursor-pointer hover:opacity-80 transition-opacity"
                                          onClick={() => toggleModel(modelName)}
                                        >
                                          {modelName}
                                          {model && (
                                            <span className="ml-1 opacity-70">
                                              ({model.api_provider})
                                            </span>
                                          )}
                                        </Badge>
                                      )
                                    })}
                                  </div>
                                )}
                              </div>
                            ))
                          )}
                        </div>
                    </TabsContent>
                  </Tabs>
                </div>
              )}
              
              {/* 步骤 2: 填写信息 */}
              {step === 2 && (
                <div className="space-y-4">
                  {/* 选择摘要 */}
                  <div className="flex gap-4 text-sm p-3 bg-muted rounded-lg">
                    <span className="flex items-center gap-1">
                      <Server className="w-4 h-4" />
                      {selectedProviders.size} 个提供商
                    </span>
                    <span className="flex items-center gap-1">
                      <Layers className="w-4 h-4" />
                      {selectedModels.size} 个模型
                    </span>
                    <span className="flex items-center gap-1">
                      <ListChecks className="w-4 h-4" />
                      {selectedTasks.size} 个任务
                    </span>
                  </div>
                  
                  <Separator />
                  
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="pack-name">模板名称 *</Label>
                      <Input
                        id="pack-name"
                        placeholder="例如：高性价比国产模型配置"
                        value={packName}
                        onChange={e => setPackName(e.target.value)}
                        maxLength={50}
                      />
                      <p className="text-xs text-muted-foreground">
                        {packName.length}/50
                      </p>
                    </div>
                    
                    <div className="space-y-2">
                      <Label htmlFor="pack-description">模板描述 *</Label>
                      <Textarea
                        id="pack-description"
                        placeholder="详细描述这个配置模板的特点、适用场景等..."
                        value={packDescription}
                        onChange={e => setPackDescription(e.target.value)}
                        rows={4}
                        maxLength={500}
                      />
                      <p className="text-xs text-muted-foreground">
                        {packDescription.length}/500
                      </p>
                    </div>
                    
                    <div className="space-y-2">
                      <Label htmlFor="pack-author">作者名称 *</Label>
                      <Input
                        id="pack-author"
                        placeholder="你的昵称或 ID"
                        value={packAuthor}
                        onChange={e => setPackAuthor(e.target.value)}
                        maxLength={30}
                      />
                    </div>
                    
                    <div className="space-y-2">
                      <Label>标签（可选，最多 5 个）</Label>
                      <div className="flex flex-wrap gap-2">
                        {PRESET_TAGS.map(tag => (
                          <Badge
                            key={tag}
                            variant={packTags.includes(tag) ? 'default' : 'outline'}
                            className="cursor-pointer transition-colors"
                            onClick={() => toggleTag(tag)}
                          >
                            {packTags.includes(tag) && <Check className="w-3 h-3 mr-1" />}
                            <Tag className="w-3 h-3 mr-1" />
                            {tag}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                  
                  <Alert>
                    <Info className="h-4 w-4" />
                    <AlertTitle>审核说明</AlertTitle>
                    <AlertDescription>
                      提交后需要经过审核才能在市场中展示。审核通常在 1-3 个工作日内完成。
                    </AlertDescription>
                  </Alert>
                </div>
              )}
            </>
          )}
        </ScrollArea>
        
        <DialogFooter className="flex justify-between pt-4 border-t">
          <div>
            {step > 1 && (
              <Button variant="outline" onClick={() => setStep(step - 1)} disabled={submitting}>
                上一步
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => {
                setOpen(false)
                resetForm()
              }}
              disabled={submitting}
            >
              取消
            </Button>
            {step < totalSteps ? (
              <Button
                onClick={() => setStep(step + 1)}
                disabled={
                  loading ||
                  (selectedProviders.size === 0 && selectedModels.size === 0 && selectedTasks.size === 0)
                }
              >
                下一步
              </Button>
            ) : (
              <Button onClick={handleSubmit} disabled={submitting}>
                {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                提交审核
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
