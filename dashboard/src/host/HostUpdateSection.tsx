import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { applyHostUpdate, checkHostUpdate, getHostUpdate } from './api'
import type { HostUpdateCheck, HostUpdateStatus } from './api'

export function HostUpdateSection() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<HostUpdateStatus | null>(null)
  const [check, setCheck] = useState<HostUpdateCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [restarting, setRestarting] = useState(0)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const result = await getHostUpdate()
        if (!active) return
        setStatus(result)
        setError('')
        if (restarting && result.last_result && result.last_result.finished_at >= restarting) {
          setRestarting(0)
          setCheck(null)
        }
      } catch (reason) {
        if (active && !restarting) setError(String(reason))
      }
    }
    void refresh()
    const timer = restarting ? setInterval(() => void refresh(), 5000) : undefined
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [restarting])
  async function run(apply: boolean) {
    setBusy(true)
    setError('')
    try {
      if (apply && check?.latest_commit) {
        const started = Date.now() / 1000
        await applyHostUpdate(check.latest_commit)
        setRestarting(started)
      } else setCheck(await checkHostUpdate())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('hostOperations.update')}</CardTitle>
        <CardDescription>{t('hostOperations.updateHint')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {status && (
          <p className="text-sm">
            {status.local.version}
            {check?.latest_version && ` → ${check.latest_version}`}
          </p>
        )}
        {check?.latest_commit && (
          <code className="block text-xs break-all">{check.latest_commit}</code>
        )}
        {check?.blocker_messages.map((message) => (
          <Alert key={message}>
            <AlertDescription>{message}</AlertDescription>
          </Alert>
        ))}
        {check && !check.can_apply && !check.blocker_messages.length && (
          <p className="text-sm">{t('hostOperations.current')}</p>
        )}
        {status?.last_result && (
          <p role="status" className="text-sm">
            {status.last_result.success
              ? t('hostOperations.updated')
              : `${status.last_result.error} ${status.last_result.rollback_error}`}
          </p>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {restarting > 0 && <p role="status">{t('hostOperations.restarting')}</p>}
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy || restarting > 0 || status?.busy}
          onClick={() => void run(false)}
        >
          {t(busy ? 'hostOperations.working' : 'hostOperations.check')}
        </Button>
        {check?.can_apply && (
          <Button disabled={busy || restarting > 0 || status?.busy} onClick={() => void run(true)}>
            {t('hostOperations.apply')}
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}
