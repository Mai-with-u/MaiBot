import { useCallback, useEffect, useMemo, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { KeyValueEditor } from '@/components/ui/key-value-editor'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { DynamicConfigForm } from '@/components/dynamic-form'
import { useConfigForm } from '@/hooks/useConfigForm'
import { useToast } from '@/hooks/use-toast'
import { getBotConfig, getBotConfigSchema, updateBotConfigSection } from '@/lib/config-api'
import { fieldHooks } from '@/lib/field-hooks'
import type { FieldHookComponent } from '@/lib/field-hooks'
import { generateId } from '@/lib/id'
import {
  getMCPStatus,
  testMCPConnection,
  type MCPConnectionTestResponse,
  type MCPStatusResponse,
} from '@/lib/mcp-api'
import type { ConfigSchema } from '@/types/config-schema'
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  Info,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Server,
  Settings2,
  TestTube2,
  Trash2,
} from 'lucide-react'

type ConfigSectionData = Record<string, unknown>
type MCPTransport = 'stdio' | 'streamable_http' | 'sse'

interface MCPAuthorization {
  mode: 'none' | 'bearer'
  bearer_token: string
}

interface MCPServerConfig {
  [key: string]: unknown
  _uuid?: string
  name: string
  enabled: boolean
  transport: MCPTransport
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
  http_timeout_seconds: number
  read_timeout_seconds: number
  authorization: MCPAuthorization
}

interface MCPRootConfig {
  enabled: boolean
  uri: string
  name: string
}

const DEFAULT_MCP_SERVER: MCPServerConfig = {
  name: '',
  enabled: true,
  transport: 'stdio',
  command: '',
  args: [],
  env: {},
  url: '',
  headers: {},
  http_timeout_seconds: 30,
  read_timeout_seconds: 300,
  authorization: {
    mode: 'none',
    bearer_token: '',
  },
}

function asStringMap(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, itemValue]) => [
      key,
      String(itemValue ?? ''),
    ])
  )
}

function normalizeMCPServer(value: unknown, index: number): MCPServerConfig {
  const source =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const auth =
    source.authorization &&
    typeof source.authorization === 'object' &&
    !Array.isArray(source.authorization)
      ? (source.authorization as Record<string, unknown>)
      : {}
  const transport: MCPTransport =
    source.transport === 'streamable_http' || source.transport === 'sse'
      ? source.transport
      : 'stdio'

  return {
    ...DEFAULT_MCP_SERVER,
    ...source,
    _uuid: typeof source._uuid === 'string' ? source._uuid : generateId(),
    name: typeof source.name === 'string' ? source.name : `mcp-server-${index + 1}`,
    enabled: typeof source.enabled === 'boolean' ? source.enabled : DEFAULT_MCP_SERVER.enabled,
    transport,
    command: typeof source.command === 'string' ? source.command : '',
    args: Array.isArray(source.args) ? source.args.map((item) => String(item ?? '')) : [],
    env: asStringMap(source.env),
    url: typeof source.url === 'string' ? source.url : '',
    headers: asStringMap(source.headers),
    http_timeout_seconds:
      typeof source.http_timeout_seconds === 'number'
        ? source.http_timeout_seconds
        : DEFAULT_MCP_SERVER.http_timeout_seconds,
    read_timeout_seconds:
      typeof source.read_timeout_seconds === 'number'
        ? source.read_timeout_seconds
        : DEFAULT_MCP_SERVER.read_timeout_seconds,
    authorization: {
      mode: auth.mode === 'bearer' ? 'bearer' : 'none',
      bearer_token: typeof auth.bearer_token === 'string' ? auth.bearer_token : '',
    },
  }
}

function normalizeMCPServers(value: unknown): MCPServerConfig[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.map((item, index) => normalizeMCPServer(item, index))
}

function normalizeMCPRoots(value: unknown): MCPRootConfig[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => {
    const source =
      item && typeof item === 'object' && !Array.isArray(item)
        ? (item as Record<string, unknown>)
        : {}
    return {
      enabled: typeof source.enabled === 'boolean' ? source.enabled : true,
      uri: typeof source.uri === 'string' ? source.uri : '',
      name: typeof source.name === 'string' ? source.name : '',
    }
  })
}

function validateMCPServer(
  server: MCPServerConfig,
  servers: MCPServerConfig[],
  index: number,
  includeDisabled = false
): string[] {
  if (!server.enabled && !includeDisabled) {
    return []
  }

  const errors: string[] = []
  const name = server.name.trim()
  if (!name) {
    errors.push('请填写服务名称')
  } else if (
    servers.some(
      (item, itemIndex) => itemIndex !== index && item.enabled && item.name.trim() === name
    )
  ) {
    errors.push('服务名称必须唯一')
  }

  if (server.transport === 'stdio' && !server.command.trim()) {
    errors.push('stdio 模式必须填写启动命令')
  }
  if (server.transport !== 'stdio') {
    if (!server.url.trim()) {
      errors.push('远程传输必须填写服务 URL')
    } else {
      try {
        const url = new URL(server.url)
        if (!['http:', 'https:'].includes(url.protocol)) {
          errors.push('服务 URL 必须使用 http 或 https')
        }
      } catch {
        errors.push('服务 URL 格式不正确')
      }
    }
  }
  if (server.authorization.mode === 'bearer' && !server.authorization.bearer_token.trim()) {
    errors.push('Bearer 认证必须填写 Token')
  }
  return errors
}

const MCPRootItemsEditor: FieldHookComponent = ({ value, onChange }) => {
  const roots = normalizeMCPRoots(value)

  const updateRoot = (index: number, patch: Partial<MCPRootConfig>) => {
    onChange?.(roots.map((root, rootIndex) => (rootIndex === index ? { ...root, ...patch } : root)))
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">允许访问的 Roots</p>
          <p className="text-xs text-muted-foreground">只向 MCP 服务暴露确实需要访问的目录。</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onChange?.([...roots, { enabled: false, uri: '', name: '' }])}
        >
          <Plus className="mr-1 h-4 w-4" />
          添加 Root
        </Button>
      </div>
      {roots.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
          尚未暴露任何 Root。
        </div>
      ) : (
        roots.map((root, index) => (
          <div
            key={`${root.uri}-${index}`}
            className="grid gap-2 rounded-md border bg-muted/20 p-3 md:grid-cols-[auto_1fr_1fr_auto]"
          >
            <Switch
              checked={root.enabled}
              onCheckedChange={(enabled) => updateRoot(index, { enabled })}
              aria-label={`启用 Root ${index + 1}`}
            />
            <Input
              value={root.name}
              onChange={(event) => updateRoot(index, { name: event.target.value })}
              placeholder="显示名称，例如 project"
            />
            <Input
              value={root.uri}
              onChange={(event) => updateRoot(index, { uri: event.target.value })}
              placeholder="file:///path/to/project"
              aria-invalid={root.enabled && !root.uri.trim()}
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="text-destructive hover:text-destructive"
              onClick={() => onChange?.(roots.filter((_, rootIndex) => rootIndex !== index))}
              title="删除 Root"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            {root.enabled && !root.uri.trim() && (
              <p className="text-xs text-destructive md:col-start-3">启用的 Root 必须填写 URI</p>
            )}
          </div>
        ))
      )}
    </div>
  )
}

function updateNestedValue(
  target: ConfigSectionData | null | undefined,
  pathSegments: string[],
  value: unknown
): ConfigSectionData {
  const currentTarget = target && typeof target === 'object' && !Array.isArray(target) ? target : {}
  const [currentPath, ...restPath] = pathSegments

  if (!currentPath) {
    return currentTarget
  }

  if (restPath.length === 0) {
    return {
      ...currentTarget,
      [currentPath]: value,
    }
  }

  return {
    ...currentTarget,
    [currentPath]: updateNestedValue(
      currentTarget[currentPath] as ConfigSectionData | undefined,
      restPath,
      value
    ),
  }
}

function MCPServersBlockEditor({
  servers,
  onChange,
  runtimeStatus,
}: {
  servers: MCPServerConfig[]
  onChange: (servers: MCPServerConfig[]) => void
  runtimeStatus?: MCPStatusResponse
}) {
  const [testStates, setTestStates] = useState<
    Record<
      string,
      {
        testing: boolean
        result?: MCPConnectionTestResponse
        error?: string
      }
    >
  >({})

  const serverKey = (server: MCPServerConfig, index: number) =>
    server._uuid || `${server.name}-${index}`

  const updateServer = (index: number, patch: Partial<MCPServerConfig>) => {
    onChange(
      servers.map((server, serverIndex) =>
        serverIndex === index ? { ...server, ...patch } : server
      )
    )
  }

  const updateAuthorization = (index: number, patch: Partial<MCPAuthorization>) => {
    const server = servers[index]
    if (!server) {
      return
    }
    updateServer(index, {
      authorization: {
        ...server.authorization,
        ...patch,
      },
    })
  }

  const addServer = () => {
    onChange([
      ...servers,
      {
        ...DEFAULT_MCP_SERVER,
        _uuid: generateId(),
        name: `mcp-server-${servers.length + 1}`,
      },
    ])
  }

  const duplicateServer = (index: number) => {
    const server = servers[index]
    if (!server) {
      return
    }
    const nextServer = {
      ...server,
      _uuid: generateId(),
      name: `${server.name || 'mcp-server'}-copy`,
      args: [...server.args],
      env: { ...server.env },
      headers: { ...server.headers },
      authorization: { ...server.authorization },
    }
    onChange([...servers.slice(0, index + 1), nextServer, ...servers.slice(index + 1)])
  }

  const removeServer = (index: number) => {
    onChange(servers.filter((_, serverIndex) => serverIndex !== index))
  }

  const testServer = async (server: MCPServerConfig, index: number) => {
    const key = serverKey(server, index)
    setTestStates((current) => ({
      ...current,
      [key]: { testing: true },
    }))
    try {
      const payload = { ...server }
      delete payload._uuid
      const result = await testMCPConnection(payload)
      setTestStates((current) => ({
        ...current,
        [key]: { testing: false, result },
      }))
    } catch (error) {
      setTestStates((current) => ({
        ...current,
        [key]: {
          testing: false,
          error: error instanceof Error ? error.message : '连接测试失败',
        },
      }))
    }
  }

  return (
    <Card>
      <CardHeader className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-muted-foreground" />
              <CardTitle className="text-lg">MCP 服务</CardTitle>
              <Badge variant="secondary" className="text-xs">
                {servers.length} 个
              </Badge>
            </div>
            <CardDescription>
              这里会写入 mcp.servers。stdio 用命令启动本地服务，streamable_http 连接远程 MCP 端点。
            </CardDescription>
          </div>
          <Button type="button" size="sm" onClick={addServer}>
            <Plus className="mr-1 h-4 w-4" />
            添加服务
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {servers.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
            尚未配置 MCP 服务。添加一个服务后，MaiSaka 可以调用它暴露的工具。
          </div>
        ) : (
          servers.map((server, index) => {
            const key = serverKey(server, index)
            const errors = validateMCPServer(server, servers, index)
            const testErrors = validateMCPServer(server, servers, index, true)
            const testState = testStates[key]
            const currentStatus = runtimeStatus?.servers.find((item) => item.name === server.name)

            return (
              <Card key={key} className="border-border/70 bg-muted/20 shadow-none">
                <CardHeader className="space-y-3 px-4 py-3">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <Switch
                        checked={server.enabled}
                        onCheckedChange={(enabled) => updateServer(index, { enabled })}
                      />
                      <div className="min-w-0 flex-1">
                        <Input
                          value={server.name}
                          onChange={(event) => updateServer(index, { name: event.target.value })}
                          placeholder="服务名称，必须唯一"
                          className="h-8 font-medium"
                        />
                      </div>
                      <Badge
                        variant={server.enabled ? 'default' : 'secondary'}
                        className="shrink-0 text-[10px]"
                      >
                        {server.enabled ? '启用' : '禁用'}
                      </Badge>
                      {currentStatus && (
                        <Badge
                          variant={currentStatus.connected ? 'secondary' : 'destructive'}
                          className="shrink-0 text-[10px]"
                          title={currentStatus.error || undefined}
                        >
                          {currentStatus.connected
                            ? `已连接 · ${currentStatus.tool_count} 工具`
                            : '连接异常'}
                        </Badge>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={testState?.testing || testErrors.length > 0}
                        onClick={() => void testServer(server, index)}
                        title={testErrors[0] || '测试连接并发现工具'}
                      >
                        {testState?.testing ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <TestTube2 className="mr-1 h-4 w-4" />
                        )}
                        测试
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => duplicateServer(index)}
                        title="复制服务"
                      >
                        <Copy className="h-4 w-4" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:text-destructive"
                        onClick={() => removeServer(index)}
                        title="删除服务"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4 px-4 pb-4 pt-0">
                  <div className="grid gap-3 md:grid-cols-[12rem_1fr]">
                    <div className="space-y-1.5">
                      <span className="text-xs font-medium text-muted-foreground">传输方式</span>
                      <Select
                        value={server.transport}
                        onValueChange={(transport) =>
                          updateServer(index, { transport: transport as MCPTransport })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="stdio">本地命令（stdio）</SelectItem>
                          <SelectItem value="streamable_http">远程 HTTP</SelectItem>
                          <SelectItem value="sse">旧版 SSE</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    {server.transport === 'stdio' ? (
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">启动命令</span>
                        <Input
                          value={server.command}
                          onChange={(event) => updateServer(index, { command: event.target.value })}
                          placeholder="例如 uvx、npx、python"
                        />
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">服务 URL</span>
                        <Input
                          value={server.url}
                          onChange={(event) => updateServer(index, { url: event.target.value })}
                          placeholder="https://example.com/mcp"
                        />
                      </div>
                    )}
                  </div>

                  {server.transport === 'stdio' ? (
                    <div className="grid gap-3 lg:grid-cols-2">
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">命令参数</span>
                        <Textarea
                          value={server.args.join('\n')}
                          onChange={(event) =>
                            updateServer(index, {
                              args: event.target.value
                                .split('\n')
                                .map((line) => line.trim())
                                .filter((line) => line.length > 0),
                            })
                          }
                          rows={4}
                          placeholder="每行一个参数"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">环境变量</span>
                        <KeyValueEditor
                          value={server.env}
                          onChange={(env) => updateServer(index, { env: asStringMap(env) })}
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className="grid gap-3 md:grid-cols-2">
                        <div className="space-y-1.5">
                          <span className="text-xs font-medium text-muted-foreground">
                            认证模式
                          </span>
                          <Select
                            value={server.authorization.mode}
                            onValueChange={(mode) =>
                              updateAuthorization(index, { mode: mode as MCPAuthorization['mode'] })
                            }
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">none</SelectItem>
                              <SelectItem value="bearer">bearer</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        {server.authorization.mode === 'bearer' && (
                          <div className="space-y-1.5">
                            <span className="text-xs font-medium text-muted-foreground">
                              Bearer Token
                            </span>
                            <Input
                              type="password"
                              value={server.authorization.bearer_token}
                              onChange={(event) =>
                                updateAuthorization(index, { bearer_token: event.target.value })
                              }
                              placeholder="HTTP Bearer Token"
                            />
                          </div>
                        )}
                      </div>
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">
                          请求 Headers
                        </span>
                        <KeyValueEditor
                          value={server.headers}
                          onChange={(headers) =>
                            updateServer(index, { headers: asStringMap(headers) })
                          }
                        />
                      </div>
                    </div>
                  )}

                  <div
                    className={`grid gap-3 ${server.transport === 'stdio' ? '' : 'md:grid-cols-2'}`}
                  >
                    {server.transport !== 'stdio' && (
                      <div className="space-y-1.5">
                        <span className="text-xs font-medium text-muted-foreground">
                          HTTP 请求超时（秒）
                        </span>
                        <Input
                          type="number"
                          min={0.1}
                          step={0.1}
                          value={server.http_timeout_seconds}
                          onChange={(event) =>
                            updateServer(index, {
                              http_timeout_seconds: Number.parseFloat(event.target.value) || 0.1,
                            })
                          }
                        />
                      </div>
                    )}
                    <div className="space-y-1.5">
                      <span className="text-xs font-medium text-muted-foreground">
                        会话读取超时（秒）
                      </span>
                      <Input
                        type="number"
                        min={0.1}
                        step={0.1}
                        value={server.read_timeout_seconds}
                        onChange={(event) =>
                          updateServer(index, {
                            read_timeout_seconds: Number.parseFloat(event.target.value) || 0.1,
                          })
                        }
                      />
                    </div>
                  </div>

                  {errors.length > 0 && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                      {errors.join('；')}
                    </div>
                  )}

                  {(testState?.result || testState?.error) && (
                    <div
                      className={`rounded-md border px-3 py-2 text-xs ${
                        testState.result?.success
                          ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300'
                          : 'border-destructive/40 bg-destructive/5 text-destructive'
                      }`}
                    >
                      {testState.result?.success ? (
                        <div className="space-y-1">
                          <div className="flex items-center gap-1 font-medium">
                            <CheckCircle2 className="h-4 w-4" />
                            连接成功
                            {testState.result.protocol_version &&
                              ` · 协议 ${testState.result.protocol_version}`}
                            {` · 发现 ${testState.result.tools.length} 个工具`}
                          </div>
                          {testState.result.tools.length > 0 && (
                            <p className="text-muted-foreground">
                              {testState.result.tools
                                .map((tool) => tool.title || tool.name)
                                .join('、')}
                            </p>
                          )}
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          <AlertCircle className="h-4 w-4" />
                          {testState.result?.error || testState.error}
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })
        )}
      </CardContent>
    </Card>
  )
}

export function MCPSettingsPage() {
  return <MCPSettingsPageContent />
}

function MCPSettingsPageContent() {
  const [advancedVisible, setAdvancedVisible] = useState(false)
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const statusQuery = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMCPStatus,
    refetchInterval: 5_000,
  })

  useEffect(() => {
    const hookEntries = [['mcp.client.roots.items', MCPRootItemsEditor]] as const

    for (const [fieldPath, hookComponent] of hookEntries) {
      fieldHooks.register(fieldPath, hookComponent, 'replace')
    }

    return () => {
      for (const [fieldPath] of hookEntries) {
        fieldHooks.unregister(fieldPath)
      }
    }
  }, [])

  // 配置表单编排：config + schema 并行加载、渲染期 seed 草稿、脏跟踪统一由 useConfigForm 承载。
  // 草稿仅取 mcp 节；mcpSchema 为展示派生数据（非草稿），从 schema 另行派生。
  const form = useConfigForm<ConfigSectionData, Record<string, unknown>, ConfigSchema>({
    queryKey: ['mcp-settings'],
    loadConfig: () => getBotConfig(),
    loadSchema: () => getBotConfigSchema(),
    seed: (config) => {
      const configPayload = config as { config?: Record<string, unknown> } & Record<string, unknown>
      const fullConfig = (configPayload.config ?? configPayload) as Record<string, unknown>
      return (fullConfig.mcp ?? {}) as ConfigSectionData
    },
  })
  const mcpConfig = form.draft ?? {}
  const hasUnsavedChanges = form.isDirty
  const loading = form.isLoading
  const loadError = form.error

  const mcpSchema = useMemo<ConfigSchema | null>(() => {
    if (!form.schema) return null
    const schemaPayload = form.schema as { schema?: ConfigSchema } & ConfigSchema
    const fullSchema = (schemaPayload.schema ?? schemaPayload) as ConfigSchema
    return fullSchema.nested?.mcp ?? null
  }, [form.schema])

  // 保存：失败由全局 mutation 错误 toast 呈现（meta.errorTitle 定制标题）
  const saveMutation = useMutation({
    mutationFn: () => {
      const configToSave = { ...mcpConfig }
      if (Array.isArray(configToSave.servers)) {
        configToSave.servers = configToSave.servers.map((server: MCPServerConfig) => {
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          const { _uuid, ...rest } = server
          return rest
        })
      }
      return updateBotConfigSection('mcp', configToSave)
    },
    meta: { errorTitle: '保存失败' },
    onSuccess: () => {
      toast({
        title: '保存成功',
        description: 'MCP 设置已保存，主程序会自动重载发生变化的连接。',
      })
      // 失效 ['mcp-settings'] 前缀（含 config/schema 子查询）→ config 重拉 → 渲染期重新 seed → isDirty 归零
      void queryClient.invalidateQueries({ queryKey: ['mcp-settings'] })
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['mcp-status'] })
      }, 1_500)
    },
  })
  const saving = saveMutation.isPending

  const saveConfig = useCallback(async (): Promise<boolean> => {
    try {
      await saveMutation.mutateAsync()
      return true
    } catch {
      // 失败已由全局 mutation 错误 toast 呈现
      return false
    }
  }, [saveMutation])

  const formSchema: ConfigSchema | null = mcpSchema
    ? {
        className: 'MCPSettings',
        classDoc: 'MCP 设置',
        fields: [],
        nested: {
          mcp: {
            ...mcpSchema,
            fields: mcpSchema.fields.filter((field) => field.name !== 'servers'),
            nested: mcpSchema.nested
              ? Object.fromEntries(
                  Object.entries(mcpSchema.nested)
                    .filter(([key]) => key !== 'servers')
                    .map(([key, nestedSchema]) => {
                      if (key !== 'client') {
                        return [key, nestedSchema]
                      }
                      return [
                        key,
                        {
                          ...nestedSchema,
                          fields: nestedSchema.fields.filter(
                            (field) => field.name !== 'elicitation'
                          ),
                          nested: nestedSchema.nested
                            ? Object.fromEntries(
                                Object.entries(nestedSchema.nested)
                                  .filter(([nestedKey]) => nestedKey !== 'elicitation')
                                  .map(([nestedKey, clientNestedSchema]) => [
                                    nestedKey,
                                    nestedKey === 'sampling'
                                      ? {
                                          ...clientNestedSchema,
                                          fields: clientNestedSchema.fields.filter(
                                            (field) => field.name !== 'include_context_support'
                                          ),
                                        }
                                      : clientNestedSchema,
                                  ])
                              )
                            : undefined,
                        },
                      ]
                    })
                )
              : undefined,
          },
        },
      }
    : null
  const mcpServers = normalizeMCPServers(mcpConfig.servers)
  const clientConfig =
    mcpConfig.client && typeof mcpConfig.client === 'object' && !Array.isArray(mcpConfig.client)
      ? (mcpConfig.client as ConfigSectionData)
      : {}
  const rootsConfig =
    clientConfig.roots &&
    typeof clientConfig.roots === 'object' &&
    !Array.isArray(clientConfig.roots)
      ? (clientConfig.roots as ConfigSectionData)
      : {}
  const mcpRoots = normalizeMCPRoots(rootsConfig.items)
  const hasValidationErrors =
    mcpServers.some((server, index) => validateMCPServer(server, mcpServers, index).length > 0) ||
    mcpRoots.some((root) => root.enabled && !root.uri.trim())

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl md:text-3xl font-bold">MCP 设置</h1>
            <p className="text-muted-foreground mt-1 text-xs sm:text-sm">
              管理 MCP 客户端能力与服务器连接配置
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setAdvancedVisible((visible) => !visible)}
            >
              <Settings2 className="mr-1 h-4 w-4" />
              {advancedVisible ? '收起高级设置' : '高级设置'}
            </Button>
            <Button
              onClick={saveConfig}
              disabled={loading || saving || !hasUnsavedChanges || hasValidationErrors}
              size="sm"
              className="w-28"
            >
              <Save className="h-4 w-4" strokeWidth={2} fill="none" />
              <span className="ml-1 text-xs sm:text-sm">
                {saving ? '应用中' : hasUnsavedChanges ? '保存并应用' : '已应用'}
              </span>
            </Button>
          </div>
        </div>

        <Alert>
          {statusQuery.isFetching ? (
            <RefreshCw className="h-4 w-4 animate-spin" />
          ) : (
            <Info className="h-4 w-4" />
          )}
          <AlertDescription>
            MCP 连接由所有聊天流共享；保存后会自动热重载共享连接，无需重启麦麦。
            {statusQuery.data?.initialized && (
              <span className="ml-1">
                当前已连接 {statusQuery.data.server_count} 个服务，共 {statusQuery.data.tool_count}{' '}
                个工具。
              </span>
            )}
          </AlertDescription>
        </Alert>

        {loading && (
          <div className="flex h-64 items-center justify-center">
            <ThinkingIllustration size="lg" />
          </div>
        )}

        {!loading && Boolean(loadError) && (
          <div className="flex h-64 flex-col items-center justify-center gap-2">
            <p className="text-sm text-destructive">
              {loadError instanceof Error ? loadError.message : '加载配置失败'}
            </p>
            <Button variant="outline" size="sm" onClick={() => form.reload()}>
              重试
            </Button>
          </div>
        )}

        {!loading && !loadError && (
          <MCPServersBlockEditor
            servers={mcpServers}
            runtimeStatus={statusQuery.data}
            onChange={(servers) => {
              form.setDraft((currentConfig) => ({
                ...currentConfig,
                servers,
              }))
            }}
          />
        )}

        {!loading && !loadError && formSchema && (
          <DynamicConfigForm
            schema={formSchema}
            values={{ mcp: mcpConfig }}
            onChange={(fieldPath, value) => {
              const [, ...restPath] = fieldPath.split('.')
              const nextConfig =
                restPath.length === 0
                  ? (value as ConfigSectionData)
                  : updateNestedValue(mcpConfig, restPath, value)

              form.setDraft(nextConfig)
            }}
            hooks={fieldHooks}
            advancedVisible={advancedVisible}
          />
        )}

        {!loading && !loadError && !formSchema && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>当前配置 schema 中没有找到 MCP 设置。</AlertDescription>
          </Alert>
        )}
      </div>
    </ScrollArea>
  )
}
