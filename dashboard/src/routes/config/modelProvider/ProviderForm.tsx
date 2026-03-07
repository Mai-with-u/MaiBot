import { useCallback, useMemo, useState } from 'react'
import { Check, ChevronsUpDown, Copy, Eye, EyeOff } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'

import { PROVIDER_TEMPLATES } from '../providerTemplates'
import type { APIProvider, FormErrors } from './types'
import { validateProvider } from './utils'

interface ProviderFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingProvider: APIProvider | null
  editingIndex: number | null
  providers: APIProvider[]
  onSave: (provider: APIProvider, index: number | null) => void
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
  const [selectedTemplate, setSelectedTemplate] = useState<string>('custom')
  const [templateComboboxOpen, setTemplateComboboxOpen] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [localProvider, setLocalProvider] = useState<APIProvider | null>(editingProvider)
  const { toast } = useToast()

  // 同步外部状态到本地
  if (editingProvider !== localProvider && open) {
    setLocalProvider(editingProvider)
    setFormErrors({})
    setShowApiKey(false)

    // 检测匹配的模板
    if (editingProvider) {
      const matchedTemplate = PROVIDER_TEMPLATES.find(
        t => t.base_url === editingProvider.base_url && t.client_type === editingProvider.client_type
      )
      setSelectedTemplate(matchedTemplate?.id || 'custom')
    } else {
      setSelectedTemplate('custom')
    }
  }

  const isUsingTemplate = useMemo(() => selectedTemplate !== 'custom', [selectedTemplate])

  const handleTemplateChange = useCallback((templateId: string) => {
    setSelectedTemplate(templateId)
    setTemplateComboboxOpen(false)
    const template = PROVIDER_TEMPLATES.find(t => t.id === templateId)
    if (template && template.id !== 'custom') {
      setLocalProvider(prev => ({
        ...prev!,
        name: template.name,
        base_url: template.base_url,
        client_type: template.client_type,
      }))
    } else if (template?.id === 'custom') {
      setLocalProvider(prev => ({
        ...prev!,
        name: '',
        base_url: '',
        client_type: 'openai',
      }))
    }
  }, [])

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

  const handleSaveEdit = () => {
    if (!localProvider) return

    const { isValid, errors } = validateProvider(localProvider, providers, editingIndex)

    if (!isValid) {
      setFormErrors(errors)
      return
    }

    setFormErrors({})
    onSave(localProvider, editingIndex)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
                aria-describedby={formErrors.name ? 'name-error' : undefined}
                className={formErrors.name ? 'border-destructive focus-visible:ring-destructive' : ''}
              />
              {formErrors.name && (
                <p id="name-error" role="alert" className="text-xs text-destructive">{formErrors.name}</p>
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
                aria-describedby={formErrors.base_url ? 'base-url-error' : undefined}
                className={`${isUsingTemplate ? 'bg-muted cursor-not-allowed' : ''} ${formErrors.base_url ? 'border-destructive focus-visible:ring-destructive' : ''}`}
              />
              {formErrors.base_url && (
                <p id="base-url-error" role="alert" className="text-xs text-destructive">{formErrors.base_url}</p>
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
                  value={localProvider?.api_key || ''}
                  onChange={(e) => {
                    setLocalProvider((prev) =>
                      prev ? { ...prev, api_key: e.target.value } : null
                    )
                    if (formErrors.api_key) {
                      setFormErrors((prev) => ({ ...prev, api_key: undefined }))
                    }
                  }}
                  placeholder="sk-..."
                  aria-invalid={formErrors.api_key ? true : undefined}
                  aria-describedby={formErrors.api_key ? 'api-key-error' : undefined}
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
                <p id="api-key-error" role="alert" className="text-xs text-destructive">{formErrors.api_key}</p>
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
                value={localProvider?.client_type || 'openai'}
                onValueChange={(value) =>
                  setLocalProvider((prev) =>
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
                  value={localProvider?.max_retry ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setLocalProvider((prev) =>
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
                  value={localProvider?.timeout ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setLocalProvider((prev) =>
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
                  value={localProvider?.retry_interval ?? ''}
                  onChange={(e) => {
                    const val = e.target.value === '' ? null : parseInt(e.target.value)
                    setLocalProvider((prev) =>
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
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} data-tour="provider-cancel-button">
              取消
            </Button>
            <Button type="submit" data-tour="provider-save-button">保存</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
