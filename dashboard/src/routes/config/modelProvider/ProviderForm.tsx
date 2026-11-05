import { useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Check, ChevronsUpDown, Copy, Eye, EyeOff, Info } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { FoldSection, FormRow, FormSection, FormUnit } from '@/components/ui/form-section'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { KeyValueEditor } from '@/components/ui/key-value-editor'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useToast } from '@/hooks/use-toast'
import { AnimationContext } from '@/lib/animation-context'
import { fetchModelClientTypes, type ModelClientType } from '@/lib/config-api'

import { PROVIDER_TEMPLATES } from '../providerTemplates'
import type { APIProvider, FormErrors } from './types'
import { validateProvider } from './utils'
import '@/styles/provider-form.css'

const PROVIDER_TEMPLATE_OPTIONS = PROVIDER_TEMPLATES.filter((template) => template.id !== 'custom')
const DEFAULT_PROVIDER_TEMPLATE = PROVIDER_TEMPLATES.find(
  (template) => template.id === 'deepseek'
)!

if (!DEFAULT_PROVIDER_TEMPLATE) {
  throw new Error('缺少默认的 DeepSeek 提供商模板')
}

const DEFAULT_PROVIDER_TEMPLATE_ID = DEFAULT_PROVIDER_TEMPLATE.id

interface ProviderFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingProvider: APIProvider | null
  editingIndex: number | null
  providers: APIProvider[]
  onSave: (provider: APIProvider, index: number | null, templateId?: string) => Promise<void> | void
  tourState: { isRunning: boolean }
}

export function ProviderForm({
  open,
  onOpenChange,
  editingProvider,
  editingIndex,
  providers,
  onSave,
  tourState,
}: ProviderFormProps) {
  const [formErrors, setFormErrors] = useState<FormErrors>({})
  const [selectedTemplate, setSelectedTemplate] = useState<string>(DEFAULT_PROVIDER_TEMPLATE_ID)
  const [lastTemplateId, setLastTemplateId] = useState(DEFAULT_PROVIDER_TEMPLATE_ID)
  const [templateComboboxOpen, setTemplateComboboxOpen] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [localProvider, setLocalProvider] = useState<APIProvider | null>(editingProvider)
  const [clientTypes, setClientTypes] = useState<ModelClientType[]>([])
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()
  const animationSettings = useContext(AnimationContext)
  const reducedMotion = useReducedMotion()
  const transitionDuration = reducedMotion || animationSettings?.enableAnimations === false ? 0 : 0.2

  useEffect(() => {
    if (!open) return

    let cancelled = false
    fetchModelClientTypes()
      .then((result) => {
        if (!cancelled) {
          setClientTypes(result || [])
        }
      })
      .catch(() => {
        if (!cancelled) {
          setClientTypes([])
        }
      })

    return () => {
      cancelled = true
    }
  }, [open])

  // 当弹窗打开时，根据当前编辑对象同步一次本地编辑状态
  useEffect(() => {
    if (!open) {
      setLocalProvider(null)
      setFormErrors({})
      setShowApiKey(false)
      setAdvancedOpen(false)
      setSelectedTemplate(DEFAULT_PROVIDER_TEMPLATE_ID)
      setLastTemplateId(DEFAULT_PROVIDER_TEMPLATE_ID)
      setSaving(false)
      return
    }

    setLocalProvider(editingProvider)
    setFormErrors({})
    setShowApiKey(false)
    setAdvancedOpen(false)

    // 编辑时匹配已有配置；新增时默认使用 DeepSeek 模板。
    if (editingIndex !== null && editingProvider) {
      const matchedTemplate = PROVIDER_TEMPLATES.find(
        (template) =>
          template.base_url === editingProvider.base_url &&
          (template.allowed_client_types ?? [template.client_type]).some(
            (clientType) => clientType === editingProvider.client_type
          )
      )
      setSelectedTemplate(matchedTemplate?.id || 'custom')
      if (matchedTemplate?.defaultApiKey) {
        setLocalProvider({ ...editingProvider, api_key: matchedTemplate.defaultApiKey })
      }
      if (matchedTemplate && matchedTemplate.id !== 'custom') {
        setLastTemplateId(matchedTemplate.id)
      }
    } else {
      setSelectedTemplate(DEFAULT_PROVIDER_TEMPLATE_ID)
      setLastTemplateId(DEFAULT_PROVIDER_TEMPLATE_ID)
      setLocalProvider((provider) =>
        provider
          ? {
              ...provider,
              name: DEFAULT_PROVIDER_TEMPLATE.name,
              base_url: DEFAULT_PROVIDER_TEMPLATE.base_url,
              client_type: DEFAULT_PROVIDER_TEMPLATE.client_type,
            }
          : null
      )
    }
  }, [open, editingProvider, editingIndex])

  const isUsingTemplate = useMemo(() => selectedTemplate !== 'custom', [selectedTemplate])
  const isMaiReplier = selectedTemplate === 'mai_replier'
  const selectedTemplateConfig = useMemo(
    () => PROVIDER_TEMPLATES.find((template) => template.id === selectedTemplate),
    [selectedTemplate]
  )
  const isClientTypeLocked = Boolean(
    isUsingTemplate && !selectedTemplateConfig?.allowed_client_types?.length
  )
  const clientTypeOptions = useMemo(() => {
    const options = [...clientTypes]
    const knownTypes = new Set(options.map((item) => item.client_type))

    for (const clientType of ['openai', 'openai_responses', 'gemini', localProvider?.client_type].filter(Boolean) as string[]) {
      if (!knownTypes.has(clientType)) {
        options.push({
          client_type: clientType,
          owner_plugin_id: null,
          version: '',
          description: '',
          builtin: clientType === 'openai' || clientType === 'openai_responses' || clientType === 'gemini',
        })
        knownTypes.add(clientType)
      }
    }

    return options
  }, [clientTypes, localProvider?.client_type])
  const visibleClientTypeOptions = useMemo(() => {
    const allowedClientTypes = selectedTemplateConfig?.allowed_client_types
    if (!allowedClientTypes?.length) return clientTypeOptions

    const allowedClientTypeSet = new Set<string>(allowedClientTypes)
    return clientTypeOptions.filter((item) => allowedClientTypeSet.has(item.client_type))
  }, [clientTypeOptions, selectedTemplateConfig])

  const handleTemplateChange = useCallback((templateId: string) => {
    const template = PROVIDER_TEMPLATES.find(t => t.id === templateId)
    if (!template || template.id === 'custom') return

    setSelectedTemplate(template.id)
    setLastTemplateId(template.id)
    setTemplateComboboxOpen(false)
    setFormErrors({})
    setLocalProvider(prev => ({
      ...prev!,
      name: template.name,
      base_url: template.base_url,
      client_type: template.client_type,
      ...template.providerDefaults,
      api_key: template.defaultApiKey ?? (
        PROVIDER_TEMPLATES.some((item) => item.defaultApiKey === prev?.api_key)
          ? ''
          : prev?.api_key ?? ''
      ),
    }))
  }, [])

  const handleTemplateModeToggle = useCallback(() => {
    if (isUsingTemplate) {
      setLastTemplateId(selectedTemplate)
      setSelectedTemplate('custom')
      setTemplateComboboxOpen(false)
      return
    }

    if (lastTemplateId) {
      handleTemplateChange(lastTemplateId)
    }
  }, [handleTemplateChange, isUsingTemplate, lastTemplateId, selectedTemplate])

  const copyApiKey = useCallback(async () => {
    if (!localProvider?.api_key) return
    try {
      await navigator.clipboard.writeText(localProvider.api_key)
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
  }, [localProvider?.api_key, toast])

  const handleSaveEdit = async () => {
    if (!localProvider) return

    const { isValid, errors } = validateProvider(localProvider, providers, editingIndex)

    if (!isValid) {
      setFormErrors(errors)
      return
    }

    setFormErrors({})
    setSaving(true)
    try {
      if (selectedTemplateConfig?.models) {
        await onSave(localProvider, editingIndex, selectedTemplate)
      } else {
        await onSave(localProvider, editingIndex)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-[95vw] sm:[--dialog-width:50rem]"
        aria-describedby={undefined}
        data-tour="provider-dialog"
        preventOutsideClose={tourState.isRunning}
        confirmOnEnter
      >
        <DialogHeader>
          <DialogTitle>
            {editingIndex !== null ? '编辑提供商' : '添加提供商'}
          </DialogTitle>
        </DialogHeader>

        <form
          onSubmit={(e) => { e.preventDefault(); void handleSaveEdit(); }}
          autoComplete="off"
          className="contents"
        >
          <DialogBody type="auto" data-form-scroll="true" viewportClassName="pr-0">
          <div className="grid gap-5 py-3">
            <FormSection title="连接">
              <FormRow
                label="模板"
                htmlFor="template"
                data-tour="provider-template-select"
                labelExtra={isUsingTemplate && selectedTemplateConfig?.description
                  ? <TemplateInfo description={selectedTemplateConfig.description} />
                  : undefined}
              >
                {/* 切到自定义时模板下拉向左收起、说明淡入，连接信息在原位从锁定变为可填。 */}
                <div data-provider-source={isUsingTemplate ? 'template' : 'custom'}>
                  <div data-provider-source-pick="true" inert={!isUsingTemplate}>
                    <Popover
                      open={isUsingTemplate && templateComboboxOpen}
                      onOpenChange={setTemplateComboboxOpen}
                    >
                      <PopoverTrigger asChild>
                        <Button
                          id="template"
                          variant="outline"
                          role="combobox"
                          aria-expanded={isUsingTemplate && templateComboboxOpen}
                          disabled={!isUsingTemplate}
                          className="w-full min-w-0 justify-between"
                        >
                          <span className="truncate">
                            {isUsingTemplate
                              ? selectedTemplateConfig?.display_name || '选择提供商模板...'
                              : '自定义提供商'}
                          </span>
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
                                {PROVIDER_TEMPLATE_OPTIONS.map((template) => (
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
                                    {template.description && <TemplateInfo description={template.description} />}
                                  </CommandItem>
                                ))}
                              </CommandGroup>
                            </CommandList>
                          </ScrollArea>
                        </Command>
                      </PopoverContent>
                    </Popover>
                  </div>
                  <p data-provider-source-note="true" aria-hidden={isUsingTemplate}>
                    手动填写供应商信息
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    className="shrink-0 whitespace-nowrap"
                    onClick={handleTemplateModeToggle}
                  >
                    {isUsingTemplate ? '使用自定义提供商' : '使用供应商模板'}
                  </Button>
                </div>
                {selectedTemplateConfig?.models && (
                  <p className="text-xs text-muted-foreground">
                    {`已自动配置连接信息，保存后添加全部 ${selectedTemplateConfig.models.length} 个预设模型，仅用于回复模型。`}
                  </p>
                )}
              </FormRow>

              <FormRow
                label="名称"
                htmlFor="name"
                required
                error={formErrors.name}
                data-tour="provider-name-input"
                labelExtra={(
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
                )}
              >
                <Input
                  id="name"
                  value={localProvider?.name || ''}
                  onChange={(e) => {
                    setLocalProvider((prev) =>
                      prev ? { ...prev, name: e.target.value } : null
                    )
                    if (formErrors.name) {
                      setFormErrors((prev) => ({ ...prev, name: undefined }))
                    }
                  }}
                  placeholder="例如: DeepSeek, SiliconFlow"
                  aria-invalid={formErrors.name ? true : undefined}
                  className={formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}
                />
              </FormRow>

              <AnimatePresence initial={false}>
                {!isMaiReplier && (
                  <motion.div
                    key="provider-connection-fields"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: transitionDuration }}
                    className="overflow-hidden"
                    data-provider-connection="true"
                  >
                    <FormRow
                      label="基础 URL"
                      htmlFor="base_url"
                      required
                      error={formErrors.base_url}
                      data-tour="provider-url-input"
                      labelExtra={(
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
                      )}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <Input
                          id="base_url"
                          value={localProvider?.base_url || ''}
                          onChange={(e) => {
                            setLocalProvider((prev) =>
                              prev ? { ...prev, base_url: e.target.value } : null
                            )
                            if (formErrors.base_url) {
                              setFormErrors((prev) => ({ ...prev, base_url: undefined }))
                            }
                          }}
                          placeholder="https://api.example.com/v1"
                          disabled={isUsingTemplate}
                          aria-invalid={formErrors.base_url ? true : undefined}
                          data-provider-locked={isUsingTemplate ? 'true' : undefined}
                          className={`min-w-0 flex-1 ${formErrors.base_url ? 'border-destructive focus-visible:ring-destructive' : ''}`}
                        />
                        <FormUnit data-provider-template-tag={isUsingTemplate ? 'shown' : 'hidden'} aria-hidden={!isUsingTemplate}>
                          随模板
                        </FormUnit>
                      </div>
                    </FormRow>

                    <FormRow
                      label="客户端类型"
                      htmlFor="client_type"
                      labelExtra={(
                        <HelpTooltip
                          content={
                            <div className="space-y-2">
                              <p className="font-medium">API 客户端类型</p>
                              <p>指定与提供商通信时使用的 API 协议格式。</p>
                              <ul className="list-disc list-inside space-y-1 text-xs">
                                <li><strong>OpenAI：</strong>兼容 OpenAI API 格式的提供商</li>
                                <li><strong>OpenAI Responses：</strong>OpenAI Responses API 原生格式</li>
                                <li><strong>Gemini：</strong>Google Gemini 专用格式</li>
                                <li>已加载的插件可以在这里提供新的客户端类型</li>
                              </ul>
                            </div>
                          }
                          side="right"
                          maxWidth="350px"
                        />
                      )}
                    >
                      <Select
                        value={localProvider?.client_type || 'openai'}
                        onValueChange={(value) =>
                          setLocalProvider((prev) =>
                            prev ? { ...prev, client_type: value } : null
                          )
                        }
                        disabled={isClientTypeLocked}
                      >
                        <SelectTrigger id="client_type" data-provider-locked={isClientTypeLocked ? 'true' : undefined}>
                          <SelectValue placeholder="选择客户端类型" />
                        </SelectTrigger>
                        <SelectContent>
                          {visibleClientTypeOptions.map((item) => (
                            <SelectItem key={item.client_type} value={item.client_type}>
                              {item.client_type}
                              {item.owner_plugin_id ? ` (${item.owner_plugin_id})` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormRow>

                    <FormRow
                      label="API Key"
                      htmlFor="api_key"
                      data-tour="provider-apikey-input"
                      labelExtra={(
                        <HelpTooltip
                          content={
                            <div className="space-y-2">
                              <p className="font-medium">API 密钥</p>
                              <p>从提供商平台获取的身份验证密钥。无鉴权服务可留空。</p>
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
                      )}
                    >
                      <div className="flex min-w-0 gap-2">
                        <Input
                          id="api_key"
                          type={showApiKey ? 'text' : 'password'}
                          value={localProvider?.api_key || ''}
                          onChange={(e) => {
                            setLocalProvider((prev) =>
                              prev ? { ...prev, api_key: e.target.value } : null
                            )
                          }}
                          placeholder="sk-..."
                          className="min-w-0 flex-1"
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          className="shrink-0"
                          onClick={() => setShowApiKey(!showApiKey)}
                          title={showApiKey ? '隐藏密钥' : '显示密钥'}
                          aria-label={showApiKey ? '隐藏密钥' : '显示密钥'}
                        >
                          {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          className="shrink-0"
                          onClick={copyApiKey}
                          title="复制密钥"
                          aria-label="复制密钥"
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                      </div>
                    </FormRow>
                  </motion.div>
                )}
              </AnimatePresence>
            </FormSection>

            <FoldSection title="高级配置" open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <FormRow label="请求策略" alignTop>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  {([
                    ['max_retry', '最大重试', '次', '0', '默认 3', '单个模型 API 请求的最大尝试次数（含首次请求），失败后按重试间隔重试。设置为 0 或 1 表示不重试。默认值：3'],
                    ['timeout', '超时', '秒', '1', '默认 30', '单次 API 请求的超时时间（秒）。超时后会触发重试或报错。默认值：30 秒'],
                    ['retry_interval', '重试间隔', '秒', '1', '默认 10', '两次重试之间的等待时间（秒）。适当的间隔可以避免触发 API 限流。默认值：10 秒'],
                  ] as const).map(([field, label, unit, min, placeholder, help]) => (
                    <div key={field} className="grid min-w-0 gap-1">
                      <div className="flex items-center gap-1">
                        <Label htmlFor={field} className="text-xs text-muted-foreground">{label}</Label>
                        <HelpTooltip content={help} side="top" maxWidth="250px" />
                      </div>
                      <div className="flex min-w-0 items-center gap-2">
                        <Input
                          id={field}
                          type="number"
                          min={min}
                          className="min-w-0 flex-1"
                          value={localProvider?.[field] ?? ''}
                          onChange={(e) => {
                            const val = e.target.value === '' ? null : parseInt(e.target.value)
                            setLocalProvider((prev) =>
                              prev ? { ...prev, [field]: val } : null
                            )
                          }}
                          placeholder={placeholder}
                        />
                        <FormUnit>{unit}</FormUnit>
                      </div>
                    </div>
                  ))}
                </div>
              </FormRow>

              <FormRow
                label="请求头"
                alignTop
                labelExtra={(
                  <HelpTooltip
                    content="该提供商的每一次请求都会携带这些 HTTP 请求头。请求头名称和值均为字符串。"
                    side="top"
                    maxWidth="280px"
                  />
                )}
              >
                <KeyValueEditor
                  value={localProvider?.default_headers ?? {}}
                  onChange={(headers) => {
                    const normalizedHeaders = Object.fromEntries(
                      Object.entries(headers).map(([key, value]) => [key, String(value)])
                    )
                    setLocalProvider((prev) =>
                      prev ? { ...prev, default_headers: normalizedHeaders } : null
                    )
                  }}
                  placeholder="添加请求头，例如 HTTP-Referer"
                  className="min-h-32"
                />
              </FormRow>
            </FoldSection>
          </div>
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} data-tour="provider-cancel-button">
              取消
            </Button>
            <Button
              type="submit"
              data-dialog-action="confirm"
              data-tour="provider-save-button"
              disabled={saving}
            >
              {saving ? '保存中...' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function TemplateInfo({ description }: { description: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span tabIndex={0} className="ml-1 inline-flex cursor-help text-muted-foreground" aria-label={description}>
            <Info className="h-4 w-4" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="z-[70] max-w-xs whitespace-pre-line">{description}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
