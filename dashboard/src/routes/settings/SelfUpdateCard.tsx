import { Card } from '@/components/ui/card'
import { AlertTriangle, CheckCircle2, Download, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { RestartOverlay } from '@/components/restart-overlay'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { markSelfUpdatePending } from '@/lib/self-update-pending'
import {
  applySelfUpdate,
  checkSelfUpdate,
  getSelfUpdateStatus,
  getSelfUpdateCustomPrefix,
  SELF_UPDATE_STATUS_EVENT,
  type SelfUpdateResult,
  type SelfUpdateStatus,
  type SelfUpdateSourceSelection,
} from '@/lib/system-api'

// 更新要拉取代码并同步依赖，比普通重启慢得多：每 2 秒检查一次，最多等待约 15 分钟。
const UPDATE_HEALTH_CHECK_ATTEMPTS = 450

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString()
}

function formatRevision(version: string, commit: string): string {
  return commit ? `${version} (${commit.slice(0, 8)})` : version
}

/** 主程序自更新卡片：检查官方仓库的新版本，并在确认后更新、重启。 */
export function SelfUpdateCard() {
  const { t } = useTranslation()

  return (
    <RestartProvider>
      <SelfUpdateCardContent />
      <RestartOverlay
        title={t('settings.selfUpdate.overlayTitle')}
        description={t('settings.selfUpdate.overlayDesc')}
      />
    </RestartProvider>
  )
}

function SelfUpdateCardContent() {
  const { t } = useTranslation()
  const { toast } = useToast()
  const { triggerRestart, isRestarting } = useRestart()
  const [status, setStatus] = useState<SelfUpdateStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [sourceId, setSourceId] = useState<SelfUpdateSourceSelection['source_id'] | ''>('')
  const [customPrefix, setCustomPrefix] = useState(getSelfUpdateCustomPrefix)
  const [checkedSource, setCheckedSource] = useState<SelfUpdateSourceSelection | null>(null)

  useEffect(() => {
    let cancelled = false
    let receivedCheck = false
    const handleStatus = (event: Event) => {
      receivedCheck = true
      setStatus((event as CustomEvent<SelfUpdateStatus>).detail)
      // 自动检查只提供展示状态；应用更新仍由用户选择并确认本次来源。
      setCheckedSource(null)
      setLoadError(null)
    }
    window.addEventListener(SELF_UPDATE_STATUS_EVENT, handleStatus)
    getSelfUpdateStatus()
      .then((next) => {
        if (!cancelled && !receivedCheck) setStatus(next)
      })
      .catch((error: unknown) => {
        if (!cancelled && !receivedCheck) setLoadError(errorMessage(error))
      })
    return () => {
      cancelled = true
      window.removeEventListener(SELF_UPDATE_STATUS_EVENT, handleStatus)
    }
  }, [])

  const handleCheck = async () => {
    if (!sourceId || (sourceId === 'custom' && !customPrefix.trim())) return
    const selection: SelfUpdateSourceSelection = {
      source_id: sourceId,
      ...(sourceId === 'custom' ? { custom_prefix: customPrefix.trim() } : {}),
    }
    setChecking(true)
    setCheckedSource(null)
    try {
      setStatus(await checkSelfUpdate(selection))
      setCheckedSource(selection)
      setSourceDialogOpen(false)
      setLoadError(null)
    } catch (error) {
      toast({
        title: t('settings.selfUpdate.checkFailed'),
        description: errorMessage(error),
        variant: 'destructive',
      })
    } finally {
      setChecking(false)
    }
  }

  const handleApply = async () => {
    const targetCommit = status?.latest_commit
    if (!targetCommit || !checkedSource) return

    setApplying(true)
    try {
      await applySelfUpdate(targetCommit, checkedSource)
    } catch (error) {
      toast({
        title: t('settings.selfUpdate.applyFailed'),
        description: errorMessage(error),
        variant: 'destructive',
      })
      setApplying(false)
      return
    }
    markSelfUpdatePending(targetCommit)
    // 后端已经登记更新并开始重启，这里只负责等待它带着新版本回来。
    await triggerRestart({ skipApiCall: true, maxAttempts: UPDATE_HEALTH_CHECK_ATTEMPTS })
  }

  const busy = checking || applying || isRestarting

  return (
    <Card className="p-4 sm:p-6" data-testid="self-update-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base sm:text-lg font-semibold">{t('settings.selfUpdate.title')}</h3>
          {status && (
            <p className="mt-1 text-xs sm:text-sm text-muted-foreground break-all">
              {t('settings.selfUpdate.current', { version: status.current_version })}
              {status.branch && ` · ${status.branch}`}
              {status.deployment_type === 'package'
                ? ' · PyPI'
                : status.current_commit && ` · ${status.current_commit.slice(0, 8)}`}
            </p>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setSourceId('')
            setCustomPrefix(getSelfUpdateCustomPrefix())
            setSourceDialogOpen(true)
          }}
          disabled={busy || !status?.supported}
        >
          {checking ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          {checking ? t('settings.selfUpdate.checking') : t('settings.selfUpdate.check')}
        </Button>
      </div>

      <Dialog open={sourceDialogOpen} onOpenChange={(open) => { if (!checking) setSourceDialogOpen(open) }}>
        <DialogContent preventOutsideClose={checking}>
          <DialogHeader>
            <DialogTitle>选择本体更新源</DialogTitle>
            <DialogDescription>本次检查和更新使用同一个来源，失败后可重新选择。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="self-update-source">更新源</Label>
              <Select value={sourceId} disabled={checking}
                onValueChange={(value: SelfUpdateSourceSelection['source_id']) => setSourceId(value)}>
                <SelectTrigger id="self-update-source"><SelectValue placeholder="请选择本次更新使用的来源" /></SelectTrigger>
                <SelectContent>
                  {status?.deployment_type === 'package' ? <>
                    <SelectItem value="pypi">PyPI 官方</SelectItem>
                    <SelectItem value="tuna">清华镜像</SelectItem>
                    <SelectItem value="aliyun">阿里镜像</SelectItem>
                  </> : <>
                    <SelectItem value="cnb">CNB 镜像</SelectItem>
                    <SelectItem value="github">GitHub 官方源</SelectItem>
                    <SelectItem value="custom">自定义源</SelectItem>
                  </>}
                </SelectContent>
              </Select>
            </div>
            {sourceId === 'custom' && <div className="space-y-2">
              <Label htmlFor="self-update-custom-source">Git 镜像前缀</Label>
              <Input id="self-update-custom-source" value={customPrefix} disabled={checking}
                onChange={(event) => setCustomPrefix(event.target.value)}
                placeholder="https://example.com/https://github.com" />
              <p className="text-sm text-muted-foreground">只填写一个源前缀，后面会追加 /Mai-with-u/MaiBot.git。</p>
            </div>}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={checking} onClick={() => setSourceDialogOpen(false)}>取消</Button>
            <Button disabled={checking || !sourceId || (sourceId === 'custom' && !customPrefix.trim())} onClick={handleCheck}>
              {checking && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}检查更新
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="mt-3 space-y-3 text-xs sm:text-sm">
        {loadError && (
          <p className="text-destructive">
            {t('settings.selfUpdate.loadFailed')}: {loadError}
          </p>
        )}
        {!status && !loadError && (
          <p className="text-muted-foreground">{t('settings.selfUpdate.loading')}</p>
        )}
        {status && !status.supported && (
          <div className="rounded-md bg-muted/50 p-3 text-muted-foreground">
            {status.blockers.map((blocker) => (
              <p key={blocker}>{t(`settings.selfUpdate.blockers.${blocker}`)}</p>
            ))}
          </div>
        )}
        {status?.supported && status.checked_at === null && (
          <p className="text-muted-foreground">{t('settings.selfUpdate.notChecked')}</p>
        )}
        {status?.supported && status.checked_at !== null && !status.update_available && (
          <p className="flex items-center gap-2 text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
            {t('settings.selfUpdate.upToDate')}
            <span>· {t('settings.selfUpdate.checkedAt', { time: formatTime(status.checked_at) })}</span>
          </p>
        )}
        {status?.supported && status.update_available && (
          <AvailableUpdate status={status} busy={busy || !checkedSource} applying={applying} onApply={handleApply} />
        )}
        {status?.last_result && <LastResult result={status.last_result} />}
        {status?.supported && (
          <p className="text-muted-foreground break-all">
            {status.source_name && `${t('settings.selfUpdate.source', { name: status.source_name })} · `}
            {status.deployment_type === 'package'
              ? '本体更新时选择 PyPI 官方、清华或阿里，失败不自动换源。版本信息从 PyPI 官方检查。'
              : '本体更新时选择 CNB、GitHub 或一个自定义源，失败不自动换源。更新后的依赖按项目配置安装。'}
          </p>
        )}
      </div>
    </Card>
  )
}

interface AvailableUpdateProps {
  status: SelfUpdateStatus
  busy: boolean
  applying: boolean
  onApply: () => void
}

function AvailableUpdate({ status, busy, applying, onApply }: AvailableUpdateProps) {
  const { t } = useTranslation()
  const packaged = status.deployment_type === 'package'
  const versionChanged =
    status.latest_version !== null && status.latest_version !== status.current_version
  const targetLabel = packaged ? status.latest_version ?? status.current_version : formatRevision(
    status.latest_version ?? status.current_version,
    status.latest_commit ?? ''
  )

  return (
    <div className="space-y-3 rounded-md border border-primary/30 bg-primary/5 p-3">
      <div>
        <p className="font-medium text-foreground">
          {versionChanged
            ? t('settings.selfUpdate.newVersion', { version: status.latest_version })
            : t('settings.selfUpdate.newCommits', { count: status.behind })}
        </p>
        {!packaged && <p className="text-muted-foreground">
          {t('settings.selfUpdate.behind', { branch: status.branch, count: status.behind })}
        </p>}
      </div>

      {!packaged && <ul className="max-h-40 space-y-1 overflow-y-auto rounded-md bg-background/60 p-2">
        {status.commits.map((commit) => (
          <li key={commit.short_id} className="flex gap-2">
            <code className="shrink-0 text-muted-foreground">{commit.short_id}</code>
            <span className="min-w-0 break-words">{commit.summary}</span>
          </li>
        ))}
        {status.behind > status.commits.length && (
          <li className="text-muted-foreground">
            {t('settings.selfUpdate.moreCommits', { count: status.commits.length })}
          </li>
        )}
      </ul>}

      {status.dependencies_changed && status.dependency_tool && (
        <p className="text-muted-foreground">
          {t('settings.selfUpdate.dependenciesChanged', { tool: status.dependency_tool })}
        </p>
      )}

      {status.blockers.length > 0 && (
        <div className="space-y-1 rounded-md border border-destructive/30 bg-destructive/5 p-2">
          <p className="flex items-center gap-2 font-medium text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            {t('settings.selfUpdate.cannotApply')}
          </p>
          <ul className="list-inside list-disc text-muted-foreground">
            {status.blockers.map((blocker) => (
              <li key={blocker}>{t(`settings.selfUpdate.blockers.${blocker}`)}</li>
            ))}
          </ul>
          {status.dirty_files.length > 0 && (
            <p className="break-all text-muted-foreground">
              {t('settings.selfUpdate.dirtyFiles')} {status.dirty_files.join(', ')}
            </p>
          )}
        </div>
      )}

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" disabled={busy || !status.can_apply}>
            {applying ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            {t('settings.selfUpdate.apply')}
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('settings.selfUpdate.confirmTitle', { version: targetLabel })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t(packaged
              ? 'settings.selfUpdate.packageConfirmDesc'
              : 'settings.selfUpdate.confirmDesc')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('settings.selfUpdate.cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={onApply}>
              {t('settings.selfUpdate.confirmAction')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function LastResult({ result }: { result: SelfUpdateResult }) {
  const { t } = useTranslation()
  const time = formatTime(result.finished_at)
  const to = formatRevision(result.to_version || '?', result.to_commit)

  if (result.success) {
    return (
      <p className="flex items-center gap-2 rounded-md bg-muted/50 p-3">
        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
        {t('settings.selfUpdate.lastSuccess', {
          time,
          from: formatRevision(result.from_version, result.from_commit),
          to,
        })}
      </p>
    )
  }

  return (
    <div className="space-y-2 rounded-md border border-destructive/30 bg-destructive/5 p-3">
      <p className="flex items-center gap-2 font-medium text-destructive">
        <XCircle className="h-4 w-4 shrink-0" />
        {t('settings.selfUpdate.lastFailed', { time, to })}
      </p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-background/60 p-2 text-xs">
        {result.error}
      </pre>
      {result.rolled_back && (
        <p className="text-muted-foreground">{t('settings.selfUpdate.rolledBack')}</p>
      )}
      {result.rollback_error && (
        <p className="text-destructive">
          {t('settings.selfUpdate.rollbackFailed', { error: result.rollback_error })}
        </p>
      )}
    </div>
  )
}
