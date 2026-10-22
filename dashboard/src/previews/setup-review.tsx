import { ArrowRight } from 'lucide-react'
import { createInstance } from 'i18next'
import { useState } from 'react'
import { I18nextProvider, useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import zh from '@/i18n/locales/zh.json'
import { validateToken } from '@/lib/token-validator'
import { CustomTokenForm } from '@/routes/setup/StepForms'
import { SetupWizardLayout } from '@/routes/setup/SetupWizardLayout'

const i18n = createInstance()
void i18n.init({
  lng: 'zh',
  resources: { zh: { translation: zh } },
  interpolation: { escapeValue: false },
})

function PasswordSetupPreview() {
  const { t } = useTranslation()
  const [token, setToken] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const steps = [
    { id: 'custom-token', ...zh.setupPage.steps.customToken },
    { id: 'bot-profile', ...zh.setupPage.steps.botProfile },
    { id: 'model-setup', ...zh.setupPage.steps.modelSetup },
  ]
  return (
    <div className="min-h-screen px-4">
      <SetupWizardLayout
        steps={steps}
        currentStep={0}
        actions={
          <>
            <Button variant="ghost" disabled className="px-1">
              {t('setupPage.actions.skip')}
            </Button>
            <Button
              size="lg"
              variant={validateToken(token).isValid ? 'default' : 'outline'}
              className="min-w-44"
              disabled={!validateToken(token).isValid}
              onClick={() => setSubmitted(true)}
            >
              {t('setupPage.actions.saveToken')}
              <ArrowRight />
            </Button>
            {submitted && (
              <p role="status" className="text-muted-foreground w-full text-sm">
                校验通过。此处仅为预览，不保存密码。
              </p>
            )}
          </>
        }
      >
        <CustomTokenForm
          token={token}
          onChange={(value) => {
            setToken(value)
            setSubmitted(false)
          }}
        />
      </SetupWizardLayout>
    </div>
  )
}

export function SetupReview() {
  return (
    <I18nextProvider i18n={i18n}>
      <PasswordSetupPreview />
    </I18nextProvider>
  )
}
