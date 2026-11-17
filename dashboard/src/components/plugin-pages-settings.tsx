import { Fragment, type ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import type { WebUIExtension } from '@/lib/plugin-webui'

export interface PluginPagePreferences {
  hidden: string[]
  order: string[]
}

/** 正式设置与预览共用同一个受控页面，数据加载和持久化留给调用方。 */
export function PluginPagesSettings({
  extensions,
  preferences,
  loading = false,
  error,
  onRefresh,
  onPreferencesChange,
  renderPageLink,
}: {
  extensions: WebUIExtension[]
  preferences: PluginPagePreferences
  loading?: boolean
  error?: string | null
  onRefresh: () => void
  onPreferencesChange: (preferences: PluginPagePreferences) => void
  renderPageLink: (extension: WebUIExtension, page: WebUIExtension['pages'][number]) => ReactNode
}) {
  const { t } = useTranslation()
  return (
    <section
      id="webui-extensions"
      className="plugin-pages-settings"
      aria-labelledby="webui-extensions-title"
    >
      <header className="plugin-pages-heading">
        <div>
          <h2 id="webui-extensions-title">{t('pluginWebUI.settingsTitle')}</h2>
        </div>
        <Button variant="outline" disabled={loading} onClick={onRefresh}>
          <RefreshCw className={loading ? 'animate-spin' : undefined} />
          {t('pluginWebUI.refresh')}
        </Button>
      </header>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {loading && <p role="status">{t('pluginWebUI.loading')}</p>}
      {!loading && extensions.length === 0 && (
        <p className="text-muted-foreground py-8 text-center">{t('pluginWebUI.empty')}</p>
      )}
      {extensions.length > 0 && (
        <ul className="plugin-pages-list">
          {extensions.map((extension) => {
            const title = extension.workspace_title ?? extension.plugin_id
            const shown = !preferences.hidden.includes(extension.plugin_id)
            return (
              <li key={extension.plugin_id} className="plugin-pages-row" data-hidden={!shown}>
                <div className="plugin-pages-details">
                  {extension.pages.length !== 1 && (
                    <div className="plugin-pages-title"><h3>{title}</h3></div>
                  )}
                    <div className="plugin-pages-links">
                      {extension.pages.map((page) => (
                        <Fragment key={page.id}>
                          {shown ? (
                            renderPageLink(extension, page)
                          ) : (
                            <Button variant="outline" size="xs" disabled>
                              {page.title}
                            </Button>
                          )}
                        </Fragment>
                      ))}
                    </div>
                    {extension.compatibility_error && (
                      <p className="text-destructive text-sm">
                        {extension.compatibility_error}
                      </p>
                    )}
                  </div>
                  <div className="plugin-pages-actions">
                    <label className="plugin-pages-visibility">
                      <span>{t('pluginWebUI.visibleLabel')}</span>
                      <Switch
                        aria-label={t('pluginWebUI.show', { plugin: title })}
                        checked={shown}
                        onCheckedChange={(checked) =>
                          onPreferencesChange({
                            ...preferences,
                            hidden: checked
                              ? preferences.hidden.filter((id) => id !== extension.plugin_id)
                              : [...preferences.hidden, extension.plugin_id],
                          })
                        }
                      />
                    </label>
                  </div>
                </li>
              )
            })}
          </ul>
      )}
    </section>
  )
}
