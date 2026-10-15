import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

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
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { backendApi } from '@/lib/http'
import { readPluginPageState } from '@/lib/plugin-api-arguments'
import { customPageDocument, parseCustomPageRequest } from '@/lib/plugin-custom-page'
import { pluginPageFonts, readPluginPageTheme } from '@/lib/plugin-page-theme'
import type { PluginPageContext, PluginPageFont } from '@/lib/plugin-page-theme'
import {
  extensionPath,
  invokePluginWebUI,
  refreshPluginWebUI,
  uploadPluginWebUI,
} from '@/lib/plugin-webui'
import type { CustomPage, WebUIPage } from '@/lib/plugin-webui'
import { prepareUploadImage } from '@/lib/upload-image'

interface CustomPageProps {
  pluginId: string
  page: CustomPage
  pages: WebUIPage[]
}

export function PluginCustomPage(props: CustomPageProps) {
  const [generation, setGeneration] = useState(0)
  return (
    <CustomPageSession
      key={generation}
      {...props}
      onReload={() => setGeneration((value) => value + 1)}
    />
  )
}

function CustomPageSession({
  pluginId,
  page,
  pages,
  onReload,
}: CustomPageProps & { onReload: () => void }) {
  const { t, i18n } = useTranslation()
  const frame = useRef<HTMLIFrameElement>(null)
  const port = useRef<MessagePort | null>(null)
  const controller = useRef<AbortController | null>(null)
  const loaded = useRef(false)
  const connected = useRef(false)
  const [pageState] = useState(readPluginPageState)
  // getRandomValues also works on public HTTP deployments where randomUUID is unavailable.
  const [nonce] = useState(() =>
    Array.from(crypto.getRandomValues(new Uint32Array(4)), (value) =>
      value.toString(16).padStart(8, '0')
    ).join('')
  )
  const probes = useRef<HTMLDivElement>(null)
  const themeFonts = useRef(new Map<string, PluginPageFont>())
  const context = useRef<PluginPageContext>({
    theme: 'light',
    language: i18n.language,
    dashboardStyle: 'modern',
    styles: '',
  })
  const [source, setSource] = useState<string | null>(null)
  const [height, setHeight] = useState(384)
  const [error, setError] = useState<string | null>(null)
  const [stopped, setStopped] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [confirmation, setConfirmation] = useState<string | null>(null)
  const confirm = useRef<((accepted: boolean) => void) | null>(null)

  useEffect(() => {
    let scheduled = 0
    const update = () => {
      scheduled = 0
      const bounds = frame.current?.getBoundingClientRect()
      if (bounds)
        port.current?.postMessage({
          type: 'viewport',
          top: Math.max(0, -bounds.top),
          height: Math.max(
            1,
            Math.min(bounds.bottom, window.innerHeight) - Math.max(0, bounds.top)
          ),
        })
    }
    const schedule = () => {
      if (!scheduled) scheduled = requestAnimationFrame(update)
    }
    schedule()
    window.addEventListener('scroll', schedule, true)
    window.addEventListener('resize', schedule)
    return () => {
      cancelAnimationFrame(scheduled)
      window.removeEventListener('scroll', schedule, true)
      window.removeEventListener('resize', schedule)
    }
  }, [source, height])

  useEffect(() => {
    let scheduled = 0
    let disposed = false
    const update = () => {
      scheduled = 0
      if (!probes.current) return
      const snapshot = readPluginPageTheme(probes.current, i18n.language)
      if (JSON.stringify(snapshot) !== JSON.stringify(context.current)) {
        context.current = snapshot
        port.current?.postMessage({ type: 'context', context: snapshot })
      }
      void pluginPageFonts(snapshot.styles)
        .then((fonts) => {
          if (disposed) return
          const added = fonts.filter((font) => !themeFonts.current.has(font.id))
          for (const font of added) themeFonts.current.set(font.id, font)
          if (added.length) port.current?.postMessage({ type: 'fonts', fonts: added })
        })
        .catch((error) => {
          if (!disposed) setError(String(error))
        })
    }
    const schedule = () => {
      if (!scheduled) scheduled = requestAnimationFrame(update)
    }
    update()
    const observer = new MutationObserver(schedule)
    observer.observe(document.documentElement, { attributes: true })
    window.addEventListener('resize', schedule)
    document.fonts?.addEventListener('loadingdone', schedule)
    return () => {
      disposed = true
      observer.disconnect()
      cancelAnimationFrame(scheduled)
      window.removeEventListener('resize', schedule)
      document.fonts?.removeEventListener('loadingdone', schedule)
    }
  }, [i18n.language])

  useEffect(() => {
    const operation = new AbortController()
    controller.current = operation
    const path = [pluginId, page.id].map(encodeURIComponent).join('/')
    void backendApi
      .get<{ document: string; revision: string }>(
        `/api/webui/plugins/runtime/webui/${path}/document`,
        { signal: operation.signal }
      )
      .then((result) => {
        if (operation.signal.aborted) return
        if (result.revision !== page.document_revision)
          throw new Error('Page changed; refresh the plugin registry')
        setSource(customPageDocument(result.document, nonce))
      })
      .catch((error) => {
        if (!operation.signal.aborted) setError(String(error))
      })
    return () => {
      operation.abort()
      port.current?.close()
      port.current = null
      confirm.current?.(false)
      confirm.current = null
    }
  }, [pluginId, page.id, page.document_revision, nonce])

  const reload = async () => {
    setReloading(true)
    setError(null)
    try {
      await backendApi.post(
        `/api/webui/plugins/runtime/webui/${encodeURIComponent(pluginId)}/reload`,
        {}
      )
      await refreshPluginWebUI(true)
      onReload()
    } catch (error) {
      setError(String(error))
      setReloading(false)
    }
  }

  const stop = useCallback((reason: string) => {
    setError(reason)
    controller.current?.abort()
    port.current?.close()
    port.current = null
    confirm.current?.(false)
    confirm.current = null
    setConfirmation(null)
    setStopped(true)
  }, [])

  const connect = useCallback(() => {
    // A navigated frame must never receive a new authenticated bridge.
    if (connected.current) return
    connected.current = true
    if (!frame.current?.contentWindow || !controller.current) return
    const channel = new MessageChannel()
    port.current = channel.port1
    const signal = controller.current.signal
    let inflight = 0
    let tokens = 200
    let refill = performance.now()
    let layoutTokens = 100
    let layoutRefill = refill
    const ids = new Set<number>()
    const operations = new Map<number, AbortController>()
    channel.port1.onmessage = async (event) => {
      if (signal.aborted) return
      const now = performance.now()
      if (event.data?.type === 'layout') {
        if (page.layout !== 'document') return
        layoutTokens = Math.min(100, layoutTokens + (now - layoutRefill) / 50)
        layoutRefill = now
        if (layoutTokens < 1) {
          stop(t('pluginWebUI.customStopLayoutRate'))
          return
        }
        layoutTokens -= 1
        const nextHeight = event.data.height
        if (Number.isSafeInteger(nextHeight) && nextHeight > 0 && nextHeight <= 30000)
          setHeight(nextHeight)
        else {
          stop(t('pluginWebUI.customStopHeight'))
        }
        return
      }
      tokens = Math.min(200, tokens + (now - refill) / 50)
      refill = now
      // Allow bursts of 200 messages and sustain 20 per second for batch uploads.
      // Flooding closes this page's bridge instead of growing a queue in the host.
      if (tokens < 1) {
        stop(t('pluginWebUI.customStopRequestRate'))
        return
      }
      tokens -= 1
      if (event.data?.kind === 'cancel' && Number.isSafeInteger(event.data.id)) {
        operations.get(event.data.id)?.abort()
        return
      }
      let id: number | undefined =
        Number.isSafeInteger(event.data?.id) && event.data.id > 0 ? event.data.id : undefined
      try {
        const request = parseCustomPageRequest(event.data, page)
        id = request.id
        if (ids.has(id)) {
          stop(t('pluginWebUI.customStopDuplicateId'))
          return
        }
        if (inflight >= 2) throw new Error('Concurrency limit reached')
        ids.add(id)
        inflight += 1
        // Cancellation owns the whole request, including confirmation and image preparation.
        const operation = new AbortController()
        operations.set(id, operation)
        const abort = () => operation.abort()
        signal.addEventListener('abort', abort, { once: true })
        try {
          if (request.kind === 'navigate') {
            if (!pages.some((target) => target.id === request.name))
              throw new Error('Page is not declared by this plugin')
            const state = new URLSearchParams({ state: JSON.stringify(request.args) })
            channel.port1.postMessage({ id, result: null })
            window.location.assign(`${extensionPath(pluginId, request.name)}?${state}`)
            return
          }
          const binding =
            request.kind === 'uploads'
              ? page.actions[page.uploads![request.name].action]
              : page[request.kind][request.name]
          if (request.kind !== 'queries' && binding.confirmation) {
            if (confirm.current) throw new Error('Another confirmation is open')
            const accepted = await new Promise<boolean>((resolve) => {
              const cancel = () => finish(false)
              const timer = window.setTimeout(cancel, 45000)
              const finish = (accepted: boolean) => {
                window.clearTimeout(timer)
                operation.signal.removeEventListener('abort', cancel)
                confirm.current = null
                setConfirmation(null)
                resolve(accepted)
              }
              confirm.current = finish
              operation.signal.addEventListener('abort', cancel, { once: true })
              setConfirmation(binding.confirmation)
            })
            if (!accepted || operation.signal.aborted) throw new Error('Action cancelled')
          }
          let result: unknown
          if (request.kind === 'uploads') {
            const upload = page.uploads[request.name]
            const prepared = upload.image_max_edge
              ? await prepareUploadImage(request.file!, upload.image_max_edge)
              : { file: request.file! }
            if (operation.signal.aborted) throw new DOMException('Upload cancelled', 'AbortError')
            if (prepared.file.size > 20 * 1024 * 1024) throw new Error('File exceeds 20 MiB')
            result = await uploadPluginWebUI(
              pluginId,
              page.id,
              request.name,
              prepared.file,
              request.args,
              (percent) => {
                if (!operation.signal.aborted)
                  channel.port1.postMessage({ id, type: 'progress', percent })
              },
              operation.signal,
              !!binding.confirmation
            )
          } else
            result = await invokePluginWebUI(
              pluginId,
              page.id,
              request.kind,
              request.name,
              request.args,
              request.kind === 'actions' && !!binding.confirmation,
              operation.signal
            )
          if (!operation.signal.aborted) channel.port1.postMessage({ id, result })
        } finally {
          signal.removeEventListener('abort', abort)
          operations.delete(id)
          inflight -= 1
          ids.delete(id)
        }
      } catch (error) {
        if (!signal.aborted && id !== undefined)
          channel.port1.postMessage({ id, error: String(error) })
      }
    }
    // srcdoc has an opaque origin; a dedicated transferred port carries only this page's calls.
    frame.current.contentWindow.postMessage(
      {
        type: 'maibot:connect',
        context: context.current,
        page: { id: page.id, layout: page.layout, state: pageState },
        fonts: Array.from(themeFonts.current.values()),
      },
      '*',
      [channel.port2]
    )
  }, [page, pages, pageState, pluginId, stop, t])

  useEffect(() => {
    if (!source) return
    const timer = window.setTimeout(() => {
      if (!connected.current) {
        stop(t('pluginWebUI.customStopTimeout'))
      }
    }, 10000)
    return () => window.clearTimeout(timer)
  }, [source, stop, t])

  useEffect(() => {
    const ready = (event: MessageEvent) => {
      if (
        event.source === frame.current?.contentWindow &&
        event.origin === 'null' &&
        event.data?.type === 'maibot:ready' &&
        event.data.nonce === nonce
      )
        connect()
    }
    window.addEventListener('message', ready)
    return () => window.removeEventListener('message', ready)
  }, [connect, nonce])

  function resolveConfirmation(accepted: boolean) {
    confirm.current?.(accepted)
    confirm.current = null
    setConfirmation(null)
  }

  return (
    <div className="space-y-3 p-4">
      <div
        ref={probes}
        aria-hidden="true"
        inert
        className="pointer-events-none invisible fixed top-0 -left-[10000px]"
        data-dashboard-main="true"
      >
        <Button data-theme-probe="button" variant="outline">
          Button
        </Button>
        <Button data-theme-probe="primary">Primary</Button>
        <Button data-theme-probe="danger" variant="destructive">
          Danger
        </Button>
        <Card data-theme-probe="card">Card</Card>
        <Input data-theme-probe="input" tabIndex={-1} />
        <span data-theme-probe="muted" className="text-muted-foreground">
          Text
        </span>
        <div data-theme-probe="background" className="bg-background" />
        <div data-theme-probe="soft" className="bg-muted" />
      </div>
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">{page.title}</h1>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => stop(t('pluginWebUI.customStopManual'))}
            disabled={stopped}
          >
            {t('pluginWebUI.customStop')}
          </Button>
          <Button variant="outline" onClick={() => void reload()} disabled={reloading}>
            {t('pluginWebUI.customReload')}
          </Button>
        </div>
      </div>
      {page.description && <p className="text-muted-foreground text-sm">{page.description}</p>}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {!source && !error && !stopped && <p>{t('pluginWebUI.loading')}</p>}
      {stopped ? (
        <p>{t('pluginWebUI.customStopped')}</p>
      ) : (
        source && (
          <iframe
            ref={frame}
            title={page.title}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'"
            srcDoc={source}
            onLoad={() => {
              if (loaded.current) stop(t('pluginWebUI.customStopNavigation'))
              loaded.current = true
            }}
            style={{
              height: page.layout === 'viewport' ? 'calc(100dvh - 12rem)' : height,
              minHeight: 384,
            }}
            className="block w-full border-0"
          />
        )
      )}
      <AlertDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) resolveConfirmation(false)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('pluginWebUI.confirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-line">
              {confirmation}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('pluginWebUI.cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={() => resolveConfirmation(true)}>
              {t('pluginWebUI.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
