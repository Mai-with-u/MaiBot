import { Activity, Database, List, Puzzle, Settings } from 'lucide-react'
import { useEffect, useSyncExternalStore } from 'react'

import { backendApi } from '@/lib/http'
import { onBackendUrlChanged } from '@/lib/api-base'
import { resolveApiPath } from '@/lib/api-base'

export type Scalar = string | number | boolean | null
export interface DataReference {
  scope?: 'query' | 'selection' | 'item'
  source: string
  field: string
}
export interface Parameter {
  type: 'string' | 'integer' | 'number' | 'boolean'
  required: boolean
  max_length: number
  minimum: number | null
  maximum: number | null
  choices: Scalar[]
}
export interface APIBinding {
  api: string
  version: string
  parameters: Record<string, Parameter>
  confirmation: string | null
  arguments?: Record<string, DataReference>
  clear_selection?: string | null
}
export interface WebUINode {
  type:
    | 'stack'
    | 'grid'
    | 'card'
    | 'tabs'
    | 'text'
    | 'stat'
    | 'progress'
    | 'table'
    | 'chart'
    | 'gallery'
    | 'image'
    | 'pagination'
    | 'input'
    | 'select'
    | 'multi_select'
    | 'checkbox'
    | 'switch'
    | 'date'
    | 'button'
    | 'dialog'
    | 'collapsible'
    | 'repeat'
    | 'upload'
  label: string | null
  value: Scalar | DataReference
  children: WebUINode[]
  columns: number | Array<{ field: string; label: string }> | null
  name: string | null
  options: Array<{ label: string; value: string }>
  action: string | null
  variant: 'primary' | 'danger' | 'muted'
  chart_type: 'line' | 'bar'
  x: string | null
  y: string | null
  selection?: string | null
  detail?: string | null
  max_items?: number
  default_open?: boolean
  image_max_edge?: number | null
  compact?: boolean
  manual_upload?: boolean
  submit_label?: string | null
  when?: VisibilityCondition | null
  disabled_when?: VisibilityCondition | null
  disabled_reason?: string | null
  presentation?: 'dropdown' | 'buttons'
}
export interface VisibilityCondition {
  reference: DataReference
  operator: 'truthy' | 'empty' | 'not_empty' | 'equals'
  expected: Scalar
}
export interface DataContexts {
  selection?: Record<string, unknown>
  item?: Record<string, unknown>
}
export interface WebUIPage {
  poll_interval_seconds?: number
  auto_refresh?: boolean
  id: string
  title: string
  description: string
  placement: 'sidebar' | 'workspace'
  icon: keyof typeof extensionIcons
  queries: Record<string, APIBinding>
  actions: Record<string, APIBinding>
  content: WebUINode[]
}
export interface WebUIExtension {
  required_capabilities?: string[]
  plugin_id: string
  workspace_title: string | null
  pages: WebUIPage[]
}
export const extensionIcons = {
  puzzle: Puzzle,
  chart: Activity,
  settings: Settings,
  database: Database,
  list: List,
}
export const extensionPath = (pluginId: string, pageId: string) =>
  `/extensions/${encodeURIComponent(pluginId)}/${encodeURIComponent(pageId)}`
export const extensionWorkspace = (pluginId: string) => `plugin:${pluginId}` as const

interface Preferences {
  hidden: string[]
  order: string[]
  workspaceOrder?: string[]
}
interface RegistryState {
  extensions: WebUIExtension[]
  loading: boolean
  error: string | null
  preferences: Preferences
}
const STORAGE_KEY = 'maibot-plugin-webui-preferences'
const listeners = new Set<() => void>()
let state: RegistryState = {
  extensions: [],
  loading: true,
  error: null,
  preferences: { hidden: [], order: [] },
}
let generation = 0
let pending: Promise<void> | null = null
let activeUsers = 0
let registryTimer: number | null = null
let unsubscribeBackend: (() => void) | null = null

function publish(next: RegistryState) {
  state = next
  listeners.forEach((listener) => listener())
}
function readPreferences(): Preferences {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return { hidden: [], order: [] }
  try {
    const parsed = JSON.parse(raw)
    if (
      Array.isArray(parsed.hidden) &&
      parsed.hidden.every((item: unknown) => typeof item === 'string') &&
      Array.isArray(parsed.order) &&
      parsed.order.every((item: unknown) => typeof item === 'string') &&
      (parsed.workspaceOrder === undefined ||
        (Array.isArray(parsed.workspaceOrder) &&
          parsed.workspaceOrder.every((item: unknown) => typeof item === 'string')))
    )
      return parsed
  } catch {
    /* 损坏的本地显示偏好不影响插件注册和后端权限。 */
  }
  return { hidden: [], order: [] }
}

export function refreshPluginWebUI(ensureFresh = false): Promise<void> {
  // 启停完成时，已有请求可能读取了变更前的注册表，需等它结束后再查询一次。
  if (pending) return ensureFresh ? pending.then(() => refreshPluginWebUI()) : pending
  const current = generation
  pending = backendApi
    .get<{ extensions: WebUIExtension[]; capabilities?: string[] }>('/api/webui/plugins/runtime/webui')
    .then((response) => {
      if (current === generation) {
        // 未变更的声明保留引用，注册表轮询不会中断页面中进行的操作或重置表单。
        const extensions = response.extensions.map((extension) => {
          const unsupported = (extension.required_capabilities ?? []).filter(capability =>
            !(response.capabilities ?? []).includes(capability))
          if (unsupported.length) throw new Error(`Host does not support ${unsupported.join(', ')}; upgrade host and SDK`)
          const previous = state.extensions.find((item) => item.plugin_id === extension.plugin_id)
          return previous && JSON.stringify(previous) === JSON.stringify(extension)
            ? previous
            : extension
        })
        publish({ ...state, extensions, loading: false, error: null })
      }
    })
    .catch((error: unknown) => {
      if (current === generation)
        publish({ ...state, extensions: [], loading: false, error: String(error) })
    })
    .finally(() => {
      if (current === generation) pending = null
    })
  return pending
}

export function setPluginWebUIPreferences(preferences: Preferences) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences))
  publish({ ...state, preferences })
}

/** 所有导航共享一次注册表加载；定期刷新以反映插件卸载和重载。 */
export function usePluginWebUI(enabled = false): RegistryState {
  const snapshot = useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    () => state
  )
  useEffect(() => {
    if (!enabled) return
    activeUsers += 1
    if (activeUsers === 1) {
      publish({ ...state, preferences: readPreferences() })
      void refreshPluginWebUI()
      registryTimer = window.setInterval(() => {
        void refreshPluginWebUI()
      }, 30000)
      unsubscribeBackend = onBackendUrlChanged(() => {
        generation += 1
        pending = null
        publish({ ...state, extensions: [], loading: true, error: null })
        void refreshPluginWebUI()
      })
    }
    return () => {
      activeUsers -= 1
      if (activeUsers === 0) {
        if (registryTimer !== null) clearInterval(registryTimer)
        registryTimer = null
        unsubscribeBackend?.()
        unsubscribeBackend = null
      }
    }
  }, [enabled])
  return snapshot
}

export function visibleExtensions(registry: RegistryState): WebUIExtension[] {
  const { hidden, order } = registry.preferences
  return registry.extensions
    .filter((extension) => !hidden.includes(extension.plugin_id))
    .sort((a, b) => {
      const rank = (id: string) => {
        const index = order.indexOf(id)
        return index === -1 ? order.length : index
      }
      return rank(a.plugin_id) - rank(b.plugin_id) || a.plugin_id.localeCompare(b.plugin_id)
    })
}

/** 顶部工作区独立排序；未设置时沿用原有页面顺序。 */
export function orderedWorkspaceExtensions(
  extensions: WebUIExtension[],
  order: string[]
): WebUIExtension[] {
  return extensions
    .filter((extension) => extension.pages.some((page) => page.placement === 'workspace'))
    .sort((a, b) => {
      const rank = (id: string) => {
        const index = order.indexOf(id)
        return index === -1 ? order.length : index
      }
      return rank(a.plugin_id) - rank(b.plugin_id) || a.plugin_id.localeCompare(b.plugin_id)
    })
}

export async function invokePluginWebUI(
  pluginId: string,
  pageId: string,
  kind: 'queries' | 'actions',
  name: string,
  args: Record<string, Scalar>,
  confirmed = false,
  signal?: AbortSignal
): Promise<unknown> {
  const path = [pluginId, pageId, kind, name].map(encodeURIComponent).join('/')
  const response = await backendApi.post<{ result: unknown }>(
    `/api/webui/plugins/runtime/webui/${path}`,
    {
      body: { args, confirmed },
      signal,
    }
  )
  return response.result
}

export async function uploadPluginWebUI(
  pluginId: string, pageId: string, name: string, file: File,
  args: Record<string, Scalar>, onProgress: (percent: number) => void, signal?: AbortSignal
): Promise<unknown> {
  const path = [pluginId, pageId, 'uploads', name].map(encodeURIComponent).join('/')
  const url = await resolveApiPath(`/api/webui/plugins/runtime/webui/${path}`)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    if (signal?.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return }
    signal?.addEventListener('abort', abort, { once: true })
    xhr.onloadend = () => signal?.removeEventListener('abort', abort)
    xhr.onabort = () => reject(new DOMException('Upload cancelled; received files remain saved', 'AbortError'))
    xhr.open('POST', url)
    xhr.withCredentials = true
    xhr.timeout = 120000
    xhr.ontimeout = () => reject(new Error('Upload timed out; check the result before retrying'))
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100))
    }
    xhr.onerror = () => reject(new Error('Upload connection failed'))
    xhr.onload = () => {
      if (xhr.status === 401) window.location.href = '/auth'
      try {
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `HTTP ${xhr.status}: ${xhr.statusText || 'Upload failed'}`
          try {
            const errorBody = JSON.parse(xhr.responseText)
            if (typeof errorBody.detail === 'string') detail = errorBody.detail
          } catch { /* Servers may return plain text for an internal error. */ }
          throw new Error(detail)
        }
        const body = JSON.parse(xhr.responseText)
        resolve(body.result)
      } catch (error) { reject(error) }
    }
    const form = new FormData()
    form.append('file', file)
    form.append('args', JSON.stringify(args))
    xhr.send(form)
  })
}

export function resolveReference(
  reference: DataReference,
  data: Record<string, unknown>,
  contexts: DataContexts = {}
): unknown {
  const source =
    reference.scope === 'selection'
      ? contexts.selection
      : reference.scope === 'item'
        ? contexts.item
        : data
  if (reference.scope === 'selection' && (!source || !Object.hasOwn(source, reference.source)))
    return null
  if (!source || !Object.hasOwn(source, reference.source))
    throw new Error(`Missing data source: ${reference.source}`)
  let value = source[reference.source]
  for (const field of reference.field ? reference.field.split('.') : []) {
    if (value === null || typeof value !== 'object' || !Object.hasOwn(value, field)) {
      throw new Error(`Missing data field: ${reference.source}.${reference.field}`)
    }
    value = (value as Record<string, unknown>)[field]
  }
  return value
}

export function resolveNodeValue(
  node: WebUINode,
  data: Record<string, unknown>,
  contexts: DataContexts = {}
): unknown {
  return node.value !== null && typeof node.value === 'object'
    ? resolveReference(node.value, data, contexts)
    : node.value
}

export function nodeVisible(
  node: WebUINode,
  data: Record<string, unknown>,
  contexts: DataContexts = {}
): boolean {
  return !node.when || conditionMatches(node.when, data, contexts)
}

export function conditionMatches(condition: VisibilityCondition, data: Record<string, unknown>, contexts: DataContexts = {}): boolean {
  const value = resolveReference(condition.reference, data, contexts)
  const empty =
    value == null ||
    value === '' ||
    (Array.isArray(value) && value.length === 0) ||
    (typeof value === 'object' && value !== null && Object.keys(value).length === 0)
  switch (condition.operator) {
    case 'empty':
      return empty
    case 'not_empty':
      return !empty
    case 'equals':
      return value === condition.expected
    case 'truthy':
      return Boolean(value)
  }
}

/** Bound the expanded tree before React mounts it, including nested loops. */
export function validateRenderSize(
  nodes: WebUINode[],
  data: Record<string, unknown>,
  contexts: DataContexts = {},
  openDialog: string | null = null
): void {
  let count = 0
  const visit = (children: WebUINode[], context: DataContexts, depth: number) => {
    for (const node of children) {
      if (++count > 1000 || depth > 8) throw new Error('Expanded component tree exceeds limits')
      if (!nodeVisible(node, data, context)) continue
      if (node.type === 'dialog' && node.name !== openDialog) continue
      if (node.type === 'repeat') {
        const rows = resolveNodeValue(node, data, context)
        if (!Array.isArray(rows)) throw new Error('Repeat data must be an array')
        for (const row of rows.slice(0, node.max_items ?? 50))
          visit(
            node.children,
            { ...context, item: { ...context.item, [node.name!]: row } },
            depth + 1
          )
      } else visit(node.children, context, depth + 1)
    }
  }
  visit(nodes, contexts, 1)
}
