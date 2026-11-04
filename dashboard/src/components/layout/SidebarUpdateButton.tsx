import { ArrowUpToLine } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { getSetting } from '@/lib/settings-manager'
import { checkHostUpdate, getHostUpdate, HOST_UPDATE_CHECK_EVENT, type HostUpdateCheck } from '@/host/api'

let startupCheck: ReturnType<typeof checkHostUpdate> | undefined

const HostUpdateSection = lazy(() =>
  import('@/host/HostUpdateSection').then((module) => ({ default: module.HostUpdateSection }))
)

export function SidebarUpdateButton({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<HostUpdateCheck | null>(null)
  const [localVersion, setLocalVersion] = useState('')
  const [alwaysShow, setAlwaysShow] = useState(() => getSetting('alwaysShowUpdateButton'))

  useEffect(() => {
    const refreshSetting = () => setAlwaysShow(getSetting('alwaysShowUpdateButton'))
    window.addEventListener('maibot-settings-change', refreshSetting)
    return () => window.removeEventListener('maibot-settings-change', refreshSetting)
  }, [])

  useEffect(() => {
    let cancelled = false
    let receivedCheck = false
    void getHostUpdate().then(result => {
      if (!cancelled) setLocalVersion(result.local.version)
    }).catch((error: unknown) => console.debug('读取当前程序版本失败:', error))
    const handleStatus = (event: Event) => {
      receivedCheck = true
      setStatus((event as CustomEvent<HostUpdateCheck>).detail)
    }
    window.addEventListener(HOST_UPDATE_CHECK_EVENT, handleStatus)
    void (startupCheck ??= checkHostUpdate())
      .then((next) => {
        // 事件推送的新结果优先于启动检查返回的结果。
        if (!cancelled && !receivedCheck) setStatus(next)
      })
      .catch((error: unknown) => {
        if (!cancelled) console.debug('读取自更新状态失败:', error)
      })
    return () => {
      cancelled = true
      window.removeEventListener(HOST_UPDATE_CHECK_EVENT, handleStatus)
    }
  }, [])

  const updateAvailable = Boolean(status?.latest_commit && status?.latest_commit !== status.local.commit)
  const version = updateAvailable && status?.latest_version
    ? status.latest_version
    : status?.local.version || localVersion
  const label = `${t('settings.selfUpdate.title')}${version ? ` V${version}` : ''}`
  if (!open && !alwaysShow && !updateAvailable) return null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size={collapsed ? "icon" : "default"}
          className={`relative max-w-full gap-2 ${collapsed ? '' : 'w-full justify-start whitespace-nowrap'}`}
          title={label}
          aria-label={label}
        >
          <ArrowUpToLine className="h-4 w-4 shrink-0" />
          {!collapsed && <span className="min-w-0 truncate text-left text-sm font-bold">{label}</span>}
          {updateAvailable && <span aria-hidden="true" className="absolute -right-1 -top-1 z-20 h-2.5 w-2.5 rounded-full border border-background bg-yellow-400" />}
        </Button>
      </DialogTrigger>
      <DialogContent className="[--dialog-width:48rem]" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{t('settings.selfUpdate.title')}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Suspense fallback={<p className="text-sm text-muted-foreground">{t('settings.selfUpdate.loading')}</p>}>
            <HostUpdateSection />
          </Suspense>
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
