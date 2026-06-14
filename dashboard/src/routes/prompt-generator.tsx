import { type ReactNode, useMemo, useState } from 'react'

import { useMutation, useQuery } from '@tanstack/react-query'
import { Bot, Copy, Download, FileText, Loader2, RefreshCw, Sparkles, Upload, Wand2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { getModelConfig } from '@/lib/config-api'
import {
  applyPromptGeneratorBlocks,
  generatePromptPersona,
  type PromptGeneratorConfigBlock,
  type PromptGeneratorResponse,
} from '@/lib/prompt-generator-api'
import { cn } from '@/lib/utils'

type OutputTab = 'blocks' | 'toml' | 'raw'
type TargetScene = 'group' | 'private' | 'both'

interface PromptGeneratorModel {
  api_provider: string
  model_identifier: string
  name: string
  visual: boolean
}

function unwrapConfigPayload(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object') {
    return {}
  }
  const record = payload as Record<string, unknown>
  const config = record.config
  return config && typeof config === 'object' ? (config as Record<string, unknown>) : record
}

function normalizeModels(payload: unknown): PromptGeneratorModel[] {
  const config = unwrapConfigPayload(payload)
  const rawModels = config.models
  if (!Array.isArray(rawModels)) {
    return []
  }

  return rawModels
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null
      }
      const record = item as Record<string, unknown>
      const name = String(record.name ?? '').trim()
      const modelIdentifier = String(record.model_identifier ?? '').trim()
      const apiProvider = String(record.api_provider ?? '').trim()
      if (!name) {
        return null
      }
      return {
        api_provider: apiProvider,
        model_identifier: modelIdentifier,
        name,
        visual: record.visual === true,
      }
    })
    .filter((item): item is PromptGeneratorModel => item !== null)
}

function FieldBlock({
  children,
  description,
  label,
}: {
  children: ReactNode
  description?: string
  label: string
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {children}
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  )
}

function ConfigBlockView({
  applying,
  block,
  disabled,
  onApply,
  onCopy,
}: {
  applying: boolean
  block: PromptGeneratorConfigBlock
  disabled: boolean
  onApply: (block: PromptGeneratorConfigBlock) => void
  onCopy: (content: string, label: string) => void
}) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-medium">{block.title}</h3>
            <Badge variant="secondary">{block.section}.{block.field}</Badge>
          </div>
          {block.description && <p className="text-xs text-muted-foreground">{block.description}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onCopy(block.toml, block.title)}>
            <Copy className="h-4 w-4" />
            复制
          </Button>
          <Button size="sm" onClick={() => onApply(block)} disabled={disabled}>
            {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            注入此块
          </Button>
        </div>
      </div>
      <Textarea readOnly autoResize={false} value={block.toml} className="mt-3 min-h-[132px] font-mono text-xs" />
    </div>
  )
}

export function PromptGeneratorPage() {
  const { toast } = useToast()
  const [selectedModelName, setSelectedModelName] = useState('')
  const [sourceText, setSourceText] = useState('')
  const [targetScene, setTargetScene] = useState<TargetScene>('group')
  const [language, setLanguage] = useState('简体中文')
  const [extraRequirements, setExtraRequirements] = useState('')
  const [temperature, setTemperature] = useState('0.3')
  const [maxTokens, setMaxTokens] = useState('1800')
  const [applyingBlockId, setApplyingBlockId] = useState<string | null>(null)
  const [outputTab, setOutputTab] = useState<OutputTab>('blocks')
  const [generated, setGenerated] = useState<PromptGeneratorResponse | null>(null)

  // 模型列表：读取失败用局部呈现（不弹 toast），「刷新模型」按钮调 refetch
  const modelsQuery = useQuery({
    queryKey: ['promptGenerator', 'models'],
    queryFn: () => getModelConfig(),
  })
  const loadingModels = modelsQuery.isFetching
  const models = useMemo(() => normalizeModels(modelsQuery.data), [modelsQuery.data])

  // 当前选中项：用户已选且仍有效则沿用，否则回落到首个模型（渲染期派生，避免与列表加载同步的副作用）
  const modelName = models.some((model) => model.name === selectedModelName)
    ? selectedModelName
    : models[0]?.name ?? ''
  const selectedModel = models.find((model) => model.name === modelName)

  // 注入配置块（失败由全局 mutation 错误 toast 呈现）
  const applyMutation = useMutation({
    mutationFn: async (vars: { blocks: PromptGeneratorConfigBlock[]; label: string; blockId?: string }) =>
      applyPromptGeneratorBlocks(vars.blocks),
    meta: { errorTitle: '注入失败' },
    onMutate: (vars) => {
      setApplyingBlockId(vars.blockId ?? null)
    },
    onSuccess: (data, vars) => {
      toast({
        title: `${vars.label}已注入配置`,
        description: data.sections.length > 0 ? `已更新 ${data.sections.join('、')}` : undefined,
      })
    },
    onSettled: () => {
      setApplyingBlockId(null)
    },
  })
  const applyingAll = applyMutation.isPending && applyingBlockId === null
  const applying = applyMutation.isPending

  // 生成人设（失败由全局 mutation 错误 toast 呈现）
  const generateMutation = useMutation({
    mutationFn: async (vars: {
      model_name: string
      source_text: string
      target_scene: TargetScene
      language: string
      extra_requirements: string
      temperature: number
      max_tokens: number
    }) => generatePromptPersona(vars),
    meta: { errorTitle: '生成失败' },
    onSuccess: (data) => {
      setGenerated(data)
      setOutputTab('blocks')
      toast({
        title: '人设解析完成',
        description: `${data.model_name} · ${data.total_tokens || 0} tokens`,
      })
    },
  })
  const generating = generateMutation.isPending

  const handleCopy = async (content: string, label: string) => {
    try {
      await navigator.clipboard.writeText(content)
      toast({ title: `${label}已复制` })
    } catch (error) {
      toast({
        title: '复制失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    }
  }

  const handleDownload = (filename: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    toast({ title: '已生成下载文件', description: filename })
  }

  const handleApplyBlocks = (blocks: PromptGeneratorConfigBlock[], label: string, blockId?: string) => {
    if (blocks.length === 0) {
      toast({ title: '没有可注入的配置块', variant: 'destructive' })
      return
    }
    applyMutation.mutate({ blocks, label, blockId })
  }

  const handleGenerate = () => {
    const normalizedSourceText = sourceText.trim()
    if (!modelName) {
      toast({ title: '请选择生成模型', variant: 'destructive' })
      return
    }
    if (!normalizedSourceText) {
      toast({ title: '请输入要解析的人设或文段', variant: 'destructive' })
      return
    }

    const parsedTemperature = Number.parseFloat(temperature)
    const parsedMaxTokens = Number.parseInt(maxTokens, 10)
    if (!Number.isFinite(parsedTemperature) || parsedTemperature < 0 || parsedTemperature > 2) {
      toast({ title: '温度需要在 0-2 之间', variant: 'destructive' })
      return
    }
    if (!Number.isFinite(parsedMaxTokens) || parsedMaxTokens < 256 || parsedMaxTokens > 8192) {
      toast({ title: '最大输出 Token 需要在 256-8192 之间', variant: 'destructive' })
      return
    }

    generateMutation.mutate({
      model_name: modelName,
      source_text: normalizedSourceText,
      target_scene: targetScene,
      language,
      extra_requirements: extraRequirements.trim(),
      temperature: parsedTemperature,
      max_tokens: parsedMaxTokens,
    })
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4 sm:p-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Sparkles className="h-6 w-6 text-primary" />
              <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">人设生成器（测试功能）</h1>
            </div>
            <p className="text-sm text-muted-foreground">
              选择已配置模型，把任意文段、角色卡或人设解析成 MaiBot 配置格式。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => void modelsQuery.refetch()} disabled={loadingModels}>
              <RefreshCw className={cn('h-4 w-4', loadingModels && 'animate-spin')} />
              刷新模型
            </Button>
            <Button size="sm" onClick={handleGenerate} disabled={generating || !modelName}>
              {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              {generating ? '生成中' : '生成'}
            </Button>
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(22rem,0.9fr)_minmax(0,1.25fr)]">
          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Bot className="h-4 w-4" />
                  生成输入
                </CardTitle>
                <CardDescription>输入越接近真实需求，模型越容易拆成可维护配置。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <FieldBlock label="生成模型" description="来自模型管理中定义的 models。">
                  <Select value={modelName} onValueChange={setSelectedModelName} disabled={loadingModels || models.length === 0}>
                    <SelectTrigger>
                      <SelectValue placeholder={loadingModels ? '加载模型中...' : '选择模型'} />
                    </SelectTrigger>
                    <SelectContent>
                      {models.map((model) => (
                        <SelectItem key={model.name} value={model.name}>
                          {model.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedModel && (
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">{selectedModel.api_provider || '未指定提供商'}</Badge>
                      <Badge variant="outline">{selectedModel.model_identifier || selectedModel.name}</Badge>
                      {selectedModel.visual && <Badge variant="outline">视觉模型</Badge>}
                    </div>
                  )}
                </FieldBlock>

                <div className="grid gap-4 md:grid-cols-2">
                  <FieldBlock label="目标场景">
                    <Select value={targetScene} onValueChange={(value) => setTargetScene(value as TargetScene)}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="group">群聊</SelectItem>
                        <SelectItem value="private">私聊</SelectItem>
                        <SelectItem value="both">群聊 + 私聊</SelectItem>
                      </SelectContent>
                    </Select>
                  </FieldBlock>
                  <FieldBlock label="生成语言">
                    <Input value={language} onChange={(event) => setLanguage(event.target.value)} />
                  </FieldBlock>
                </div>

                <FieldBlock label="原始文段或人设">
                  <Textarea
                    value={sourceText}
                    onChange={(event) => setSourceText(event.target.value)}
                    placeholder="可以粘贴角色卡、几句人设、说话风格描述、群聊要求，模型会拆成 personality / reply_style / chat prompt。"
                    className="min-h-[380px]"
                    maxLength={20000}
                  />
                  <div className="text-right text-xs text-muted-foreground">{sourceText.length}/20000</div>
                </FieldBlock>

                <FieldBlock label="额外要求">
                  <Textarea
                    value={extraRequirements}
                    onChange={(event) => setExtraRequirements(event.target.value)}
                    placeholder="例如：更短、更日常；不要攻击性；保留技术群助教气质；不要改成角色卡口吻。"
                    className="min-h-[120px]"
                    maxLength={4000}
                  />
                </FieldBlock>

                <div className="grid gap-4 md:grid-cols-2">
                  <FieldBlock label="温度">
                    <Input value={temperature} onChange={(event) => setTemperature(event.target.value)} inputMode="decimal" />
                  </FieldBlock>
                  <FieldBlock label="最大输出 Token">
                    <Input value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} inputMode="numeric" />
                  </FieldBlock>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <FileText className="h-4 w-4" />
                      生成结果
                    </CardTitle>
                    <CardDescription>生成结果会拆成配置块，注入时只覆盖对应字段，不影响其它配置。</CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => generated && void handleApplyBlocks(generated.config_blocks, '全部配置块')}
                      disabled={!generated || applying}
                    >
                      {applyingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                      全部注入
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => generated && handleCopy(generated.toml_snippet, '配置片段')}
                      disabled={!generated}
                    >
                      <Copy className="h-4 w-4" />
                      复制配置
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => generated && handleDownload('maibot-personality-prompt.toml', generated.toml_snippet)}
                      disabled={!generated}
                    >
                      <Download className="h-4 w-4" />
                      下载
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <Separator />
              <CardContent className="pt-4">
                {generated ? (
                  <Tabs value={outputTab} onValueChange={(value) => setOutputTab(value as OutputTab)}>
                    <TabsList className="grid w-full grid-cols-3 lg:w-[28rem]">
                      <TabsTrigger value="blocks">配置块</TabsTrigger>
                      <TabsTrigger value="toml">配置片段</TabsTrigger>
                      <TabsTrigger value="raw">原始输出</TabsTrigger>
                    </TabsList>
                    <TabsContent value="blocks" className="mt-4">
                      <div className="space-y-3">
                        {generated.config_blocks.map((block) => (
                          <ConfigBlockView
                            key={block.id}
                            applying={applyingBlockId === block.id}
                            block={block}
                            disabled={applying}
                            onApply={(nextBlock) => void handleApplyBlocks([nextBlock], nextBlock.title, nextBlock.id)}
                            onCopy={handleCopy}
                          />
                        ))}
                      </div>
                    </TabsContent>
                    <TabsContent value="toml" className="mt-4">
                      <Textarea
                        readOnly
                        autoResize={false}
                        value={generated.toml_snippet}
                        className="h-[560px] font-mono text-xs"
                      />
                    </TabsContent>
                    <TabsContent value="raw" className="mt-4">
                      <Textarea
                        readOnly
                        autoResize={false}
                        value={[generated.raw_response, generated.reasoning ? `\n\n# reasoning\n${generated.reasoning}` : ''].join('')}
                        className="h-[560px] font-mono text-xs"
                      />
                    </TabsContent>
                  </Tabs>
                ) : (
                  <div className="flex h-[560px] flex-col items-center justify-center rounded-md border border-dashed text-center">
                    <Wand2 className="mb-3 h-10 w-10 text-muted-foreground/50" />
                    <p className="text-sm font-medium">等待生成</p>
                    <p className="mt-1 max-w-md text-xs text-muted-foreground">
                      选择模型并输入人设后，结果会按 bot_config.toml 字段拆成可注入的配置块。
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>

            {generated && (
              <Card>
                <CardContent className="flex flex-wrap items-center gap-2 p-4 text-xs text-muted-foreground">
                  <Badge variant="secondary">{generated.model_name}</Badge>
                  <span>输入 {generated.prompt_tokens || 0}</span>
                  <span>输出 {generated.completion_tokens || 0}</span>
                  <span>总计 {generated.total_tokens || 0} tokens</span>
                  {generated.result.notes.map((note) => (
                    <Badge key={note} variant="outline" className="max-w-full whitespace-normal text-left">
                      {note}
                    </Badge>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </div>
    </ScrollArea>
  )
}
