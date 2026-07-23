import { useEffect, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  getVersionCompatibility,
  type VersionCompatibilityResult,
} from '@/lib/version-compatibility-api'

const CONFIRM_DELAY_SECONDS = 5

function VersionValue({ value }: { value: string }) {
  return (
    <span className="font-mono text-sm font-semibold text-foreground">
      v{value}
    </span>
  )
}

export function VersionCompatibilityDialog() {
  const { t } = useTranslation()
  const [compatibility, setCompatibility] = useState<VersionCompatibilityResult | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(CONFIRM_DELAY_SECONDS)

  useEffect(() => {
    const controller = new AbortController()

    async function checkCompatibility() {
      try {
        const result = await getVersionCompatibility(controller.signal)
        if (result.status !== 'compatible') {
          setCompatibility(result)
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.warn('[VersionCompatibility] 跳过版本兼容性提示:', error)
        }
      }
    }

    void checkCompatibility()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!compatibility || dismissed) {
      return
    }

    setRemainingSeconds(CONFIRM_DELAY_SECONDS)
    const timer = window.setInterval(() => {
      setRemainingSeconds((current) => {
        if (current <= 1) {
          window.clearInterval(timer)
          return 0
        }
        return current - 1
      })
    }, 1000)

    return () => window.clearInterval(timer)
  }, [compatibility, dismissed])

  if (!compatibility) {
    return null
  }

  const webuiOutdated = compatibility.status === 'webui_outdated'
  const title = webuiOutdated
    ? t('versionCompatibility.webuiOutdated.title')
    : t('versionCompatibility.mainProgramOutdated.title')
  const description = webuiOutdated
    ? t('versionCompatibility.webuiOutdated.description', {
        currentVersion: compatibility.webui_version,
        requiredVersion: compatibility.required_webui_version,
      })
    : t('versionCompatibility.mainProgramOutdated.description', {
        mainVersion: compatibility.main_program_version,
        webuiVersion: compatibility.webui_version,
        requiredVersion: compatibility.required_webui_version,
      })
  const updateTarget = webuiOutdated
    ? t('versionCompatibility.webuiOutdated.updateTarget')
    : t('versionCompatibility.mainProgramOutdated.updateTarget')

  return (
    <Dialog
      open={!dismissed}
      onOpenChange={(open) => {
        if (!open && remainingSeconds === 0) {
          setDismissed(true)
        }
      }}
    >
      <DialogContent
        hideCloseButton
        preventOutsideClose
        style={{ '--dialog-width': '38rem' } as CSSProperties}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            {title}
          </DialogTitle>
          <DialogDescription className="leading-6">{description}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 rounded-md border bg-muted/40 p-4 sm:grid-cols-3">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t('versionCompatibility.mainProgramVersion')}
            </p>
            <VersionValue value={compatibility.main_program_version} />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t('versionCompatibility.webuiVersion')}
            </p>
            <VersionValue value={compatibility.webui_version} />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t('versionCompatibility.requiredWebuiVersion')}
            </p>
            <VersionValue value={compatibility.required_webui_version} />
          </div>
        </div>

        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
          <p className="font-medium">
            {t('versionCompatibility.updateTargetLabel', { target: updateTarget })}
          </p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {t('versionCompatibility.riskWarning')}
          </p>
        </div>

        <DialogFooter className="items-center gap-3 sm:justify-between sm:space-x-0">
          <p className="text-left text-xs leading-5 text-muted-foreground">
            {remainingSeconds > 0
              ? t('versionCompatibility.countdownHint', { seconds: remainingSeconds })
              : t('versionCompatibility.confirmHint')}
          </p>
          <Button
            type="button"
            disabled={remainingSeconds > 0}
            onClick={() => setDismissed(true)}
            className="shrink-0"
          >
            {remainingSeconds > 0
              ? t('versionCompatibility.countdownButton', { seconds: remainingSeconds })
              : t('versionCompatibility.confirmButton')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
