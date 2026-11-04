import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { validateToken } from '@/lib/token-validator'
import { completeSetup, loadSetupStatus, updateAccessToken } from '@/routes/setup/api'
import { SetupWizardLayout } from '@/routes/setup/SetupWizardLayout'
import { CustomTokenForm } from '@/routes/setup/StepForms'

/** Host 首次设置只处理 WebUI 密码；没有 Bot 或 Bot 停止都不影响登录。 */
export function HostSetup() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [requiresToken, setRequiresToken] = useState(false)
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    void loadSetupStatus()
      .then(async (status) => {
        if (!active) return
        if (status.requires_custom_token) {
          setRequiresToken(true)
          setLoading(false)
        } else {
          await completeSetup()
          if (active) window.location.assign('/bots')
        }
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
    const validation = validateToken(token.trim())
    if (!validation.isValid || saving) return
    setSaving(true)
    setError('')
    try {
      const result = await updateAccessToken(token.trim())
      if (!result.success) throw new Error(result.message)
      setToken('')
      // 修改密码会撤销旧会话；重新登录后此页自动完成 Host 设置并进入机器人管理。
      window.location.assign('/auth')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSaving(false)
    }
  }

  if (loading)
    return (
      <div role="status" className="flex min-h-screen items-center justify-center gap-3">
        <Loader2 className="animate-spin" />
        {t('setupPage.loading.title')}
      </div>
    )

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
            disabled={!requiresToken || saving || !validateToken(token.trim()).isValid}
            onClick={() => void saveToken()}
          >
            {saving && <Loader2 className="animate-spin" />}
            {t('setupPage.actions.saveToken')}
          </Button>
        }
      >
        {requiresToken && <CustomTokenForm token={token} onChange={setToken} />}
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
