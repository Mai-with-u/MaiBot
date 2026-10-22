import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { getBotConfig } from '@/lib/config-api'

import type { ModelInfo } from '../model/types'
import type { APIProvider } from '../modelProvider/types'
import type { WebAdapter, WebSearchConfig, WebServiceConfig } from './types'

interface Props {
  config: WebSearchConfig
  providers: APIProvider[]
  models: ModelInfo[]
  editingName: string | null
  assignedService: string
  onClose: () => void
  onSave: (config: WebSearchConfig) => Promise<void>
}

const ADAPTER_LABELS: Record<WebAdapter, string> = {
  deepseek: 'DeepSeek',
  tavily: 'Tavily',
  stepsearch_mcp: 'StepSearch MCP',
}

function isDeepSeekProvider(provider: APIProvider, models: ModelInfo[]): boolean {
  if (/deepseek|(?:^|[\s_-])ds(?:\d|$|[\s_-])/i.test(provider.name)) return true
  if (
    models.some(
      (model) => model.api_provider === provider.name && /deepseek/i.test(model.model_identifier)
    )
  )
    return true
  try {
    const hostname = new URL(provider.base_url).hostname.toLowerCase()
    return hostname === 'deepseek.com' || hostname.endsWith('.deepseek.com')
  } catch {
    return false
  }
}

function normalizeDeepSeekUrl(value: string): string {
  return (value.trim() || 'https://api.deepseek.com').replace(/\/+$/, '').replace(/\/v1$/, '')
}

export function WebSearchDialog({
  config,
  providers,
  models,
  editingName,
  assignedService,
  onClose,
  onSave,
}: Props) {
  const initial = editingName ? config.services[editingName] : undefined
  const isAssigned = Boolean(editingName && assignedService === editingName)
  const deepSeekProviders = providers.filter((provider) => isDeepSeekProvider(provider, models))
  // 编辑时按实际连接信息匹配，避免将已保存的自定义地址切换到其他提供商。
  const initialProvider = initial
    ? initial.adapter === 'deepseek'
      ? deepSeekProviders.find(
          (provider) =>
            provider.api_key === initial.api_key &&
            normalizeDeepSeekUrl(provider.base_url) === normalizeDeepSeekUrl(initial.base_url ?? '')
        )
      : undefined
    : deepSeekProviders[0]
  const [connectionSource, setConnectionSource] = useState(
    initialProvider ? `provider:${initialProvider.name}` : 'custom'
  )
  const [name, setName] = useState(editingName ?? '')
  const [service, setService] = useState<WebServiceConfig>({
    adapter: 'deepseek',
    timeout: 60,
    api_key: initialProvider?.api_key ?? '',
    base_url: initialProvider?.base_url ?? '',
    ...initial,
  })
  const [servers, setServers] = useState<string[]>([])
  const [mcpNotice, setMcpNotice] = useState('')
  const [loadingServers, setLoadingServers] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    if (service.adapter !== 'stepsearch_mcp') return
    let cancelled = false
    setLoadingServers(true)
    setMcpNotice('')
    getBotConfig()
      .then((bot) => {
        if (cancelled) return
        const mcp = bot.mcp as
          | { enable?: boolean; servers?: { name: string; enabled?: boolean }[] }
          | undefined
        setServers(
          (mcp?.servers ?? [])
            .filter((server) => server.enabled !== false)
            .map((server) => server.name)
        )
        if (!mcp?.enable) setMcpNotice('MCP 当前未启用，请先在插件扩展中启用对应连接。')
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setMcpNotice(
            cause instanceof Error ? cause.message : '无法加载 MCP 连接，可手动填写连接名称。'
          )
      })
      .finally(() => {
        if (!cancelled) setLoadingServers(false)
      })
    return () => {
      cancelled = true
    }
  }, [service.adapter])

  const patch = (value: Partial<WebServiceConfig>) =>
    setService((current) => ({ ...current, ...value }))

  async function persist(next: WebSearchConfig) {
    setSaving(true)
    setError('')
    try {
      await onSave(next)
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存联网服务失败')
    } finally {
      setSaving(false)
    }
  }

  function save() {
    const nextName = name.trim()
    if (!nextName) {
      setError('请填写服务名称')
      return
    }
    if (nextName !== editingName && Object.hasOwn(config.services, nextName)) {
      setError('服务名称已存在，请使用其他名称')
      return
    }
    if (
      service.adapter === 'stepsearch_mcp' ? !service.server_ref?.trim() : !service.api_key?.trim()
    ) {
      setError(
        service.adapter === 'stepsearch_mcp'
          ? '请选择或填写已有 MCP 连接名称'
          : service.adapter === 'deepseek' && connectionSource !== 'custom'
            ? '所选提供商尚未配置 API Key，请完善提供商配置或选择“自定义”'
            : '请填写 API Key'
      )
      return
    }
    if (!Number.isFinite(service.timeout) || service.timeout! <= 0 || service.timeout! > 300) {
      setError('超时时间应大于 0 且不超过 300 秒')
      return
    }
    if (service.adapter !== 'stepsearch_mcp' && service.base_url?.trim()) {
      try {
        const url = new URL(service.base_url.trim())
        if (
          !['http:', 'https:'].includes(url.protocol) ||
          url.username ||
          url.password ||
          url.search ||
          url.hash
        ) {
          throw new Error('invalid URL')
        }
      } catch {
        setError('API 地址须为 HTTP(S) 地址，不能包含用户名、密码、查询参数或片段')
        return
      }
    }
    const services = Object.fromEntries(
      Object.entries(config.services).filter(([key]) => key !== editingName)
    )
    const cleaned: WebServiceConfig =
      service.adapter === 'stepsearch_mcp'
        ? {
            adapter: service.adapter,
            server_ref: service.server_ref!.trim(),
            timeout: service.timeout,
            category: service.category ?? '',
          }
        : {
            adapter: service.adapter,
            api_key: service.api_key!.trim(),
            base_url: service.base_url?.trim() ?? '',
            timeout: service.timeout,
            ...(service.adapter === 'tavily'
              ? {
                  search_depth: service.search_depth ?? 'basic',
                  extract_depth: service.extract_depth ?? 'basic',
                }
              : {}),
          }
    const next = {
      ...config,
      services: { ...services, [nextName]: cleaned },
    }
    void persist(next)
  }

  function remove() {
    void persist({
      ...config,
      services: Object.fromEntries(
        Object.entries(config.services).filter(([key]) => key !== editingName)
      ),
    })
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose()
      }}
    >
      <DialogContent className="sm:max-w-lg" preventOutsideClose>
        <DialogHeader>
          <DialogTitle>{editingName ? '编辑联网搜索' : '添加联网搜索'}</DialogTitle>
          <DialogDescription>
            保存连接后，需在“功能分配 → 联网搜索”中选用才能使用。
          </DialogDescription>
        </DialogHeader>
        <DialogBody viewportClassName="max-h-[65vh] pr-3">
          <fieldset disabled={saving} className="space-y-4 py-1">
            <div className="grid gap-2">
              <Label htmlFor="web-service-name">服务名称</Label>
              <Input
                id="web-service-name"
                disabled={isAssigned}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：日常搜索"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="web-service-adapter">服务商</Label>
              <Select
                value={service.adapter}
                disabled={saving}
                onValueChange={(adapter: WebAdapter) => {
                  const provider = adapter === 'deepseek' ? deepSeekProviders[0] : undefined
                  setConnectionSource(provider ? `provider:${provider.name}` : 'custom')
                  setService({
                    adapter,
                    timeout: service.timeout,
                    api_key: provider?.api_key ?? '',
                    base_url: provider?.base_url ?? '',
                    server_ref: '',
                  })
                }}
              >
                <SelectTrigger id="web-service-adapter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ADAPTER_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {service.adapter === 'deepseek' && (
              <div className="grid gap-2">
                <Label htmlFor="web-deepseek-source">连接配置</Label>
                <Select
                  value={connectionSource}
                  disabled={saving}
                  onValueChange={(value) => {
                    setConnectionSource(value)
                    const provider = deepSeekProviders.find(
                      (item) => `provider:${item.name}` === value
                    )
                    if (provider) patch({ api_key: provider.api_key, base_url: provider.base_url })
                  }}
                >
                  <SelectTrigger id="web-deepseek-source">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {deepSeekProviders.map((provider) => (
                      <SelectItem key={provider.name} value={`provider:${provider.name}`}>
                        {provider.name}
                      </SelectItem>
                    ))}
                    <SelectItem value="custom">自定义</SelectItem>
                  </SelectContent>
                </Select>
                {connectionSource !== 'custom' ? (
                  <p className="text-muted-foreground text-xs break-all">
                    {service.base_url || 'https://api.deepseek.com'}
                    <br />
                    将所选组当前的地址和密钥保存到联网配置，后续独立维护。
                  </p>
                ) : (
                  deepSeekProviders.length === 0 && (
                    <p className="text-muted-foreground text-xs">
                      未检测到 DeepSeek 提供商，请填写自定义连接。
                    </p>
                  )
                )}
              </div>
            )}
            {service.adapter === 'stepsearch_mcp' ? (
              <div className="grid gap-2">
                <Label htmlFor="web-service-server">已有 MCP 连接</Label>
                <Input
                  id="web-service-server"
                  list="web-mcp-servers"
                  value={service.server_ref ?? ''}
                  onChange={(event) => patch({ server_ref: event.target.value })}
                  placeholder="例如：stepsearch"
                />
                <datalist id="web-mcp-servers">
                  {servers.map((server) => (
                    <option key={server} value={server} />
                  ))}
                </datalist>
                <p className="text-muted-foreground text-xs">
                  复用插件扩展中的 StepSearch 连接，无需重复填写密钥。
                </p>
                {loadingServers && <p className="text-muted-foreground text-xs">正在加载连接…</p>}
                {mcpNotice && (
                  <p className="text-xs text-amber-600" role="status">
                    {mcpNotice}
                  </p>
                )}
              </div>
            ) : (
              (service.adapter !== 'deepseek' || connectionSource === 'custom') && (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor="web-service-key">API Key</Label>
                    <Input
                      id="web-service-key"
                      type="password"
                      autoComplete="new-password"
                      value={service.api_key ?? ''}
                      onChange={(event) => patch({ api_key: event.target.value })}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="web-service-url">API 地址</Label>
                    <Input
                      id="web-service-url"
                      value={service.base_url ?? ''}
                      onChange={(event) => patch({ base_url: event.target.value })}
                      placeholder={
                        service.adapter === 'deepseek'
                          ? 'https://api.deepseek.com'
                          : 'https://api.tavily.com'
                      }
                    />
                    <p className="text-muted-foreground text-xs">留空使用官方地址。</p>
                  </div>
                </>
              )
            )}
            {isAssigned && (
              <p className="text-muted-foreground text-xs">
                该服务正在用于联网搜索。修改名称或删除前，请先在功能分配中取消选用。
              </p>
            )}
            <details className="rounded-md border p-3">
              <summary className="cursor-pointer text-sm font-medium">高级选项</summary>
              <div className="mt-3 grid gap-3">
                <Label htmlFor="web-timeout">请求超时（秒）</Label>
                <Input
                  id="web-timeout"
                  type="number"
                  min={1}
                  max={300}
                  value={service.timeout ?? 60}
                  onChange={(event) => patch({ timeout: event.target.valueAsNumber })}
                />
                {service.adapter === 'tavily' && (
                  <>
                    <Label htmlFor="web-search-depth">搜索深度</Label>
                    <Select
                      value={service.search_depth ?? 'basic'}
                      disabled={saving}
                      onValueChange={(value) =>
                        patch({ search_depth: value as WebServiceConfig['search_depth'] })
                      }
                    >
                      <SelectTrigger id="web-search-depth">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="basic">标准</SelectItem>
                        <SelectItem value="advanced">深入</SelectItem>
                        <SelectItem value="fast">快速</SelectItem>
                        <SelectItem value="ultra-fast">极速</SelectItem>
                      </SelectContent>
                    </Select>
                    <Label htmlFor="web-extract-depth">网页提取深度</Label>
                    <Select
                      value={service.extract_depth ?? 'basic'}
                      disabled={saving}
                      onValueChange={(value) =>
                        patch({ extract_depth: value as WebServiceConfig['extract_depth'] })
                      }
                    >
                      <SelectTrigger id="web-extract-depth">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="basic">标准</SelectItem>
                        <SelectItem value="advanced">深入</SelectItem>
                      </SelectContent>
                    </Select>
                  </>
                )}
              </div>
            </details>
            {error && (
              <p className="text-destructive text-sm" role="alert">
                {error}
              </p>
            )}
            {confirmDelete && (
              <div className="border-destructive/40 space-y-2 rounded-md border p-3">
                <p className="text-sm">确定删除“{editingName}”？</p>
                <div className="flex gap-2">
                  <Button variant="destructive" size="sm" onClick={remove}>
                    确认删除
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setConfirmDelete(false)}>
                    取消删除
                  </Button>
                </div>
              </div>
            )}
          </fieldset>
        </DialogBody>
        <DialogFooter>
          {editingName && (
            <Button
              variant="ghost"
              className="text-destructive sm:mr-auto"
              disabled={saving || isAssigned}
              onClick={() => setConfirmDelete(true)}
            >
              删除
            </Button>
          )}
          <Button variant="outline" disabled={saving} onClick={onClose}>
            取消
          </Button>
          <Button disabled={saving} onClick={save} data-dialog-action="confirm">
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
