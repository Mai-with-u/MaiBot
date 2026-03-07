import { useNavigate } from '@tanstack/react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { AlertCircle, CheckCircle2, Download, Loader2, RefreshCw, Star, Trash2 } from 'lucide-react'

import type { GitStatus, MaimaiVersion, PluginInfo, PluginLoadProgress, PluginStatsData } from './types'
import { CATEGORY_NAMES } from './types'

interface PluginCardProps {
  plugin: PluginInfo
  gitStatus: GitStatus | null
  maimaiVersion: MaimaiVersion | null
  pluginStats: Record<string, PluginStatsData>
  loadProgress: PluginLoadProgress | null
  onInstall: (plugin: PluginInfo) => void
  onUpdate: (plugin: PluginInfo) => void
  onUninstall: (plugin: PluginInfo) => void
  checkPluginCompatibility: (plugin: PluginInfo) => boolean
  needsUpdate: (plugin: PluginInfo) => boolean
  getStatusBadge: (plugin: PluginInfo) => React.JSX.Element | null
}

export function PluginCard({
  plugin,
  gitStatus,
  maimaiVersion,
  pluginStats,
  loadProgress,
  onInstall,
  onUpdate,
  onUninstall,
  checkPluginCompatibility,
  needsUpdate,
  getStatusBadge,
}: PluginCardProps) {
  const navigate = useNavigate()

  return (
    <Card
      key={plugin.id}
      className="flex flex-col hover:shadow-lg transition-shadow h-full"
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-xl">{plugin.manifest?.name || plugin.id}</CardTitle>
          <div className="flex flex-col gap-1">
            {plugin.manifest?.categories && plugin.manifest.categories[0] && (
              <Badge variant="secondary" className="text-xs whitespace-nowrap">
                {CATEGORY_NAMES[plugin.manifest.categories[0]] || plugin.manifest.categories[0]}
              </Badge>
            )}
            {getStatusBadge(plugin)}
          </div>
        </div>
        <CardDescription className="line-clamp-2">{plugin.manifest?.description || '无描述'}</CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        <div className="space-y-3">
          {/* 统计信息 */}
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <div className="flex items-center gap-1">
              <Download className="h-4 w-4" />
              <span>{(pluginStats[plugin.id]?.downloads ?? plugin.downloads ?? 0).toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1">
              <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
              <span>{(pluginStats[plugin.id]?.rating ?? plugin.rating ?? 0).toFixed(1)}</span>
            </div>
          </div>
          {/* 标签 */}
          <div className="flex flex-wrap gap-2">
            {plugin.manifest?.keywords && plugin.manifest.keywords.slice(0, 3).map((keyword) => (
              <Badge key={keyword} variant="outline" className="text-xs">
                {keyword}
              </Badge>
            ))}
            {plugin.manifest?.keywords && plugin.manifest.keywords.length > 3 && (
              <Badge variant="outline" className="text-xs">
                +{plugin.manifest.keywords.length - 3}
              </Badge>
            )}
          </div>
          {/* 版本和作者 */}
          <div className="text-xs text-muted-foreground pt-2 border-t space-y-1">
            <div>v{plugin.manifest?.version || 'unknown'} · {plugin.manifest?.author?.name || 'Unknown'}</div>
            {/* 支持版本 */}
            {plugin.manifest?.host_application && (
              <div className="flex items-center gap-1">
                <span>支持:</span>
                <span className="font-medium">
                  {plugin.manifest.host_application.min_version}
                  {plugin.manifest.host_application.max_version 
                    ? ` - ${plugin.manifest.host_application.max_version}`
                    : ' - 最新版本'
                  }
                </span>
              </div>
            )}
          </div>
        </div>
      </CardContent>
      <CardFooter className="pt-4">
        <div className="flex items-center justify-end gap-2 w-full">
          <Button 
            variant="outline"
            size="sm"
            onClick={() => navigate({ to: '/plugin-detail', search: { pluginId: plugin.id } })}
          >
            查看详情
          </Button>
          {plugin.installed ? (
            needsUpdate(plugin) ? (
              <Button 
                size="sm"
                disabled={!gitStatus?.installed}
                title={!gitStatus?.installed ? 'Git 未安装' : undefined}
                onClick={() => onUpdate(plugin)}
              >
                <RefreshCw className="h-4 w-4 mr-1" />
                更新
              </Button>
            ) : (
              <Button 
                variant="destructive" 
                size="sm"
                disabled={!gitStatus?.installed}
                title={!gitStatus?.installed ? 'Git 未安装' : undefined}
                onClick={() => onUninstall(plugin)}
              >
                <Trash2 className="h-4 w-4 mr-1" />
                卸载
              </Button>
            )
          ) : (
            <Button 
              size="sm"
              disabled={
                !gitStatus?.installed || 
                loadProgress?.operation === 'install' ||
                (maimaiVersion !== null && !checkPluginCompatibility(plugin))
              }
              title={
                !gitStatus?.installed 
                  ? 'Git 未安装' 
                  : (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                    ? `不兼容当前版本 (需要 ${plugin.manifest?.host_application?.min_version || '未知'}${plugin.manifest?.host_application?.max_version ? ` - ${plugin.manifest.host_application.max_version}` : '+'}，当前 ${maimaiVersion?.version})`
                    : undefined
              }
              onClick={() => onInstall(plugin)}
            >
              <Download className="h-4 w-4 mr-1" />
              {loadProgress?.operation === 'install' && loadProgress?.plugin_id === plugin.id ? '安装中...' : '安装'}
            </Button>
          )}
        </div>
      </CardFooter>
      {/* 安装/卸载/更新进度显示 - 在卡片下方 */}
      {loadProgress && 
        (loadProgress.stage === 'loading' || loadProgress.stage === 'success' || loadProgress.stage === 'error') && 
        loadProgress.operation !== 'fetch' && 
        loadProgress.plugin_id === plugin.id && (
        <div className="px-6 pb-4 -mt-2">
          <div className={`space-y-2 p-3 rounded-lg border ${
            loadProgress.stage === 'success' 
              ? 'bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900' 
              : loadProgress.stage === 'error'
                ? 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900'
                : 'bg-muted/50'
          }`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {loadProgress.stage === 'loading' ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : loadProgress.stage === 'success' ? (
                  <CheckCircle2 className="h-3 w-3 text-green-600" />
                ) : (
                  <AlertCircle className="h-3 w-3 text-red-600" />
                )}
                <span className={`text-xs font-medium ${
                  loadProgress.stage === 'success' 
                    ? 'text-green-700 dark:text-green-300' 
                    : loadProgress.stage === 'error'
                      ? 'text-red-700 dark:text-red-300'
                      : ''
                }`}>
                  {loadProgress.stage === 'loading' ? (
                    <>
                      {loadProgress.operation === 'install' && '正在安装'}
                      {loadProgress.operation === 'uninstall' && '正在卸载'}
                      {loadProgress.operation === 'update' && '正在更新'}
                    </>
                  ) : loadProgress.stage === 'success' ? (
                    <>
                      {loadProgress.operation === 'install' && '安装完成'}
                      {loadProgress.operation === 'uninstall' && '卸载完成'}
                      {loadProgress.operation === 'update' && '更新完成'}
                    </>
                  ) : (
                    <>
                      {loadProgress.operation === 'install' && '安装失败'}
                      {loadProgress.operation === 'uninstall' && '卸载失败'}
                      {loadProgress.operation === 'update' && '更新失败'}
                    </>
                  )}
                </span>
              </div>
              {loadProgress.stage !== 'error' && (
                <span className={`text-xs font-medium ${
                  loadProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                }`}>{loadProgress.progress}%</span>
              )}
            </div>
            {loadProgress.stage !== 'error' && (
              <Progress 
                value={loadProgress.progress} 
                className={`h-1.5 ${loadProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`} 
              />
            )}
            <div className={`text-xs ${
              loadProgress.stage === 'success' 
                ? 'text-green-600 dark:text-green-400 truncate' 
                : loadProgress.stage === 'error'
                  ? 'text-red-600 dark:text-red-400'
                  : 'text-muted-foreground truncate'
            }`}>
              {loadProgress.stage === 'error' ? (loadProgress.error || loadProgress.message || '操作失败') : loadProgress.message}
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
