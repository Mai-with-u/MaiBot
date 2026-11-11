import { useEffect, useState, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { BotUnavailable } from '@/components/bot-unavailable'
import { Button } from '@/components/ui/button'
import { validateToken } from '@/lib/token-validator'
import { loadSetupStatus, updateAccessToken } from '@/routes/setup/api'
import { SetupWizardLayout } from '@/routes/setup/SetupWizardLayout'
import { CustomTokenForm } from '@/routes/setup/StepForms'
import { bootstrapHostRuntime } from './api'
import { useHostRuntime } from './state'

/** 先设置宿主密码，再为当前运行中的 Bot 打开配置向导。 */
export function HostSetup({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { selected_bot_running: botRunning } = useHostRuntime()
  const [loading, setLoading] = useState(true)
  const [requiresToken, setRequiresToken] = useState(false)
  const [token, setToken] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const canSave = requiresToken && !saving && token === confirmation && validateToken(token.trim()).isValid

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    void loadSetupStatus()
      .then(async (status) => {
        if (!active) return
        setRequiresToken(status.requires_custom_token)
        if (!status.requires_custom_token) await bootstrapHostRuntime()
        if (active) setLoading(false)
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason))
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [retry])

  async function saveToken() {
    if (!canSave) return
    setSaving(true)
    setError('')
    try {
      const result = await updateAccessToken(token.trim())
      if (!result.success) throw new Error(result.message)
      setToken('')
      setConfirmation('')
      setRequiresToken(false)
      await bootstrapHostRuntime()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  if (loading || (saving && !requiresToken))
    return (
      <div role="status" className="flex min-h-screen items-center justify-center gap-3">
        <Loader2 className="animate-spin" />
        {t('setupPage.loading.title')}
      </div>
    )

  if (!requiresToken && !error) {
    return botRunning ? children : <div className="h-screen"><BotUnavailable /></div>
  }

  return (
    <div className="bg-background text-foreground min-h-screen px-4 py-8">
      <SetupWizardLayout
        steps={[
          {
            id: 'custom-token',
            title: t('setupPage.steps.customToken.title'),
            description: t('setupPage.steps.customToken.description'),
          },
        ]}
        currentStep={0}
        actions={
          <Button
            disabled={!canSave}
            onClick={() => void saveToken()}
          >
            {saving && <Loader2 className="animate-spin" />}
            {t('setupPage.actions.saveToken')}
          </Button>
        }
      >
        {requiresToken && (
          <CustomTokenForm
            token={token}
            onChange={setToken}
            confirmation={confirmation}
            onConfirmationChange={setConfirmation}
          />
        )}
        {error && (
          <div className="mt-4 space-y-3">
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
            {!requiresToken && (
              <Button variant="outline" onClick={() => setRetry((current) => current + 1)}>
                {t('common.retry')}
              </Button>
            )}
          </div>
        )}
      </SetupWizardLayout>
    </div>
  )
}
