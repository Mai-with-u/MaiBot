import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import type { AISearchConfigUpdate, AISearchInstallRequest } from '@/lib/ai-search-api'
import { installPlugin } from '@/lib/plugin-api/install-flow'

interface SearchPluginActionsProps {
  installRequests: AISearchInstallRequest[]
  configUpdates: AISearchConfigUpdate[]
  onOpenConfig: (pluginId: string) => void
}

type InstallState = { status: 'installing' | 'success' | 'error' | 'cancelled'; message?: string }

export function SearchPluginActions({
  installRequests,
  configUpdates,
  onOpenConfig,
}: SearchPluginActionsProps) {
  const { t } = useTranslation()
  const [states, setStates] = useState<Record<string, InstallState>>({})
  const started = useRef(new Set<string>())

  // 安装只由用户点击触发，渲染、流式进度与模型的最终回答都不能调用安装接口。
  const confirmInstall = async (request: AISearchInstallRequest) => {
    if (started.current.has(request.id)) return
    started.current.add(request.id)
    setStates((current) => ({ ...current, [request.id]: { status: 'installing' } }))
    try {
      const result = await installPlugin(
        request.plugin_id,
        request.repository_url,
        request.branch,
        { version: request.version }
      )
      if (!result.success) throw new Error(result.message)
      setStates((current) => ({
        ...current,
        [request.id]: { status: 'success', message: result.message },
      }))
    } catch (error) {
      // 失败后不自动重试，必须由用户再次点击确认。
      started.current.delete(request.id)
      setStates((current) => ({
        ...current,
        [request.id]: {
          status: 'error',
          message: error instanceof Error ? error.message : t('search.pluginInstallFailed'),
        },
      }))
    }
  }

  return (
    <>
      {installRequests.map((request) => {
        const state = states[request.id]
        const canConfirm = !state || state.status === 'error'
        return (
          <section
            key={request.id}
            className="border-border/70 space-y-2 rounded-lg border p-3"
            aria-label={t('search.pluginInstallTitle')}
          >
            <div className="text-sm font-medium">
              {t('search.pluginInstallTitle')} · {request.name}
            </div>
            <div className="text-muted-foreground text-xs break-all">
              {request.plugin_id} ·{' '}
              {request.mode === 'branch' ? request.branch : `v${request.version}`}
            </div>
            <div className="text-muted-foreground text-xs break-all">{request.repository_url}</div>
            {canConfirm && (
              <p className="text-muted-foreground text-sm">{t('search.pluginInstallPending')}</p>
            )}
            {state && (
              <p
                role="status"
                className={state.status === 'error' ? 'text-destructive text-sm' : 'text-sm'}
              >
                {state.message || t(`search.pluginInstallStatus.${state.status}`)}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {canConfirm && (
                <>
                  <Button size="sm" onClick={() => void confirmInstall(request)}>
                    {t('search.pluginConfirmInstall')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      started.current.add(request.id)
                      setStates((current) => ({
                        ...current,
                        [request.id]: { status: 'cancelled' },
                      }))
                    }}
                  >
                    {t('search.pluginCancelInstall')}
                  </Button>
                </>
              )}
              {state?.status === 'success' && (
                <Button size="sm" variant="outline" onClick={() => onOpenConfig(request.plugin_id)}>
                  {t('search.pluginOpenConfig')}
                </Button>
              )}
            </div>
          </section>
        )
      })}
      {configUpdates.map((update) => (
        <section
          key={update.id}
          className="border-border/70 space-y-2 rounded-lg border p-3"
          aria-label={t('search.pluginConfigUpdated')}
        >
          <div className="text-sm font-medium">
            {t('search.pluginConfigUpdated')} · {update.name}
          </div>
          <p className="text-muted-foreground text-xs">{update.message}</p>
          <dl className="space-y-2 text-xs">
            {update.changes.map((change) => (
              <div key={JSON.stringify(change.path)}>
                <dt className="font-mono break-all">{change.path.join('.')}</dt>
                <dd className="text-muted-foreground mt-1 max-h-32 overflow-auto break-all whitespace-pre-wrap">
                  {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                </dd>
              </div>
            ))}
          </dl>
          <Button size="sm" variant="outline" onClick={() => onOpenConfig(update.plugin_id)}>
            {t('search.pluginOpenConfig')}
          </Button>
        </section>
      ))}
    </>
  )
}
