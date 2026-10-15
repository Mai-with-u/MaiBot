import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import {
  extensionPath,
  orderedWorkspaceExtensions,
  refreshPluginWebUI,
  setPluginWebUIPreferences,
  usePluginWebUI,
} from '@/lib/plugin-webui'

export function PluginWebUIManagerPanel() {
  const registry = usePluginWebUI(true)
  const { t } = useTranslation()
  const ordered = [...registry.extensions].sort((a, b) => {
    const rank = (id: string) => {
      const i = registry.preferences.order.indexOf(id)
      return i < 0 ? registry.preferences.order.length : i
    }
    return rank(a.plugin_id) - rank(b.plugin_id) || a.plugin_id.localeCompare(b.plugin_id)
  })
  const move = (index: number, delta: number) => {
    const order = ordered.map((item) => item.plugin_id)
    ;[order[index], order[index + delta]] = [order[index + delta], order[index]]
    setPluginWebUIPreferences({ ...registry.preferences, order })
  }
  const workspaces = orderedWorkspaceExtensions(
    registry.extensions,
    registry.preferences.workspaceOrder ?? registry.preferences.order
  )
  const moveWorkspace = (index: number, delta: number) => {
    const workspaceOrder = workspaces.map((item) => item.plugin_id)
    ;[workspaceOrder[index], workspaceOrder[index + delta]] = [
      workspaceOrder[index + delta],
      workspaceOrder[index],
    ]
    setPluginWebUIPreferences({ ...registry.preferences, workspaceOrder })
  }
  return (
    <section
      id="webui-extensions"
      className="space-y-6 border-t pt-6"
      aria-labelledby="webui-extensions-title"
    >
      <div className="flex items-center justify-between">
        <h2 id="webui-extensions-title" className="text-lg font-semibold">
          {t('pluginWebUI.manage')}
        </h2>
        <Button
          variant="outline"
          onClick={() => {
            void refreshPluginWebUI()
          }}
        >
          {t('pluginWebUI.refresh')}
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">{t('pluginWebUI.preferencesHint')}</p>
      {registry.error && (
        <Alert variant="destructive">
          <AlertDescription>{registry.error}</AlertDescription>
        </Alert>
      )}
      {registry.loading && <p>{t('pluginWebUI.loading')}</p>}
      {!registry.loading && !registry.error && ordered.length === 0 && (
        <p>{t('pluginWebUI.empty')}</p>
      )}
      {workspaces.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t('pluginWebUI.workspaceOrder')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-muted-foreground text-sm">{t('pluginWebUI.workspaceOrderHint')}</p>
            <ol className="space-y-2">
              {workspaces.map((extension, index) => {
                const title = extension.workspace_title ?? extension.plugin_id
                return (
                  <li key={extension.plugin_id} className="flex items-center gap-2">
                    <span className="text-muted-foreground w-6 shrink-0 text-center text-sm">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate" title={title}>
                      {title}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={index === 0}
                      aria-label={`${title}: ${t('pluginWebUI.moveUp')}`}
                      onClick={() => moveWorkspace(index, -1)}
                    >
                      {t('pluginWebUI.moveUp')}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={index === workspaces.length - 1}
                      aria-label={`${title}: ${t('pluginWebUI.moveDown')}`}
                      onClick={() => moveWorkspace(index, 1)}
                    >
                      {t('pluginWebUI.moveDown')}
                    </Button>
                  </li>
                )
              })}
            </ol>
          </CardContent>
        </Card>
      )}
      {ordered.map((extension, index) => (
        <Card key={extension.plugin_id}>
          <CardHeader>
            <CardTitle className="flex items-center justify-between gap-4">
              <span>{extension.workspace_title ?? extension.plugin_id}</span>
              <Switch
                aria-label={t('pluginWebUI.show', { plugin: extension.plugin_id })}
                checked={!registry.preferences.hidden.includes(extension.plugin_id)}
                onCheckedChange={(shown) =>
                  setPluginWebUIPreferences({
                    ...registry.preferences,
                    hidden: shown
                      ? registry.preferences.hidden.filter((id) => id !== extension.plugin_id)
                      : [...registry.preferences.hidden, extension.plugin_id],
                  })
                }
              />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-xs">{extension.plugin_id}</p>
            <div className="flex flex-wrap gap-3">
              {extension.pages.map((page) => (
                <Link
                  key={page.id}
                  to={extensionPath(extension.plugin_id, page.id)}
                  className="text-primary underline"
                >
                  {page.title}
                </Link>
              ))}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" disabled={index === 0} onClick={() => move(index, -1)}>
                {t('pluginWebUI.moveUp')}
              </Button>
              <Button
                variant="outline"
                disabled={index === ordered.length - 1}
                onClick={() => move(index, 1)}
              >
                {t('pluginWebUI.moveDown')}
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  )
}
