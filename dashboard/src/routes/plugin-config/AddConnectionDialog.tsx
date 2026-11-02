import { useEffect, useRef, useState } from 'react'
import { Download, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { checkGitStatus, fetchPluginList, getInstalledPlugins, installPlugin } from '@/lib/plugin-api'
import { isNapcatAdapter, mergeInstalledPluginInfo } from '@/lib/plugin-info'
import { recordPluginDownload } from '@/lib/plugin-stats'
import { InstallDialog } from '../plugins/InstallDialog'
import { PluginIcon } from '../plugins/PluginIcon'
import { getPluginType, type PluginInfo, type PluginLoadProgress } from '../plugins/types'

interface AddConnectionDialogProps {
  onClose: () => void
  onInstalled: () => void
}

function isOfficialAdapter(plugin: PluginInfo): boolean {
  const repository = plugin.manifest.repository_url || plugin.manifest.urls?.repository
  if (!repository) return false
  try {
    const url = new URL(repository)
    const owner = url.pathname.split('/')[1]?.toLowerCase()
    return ['github.com', 'cnb.cool'].includes(url.hostname.toLowerCase())
      && (owner === 'mai-with-u' || owner === 'maim-with-u')
  } catch {
    return false
  }
}

function getDownloads(plugin: PluginInfo): number | undefined {
  return plugin.marketplace_stats?.downloads ?? plugin.downloads
}

export function AddConnectionDialog({ onClose, onInstalled }: AddConnectionDialogProps) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [query, setQuery] = useState('')
  const [gitAvailable, setGitAvailable] = useState(false)
  const [selected, setSelected] = useState<PluginInfo | null>(null)
  const [progress, setProgress] = useState<PluginLoadProgress | null>(null)
  const installingRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const [market, installed, git] = await Promise.all([
          fetchPluginList({ compatibleOnly: true }),
          getInstalledPlugins({ forceRefresh: true }),
          checkGitStatus(),
        ])
        if (cancelled) return
        setPlugins(mergeInstalledPluginInfo(market, installed)
          .filter(plugin => plugin.source !== 'local' && getPluginType(plugin) === 'adapter' && !isNapcatAdapter(plugin)))
        setGitAvailable(git.installed)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '获取适配器失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [revision])

  async function handleInstall(target: string) {
    if (!selected || installingRef.current || !target.trim()) return
    const plugin = selected
    const catalog = plugin.releases
    if (catalog?.mode === 'releases') {
      const release = catalog.versions.find(item => item.version === target)
      if (catalog.sync_error || !release?.compatible || release.yanked) return
    }
    installingRef.current = true
    const base = { operation: 'install' as const, plugin_id: plugin.id, total_plugins: 1, loaded_plugins: 0 }
    setProgress({ ...base, stage: 'loading', progress: 0, message: `正在下载安装 ${plugin.manifest.name}` })
    try {
      const repository = plugin.manifest.repository_url || plugin.manifest.urls?.repository || ''
      await installPlugin(plugin.id, repository, catalog?.mode === 'releases' ? 'main' : target,
        catalog?.mode === 'releases' ? { version: target } : undefined)
      setProgress({ ...base, stage: 'success', progress: 100, loaded_plugins: 1, message: '适配器安装成功，可返回连接列表进行配置。' })
      setPlugins(current => current.map(item => item.id === plugin.id ? { ...item, installed: true } : item))
      if (plugin.manifest.id) {
        void recordPluginDownload(plugin.manifest.id).catch(cause => console.warn('Failed to record download:', cause))
      }
      onInstalled()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '安装失败'
      setProgress({ ...base, stage: 'error', progress: 0, message, error: message })
    } finally {
      installingRef.current = false
    }
  }

  const keyword = query.trim().toLowerCase()
  const visible = plugins.filter(plugin =>
    [plugin.id, plugin.manifest.name, plugin.manifest.description].some(value => value.toLowerCase().includes(keyword)))
    .sort((left, right) => {
      const leftDownloads = getDownloads(left)
      const rightDownloads = getDownloads(right)
      if (leftDownloads === undefined) return rightDownloads === undefined ? 0 : 1
      if (rightDownloads === undefined) return -1
      return rightDownloads - leftDownloads
    })
  const groups = [
    { title: '官方适配器', plugins: visible.filter(isOfficialAdapter) },
    { title: '社区适配器', plugins: visible.filter(plugin => !isOfficialAdapter(plugin)) },
  ]

  return (
    <Dialog open onOpenChange={open => { if (!open && !installingRef.current && !selected) onClose() }}>
      <DialogContent className="[--dialog-width:48rem]" preventOutsideClose={!!selected}>
        <DialogHeader>
          <DialogTitle>添加连接</DialogTitle>
          <DialogDescription>从插件市场选择适配器下载安装。安装后可在连接列表中配置，部分适配器需要重启麦麦才能生效。</DialogDescription>
        </DialogHeader>
        <Input aria-label="搜索适配器" placeholder="搜索适配器名称、平台或描述…" value={query} onChange={event => setQuery(event.target.value)} />
        <div className="min-h-0 space-y-3 overflow-y-auto">
          {loading ? (
            <div role="status" className="flex items-center justify-center gap-2 py-10"><Loader2 className="h-5 w-5 animate-spin" />正在获取适配器…</div>
          ) : error ? (
            <div role="alert" className="space-y-3"><p className="text-destructive">{error}</p><Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新加载</Button></div>
          ) : (
            <>
              {!gitAvailable && <p role="alert" className="text-destructive text-sm">Git 未安装，请安装 Git 并重启麦麦后再下载适配器。</p>}
              {visible.length === 0 && <p className="text-muted-foreground py-8 text-center">{keyword ? '没有匹配的适配器' : '暂无兼容的适配器插件'}</p>}
              {groups.filter(group => group.plugins.length > 0).map(group => (
                <section key={group.title} className="space-y-3">
                  <div className="flex items-center gap-3 py-2">
                    <h3 className="shrink-0 text-sm font-medium">{group.title}</h3>
                    <Separator className="min-w-0 flex-1" />
                  </div>
                  {group.plugins.map(plugin => (
                <div key={plugin.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3">
                  <PluginIcon pluginId={plugin.id} manifest={plugin.manifest}
                    installed={plugin.installed} marketplaceIconUrl={plugin.assets?.icon_64}
                    className="h-10 w-10 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{plugin.manifest.name}</p>
                    <p className="text-muted-foreground text-sm">{plugin.manifest.description}</p>
                    <p className="text-muted-foreground mt-1 text-xs">
                      {getDownloads(plugin) === undefined ? '暂无下载统计' : `下载 ${getDownloads(plugin)?.toLocaleString('zh-CN')} 次`}
                    </p>
                  </div>
                  <Button size="sm" variant="outline" disabled={plugin.installed || !gitAvailable} onClick={() => { setProgress(null); setSelected(plugin) }}>
                    {!plugin.installed && <Download className="h-4 w-4" />}{plugin.installed ? '已安装' : '下载安装'}
                  </Button>
                </div>
                  ))}
                </section>
              ))}
            </>
          )}
        </div>
        <InstallDialog open={selected !== null} plugin={selected} loadProgress={progress}
          onOpenChange={open => { if (!open && !installingRef.current) setSelected(null) }}
          onInstall={target => void handleInstall(target)} />
      </DialogContent>
    </Dialog>
  )
}
