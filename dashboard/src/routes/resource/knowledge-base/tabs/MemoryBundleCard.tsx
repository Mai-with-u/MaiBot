import { useCallback, useEffect, useMemo, useState } from 'react'

import { Download, Loader2, PackageOpen, RefreshCw, Trash2, Upload } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { formatChatDisplayName } from '@/lib/chat-display'
import {
  downloadMemoryBundle,
  exportMemoryBundle,
  getMemoryBundles,
  importMemoryBundle,
  uninstallMemoryBundle,
  type MemoryBundleContentLevel,
  type MemoryBundleInstallationPayload,
  type MemoryBundleSelectorType,
  type MemoryImportChatTargetPayload,
} from '@/lib/memory-api'

interface MemoryBundleCardProps {
  chatTargets: MemoryImportChatTargetPayload[]
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function selectorPayload(type: MemoryBundleSelectorType, value: string): Record<string, unknown> {
  const token = value.trim()
  if (type === 'all') {
    return { type }
  }
  if (!token) {
    throw new Error('请填写导出范围')
  }
  if (type === 'chat') {
    return { type, chat_id: token }
  }
  if (type === 'source') {
    return { type, value: token }
  }
  if (type === 'import_task') {
    return { type, task_id: token }
  }
  return { type, package_id: token }
}

export function MemoryBundleCard({ chatTargets }: MemoryBundleCardProps) {
  const [contentLevel, setContentLevel] = useState<MemoryBundleContentLevel>('knowledge')
  const [selectorType, setSelectorType] = useState<MemoryBundleSelectorType>('all')
  const [selectorValue, setSelectorValue] = useState('')
  const [packageName, setPackageName] = useState('A_Memorix 知识包')
  const [packageId, setPackageId] = useState('')
  const [packageVersion, setPackageVersion] = useState('1.0.0')
  const [includeVectors, setIncludeVectors] = useState(true)
  const [exporting, setExporting] = useState(false)

  const [bundleFile, setBundleFile] = useState<File | null>(null)
  const [installScope, setInstallScope] = useState<'global' | 'chat'>('global')
  const [installChatId, setInstallChatId] = useState('')
  const [installMode, setInstallMode] = useState<'merge' | 'restore'>('merge')
  const [installing, setInstalling] = useState(false)
  const [installations, setInstallations] = useState<MemoryBundleInstallationPayload[]>([])
  const [loadingInstallations, setLoadingInstallations] = useState(false)
  const [uninstallingId, setUninstallingId] = useState('')
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const chatOptions = useMemo(() => chatTargets.slice(0, 200), [chatTargets])

  const refreshInstallations = useCallback(async () => {
    setLoadingInstallations(true)
    try {
      const payload = await getMemoryBundles(50)
      if (!payload.success) {
        throw new Error(payload.error || '读取已安装知识包失败')
      }
      setInstallations(payload.items || [])
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setLoadingInstallations(false)
    }
  }, [])

  useEffect(() => {
    void refreshInstallations()
  }, [refreshInstallations])

  const handleExport = async () => {
    setExporting(true)
    setNotice(null)
    try {
      if (!packageName.trim() || !packageVersion.trim()) {
        throw new Error('包名称和版本不能为空')
      }
      const payload = await exportMemoryBundle({
        content_level: contentLevel,
        selector: selectorPayload(selectorType, selectorValue),
        include_vectors: includeVectors,
        package: {
          id: packageId.trim() || undefined,
          version: packageVersion.trim(),
          name: packageName.trim(),
        },
      })
      if (!payload.success || !payload.file_name) {
        throw new Error(payload.error || '导出记忆包失败')
      }
      const blob = await downloadMemoryBundle(payload.file_name)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = payload.file_name
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      const paragraphCount = Number(payload.counts?.paragraphs ?? 0)
      setNotice({ kind: 'success', text: `已导出 ${paragraphCount} 条段落：${payload.file_name}` })
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setExporting(false)
    }
  }

  const handleInstall = async () => {
    setInstalling(true)
    setNotice(null)
    try {
      if (!bundleFile) {
        throw new Error('请选择 .amembundle 文件')
      }
      if (!bundleFile.name.toLowerCase().endsWith('.amembundle')) {
        throw new Error('只支持 .amembundle 记忆包')
      }
      if (installScope === 'chat' && !installChatId) {
        throw new Error('请选择安装目标聊天流')
      }
      const payload = await importMemoryBundle(bundleFile, {
        scope_type: installScope,
        chat_id: installScope === 'chat' ? installChatId : undefined,
        mode: installMode,
      })
      if (!payload.success) {
        throw new Error(payload.error || '安装记忆包失败')
      }
      setNotice({
        kind: 'success',
        text: payload.already_installed
          ? `知识包 ${payload.package?.name || ''} 已安装，无需重复写入`
          : `已安装 ${payload.package?.name || bundleFile.name}，映射 ${payload.mapped_paragraphs ?? 0} 条段落`,
      })
      await refreshInstallations()
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setInstalling(false)
    }
  }

  const handleUninstall = async (installation: MemoryBundleInstallationPayload) => {
    if (!window.confirm(`确定卸载知识包“${installation.name}”及其独占记忆吗？共享记忆会被保留。`)) {
      return
    }
    setUninstallingId(installation.installation_id)
    setNotice(null)
    try {
      const payload = await uninstallMemoryBundle(installation.installation_id)
      if (!payload.success) {
        throw new Error(payload.error || '卸载知识包失败')
      }
      const removedParagraphs = Number(payload.removed?.paragraphs ?? 0)
      setNotice({
        kind: 'success',
        text: `${payload.message || '知识包已卸载'}，移除 ${removedParagraphs} 条独占段落`,
      })
      await refreshInstallations()
    } catch (error) {
      setNotice({ kind: 'error', text: errorMessage(error) })
    } finally {
      setUninstallingId('')
    }
  }

  return (
    <Card className="border-border/70 rounded-2xl shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PackageOpen className="h-4 w-4" />
          可分享记忆包
        </CardTitle>
        <CardDescription>
          导出 LPMM
          同语义知识，或连同人物画像、Episode、事实账本和生命周期状态完整迁移。安装过程不调用 LLM
          抽取。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {notice ? (
          <Alert variant={notice.kind === 'error' ? 'destructive' : 'default'}>
            <AlertDescription>{notice.text}</AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-2">
          <div className="border-border/60 space-y-4 rounded-xl border p-4">
            <div>
              <div className="font-medium">导出记忆包</div>
              <div className="text-muted-foreground mt-1 text-xs">
                范围与内容级别互相独立，可导出全部、聊天流、来源、旧导入任务或已安装知识包。
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>内容级别</Label>
                <Select
                  value={contentLevel}
                  onValueChange={(value) => setContentLevel(value as MemoryBundleContentLevel)}
                >
                  <SelectTrigger aria-label="记忆包内容级别">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="knowledge">知识：段落、实体、三元组</SelectItem>
                    <SelectItem value="full">完整：全部相关语义状态</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>导出范围</Label>
                <Select
                  value={selectorType}
                  onValueChange={(value) => {
                    setSelectorType(value as MemoryBundleSelectorType)
                    setSelectorValue('')
                  }}
                >
                  <SelectTrigger aria-label="记忆包导出范围">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部记忆</SelectItem>
                    <SelectItem value="chat">指定聊天流</SelectItem>
                    <SelectItem value="source">指定来源</SelectItem>
                    <SelectItem value="import_task">指定导入任务</SelectItem>
                    <SelectItem value="package">已安装知识包</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {selectorType === 'chat' ? (
              <div className="space-y-2">
                <Label>聊天流</Label>
                <Select value={selectorValue} onValueChange={setSelectorValue}>
                  <SelectTrigger aria-label="导出聊天流">
                    <SelectValue placeholder="选择群聊或私聊" />
                  </SelectTrigger>
                  <SelectContent>
                    {chatOptions.map((chat) => (
                      <SelectItem key={chat.chat_id} value={chat.chat_id}>
                        {formatChatDisplayName(chat.chat_name, chat.account_id)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : selectorType !== 'all' ? (
              <div className="space-y-2">
                <Label>
                  {selectorType === 'source'
                    ? '来源'
                    : selectorType === 'import_task'
                      ? '导入任务 ID'
                      : '包 ID 或安装 ID'}
                </Label>
                <Input
                  value={selectorValue}
                  onChange={(event) => setSelectorValue(event.target.value)}
                />
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
              <div className="space-y-2">
                <Label>包名称</Label>
                <Input
                  value={packageName}
                  onChange={(event) => setPackageName(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>版本</Label>
                <Input
                  value={packageVersion}
                  onChange={(event) => setPackageVersion(event.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>包 ID，可留空自动生成</Label>
              <Input
                value={packageId}
                onChange={(event) => setPackageId(event.target.value)}
                placeholder="例如 com.example.worldbook"
              />
            </div>
            <Label
              htmlFor="memory-bundle-include-vectors"
              className="flex items-center gap-2 text-sm"
            >
              <Checkbox
                id="memory-bundle-include-vectors"
                checked={includeVectors}
                onCheckedChange={(checked) => setIncludeVectors(checked === true)}
              />
              携带兼容向量，加快相同 embedding 配置下的安装
            </Label>
            <Button onClick={() => void handleExport()} disabled={exporting}>
              {exporting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Download className="mr-2 h-4 w-4" />
              )}
              导出并下载
            </Button>
          </div>

          <div className="border-border/60 space-y-4 rounded-xl border p-4">
            <div>
              <div className="font-medium">安装记忆包</div>
              <div className="text-muted-foreground mt-1 text-xs">
                合并模式可安全去重；恢复模式只接受完整包，并要求目标记忆库为空。
              </div>
            </div>
            <div className="space-y-2">
              <Label>记忆包文件</Label>
              <Input
                type="file"
                accept=".amembundle,application/vnd.a-memorix.bundle+zip"
                onChange={(event) => setBundleFile(event.target.files?.[0] || null)}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>安装范围</Label>
                <Select
                  value={installScope}
                  onValueChange={(value) => setInstallScope(value as 'global' | 'chat')}
                >
                  <SelectTrigger aria-label="记忆包安装范围">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="global">所有聊天可用</SelectItem>
                    <SelectItem value="chat">指定聊天流</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>安装方式</Label>
                <Select
                  value={installMode}
                  onValueChange={(value) => setInstallMode(value as 'merge' | 'restore')}
                >
                  <SelectTrigger aria-label="记忆包安装方式">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="merge">合并安装</SelectItem>
                    <SelectItem value="restore">空库恢复</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {installScope === 'chat' ? (
              <div className="space-y-2">
                <Label>目标聊天流</Label>
                <Select value={installChatId} onValueChange={setInstallChatId}>
                  <SelectTrigger aria-label="记忆包目标聊天流">
                    <SelectValue placeholder="选择群聊或私聊" />
                  </SelectTrigger>
                  <SelectContent>
                    {chatOptions.map((chat) => (
                      <SelectItem key={chat.chat_id} value={chat.chat_id}>
                        {formatChatDisplayName(chat.chat_name, chat.account_id)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
            <Button onClick={() => void handleInstall()} disabled={installing}>
              {installing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="mr-2 h-4 w-4" />
              )}
              安装知识包
            </Button>

            <div className="border-border/60 border-t pt-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-sm font-medium">已安装</div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void refreshInstallations()}
                  disabled={loadingInstallations}
                >
                  <RefreshCw
                    className={`mr-1 h-3.5 w-3.5 ${loadingInstallations ? 'animate-spin' : ''}`}
                  />
                  刷新安装记录
                </Button>
              </div>
              {installations.length ? (
                <div className="space-y-2">
                  {installations.slice(0, 6).map((item) => (
                    <div
                      key={item.installation_id}
                      className="bg-muted/40 flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium">{item.name}</div>
                        <div className="text-muted-foreground truncate text-xs">
                          {item.package_id} · {item.version} · {item.paragraph_count} 条
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Badge variant={item.status === 'installed' ? 'outline' : 'destructive'}>
                          {item.status === 'installed'
                            ? item.content_level === 'full'
                              ? '完整'
                              : '知识'
                            : '安装未完成'}
                        </Badge>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => void handleUninstall(item)}
                          disabled={Boolean(uninstallingId)}
                        >
                          {uninstallingId === item.installation_id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" />
                          )}
                          <span className="sr-only">卸载 {item.name}</span>
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-muted-foreground text-xs">还没有安装记录</div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
