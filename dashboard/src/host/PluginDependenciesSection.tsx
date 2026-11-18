import { useEffect, useState } from 'react'
import { Loader2, PackageSearch, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

import { getPluginDependencies, installPluginDependencies } from './api'
import type { PluginDependencyReport } from './api'

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

/** 插件依赖装在所有 Bot 共用的 Python 环境中，只能由宿主停止全部 Bot 后统一安装。 */
export function PluginDependenciesSection({
  onChanged,
  onBusyChange,
}: {
  onChanged: () => void
  onBusyChange: (busy: boolean) => void
}) {
  const { t } = useTranslation()
  const [report, setReport] = useState<PluginDependencyReport | null>(null)
  const [busy, setBusy] = useState<'check' | 'install' | null>(null)
  const [error, setError] = useState('')
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    onBusyChange(busy !== null)
  }, [busy, onBusyChange])

  async function check() {
    setBusy('check')
    setError('')
    setInstalled(false)
    try {
      setReport(await getPluginDependencies())
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy(null)
    }
  }

  async function install() {
    setBusy('install')
    setError('')
    try {
      const result = await installPluginDependencies()
      setInstalled(Boolean(result.installed))
      setReport(await getPluginDependencies())
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy(null)
      onChanged()
    }
  }

  const missing = report?.requirements ?? []
  const conflicts = report?.conflicts ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('hostManager.pluginDeps.title')}</CardTitle>
        <CardDescription>{t('hostManager.pluginDeps.hint')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {(error || report?.error) && (
          <Alert variant="destructive">
            <AlertDescription className="break-words whitespace-pre-line">
              {error || report?.error}
            </AlertDescription>
          </Alert>
        )}
        {installed && (
          <p role="status" className="text-sm">
            {t('hostManager.pluginDeps.installed')}
          </p>
        )}
        {conflicts.map((message) => (
          <Alert key={message} variant="destructive">
            <AlertDescription>{message}</AlertDescription>
          </Alert>
        ))}
        {report && missing.length === 0 && conflicts.length === 0 && !installed && (
          <p role="status" className="text-muted-foreground text-sm">
            {t('hostManager.pluginDeps.none')}
          </p>
        )}
        {missing.length > 0 && (
          <div className="space-y-3">
            <ul className="space-y-1 text-sm">
              {missing.map((item) => (
                <li key={item.requirement} className="flex flex-wrap gap-2">
                  <code>{item.requirement}</code>
                  <span className="text-muted-foreground">{item.plugin_ids.join('、')}</span>
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground text-xs">
              {t('hostManager.pluginDeps.installHint')}
            </p>
          </div>
        )}
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        <Button variant="outline" disabled={busy !== null} onClick={() => void check()}>
          {busy === 'check' ? <Loader2 className="animate-spin" /> : <PackageSearch />}
          {t('hostManager.pluginDeps.check')}
        </Button>
        {missing.length > 0 && (
          <Button disabled={busy !== null || conflicts.length > 0} onClick={() => void install()}>
            {busy === 'install' ? <Loader2 className="animate-spin" /> : <Wrench />}
            {t('hostManager.pluginDeps.install')}
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}
