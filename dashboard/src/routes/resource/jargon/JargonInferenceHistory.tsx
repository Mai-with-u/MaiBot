import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { getJargonDetail } from '@/lib/jargon-api'
import {
  getJargonMeaningHistory,
  lockJargonInference,
  restoreJargonMeaning,
} from '@/lib/jargon-inference-api'
import type { Jargon } from '@/types/jargon'
import type { JargonMeaningHistory } from '@/types/jargon-inference'

import { InferenceStatus } from './JargonInferenceStatus'
import { inferenceTime } from '@/lib/jargon-inference-format'

/** Show persisted inference history and protect unsaved edits from lock or restore operations. */
export function JargonInferenceHistory({
  jargon,
  dirty,
  onChanged,
  onLockChanged,
}: {
  jargon: Jargon
  dirty: boolean
  onChanged: (jargon: Jargon) => void
  onLockChanged: (id: number, locked: boolean) => void
}) {
  const { t } = useTranslation()
  const [history, setHistory] = useState<JargonMeaningHistory[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [restoring, setRestoring] = useState<JargonMeaningHistory | null>(null)

  /** Fetch history on demand and keep the last loaded entries available if a refresh fails. */
  async function loadHistory() {
    setLoading(true)
    setError('')
    try {
      setHistory((await getJargonMeaningHistory(jargon.id)).items)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }

  /** Report the lock change after persistence without replacing the parent's edited fields. */
  async function lock(locked: boolean) {
    setBusy(true)
    setError('')
    try {
      await lockJargonInference(jargon.id, locked)
      onLockChanged(jargon.id, locked)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  /** Restore the previous meaning and classification, then reload the record and its audit trail. */
  async function restore(entry: JargonMeaningHistory) {
    setBusy(true)
    setError('')
    try {
      await restoreJargonMeaning(entry.id)
      onChanged((await getJargonDetail(jargon.id)).data)
      await loadHistory()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
      setRestoring(null)
    }
  }

  return (
    <section
      className="space-y-3 border-t pt-4"
      aria-label={t('jargonInference.recordState', '推断状态与历史')}
    >
      <div className="flex items-center justify-between gap-2">
        <Label>{t('jargonInference.recordState', '推断状态与历史')}</Label>
        <Badge variant="outline">
          <InferenceStatus status={jargon.inference_status ?? 'unattempted'} />
        </Badge>
      </div>
      {jargon.inference_reason && (
        <p className="text-muted-foreground text-xs">{jargon.inference_reason}</p>
      )}
      <div className="text-muted-foreground grid gap-1 text-xs">
        <span>
          {t('jargonInference.lastAttempt', '最近尝试')}：{inferenceTime(jargon.last_attempt_at)}
        </span>
        <span>
          {t('jargonInference.lastSuccess', '最近成功')}：{inferenceTime(jargon.last_success_at)}
        </span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={`inference-lock-${jargon.id}`}>
          {t('jargonInference.lock', '锁定，阻止 AI 推断覆盖')}
        </Label>
        <Switch
          id={`inference-lock-${jargon.id}`}
          checked={jargon.inference_locked ?? false}
          disabled={busy || dirty}
          onCheckedChange={(value) => void lock(value)}
        />
      </div>
      {jargon.created_by === 'MANUAL' && (
        <p className="text-muted-foreground text-xs">
          {t(
            'jargonInference.manualHint',
            '此词条为手动维护。需要恢复 AI 更新时，请使用“取消固定”，并解除锁定。'
          )}
        </p>
      )}
      {dirty && (
        <p className="text-muted-foreground text-xs">
          {t('jargonInference.dirtyHint', '请先保存当前编辑，再更改锁定或恢复历史。')}
        </p>
      )}
      <Button
        variant="outline"
        size="sm"
        type="button"
        disabled={loading}
        onClick={() => void loadHistory()}
      >
        {loading
          ? t('jargonInference.loading', '正在加载…')
          : t('jargonInference.history', '查看含义变更历史')}
      </Button>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {history?.length === 0 && (
        <p className="text-muted-foreground text-xs">
          {t('jargonInference.noHistory', '暂无含义变更历史。')}
        </p>
      )}
      {history && history.length > 0 && (
        <ul className="space-y-3">
          {history.map((entry) => (
            <li key={entry.id} className="space-y-2 rounded-md border p-3 text-sm">
              <p className="text-muted-foreground text-xs">
                {inferenceTime(entry.created_at)} ·{' '}
                {t(`jargonInference.sources.${entry.source}`, entry.source)}
              </p>
              <div>
                <span className="text-muted-foreground">
                  {t('jargonInference.before', '变更前')}（
                  {entry.previous_is_jargon === null
                    ? t('jargonInference.unknownClassification', '待判定')
                    : entry.previous_is_jargon
                      ? t('jargonInference.isJargon', '黑话')
                      : t('jargonInference.notJargon', '非黑话')}
                  ）：
                </span>
                <span className="break-words whitespace-pre-wrap">
                  {entry.previous_meaning || '—'}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">
                  {t('jargonInference.after', '变更后')}（
                  {entry.is_jargon === null
                    ? t('jargonInference.unknownClassification', '待判定')
                    : entry.is_jargon
                      ? t('jargonInference.isJargon', '黑话')
                      : t('jargonInference.notJargon', '非黑话')}
                  ）：
                </span>
                <span className="break-words whitespace-pre-wrap">{entry.meaning || '—'}</span>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy || dirty}
                onClick={() => setRestoring(entry)}
              >
                {t('jargonInference.restore', '恢复变更前')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <AlertDialog
        open={!!restoring}
        onOpenChange={(open) => {
          if (!open && !busy) setRestoring(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('jargonInference.restoreTitle', '恢复并固定此版本？')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                'jargonInference.restoreDescription',
                '将恢复这次变更之前的含义和黑话判定，并标记为手动维护、锁定，防止后台任务再次覆盖。'
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>
              {t('jargonInference.cancel', '取消')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={busy || dirty}
              onClick={(event) => {
                event.preventDefault()
                if (restoring) void restore(restoring)
              }}
            >
              {t('jargonInference.restore', '恢复变更前')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
