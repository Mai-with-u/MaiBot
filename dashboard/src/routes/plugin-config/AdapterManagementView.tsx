import type { ReactNode } from 'react'
import {
  AlertCircle,
  ArrowUp,
  Loader2,
  MoreHorizontal,
  Package,
  Trash2,
} from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ResourceRow, SectionPanel } from '@/components/ui/management'
import { DataPanel } from '@/components/ui/surface'
import { Switch } from '@/components/ui/switch'
import type { InstalledPlugin } from '@/lib/plugin-api'
import { PluginIcon } from '../plugins/PluginIcon'
import type { PluginListGroup, PluginStatusMeta, PluginUpdateState } from './hooks/usePluginList'
import { getPluginMarketplaceRoutePath } from './utils'
import { getPluginLoadFailureInfo } from './load-failure'

interface AdapterManagementViewProps {
  groups: PluginListGroup[]
  defaults: ReactNode
  loading?: boolean
  actingPluginId?: string | null
  checkingUpdates?: boolean
  getStatusMeta: (plugin: InstalledPlugin) => PluginStatusMeta
  getUpdateState: (plugin: InstalledPlugin) => PluginUpdateState
  isVersionIncompatible?: (plugin: InstalledPlugin) => boolean
  onConfigure: (plugin: InstalledPlugin) => void
  onToggle: (plugin: InstalledPlugin) => void
  onUpdate: (plugin: InstalledPlugin) => void
  onDelete: (plugin: InstalledPlugin) => void
  onFailureDetails: (plugin: InstalledPlugin) => void
  renderIcon?: (plugin: InstalledPlugin) => ReactNode
}

/** 适配器列表只组织展示，启停与生命周期操作仍由原有领域 hook 统一处理。 */
export function AdapterManagementView({
  groups,
  defaults,
  loading = false,
  actingPluginId,
  checkingUpdates = false,
  getStatusMeta,
  getUpdateState,
  isVersionIncompatible,
  onConfigure,
  onToggle,
  onUpdate,
  onDelete,
  onFailureDetails,
  renderIcon,
}: AdapterManagementViewProps) {
  return (
    <div className="space-y-5">
      {defaults}
      {loading ? (
        <div
          className="text-muted-foreground flex items-center justify-center gap-2 py-12 text-sm"
          role="status"
        >
          <Loader2 className="h-5 w-5 animate-spin" />
          正在加载适配器
        </div>
      ) : groups.length === 0 ? (
        <SectionPanel title="适配器">
          <DataPanel className="flex flex-col items-center gap-3 py-10 text-center">
            <Package className="text-muted-foreground h-10 w-10" />
            <p>暂无已安装的适配器</p>
            <Button asChild variant="outline" size="sm">
              <a href={getPluginMarketplaceRoutePath()}>前往插件市场安装适配器</a>
            </Button>
          </DataPanel>
        </SectionPanel>
      ) : (
        groups.map((group) => (
          <SectionPanel
            key={group.key}
            title={
              <span className="flex items-center gap-2">
                <span>{group.key === 'success' ? '运行中' : group.label}</span>
                <span
                  className="text-sm tabular-nums"
                  aria-label={`${group.plugins.length} 个适配器`}
                >
                  {group.plugins.length}
                </span>
              </span>
            }
            leading={<span className={`h-2.5 w-2.5 ${group.dotClassName}`} aria-hidden="true" />}
          >
            <DataPanel padding="none">
              {group.plugins.map((plugin) => {
                const acting = actingPluginId === plugin.id
                const update = getUpdateState(plugin)
                const baseStatus = getStatusMeta(plugin)
                const incompatible = isVersionIncompatible?.(plugin) ?? false
                const failure = getPluginLoadFailureInfo(
                  plugin,
                  update,
                  incompatible,
                  checkingUpdates,
                  '适配器'
                )
                const needsUpdate = failure.needsUpdate
                const status = incompatible
                  ? { ...baseStatus, label: needsUpdate ? '需要更新' : '版本不兼容' }
                  : baseStatus.label === '加载成功'
                    ? { ...baseStatus, label: '运行中' }
                    : baseStatus
                const failed = plugin.load_status === 'failed'
                return (
                  <ResourceRow
                    key={plugin.id}
                    onActivate={() => onConfigure(plugin)}
                    activationLabel={`打开 ${plugin.manifest.name} 配置`}
                    icon={
                      renderIcon ? (
                        renderIcon(plugin)
                      ) : (
                        <PluginIcon
                          pluginId={plugin.id}
                          manifest={plugin.manifest}
                          installed
                          className="h-full w-full rounded-[inherit]"
                          iconClassName="h-6 w-6"
                        />
                      )
                    }
                    title={plugin.manifest.name}
                    metadata={
                      <>
                        <Badge variant="outline" className="text-xs font-normal">
                          v{plugin.manifest.version}
                        </Badge>
                        <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
                          {status.icon === 'loading' ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <span className={`h-2 w-2 ${status.dotClassName}`} aria-hidden="true" />
                          )}
                          {status.label}
                        </span>
                        {update.hasUpdate && !incompatible && (
                          <Badge variant="outline" className="text-xs">
                            可更新
                          </Badge>
                        )}
                      </>
                    }
                    description={plugin.manifest.description || '暂无描述'}
                    actions={
                      <>
                        {acting && (
                          <Loader2 className="h-4 w-4 animate-spin" aria-label="正在操作适配器" />
                        )}
                        <Switch
                          size="lg"
                          checked={plugin.enabled !== false}
                          disabled={acting}
                          aria-label={`${plugin.enabled === false ? '启动' : '关闭'} ${plugin.manifest.name}`}
                          onCheckedChange={() => onToggle(plugin)}
                        />
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`${plugin.manifest.name} 更多操作`}
                              disabled={acting}
                            >
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              disabled={!update.canUpdate}
                              title={update.title}
                              onSelect={() => onUpdate(plugin)}
                            >
                              <ArrowUp className="mr-2 h-4 w-4" />
                              {update.hasUpdate
                                ? `更新至 v${update.latestVersion}`
                                : update.title || '更新 / 升级'}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="text-destructive"
                              onSelect={() => onDelete(plugin)}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              卸载适配器
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    }
                  >
                    {failed && (
                      <Alert variant="destructive" className="mt-2">
                        <AlertCircle className="h-4 w-4" />
                        <AlertTitle>{failure.title}</AlertTitle>
                        <AlertDescription>
                          <p className="break-words">{failure.description}</p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {needsUpdate && (
                              <Button
                                size="xs"
                                disabled={acting || !update.canUpdate}
                                onClick={() => onUpdate(plugin)}
                              >
                                立即更新
                              </Button>
                            )}
                            {incompatible && !needsUpdate && !checkingUpdates && (
                              <Button asChild variant="outline" size="xs">
                                <a href={getPluginMarketplaceRoutePath()}>前往插件市场</a>
                              </Button>
                            )}
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() => onFailureDetails(plugin)}
                            >
                              查看详情
                            </Button>
                          </div>
                        </AlertDescription>
                      </Alert>
                    )}
                  </ResourceRow>
                )
              })}
            </DataPanel>
          </SectionPanel>
        ))
      )}
    </div>
  )
}
