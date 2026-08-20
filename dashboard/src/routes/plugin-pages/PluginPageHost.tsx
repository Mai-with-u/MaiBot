import { useEffect, useRef, useState } from 'react'
import { useParams } from '@tanstack/react-router'

import { backendApi } from '@/lib/http'
import { fetchPluginPages } from '@/lib/plugin-api/pages'
import type { PluginPageSummary } from '@/lib/plugin-api/types'
import { APP_VERSION } from '@/lib/version'

import { loadPluginPageModule } from './loader'

export interface PluginPageRequestOptions {
  method?: 'POST'
  body?: unknown
  signal?: AbortSignal
}

export interface PluginPageContext {
  pluginId: string
  pageId: string
  hostVersion: string
  apiBase: string
  assetsBase: string
  request<T>(operation: string, options?: PluginPageRequestOptions): Promise<T>
}

type PluginPageMount = (
  container: HTMLElement,
  context: PluginPageContext
) => void | (() => void)

type PluginPagePhase = 'loading' | 'ready' | 'error'

interface PluginRouteParams {
  pluginId: string
  pageId: string
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function deriveAssetsBase(entry: string): string {
  const entryUrl = new URL(entry, window.location.href)
  const assetsMarker = '/assets/'
  const markerIndex = entryUrl.pathname.indexOf(assetsMarker)
  if (markerIndex >= 0) {
    return entryUrl.pathname.slice(0, markerIndex + assetsMarker.length)
  }

  return entryUrl.pathname.slice(0, entryUrl.pathname.lastIndexOf('/') + 1)
}

function disposePluginPage(cleanupRef: { current: (() => void) | null }): void {
  const cleanup = cleanupRef.current
  cleanupRef.current = null
  if (!cleanup) {
    return
  }

  try {
    cleanup()
  } catch (error) {
    // 插件清理失败不能阻塞宿主路由卸载，但必须保留诊断信息。
    console.error('插件 WebUI 页面清理失败:', error)
  }
}

function findPluginPage(
  pages: PluginPageSummary[],
  pluginId: string,
  pageId: string
): PluginPageSummary {
  const page = pages.find((candidate) => {
    return candidate.plugin_id === pluginId && candidate.page_id === pageId
  })
  if (!page) {
    throw new Error(`插件页面不存在：${pluginId}/${pageId}`)
  }
  return page
}

function createPluginPageContext(page: PluginPageSummary): PluginPageContext {
  return {
    pluginId: page.plugin_id,
    pageId: page.page_id,
    hostVersion: APP_VERSION,
    apiBase: page.api_base,
    assetsBase: deriveAssetsBase(page.entry),
    request: async <T,>(operation: string, options: PluginPageRequestOptions = {}) => {
      if (!operation.trim()) {
        throw new Error('插件页面 API 操作名不能为空')
      }
      if (options.method && options.method !== 'POST') {
        throw new Error('插件页面 API 仅支持 POST')
      }

      const operationPath = encodeURIComponent(operation)
      return backendApi.post<T>(`${page.api_base}/${operationPath}`, {
        body: options.body,
        signal: options.signal,
      })
    },
  }
}

export function PluginPageHost() {
  const { pluginId, pageId } = useParams({ strict: false }) as PluginRouteParams
  const containerRef = useRef<HTMLDivElement>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const [phase, setPhase] = useState<PluginPagePhase>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const abortController = new AbortController()
    disposePluginPage(cleanupRef)
    containerRef.current?.replaceChildren()
    setPhase('loading')
    setErrorMessage(null)

    async function loadAndMount(): Promise<void> {
      try {
        const response = await fetchPluginPages(abortController.signal)
        const page = findPluginPage(response.pages, pluginId, pageId)
        const module = await loadPluginPageModule(page.entry)
        if (cancelled) {
          return
        }

        const mount = module[page.component]
        if (typeof mount !== 'function') {
          throw new Error(`插件页面入口缺少 ${page.component} 导出`)
        }
        const container = containerRef.current
        if (!container) {
          throw new Error('插件页面容器尚未准备完成')
        }

        const cleanup = (mount as PluginPageMount)(container, createPluginPageContext(page))
        if (cancelled) {
          if (typeof cleanup === 'function') {
            cleanup()
          }
          return
        }
        cleanupRef.current = typeof cleanup === 'function' ? cleanup : null
        setPhase('ready')
      } catch (error: unknown) {
        if (cancelled || (error instanceof Error && error.name === 'AbortError')) {
          return
        }
        setPhase('error')
        setErrorMessage(getErrorMessage(error))
      }
    }

    void loadAndMount()
    return () => {
      cancelled = true
      abortController.abort()
      disposePluginPage(cleanupRef)
      containerRef.current?.replaceChildren()
    }
  }, [pageId, pluginId])

  return (
    <section
      aria-label="插件页面"
      className="flex h-full min-h-0 flex-col overflow-auto"
      data-plugin-page-host="true"
      data-plugin-page-phase={phase}
    >
      <div ref={containerRef} className="min-h-0 flex-1" data-plugin-page-container="true" />
      {phase === 'loading' && (
        <p className="text-muted-foreground p-6" role="status">
          正在加载插件页面…
        </p>
      )}
      {phase === 'error' && (
        <div className="text-destructive p-6" role="alert">
          <h1 className="font-semibold">插件页面加载失败</h1>
          <p>{errorMessage ?? '未知错误'}</p>
        </div>
      )}
    </section>
  )
}
