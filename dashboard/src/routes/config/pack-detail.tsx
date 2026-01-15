/**
 * Pack 详情页面
 * 
 * 查看 Pack 详情并应用到本地配置
 */

import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { packDetailRoute } from '@/router'
import {
  Package,
  ArrowLeft,
  Download,
  Heart,
  Clock,
  User,
  Server,
  Layers,
  ListChecks,
  Tag,
  Check,
  AlertTriangle,
  Info,
  ChevronRight,
  Key,
  Settings,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Separator } from '@/components/ui/separator'
import { toast } from '@/hooks/use-toast'
import {
  getPack,
  recordPackDownload,
  togglePackLike,
  checkPackLike,
  detectPackConflicts,
  applyPack,
  getPackUserId,
  type ModelPack,
  type ApplyPackOptions,
  type ApplyPackConflicts,
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

export default function PackDetailPage() {
  const { packId } = packDetailRoute.useParams()
  const navigate = useNavigate()
  
  const [pack, setPack] = useState<ModelPack | null>(null)
  const [loading, setLoading] = useState(true)
  const [liked, setLiked] = useState(false)
  const [liking, setLiking] = useState(false)
  
  // 应用向导状态
  const [showApplyDialog, setShowApplyDialog] = useState(false)
  const [applyStep, setApplyStep] = useState(1)
  const [conflicts, setConflicts] = useState<ApplyPackConflicts | null>(null)
  const [detectingConflicts, setDetectingConflicts] = useState(false)
  const [applying, setApplying] = useState(false)
  
  // 应用选项
  const [applyOptions, setApplyOptions] = useState<ApplyPackOptions>({
    apply_providers: true,
    apply_models: true,
    apply_task_config: true,
    task_mode: 'append',
    selected_providers: undefined,
    selected_models: undefined,
    selected_tasks: undefined,
  })
  
  // 提供商映射和 API Key
  const [providerMapping, setProviderMapping] = useState<Record<string, string>>({})
  const [newProviderApiKeys, setNewProviderApiKeys] = useState<Record<string, string>>({})
  
  const userId = getPackUserId()
  
  // 加载 Pack
  const loadPack = useCallback(async () => {
    if (!packId) return
    
    setLoading(true)
    try {
      const data = await getPack(packId)
      setPack(data)
      
      const isLiked = await checkPackLike(packId, userId)
      setLiked(isLiked)
    } catch (error) {
      console.error('加载 Pack 失败:', error)
      toast({ title: '加载模板失败', variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }, [packId, userId])
  
  useEffect(() => {
    loadPack()
  }, [loadPack])
  
  // 点赞
  const handleLike = async () => {
    if (!packId || liking) return
    
    setLiking(true)
    try {
      const result = await togglePackLike(packId, userId)
      setLiked(result.liked)
      if (pack) {
        setPack({ ...pack, likes: result.likes })
      }
    } catch (error) {
      console.error('点赞失败:', error)
      toast({ title: '点赞失败', variant: 'destructive' })
    } finally {
      setLiking(false)
    }
  }
  
  // 开始应用流程
  const startApply = async () => {
    if (!pack) return
    
    setShowApplyDialog(true)
    setApplyStep(1)
    setDetectingConflicts(true)
    
    try {
      const detected = await detectPackConflicts(pack)
      setConflicts(detected)
      
      // 初始化提供商映射（已存在的提供商默认使用第一个匹配的本地提供商）
      const mapping: Record<string, string> = {}
      for (const c of detected.existing_providers) {
        mapping[c.pack_provider.name] = c.local_providers[0].name
      }
      setProviderMapping(mapping)
      
      // 初始化新提供商的 API Key
      const keys: Record<string, string> = {}
      for (const p of detected.new_providers) {
        keys[p.name] = ''
      }
      setNewProviderApiKeys(keys)
    } catch (error) {
      console.error('检测冲突失败:', error)
      toast({ title: '检测配置冲突失败', variant: 'destructive' })
      setShowApplyDialog(false)
    } finally {
      setDetectingConflicts(false)
    }
  }
  
  // 执行应用
  const executeApply = async () => {
    if (!pack) return
    
    // 验证新提供商都有 API Key
    if (applyOptions.apply_providers && conflicts) {
      for (const p of conflicts.new_providers) {
        if (!newProviderApiKeys[p.name]) {
          toast({ title: `请填写提供商 "${p.name}" 的 API Key`, variant: 'destructive' })
          return
        }
      }
    }
    
    setApplying(true)
    try {
      await applyPack(pack, applyOptions, providerMapping, newProviderApiKeys)
      
      // 记录下载
      await recordPackDownload(pack.id, userId)
      
      // 更新下载数
      setPack({ ...pack, downloads: pack.downloads + 1 })
      
      toast({ title: '配置模板应用成功！' })
      setShowApplyDialog(false)
    } catch (error) {
      console.error('应用 Pack 失败:', error)
      toast({ title: error instanceof Error ? error.message : '应用配置失败', variant: 'destructive' })
    } finally {
      setApplying(false)
    }
  }
  
  // 格式化日期
  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
  }
  
  if (loading) {
    return <PackDetailSkeleton />
  }
  
  if (!pack) {
    return (
      <div className="text-center py-12">
        <Package className="w-16 h-16 mx-auto mb-4 opacity-50" />
        <h2 className="text-xl font-semibold">模板不存在</h2>
        <p className="text-muted-foreground mt-2">该配置模板可能已被删除或尚未通过审核</p>
        <Button className="mt-4" onClick={() => navigate({ to: '/config/pack-market' })}>
          <ArrowLeft className="w-4 h-4 mr-2" />
          返回市场
        </Button>
      </div>
    )
  }
  
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6">
          {/* 返回按钮 */}
          <Button variant="ghost" size="sm" onClick={() => navigate({ to: '/config/pack-market' })} className="gap-2">
            <ArrowLeft className="w-4 h-4" />
            返回市场
          </Button>
      
      {/* 头部信息 */}
      <div className="flex flex-col md:flex-row gap-6">
        <div className="flex-1 space-y-4">
          <div className="flex items-start gap-3">
            <Package className="w-10 h-10 text-primary mt-1" />
            <div>
              <h1 className="text-2xl font-bold flex items-center gap-2">
                {pack.name}
                <Badge variant="secondary">v{pack.version}</Badge>
              </h1>
              <p className="text-muted-foreground mt-1">{pack.description}</p>
            </div>
          </div>
          
          {/* 元信息 */}
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <User className="w-4 h-4" />
              {pack.author}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="w-4 h-4" />
              {formatDate(pack.created_at)}
            </span>
            <span className="flex items-center gap-1">
              <Download className="w-4 h-4" />
              {pack.downloads} 次下载
            </span>
            <span className="flex items-center gap-1">
              <Heart className={`w-4 h-4 ${liked ? 'fill-red-500 text-red-500' : ''}`} />
              {pack.likes} 赞
            </span>
          </div>
          
          {/* 标签 */}
          {pack.tags && pack.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {pack.tags.map(tag => (
                <Badge key={tag} variant="outline">
                  <Tag className="w-3 h-3 mr-1" />
                  {tag}
                </Badge>
              ))}
            </div>
          )}
        </div>
        
        {/* 操作按钮 */}
        <div className="flex flex-col gap-2 min-w-[160px]">
          <Button size="lg" onClick={startApply}>
            <Download className="w-4 h-4 mr-2" />
            应用模板
          </Button>
          <Button 
            variant="outline" 
            onClick={handleLike}
            disabled={liking}
            className={liked ? 'text-red-500 border-red-200' : ''}
          >
            <Heart className={`w-4 h-4 mr-2 ${liked ? 'fill-current' : ''}`} />
            {liked ? '已点赞' : '点赞'}
          </Button>
        </div>
      </div>
      
      <Separator />
      
      {/* 内容统计 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <Server className="w-8 h-8 text-blue-500 flex-shrink-0" />
            <div>
              <p className="text-2xl font-bold">{pack.providers.length}</p>
              <p className="text-sm text-muted-foreground">API 提供商</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <Layers className="w-8 h-8 text-green-500 flex-shrink-0" />
            <div>
              <p className="text-2xl font-bold">{pack.models.length}</p>
              <p className="text-sm text-muted-foreground">模型配置</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <ListChecks className="w-8 h-8 text-purple-500 flex-shrink-0" />
            <div>
              <p className="text-2xl font-bold">{Object.keys(pack.task_config).length}</p>
              <p className="text-sm text-muted-foreground">任务配置</p>
            </div>
          </CardContent>
        </Card>
      </div>
      
      {/* 详细内容 */}
      <Tabs defaultValue="providers" className="space-y-4">
        <TabsList className="w-full sm:w-auto grid grid-cols-3 sm:flex">
          <TabsTrigger value="providers" className="gap-1 sm:gap-2">
            <Server className="w-4 h-4" />
            <span className="hidden sm:inline">提供商</span>
            <span className="sm:hidden">提供商</span>
            <span className="hidden sm:inline">({pack.providers.length})</span>
          </TabsTrigger>
          <TabsTrigger value="models" className="gap-1 sm:gap-2">
            <Layers className="w-4 h-4" />
            <span className="hidden sm:inline">模型</span>
            <span className="sm:hidden">模型</span>
            <span className="hidden sm:inline">({pack.models.length})</span>
          </TabsTrigger>
          <TabsTrigger value="tasks" className="gap-1 sm:gap-2">
            <ListChecks className="w-4 h-4" />
            <span className="hidden sm:inline">任务配置</span>
            <span className="sm:hidden">任务</span>
            <span className="hidden sm:inline">({Object.keys(pack.task_config).length})</span>
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="providers">
          <Card>
            <CardHeader>
              <CardTitle>API 提供商</CardTitle>
              <CardDescription>模板中包含的 API 提供商配置（不含 API Key）</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>名称</TableHead>
                      <TableHead>Base URL</TableHead>
                      <TableHead>类型</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pack.providers.map(provider => (
                      <TableRow key={provider.name}>
                        <TableCell className="font-medium whitespace-nowrap">{provider.name}</TableCell>
                        <TableCell className="text-muted-foreground font-mono text-sm max-w-[200px] truncate">
                          {provider.base_url}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{provider.client_type}</Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
        
        <TabsContent value="models">
          <Card>
            <CardHeader>
              <CardTitle>模型配置</CardTitle>
              <CardDescription>模板中包含的模型配置</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>模型名称</TableHead>
                      <TableHead>标识符</TableHead>
                      <TableHead>提供商</TableHead>
                      <TableHead className="text-right">价格 (入/出)</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pack.models.map(model => (
                      <TableRow key={model.name}>
                        <TableCell className="font-medium whitespace-nowrap">{model.name}</TableCell>
                        <TableCell className="text-muted-foreground font-mono text-sm max-w-[150px] truncate">
                          {model.model_identifier}
                        </TableCell>
                        <TableCell className="whitespace-nowrap">{model.api_provider}</TableCell>
                        <TableCell className="text-right text-muted-foreground whitespace-nowrap">
                          ¥{model.price_in} / ¥{model.price_out}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
        
        <TabsContent value="tasks">
          <Card>
            <CardHeader>
              <CardTitle>任务配置</CardTitle>
              <CardDescription>模板中各任务类型的模型分配</CardDescription>
            </CardHeader>
            <CardContent>
              <Accordion type="multiple" className="w-full">
                {Object.entries(pack.task_config).map(([taskKey, config]) => (
                  <AccordionItem key={taskKey} value={taskKey}>
                    <AccordionTrigger>
                      <div className="flex items-center gap-2">
                        <Settings className="w-4 h-4" />
                        {TASK_TYPE_NAMES[taskKey] || taskKey}
                        <Badge variant="secondary" className="ml-2">
                          {config.model_list.length} 个模型
                        </Badge>
                      </div>
                    </AccordionTrigger>
                    <AccordionContent>
                      <div className="space-y-2 pl-6">
                        <div className="text-sm text-muted-foreground">
                          分配的模型：
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {config.model_list.map((model: string) => (
                            <Badge key={model} variant="outline">{model}</Badge>
                          ))}
                        </div>
                        {config.temperature !== undefined && (
                          <div className="text-sm">
                            Temperature: <span className="font-mono">{config.temperature}</span>
                          </div>
                        )}
                        {config.max_tokens !== undefined && (
                          <div className="text-sm">
                            Max Tokens: <span className="font-mono">{config.max_tokens}</span>
                          </div>
                        )}
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                ))}
              </Accordion>
            </CardContent>
          </Card>
        </TabsContent>
          </Tabs>
          
          {/* 应用向导对话框 */}
          <ApplyDialog
            open={showApplyDialog}
            onOpenChange={setShowApplyDialog}
            pack={pack}
            step={applyStep}
            setStep={setApplyStep}
            conflicts={conflicts}
            detectingConflicts={detectingConflicts}
            applying={applying}
            options={applyOptions}
            setOptions={setApplyOptions}
            _providerMapping={providerMapping}
            _setProviderMapping={setProviderMapping}
            newProviderApiKeys={newProviderApiKeys}
            setNewProviderApiKeys={setNewProviderApiKeys}
            onApply={executeApply}
          />
        </div>
      </ScrollArea>
    </div>
  )
}

// 应用向导对话框
function ApplyDialog({
  open,
  onOpenChange,
  pack,
  step,
  setStep,
  conflicts,
  detectingConflicts,
  applying,
  options,
  setOptions,
  _providerMapping,
  _setProviderMapping,
  newProviderApiKeys,
  setNewProviderApiKeys,
  onApply,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  pack: ModelPack
  step: number
  setStep: (step: number) => void
  conflicts: ApplyPackConflicts | null
  detectingConflicts: boolean
  applying: boolean
  options: ApplyPackOptions
  setOptions: (options: ApplyPackOptions) => void
  _providerMapping: Record<string, string>
  _setProviderMapping: (mapping: Record<string, string>) => void
  newProviderApiKeys: Record<string, string>
  setNewProviderApiKeys: (keys: Record<string, string>) => void
  onApply: () => void
}) {
  const totalSteps = 3
  
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Package className="w-5 h-5" />
            应用配置模板
          </DialogTitle>
          <DialogDescription>
            步骤 {step} / {totalSteps}：
            {step === 1 && '选择要应用的内容'}
            {step === 2 && '配置提供商映射'}
            {step === 3 && '确认并应用'}
          </DialogDescription>
        </DialogHeader>
        
        {detectingConflicts ? (
          <div className="py-8 text-center">
            <Loader2 className="w-8 h-8 mx-auto animate-spin text-primary" />
            <p className="mt-4 text-muted-foreground">正在检测配置冲突...</p>
          </div>
        ) : (
          <>
            {/* 步骤 1: 选择内容 */}
            {step === 1 && (
              <div className="space-y-4">
                <div className="space-y-3">
                  <div className="flex items-center space-x-2">
                    <Checkbox
                      id="apply_providers"
                      checked={options.apply_providers}
                      onCheckedChange={checked =>
                        setOptions({ ...options, apply_providers: checked as boolean })
                      }
                    />
                    <Label htmlFor="apply_providers" className="flex items-center gap-2">
                      <Server className="w-4 h-4" />
                      应用提供商配置 ({pack.providers.length} 个)
                    </Label>
                  </div>
                  
                  <div className="flex items-center space-x-2">
                    <Checkbox
                      id="apply_models"
                      checked={options.apply_models}
                      onCheckedChange={checked =>
                        setOptions({ ...options, apply_models: checked as boolean })
                      }
                    />
                    <Label htmlFor="apply_models" className="flex items-center gap-2">
                      <Layers className="w-4 h-4" />
                      应用模型配置 ({pack.models.length} 个)
                    </Label>
                  </div>
                  
                  <div className="flex items-center space-x-2">
                    <Checkbox
                      id="apply_task_config"
                      checked={options.apply_task_config}
                      onCheckedChange={checked =>
                        setOptions({ ...options, apply_task_config: checked as boolean })
                      }
                    />
                    <Label htmlFor="apply_task_config" className="flex items-center gap-2">
                      <ListChecks className="w-4 h-4" />
                      应用任务配置 ({Object.keys(pack.task_config).length} 个)
                    </Label>
                  </div>
                </div>
                
                {options.apply_task_config && (
                  <div className="pl-6 space-y-2 border-l-2 border-muted">
                    <Label className="text-sm font-medium">任务配置应用模式</Label>
                    <RadioGroup
                      value={options.task_mode}
                      onValueChange={value =>
                        setOptions({ ...options, task_mode: value as 'replace' | 'append' })
                      }
                    >
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="append" id="mode_append" />
                        <Label htmlFor="mode_append" className="font-normal">
                          追加模式 - 将模板中的模型添加到现有配置
                        </Label>
                      </div>
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="replace" id="mode_replace" />
                        <Label htmlFor="mode_replace" className="font-normal">
                          替换模式 - 用模板配置完全替换现有配置
                        </Label>
                      </div>
                    </RadioGroup>
                  </div>
                )}
              </div>
            )}
            
            {/* 步骤 2: 提供商映射 */}
            {step === 2 && conflicts && (
              <div className="space-y-4">
                {/* 已存在的提供商 */}
                {options.apply_providers && conflicts.existing_providers.length > 0 && (
                  <div className="space-y-3">
                    <Alert>
                      <Info className="h-4 w-4" />
                      <AlertTitle>发现已有的提供商</AlertTitle>
                      <AlertDescription>
                        以下提供商的 URL 与您本地配置中的提供商匹配，将自动使用本地提供商：
                      </AlertDescription>
                    </Alert>
                    
                    <div className="space-y-2">
                      {conflicts.existing_providers.map(({ pack_provider, local_providers }) => (
                        <div
                          key={pack_provider.name}
                          className="flex items-center gap-2 p-3 bg-muted rounded-lg"
                        >
                          <Check className="w-4 h-4 text-green-500 flex-shrink-0" />
                          <span className="font-medium flex-shrink-0">{pack_provider.name}</span>
                          <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                          
                          {local_providers.length === 1 ? (
                            <>
                              <span className="text-muted-foreground">{local_providers[0].name}</span>
                              <Badge variant="outline" className="ml-auto">URL 匹配</Badge>
                            </>
                          ) : (
                            <>
                              <Select
                                value={_providerMapping[pack_provider.name] || local_providers[0].name}
                                onValueChange={value =>
                                  _setProviderMapping({
                                    ..._providerMapping,
                                    [pack_provider.name]: value,
                                  })
                                }
                              >
                                <SelectTrigger className="w-[200px]">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {local_providers.map(p => (
                                    <SelectItem key={p.name} value={p.name}>
                                      {p.name}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <Badge variant="outline" className="ml-auto">
                                {local_providers.length} 个匹配
                              </Badge>
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                
                {/* 新提供商 */}
                {options.apply_providers && conflicts.new_providers.length > 0 && (
                  <div className="space-y-3">
                    <Alert variant="destructive">
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>需要配置 API Key</AlertTitle>
                      <AlertDescription>
                        以下提供商在您的本地配置中不存在，需要填写 API Key：
                      </AlertDescription>
                    </Alert>
                    
                    <div className="space-y-4">
                      {conflicts.new_providers.map(provider => (
                        <div key={provider.name} className="space-y-2">
                          <div className="flex items-center gap-2">
                            <Key className="w-4 h-4 text-amber-500" />
                            <span className="font-medium">{provider.name}</span>
                            <span className="text-xs text-muted-foreground">
                              ({provider.base_url})
                            </span>
                          </div>
                          <Input
                            type="password"
                            placeholder={`输入 ${provider.name} 的 API Key`}
                            value={newProviderApiKeys[provider.name] || ''}
                            onChange={e =>
                              setNewProviderApiKeys({
                                ...newProviderApiKeys,
                                [provider.name]: e.target.value,
                              })
                            }
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                
                {(!options.apply_providers || (conflicts.existing_providers.length === 0 && conflicts.new_providers.length === 0)) && (
                  <Alert>
                    <Check className="h-4 w-4" />
                    <AlertTitle>无需配置</AlertTitle>
                    <AlertDescription>
                      模板中没有提供商配置，或您选择不应用提供商。
                    </AlertDescription>
                  </Alert>
                )}
              </div>
            )}
            
            {/* 步骤 3: 确认 */}
            {step === 3 && (
              <div className="space-y-4">
                <Alert>
                  <Info className="h-4 w-4" />
                  <AlertTitle>确认应用</AlertTitle>
                  <AlertDescription>
                    请确认以下将要应用的内容：
                  </AlertDescription>
                </Alert>
                
                <div className="space-y-2">
                  {options.apply_providers && (
                    <div className="flex items-center gap-2 text-sm">
                      <Check className="w-4 h-4 text-green-500" />
                      <Server className="w-4 h-4" />
                      <span>应用 {pack.providers.length} 个提供商配置</span>
                    </div>
                  )}
                  {options.apply_models && (
                    <div className="flex items-center gap-2 text-sm">
                      <Check className="w-4 h-4 text-green-500" />
                      <Layers className="w-4 h-4" />
                      <span>应用 {pack.models.length} 个模型配置</span>
                    </div>
                  )}
                  {options.apply_task_config && (
                    <div className="flex items-center gap-2 text-sm">
                      <Check className="w-4 h-4 text-green-500" />
                      <ListChecks className="w-4 h-4" />
                      <span>
                        {options.task_mode === 'append' ? '追加' : '替换'} {Object.keys(pack.task_config).length} 个任务配置
                      </span>
                    </div>
                  )}
                </div>
                
                {conflicts && conflicts.new_providers.length > 0 && (
                  <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertDescription>
                      将添加 {conflicts.new_providers.length} 个新提供商，请确保已填写正确的 API Key。
                    </AlertDescription>
                  </Alert>
                )}
              </div>
            )}
          </>
        )}
        
        <DialogFooter className="flex justify-between">
          <div>
            {step > 1 && !detectingConflicts && (
              <Button variant="outline" onClick={() => setStep(step - 1)} disabled={applying}>
                上一步
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={applying}>
              取消
            </Button>
            {step < totalSteps ? (
              <Button onClick={() => setStep(step + 1)} disabled={detectingConflicts}>
                下一步
              </Button>
            ) : (
              <Button onClick={onApply} disabled={applying}>
                {applying && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                应用模板
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 加载骨架
function PackDetailSkeleton() {
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6">
          {/* 返回按钮 */}
          <Skeleton className="h-9 w-24" />
          
          {/* 头部信息 */}
          <div className="flex flex-col md:flex-row gap-6">
            <div className="flex-1 space-y-4">
              <div className="flex items-start gap-3">
                <Skeleton className="w-10 h-10" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-8 w-2/3" />
                  <Skeleton className="h-4 w-full" />
                </div>
              </div>
              
              {/* 元信息 */}
              <div className="flex flex-wrap gap-4">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-4 w-20" />
              </div>
              
              {/* 标签 */}
              <div className="flex flex-wrap gap-2">
                <Skeleton className="h-6 w-20" />
                <Skeleton className="h-6 w-24" />
                <Skeleton className="h-6 w-16" />
              </div>
            </div>
            
            {/* 操作按钮 */}
            <div className="flex flex-col gap-2 min-w-[160px]">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          </div>
          
          <Skeleton className="h-px w-full" />
          
          {/* 内容统计卡片 */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
          
          {/* Tabs */}
          <div className="space-y-4">
            <div className="flex gap-2">
              <Skeleton className="h-10 w-32" />
              <Skeleton className="h-10 w-32" />
              <Skeleton className="h-10 w-32" />
            </div>
            <Skeleton className="h-96 w-full" />
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
