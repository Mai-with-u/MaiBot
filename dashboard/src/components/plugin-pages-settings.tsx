import { Fragment, type ReactNode } from 'react'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SectionPanel } from '@/components/ui/management'
import { OrderControls } from '@/components/ui/order-controls'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { Switch } from '@/components/ui/switch'
import { orderedWorkspaceExtensions, type WebUIExtension } from '@/lib/plugin-webui'
import { MAX_VISIBLE_PLUGIN_TABS, splitWorkspaceTabs } from '@/lib/workspace-tabs'

export interface PluginPagePreferences {
  hidden: string[]
  order: string[]
  workspaceOrder?: string[]
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
  // 兼容旧版独立保存的顶栏顺序；未提供顶栏入口的插件排在后面，仍可管理。
  const workspaces = orderedWorkspaceExtensions(
    extensions,
    preferences.workspaceOrder ?? preferences.order
  )
  const workspaceIds = new Set(workspaces.map((item) => item.plugin_id))
  const sidebar = extensions
    .filter((item) => !workspaceIds.has(item.plugin_id))
    .sort((a, b) => {
      const rank = (id: string) => {
        const index = preferences.order.indexOf(id)
        return index < 0 ? preferences.order.length : index
      }
      return rank(a.plugin_id) - rank(b.plugin_id) || a.plugin_id.localeCompare(b.plugin_id)
    })
  const ordered = [...workspaces, ...sidebar]
  const { visible, overflow } = splitWorkspaceTabs(
    workspaces.filter((item) => !preferences.hidden.includes(item.plugin_id))
  )
  const move = (extension: WebUIExtension, delta: -1 | 1) => {
    const group = workspaceIds.has(extension.plugin_id) ? workspaces : sidebar
    const index = group.indexOf(extension)
    const next = [...group]
    ;[next[index], next[index + delta]] = [next[index + delta], next[index]]
    const nextWorkspaces = group === workspaces ? next : workspaces
    const nextSidebar = group === sidebar ? next : sidebar
    onPreferencesChange({
      ...preferences,
      order: [...nextWorkspaces, ...nextSidebar].map((item) => item.plugin_id),
      workspaceOrder: nextWorkspaces.map((item) => item.plugin_id),
    })
  }
  return (
    <section
      id="webui-extensions"
      className="plugin-pages-settings"
      aria-labelledby="webui-extensions-title"
    >
      <header className="plugin-pages-heading">
        <div>
          <h2 id="webui-extensions-title">{t('pluginWebUI.settingsTitle')}</h2>
          <p>{t('pluginWebUI.settingsHint')}</p>
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
      <PageFrame className="p-4">
        <h3 className="text-muted-foreground mb-3 text-sm">{t('pluginWebUI.topbarPreview')}</h3>
        <DataPanel className="plugin-pages-preview">
          <div className="plugin-pages-preview-tabs" aria-label={t('pluginWebUI.topbarPreview')}>
            <Button variant="outline" size="sm" aria-pressed tabIndex={-1}>
              {t('workspace.settings')}
            </Button>
            <Button variant="outline" size="sm" tabIndex={-1}>
              {t('workspace.logs')}
            </Button>
            {visible.map((item) => (
              <Button
                key={item.plugin_id}
                variant="outline"
                size="sm"
                className="plugin-pages-preview-slot"
                tabIndex={-1}
              >
                {item.workspace_title ?? item.plugin_id}
              </Button>
            ))}
            {overflow.length > 0 && (
              <Button variant="outline" size="sm" tabIndex={-1}>
                {t('pluginWebUI.moreShort')}
                <Badge variant="secondary">{overflow.length}</Badge>
              </Button>
            )}
          </div>
          <p className="text-muted-foreground text-xs">
            {t('pluginWebUI.topbarLimit', { count: MAX_VISIBLE_PLUGIN_TABS })}
          </p>
        </DataPanel>
      </PageFrame>
      <SectionPanel
        title={t('pluginWebUI.orderAndVisibility')}
        description={t('pluginWebUI.pluginCount', { count: ordered.length })}
      >
        {loading && <p role="status">{t('pluginWebUI.loading')}</p>}
        {!loading && ordered.length === 0 && (
          <p className="text-muted-foreground py-8 text-center">{t('pluginWebUI.empty')}</p>
        )}
        {ordered.length > 0 && (
          <DataPanel padding="none">
            <ol className="plugin-pages-list">
              {ordered.map((extension, index) => {
                const title = extension.workspace_title ?? extension.plugin_id
                const shown = !preferences.hidden.includes(extension.plugin_id)
                const isWorkspace = workspaceIds.has(extension.plugin_id)
                const group = isWorkspace ? workspaces : sidebar
                const placement = !shown
                  ? 'hiddenLabel'
                  : visible.includes(extension)
                    ? 'topbarLabel'
                    : isWorkspace
                      ? 'overflowLabel'
                      : 'sidebarLabel'
                return (
                  <Fragment key={extension.plugin_id}>
                    {(extension === overflow[0] || extension === sidebar[0]) && (
                      <li className="plugin-pages-group-label">
                        <ChevronDown size={14} />
                        {t(
                          extension === sidebar[0]
                            ? 'pluginWebUI.sidebarGroup'
                            : 'pluginWebUI.overflowGroup'
                        )}
                      </li>
                    )}
                    <li className="plugin-pages-row" data-hidden={!shown}>
                      <Badge variant="outline" className="plugin-pages-ordinal">
                        {index + 1}
                      </Badge>
                      <div className="plugin-pages-details">
                        <div className="plugin-pages-title">
                          <h3>{title}</h3>
                          <code>{extension.plugin_id}</code>
                          <Badge
                            variant={shown && visible.includes(extension) ? 'default' : 'outline'}
                          >
                            {t(`pluginWebUI.${placement}`)}
                          </Badge>
                        </div>
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
                        <OrderControls
                          label={title}
                          index={group.indexOf(extension)}
                          count={group.length}
                          upLabel={t('pluginWebUI.moveUp')}
                          downLabel={t('pluginWebUI.moveDown')}
                          onMove={(delta) => move(extension, delta)}
                        />
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
                  </Fragment>
                )
              })}
            </ol>
          </DataPanel>
        )}
      </SectionPanel>
    </section>
  )
}
