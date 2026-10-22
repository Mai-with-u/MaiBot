import { ArrowUpToLine } from 'lucide-react'
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { getSetting } from '@/lib/settings-manager'
import { checkSelfUpdateOnStartup, SELF_UPDATE_STATUS_EVENT, type SelfUpdateStatus } from '@/lib/system-api'

const SelfUpdateCard = lazy(() =>
  import('@/routes/settings/SelfUpdateCard').then((module) => ({ default: module.SelfUpdateCard }))
)

interface HeaderUpdateButtonProps {
  children: ReactNode
  highlighted: boolean
  onPointerEnter: () => void
  onPointerLeave: () => void
}

export function HeaderUpdateButton({ children, highlighted, onPointerEnter, onPointerLeave }: HeaderUpdateButtonProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<SelfUpdateStatus | null>(null)
  const [alwaysShow, setAlwaysShow] = useState(() => getSetting('alwaysShowUpdateButton'))

  useEffect(() => {
    const refreshSetting = () => setAlwaysShow(getSetting('alwaysShowUpdateButton'))
    window.addEventListener('maibot-settings-change', refreshSetting)
    return () => window.removeEventListener('maibot-settings-change', refreshSetting)
  }, [])

  useEffect(() => {
    let cancelled = false
    let receivedCheck = false
    const handleStatus = (event: Event) => {
      receivedCheck = true
      setStatus((event as CustomEvent<SelfUpdateStatus>).detail)
    }
    window.addEventListener(SELF_UPDATE_STATUS_EVENT, handleStatus)
    void checkSelfUpdateOnStartup()
      .then((next) => {
        // 事件推送的新结果优先于启动检查返回的结果。
        if (!cancelled && !receivedCheck) setStatus(next)
      })
      .catch((error: unknown) => {
        if (!cancelled) console.debug('读取自更新状态失败:', error)
      })
    return () => {
      cancelled = true
      window.removeEventListener(SELF_UPDATE_STATUS_EVENT, handleStatus)
    }
  }, [])

  if (!open && !alwaysShow && !status?.update_available) return null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative isolate border-0 bg-transparent shadow-none"
          title={t('settings.selfUpdate.title')}
          aria-label={t('settings.selfUpdate.title')}
          data-dashboard-header-action="true"
          data-header-update-available={status?.update_available ? 'true' : 'false'}
          data-header-action-highlighted={highlighted ? 'true' : 'false'}
          onPointerEnter={onPointerEnter}
          onPointerLeave={onPointerLeave}
        >
          {children}
          <ArrowUpToLine className="h-4 w-4" />
          {status?.update_available && (
            <>
              <span className="relative z-10 whitespace-nowrap text-sm font-bold">
                {t('home.versionCard.updateAvailable')} {status.latest_version ? `V${status.latest_version}` : status.latest_commit?.slice(0, 8)}
              </span>
              <span aria-hidden="true" className="absolute -right-1 -top-1 z-20 h-2.5 w-2.5 rounded-full border border-background bg-yellow-400" />
            </>
          )}
        </Button>
      </DialogTrigger>
      <DialogContent className="[--dialog-width:48rem]" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{t('settings.selfUpdate.title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Suspense fallback={<p className="text-sm text-muted-foreground">{t('settings.selfUpdate.loading')}</p>}>
            <SelfUpdateCard />
          </Suspense>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
