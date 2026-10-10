import { Link, useParams } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorBoundary } from '@/components/error-boundary'
import { PluginWebUIRenderer } from '@/components/plugin-webui-renderer'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  invokePluginWebUI,
  uploadPluginWebUI,
  refreshPluginWebUI,
  resolveReference,
  usePluginWebUI,
  extensionPath,
} from '@/lib/plugin-webui'
import type { APIBinding, DataContexts, Scalar, WebUIPage, WebUINode } from '@/lib/plugin-webui'

function initialValues(nodes: WebUINode[]): Record<string, Scalar> {
  const values: Record<string, Scalar> = {}
  const pending = [...nodes]
  while (pending.length) {
    const node = pending.pop()!
    if (node.type === 'pagination' && node.name !== null) values[node.name] = 1
    if (
      ['input', 'select', 'switch', 'date'].includes(node.type) &&
      node.name !== null &&
      (node.value === null || typeof node.value !== 'object')
    )
      values[node.name] = node.value
    pending.push(...node.children)
  }
  return values
}

function pageValues(page: WebUIPage): Record<string, Scalar> {
  const values = initialValues(page.content)
  // Navigation parameters belong to this browser page, never plugin-wide settings.
  const search = new URLSearchParams(window.location.search)
  for (const binding of Object.values(page.queries)) {
    for (const [name, parameter] of Object.entries(binding.parameters)) {
      const raw = search.get(name)
      if (raw === null) continue
      if (parameter.type === 'boolean') { if (raw === 'true' || raw === 'false') values[name] = raw === 'true' }
      else if (parameter.type === 'integer' || parameter.type === 'number') {
        const value = Number(raw)
        if (Number.isFinite(value) && (parameter.type !== 'integer' || Number.isSafeInteger(value))) values[name] = value
      } else values[name] = raw
    }
  }
  return values
}

function argumentsFor(
  binding: APIBinding,
  values: Record<string, Scalar>,
  data: Record<string, unknown> = {},
  contexts: DataContexts = {}
): Record<string, Scalar> {
  const args: Record<string, Scalar> = Object.fromEntries(
    Object.keys(binding.parameters)
      .filter((name) => values[name] !== undefined && values[name] !== null)
      .map((name) => [name, values[name]])
  )
  for (const [name, reference] of Object.entries(binding.arguments ?? {})) {
    const value = resolveReference(reference, data, contexts)
    if (value === null || value === undefined) {
      delete args[name]
      if (binding.parameters[name].required) throw new Error(`Missing action argument: ${name}`)
    } else if (
      !['string', 'number', 'boolean'].includes(typeof value) ||
      (typeof value === 'number' && !Number.isFinite(value))
    ) {
      throw new Error(`Action argument must be scalar: ${name}`)
    } else args[name] = value as Scalar
  }
  return args
}

function ExtensionPage({ pluginId, page }: { pluginId: string; page: WebUIPage }) {
  const registry = usePluginWebUI()
  const { t } = useTranslation()
  const [values, setValues] = useState(() => pageValues(page))
  const valuesRef = useRef(values)
  const galleryPreferences = useRef<Record<string, string>>({})
  const [data, setData] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<{
    name: string
    args: Record<string, Scalar>
  } | null>(null)
  const [revision, setRevision] = useState(0)
  const [selectionRevision, setSelectionRevision] = useState(0)
  const [clearSelection, setClearSelection] = useState<string | null>(null)
  const renderFailed = useRef(false)
  const alive = useRef(true)
  const controller = useRef<AbortController | null>(null)

  const loadQueries = useCallback(
    async (signal: AbortSignal) => {
      const results: Record<string, unknown> = {}
      // 顺序执行，遵守插件网关的并发上限；整个快照成功后才替换页面数据。
      for (const [name, binding] of Object.entries(page.queries)) {
        results[name] = await invokePluginWebUI(
          pluginId,
          page.id,
          'queries',
          name,
          argumentsFor(binding, valuesRef.current),
          false,
          signal
        )
      }
      if (!signal.aborted && alive.current) {
        setData(results)
        setLoaded(true)
        if (renderFailed.current) {
          renderFailed.current = false
          setRevision((value) => value + 1)
        }
      }
    },
    [page, pluginId]
  )

  const refresh = useCallback(async (clearError = true) => {
    if (busyRef.current) return
    if (!clearError && controller.current) return
    controller.current?.abort()
    if (clearError) { busyRef.current = true; setBusy(true) }
    if (clearError) setError(null)
    controller.current = new AbortController()
    const operation = controller.current
    try {
      await loadQueries(operation.signal)
      if (alive.current && !operation.signal.aborted) {
        setClearSelection(null)
      }
    } catch (error) {
      if (alive.current && !operation.signal.aborted) setError(String(error))
    } finally {
      if (controller.current === operation) {
        controller.current = null
        busyRef.current = false
        if (alive.current) setBusy(false)
      }
    }
  }, [loadQueries])

  useEffect(() => {
    alive.current = true
    void refresh()
    return () => {
      alive.current = false
      controller.current?.abort()
      busyRef.current = false
    }
  }, [refresh])

  useEffect(() => {
    const seconds = page.poll_interval_seconds ?? 0
    if (seconds < 3) return
    const timer = window.setInterval(() => { if (!busyRef.current) void refresh(false) }, seconds * 1000)
    return () => window.clearInterval(timer)
  }, [page.poll_interval_seconds, refresh])

  const execute = async (name: string, args: Record<string, Scalar>, confirmed = false) => {
    if (busyRef.current) return
    const binding = page.actions[name]
    if (binding.confirmation && !confirmed) {
      // Freeze the selected row's scalar arguments while the user confirms the action.
      setConfirmation({ name, args })
      return
    }
    setConfirmation(null)
    busyRef.current = true
    setBusy(true)
    setError(null)
    setMessage(null)
    controller.current?.abort()
    controller.current = new AbortController()
    const operation = controller.current
    try {
      const result = await invokePluginWebUI(pluginId, page.id, 'actions', name, args, confirmed, operation.signal)
      if (alive.current && !operation.signal.aborted) setMessage(t('pluginWebUI.completed'))
      await loadQueries(operation.signal)
      if (alive.current && !operation.signal.aborted) {
        setClearSelection(binding.clear_selection ?? null)
        setSelectionRevision((value) => value + 1)
      }
      if (result && typeof result === 'object' && 'navigate_page' in result && typeof result.navigate_page === 'string' &&
        registry.extensions.find(e => e.plugin_id === pluginId)?.pages.some(p => p.id === result.navigate_page)) {
        const target = registry.extensions.find(e => e.plugin_id === pluginId)!.pages.find(p => p.id === result.navigate_page)!
        const parameters = new Set(Object.values(target.queries).flatMap(binding => Object.keys(binding.parameters)))
        const search = new URLSearchParams()
        if ('navigate_params' in result && result.navigate_params && typeof result.navigate_params === 'object')
          for (const [key, value] of Object.entries(result.navigate_params))
            if (parameters.has(key) && ['string', 'number', 'boolean'].includes(typeof value)) search.set(key, String(value))
        const destination = extensionPath(pluginId, target.id) + (search.size ? `?${search}` : '')
        if (target.id === page.id) {
          window.history.replaceState(null, '', destination)
          valuesRef.current = pageValues(page)
          setValues(valuesRef.current)
          await loadQueries(operation.signal)
        } else window.location.assign(destination)
      }
    } catch (error) {
      if (alive.current && !operation.signal.aborted) setError(String(error))
    } finally {
      if (controller.current === operation) {
        controller.current = null
        busyRef.current = false
        if (alive.current) setBusy(false)
      }
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 md:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{page.title}</h1>
          <p className="text-muted-foreground text-sm">{page.description}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t('pluginWebUI.providedBy', { plugin: pluginId })}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => {
            void refresh()
          }}
        >
          {t('pluginWebUI.refresh')}
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {message && (
        <p role="status" className="text-muted-foreground text-sm">
          {message}
        </p>
      )}
      {!loaded && busy && <p role="status">{t('pluginWebUI.loading')}</p>}
      {
        <ErrorBoundary
          key={revision}
          onError={() => { renderFailed.current = true }}
          fallback={
            <Alert variant="destructive">
              <AlertDescription>{t('pluginWebUI.invalidData')}</AlertDescription>
            </Alert>
          }
        >
          <div className="space-y-4">
            <PluginWebUIRenderer
              nodes={page.content}
              data={data}
              values={values}
              busy={busy}
              pendingData={!loaded}
              selectionRevision={selectionRevision}
              clearSelection={clearSelection}
              galleryPreferences={galleryPreferences.current}
              onUpload={async (name, file, progress) => {
                const args = argumentsFor(page.actions[name], valuesRef.current)
                delete args.upload_id
                return uploadPluginWebUI(pluginId, page.id, name, file, args, progress)
              }}
              onUploadComplete={async () => { if (alive.current) await refresh() }}
              onChange={(name, value) => {
                const next = { ...valuesRef.current, [name]: value }
                if (page.auto_refresh) {
                  const pending = [...page.content]
                  while (pending.length) {
                    const node = pending.pop()!
                    if (node.type === 'pagination' && node.name !== null && node.name !== name)
                      next[node.name] = 1
                    pending.push(...node.children)
                  }
                }
                valuesRef.current = next
                setValues(next)
                if (page.auto_refresh) void refresh()
              }}
              onAction={(name, contexts) => {
                try {
                  void execute(
                    name,
                    argumentsFor(page.actions[name], valuesRef.current, data, contexts)
                  )
                } catch (error) {
                  setError(String(error))
                }
              }}
            />
          </div>
        </ErrorBoundary>
      }
      <AlertDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('pluginWebUI.confirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation ? page.actions[confirmation.name].confirmation : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('pluginWebUI.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmation) void execute(confirmation.name, confirmation.args, true)
              }}
            >
              {t('pluginWebUI.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export function PluginWebUIPage() {
  const { pluginId, pageId } = useParams({ from: '/protected/extensions/$pluginId/$pageId' })
  const registry = usePluginWebUI()
  const { t } = useTranslation()
  const extension = registry.extensions.find((item) => item.plugin_id === pluginId)
  const page = extension?.pages.find((item) => item.id === pageId)
  if (registry.loading) return <p className="p-6">{t('pluginWebUI.loading')}</p>
  if (registry.error)
    return (
      <div className="space-y-4 p-6">
        <Alert variant="destructive">
          <AlertDescription>{registry.error}</AlertDescription>
        </Alert>
        <Button
          onClick={() => {
            void refreshPluginWebUI()
          }}
        >
          {t('pluginWebUI.refresh')}
        </Button>
      </div>
    )
  if (!page || registry.preferences.hidden.includes(pluginId))
    return (
      <p className="p-6">
        {t('pluginWebUI.unavailable')}{' '}
        <Link to="/plugin-config" hash="webui-extensions">
          {t('pluginWebUI.manage')}
        </Link>
      </p>
    )
  // 注册表刷新得到相同声明时保留表单；声明发生变化时重建页面状态。
  return (
    <ExtensionPage
      key={`${pluginId}/${pageId}/${JSON.stringify(page)}`}
      pluginId={pluginId}
      page={page}
    />
  )
}
